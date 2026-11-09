from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scraper.parsers import parse_company, parse_job_cards, parse_job_detail


class Command(BaseCommand):
    help = "Parse local HTML files with the current selectors (offline check)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Directory with .html files")

    def handle(self, *args, **options):
        directory = Path(options["path"])
        if not directory.exists():
            raise CommandError(f"{directory} not found")

        for file in sorted(directory.glob("*.html")):
            html = file.read_text(encoding="utf-8")
            cards = parse_job_cards(html)
            if cards:
                self.stdout.write(self.style.SUCCESS(f"{file.name}: {len(cards)} cards"))
                for card in cards[:3]:
                    self.stdout.write(
                        f"  {card['linkedin_id']} | {card['job_title']} | {card['company_name']} "
                        f"| {card['location']} | {card['posted_ago_text']}"
                    )
                continue

            detail = parse_job_detail(html)
            if detail["description"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{file.name}: detail | {detail['seniority_level']} | "
                        f"{detail['employment_type']} | applicants={detail['applicants_count']}"
                    )
                )
                continue

            company = parse_company(html)
            if company["name"] or company["about"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{file.name}: company | {company['name']} | {company['industry']} "
                        f"| {company['company_size']} | {company['headquarters']}"
                    )
                )
                continue

            self.stdout.write(self.style.WARNING(f"{file.name}: could not classify"))
