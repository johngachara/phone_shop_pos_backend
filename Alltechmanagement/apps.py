from django.apps import AppConfig


class AlltechmanagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Alltechmanagement'

    def ready(self):
        # Pins connections to DB_SCHEMA when one is configured. Registered here
        # so it is in place before the first query, including migrations.
        from Alltechmanagement import db_schema
        db_schema.register()
