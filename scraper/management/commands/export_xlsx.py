from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scraper.exporting import export_rows


class Command(BaseCommand):
    help = "Export vacancies + companies to a styled .xlsx workbook."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="out/vacancies.xlsx")

    def handle(self, *args, **options):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            raise CommandError("pip install openpyxl") from exc

        headers, rows = export_rows()
        target = Path(options["out"])
        target.parent.mkdir(parents=True, exist_ok=True)

        workbook = Workbook(write_only=False)
        sheet = workbook.active
        sheet.title = "Vacancies"

        header_fill = PatternFill("solid", fgColor="0A66C2")
        header_font = Font(color="FFFFFF", bold=True)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="center")

        for row in rows:
            sheet.append([str(value)[:32000] if value != "" else "" for value in row])

        widths = {"job_title": 38, "company_name": 28, "location": 22, "description": 80,
                  "company_about": 60}
        for index, header in enumerate(headers, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = widths.get(header, 18)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        workbook.save(target)
        self.stdout.write(self.style.SUCCESS(f"{len(rows)} rows -> {target.resolve()}"))
