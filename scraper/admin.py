from django.contrib import admin, messages
from django.db.models import Count
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from .models import Company, ParseStatus, QueryStatus, SearchQuery, Vacancy


class DashboardAdminSite(admin.AdminSite):
    site_header = "LinkedIn vacancies parser"
    site_title = "LinkedIn parser"
    index_title = "Overview"

    def get_urls(self):
        custom = [
            path("dashboard/", self.admin_view(self.dashboard_view), name="dashboard"),
        ]
        return custom + super().get_urls()

    def dashboard_view(self, request):
        vac = Vacancy.objects
        context = {
            **self.each_context(request),
            "title": "Parser dashboard",
            "totals": {
                "vacancies": vac.count(),
                "enriched": vac.filter(status=ParseStatus.FETCHED).count(),
                "pending": vac.filter(status=ParseStatus.PENDING).count(),
                "failed": vac.filter(status=ParseStatus.FAILED).count(),
                "gone": vac.filter(status=ParseStatus.GONE).count(),
                "companies": Company.objects.count(),
                "companies_enriched": Company.objects.filter(details_status=ParseStatus.FETCHED).count(),
                "queries": SearchQuery.objects.count(),
            },
            "top_companies": Company.objects.annotate(n=Count("vacancies")).filter(n__gt=0).order_by("-n")[:10],
            "top_locations": (
                vac.exclude(location="").values("location").annotate(n=Count("id")).order_by("-n")[:10]
            ),
            "recent_queries": SearchQuery.objects.order_by("-created_at")[:10],
            "status_breakdown": [
                {"label": label, "value": vac.filter(status=value).count()}
                for value, label in ParseStatus.choices
            ],
        }
        return TemplateResponse(request, "admin/dashboard.html", context)


# NB: the name must not be "admin" — Django reserves that for the default site
# instance and would ignore this one when resolving admin URLs.
parser_admin = DashboardAdminSite(name="parser")


# Search queries
@admin.register(SearchQuery, site=parser_admin)
class SearchQueryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "keywords",
        "country",
        "geo_id",
        "status_badge",
        "progress",
        "vacancies_found",
        "last_run_at",
    )
    list_filter = ("status", "country", "posted", "remote", "experience", "created_at")
    search_fields = ("name", "keywords", "country", "geo_id")
    readonly_fields = (
        "status",
        "last_error",
        "last_run_at",
        "shard_state_pretty",
        "total_results_hint",
        "progress",
        "created_at",
        "updated_at",
        "run_instructions",
    )
    fieldsets = (
        ("Search parameters", {
            "fields": ("name", "keywords", "country", "geo_id"),
            "description": "geoId is resolved automatically from the country when left empty.",
        }),
        ("LinkedIn facets (optional)", {
            "fields": ("posted", "remote", "experience", "extra_params"),
            "classes": ("collapse",),
        }),
        ("Pagination state", {
            "fields": ("depth_cap", "page_size", "shard_state_pretty", "progress", "total_results_hint"),
        }),
        ("Parsing status", {
            "fields": ("status", "last_error", "last_run_at", "run_instructions", "created_at", "updated_at"),
        }),
    )
    actions = ("reset_progress", "mark_done", "queue_reset")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_vacancies=Count("vacancies", distinct=True))

    @admin.display(description="Status")
    def status_badge(self, obj):

        return f'{obj.get_status_display()}'

    @admin.display(description="Cards read")
    def progress(self, obj):
        return obj.progress

    @admin.display(description="Vacancies", ordering="_vacancies")
    def vacancies_found(self, obj):
        return getattr(obj, "_vacancies", obj.vacancies_count())

    @admin.display(description="Shard state")
    def shard_state_pretty(self, obj):
        if not obj.shard_state:
            return "— (initialised on the first run)"
        rows = [
            f"{s.get('posted') or 'any time'}: start={s.get('start', 0)}"
            f"{' done' if s.get('done') else ''}"
            for s in obj.shard_state
        ]
        return " | ".join(rows)

    @admin.display(description="How to run")
    def run_instructions(self, obj):
        pk = obj.pk or "<id>"
        return (
            f'python manage.py parse_search --query-id {pk}\n'
            f'python manage.py parse_details --all --with-company'
        )

    # actions
    @admin.action(description="Reset pagination progress (re-crawl)")
    def reset_progress(self, request, queryset):
        for query in queryset:
            query.reset_progress()
            query.save(update_fields=["shard_state", "status", "last_error", "updated_at"])
        self.message_user(request, f"Progress reset for {queryset.count()} queries.", messages.SUCCESS)

    @admin.action(description="Mark as done")
    def mark_done(self, request, queryset):
        queryset.update(status=QueryStatus.DONE, updated_at=timezone.now())
        self.message_user(request, "Marked done.", messages.SUCCESS)

    @admin.action(description="Re-queue their vacancies for detail parsing")
    def queue_reset(self, request, queryset):
        ids = Vacancy.objects.filter(search_queries__in=queryset).values_list("id", flat=True)
        updated = Vacancy.objects.filter(id__in=ids).update(
            status=ParseStatus.PENDING, attempts=0, last_error=""
        )
        self.message_user(request, f"{updated} vacancies re-queued for stage 2.", messages.SUCCESS)


