from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from firebase_admin import auth
from .models import AuthorizedFirebaseToken
import logging

logger = logging.getLogger('django.security')


class FirebaseAuthTokenView(APIView):
    def post(self, request, *args, **kwargs):
        firebase_token = request.data.get('idToken')
        request_ip = request.META.get('HTTP_X_FORWARDED_FOR')
        if request_ip:
            request_ip = request_ip.split(',')[0]  # Handle multiple IPs
        else:
            request_ip = request.META.get('REMOTE_ADDR')

        try:
            # Verify Firebase Token and get decoded payload
            decoded_token = auth.verify_id_token(firebase_token)
            uid = decoded_token['uid']  # Extract the UID
            # Check if the token is in the AuthorizedFirebaseToken table
            if not AuthorizedFirebaseToken.objects.filter(token=uid).exists():
                logger.warning(
                    f"Failed JWT token request from IP: {request_ip} with error: Unauthorized token"
                )
                return Response({'detail': 'Unauthorized Firebase token'}, status=status.HTTP_403_FORBIDDEN)

            # Generate JWT tokens using RefreshToken
            refresh = RefreshToken()
            refresh['firebase_uid'] = uid  # Store UID in token claims
            access_token = str(refresh.access_token)  # Get the access token

            # Log the success for Fail2Ban
            logger.info(f"JWT token successfully requested for UID: {uid} from IP: {request_ip}")

            # Return the access token and refresh token to the frontend
            return Response({
                'access': access_token,
                'refresh': str(refresh),
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.warning(
                f"Failed JWT token request from IP: {request_ip} with error: {str(e)}"
            )
            return Response({'detail': 'Invalid Firebase Token'}, status=status.HTTP_400_BAD_REQUEST)
