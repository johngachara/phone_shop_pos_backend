"""End-to-end sale flow.

The endpoint sweep proves these routes reject unauthenticated callers. It cannot
prove the sale logic itself, which is where the redesign changed behaviour: a
sale is now one row that changes status, rather than a row copied between three
tables and deleted.
"""
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from Alltechmanagement.models import Customer, Sale, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, SupabaseUser


def make_principal(role=ROLE_EMPLOYEE):
    """An authenticated Alltech principal.

    A real SupabaseUser rather than a stand-in, so these tests go through the
    same permission classes as production. A bare object with
    is_authenticated=True would sail past role checks that a real caller has to
    satisfy, and the tests would pass against an endpoint that is wide open.
    """
    return SupabaseUser(
        user_id="test-principal",
        email="till@alltechnyeri.co.ke",
        role=role,
        is_alltech=True,
    )


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=make_principal())
    return api


@pytest.fixture
def stock(db):  # noqa: PT004 -- `db` is replaced by `transactional_db` where needed
    return Stock.objects.create(
        product_name="iPhone 12 Screen",
        quantity=10,
        selling_price=Decimal("5000.00"),
        buying_price=Decimal("3000.00"),
    )


# sell_api and refund2_api are async views: they reach the ORM through
# sync_to_async, which runs on a separate database connection in a worker
# thread. That connection cannot see rows a test created inside pytest-django's
# wrapping transaction, so those two tests need real committed data.
@pytest.mark.django_db(transaction=True)
def test_sell_holds_stock_and_captures_buying_price(client, stock):
    response = client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name,
        "price": "5000.00",
        "quantity": 2,
        "customer_name": "Jane",
    }, format="json")
    assert response.status_code == 200, response.content

    sale = Sale.objects.get(pk=response.json()["transaction_id"])
    assert sale.status == Sale.Status.PENDING
    # Captured from the item at sale time, not looked up later.
    assert sale.buying_price == Decimal("3000.00")
    assert sale.profit == Decimal("4000.00")

    stock.refresh_from_db()
    assert stock.quantity == 8


@pytest.mark.django_db
def test_complete_flips_status_without_copying_rows(client, stock):
    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=2,
        selling_price=Decimal("5000.00"), buying_price=Decimal("3000.00"),
        customer_name="Jane", stock=stock,
    )

    response = client.post(f"/api/complete2/{sale.pk}")
    assert response.status_code == 200, response.content

    sale.refresh_from_db()
    assert sale.status == Sale.Status.COMPLETED
    assert sale.completed_at is not None
    # Not yet reported: that is what the sales email consumes.
    assert sale.reported_at is None
    # The row survives. It used to be deleted and duplicated into two others.
    assert Sale.objects.count() == 1

    customer = Customer.objects.get(name="jane")
    assert customer.total_spent == Decimal("10000.00")


@pytest.mark.django_db
def test_completing_twice_does_not_double_count_the_customer(client, stock):
    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=1,
        selling_price=Decimal("5000.00"), customer_name="Jane", stock=stock,
    )
    assert client.post(f"/api/complete2/{sale.pk}").status_code == 200
    # Second attempt finds no PENDING row and must not add to total_spent again.
    assert client.post(f"/api/complete2/{sale.pk}").status_code == 404
    assert Customer.objects.get(name="jane").total_spent == Decimal("5000.00")


@pytest.mark.django_db(transaction=True)
def test_refund_returns_every_unit_not_just_one(client, stock):
    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=3,
        selling_price=Decimal("5000.00"), customer_name="Jane", stock=stock,
    )
    Stock.objects.filter(pk=stock.pk).update(quantity=7)

    response = client.post(f"/api/refund2/{sale.pk}")
    assert response.status_code == 200, response.content

    stock.refresh_from_db()
    # Restored exactly 1 before this fix, so a 3-unit refund lost 2 units.
    assert stock.quantity == 10
    assert not Sale.objects.filter(pk=sale.pk).exists()


