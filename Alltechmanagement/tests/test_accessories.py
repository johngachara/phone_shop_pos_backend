"""Accessories, ported from the Firestore-backed sequelizer service."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Accessory, Customer, Sale
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


def principal(role=ROLE_EMPLOYEE):
    return SupabaseUser(user_id="till-1", email="till@alltechnyeri.co.ke",
                        role=role, is_alltech=True)


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=principal())
    return api


@pytest.fixture
def accessory(db):
    return Accessory.objects.create(
        product_name="USB-C Cable", quantity=10,
        selling_price=Decimal("500.00"), buying_price=Decimal("300.00"),
    )


@pytest.mark.django_db
def test_list_is_paginated(client, accessory):
    for i in range(5):
        Accessory.objects.create(product_name=f"Item {i}", quantity=1,
                                 selling_price=Decimal("100.00"),
                                 buying_price=Decimal("60.00"))
    body = client.get("/api/accessories/?page=1&limit=2").json()
    assert body["totalItems"] == 6
    assert body["totalPages"] == 3
    assert len(body["items"]) == 2


@pytest.mark.django_db
def test_add_accepts_the_price_alias(client):
    # The current POS sends `price`; the column is `selling_price`.
    response = client.post("/api/accessories/add/", {
        "product_name": "Screen Protector", "quantity": 5, "price": "250.00",
        "buying_price": "90.00",
    }, format="json")
    assert response.status_code == 201, response.content
    assert Accessory.objects.get(product_name="Screen Protector").selling_price == Decimal("250.00")


@pytest.mark.django_db
def test_duplicate_name_is_rejected_case_insensitively(client, accessory):
    response = client.post("/api/accessories/add/", {
        "product_name": "usb-c cable", "quantity": 1, "price": "500.00",
        "buying_price": "200.00",
    }, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_sell_reduces_stock_and_records_a_completed_sale(client, accessory):
    # Explicit now that accessories can also be held. The default matches
    # screens -- holding -- so a paid sale has to say so.
    response = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 2, "customer_name": "Jane", "complete": True,
    }, format="json")
    assert response.status_code == 200, response.content

    accessory.refresh_from_db()
    assert accessory.quantity == 8

    sale = Sale.objects.get(pk=response.json()["sale_id"])
    assert sale.status == Sale.Status.COMPLETED
    assert sale.item_type == Sale.ItemType.ACCESSORY
    # Captured at sale time, exactly as screen sales do.
    assert sale.buying_price == Decimal("300.00")
    assert sale.profit == Decimal("400.00")
    assert Customer.objects.get(name="jane").total_spent == Decimal("1000.00")


@pytest.mark.django_db
def test_sell_refuses_to_oversell(client, accessory):
    response = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 99, "customer_name": "Jane",
    }, format="json")
    assert response.status_code == 400
    accessory.refresh_from_db()
    assert accessory.quantity == 10


@pytest.mark.django_db
def test_accessory_sales_appear_in_reporting_alongside_screen_sales(client, accessory):
    from Alltechmanagement.admin_apis import completed_sales
    client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 1, "customer_name": "Jane", "complete": True,
    }, format="json")
    Sale.objects.create(product_name="Screen", quantity=1,
                        selling_price=Decimal("5000.00"), customer_name="bob",
                        status=Sale.Status.COMPLETED)
    # The original wrote accessory sales into their own Firestore collections,
    # so they never reached the reports at all.
    assert completed_sales().count() == 2


@pytest.mark.django_db
def test_deleting_an_accessory_keeps_its_sales(client, accessory):
    client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 1, "customer_name": "Jane", "complete": True,
    }, format="json")
    assert client.delete(f"/api/accessories/{accessory.pk}/delete/").status_code == 204
    sale = Sale.objects.get(item_type=Sale.ItemType.ACCESSORY)
    assert sale.product_name == "USB-C Cable"
    assert sale.accessory is None


@pytest.mark.django_db
def test_accessories_require_an_alltech_role():
    anon = APIClient()
    assert anon.get("/api/accessories/").status_code == 401
    outsider = APIClient()
    outsider.force_authenticate(
        user=SupabaseUser(user_id="x", role=None, is_alltech=False)
    )
    assert outsider.get("/api/accessories/").status_code == 403


@pytest.mark.django_db
def test_manager_can_also_use_accessories(accessory):
    api = APIClient()
    api.force_authenticate(user=principal(ROLE_MANAGER))
    assert api.get("/api/accessories/").status_code == 200


@pytest.mark.django_db
def test_the_first_page_is_cached_and_writes_clear_it(client, accessory):
    from django.core.cache import cache
    cache.clear()

    assert client.get('/api/accessories/').json()['totalItems'] == 1

    # A write must make the cached page stale immediately. Before this, the
    # four write paths deleted a key nothing had ever set.
    client.post('/api/accessories/add/', {
        'product_name': 'Car Charger', 'quantity': 3, 'price': '800.00',
        'buying_price': '350.00',
    }, format='json')

    assert client.get('/api/accessories/').json()['totalItems'] == 2


@pytest.mark.django_db
def test_a_search_is_not_served_from_the_unfiltered_cache(client, accessory):
    from django.core.cache import cache
    cache.clear()

    # Warm the cache with the unfiltered page first.
    assert client.get('/api/accessories/').json()['totalItems'] == 1

    # A search must not be answered with the cached full list.
    body = client.get('/api/accessories/?q=zzzznothing').json()
    assert body['items'] == []


# --- holding an accessory ----------------------------------------------------

@pytest.mark.django_db
def test_an_accessory_can_be_held(client, accessory):
    """Accessories were complete-only.

    An accessory handed over on credit had nowhere to live: it was either
    recorded as paid when it was not, or not recorded at all.
    """
    response = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 2, "customer_name": "Njeri",
    }, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == Sale.Status.PENDING

    # Stock leaves the shelf immediately either way -- the item is in the
    # customer's hand.
    accessory.refresh_from_db()
    assert accessory.quantity == 8

    # But it is not money yet.
    assert not Customer.objects.filter(name="njeri").exists()


@pytest.mark.django_db
def test_a_held_accessory_appears_in_unpaid_orders(client, accessory):
    client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 1, "customer_name": "Njeri",
    }, format="json")
    rows = client.get("/api/saved2").json()
    rows = rows.get("results") or rows.get("data") or []
    assert any(r["product_name"] == accessory.product_name for r in rows)


@pytest.mark.django_db
def test_completing_a_held_accessory_books_the_money(client, accessory):
    sale_id = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 2, "customer_name": "Njeri",
    }, format="json").json()["sale_id"]

    assert client.post(f"/api/complete2/{sale_id}").status_code == 200
    assert Customer.objects.get(name="njeri").total_spent == Decimal("1000.00")


@pytest.mark.django_db
def test_selling_an_accessory_outright_still_works(client, accessory):
    response = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 1, "customer_name": "Njeri", "complete": True,
    }, format="json")
    assert response.json()["status"] == Sale.Status.COMPLETED
    assert Customer.objects.get(name="njeri").total_spent == Decimal("500.00")


@pytest.mark.django_db(transaction=True)
def test_refunding_a_held_accessory_returns_it_to_accessories(client, accessory):
    """The trap in this feature.

    Refund looked the item up in Stock by name. An accessory is not in Stock,
    so the refund would have been refused and the units left deducted from a
    shelf they were never returned to.
    """
    sale_id = client.post(f"/api/accessories/{accessory.pk}/sell/", {
        "product_name": accessory.product_name, "price": "500.00",
        "quantity": 3, "customer_name": "Njeri",
    }, format="json").json()["sale_id"]

    accessory.refresh_from_db()
    assert accessory.quantity == 7

    assert client.post(f"/api/refund2/{sale_id}").status_code == 200

    accessory.refresh_from_db()
    assert accessory.quantity == 10
    assert not Sale.objects.filter(pk=sale_id).exists()


@pytest.mark.django_db(transaction=True)
def test_refunding_a_screen_still_returns_it_to_stock(client, db):
    from Alltechmanagement.models import Stock
    stock = Stock.objects.create(
        product_name="Held Screen", quantity=5,
        selling_price=Decimal("4000.00"), buying_price=Decimal("2500.00"),
    )
    sale_id = client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name, "price": "4000.00",
        "quantity": 2, "customer_name": "Njeri",
    }, format="json").json()["transaction_id"]

    assert client.post(f"/api/refund2/{sale_id}").status_code == 200
    stock.refresh_from_db()
    assert stock.quantity == 5
