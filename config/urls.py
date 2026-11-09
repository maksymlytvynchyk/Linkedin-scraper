"""
URL configuration.

The project exposes a single admin site (``scraper.admin.parser_admin``) whose
index lists exactly the two required sections — Search queries and Vacancies —
plus Companies, and a `/admin/dashboard/` overview page.
"""
from django.urls import path
from django.http import HttpResponse
from scraper.admin import parser_admin
def home_page(request):
    return HttpResponse(
        "<h1>Головна сторінка працює!</h1><p>Адмінка доступна за адресою /admin/</p>"
    )
urlpatterns = [
    path("admin/", parser_admin.urls),
    path("", home_page),
]