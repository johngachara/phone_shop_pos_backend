import logging
import os

import firebase_admin
from firebase_admin import credentials, messaging, db
from dotenv import load_dotenv

from djangoProject15 import settings

load_dotenv()

logger = logging.getLogger('django')

# Firebase used to be initialized at import time, which meant the whole Django
# process refused to start whenever key_pair.json was absent -- true in every
# container and every CI run, since the file is gitignored. Initialization is now
# lazy and failure is non-fatal: push notifications degrade, the app still boots.
_initialized = False


def _ensure_app():
    global _initialized
    if _initialized:
        return True
    if firebase_admin._apps:
        _initialized = True
        return True

    source = _credentials_from_env() or _credentials_from_file()
    if source is None:
        logger.warning("No Firebase credentials configured; push notifications disabled")
        return False

    try:
        cred = credentials.Certificate(source)
        firebase_admin.initialize_app(cred, {"databaseURL": os.getenv('DATABASE_URL')})
        _initialized = True
        return True
    except Exception as exc:
        logger.error("Firebase initialization failed; push notifications disabled: %s", exc)
        return False


# Service-account fields, as they appear in a downloaded key_pair.json.
_ENV_TO_FIELD = {
    "FIREBASE_TYPE": "type",
    "FIREBASE_PROJECT_ID": "project_id",
    "FIREBASE_PRIVATE_KEY_ID": "private_key_id",
    "FIREBASE_PRIVATE_KEY": "private_key",
    "FIREBASE_CLIENT_EMAIL": "client_email",
    "FIREBASE_CLIENT_ID": "client_id",
    "FIREBASE_AUTH_URI": "auth_uri",
    "FIREBASE_TOKEN_URI": "token_uri",
    "FIREBASE_AUTH_PROVIDER_X509_CERT_URL": "auth_provider_x509_cert_url",
    "FIREBASE_CLIENT_X509_CERT_URL": "client_x509_cert_url",
    "FIREBASE_UNIVERSE_DOMAIN": "universe_domain",
}

# The three that a service account cannot work without.
_REQUIRED_ENV = ("FIREBASE_PROJECT_ID", "FIREBASE_PRIVATE_KEY", "FIREBASE_CLIENT_EMAIL")


def _credentials_from_env():
    """Build the service-account dict from FIREBASE_* environment variables.

    Preferred over a key file: nothing has to be copied onto a server, and the
    values travel as ordinary secrets in .env or CI. Returns None when the
    required variables are absent, so the file path can be tried instead.
    """
    if not all(os.getenv(name) for name in _REQUIRED_ENV):
        return None

    info = {}
    for env_name, field in _ENV_TO_FIELD.items():
        value = os.getenv(env_name)
        if value:
            info[field] = value

    info.setdefault("type", "service_account")
    # Env vars cannot hold real newlines, so the PEM body arrives with literal
    # backslash-n. Without this the key parses as garbage and every push fails
    # with an opaque error.
    info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


def _credentials_from_file():
    """Fall back to a service-account JSON file, for local development."""
    key_path = getattr(settings, "KEY", None)
    if key_path and os.path.exists(key_path):
        return key_path
    return None


def send_push(title, msg, registration_token, dataObject):
    if not _ensure_app():
        logger.warning("send_push called with Firebase unavailable; dropping notification %r", title)
        return None
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=msg,
        ),
        data=dataObject,
        tokens=registration_token
    )
    return messaging.send_each_for_multicast(message)


def get_ref():
    """Realtime Database reference, or None when Firebase is unavailable.

    Callers must handle None. Returning None beats raising here because a missing
    credential file should not take down endpoints that never touch FCM.
    """
    if not _ensure_app():
        return None
    try:
        return db.reference('alltech/Receipt')
    except Exception as exc:
        logger.error("Could not resolve Firebase db reference: %s", exc)
        return None
