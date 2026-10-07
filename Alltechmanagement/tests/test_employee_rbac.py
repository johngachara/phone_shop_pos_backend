"""Employees add and sell; only managers change or remove an item.

Enforced at the endpoint, not only by hiding buttons in the POS: a hidden
button is no protection against the same request sent any other way. Each
refusal is also checked to have left the data untouched, since a 403 returned
after the write would pass a status-code assertion and still be the bug.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Accessory, Sale, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


def client_for(role):
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id=f"test-{role}", email=f"{role}@alltechnyeri.co.ke",
        role=role, is_alltech=True,
    ))
    return api


@pytest.fixture
def employee():
    return client_for(ROLE_EMPLOYEE)


@pytest.fixture
def manager():
    return client_for(ROLE_MANAGER)


def make_screen():
    return Stock.objects.create(
        product_name="Infinix Hot Screen", quantity=4,
        selling_price=Decimal("1500.00"), buying_price=Decimal("900.00"),
    )


def make_accessory():
    return Accessory.objects.create(
        product_name="Earphones", quantity=4,
        selling_price=Decimal("300.00"), buying_price=Decimal("150.00"),
    )


# async stock views use their own connection; see test_sale_flow.py.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("method", ["put", "patch"])
def test_employee_cannot_update_stock(employee, method):
    screen = make_screen()
    response = getattr(employee, method)(
        f"/api/update_stock2/{screen.pk}",
        {"product_name": "Renamed", "quantity": 99, "selling_price": "1.00", "buying_price": "1.00"},
        format="json",
    )
    assert response.status_code == 403
    screen.refresh_from_db()
    assert (screen.product_name, screen.quantity, screen.selling_price) == (
        "Infinix Hot Screen", 4, Decimal("1500.00"))


@pytest.mark.django_db(transaction=True)
def test_employee_cannot_delete_stock(employee):
    screen = make_screen()
    assert employee.delete(f"/api/delete_stock2_api/{screen.pk}").status_code == 403
    assert Stock.objects.filter(pk=screen.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_manager_can_update_and_delete_stock(manager):
    screen = make_screen()
    response = manager.patch(f"/api/update_stock2/{screen.pk}", {"quantity": 9}, format="json")
    assert response.status_code == 200, response.content
    screen.refresh_from_db()
    assert screen.quantity == 9
    assert manager.delete(f"/api/delete_stock2_api/{screen.pk}").status_code == 200
    assert not Stock.objects.filter(pk=screen.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_employee_can_still_add_and_sell_stock(employee):
    response = employee.post("/api/add_stock2", {
        "product_name": "Oppo A5 Screen", "quantity": 3,
        "selling_price": "1800.00", "buying_price": "1000.00",
    }, format="json")
    assert response.status_code in (200, 201), response.content
    screen = Stock.objects.get(product_name="Oppo A5 Screen")

    response = employee.post(f"/api/sell2/{screen.pk}", {
        "product_name": screen.product_name, "price": "1800.00", "quantity": 1,
        "customer_name": "Jane", "complete": True,
    }, format="json")
    assert response.status_code == 200, response.content
    screen.refresh_from_db()
    assert screen.quantity == 2


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["put", "patch"])
def test_employee_cannot_update_accessories(employee, method):
    item = make_accessory()
    response = getattr(employee, method)(
        f"/api/accessories/{item.pk}/update/",
        {"product_name": "Renamed", "quantity": 99, "selling_price": "1.00", "buying_price": "1.00"},
        format="json",
    )
    assert response.status_code == 403
    item.refresh_from_db()
    assert (item.product_name, item.quantity) == ("Earphones", 4)


@pytest.mark.django_db
def test_employee_cannot_delete_accessories(employee):
    item = make_accessory()
    assert employee.delete(f"/api/accessories/{item.pk}/delete/").status_code == 403
    assert Accessory.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_manager_can_update_and_delete_accessories(manager):
    item = make_accessory()
    response = manager.patch(f"/api/accessories/{item.pk}/update/", {"quantity": 7}, format="json")
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.quantity == 7
    assert manager.delete(f"/api/accessories/{item.pk}/delete/").status_code == 204


@pytest.mark.django_db
def test_employee_can_still_add_and_sell_accessories(employee):
    response = employee.post("/api/accessories/add/", {
        "product_name": "Car charger", "quantity": 3,
        "selling_price": "700.00", "buying_price": "400.00",
    }, format="json")
    assert response.status_code == 201, response.content
    item = Accessory.objects.get(product_name="Car charger")
    response = employee.post(f"/api/accessories/{item.pk}/sell/", {
        "product_name": item.product_name, "price": "700.00", "quantity": 1,
        "customer_name": "Jane", "complete": True,
    }, format="json")
    assert response.status_code in (200, 201), response.content
    assert Sale.objects.filter(accessory=item).exists()


# --- the assistant ------------------------------------------------------------

@pytest.mark.parametrize("name, args", [
    ("update_stock", {"id": 1, "selling_price": 1200}),
    ("update_stock", {"id": 1, "product_name": "Fixed typo"}),
    ("update_stock", {"id": 1, "quantity": 3}),
    ("update_stock_batch", {"items": [{"id": 1, "selling_price": 1200}]}),
    ("delete_stock", {"id": 1}),
    ("delete_stock_batch", {"ids": [1, 2]}),
])
def test_assistant_refuses_employee_changes_to_existing_items(name, args):
    from Alltechmanagement.ai.tools import check_role_restriction
    assert check_role_restriction(SupabaseUser(
        user_id="e", role=ROLE_EMPLOYEE, is_alltech=True), name, args)
    assert check_role_restriction(SupabaseUser(
        user_id="m", role=ROLE_MANAGER, is_alltech=True), name, args) is None


@pytest.mark.parametrize("name", ["add_stock", "add_stock_batch"])
def test_assistant_still_lets_employees_add(name):
    from Alltechmanagement.ai.tools import check_role_restriction
    assert check_role_restriction(SupabaseUser(
        user_id="e", role=ROLE_EMPLOYEE, is_alltech=True), name, {}) is None


@pytest.mark.django_db(transaction=True)
def test_deleting_a_missing_stock_item_is_a_404_not_a_success(manager):
    # Regression: the not-found case fell into a blanket except that returned
    # an error body with status 200.
    assert manager.delete("/api/delete_stock2_api/999999").status_code == 404
