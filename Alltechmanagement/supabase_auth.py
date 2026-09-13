"""Supabase authentication and role enforcement.

Identity comes from Supabase. This module verifies the access token the client
presents, decides whether its owner belongs to Alltech at all, and exposes the
role that the permission classes in permissions.py act on.

Where the claims live
---------------------
Under `app_metadata.alltech`, not at the top level of `app_metadata`:

    "app_metadata": {"alltech": {"is_alltech": true, "role": "manager"}}

The Supabase project is shared with another application, whose users already
carry a top-level `app_metadata.role` holding values like "it_manager" and
"l2_technician". Writing Alltech's role into that same key would overwrite
theirs and break their authorization. Namespacing keeps the two apart.

`app_metadata` specifically, never `user_metadata`: only the service role can
write app_metadata, whereas a signed-in user can edit their own user_metadata
and would be able to promote themselves to manager.

Why the gate lives here rather than in Django middleware
-------------------------------------------------------
Django middleware runs before DRF resolves authentication, so it has no verified
identity to test -- it would have to parse and verify the token itself,
duplicating this code. Rejecting during authentication still happens before any
view body executes, which is the property that matters.
"""
import hashlib
import logging

import jwt
import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import authentication, exceptions

logger = logging.getLogger('django')

ROLE_EMPLOYEE = 'employee'
ROLE_MANAGER = 'manager'
VALID_ROLES = (ROLE_EMPLOYEE, ROLE_MANAGER)

# Verification results are cached briefly when Supabase is asked to validate the
# token for us. Short enough that a revoked session stops working quickly,
# long enough to keep a burst of POS requests off the network.
REMOTE_VERIFY_CACHE_SECONDS = 60


class SupabaseUser:
    """The authenticated principal.

    Not a Django model. There is no local user table -- Supabase owns identity,
    and duplicating it here would create two sources of truth for who someone is.
    """

    def __init__(self, user_id, email=None, role=None, is_alltech=False, claims=None):
        self.id = user_id
        self.email = email
        self.role = role
        self.is_alltech = is_alltech
        self.claims = claims or {}

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return True

    @property
    def is_manager(self):
        return self.role == ROLE_MANAGER

    @property
    def is_employee(self):
        return self.role == ROLE_EMPLOYEE

    def get_username(self):
        return self.email or self.id

    def __str__(self):
        return f"SupabaseUser({self.email or self.id}, role={self.role})"


def _alltech_claims(app_metadata):
    section = (app_metadata or {}).get('alltech') or {}
    if not isinstance(section, dict):
        return {}
    return section


def _build_user(user_id, email, app_metadata, claims):
    section = _alltech_claims(app_metadata)
    role = section.get('role')
    if role not in VALID_ROLES:
        role = None
    return SupabaseUser(
        user_id=user_id,
        email=email,
        role=role,
        is_alltech=bool(section.get('is_alltech')),
        claims=claims,
    )


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(f'{self.keyword} '):
            return None

        token = header.split(' ', 1)[1].strip()
        if not token:
            raise exceptions.AuthenticationFailed('Empty bearer token')

        user = self._verify(token)

        if not user.is_alltech:
            # Every other user on this Supabase project belongs to a different
            # application. Without this check they could all sign in here.
            logger.warning("Rejected non-Alltech principal %s", user.id)
            raise exceptions.PermissionDenied('Not authorized for this application')

        if user.role is None:
            logger.warning("Alltech principal %s has no valid role", user.id)
            raise exceptions.PermissionDenied('No role assigned')

        return user, token

    def _verify(self, token):
        secret = getattr(settings, 'SUPABASE_JWT_SECRET', None)
        if secret:
            return self._verify_locally(token, secret)
        return self._verify_remotely(token)

    def _verify_locally(self, token, secret):
        """Verify the HS256 signature without a network call. Preferred."""
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=['HS256'],
                audience='authenticated',
                options={'require': ['exp', 'sub']},
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Token expired')
        except jwt.InvalidTokenError as exc:
            logger.warning("Rejected token: %s", exc)
            raise exceptions.AuthenticationFailed('Invalid token')

        return _build_user(
            user_id=claims.get('sub'),
            email=claims.get('email'),
            app_metadata=claims.get('app_metadata'),
            claims=claims,
        )

    def _verify_remotely(self, token):
        """Ask Supabase to validate the token.

        Used when SUPABASE_JWT_SECRET is not configured. Correct but slower, so
        the result is cached against a hash of the token -- never the token
        itself, which would put a live credential in Redis.
        """
        cache_key = 'supabase_auth_' + hashlib.sha256(token.encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return _build_user(**cached)

        base_url = getattr(settings, 'SUPABASE_URL', None)
        api_key = getattr(settings, 'SUPABASE_KEY', None)
        if not base_url or not api_key:
            raise exceptions.AuthenticationFailed('Authentication is not configured')

        try:
            response = requests.get(
                f'{base_url}/auth/v1/user',
                headers={'apikey': api_key, 'Authorization': f'Bearer {token}'},
                timeout=5,
            )
        except requests.RequestException as exc:
            logger.error("Supabase verification unreachable: %s", exc)
            # 503, not 401: the caller's credential may be perfectly valid.
            raise exceptions.APIException('Authentication service unavailable')

        if response.status_code != 200:
            raise exceptions.AuthenticationFailed('Invalid token')

        payload = response.json()
        data = {
            'user_id': payload.get('id'),
            'email': payload.get('email'),
            'app_metadata': payload.get('app_metadata'),
            'claims': {},
        }
        cache.set(cache_key, data, REMOTE_VERIFY_CACHE_SECONDS)
        return _build_user(**data)

    def authenticate_header(self, request):
        return self.keyword
