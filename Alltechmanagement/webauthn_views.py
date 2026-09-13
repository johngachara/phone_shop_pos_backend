"""Passkeys: the second step of sign-in.

Supabase checks the password; this checks the device. Ported from the
`sequelizer` service, which held credentials in a Firestore array on the user
document and identified the user from a Firebase ID token.

Two changes from the original, both deliberate:

- The challenge was stored on the user document with no expiry, so it stayed
  valid until something replaced it. A challenge is a one-time value and an
  unbounded one widens the replay window indefinitely. Challenges now live in
  the cache with a short TTL and are deleted as soon as they are used.

- The signature counter was written but never checked. It exists so that a
  cloned authenticator can be detected: a counter that fails to advance means
  the same credential is in use in two places. It is verified now.

The caller is already authenticated by Supabase when these run, so the user is
taken from request.user and never from the request body -- otherwise anyone
could enrol a passkey against anyone else's account.

py_webauthn's options_to_json returns a serialised string, not a dict. Passing
it straight to DRF's Response encodes it a second time and the browser gets a
JSON string where it expects an object, so every field reads as undefined. The
options are decoded back before being returned.
"""
import json
import logging

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidCBORData,
    InvalidJSONStructure,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from django.conf import settings

from Alltechmanagement.models import WebAuthnCredential
from Alltechmanagement.permissions import IsEmployeeOrManager
from Alltechmanagement.throttles import POSAuthThrottle

logger = logging.getLogger('django')

RP_NAME = 'Alltech'
# Five minutes: long enough for a user to reach for a fingerprint reader,
# short enough that an intercepted challenge is quickly useless.
CHALLENGE_TTL_SECONDS = 300

# Everything a browser can send that means "this assertion is not acceptable".
# The structural errors matter as much as the cryptographic ones: the payload is
# client-controlled, and a malformed one that escapes this tuple becomes a 500,
# which is both a worse answer and a louder signal to whoever sent it.
WEBAUTHN_REJECTIONS = (
    InvalidRegistrationResponse,
    InvalidAuthenticationResponse,
    InvalidJSONStructure,
    InvalidCBORData,
    ValueError,
    KeyError,
    TypeError,
)


def _rp_id():
    return getattr(settings, 'WEBAUTHN_RP_ID', None)


def _expected_origin():
    return getattr(settings, 'WEBAUTHN_ORIGIN', None)


def _challenge_key(kind, user_id):
    return f'webauthn_{kind}_challenge_{user_id}'


