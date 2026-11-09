import time

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from scraper.client import BlockedError, LinkedInClient, LinkedInError
from scraper.models import Company, ParseStatus, Vacancy
from scraper.parsers import parse_company, parse_job_detail
from scraper.services import apply_company, apply_detail, mark_failed, upsert_company


class Command(BaseCommand):
    help = "Stage 2: fetch full vacancy details and company profiles for stored vacancies."

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument("--all", action="store_true", help="Process every pending vacancy.")
        scope.add_argument("--limit", type=int, default=100, help="Max vacancies this run (default 100).")
        scope.add_argument("--vacancy-id", type=int, action="append", help="Specific vacancy pk (repeatable).")
        parser.add_argument("--retry-failed", action="store_true", help="Also retry previously failed rows.")
        parser.add_argument("--with-company", action="store_true", help="Also fetch company pages.")
        parser.add_argument("--refresh-companies", action="store_true", help="Re-fetch company pages already stored.")
        parser.add_argument("--max-attempts", type=int, default=5, help="Skip rows that failed more often.")
        parser.add_argument("--min-delay", type=float, default=None, help="Seconds between HTTP requests.")
        parser.add_argument("--stop-on-block", action="store_true")

    def handle(self, *args, **options):
        self.client = LinkedInClient(min_delay=options["min_delay"])
        self.max_attempts = options["max_attempts"]
        self.with_company = options["with_company"] or options["refresh_companies"]

        vacancies = self._select(options)
        if not vacancies:
            self.stdout.write(self.style.WARNING("No vacancies to process. Run parse_search first."))
            return

        self.stdout.write(f"processing {len(vacancies)} vacancies (with_company={self.with_company})")
        stats = {"fetched": 0, "failed": 0, "gone": 0, "companies": 0}

        for index, vacancy in enumerate(vacancies, 1):
            try:
                self._process_vacancy(vacancy, stats)
            except BlockedError as exc:
                self.stderr.write(self.style.ERROR(f"blocked at {index}/{len(vacancies)}: {exc}"))
                if options["stop_on_block"]:
                    raise CommandError("Aborted on block; re-run later, progress is saved.") from exc
                self.client.limiter.penalise(60)
                continue
            except (LinkedInError, DatabaseError) as exc:
                stats["failed"] += 1
                mark_failed(vacancy, str(exc))
                self.stderr.write(self.style.WARNING(f"  [{vacancy.pk}] error: {exc}"))
                continue

            if index % 25 == 0 or index == len(vacancies):
                self.stdout.write(
                    f"  {index}/{len(vacancies)}  fetched={stats['fetched']} "
                    f"failed={stats['failed']} gone={stats['gone']} companies={stats['companies']}"
                )

        self.client.session.close()
        self.stdout.write(
            self.style.SUCCESS(
                f"details done: fetched={stats['fetched']} failed={stats['failed']} "
                f"gone={stats['gone']} companies={stats['companies']} "
                f"requests={self.client.stats['requests']} blocks={self.client.stats['blocks']}"
            )
        )

    def _select(self, options) -> list[Vacancy]:
        if options["vacancy_id"]:
            return list(Vacancy.objects.filter(pk__in=options["vacancy_id"]).order_by("pk"))

        statuses = [ParseStatus.PENDING]
        if options["retry_failed"]:
            statuses.append(ParseStatus.FAILED)

        qs = (
            Vacancy.objects.filter(status__in=statuses, attempts__lt=self.max_attempts)
            .exclude(status=ParseStatus.GONE)
            .order_by("attempts", "first_seen_at")
        )
        if options["all"]:
            return list(qs)
        return list(qs[: options["limit"]])

    def _process_vacancy(self, vacancy: Vacancy, stats: dict) -> None:
        html, status_code = self.client.job_detail(vacancy.linkedin_id)

        if status_code == 404 or (status_code == 200 and not (html or "").strip()):
            stats["gone"] += 1
            mark_failed(vacancy, f"posting unavailable (HTTP {status_code})", gone=True)
            self.stdout.write(f"  [{vacancy.pk}] {vacancy.linkedin_id} is gone")
            return
        if status_code != 200:
            stats["failed"] += 1
            mark_failed(vacancy, f"detail HTTP {status_code}")
            return

        detail = parse_job_detail(html)
        company = None

        if self.with_company:
            company = self._ensure_company(vacancy, detail, stats)
        elif detail.get("company_url") and vacancy.company is None:
            company = upsert_company(name=vacancy.company_name, url=detail["company_url"])

        apply_detail(vacancy, detail, company=company)
        stats["fetched"] += 1

    def _ensure_company(self, vacancy: Vacancy, detail: dict, stats: dict) -> Company | None:
        company = vacancy.company
        if company is None and detail.get("company_url"):
            company = upsert_company(name=vacancy.company_name, url=detail["company_url"])
        if company is None:
            company = upsert_company(name=vacancy.company_name)
        if company is None or not company.slug:
            return company

        already = company.details_status == ParseStatus.FETCHED and (company.about or company.company_size)
        if already and not self._refresh_companies:
            return company
        if company.attempts >= self.max_attempts and company.details_status == ParseStatus.FAILED:
            return company

        page, status_code = self.client.company_page(company.slug)
        if status_code == 200 and (page or "").strip():
            apply_company(company, parse_company(page))
            stats["companies"] += 1
        elif status_code == 404:
            mark_failed(company, f"company page unavailable (HTTP {status_code})", gone=True)
        else:
            mark_failed(company, f"company HTTP {status_code}")
        return company

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._refresh_companies = False
        self.client = None

    def execute(self, *args, **options):
        self._refresh_companies = bool(options.get("refresh_companies"))
        return super().execute(*args, **options)
