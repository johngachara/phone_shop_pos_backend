import logging
import os
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from clerk_backend_api import Clerk
logger = logging.getLogger('django')
import jwt
from jwt.exceptions import InvalidTokenError
from dotenv import load_dotenv
load_dotenv()


class ClerkUser:
    def __init__(self, data):
        self.data = data
        self.is_authenticated = True
        self.is_active = True

    def __str__(self):
        return str(self.data)

class ClerkAuthentication(BaseAuthentication):
    def __init__(self):
        self.clerk = Clerk(bearer_auth=os.getenv('CLERK_SECRET_KEY'))

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            raise AuthenticationFailed('No valid authorization token provided')

        token = auth_header.split(' ')[1]
        try:
            # First decode the token to verify it's valid JWT
            try:
                decoded_token = jwt.decode(token, options={"verify_signature": False})
            except InvalidTokenError:
                raise AuthenticationFailed('Invalid JWT token format')

            # Get the user ID from the token
            user_id = decoded_token.get('sub')
            if not user_id:
                raise AuthenticationFailed('No user ID in token')

            # Get user data from Clerk
            try:
                user = self.clerk.users.get(user_id=user_id)
                if not user:
                    raise AuthenticationFailed('User not found')
                return (ClerkUser(user), token)
            except Exception as clerk_error:
                logger.error(str(clerk_error))
                raise AuthenticationFailed(f'Clerk API error')

        except Exception as e:
            logger.error(str(e))
            raise AuthenticationFailed(f'Authentication failed')

    def authenticate_header(self, request):
        return 'Bearer'