"""Parser tests against real captured markup (no network)."""
from pathlib import Path

from django.test import SimpleTestCase

from scraper.models import extract_company_slug, extract_linkedin_id
from scraper.parsers import (
    parse_company,
    parse_company_size,
    parse_followers,
    parse_job_cards,
    parse_job_detail,
    posted_age_days,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class IdentifierTests(SimpleTestCase):
    def test_extract_id_from_view_url(self):
        self.assertEqual(
            extract_linkedin_id("https://www.linkedin.com/jobs/view/python-developer-at-caci-international-inc-4431992044?position=1"),
            "4431992044",
        )

    def test_extract_id_from_urn(self):
        self.assertEqual(extract_linkedin_id("urn:li:jobPosting:4465604021"), "4465604021")

    def test_extract_id_plain_url(self):
        self.assertEqual(extract_linkedin_id("https://www.linkedin.com/jobs/view/4475278264/"), "4475278264")

    def test_extract_id_missing(self):
        self.assertIsNone(extract_linkedin_id(None, "", "https://example.com/nothing"))

    def test_company_slug(self):
        self.assertEqual(
            extract_company_slug("https://www.linkedin.com/company/caci-international-inc?trk=x"),
            "caci-international-inc",
        )


class SearchCardTests(SimpleTestCase):
    def setUp(self):
        self.cards = parse_job_cards(fixture("sample_search.html"))

    def test_card_count(self):
        self.assertEqual(len(self.cards), 3)

    def test_first_card_fields(self):
        job = self.cards[0]
        self.assertEqual(job["linkedin_id"], "4431992044")
        self.assertEqual(job["job_title"], "Python Developer")
        self.assertEqual(job["company_name"], "CACI International Inc")
        self.assertEqual(job["company_slug"], "caci-international-inc")
        self.assertEqual(job["location"], "Chantilly, VA")
        self.assertEqual(job["posted_ago_text"], "3 months ago")
        self.assertEqual(job["posted_ago_days"], 90)
        self.assertTrue(job["actively_hiring"])
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/4431992044/")

    def test_second_card_early_applicant(self):
        job = self.cards[1]
        self.assertEqual(job["posted_ago_days"], 6)
        self.assertTrue(job["early_applicant"])

    def test_empty_html(self):
        self.assertEqual(parse_job_cards(""), [])
        self.assertEqual(parse_job_cards("<ul></ul>"), [])


class DetailTests(SimpleTestCase):
    def setUp(self):
        self.detail = parse_job_detail(fixture("sample_detail.html"))

    def test_description(self):
        self.assertIn("skilled Python Developer", self.detail["description"])
        self.assertIn("PostgreSQL", self.detail["description"])

    def test_criteria(self):
        self.assertEqual(self.detail["seniority_level"], "Not Applicable")
        self.assertEqual(self.detail["employment_type"], "Full-time")
        self.assertEqual(self.detail["job_function"], "Information Technology")
        self.assertEqual(self.detail["industries"], "Defense and Space Manufacturing")

    def test_applicants_and_salary(self):
        self.assertEqual(self.detail["applicants_count"], 75)
        self.assertIn("103,800", self.detail["salary_text"])

    def test_company_url(self):
        self.assertTrue(self.detail["company_url"].startswith("https://www.linkedin.com/company/caci"))


class CompanyTests(SimpleTestCase):
    def setUp(self):
        self.company = parse_company(fixture("sample_company.html"))

    def test_identity(self):
        self.assertEqual(self.company["name"], "CACI International Inc")
        self.assertIn("national security", self.company["tagline"])

    def test_facts(self):
        self.assertEqual(self.company["industry"], "Defense and Space Manufacturing")
        self.assertEqual(self.company["company_size"], "10,001+ employees")
        self.assertEqual(self.company["headquarters"], "Reston, Virginia")
        self.assertEqual(self.company["company_type"], "Public Company")
        self.assertEqual(self.company["founded"], 1962)
        self.assertEqual(self.company["employee_range_start"], 10001)
        self.assertIsNone(self.company["employee_range_end"])
        self.assertEqual(self.company["follower_count"], 298424)

    def test_website_unwrapped_from_redirect(self):
        self.assertEqual(self.company["website"], "http://www.caci.com")

    def test_about(self):
        self.assertIn("NYSE: CACI", self.company["about"])


class HelperTests(SimpleTestCase):
    def test_posted_age_days(self):
        self.assertEqual(posted_age_days("3 months ago"), 90)
        self.assertEqual(posted_age_days("1 week ago"), 7)
        self.assertEqual(posted_age_days("Reposted 2 days ago"), 2)
        self.assertIsNone(posted_age_days(""))

    def test_company_size(self):
        self.assertEqual(parse_company_size("51-200 employees"), (51, 200))
        self.assertEqual(parse_company_size("10,001+ employees"), (10001, None))
        self.assertEqual(parse_company_size(""), (None, None))

    def test_followers(self):
        self.assertEqual(parse_followers("298,424"), 298424)
        self.assertEqual(parse_followers("1.2M"), 1_200_000)
        self.assertEqual(parse_followers("15K"), 15000)
        self.assertIsNone(parse_followers(""))
