"""Settings for running the test suite without PostgreSQL.

Used by ``manage.py test`` (in-memory) and by the live smoke script, which sets
``SMOKE_DB`` to persist a small crawl to a file.
"""
import os

from .settings import * 

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SMOKE_DB") or ":memory:",
    }
}
