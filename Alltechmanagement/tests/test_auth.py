"""Supabase authentication and role enforcement.

Tokens are minted locally with a throwaway HS256 secret so these run without a
network call. That exercises the same code path production uses whenever
SUPABASE_JWT_SECRET is configured.

The negative cases carry the weight here. A positive test on an unprotected
endpoint proves nothing: before this change every authenticated caller could
reach every endpoint, and each test below would still have passed.
"""
import time

import jwt
import pytest
from rest_framework.test import APIClient

TEST_SECRET = 'test-jwt-secret-not-used-anywhere-real'


def make_token(sub='user-1', email='a@b.co', app_metadata=None, expires_in=3600,
               secret=TEST_SECRET):
    payload = {
        'sub': sub,
        'email': email,
        'aud': 'authenticated',
        'exp': int(time.time()) + expires_in,
        'iat': int(time.time()),
        'app_metadata': app_metadata if app_metadata is not None else {},
    }
    return jwt.encode(payload, secret, algorithm='HS256')


def alltech(role):
    return {'alltech': {'is_alltech': True, 'role': role}}


@pytest.fixture(autouse=True)
def local_verification(settings):
    settings.SUPABASE_JWT_SECRET = TEST_SECRET


@pytest.fixture
def client():
    return APIClient()


def auth(client, token):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


# --- the is_alltech gate -----------------------------------------------------

@pytest.mark.django_db
def test_user_from_another_application_is_rejected(client):
    # Every other user on this shared Supabase project looks like this: valid
    # token, real account, no Alltech claim. Without the gate they could all
    # sign in here.
    token = make_token(app_metadata={'role': 'it_manager', 'active_org_id': 'x'})
    response = auth(client, token).get('/api/get_shop2_stock')
    assert response.status_code == 403


@pytest.mark.django_db
def test_alltech_flag_without_a_role_is_rejected(client):
    token = make_token(app_metadata={'alltech': {'is_alltech': True}})
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 403


@pytest.mark.django_db
def test_unrecognised_role_is_rejected(client):
    token = make_token(app_metadata={'alltech': {'is_alltech': True, 'role': 'owner'}})
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 403


@pytest.mark.django_db
def test_top_level_role_cannot_grant_access(client):
    # The other application writes app_metadata.role. It must never be read as
    # an Alltech role, or its managers become managers here.
    token = make_token(app_metadata={'role': 'manager', 'is_alltech': True})
    assert auth(client, token).get('/api/dashboard/').status_code == 403


# --- token validity ----------------------------------------------------------

@pytest.mark.django_db
def test_expired_token_is_rejected(client):
    token = make_token(app_metadata=alltech('manager'), expires_in=-10)
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 401


@pytest.mark.django_db
def test_token_signed_with_the_wrong_secret_is_rejected(client):
    token = make_token(app_metadata=alltech('manager'), secret='not-the-secret')
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 401


@pytest.mark.django_db
def test_unsigned_token_is_rejected(client):
    # alg=none: the classic forgery. PyJWT must not accept it, since the
    # payload would otherwise be entirely attacker-controlled.
    payload = {'sub': 'x', 'aud': 'authenticated',
               'exp': int(time.time()) + 600, 'app_metadata': alltech('manager')}
    token = jwt.encode(payload, key='', algorithm='none')
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 401


@pytest.mark.django_db
def test_missing_token_is_unauthorised_not_forbidden(client):
    # 401 tells a client to authenticate; 403 tells it not to bother retrying.
    assert client.get('/api/get_shop2_stock').status_code == 401


# --- roles -------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('path', [
    '/api/dashboard/', '/api/weekly/', '/api/monthly/', '/api/yearly/',
    '/api/customers-insights/', '/api/products-insights/', '/api/patterns/',
    '/api/users/',
])
def test_employee_cannot_reach_manager_endpoints(client, path):
    token = make_token(app_metadata=alltech('employee'))
    assert auth(client, token).get(path).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize('path', [
    '/api/get_shop2_stock', '/api/saved2', '/api/customers/',
    '/api/detailed/low_stock/',
])
def test_employee_can_reach_till_endpoints(client, path):
    token = make_token(app_metadata=alltech('employee'))
    assert auth(client, token).get(path).status_code == 200


@pytest.mark.django_db
def test_manager_can_reach_manager_endpoints(client):
    token = make_token(app_metadata=alltech('manager'))
    assert auth(client, token).get('/api/dashboard/').status_code == 200


# --- machine endpoints -------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('path', ['/api/send_sale2', '/api/daily-ai/', '/api/weekly-ai/'])
def test_a_user_token_cannot_reach_machine_endpoints(client, path):
    # These send email and spend AI quota. A manager signing in should not be
    # able to trigger them by hand.
    token = make_token(app_metadata=alltech('manager'))
    assert auth(client, token).get(path).status_code in (401, 403)


# --- health ------------------------------------------------------------------

@pytest.mark.django_db
def test_health_stays_public(client):
    # The container healthcheck has no credentials.
    assert client.get('/api/health/').status_code in (200, 503)


# --- asymmetric signing (after a project migrates off the legacy secret) -----

@pytest.mark.django_db
def test_asymmetric_token_is_verified_against_jwks(client, settings, monkeypatch):
    """A project migrated to JWT signing keys must keep working.

    Supabase can be switched from the legacy shared HS256 secret to asymmetric
    keys at any time. Nothing secret is distributed in that mode -- the
    verifying key is public -- so the algorithm in the token header, not
    configuration, has to decide how a token is checked.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa
    from Alltechmanagement import supabase_auth

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    payload = {
        'sub': 'user-rs', 'email': 'rs@b.co', 'aud': 'authenticated',
        'exp': int(time.time()) + 600, 'app_metadata': alltech('manager'),
    }
    token = jwt.encode(payload, private_key, algorithm='RS256',
                       headers={'kid': 'test-kid'})

    class FakeKey:
        key = private_key.public_key()

    class FakeJWKSClient:
        def __init__(self, *a, **kw):
            pass

        def get_signing_key_from_jwt(self, _token):
            return FakeKey()

    monkeypatch.setattr(supabase_auth, '_jwks_client', FakeJWKSClient())
    settings.SUPABASE_URL = 'https://example.supabase.co'
    # The legacy secret is present and must be ignored for an RS256 token.
    settings.SUPABASE_JWT_SECRET = TEST_SECRET

    assert auth(client, token).get('/api/dashboard/').status_code == 200


@pytest.mark.django_db
def test_unknown_algorithm_is_rejected(client):
    payload = {'sub': 'x', 'aud': 'authenticated',
               'exp': int(time.time()) + 600, 'app_metadata': alltech('manager')}
    token = jwt.encode(payload, TEST_SECRET, algorithm='HS512')
    assert auth(client, token).get('/api/get_shop2_stock').status_code == 401
