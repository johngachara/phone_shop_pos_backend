"""Low stock reporting, merged across both inventories.

Screens and accessories are separate tables, but a low accessory is just as
much a restock problem as a low screen. Before this, the endpoint (and the
dashboard/low-stock page built on it) only ever looked at Stock, so a shop
with plenty of low accessories reported a smaller problem than it actually had.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Accessory, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id="till-1", email="till@alltechnyeri.co.ke",
        role=ROLE_EMPLOYEE, is_alltech=True))
    return api


@pytest.mark.django_db
def test_low_stock_merges_screens_and_accessories(client):
    Stock.objects.create(product_name="iPhone 12 Screen", quantity=2,
                         selling_price=Decimal("5000.00"), buying_price=Decimal("3000.00"))
    Stock.objects.create(product_name="Well stocked screen", quantity=50,
                         selling_price=Decimal("5000.00"), buying_price=Decimal("3000.00"))
    Accessory.objects.create(product_name="USB-C Cable", quantity=1,
                             selling_price=Decimal("500.00"), buying_price=Decimal("300.00"))
    Accessory.objects.create(product_name="Well stocked cable", quantity=50,
                             selling_price=Decimal("500.00"), buying_price=Decimal("300.00"))

    body = client.get("/api/detailed/low_stock/?threshold=3").json()
    names = {item["product_name"]: item["item_type"] for item in body["results"]}

    assert names == {
        "iPhone 12 Screen": "SCREEN",
        "USB-C Cable": "ACCESSORY",
    }


@pytest.mark.django_db
def test_low_stock_is_sorted_by_quantity_across_both_inventories(client):
    Stock.objects.create(product_name="Screen A", quantity=2,
                         selling_price=Decimal("100.00"), buying_price=Decimal("60.00"))
    Accessory.objects.create(product_name="Accessory A", quantity=1,
                             selling_price=Decimal("100.00"), buying_price=Decimal("60.00"))
    Stock.objects.create(product_name="Screen B", quantity=0,
                         selling_price=Decimal("100.00"), buying_price=Decimal("60.00"))

    body = client.get("/api/detailed/low_stock/?threshold=3").json()
    quantities = [item["quantity"] for item in body["results"]]
    assert quantities == sorted(quantities)


@pytest.mark.django_db
def test_low_stock_respects_the_threshold(client):
    Accessory.objects.create(product_name="Plenty", quantity=10,
                             selling_price=Decimal("100.00"), buying_price=Decimal("60.00"))
    body = client.get("/api/detailed/low_stock/?threshold=3").json()
    assert body["count"] == 0