@pytest.mark.django_db
def test_second_customer_purchase_accumulates(client, stock):
    for _ in range(2):
        sale = Sale.objects.create(
            product_name=stock.product_name, quantity=1,
            selling_price=Decimal("5000.00"), customer_name="Jane", stock=stock,
        )
        assert client.post(f"/api/complete2/{sale.pk}").status_code == 200
    assert Customer.objects.get(name="jane").total_spent == Decimal("10000.00")


@pytest.mark.django_db
def test_pending_sales_are_not_treated_as_revenue():
    Sale.objects.create(
        product_name="A", quantity=1, selling_price=Decimal("100.00"),
        customer_name="x", status=Sale.Status.PENDING,
    )
    Sale.objects.create(
        product_name="B", quantity=1, selling_price=Decimal("200.00"),
        customer_name="x", status=Sale.Status.COMPLETED,
        completed_at=timezone.now(),
    )
    from Alltechmanagement.admin_apis import completed_sales
    assert completed_sales().count() == 1
    assert completed_sales().first().product_name == "B"


@pytest.mark.django_db
def test_employee_can_run_the_till_end_to_end(stock):
    """The whole point of the employee role: run a sale without manager rights."""
    api = APIClient()
    api.force_authenticate(user=make_principal(ROLE_EMPLOYEE))

    sale = Sale.objects.create(
        product_name=stock.product_name, quantity=1,
        selling_price=Decimal("5000.00"), buying_price=stock.buying_price,
        customer_name="Jane", stock=stock,
    )
    assert api.post(f"/api/complete2/{sale.pk}").status_code == 200
    # ...but analytics stay closed.
    assert api.get("/api/dashboard/").status_code == 403


# --- selling directly, without holding -----------------------------------

@pytest.mark.django_db(transaction=True)
def test_a_direct_sale_is_money_immediately(client, stock):
    """Paid at the counter, so it never passes through Orders.

    The held-order flow exists because an item is often handed over before it
    is paid for. When it is paid there and then, routing it through a
    completion step nobody will perform would leave revenue permanently
    pending.
    """
    response = client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name,
        "price": "5000.00",
        "quantity": 2,
        "customer_name": "Mwangi",
        "complete": True,
    }, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == Sale.Status.COMPLETED

    sale = Sale.objects.get(pk=response.json()["transaction_id"])
    assert sale.status == Sale.Status.COMPLETED
    assert sale.completed_at is not None
    # Not yet reported: the daily email still has to pick it up.
    assert sale.reported_at is None

    stock.refresh_from_db()
    assert stock.quantity == 8

    # The customer's total moves now, not at some later completion.
    assert Customer.objects.get(name="mwangi").total_spent == Decimal("10000.00")


@pytest.mark.django_db(transaction=True)
def test_holding_is_still_the_default(client, stock):
    response = client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name, "price": "5000.00",
        "quantity": 1, "customer_name": "Mwangi",
    }, format="json")
    assert response.json()["status"] == Sale.Status.PENDING
    # Nothing is owed to the customer record until the order is completed.
    assert not Customer.objects.filter(name="mwangi").exists()


@pytest.mark.django_db(transaction=True)
def test_a_direct_sale_does_not_appear_in_unpaid_orders(client, stock):
    client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name, "price": "5000.00",
        "quantity": 1, "customer_name": "Mwangi", "complete": True,
    }, format="json")
    assert Sale.objects.filter(status=Sale.Status.PENDING).count() == 0


@pytest.mark.django_db(transaction=True)
def test_a_direct_sale_cannot_oversell(client, stock):
    response = client.post(f"/api/sell2/{stock.pk}", {
        "product_name": stock.product_name, "price": "5000.00",
        "quantity": 999, "customer_name": "Mwangi", "complete": True,
    }, format="json")
    assert response.status_code == 400
    stock.refresh_from_db()
    assert stock.quantity == 10
    assert Sale.objects.count() == 0
