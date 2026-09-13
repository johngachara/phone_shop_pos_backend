"""Bootstrap tests: the stack can start at all.

These are deliberately about importability and configuration rather than
behaviour. Every one of them corresponds to something that actually broke the
local stack while it was being stood up, so they exist to stop it regressing.
"""
import importlib

import pytest
from django.urls import reverse


def test_settings_import_without_optional_credentials(settings):
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


def test_allowed_hosts_contains_no_none(settings):
    # os.getenv returns None for unset vars, and a None here makes every host
    # check raise instead of failing cleanly.
    assert all(host is not None for host in settings.ALLOWED_HOSTS)


def test_debug_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    import djangoProject15.settings as settings_module
    reloaded = importlib.reload(settings_module)
    try:
        assert reloaded.DEBUG is False
    finally:
        monkeypatch.setenv("DEBUG", "True")
        importlib.reload(settings_module)


@pytest.mark.parametrize("module", [
    "Alltechmanagement.FCMManager",
    "Alltechmanagement.GPTAgent",
    "Alltechmanagement.health",
    "Alltechmanagement.views",
    "Alltechmanagement.admin_apis",
    "Alltechmanagement.urls",
])
def test_module_imports_without_external_credentials(module):
    # Firebase and the AI clients used to be constructed at import time, so a
    # missing key_pair.json or GITHUB_TOKEN took down the entire process.
    importlib.import_module(module)


def test_fcm_get_ref_returns_none_without_credentials(monkeypatch):
    from Alltechmanagement import FCMManager
    monkeypatch.setattr(FCMManager, "_initialized", False)
    monkeypatch.setattr(FCMManager.settings, "KEY", "/nonexistent/key_pair.json")
    monkeypatch.setattr(FCMManager.firebase_admin, "_apps", {})
    assert FCMManager.get_ref() is None


def test_gpt_agent_raises_configuration_error_without_keys(monkeypatch):
    from Alltechmanagement import GPTAgent
    monkeypatch.setattr(GPTAgent, "_openai_client", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GPTAgent.AIConfigurationError):
        GPTAgent.get_openai_client()


@pytest.mark.django_db
def test_health_endpoint_reports_database_ok(client):
    response = client.get(reverse("health"))
    assert response.status_code in (200, 503)
    body = response.json()
    assert body["checks"]["database"] == "ok"
    # The probe is unauthenticated, so it must not leak infrastructure detail.
    assert set(body["checks"].values()) <= {"ok", "unavailable"}
