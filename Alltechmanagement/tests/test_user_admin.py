"""Manager-driven user administration.

There is no self-service password reset on this project: staff use addresses on
the shop's domain and have no mailbox. Everything about an account therefore
has to be doable by a manager from the Users page, which puts real weight on
these endpoints refusing the wrong caller.
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import WebAuthnCredential
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser

TARGET = 'user-target'


def client_as(role, user_id='caller'):
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id=user_id, email='a@alltechnyeri.co.ke', role=role, is_alltech=True,
    ))
    return api


def alltech_user(user_id=TARGET):
    return {
        'id': user_id,
        'email': 'staff@alltechnyeri.co.ke',
        'app_metadata': {'alltech': {'is_alltech': True, 'role': 'employee'}},
    }


@pytest.mark.django_db
def test_manager_can_set_a_password():
    with patch('Alltechmanagement.user_admin._admin_request') as admin:
        admin.side_effect = [alltech_user(), {}]
        response = client_as(ROLE_MANAGER).post(
            f'/api/users/{TARGET}/password/', {'password': 'a-long-enough-pass'}, format='json',
        )
    assert response.status_code == 200
    assert response.json()['updated'] is True


@pytest.mark.django_db
def test_short_passwords_are_refused():
    response = client_as(ROLE_MANAGER).post(
        f'/api/users/{TARGET}/password/', {'password': 'short'}, format='json',
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_an_employee_cannot_set_anyone_password():
    # Otherwise any employee could take over the manager's account.
    response = client_as(ROLE_EMPLOYEE).post(
        f'/api/users/{TARGET}/password/', {'password': 'a-long-enough-pass'}, format='json',
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_user_of_another_application_cannot_be_touched():
    outsider = {'id': 'x', 'email': 'them@other.co', 'app_metadata': {'role': 'it_manager'}}
    with patch('Alltechmanagement.user_admin._admin_request', return_value=outsider):
        response = client_as(ROLE_MANAGER).post(
            '/api/users/x/password/', {'password': 'a-long-enough-pass'}, format='json',
        )
    # Their account belongs to another system on the same Supabase project.
    assert response.status_code == 404


@pytest.mark.django_db
def test_manager_can_clear_passkeys():
    WebAuthnCredential.objects.create(
        user_id=TARGET, credential_id='c1', public_key='k', sign_count=0)
    WebAuthnCredential.objects.create(
        user_id=TARGET, credential_id='c2', public_key='k', sign_count=0)
    WebAuthnCredential.objects.create(
        user_id='someone-else', credential_id='c3', public_key='k', sign_count=0)

    with patch('Alltechmanagement.user_admin._admin_request', return_value=alltech_user()):
        response = client_as(ROLE_MANAGER).delete(f'/api/users/{TARGET}/passkeys/')

    assert response.status_code == 200
    assert response.json()['removed'] == 2
    # Only the named user's registrations. A lost phone must not sign everyone
    # else out of their second step.
    assert WebAuthnCredential.objects.filter(user_id='someone-else').count() == 1


@pytest.mark.django_db
def test_an_employee_cannot_clear_passkeys():
    response = client_as(ROLE_EMPLOYEE).delete(f'/api/users/{TARGET}/passkeys/')
    assert response.status_code == 403


@pytest.mark.django_db
def test_listing_reports_how_many_passkeys_each_user_has():
    WebAuthnCredential.objects.create(
        user_id=TARGET, credential_id='c1', public_key='k', sign_count=0)
    with patch('Alltechmanagement.user_admin._admin_request',
               return_value={'users': [alltech_user()]}):
        body = client_as(ROLE_MANAGER).get('/api/users/').json()
    # So a manager can tell whether clearing will lock someone out or let them
    # back in, before doing it.
    assert body['users'][0]['passkey_count'] == 1
