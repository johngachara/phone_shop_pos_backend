"""Dependency health probe.

Deliberately kept in its own module rather than views.py: views.py imports the
Meilisearch client, the AI agent and the whole model layer, and a health check
that cannot be imported when one of those is misconfigured is worthless exactly
when it is needed.

Unauthenticated, because the container healthcheck and CI have no credentials.
It therefore reports only reachable/unreachable per dependency -- no versions,
hostnames, credentials or error detail, which would otherwise hand an unauthed
caller a map of the infrastructure.
"""
import logging
import os

import meilisearch
from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger('django')


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_cache():
    cache.set("healthcheck", "ok", timeout=5)
    if cache.get("healthcheck") != "ok":
        raise RuntimeError("cache round-trip failed")


def _check_meilisearch():
    client = meilisearch.Client(os.getenv('MEILISEARCH_URL'), os.getenv('MEILISEARCH_KEY'))
    if not client.is_healthy():
        raise RuntimeError("meilisearch reported unhealthy")


CHECKS = {
    "database": _check_database,
    "cache": _check_cache,
    "search": _check_meilisearch,
}


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    results = {}
    for name, check in CHECKS.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:
            # Full detail to the log, bare status to the caller.
            logger.error("Health check %s failed: %s", name, exc)
            results[name] = "unavailable"

    healthy = all(value == "ok" for value in results.values())
    return Response(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
