from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import logging
import json

from Alltechmanagement.throttles import CeleryAuthTokenThrottle
from django.conf import settings

logger = logging.getLogger('scheduler')


class CeleryAuthTokenView(APIView):
    """Exchange the shared machine key for a short-lived token.

    Explicitly unauthenticated, and it has to be: this is where a background
    job gets its token, so requiring one here is a deadlock -- the caller can
    never obtain the credential the endpoint demands. The project default is
    now IsAlltechUser, which silently applied here and broke every scheduled
    job with "Authentication credentials were not provided".

    The API key in the body is the credential. What keeps that safe is the
    throttle: CeleryAuthTokenThrottle allows 5 attempts a day per address,
    which is generous for three jobs and useless for guessing a key.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CeleryAuthTokenThrottle]

    def post(self, request, *args, **kwargs):
        try:
            # Try to parse the JSON data
            data = json.loads(request.body)
            api_key = data.get('api_key')
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in request: {str(e)}")
            return Response({'detail': 'Invalid JSON in request body'}, status=status.HTTP_400_BAD_REQUEST)

        if not api_key:
            return Response({'detail': 'API key is required'}, status=status.HTTP_400_BAD_REQUEST)

        request_ip = request.META.get('HTTP_X_FORWARDED_FOR')
        if request_ip:
            request_ip = request_ip.split(',')[0]  # Handle multiple IPs
        else:
            request_ip = request.META.get('REMOTE_ADDR')

        try:
            if api_key != settings.CELERY_API_KEY:
                logger.warning(
                    f"Failed JWT token request for Celery from IP: {request_ip} with error: Invalid API key"
                )
                return Response({'detail': 'Invalid API key'}, status=status.HTTP_403_FORBIDDEN)

            # Generate JWT tokens using RefreshToken
            refresh = RefreshToken()
            refresh['is_celery'] = True  # Add a custom claim to identify Celery tokens
            access_token = str(refresh.access_token)

            # Log the success
            logger.info(f"JWT token successfully requested for Celery from IP: {request_ip}")

            # Return the access token and refresh token
            return Response({
                'access': access_token,
                'refresh': str(refresh),
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.warning(
                f"Failed JWT token request for Celery from IP: {request_ip} with error: {str(e)}"
            )
            return Response({'detail': 'Error generating token'}, status=status.HTTP_400_BAD_REQUEST)
