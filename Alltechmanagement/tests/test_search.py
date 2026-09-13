"""Fuzzy product search.

The point of trigram matching over ILIKE is tolerance for how people type at a
counter under pressure. These tests are mostly about misspellings, because a
plain substring search passes every "correct spelling" test and still fails the
user.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Accessory, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, SupabaseUser


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id='till', email='t@alltechnyeri.co.ke',
        role=ROLE_EMPLOYEE, is_alltech=True,
    ))
    return api


@pytest.fixture
def catalogue(db):
    for name in ('iPhone 12 Screen', 'Samsung A54 Screen',
                 'Redmi Note 12 Screen', 'Tecno Spark 10 Screen'):
        Stock.objects.create(
            product_name=name, quantity=5, selling_price=Decimal('1000.00'))
    Accessory.objects.create(
        product_name='USB-C Cable 2m', quantity=10, selling_price=Decimal('450.00'))


def names(response):
    body = response.json()
    rows = body.get('results') or body.get('data') or body.get('items') or []
    return [r['product_name'] for r in rows]


@pytest.mark.django_db
def test_exact_substring_matches(client, catalogue):
    assert 'iPhone 12 Screen' in names(client.get('/api/get_shop2_stock?q=iphone'))


@pytest.mark.django_db
@pytest.mark.parametrize('typo,expected', [
    ('ifone', 'iPhone 12 Screen'),
    ('samsng', 'Samsung A54 Screen'),
    ('reddmi', 'Redmi Note 12 Screen'),
])
def test_misspellings_still_find_the_item(client, catalogue, typo, expected):
    # This is the whole reason for trigram matching. ILIKE returns nothing for
    # every one of these.
    assert expected in names(client.get(f'/api/get_shop2_stock?q={typo}'))


@pytest.mark.django_db
def test_an_exact_match_ranks_first(client, catalogue):
    # A similarity score alone does not guarantee it: if someone types the
    # product name, that item has to be the first row.
    assert names(client.get('/api/get_shop2_stock?q=Samsung A54 Screen'))[0] == 'Samsung A54 Screen'


@pytest.mark.django_db
def test_nonsense_returns_nothing_rather_than_everything(client, catalogue):
    # A threshold set too low turns every query into the whole catalogue,
    # which is worse than no search at all.
    assert names(client.get('/api/get_shop2_stock?q=zzzqqqxxyy')) == []


@pytest.mark.django_db
def test_no_query_returns_the_whole_list(client, catalogue):
    assert len(names(client.get('/api/get_shop2_stock'))) == 4


@pytest.mark.django_db
def test_accessories_search_is_not_limited_to_the_loaded_page(client, db):
    # The bug this replaces: the page filtered client-side, so an item on
    # page 2 looked like it was not stocked.
    for i in range(60):
        Accessory.objects.create(
            product_name=f'Filler item {i}', quantity=1, selling_price=Decimal('10.00'))
    Accessory.objects.create(
        product_name='Wireless Charger', quantity=1, selling_price=Decimal('2500.00'))

    body = client.get('/api/accessories/?q=charger&page=1&limit=50').json()
    assert [i['product_name'] for i in body['items']] == ['Wireless Charger']


@pytest.mark.django_db
def test_accessories_tolerate_a_misspelling(client, catalogue):
    body = client.get('/api/accessories/?q=usbc').json()
    assert any('USB-C' in i['product_name'] for i in body['items'])
