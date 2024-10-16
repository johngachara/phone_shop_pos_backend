from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
import logging

logger = logging.getLogger('django.security')


class RefreshTokenView(APIView):
    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get('refresh')
        request_ip = request.META.get('HTTP_X_FORWARDED_FOR')
        if request_ip:
            request_ip = request_ip.split(',')[0]
        else:
            request_ip = request.META.get('REMOTE_ADDR')

        if not refresh_token:
            logger.warning(f"Refresh token request failed from IP: {request_ip}. No refresh token provided.")
            return Response({'detail': 'No refresh token provided'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            refresh = RefreshToken(refresh_token)
            access_token = str(refresh.access_token)

            logger.info(f"Refresh token successfully used from IP: {request_ip}")

            return Response({
                'access': access_token,
            }, status=status.HTTP_200_OK)

        except TokenError as e:
            if 'token_not_valid' in str(e):
                logger.warning(f"Refresh token expired from IP: {request_ip}")
                return Response({'detail': 'Refresh token expired'}, status=status.HTTP_401_UNAUTHORIZED)
            else:
                logger.warning(f"Invalid refresh token from IP: {request_ip}. Error: {str(e)}")
                return Response({'detail': 'Invalid refresh token'}, status=status.HTTP_401_UNAUTHORIZED)

        except Exception as e:
            logger.error(f"Unexpected error in refresh token request from IP: {request_ip}. Error: {str(e)}")
            return Response({'detail': 'An error occurred while refreshing the token'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
