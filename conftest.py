"""Test-session configuration.

The settings module reads configuration from the environment at import time, so
anything the suite depends on has to be set before Django is imported -- hence
this file rather than a fixture.
"""
import os

# A test run must never touch the live Supabase project, the live Redis or the
# live Meilisearch. pytest-django creates test_<NAME> against DB_HOST, so these
# defaults point the suite at the local compose stack and nothing else.
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-in-any-deployment")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_CRED", "postgres")
os.environ.setdefault("DB_PASS", "postgres")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "55432")  # compose publishes Postgres here
os.environ.setdefault("MEILISEARCH_URL", "http://localhost:57700")
os.environ.setdefault("MEILISEARCH_KEY", "devmasterkey")
