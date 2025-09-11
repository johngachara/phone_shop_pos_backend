import os

import firebase_admin
from firebase_admin import credentials, messaging, db
from djangoProject15 import settings
from dotenv import load_dotenv
load_dotenv()
# Check if the app is already initialized
if not firebase_admin._apps:
    cred = credentials.Certificate(settings.KEY)
    firebase_admin.initialize_app(cred, {
        "databaseURL": os.getenv('DATABASE_URL')
    })

ref = db.reference('alltech/Receipt')


def send_push(title, msg, registration_token, dataObject):
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=msg,
        ),
        data=dataObject,
        tokens=registration_token
    )
    response = messaging.send_each_for_multicast(message)
    return response


def get_ref():
    return ref
