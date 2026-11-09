from .models import Vacancy

# (header, attribute path on Vacancy)
COLUMNS = [
    ("linkedin_id", "linkedin_id"),
    ("job_title", "job_title"),
    ("company_name", "company_name"),
    ("location", "location"),
    ("url", "url"),
    ("posted_ago_text", "posted_ago_text"),
    ("posted_ago_days", "posted_ago_days"),
    ("seniority_level", "seniority_level"),
    ("employment_type", "employment_type"),
    ("job_function", "job_function"),
    ("industries", "industries"),
    ("applicants_count", "applicants_count"),
    ("salary_text", "salary_text"),
    ("status", "status"),
    ("first_seen_at", "first_seen_at"),
    # company columns
    ("company_slug", "company.slug"),
    ("company_industry", "company.industry"),
    ("company_size", "company.company_size"),
    ("company_employees_min", "company.employee_range_start"),
    ("company_employees_max", "company.employee_range_end"),
    ("company_headquarters", "company.headquarters"),
    ("company_type", "company.company_type"),
    ("company_founded", "company.founded"),
    ("company_website", "company.website"),
    ("company_followers", "company.follower_count"),
    ("company_linkedin_url", "company.linkedin_url"),
    ("company_specialties", "company.specialties"),
    ("company_about", "company.about"),
    # job content last (long fields)
    ("description", "description"),
]


def _resolve(obj, path: str):
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


def export_rows():
    """Yield (headers, rows) for every vacancy, joined with its company."""
    headers = [header for header, _ in COLUMNS]
    queryset = Vacancy.objects.select_related("company").order_by("linkedin_id").iterator(chunk_size=500)
    rows = []
    for vacancy in queryset:
        row = []
        for _, path in COLUMNS:
            value = _resolve(vacancy, path)
            if isinstance(value, (list, dict)):
                value = "; ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            if hasattr(value, "isoformat"):
                value = value.isoformat(sep=" ", timespec="seconds")
            row.append("" if value is None else value)
        rows.append(row)
    return headers, rows
