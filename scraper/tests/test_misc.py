"""End-to-end smoke tests: admin pages and the CSV/XLSX exports."""
import csv
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from scraper.models import SearchQuery
from scraper.parsers import parse_job_cards
from scraper.services import apply_detail, upsert_vacancy


def fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


class BaseData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.query = SearchQuery.objects.create(keywords="Python Developer", country="United States")
        for card in parse_job_cards(fixture("sample_search.html")):
            vacancy, _ = upsert_vacancy(card, cls.query)
            apply_detail(vacancy, {"description": "Test description", "employment_type": "Full-time"})


class ExportTests(BaseData):
    def test_export_csv_contains_rows_and_company_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "vacancies.csv"
            call_command("export_csv", "--out", str(target), verbosity=0)
            self.assertTrue(target.exists())

            with target.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))

        header, *body = rows
        self.assertEqual(len(body), 3)
        self.assertIn("linkedin_id", header)
        self.assertIn("company_industry", header)
        self.assertIn("description", header)
        self.assertEqual(header.index("job_title"), 1)

    def test_export_xlsx_contains_rows(self):
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "vacancies.xlsx"
            call_command("export_xlsx", "--out", str(target), verbosity=0)
            self.assertTrue(target.exists())

            workbook = load_workbook(target)
            sheet = workbook.active
            self.assertEqual(sheet.max_row, 4)  # header + 3 vacancies
            self.assertEqual(sheet["A1"].value, "linkedin_id")
            self.assertEqual(sheet["B1"].value, "job_title")


class AdminSmokeTests(BaseData):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_admin_index_lists_required_sections(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Search queries", content)
        self.assertIn("Vacancies", content)

    def test_dashboard_renders(self):
        response = self.client.get("/admin/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parser dashboard")

    def test_changelists_render(self):
        for path in (
            "/admin/scraper/searchquery/",
            "/admin/scraper/vacancy/",
            "/admin/scraper/company/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_vacancy_change_page_renders(self):
        from scraper.models import Vacancy

        vacancy = Vacancy.objects.first()
        response = self.client.get(f"/admin/scraper/vacancy/{vacancy.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, vacancy.linkedin_id)

    def test_search_query_add_page_renders(self):
        self.assertEqual(self.client.get("/admin/scraper/searchquery/add/").status_code, 200)


@override_settings(LINKEDIN_MIN_DELAY=0.0)
class CommandParsingTests(TestCase):
    """Commands must no-op cleanly when there is no work."""

    def test_parse_details_without_vacancies(self):
        call_command("parse_details", "--limit", "5", verbosity=0)

    def test_replay_samples_reads_fixtures(self):
        fixtures = Path(__file__).parent / "fixtures"
        call_command("replay_samples", str(fixtures), verbosity=0)