def _not_configured():
    return Response(
        {'error': 'Passkeys are not configured on this server.'},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([POSAuthThrottle])
def registration_options(request):
    if not _rp_id():
        return _not_configured()

    user = request.user
    existing = WebAuthnCredential.objects.filter(user_id=user.id)

    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name=RP_NAME,
        user_id=str(user.id).encode(),
        user_name=user.email or str(user.id),
        # Stops the same authenticator being enrolled twice, which would leave
        # the user with two credentials and no way to tell them apart.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    cache.set(
        _challenge_key('reg', user.id),
        bytes_to_base64url(options.challenge),
        CHALLENGE_TTL_SECONDS,
    )
    # json.loads, because options_to_json returns a string and DRF would
    # encode it again -- see the note at the top of this module.
    return Response(json.loads(options_to_json(options)))


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([POSAuthThrottle])
def verify_registration(request):
    if not _rp_id():
        return _not_configured()

    user = request.user
    key = _challenge_key('reg', user.id)
    stored = cache.get(key)
    if not stored:
        return Response(
            {'error': 'No registration in progress, or it expired.'}, status=400
        )

    try:
        verification = verify_registration_response(
            credential=request.data,
            expected_challenge=base64url_to_bytes(stored),
            expected_origin=_expected_origin(),
            expected_rp_id=_rp_id(),
            require_user_verification=False,
        )
    except WEBAUTHN_REJECTIONS as exc:
        logger.warning("Passkey registration rejected for %s: %s", user.id, exc)
        return Response({'error': 'Registration could not be verified.'}, status=400)
    finally:
        # One challenge, one attempt, success or failure.
        cache.delete(key)

    credential_id = bytes_to_base64url(verification.credential_id)
    WebAuthnCredential.objects.update_or_create(
        credential_id=credential_id,
        defaults={
            'user_id': user.id,
            'public_key': bytes_to_base64url(verification.credential_public_key),
            'sign_count': verification.sign_count,
            'device_type': getattr(verification.credential_device_type, 'value', ''),
            'backed_up': bool(verification.credential_backed_up),
            'label': request.data.get('label', '') if isinstance(request.data, dict) else '',
        },
    )

    logger.info("Passkey registered for %s", user.id)
    return Response({'success': True, 'message': 'Passkey registered successfully'})


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([POSAuthThrottle])
def authentication_options(request):
    if not _rp_id():
        return _not_configured()

    user = request.user
    credentials = list(WebAuthnCredential.objects.filter(user_id=user.id))
    if not credentials:
        return Response({'error': 'No passkeys registered for this user.'}, status=400)

    options = generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in credentials
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    cache.set(
        _challenge_key('auth', user.id),
        bytes_to_base64url(options.challenge),
        CHALLENGE_TTL_SECONDS,
    )
    # json.loads, because options_to_json returns a string and DRF would
    # encode it again -- see the note at the top of this module.
    return Response(json.loads(options_to_json(options)))


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([POSAuthThrottle])
def verify_authentication(request):
    if not _rp_id():
        return _not_configured()

    user = request.user
    key = _challenge_key('auth', user.id)
    stored = cache.get(key)
    if not stored:
        return Response(
            {'error': 'No authentication in progress, or it expired.'}, status=400
        )

    raw_id = request.data.get('id') if isinstance(request.data, dict) else None
    if not raw_id:
        cache.delete(key)
        return Response({'error': 'Malformed assertion.'}, status=400)

    try:
        # Scoped to this user: a credential belonging to someone else must not
        # satisfy this user's challenge.
        credential = WebAuthnCredential.objects.get(
            credential_id=raw_id, user_id=user.id
        )
    except WebAuthnCredential.DoesNotExist:
        cache.delete(key)
        return Response({'error': 'Unknown credential.'}, status=400)

    try:
        verification = verify_authentication_response(
            credential=request.data,
            expected_challenge=base64url_to_bytes(stored),
            expected_origin=_expected_origin(),
            expected_rp_id=_rp_id(),
            credential_public_key=base64url_to_bytes(credential.public_key),
            credential_current_sign_count=credential.sign_count,
            require_user_verification=False,
        )
    except WEBAUTHN_REJECTIONS as exc:
        logger.warning("Passkey assertion rejected for %s: %s", user.id, exc)
        return Response({'error': 'Authentication could not be verified.'}, status=400)
    finally:
        cache.delete(key)

    # A counter that does not advance means this credential exists in more than
    # one place. Zero is the documented "not supported" value and is exempt.
    if verification.new_sign_count and verification.new_sign_count <= credential.sign_count:
        logger.error(
            "Passkey counter did not advance for %s (stored %s, presented %s); "
            "possible cloned authenticator",
            user.id, credential.sign_count, verification.new_sign_count,
        )
        return Response({'error': 'Authentication could not be verified.'}, status=400)

    credential.sign_count = verification.new_sign_count
    credential.last_used_at = timezone.now()
    credential.save(update_fields=['sign_count', 'last_used_at'])

    return Response({'success': True})


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([POSAuthThrottle])
def list_credentials(request):
    credentials = WebAuthnCredential.objects.filter(user_id=request.user.id)
    return Response({'credentials': [
        {
            'id': c.credential_id,
            'label': c.label,
            'device_type': c.device_type,
            'backed_up': c.backed_up,
            'created_at': c.created_at,
            'last_used_at': c.last_used_at,
        }
        for c in credentials
    ]})
