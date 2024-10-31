import logging
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import Token

logger = logging.getLogger('django.security')


# custom_auth.py


class CeleryUser:
    def __init__(self, token: Token):
        self.token = token
        self.is_active = True
        self.is_authenticated = True

    def __str__(self):
        return f"CeleryUser(token={self.token})"


class CeleryJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        logger.debug("CeleryJWTAuthentication: authenticate called")
        jwt_auth = JWTAuthentication()

        try:
            header = jwt_auth.get_header(request)
            if header is None:
                logger.debug("CeleryJWTAuthentication: No JWT header found")
                return None

            raw_token = jwt_auth.get_raw_token(header)
            if raw_token is None:
                logger.debug("CeleryJWTAuthentication: No raw token found")
                return None

            validated_token = jwt_auth.get_validated_token(raw_token)
            logger.debug(f"CeleryJWTAuthentication: Token validated successfully")

            if not validated_token.get('is_celery', False):
                logger.error("CeleryJWTAuthentication: Invalid token for Celery authentication")
                raise AuthenticationFailed('Invalid token for Celery authentication')

            celery_user = CeleryUser(token=validated_token)
            logger.debug(f"CeleryJWTAuthentication: Authentication successful for user {celery_user}")
            return celery_user, validated_token

        except (InvalidToken, TokenError) as e:
            logger.error(f"CeleryJWTAuthentication: Error validating token - {str(e)}")
            raise AuthenticationFailed("Invalid token")

    def authenticate_header(self, request):
        logger.debug("CeleryJWTAuthentication: authenticate_header called")
        return JWTAuthentication().authenticate_header(request)
