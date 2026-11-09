import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from scraper.exporting import export_rows


class Command(BaseCommand):
    help = "Export vacancies + companies to CSV (UTF-8 with BOM for Excel)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="out/vacancies.csv")
        parser.add_argument("--limit", type=int, default=0, help="0 = all rows")

    def handle(self, *args, **options):
        headers, rows = export_rows()
        if options["limit"]:
            rows = rows[: options["limit"]]

        target = Path(options["out"])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"{len(rows)} rows -> {target.resolve()}"))
