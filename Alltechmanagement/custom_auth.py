from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework.exceptions import AuthenticationFailed


class CustomUser:
    def __init__(self, firebase_uid):
        self.firebase_uid = firebase_uid
        self.is_authenticated = True

    def __str__(self):
        return f"CustomUser(firebase_uid={self.firebase_uid})"


class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        try:
            firebase_uid = validated_token.get('firebase_uid')
            if not firebase_uid:
                raise InvalidToken('Token contains no valid firebase_uid')

            # Return an instance of the CustomUser class
            return CustomUser(firebase_uid=firebase_uid)
        except Exception as e:
            raise AuthenticationFailed(str(e))
