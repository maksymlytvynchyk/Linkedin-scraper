import hashlib
import re

from django.db import models
from django.utils import timezone


_VIEW_URL_RE = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{4,})(?:[/?#]|$)")
_COMPANY_SLUG_RE = re.compile(r"/company/([^/?#]+)")


def extract_linkedin_id(*candidates: str | None) -> str | None:
    """Extract the numeric job id from a LinkedIn job URL or urn."""
    for raw in candidates:
        if not raw:
            continue
        urn = re.search(r"urn:li:jobPosting:(\d+)", raw)
        if urn:
            return urn.group(1)
        match = _VIEW_URL_RE.search(raw)
        if match:
            return match.group(1)
    return None


def extract_company_slug(url: str | None) -> str:
    """Extract the universal company name from a LinkedIn company URL.

    Returns ``""`` (never ``None``) when the URL carries no company path, so
    callers can persist the result into a NOT NULL column verbatim.
    """
    if not url:
        return ""
    match = _COMPANY_SLUG_RE.search(url)
    return match.group(1) if match else ""


class Country(models.TextChoices):
    """Convenience list for the admin. Free-text countries are also allowed,
    and the geo id is resolved automatically at runtime."""

    USA = "United States", "USA"
    UK = "United Kingdom", "United Kingdom"
    CANADA = "Canada", "Canada"
    GERMANY = "Germany", "Germany"
    FRANCE = "France", "France"
    SPAIN = "Spain", "Spain"
    NETHERLANDS = "Netherlands", "Netherlands"
    POLAND = "Poland", "Poland"
    PORTUGAL = "Portugal", "Portugal"
    UKRAINE = "Ukraine", "Ukraine"
    INDIA = "India", "India"
    BRAZIL = "Brazil", "Brazil"
    MEXICO = "Mexico", "Mexico"
    AUSTRALIA = "Australia", "Australia"
    SINGAPORE = "Singapore", "Singapore"
    UAE = "United Arab Emirates", "United Arab Emirates"
    ISRAEL = "Israel", "Israel"
    TURKEY = "Turkey", "Turkey"
    JAPAN = "Japan", "Japan"
    REMOTE = "Worldwide", "Worldwide"


POSTED_CHOICES = [
    ("", "Any time"),
    ("r86400", "Past 24 hours"),
    ("r604800", "Past 7 days"),
    ("r2592000", "Past 30 days"),
]

REMOTE_CHOICES = [
    ("", "Any"),
    ("1", "On-site"),
    ("2", "Remote"),
    ("3", "Hybrid"),
]

EXPERIENCE_CHOICES = [
    ("", "Any"),
    ("1", "Internship"),
    ("2", "Entry level"),
    ("3", "Associate"),
    ("4", "Mid-Senior level"),
    ("5", "Director"),
    ("6", "Executive"),
]


class ParseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FETCHED = "fetched", "Fetched"
    FAILED = "failed", "Failed"
    GONE = "gone", "Gone / expired"


class QueryStatus(models.TextChoices):
    NEW = "new", "New"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class SearchQuery(models.Model):
    """A saved search (keywords + country) with resumable pagination state."""

    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional label. Defaults to '<keywords> — <country>'.",
    )
    keywords = models.CharField(max_length=200)
    country = models.CharField(
        max_length=80,
        choices=Country.choices,
        default=Country.USA,
        help_text="Country name passed to LinkedIn's `location` parameter.",
    )
    geo_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="LinkedIn geoId. Auto-resolved from the country when left empty.",
    )

    # Optional LinkedIn search facets.
    posted = models.CharField(max_length=16, blank=True, choices=POSTED_CHOICES, default="")
    remote = models.CharField(max_length=1, blank=True, choices=REMOTE_CHOICES, default="")
    experience = models.CharField(max_length=1, blank=True, choices=EXPERIENCE_CHOICES, default="")
    extra_params = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw LinkedIn query params, e.g. {\"f_C\": \"123\"} for company filter.",
    )

    # Pagination / sharding state — this is what makes restarts safe.
    depth_cap = models.PositiveIntegerField(
        default=1000,
        help_text="LinkedIn stops returning results past roughly 1000 per query.",
    )
    # List of shards: [{"posted": "r86400", "start": 100, "done": false}, ...]
    shard_state = models.JSONField(default=list, blank=True)
    page_size = models.PositiveIntegerField(default=10)
    total_results_hint = models.PositiveIntegerField(
        null=True, blank=True, help_text="Parsed from the search page HTML, if present."
    )

    status = models.CharField(max_length=16, choices=QueryStatus.choices, default=QueryStatus.NEW)
    last_error = models.TextField(blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Search query"
        verbose_name_plural = "Search queries"

    def __str__(self) -> str:
        return self.name or f"{self.keywords} — {self.country}"

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = f"{self.keywords} — {self.country}"
        super().save(*args, **kwargs)

    # shard helpers
    def ensure_shards(self, posted_windows: list[str]) -> None:
        """Initialise the shard plan. ``""`` = no time filter.

        ``depth_cap`` is split evenly across the windows so the broad
        "any time" query cannot consume the whole allowance and starve the
        narrow 7-day / 24-hour windows (which is where the extra coverage
        comes from).
        """
        if self.shard_state:
            return
        windows = posted_windows or [""]
        per_shard = max(self.page_size, self.depth_cap // len(windows))
        self.shard_state = [
            {"posted": p, "start": 0, "done": False, "cap": per_shard} for p in windows
        ]

    def current_shard(self) -> dict | None:
        for shard in self.shard_state or []:
            if not shard.get("done"):
                return shard
        return None

    def advance_shard(self, *, done: bool = False, batch: int = 0) -> None:
        """Mark the cursor of the current shard, or close it."""
        shard = self.current_shard()
        if shard is None:
            return
        if done:
            shard["done"] = True
        else:
            shard["start"] = int(shard.get("start") or 0) + batch
        self.shard_state = list(self.shard_state)  # force JSONField dirty tracking

    def reset_progress(self) -> None:
        for shard in self.shard_state or []:
            shard["start"] = 0
            shard["done"] = False
        self.shard_state = list(self.shard_state)
        self.status = QueryStatus.NEW
        self.last_error = ""

    @property
    def progress(self) -> int:
        """Total cards consumed across finished/active shards."""
        return sum(int(s.get("start") or 0) for s in self.shard_state or [])

    def vacancies_count(self) -> int:
        return self.vacancies.count()


class Company(models.Model):
    """Company data collected from the LinkedIn company page."""

    name = models.CharField(max_length=300, blank=True)
    slug = models.CharField(max_length=300, blank=True, db_index=True)
    linkedin_url = models.URLField(max_length=1000, unique=True)

    tagline = models.CharField(max_length=500, blank=True)
    about = models.TextField(blank=True)
    website = models.URLField(max_length=1000, blank=True)
    industry = models.CharField(max_length=300, blank=True)
    company_size = models.CharField(max_length=120, blank=True)
    employee_range_start = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    employee_range_end = models.PositiveIntegerField(null=True, blank=True)
    headquarters = models.CharField(max_length=300, blank=True)
    company_type = models.CharField(max_length=120, blank=True)
    founded = models.PositiveIntegerField(null=True, blank=True)
    specialties = models.JSONField(default=list, blank=True)
    follower_count = models.PositiveIntegerField(null=True, blank=True)

    details_status = models.CharField(max_length=16, choices=ParseStatus.choices, default=ParseStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    details_fetched_at = models.DateTimeField(null=True, blank=True)
    raw_html = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        indexes = [models.Index(fields=["name"], name="company_name_idx")]

    def __str__(self) -> str:
        return self.name or self.slug or self.linkedin_url

    @classmethod
    def url_for_slug(cls, slug: str) -> str:
        return f"https://www.linkedin.com/company/{slug}"


class Vacancy(models.Model):
    """One LinkedIn job posting, deduplicated on the LinkedIn job id."""

    linkedin_id = models.CharField(max_length=32, unique=True)
    url = models.URLField(max_length=600)
    job_title = models.CharField(max_length=500)
    company_name = models.CharField(max_length=300, blank=True)
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="vacancies"
    )
    location = models.CharField(max_length=300, blank=True)

    # list page (stage 1)
    posted_ago_text = models.CharField(max_length=120, blank=True)
    posted_ago_days = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    actively_hiring = models.BooleanField(default=False)
    early_applicant = models.BooleanField(default=False)
    benefits = models.JSONField(default=list, blank=True)
    search_matched_keywords = models.CharField(max_length=300, blank=True)

    # detail page (stage 2)
    description = models.TextField(blank=True)
    seniority_level = models.CharField(max_length=160, blank=True)
    employment_type = models.CharField(max_length=160, blank=True)
    job_function = models.CharField(max_length=300, blank=True)
    industries = models.CharField(max_length=500, blank=True)
    applicants_count = models.PositiveIntegerField(null=True, blank=True)
    apply_url = models.URLField(max_length=1000, blank=True)
    salary_text = models.CharField(max_length=300, blank=True)
    similar_jobs = models.JSONField(default=list, blank=True)

    # bookkeeping
    status = models.CharField(max_length=16, choices=ParseStatus.choices, default=ParseStatus.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)

    first_seen_query = models.ForeignKey(
        SearchQuery, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    search_queries = models.ManyToManyField(SearchQuery, blank=True, related_name="vacancies")

    raw_card_html = models.TextField(blank=True)
    raw_detail_html = models.TextField(blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True, db_index=True)
    seen_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-first_seen_at"]
        verbose_name = "Vacancy"
        verbose_name_plural = "Vacancies"
        indexes = [
            models.Index(fields=["company_name", "location"], name="vacancy_company_loc_idx"),
            models.Index(fields=["status", "attempts"], name="vacancy_status_attempt_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.job_title} @ {self.company_name}"

    def short_id(self) -> str:
        return hashlib.md5(self.linkedin_id.encode()).hexdigest()[:8]

    @property
    def is_enriched(self) -> bool:
        return bool(self.description)
