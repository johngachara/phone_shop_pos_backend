"""Bootstrap tests: the stack can start at all.

These are deliberately about importability and configuration rather than
behaviour. Every one of them corresponds to something that actually broke the
local stack while it was being stood up, so they exist to stop it regressing.
"""
import importlib
from decimal import Decimal

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
    "Alltechmanagement.ai.provider",
    "Alltechmanagement.ai.tools",
    "Alltechmanagement.ai.views",
    "Alltechmanagement.health",
    "Alltechmanagement.views",
    "Alltechmanagement.admin_apis",
    "Alltechmanagement.urls",
])
def test_module_imports_without_external_credentials(module):
    # Firebase and the AI clients used to be constructed at import time, so a
    # missing key_pair.json or model API key took down the entire process.
    importlib.import_module(module)


def test_fcm_get_ref_returns_none_without_credentials(monkeypatch):
    from Alltechmanagement import FCMManager
    monkeypatch.setattr(FCMManager, "_initialized", False)
    monkeypatch.setattr(FCMManager.settings, "KEY", "/nonexistent/key_pair.json")
    monkeypatch.setattr(FCMManager.firebase_admin, "_apps", {})
    # Both credential sources have to be absent: env vars are tried first now,
    # and a real .env supplies them.
    for name in FCMManager._ENV_TO_FIELD:
        monkeypatch.delenv(name, raising=False)
    assert FCMManager.get_ref() is None


def test_fcm_builds_credentials_from_env(monkeypatch):
    from Alltechmanagement import FCMManager
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("FIREBASE_CLIENT_EMAIL", "svc@demo.iam.gserviceaccount.com")
    monkeypatch.setenv(
        "FIREBASE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nAAAA\\nBBBB\\n-----END PRIVATE KEY-----\\n",
    )
    info = FCMManager._credentials_from_env()
    assert info["type"] == "service_account"
    assert info["project_id"] == "demo-project"
    # Environment variables cannot carry real newlines; if the literal \n
    # escapes survive, the PEM parses as garbage and every push fails opaquely.
    assert "\\n" not in info["private_key"]
    assert info["private_key"].count("\n") == 4


def test_fcm_env_credentials_need_the_required_fields(monkeypatch):
    from Alltechmanagement import FCMManager
    for name in FCMManager._ENV_TO_FIELD:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    assert FCMManager._credentials_from_env() is None


def test_ai_provider_raises_when_no_key_is_configured(monkeypatch):
    from Alltechmanagement.ai import provider
    monkeypatch.setattr(provider, "_client", None)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(provider.AIUnavailable):
        provider.get_client()


@pytest.mark.django_db
def test_health_endpoint_reports_database_ok(client):
    response = client.get(reverse("health"))
    assert response.status_code in (200, 503)
    body = response.json()
    assert body["checks"]["database"] == "ok"
    # The probe is unauthenticated, so it must not leak infrastructure detail.
    assert set(body["checks"].values()) <= {"ok", "unavailable"}


@pytest.mark.django_db
def test_sale_profit_is_none_when_buying_price_unknown():
    from Alltechmanagement.models import Sale
    sale = Sale.objects.create(
        product_name="Screen A", quantity=3, selling_price="100.00",
        customer_name="jane",
    )
    assert sale.total_amount == 300
    # None, not zero: an unrecorded cost is not a free item, and reporting it
    # as zero would overstate profit.
    assert sale.profit is None


@pytest.mark.django_db
def test_sale_profit_uses_price_captured_at_sale_time():
    from Alltechmanagement.models import Sale, Stock
    stock = Stock.objects.create(
        product_name="Screen B", quantity=10,
        selling_price="100.00", buying_price="60.00",
    )
    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=2,
        selling_price="100.00", buying_price=stock.buying_price,
        customer_name="jane", stock=stock,
    )
    assert sale.profit == 80

    # Restocking at a higher cost must not rewrite the profit already booked.
    stock.buying_price = "90.00"
    stock.save(update_fields=["buying_price"])
    sale.refresh_from_db()
    assert sale.profit == 80


@pytest.mark.django_db
def test_deleting_stock_keeps_the_sale_record():
    from Alltechmanagement.models import Sale, Stock
    stock = Stock.objects.create(
        product_name="Screen C", quantity=1, selling_price="50.00",
        buying_price="30.00",
    )
    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=1,
        selling_price="50.00", customer_name="jane", stock=stock,
    )
    stock.delete()
    sale.refresh_from_db()
    # A sale is a historical record; it must survive the item being removed,
    # keeping the name and price it was actually sold under.
    assert sale.product_name == "Screen C"
    assert sale.selling_price == Decimal("50.00")
    assert sale.stock is None


@pytest.mark.django_db
def test_money_properties_survive_string_assignment():
    from Alltechmanagement.models import Sale
    # Django leaves Python-assigned values untouched until reload, so a string
    # price would make `price * quantity` string repetition rather than
    # arithmetic. That produced "100.00100.00100.00" as a money amount.
    sale = Sale(product_name="X", quantity=3, selling_price="100.00", buying_price="60.00")
    assert sale.total_amount == Decimal("300.00")
    assert sale.profit == Decimal("120.00")


@pytest.mark.parametrize("path", [
    'api/health/',
    # Reached by the scheduler over the Docker network on plain HTTP. A
    # redirect to https sends it to a port serving none, and the job dies on a
    # read timeout -- which is exactly how this was found.
    'api/celery-token/',
    'api/send_sale2',
    'api/daily-ai/',
    'api/weekly-ai/',
])
def test_internal_paths_are_exempt_from_the_https_redirect(settings, path):
    import re
    exempt = getattr(settings, 'SECURE_REDIRECT_EXEMPT', [])
    assert any(re.match(pattern, path) for pattern in exempt), f'{path} would be redirected'


def test_health_is_exempt_from_the_https_redirect(settings):
    """The healthcheck is a plain-HTTP request to localhost.

    It never passes through the proxy, so it carries no X-Forwarded-Proto and
    SECURE_SSL_REDIRECT would answer it with a 301 to https on a port that
    serves none. The container would then be marked unhealthy and every deploy
    would fail, with the service actually working the whole time.
    """
    import re
    exempt = getattr(settings, 'SECURE_REDIRECT_EXEMPT', [])
    assert any(re.match(pattern, 'api/health/') for pattern in exempt)
