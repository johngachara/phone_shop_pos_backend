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

    key_path = settings.KEY
    if not os.path.exists(key_path):
        logger.warning("Firebase credentials not found at %s; push notifications disabled", key_path)
        return False

    try:
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred, {"databaseURL": os.getenv('DATABASE_URL')})
        _initialized = True
        return True
    except Exception as exc:
        logger.error("Firebase initialization failed; push notifications disabled: %s", exc)
        return False


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
