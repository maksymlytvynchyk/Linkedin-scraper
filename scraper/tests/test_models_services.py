"""Deduplication and resume-state tests."""
from django.test import TestCase

from scraper.models import ParseStatus, SearchQuery, Vacancy
from scraper.parsers import parse_job_cards
from scraper.services import upsert_company, upsert_vacancy
from pathlib import Path


def fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


class DedupTests(TestCase):
    def setUp(self):
        self.cards = parse_job_cards(fixture("sample_search.html"))
        self.query = SearchQuery.objects.create(keywords="Python Developer", country="United States")

    def test_same_card_twice_creates_one_row(self):
        first, created_1 = upsert_vacancy(self.cards[0], self.query)
        second, created_2 = upsert_vacancy(self.cards[0], self.query)
        self.assertTrue(created_1)
        self.assertFalse(created_2)
        self.assertEqual(Vacancy.objects.count(), 1)
        self.assertEqual(first.pk, second.pk)

    def test_second_search_links_without_duplicating(self):
        other = SearchQuery.objects.create(keywords="Backend Engineer", country="Canada")
        upsert_vacancy(self.cards[0], self.query)
        vacancy, created = upsert_vacancy(self.cards[0], other)
        self.assertFalse(created)
        self.assertEqual(Vacancy.objects.count(), 1)
        self.assertEqual(vacancy.search_queries.count(), 2)

    def test_company_deduplicated_by_url(self):
        upsert_vacancy(self.cards[0], self.query)
        upsert_vacancy(self.cards[1], self.query)
        # two distinct companies in the fixture
        self.assertEqual(upsert_company(name="CACI International Inc", slug="caci-international-inc").vacancies.count(), 1)
        self.assertEqual(upsert_company(name="Renamed CACI", slug="caci-international-inc").pk,
                         upsert_company(name="CACI", slug="caci-international-inc").pk)

    def test_company_without_page_keyed_by_name(self):
        first = upsert_company(name="Anonymous Corp")
        second = upsert_company(name="Anonymous Corp")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.linkedin_url, "urn:company-name:anonymous corp")

    def test_card_refresh_updates_mutable_fields(self):
        vacancy, _ = upsert_vacancy(self.cards[0], self.query)
        vacancy.status = ParseStatus.FETCHED
        vacancy.save()

        changed = dict(self.cards[1])
        changed["linkedin_id"] = self.cards[0]["linkedin_id"]
        changed["job_title"] = "Python Developer (Updated)"
        upsert_vacancy(changed, self.query)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.job_title, "Python Developer (Updated)")
        # a re-discovery must not reset stage-2 status
        self.assertEqual(vacancy.status, ParseStatus.FETCHED)


class ShardStateTests(TestCase):
    def test_shard_lifecycle(self):
        query = SearchQuery.objects.create(keywords="Python", country="United States")
        query.ensure_shards(["", "r604800"])
        self.assertEqual(len(query.shard_state), 2)
        self.assertIsNotNone(query.current_shard())

        query.advance_shard(batch=10)
        self.assertEqual(query.current_shard()["start"], 10)
        self.assertEqual(query.progress, 10)

        query.advance_shard(done=True)
        self.assertEqual(query.current_shard()["posted"], "r604800")

        query.advance_shard(done=True)
        self.assertIsNone(query.current_shard())

        query.reset_progress()
        self.assertIsNotNone(query.current_shard())
        self.assertEqual(query.progress, 0)

    def test_ensure_shards_is_idempotent(self):
        query = SearchQuery.objects.create(keywords="Python", country="United States")
        query.ensure_shards(["r86400"])
        query.ensure_shards(["", "r604800"])
        self.assertEqual(len(query.shard_state), 1)

    def test_str_defaults_to_name(self):
        query = SearchQuery.objects.create(keywords="Python Developer", country="United States")
        self.assertEqual(str(query), "Python Developer — United States")
