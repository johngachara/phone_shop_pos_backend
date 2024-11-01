from datetime import datetime, timezone
from firebase_admin import auth
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework.exceptions import AuthenticationFailed
from django.utils import timezone as django_timezone
import logging

logger = logging.getLogger('django')


class CustomUser:
    def __init__(self, firebase_uid):
        self.firebase_uid = firebase_uid
        self.is_authenticated = True

    def __str__(self):
        return f"CustomUser(firebase_uid={self.firebase_uid})"

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return True

    def get_username(self):
        return self.firebase_uid


class FirebaseAuthentication:
    """
    Custom authentication class that verifies Firebase ID tokens and
    attaches a CustomUser instance to the request if successful.
    """

    def authenticate(self, request):
        # Get the 'Authorization' header from the request
        auth_header = request.headers.get('Authorization')
        logger.debug('Checking Authorization header')

        if not auth_header:
            logger.debug("No Authorization header found")
            return None

        if not auth_header.startswith('Bearer '):
            logger.error("Authorization header format is invalid")
            raise AuthenticationFailed('Invalid Authorization header format')

        # Extract the token from the Authorization header
        token = auth_header.split(' ')[1]

        try:
            # Verify the Firebase token
            decoded_token = auth.verify_id_token(token)
            firebase_uid = decoded_token.get('uid')

            if not firebase_uid:
                raise AuthenticationFailed('Invalid Firebase token: No UID found')

            user = CustomUser(firebase_uid=firebase_uid)
            logger.info(f"Successfully authenticated user: {firebase_uid}")

            return (user, None)  # Returning user and None (no credentials)

        except Exception as e:
            logger.error(f"Firebase token verification failed: {str(e)}")
            raise AuthenticationFailed(f'Authentication failed: {str(e)}')


class CustomJWTAuthentication(JWTAuthentication):
    """
    Custom JWT Authentication that retrieves the user from the validated token.
    """

    def get_user(self, validated_token):
        try:
            firebase_uid = validated_token.get('firebase_uid')
            if not firebase_uid:
                raise InvalidToken('Token contains no valid firebase_uid')

            # Check if the token has expired
            exp = validated_token.get('exp')
            if exp is not None:
                exp_datetime = datetime.fromtimestamp(exp, tz=timezone.utc)
                if django_timezone.now() > exp_datetime:
                    raise InvalidToken('Token has expired')

            return CustomUser(firebase_uid=firebase_uid)

        except Exception as e:
            logger.error(f"Error getting user from token: {str(e)}")
            raise AuthenticationFailed('Authentication failed due to an internal error.')
