from django.core.management.base import BaseCommand

from scraper.geo import resolve_geo_id
from scraper.models import SearchQuery
from scraper.client import LinkedInClient


DEFAULT_QUERIES = [
    ("Python Developer", "United States"),
]


class Command(BaseCommand):
    help = "Seed SearchQuery rows (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--keywords", action="append", help="Keyword set (repeatable).")
        parser.add_argument("--country", action="append", help="Country (repeatable).")
        parser.add_argument("--with-geo", action="store_true", default=True, help="Resolve geoId now (default).")

    def handle(self, *args, **options):
        keywords = options["keywords"] or [k for k, _ in DEFAULT_QUERIES]
        countries = options["country"] or [c for _, c in DEFAULT_QUERIES]

        client = LinkedInClient(min_delay=0.0) if options["with_geo"] else None
        created_count = 0

        for kw in keywords:
            for country in countries:
                query, created = SearchQuery.objects.get_or_create(keywords=kw, country=country)
                if created:
                    created_count += 1
                if options["with_geo"] and not query.geo_id:
                    query.geo_id = resolve_geo_id(country, client)
                    query.save(update_fields=["geo_id", "updated_at"])
                self.stdout.write(
                    f"{'created' if created else 'exists '} #{query.pk}: {kw!r} / {country!r} "
                    f"geoId={query.geo_id or '-'}"
                )

        if client:
            client.session.close()
        self.stdout.write(self.style.SUCCESS(f"{created_count} new search queries"))
