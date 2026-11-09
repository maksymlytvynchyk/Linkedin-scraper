import re
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from scraper.client import BlockedError, LinkedInClient, LinkedInError
from scraper.geo import resolve_geo_id
from scraper.models import ParseStatus, QueryStatus, SearchQuery
from scraper.parsers import parse_job_cards
from scraper.services import mark_failed, upsert_vacancy

TOTAL_RESULTS_RE = re.compile(r'"totalResults"\s*:\s*(\d+)')


class Command(BaseCommand):
    help = "Stage 1: collect vacancy cards for saved search queries and store them in PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument("--query-id", type=int, action="append", help="SearchQuery id (repeatable).")
        parser.add_argument("--all", action="store_true", help="Run every active search query.")
        parser.add_argument("--keywords", help="Ad-hoc search: keywords (creates the query if needed).")
        parser.add_argument("--country", default="United States", help="Ad-hoc search: country.")
        parser.add_argument("--posted", help='Restrict shards, e.g. "" / r86400 / r604800 / r2592000.')
        parser.add_argument("--max-pages", type=int, default=1000,
                            help="Total pages this run may request per query (10 cards/page). "
                                 "Progress is saved, so re-running continues.")
        parser.add_argument("--max-inserts", type=int, default=0, help="Stop after N new vacancies (0 = unlimited).")
        parser.add_argument("--min-delay", type=float, default=None, help="Seconds between HTTP requests.")
        parser.add_argument("--resume", action="store_true", default=True, help="Resume from stored cursors (default).")
        parser.add_argument("--fresh", action="store_true", help="Reset pagination state before running.")
        parser.add_argument("--stop-on-block", action="store_true", help="Abort instead of stopping just this query.")

    def handle(self, *args, **options):
        self.budget = max(1, options["max_pages"])
        self.max_inserts = options["max_inserts"]
        self.stop_on_block = options["stop_on_block"]
        self.client = LinkedInClient(min_delay=options["min_delay"])

        queries = self._select_queries(options)
        if not queries:
            raise CommandError("No search queries matched. Use --query-id, --all, or --keywords.")

        totals = {"new": 0, "seen": 0, "pages": 0, "errors": 0}
        for query in queries:
            try:
                stats = self.run_query(query, options)
            except BlockedError as exc:
                self.stderr.write(self.style.ERROR(f"[{query.pk}] blocked: {exc}"))
                query.status = QueryStatus.FAILED
                query.last_error = str(exc)[:2000]
                query.save(update_fields=["status", "last_error", "updated_at"])
                totals["errors"] += 1
                if self.stop_on_block:
                    raise CommandError("Aborted on block; re-run later to continue.") from exc
                continue
            except (LinkedInError, DatabaseError) as exc:
                self.stderr.write(self.style.ERROR(f"[{query.pk}] failed: {exc}"))
                query.status = QueryStatus.FAILED
                query.last_error = str(exc)[:2000]
                query.save(update_fields=["status", "last_error", "updated_at"])
                totals["errors"] += 1
                continue
            for key in ("new", "seen", "pages"):
                totals[key] += stats[key]
            if self.max_inserts and totals["new"] >= self.max_inserts:
                self.stdout.write(self.style.WARNING("max-inserts reached, stopping."))
                break

        self.client.session.close()
        self.stdout.write(
            self.style.SUCCESS(
                f"search done: {totals['new']} new vacancies, {totals['seen']} already known, "
                f"{totals['pages']} pages, {totals['errors']} errors, "
                f"{self.client.stats['requests']} requests "
                f"({self.client.stats['blocks']} blocks)"
            )
        )

    def _select_queries(self, options) -> list[SearchQuery]:
        if options["keywords"]:
            query = self._adhoc_query(options["keywords"], options["country"])
            return [query]

        qs = SearchQuery.objects.all()
        if options["query_id"]:
            qs = qs.filter(pk__in=options["query_id"])
        elif not options["all"]:
            raise CommandError(
                "Nothing to do. Pass --all, --query-id <n>, or --keywords \"...\". "
                "Tip: `manage.py seed_queries` creates the demo queries."
            )
        return list(qs.order_by("pk"))

    def _adhoc_query(self, keywords: str, country: str) -> SearchQuery:
        query, created = SearchQuery.objects.get_or_create(
            keywords=keywords.strip(), country=country.strip() or "United States"
        )
        if created:
            self.stdout.write(self.style.NOTICE(f"created SearchQuery #{query.pk} for ad-hoc run"))
        return query

    def run_query(self, query: SearchQuery, options: dict) -> dict:
        posted_windows = [options["posted"]] if options.get("posted") is not None else ["", "r604800", "r86400"]
        if options["fresh"]:
            query.shard_state = []
            query.status = QueryStatus.NEW
        query.ensure_shards(posted_windows)

        if not query.geo_id:
            query.geo_id = resolve_geo_id(query.country, self.client)
            query.save(update_fields=["geo_id", "updated_at"])

        self.stdout.write(
            f"[{query.pk}] {query.name or query.keywords}: keywords={query.keywords!r} "
            f"country={query.country!r} geoId={query.geo_id or '-'} "
            f"shards={[(s['posted'] or 'any', s['start'], s['done']) for s in query.shard_state]}"
        )

        query.status = QueryStatus.RUNNING
        query.last_error = ""
        query.last_run_at = timezone.now()
        query.save(update_fields=["status", "last_error", "last_run_at", "updated_at"])

        stats = {"new": 0, "seen": 0, "pages": 0}
        seen_in_run: set[str] = set()
        consecutive_errors = 0
        hinted = False
        budget = self.budget

        while True:
            if stats["pages"] >= budget:
                self.stdout.write(f"  page budget ({budget}) reached; progress saved, resume later")
                break

            shard = query.current_shard()
            if shard is None:
                break
            page = int(shard.get("start") or 0)
            shard_cap = int(shard.get("cap") or query.depth_cap)
            if page >= shard_cap:
                query.advance_shard(done=True)
                query.save(update_fields=["shard_state", "updated_at"])
                self.stdout.write(
                    f"  shard posted={shard['posted'] or 'any'} reached its cap ({shard_cap})"
                )
                continue

            try:
                html = self.client.search_page(
                    keywords=query.keywords,
                    location=query.country,
                    start=page,
                    geo_id=query.geo_id,
                    posted=shard.get("posted", ""),
                    remote=query.remote,
                    experience=query.experience,
                    extra_params=query.extra_params,
                )
            except BlockedError:
                raise
            except LinkedInError as exc:
                consecutive_errors += 1
                stats["errors"] = stats.get("errors", 0) + 1
                self.stderr.write(self.style.WARNING(f"  page start={page} error: {exc}"))
                if consecutive_errors >= 3:
                    query.last_error = str(exc)[:2000]
                    query.save(update_fields=["last_error", "updated_at"])
                    break
                time.sleep(5 * consecutive_errors)
                continue

            consecutive_errors = 0
            stats["pages"] += 1

            if not html or not html.strip():
                query.advance_shard(done=True)
                query.save(update_fields=["shard_state", "updated_at"])
                self.stdout.write(f"  shard posted={shard['posted'] or 'any'} exhausted after {page} cards")
                continue

            if not hinted:
                match = TOTAL_RESULTS_RE.search(html)
                if match:
                    query.total_results_hint = int(match.group(1))
                    query.save(update_fields=["total_results_hint", "updated_at"])
                    self.stdout.write(f"  LinkedIn reports totalResults={query.total_results_hint}")
                hinted = True

            cards = parse_job_cards(html)
            if not cards:
                query.advance_shard(done=True)
                query.save(update_fields=["shard_state", "updated_at"])
                self.stdout.write("  no parsable cards on page -> shard closed")
                continue

            new_on_page = 0
            for card in cards:
                if card["linkedin_id"] in seen_in_run:
                    continue
                seen_in_run.add(card["linkedin_id"])
                try:
                    _, created = upsert_vacancy(card, query)
                except DatabaseError as exc:
                    self.stderr.write(self.style.ERROR(f"  db error on {card['linkedin_id']}: {exc}"))
                    stats["errors"] = stats.get("errors", 0) + 1
                    continue
                if created:
                    stats["new"] += 1
                    new_on_page += 1
                else:
                    stats["seen"] += 1

            self.stdout.write(
                f"  posted={shard['posted'] or 'any'} start={page}: "
                f"{len(cards)} cards, {new_on_page} new (total new={stats['new']})"
            )

            query.advance_shard(done=False, batch=query.page_size)
            query.save(update_fields=["shard_state", "updated_at"])

            if self.max_inserts and stats["new"] >= self.max_inserts:
                break

        shard_left = query.current_shard()
        query.status = QueryStatus.DONE if shard_left is None else QueryStatus.NEW
        query.save(update_fields=["status", "updated_at"])
        return stats
