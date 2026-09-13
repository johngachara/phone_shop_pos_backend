"""Reporting over the new data structure.

Two things changed underneath these endpoints: accessory sales now land in the
same table as screen sales, and sales carry the cost they were sold at.
"""
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from Alltechmanagement.models import Sale
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


@pytest.fixture
def manager():
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id="mgr", email="m@alltechnyeri.co.ke",
        role=ROLE_MANAGER, is_alltech=True))
    return api


def make_sale(**kwargs):
    defaults = dict(
        product_name="Screen", quantity=1, selling_price=Decimal("1000.00"),
        customer_name="jane", status=Sale.Status.COMPLETED,
        completed_at=timezone.now(),
    )
    defaults.update(kwargs)
    return Sale.objects.create(**defaults)


@pytest.mark.django_db
def test_profit_uses_only_sales_that_recorded_a_cost(manager):
    make_sale(selling_price=Decimal("1000.00"), buying_price=Decimal("600.00"))
    make_sale(selling_price=Decimal("2000.00"))  # cost unknown

    body = manager.get("/api/dashboard/").json()
    totals = body["today_metrics"]

    assert totals["total_sales"] == Decimal("3000.00")
    # Only the sale with a known cost contributes.
    assert Decimal(totals["total_profit"]) == Decimal("400.00")
    # ...and the caller can see it came from 1 of 2 sales, rather than being
    # handed a profit figure silently derived from half the data.
    assert totals["sales_with_cost"] == 1
    assert totals["sales_count"] == 2


@pytest.mark.django_db
def test_profit_is_zero_not_an_error_when_no_cost_is_known(manager):
    make_sale(selling_price=Decimal("1000.00"))
    totals = manager.get("/api/dashboard/").json()["today_metrics"]
    assert totals["total_profit"] == 0
    assert totals["sales_with_cost"] == 0


@pytest.mark.django_db
def test_accessory_sales_are_reported_and_split_out(manager):
    make_sale(item_type=Sale.ItemType.SCREEN,
              selling_price=Decimal("5000.00"), buying_price=Decimal("3000.00"))
    make_sale(item_type=Sale.ItemType.ACCESSORY, product_name="Cable",
              selling_price=Decimal("500.00"), buying_price=Decimal("300.00"))

    body = manager.get("/api/dashboard/").json()
    split = body["by_item_type"]

    assert split["SCREEN"]["total_sales"] == Decimal("5000.00")
    assert split["ACCESSORY"]["total_sales"] == Decimal("500.00")
    assert Decimal(split["ACCESSORY"]["total_profit"]) == Decimal("200.00")
    # Accessory revenue reached no reporting endpoint at all before this.
    assert body["today_metrics"]["total_sales"] == Decimal("5500.00")


@pytest.mark.django_db
def test_pending_sales_are_excluded_from_reporting(manager):
    make_sale(status=Sale.Status.COMPLETED, selling_price=Decimal("1000.00"))
    make_sale(status=Sale.Status.PENDING, selling_price=Decimal("9999.00"),
              completed_at=None)
    # A held order is not revenue.
    assert manager.get("/api/dashboard/").json()["today_metrics"]["sales_count"] == 1


@pytest.mark.django_db
def test_reporting_stays_manager_only():
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id="e", role=ROLE_EMPLOYEE, is_alltech=True))
    assert api.get("/api/dashboard/").status_code == 403
