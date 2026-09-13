"""Pin every database connection to the configured Postgres schema.

Why this is not just a connection option
----------------------------------------
The obvious way to do this is libpq's `options=-c search_path=...`, set through
DATABASES['default']['OPTIONS']. That works against a plain Postgres, and it is
still set in settings.py for exactly that case.

It does not work through Supabase's pooler. Supavisor accepts the connection and
*silently discards* the parameter -- `SHOW search_path` comes back as the default
`"$user", public, extensions` with no error and no warning. Relying on it alone
would have created this project's tables in the `public` schema of a Supabase
instance shared with another application, while every log line suggested they
were safely isolated.

Issuing `SET search_path` on the connection does take effect, so that is what
this does, and it then verifies the result rather than trusting it. A wrong
schema raises at connection time instead of quietly writing to someone else's
tables.

Prefer the session-mode port (5432) when DB_SCHEMA is set. Verified: the libpq
option is discarded on both Supabase ports, and the runtime SET does take effect
on both. What is *not* guaranteed under transaction pooling (6543) is that the
setting stays attached -- the pooler reassigns server connections between
transactions, so a session-level SET can outlive its owner or be lost. The check
below runs once per Django connection and cannot catch a later reassignment, so
it is a guard against misconfiguration, not against pooler behaviour.
"""
import logging

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created

logger = logging.getLogger('django')


def _apply_search_path(sender, connection, **kwargs):
    if connection.vendor != 'postgresql':
        return

    from django.conf import settings
    schema = getattr(settings, 'DB_SCHEMA', None)
    if not schema:
        return

    with connection.cursor() as cursor:
        # Quoted to keep an identifier with unusual characters from being
        # interpreted as a list of schemas.
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute('SELECT current_schema()')
        actual = cursor.fetchone()[0]

    if actual != schema:
        raise ImproperlyConfigured(
            f"DB_SCHEMA is {schema!r} but the connection resolved to {actual!r}. "
            "Refusing to continue: writes would land in the wrong schema. If this "
            "is Supabase, check that DB_PORT is the session-mode port (5432) and "
            "not the transaction pooler (6543), and that the schema exists."
        )


def register():
    connection_created.connect(_apply_search_path, dispatch_uid='alltech_search_path')
    logger.debug("search_path enforcement registered")