# Vacancies
@admin.register(Vacancy, site=parser_admin)
class VacancyAdmin(admin.ModelAdmin):
    list_display = (
        "job_title",
        "company_link",
        "location",
        "posted_ago_text",
        "status_badge",
        "attempts",
        "applicants_count",
        "salary_text",
        "first_seen_at",
    )
    list_filter = (
        "status",
        ("company", admin.RelatedOnlyFieldListFilter),
        "employment_type",
        "seniority_level",
        "actively_hiring",
        "posted_ago_days",
        "first_seen_at",
    )
    search_fields = ("job_title", "company_name", "location", "linkedin_id", "description")
    date_hierarchy = "first_seen_at"
    readonly_fields = (
        "linkedin_id",
        "url_link",
        "company_name",
        "company_link",
        "search_queries_list",
        "status",
        "attempts",
        "last_error",
        "detail_fetched_at",
        "first_seen_at",
        "seen_at",
        "updated_at",
        "short_id",
    )
    autocomplete_fields = ("company",)
    filter_horizontal = ("search_queries",)
    fieldsets = (
        ("Posting", {
            "fields": ("linkedin_id", "job_title", "url_link", "company", "company_name",
                       "company_link", "location", "posted_ago_text", "posted_ago_days",
                       "actively_hiring", "early_applicant", "benefits", "search_matched_keywords"),
        }),
        ("Details", {
            "fields": ("description", "seniority_level", "employment_type", "job_function",
                       "industries", "applicants_count", "salary_text", "apply_url", "similar_jobs"),
        }),
        ("Processing", {
            "fields": ("status", "attempts", "last_error", "detail_fetched_at",
                       "first_seen_at", "seen_at", "updated_at", "search_queries_list", "short_id"),
        }),
    )
    actions = ("requeue_details", "mark_gone")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("company")

    @admin.display(description="Company", ordering="company_name")
    def company_link(self, obj):
        if obj.company_id and obj.company.linkedin_url.startswith("http"):
            return (
                f'{obj.company.linkedin_url}\n'
            )
        return obj.company_name

    @admin.display(description="Status")
    def status_badge(self, obj):
        return f'{obj.get_status_display()}'

    @admin.display(description="LinkedIn URL")
    def url_link(self, obj):
        if not obj.url:
            return "—"
        return f'{obj.url}'

    @admin.display(description="Matched by")
    def search_queries_list(self, obj):
        return ", ".join(str(q) for q in obj.search_queries.all()) or "—"

    @admin.action(description="Re-queue for detail parsing")
    def requeue_details(self, request, queryset):
        updated = queryset.update(status=ParseStatus.PENDING, attempts=0, last_error="")
        self.message_user(request, f"{updated} vacancies re-queued.", messages.SUCCESS)

    @admin.action(description="Mark as gone (expired)")
    def mark_gone(self, request, queryset):
        updated = queryset.update(status=ParseStatus.GONE, updated_at=timezone.now())
        self.message_user(request, f"{updated} vacancies marked gone.", messages.SUCCESS)


# Companies
@admin.register(Company, site=parser_admin)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "industry",
        "company_size",
        "headquarters",
        "founded",
        "vacancies_total",
        "details_status",
        "details_fetched_at",
    )
    list_filter = ("details_status", "industry", "company_type")
    search_fields = ("name", "slug", "industry", "headquarters")
    # ``search_fields`` drives the Vacancy admin autocomplete widget.
    readonly_fields = ("slug", "followers_display", "vacancies_total", "details_status",
                       "attempts", "last_error", "details_fetched_at", "created_at", "updated_at")
    fieldsets = (
        ("Identity", {"fields": ("name", "slug", "tagline", "website", "linkedin_url")}),
        ("Facts", {"fields": ("industry", "company_size", "employee_range_start",
                              "employee_range_end", "headquarters", "company_type",
                              "founded", "specialties", "followers_display")}),
        ("About", {"fields": ("about",)}),
        ("Parsing", {"fields": ("details_status", "attempts", "last_error",
                                "details_fetched_at", "vacancies_total", "created_at", "updated_at")}),
    )
    actions = ("requeue_company_details",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_vacancies=Count("vacancies"))

    @admin.display(description="Vacancies", ordering="_vacancies")
    def vacancies_total(self, obj):
        return getattr(obj, "_vacancies", 0)

    @admin.display(description="Followers")
    def followers_display(self, obj):
        return f"{obj.follower_count:,}" if obj.follower_count else "—"

    @admin.action(description="Re-queue company profile fetch")
    def requeue_company_details(self, request, queryset):
        updated = queryset.update(
            details_status=ParseStatus.PENDING, attempts=0, last_error=""
        )
        self.message_user(request, f"{updated} companies re-queued.", messages.SUCCESS)
