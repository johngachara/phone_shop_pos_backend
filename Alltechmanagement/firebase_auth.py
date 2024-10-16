from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from firebase_admin import auth
from .models import AuthorizedFirebaseToken
import logging

logger = logging.getLogger('django')


class FirebaseAuthTokenView(APIView):
    def post(self, request, *args, **kwargs):
        firebase_token = request.data.get('idToken')
        request_ip = request.META.get('HTTP_X_FORWARDED_FOR')
        if request_ip:
            request_ip = request_ip.split(',')[0]
        else:
            request_ip = request.META.get('REMOTE_ADDR')

        try:
            decoded_token = auth.verify_id_token(firebase_token)
            uid = decoded_token['uid']

            if not AuthorizedFirebaseToken.objects.filter(token=uid).exists():
                logger.warning(f"Failed JWT token request from IP: {request_ip} with error: Unauthorized token")
                return Response({'detail': 'Unauthorized Firebase token'}, status=status.HTTP_403_FORBIDDEN)

            refresh = RefreshToken()
            refresh['firebase_uid'] = uid
            access_token = str(refresh.access_token)

            logger.info(f"JWT token successfully requested for UID: {uid} from IP: {request_ip}")

            return Response({
                'access': access_token,
                'refresh': str(refresh),
            }, status=status.HTTP_200_OK)

        except auth.ExpiredIdTokenError:
            logger.warning(f"Failed JWT token request from IP: {request_ip} with error: Firebase token expired")
            return Response({'detail': 'Firebase token expired'}, status=status.HTTP_401_UNAUTHORIZED)

        except auth.InvalidIdTokenError:
            logger.warning(f"Failed JWT token request from IP: {request_ip} with error: Invalid Firebase token")
            return Response({'detail': 'Invalid Firebase token'}, status=status.HTTP_401_UNAUTHORIZED)

        except Exception as e:
            logger.warning(f"Failed JWT token request from IP: {request_ip} with error: {str(e)}")
            return Response({'detail': 'Authentication failed'}, status=status.HTTP_400_BAD_REQUEST)
