"""In-house repairs.

A screen can be sold to a customer, or fitted by the shop as an in-house
repair. A repair is paid for as the screen plus a repair (labour) charge -- a
screen at 1200 fitted for 700 is a 1900 sale -- and every money figure has to
agree on that: the sale itself, the customer's total, the analytics, the AI
report data and the emailed sales report.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.template.loader import render_to_string as real_render
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from Alltechmanagement.models import Accessory, Customer, Sale, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


def principal(role):
    return SupabaseUser(
        user_id=f"test-{role}", email=f"{role}@alltechnyeri.co.ke",
        role=role, is_alltech=True,
    )


@pytest.fixture
def employee():
    api = APIClient()
    api.force_authenticate(user=principal(ROLE_EMPLOYEE))
    return api


@pytest.fixture
def manager():
    api = APIClient()
    api.force_authenticate(user=principal(ROLE_MANAGER))
    return api


@pytest.fixture
def screen(db):
    return Stock.objects.create(
        product_name="Tecno Spark Screen", quantity=5,
        selling_price=Decimal("1200.00"), buying_price=Decimal("800.00"),
    )


def repair(**kwargs):
    defaults = dict(
        product_name="Tecno Spark Screen", quantity=1,
        selling_price=Decimal("1200.00"), buying_price=Decimal("800.00"),
        repair_charge=Decimal("700.00"), sale_type=Sale.SaleType.REPAIR,
        customer_name="jane", status=Sale.Status.COMPLETED, completed_at=timezone.now(),
    )
    defaults.update(kwargs)
    return Sale.objects.create(**defaults)


def customer_sale(**kwargs):
    defaults = dict(
        product_name="Tecno Spark Screen", quantity=2,
        selling_price=Decimal("1200.00"), buying_price=Decimal("800.00"),
        customer_name="john", status=Sale.Status.COMPLETED, completed_at=timezone.now(),
    )
    defaults.update(kwargs)
    return Sale.objects.create(**defaults)


# --- the sale itself ---------------------------------------------------------

def test_repair_total_is_screen_plus_repair_charge():
    sale = Sale(selling_price="1200.00", quantity=1, repair_charge="700.00",
                buying_price="800.00", sale_type=Sale.SaleType.REPAIR)
    assert sale.total_amount == Decimal("1900.00")
    # Screen margin 400 plus the labour, which has no cost.
    assert sale.profit == Decimal("1100.00")


def test_customer_sale_is_unchanged_by_the_new_fields():
    sale = Sale(selling_price="1200.00", quantity=2, buying_price="800.00")
    assert sale.sale_type == Sale.SaleType.CUSTOMER
    assert sale.repair_charge == Decimal("0")
    assert sale.total_amount == Decimal("2400.00")
    assert sale.profit == Decimal("800.00")


@pytest.mark.django_db(transaction=True)
def test_selling_a_screen_as_a_repair_records_it(employee, screen):
    response = employee.post(f"/api/sell2/{screen.pk}", {
        "product_name": screen.product_name, "price": "1200.00", "quantity": 1,
        "customer_name": "Jane", "sale_type": "REPAIR", "repair_charge": "700.00",
        "complete": True,
    }, format="json")
    assert response.status_code == 200, response.content

    sale = Sale.objects.get(pk=response.json()["transaction_id"])
    assert sale.sale_type == Sale.SaleType.REPAIR
    assert sale.repair_charge == Decimal("700.00")
    assert sale.total_amount == Decimal("1900.00")
    # The customer's spend is what they paid, labour included.
    assert Customer.objects.get(name="jane").total_spent == Decimal("1900.00")
    screen.refresh_from_db()
    assert screen.quantity == 4


@pytest.mark.django_db(transaction=True)
def test_held_repair_books_the_full_amount_on_completion(employee, screen):
    response = employee.post(f"/api/sell2/{screen.pk}", {
        "product_name": screen.product_name, "price": "1200.00", "quantity": 1,
        "customer_name": "Jane", "sale_type": "REPAIR", "repair_charge": "700.00",
    }, format="json")
    sale_id = response.json()["transaction_id"]

    pending = employee.get("/api/saved2").json()["data"]
    row = next(r for r in pending if r["id"] == sale_id)
    assert row["sale_type"] == "REPAIR"
    assert Decimal(row["total_amount"]) == Decimal("1900.00")

    assert employee.post(f"/api/complete2/{sale_id}").status_code == 200
    assert Customer.objects.get(name="jane").total_spent == Decimal("1900.00")


@pytest.mark.django_db(transaction=True)
def test_refunding_a_held_repair_returns_the_screen(employee, screen):
    response = employee.post(f"/api/sell2/{screen.pk}", {
        "product_name": screen.product_name, "price": "1200.00", "quantity": 1,
        "customer_name": "Jane", "sale_type": "REPAIR", "repair_charge": "700.00",
    }, format="json")
    assert employee.post(f"/api/refund2/{response.json()['transaction_id']}").status_code == 200
    screen.refresh_from_db()
    assert screen.quantity == 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("payload, field", [
    # A repair needs a charge -- otherwise it is a customer sale mislabelled.
    ({"sale_type": "REPAIR"}, "repair_charge"),
    ({"sale_type": "REPAIR", "repair_charge": "0"}, "repair_charge"),
    ({"sale_type": "REPAIR", "repair_charge": "-5"}, "repair_charge"),
    # And a customer sale cannot carry one.
    ({"repair_charge": "700.00"}, "repair_charge"),
    ({"sale_type": "WARRANTY"}, "sale_type"),
])
def test_invalid_repair_payloads_are_refused_and_stock_untouched(employee, screen, payload, field):
    body = {"product_name": screen.product_name, "price": "1200.00", "quantity": 1,
            "customer_name": "Jane", **payload}
    response = employee.post(f"/api/sell2/{screen.pk}", body, format="json")
    assert response.status_code == 400
    assert field in response.json()
    screen.refresh_from_db()
    assert screen.quantity == 5
    assert not Sale.objects.exists()


@pytest.mark.django_db
def test_accessories_cannot_be_sold_as_a_repair(employee):
    item = Accessory.objects.create(
        product_name="Charger", quantity=3,
        selling_price=Decimal("500.00"), buying_price=Decimal("300.00"),
    )
    response = employee.post(f"/api/accessories/{item.pk}/sell/", {
        "product_name": "Charger", "price": "500.00", "quantity": 1,
        "customer_name": "Jane", "sale_type": "REPAIR", "repair_charge": "200.00",
    }, format="json")
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.quantity == 3


# --- analytics ---------------------------------------------------------------

@pytest.mark.django_db
def test_dashboard_counts_the_repair_charge_and_splits_by_sale_type(manager):
    repair()          # 1900 revenue, 1100 profit
    customer_sale()   # 2400 revenue, 800 profit

    data = manager.get("/api/dashboard/").json()
    today = data["today_metrics"]
    assert Decimal(str(today["total_sales"])) == Decimal("4300.00")
    assert Decimal(str(today["total_profit"])) == Decimal("1900.00")
    assert today["repair_count"] == 1
    assert Decimal(str(today["repair_revenue"])) == Decimal("700.00")

    split = data["by_sale_type"]
    assert Decimal(str(split["REPAIR"]["total_sales"])) == Decimal("1900.00")
    assert Decimal(str(split["REPAIR"]["repair_charges"])) == Decimal("700.00")
    assert Decimal(str(split["CUSTOMER"]["total_sales"])) == Decimal("2400.00")
    assert data["today_by_sale_type"]["REPAIR"]["sales_count"] == 1


@pytest.mark.django_db
def test_period_reports_include_repairs(manager):
    repair()
    customer_sale()
    month = manager.get("/api/monthly/").json()["current_year_data"][0]
    assert Decimal(str(month["total_sales"])) == Decimal("4300.00")
    assert month["repair_count"] == 1
    assert Decimal(str(month["repair_revenue"])) == Decimal("700.00")

    year = manager.get("/api/yearly/").json()["current_year_summary"]
    assert Decimal(str(year["total_sales"])) == Decimal("4300.00")
    assert year["repair_count"] == 1

    week = manager.get("/api/weekly/").json()["weekly_summary"][0]
    assert Decimal(str(week["total_sales"])) == Decimal("4300.00")


@pytest.mark.django_db
def test_customer_and_product_insights_include_the_repair_charge(manager):
    repair()
    customers = manager.get("/api/customers-insights/").json()
    jane = next(c for c in customers["current_year_top_customers"] if c["customer_name"] == "jane")
    assert Decimal(str(jane["total_spent"])) == Decimal("1900.00")

    products = manager.get("/api/products-insights/").json()["current_year_performance"][0]
    assert Decimal(str(products["total_revenue"])) == Decimal("1900.00")
    assert products["repair_count"] == 1


# --- AI report data ----------------------------------------------------------

@pytest.mark.django_db
def test_ai_report_figures_separate_repairs():
    from Alltechmanagement.ai import tools as ai_tools
    repair()
    customer_sale()
    summary = ai_tools.sales_summary(user=None, days=1)
    assert Decimal(summary["revenue"]) == Decimal("4300.00")
    assert Decimal(summary["profit"]) == Decimal("1900.00")
    assert summary["in_house_repairs"] == 1
    assert summary["customer_sales"] == 1
    assert Decimal(summary["repair_labour_revenue"]) == Decimal("700.00")

    top = ai_tools.top_customers(days=1)
    assert {c["customer_name"]: Decimal(c["total_spent"]) for c in top} == {
        "john": Decimal("2400.00"), "jane": Decimal("1900.00"),
    }


@pytest.mark.django_db
def test_ai_report_prompt_mentions_repairs():
    from Alltechmanagement import GPTAgent
    repair()
    with patch.object(GPTAgent, "chat") as chat:
        chat.return_value.content = "report"
        GPTAgent.run_conversation("summarise", days=1)
    prompt = chat.call_args[0][0][1]["content"]
    assert "in-house repairs: 1" in prompt
    assert "repair labour charged: 700.00" in prompt


# --- emailed sales report ----------------------------------------------------

@pytest.mark.django_db
def test_email_report_shows_repairs_and_the_true_total(monkeypatch):
    repair()
    customer_sale()
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("RESEND_SENDER_EMAIL", "pos@alltech.co.ke")
    monkeypatch.setenv("GMAIL_RECEIVER", "mgr@alltech.co.ke")

    from Alltechmanagement import views

    class Machine:
        is_authenticated = True
        id = None
        firebase_uid = "celery"

    rendered = {}

    def capture(template, context):
        rendered["context"] = context
        rendered["html"] = real_render(template, context)
        return rendered["html"]

    with patch.object(views, "render_to_string", side_effect=capture), \
            patch("resend.Emails.send", return_value={"id": "x"}):
        request = APIRequestFactory().get("/api/send_sale2")
        force_authenticate(request, user=Machine())
        response = views.send_sales2_api(request)

    assert response.status_code == 200, response.data
    ctx = rendered["context"]
    assert ctx["total"] == Decimal("4300.00")
    assert ctx["repair_total"] == Decimal("1900.00")
    assert ctx["customer_total"] == Decimal("2400.00")
    assert ctx["repair_count"] == 1
    assert ctx["repair_charges"] == Decimal("700.00")

    html = rendered["html"]
    assert ">Repair<" in html
    assert ">Sale<" in html
    assert "In-house repairs (1)" in html
    assert "1900.00" in html
    assert "Total: 4300.00" in html
