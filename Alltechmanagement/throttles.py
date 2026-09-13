import logging
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle, SimpleRateThrottle

from djangoProject15 import settings

logger = logging.getLogger('django.security')


def principal_ident(request):
    """Stable per-caller identifier, or None for an unidentifiable caller.

    `id` is the Supabase user id. `firebase_uid` is still read so the machine
    token path -- which has no Supabase identity -- keeps a bucket of its own
    rather than sharing the anonymous one with the public internet.
    """
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return None
    return getattr(user, 'id', None) or getattr(user, 'firebase_uid', None)


class PrincipalRateThrottle(UserRateThrottle):
    """Per-principal rate limiting with an anonymous fallback.

    Both of the previous base classes keyed on a provider-specific attribute
    (`user.firebase_uid`, `user.data.id`). A Supabase principal has neither, so
    after the identity change every authenticated caller would have dropped into
    the anonymous bucket -- 2 requests an hour -- and the dashboard throttle
    would have raised AttributeError outright on `request.user.data`.
    """

    def allow_request(self, request, view):
        if principal_ident(request) is None:
            try:
                anon_rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['anon']
                self.rate = anon_rate
                self.num_requests, self.duration = self.parse_rate(self.rate)
            except (KeyError, TypeError):
                self.rate = '2/hour'  # Default fallback
                self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        ident = principal_ident(request)
        if ident is None:
            logger.warning('Unidentifiable principal; throttling by IP')
            return self.cache_format % {
                'scope': f"anon_{self.scope}",
                'ident': self.get_ident(request),
            }
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident,
        }


# Names kept so the scope classes below read unchanged.
FirebaseUserRateThrottle = PrincipalRateThrottle
ClerkUserRateThrottle = PrincipalRateThrottle
BaseFirebaseThrottle = PrincipalRateThrottle
BaseClerkThrottle = PrincipalRateThrottle


class POSAuthThrottle(AnonRateThrottle):
    """
    Strict throttling for login attempts and auth-related endpoints
    5 attempts per minute - prevents brute force while allowing retries
    """
    rate = '5/minute'
    scope = 'pos_auth'


class InventoryModificationThrottle(BaseFirebaseThrottle):
    """
    For stock updates, adding/removing items
    30 per minute = 1 operation every 2 seconds
    """
    rate = '30/minute'
    scope = 'inventory_mod'


class SalesOperationsThrottle(BaseFirebaseThrottle):
    """
    For active selling operations
    120 per minute = 2 operations per second
    """
    rate = '120/minute'
    scope = 'sales_ops'


class OrderManagementThrottle(BaseFirebaseThrottle):
    """
    For order status changes and refunds
    60 per minute = 1 operation per second
    """
    rate = '60/minute'
    scope = 'order_mgmt'


class InventoryCheckThrottle(BaseFirebaseThrottle):
    """
    For checking stock levels and prices
    300 per minute = 5 queries per second
    """
    rate = '300/minute'
    scope = 'inventory_check'


class DashBoardThrottle(BaseClerkThrottle):
    """
    For admin dashboard apis
    20 queries per second
    """
    rate = '20/second'
    scope = 'dashboard'

class CeleryAuthTokenThrottle(SimpleRateThrottle):
    """
    Throttle for the Celery authentication token endpoint.
    Uses IP-based rate limiting regardless of authentication.
    """
    rate = '7/day'
    scope = 'celery_auth_token'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return f'throttle_celery_auth_{ident}'

    def get_ident(self, request):
        """Get client identifier using X-Forwarded-For if behind proxy"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class WeeklyEmailAPIThrottle(SimpleRateThrottle):
    """
    Throttle for weekly sales email API.
    Uses IP-based rate limiting with a weekly quota.
    """
    scope = 'weekly_email'
    rate = '5/day'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return f'throttle_sales_email_{self.scope}_{ident}'

    def get_ident(self, request):
        """Get client identifier using X-Forwarded-For if behind proxy"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
