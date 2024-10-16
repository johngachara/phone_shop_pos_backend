from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import logging
import json

from djangoProject15 import settings

logger = logging.getLogger('scheduler')


class CeleryAuthTokenView(APIView):
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
