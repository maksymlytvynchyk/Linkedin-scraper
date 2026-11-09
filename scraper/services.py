import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Company, ParseStatus, SearchQuery, Vacancy, extract_company_slug

logger = logging.getLogger("scraper.services")

# Fields refreshed on every re-discovery (cheap, changes over time).
_CARD_REFRESH_FIELDS = [
    "job_title",
    "company_name",
    "location",
    "posted_ago_text",
    "posted_ago_days",
    "actively_hiring",
    "early_applicant",
    "benefits",
    "url",
    "seen_at",
    "raw_card_html",
]


def upsert_company(*, name: str = "", slug: str = "", url: str = "") -> Company | None:
    """Get or create a company keyed by its LinkedIn URL (or slug).

    ``slug`` must never reach the database as ``None``: every character column
    is ``NOT NULL`` and stores ``""`` for "unknown". ``extract_company_slug``
    returns ``None`` for URLs without a company path, so coalesce explicitly.
    """
    url = (url or "").strip()
    slug = (slug or "").strip() or (extract_company_slug(url) or "")

    if not url and slug:
        url = Company.url_for_slug(slug)
    if not url:
        if not name:
            return None
        # No company page known: key on a pseudo-URL so duplicates still collapse.
        url = f"urn:company-name:{name.strip().lower()[:300]}"

    defaults = {"name": name or "", "slug": slug}
    try:
        company, _ = Company.objects.get_or_create(linkedin_url=url, defaults=defaults)
    except IntegrityError:
        # Concurrent insert from another worker.
        company = Company.objects.get(linkedin_url=url)
        company.slug = company.slug or ""

    updates = []
    if name and company.name != name:
        company.name = name
        updates.append("name")
    if slug and company.slug != slug:
        company.slug = slug
        updates.append("slug")
    if updates:
        company.save(update_fields=[*updates, "updated_at"])
    return company


def upsert_vacancy(
    card: dict, search: SearchQuery | None = None, *, commit: bool = True
) -> tuple[Vacancy, bool]:
    """Insert or refresh one vacancy from a parsed search card.

    Returns ``(vacancy, created)``. Deduplication key: ``linkedin_id``.
    """
    with transaction.atomic():
        company = upsert_company(
            name=card.get("company_name", ""),
            slug=card.get("company_slug", ""),
            url=card.get("company_url", ""),
        )

        defaults = {
            "url": card.get("url") or f"https://www.linkedin.com/jobs/view/{card['linkedin_id']}/",
            "job_title": (card.get("job_title") or "")[:500],
            "company_name": (card.get("company_name") or "")[:300],
            "company": company,
            "location": (card.get("location") or "")[:300],
            "posted_ago_text": (card.get("posted_ago_text") or "")[:120],
            "posted_ago_days": card.get("posted_ago_days"),
            "actively_hiring": bool(card.get("actively_hiring")),
            "early_applicant": bool(card.get("early_applicant")),
            "benefits": card.get("benefits") or [],
            "raw_card_html": card.get("raw_card_html") or "",
            "seen_at": timezone.now(),
            "first_seen_query": search,
        }
        if search is not None:
            defaults["search_matched_keywords"] = search.keywords[:300]

        try:
            vacancy, created = Vacancy.objects.get_or_create(
                linkedin_id=str(card["linkedin_id"]), defaults=defaults
            )
        except IntegrityError:
            vacancy = Vacancy.objects.get(linkedin_id=str(card["linkedin_id"]))
            created = False

        if not created:
            changed = []
            for field in _CARD_REFRESH_FIELDS:
                if field == "raw_card_html" and vacancy.raw_card_html:
                    continue
                new_value = defaults.get(field)
                if new_value in (None, "") and getattr(vacancy, field):
                    continue
                if getattr(vacancy, field) != new_value and new_value is not None:
                    setattr(vacancy, field, new_value)
                    changed.append(field)
            if company and vacancy.company_id != company.id:
                vacancy.company = company
                changed.append("company")
            if changed:
                vacancy.save(update_fields=[*changed, "updated_at"])

        if search is not None:
            vacancy.search_queries.add(search)

    return vacancy, created


def apply_detail(vacancy: Vacancy, detail: dict, *, company: Company | None = None) -> Vacancy:
    """Write parsed detail data onto a vacancy and mark it fetched."""
    fields = [
        "description",
        "seniority_level",
        "employment_type",
        "job_function",
        "industries",
        "applicants_count",
        "apply_url",
        "salary_text",
        "similar_jobs",
        "raw_detail_html",
    ]
    for field in fields:
        value = detail.get(field)
        if value in (None, ""):
            continue
        setattr(vacancy, field, value)

    if company is not None and vacancy.company_id != company.id:
        vacancy.company = company

    vacancy.status = ParseStatus.FETCHED
    vacancy.detail_fetched_at = timezone.now()
    vacancy.last_error = ""
    vacancy.attempts = (vacancy.attempts or 0) + 1
    vacancy.save()
    return vacancy


def apply_company(company: Company, data: dict) -> Company:
    """Write parsed company page data."""
    mapping = {
        "name": "name",
        "tagline": "tagline",
        "about": "about",
        "website": "website",
        "industry": "industry",
        "company_size": "company_size",
        "employee_range_start": "employee_range_start",
        "employee_range_end": "employee_range_end",
        "headquarters": "headquarters",
        "company_type": "company_type",
        "founded": "founded",
        "specialties": "specialties",
        "follower_count": "follower_count",
        "raw_html": "raw_html",
    }
    for source, target in mapping.items():
        value = data.get(source)
        if value in (None, "", []):
            continue
        setattr(company, target, value)

    company.details_status = ParseStatus.FETCHED
    company.details_fetched_at = timezone.now()
    company.attempts = (company.attempts or 0) + 1
    company.last_error = ""
    company.save()
    return company


def mark_failed(obj, error: str, *, gone: bool = False) -> None:
    """Record a parsing failure without losing the row."""
    if isinstance(obj, Company):
        obj.details_status = ParseStatus.GONE if gone else ParseStatus.FAILED
    else:
        obj.status = ParseStatus.GONE if gone else ParseStatus.FAILED
    obj.attempts = (obj.attempts or 0) + 1
    obj.last_error = (error or "")[:2000]
    obj.save()
