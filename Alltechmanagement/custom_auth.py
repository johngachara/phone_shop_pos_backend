from django.utils import timezone
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

            # Check if the token has expired
            exp = validated_token.get('exp')
            if exp is not None:
                # Convert the offset-naive datetime to offset-aware
                exp_datetime = timezone.datetime.fromtimestamp(exp, tz=timezone.utc)
                if timezone.now() > exp_datetime:
                    raise InvalidToken('Token has expired')

            return CustomUser(firebase_uid=firebase_uid)
        except Exception as e:
            raise AuthenticationFailed(str(e))
