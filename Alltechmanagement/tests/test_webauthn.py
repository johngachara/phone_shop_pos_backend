"""Passkey registration and assertion.

The cryptographic verification is py_webauthn's job and is not re-tested here.
What is tested is everything around it, which is where the original Firestore
implementation was weak: challenge lifetime, single use, credential ownership,
and the signature counter it recorded but never checked.
"""
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from Alltechmanagement.models import WebAuthnCredential
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, SupabaseUser

USER_ID = "user-abc"
OTHER_USER_ID = "user-xyz"


@pytest.fixture(autouse=True)
def configured(settings):
    settings.WEBAUTHN_RP_ID = "alltechnyeri.co.ke"
    settings.WEBAUTHN_ORIGIN = "https://pos.alltechnyeri.co.ke"


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id=USER_ID, email="a@alltechnyeri.co.ke",
        role=ROLE_EMPLOYEE, is_alltech=True,
    ))
    return api


@pytest.fixture
def credential(db):
    return WebAuthnCredential.objects.create(
        user_id=USER_ID, credential_id="Y3JlZC1hYmM",
        public_key="cHVibGljLWtleQ", sign_count=5,
    )


@pytest.mark.django_db
def test_registration_options_are_issued_and_a_challenge_is_stored(client):
    response = client.post("/api/passkeys/register/options/")
    assert response.status_code == 200
    assert cache.get(f"webauthn_reg_challenge_{USER_ID}") is not None


@pytest.mark.django_db
def test_verification_without_a_challenge_is_refused(client):
    cache.delete(f"webauthn_reg_challenge_{USER_ID}")
    assert client.post("/api/passkeys/register/verify/", {}, format="json").status_code == 400


@pytest.mark.django_db
def test_a_challenge_is_consumed_even_when_verification_fails(client):
    client.post("/api/passkeys/register/options/")
    key = f"webauthn_reg_challenge_{USER_ID}"
    assert cache.get(key) is not None

    client.post("/api/passkeys/register/verify/", {"id": "nope"}, format="json")
    # One challenge, one attempt. Leaving it in place would let an attacker
    # keep trying against a challenge they had already observed.
    assert cache.get(key) is None


@pytest.mark.django_db
def test_auth_options_refused_when_no_passkey_is_registered(client):
    response = client.post("/api/passkeys/auth/options/")
    assert response.status_code == 400


@pytest.mark.django_db
def test_assertion_for_another_users_credential_is_refused(client, credential):
    WebAuthnCredential.objects.create(
        user_id=OTHER_USER_ID, credential_id="b3RoZXItY3JlZA",
        public_key="cHVibGljLWtleQ", sign_count=1,
    )
    cache.set(f"webauthn_auth_challenge_{USER_ID}", "Y2hhbGxlbmdl", 300)

    response = client.post("/api/passkeys/auth/verify/",
                           {"id": "b3RoZXItY3JlZA"}, format="json")
    assert response.status_code == 400
    assert "Unknown credential" in response.json()["error"]


@pytest.mark.django_db
def test_a_counter_that_does_not_advance_is_treated_as_a_clone(client, credential):
    cache.set(f"webauthn_auth_challenge_{USER_ID}", "Y2hhbGxlbmdl", 300)

    class Verified:
        new_sign_count = 5  # stored is also 5: no advance

    with patch("Alltechmanagement.webauthn_views.verify_authentication_response",
               return_value=Verified()):
        response = client.post("/api/passkeys/auth/verify/",
                               {"id": credential.credential_id}, format="json")

    # The original recorded this counter and never compared it, so a cloned
    # authenticator was indistinguishable from the real one.
    assert response.status_code == 400
    credential.refresh_from_db()
    assert credential.sign_count == 5


@pytest.mark.django_db
def test_a_successful_assertion_advances_the_counter(client, credential):
    cache.set(f"webauthn_auth_challenge_{USER_ID}", "Y2hhbGxlbmdl", 300)

    class Verified:
        new_sign_count = 9

    with patch("Alltechmanagement.webauthn_views.verify_authentication_response",
               return_value=Verified()):
        response = client.post("/api/passkeys/auth/verify/",
                               {"id": credential.credential_id}, format="json")

    assert response.status_code == 200
    credential.refresh_from_db()
    assert credential.sign_count == 9
    assert credential.last_used_at is not None


@pytest.mark.django_db
def test_a_zero_counter_is_accepted(client, credential):
    # Zero is the documented "counter not supported" value; many platform
    # authenticators never increment. Rejecting it would lock those users out.
    credential.sign_count = 0
    credential.save(update_fields=["sign_count"])
    cache.set(f"webauthn_auth_challenge_{USER_ID}", "Y2hhbGxlbmdl", 300)

    class Verified:
        new_sign_count = 0

    with patch("Alltechmanagement.webauthn_views.verify_authentication_response",
               return_value=Verified()):
        response = client.post("/api/passkeys/auth/verify/",
                               {"id": credential.credential_id}, format="json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_passkey_endpoints_require_authentication():
    assert APIClient().post("/api/passkeys/register/options/").status_code == 401


@pytest.mark.django_db
def test_endpoints_report_clearly_when_not_configured(client, settings):
    settings.WEBAUTHN_RP_ID = None
    response = client.post("/api/passkeys/register/options/")
    # 503 and a plain message: a misconfigured relying-party id otherwise
    # surfaces as an opaque verification failure much later.
    assert response.status_code == 503


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [
    {}, {"id": "x"}, {"rawId": None}, {"id": "x", "response": "not-an-object"},
])
def test_malformed_payloads_are_client_errors_not_server_errors(client, credential, payload):
    # py_webauthn raises structural errors (InvalidJSONStructure, InvalidCBORData)
    # as well as cryptographic ones. Catching only the latter turned a malformed
    # request into a 500.
    cache.set(f"webauthn_auth_challenge_{USER_ID}", "Y2hhbGxlbmdl", 300)
    response = client.post("/api/passkeys/auth/verify/", payload, format="json")
    assert response.status_code == 400, response.content


@pytest.mark.django_db
def test_options_are_returned_as_an_object_not_a_json_string(client):
    """The response body must be a JSON object, not a string containing one.

    py_webauthn's options_to_json returns a serialised string. Handing that to
    DRF's Response encodes it a second time, so the browser receives
    "{\\"rp\\": ...}" and every field reads as undefined --
    navigator.credentials.create then fails with "cannot read properties of
    undefined", which is exactly what a passkey enrolment reported.
    """
    body = client.post('/api/passkeys/register/options/').json()
    assert isinstance(body, dict), f'expected an object, got {type(body).__name__}'
    for field in ('rp', 'user', 'challenge', 'pubKeyCredParams'):
        assert field in body, f'{field} missing from the options'
    assert isinstance(body['user'], dict)
    assert isinstance(body['challenge'], str)


@pytest.mark.django_db
def test_authentication_options_are_also_an_object(client, credential):
    body = client.post('/api/passkeys/auth/options/').json()
    assert isinstance(body, dict)
    assert 'challenge' in body
    assert isinstance(body.get('allowCredentials'), list)
