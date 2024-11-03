import logging
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle, SimpleRateThrottle

from djangoProject15 import settings

logger = logging.getLogger('django.security')


class FirebaseUserRateThrottle(UserRateThrottle):
    """
    Custom UserRateThrottle that uses firebase_uid as the unique identifier for rate limiting.
    Includes additional logging for security monitoring.
    """

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated and hasattr(request.user, 'firebase_uid'):
            ident = request.user.firebase_uid
            logger.info(f'Rate limiting for Firebase user: {ident}')
            return self.cache_format % {
                'scope': self.scope,
                'ident': ident
            }
        logger.warning('Unauthenticated request or missing firebase_uid')
        return super().get_cache_key(request, view)


class BaseFirebaseThrottle(FirebaseUserRateThrottle):
    """
    Base throttle class for Firebase authentication with anonymous fallback
    """

    def allow_request(self, request, view):
        if not request.user.is_authenticated or not hasattr(request.user, 'firebase_uid'):
            try:
                anon_rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['anon']
                self.rate = anon_rate
                self.num_requests, self.duration = self.parse_rate(self.rate)
            except (KeyError, TypeError):
                self.rate = '2/minute'  # Default fallback
                self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if not request.user.is_authenticated or not hasattr(request.user, 'firebase_uid'):
            ident = self.get_ident(request)
            logger.warning('Unauthenticated request or missing firebase_uid')
            return self.cache_format % {
                'scope': f"anon_{self.scope}",
                'ident': ident
            }

        ident = request.user.firebase_uid
        logger.info(f'Rate limiting for Firebase user: {ident}')
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


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


class CeleryAuthTokenThrottle(SimpleRateThrottle):
    """
    Throttle for the Celery authentication token endpoint.
    Uses IP-based rate limiting regardless of authentication.
    """
    rate = '5/day'
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
