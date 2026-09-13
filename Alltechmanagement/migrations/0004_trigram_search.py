from django.conf import settings
from django.db import migrations


def _extensions_schema():
    return getattr(settings, 'DB_EXTENSIONS_SCHEMA', None) or 'public'


def create_indexes(apps, schema_editor):
    """GIN trigram indexes on the two searchable product names.

    Without these, every fuzzy search is a sequential scan computing a
    similarity score per row. That is survivable at a few hundred items and
    is not the shape you want a counter search to have as the catalogue grows.

    The operator class is schema-qualified: pg_trgm lives in `public` on
    Supabase while these tables live in their own schema, and an unqualified
    `gin_trgm_ops` fails to resolve.
    """
    schema = _extensions_schema()
    with schema_editor.connection.cursor() as cursor:
        # Idempotent, and a no-op on Supabase where it is already installed.
        cursor.execute(f'CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA {schema}')
        for table in ('stock', 'accessories'):
            cursor.execute(
                f'CREATE INDEX IF NOT EXISTS {table}_product_name_trgm '
                f'ON {table} USING gin (product_name {schema}.gin_trgm_ops)'
            )


def drop_indexes(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in ('stock', 'accessories'):
            cursor.execute(f'DROP INDEX IF EXISTS {table}_product_name_trgm')
    # The extension is deliberately not dropped: it is shared with whatever
    # else uses this database, and removing it would break them.


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0003_accessory_webauthncredential_sale_item_type_and_more'),
    ]

    operations = [
        migrations.RunPython(create_indexes, drop_indexes),
    ]
