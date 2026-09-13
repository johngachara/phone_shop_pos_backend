"""Manager-only user administration.

Creating a Supabase user requires the service role key, which bypasses row-level
security and can rewrite anyone's app_metadata -- including their role. It
therefore never leaves the server: the browser calls these endpoints, and only
this process holds the key.

Roles and the Alltech flag are written under `app_metadata.alltech`, never at the
top level and never in `user_metadata`. See supabase_auth.py for why.
"""
import logging

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from Alltechmanagement.permissions import IsManager
from Alltechmanagement.supabase_auth import VALID_ROLES
from Alltechmanagement.throttles import InventoryModificationThrottle

logger = logging.getLogger('django')

REQUEST_TIMEOUT = 10


class SupabaseAdminError(Exception):
    pass


def _admin_request(method, path, payload=None, params=None):
    base_url = getattr(settings, 'SUPABASE_URL', None)
    service_key = getattr(settings, 'SUPABASE_KEY', None)
    if not base_url or not service_key:
        raise SupabaseAdminError('Supabase admin credentials are not configured')

    try:
        response = requests.request(
            method,
            f'{base_url}/auth/v1/admin/{path}',
            headers={
                'apikey': service_key,
                'Authorization': f'Bearer {service_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Supabase admin request failed: %s", exc)
        raise SupabaseAdminError('Supabase is unreachable')

    if response.status_code >= 400:
        # Logged in full, returned as a generic message: Supabase error bodies
        # can disclose whether an address is already registered.
        logger.error("Supabase admin %s %s -> %s %s",
                     method, path, response.status_code, response.text[:500])
        raise SupabaseAdminError('Supabase rejected the request')

    return response.json() if response.content else {}


def _alltech_section(user):
    section = (user.get('app_metadata') or {}).get('alltech') or {}
    return section if isinstance(section, dict) else {}


def _present(user):
    """Shape a Supabase user for the POS.

    Only fields the POS needs. The raw record carries other applications'
    metadata -- `active_org_id`, their own `role` -- which is none of this
    application's business and must not be handed to its frontend.
    """
    section = _alltech_section(user)
    return {
        'id': user.get('id'),
        'email': user.get('email'),
        'role': section.get('role'),
        'is_alltech': bool(section.get('is_alltech')),
        'created_at': user.get('created_at'),
        'last_sign_in_at': user.get('last_sign_in_at'),
    }


class UserAdminListView(APIView):
    permission_classes = [IsManager]
    throttle_classes = [InventoryModificationThrottle]

    def get(self, request):
        try:
            payload = _admin_request('GET', 'users', params={'per_page': 200})
        except SupabaseAdminError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        users = payload.get('users', payload if isinstance(payload, list) else [])
        # Other applications share this Supabase project, and their users are
        # not this application's to list.
        alltech_users = [u for u in users if _alltech_section(u).get('is_alltech')]
        return Response({'users': [_present(u) for u in alltech_users]})

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        password = request.data.get('password') or ''
        role = request.data.get('role')

        if not email or '@' not in email:
            return Response({'error': 'A valid email is required.'}, status=400)
        if len(password) < 12:
            # Longer than Supabase's default minimum: these accounts have
            # standing access to stock and sales.
            return Response(
                {'error': 'Password must be at least 12 characters.'}, status=400
            )
        if role not in VALID_ROLES:
            return Response(
                {'error': f'Role must be one of: {", ".join(VALID_ROLES)}.'}, status=400
            )

        try:
            created = _admin_request('POST', 'users', payload={
                'email': email,
                'password': password,
                'email_confirm': True,
                'app_metadata': {'alltech': {'is_alltech': True, 'role': role}},
            })
        except SupabaseAdminError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        logger.info("Manager %s created Alltech user %s with role %s",
                    request.user.id, email, role)
        return Response(_present(created), status=status.HTTP_201_CREATED)


class UserAdminDetailView(APIView):
    permission_classes = [IsManager]
    throttle_classes = [InventoryModificationThrottle]

    def _load_alltech_user(self, user_id):
        user = _admin_request('GET', f'users/{user_id}')
        if not _alltech_section(user).get('is_alltech'):
            return None
        return user

    def patch(self, request, user_id):
        role = request.data.get('role')
        if role not in VALID_ROLES:
            return Response(
                {'error': f'Role must be one of: {", ".join(VALID_ROLES)}.'}, status=400
            )

        if user_id == request.user.id and role != 'manager':
            # Removing your own manager role can leave no manager at all, and
            # the person who did it can no longer reach this endpoint to undo it.
            return Response(
                {'error': 'You cannot remove your own manager role.'}, status=400
            )

        try:
            user = self._load_alltech_user(user_id)
            if user is None:
                return Response({'error': 'User not found.'}, status=404)

            # Merge rather than replace: app_metadata holds another
            # application's keys on this shared project, and a wholesale
            # overwrite would delete them.
            app_metadata = dict(user.get('app_metadata') or {})
            section = dict(_alltech_section(user))
            section['role'] = role
            section['is_alltech'] = True
            app_metadata['alltech'] = section

            updated = _admin_request('PUT', f'users/{user_id}',
                                     payload={'app_metadata': app_metadata})
        except SupabaseAdminError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        logger.info("Manager %s set role of %s to %s", request.user.id, user_id, role)
        return Response(_present(updated))

    def delete(self, request, user_id):
        """Revoke access without deleting the Supabase account.

        The account may belong to another application on this shared project, so
        deleting it outright is not this application's decision. Clearing the
        Alltech flag removes access here and leaves everything else intact.
        """
        if user_id == request.user.id:
            return Response(
                {'error': 'You cannot revoke your own access.'}, status=400
            )

        try:
            user = self._load_alltech_user(user_id)
            if user is None:
                return Response({'error': 'User not found.'}, status=404)

            app_metadata = dict(user.get('app_metadata') or {})
            app_metadata['alltech'] = {'is_alltech': False, 'role': None}
            _admin_request('PUT', f'users/{user_id}',
                           payload={'app_metadata': app_metadata})
        except SupabaseAdminError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        logger.info("Manager %s revoked Alltech access for %s", request.user.id, user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
