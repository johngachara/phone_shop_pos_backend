"""Push notifications.

Replaces the WhatsApp bot, which drove a real WhatsApp Web session through
Puppeteer -- a browser automation pretending to be a person, which breaks
whenever the session drops and was never a supported way to use WhatsApp.

Delivery is Manager-only. The old PushNotificationToken table was a bare token
column with no association to anybody, so "send this to managers" was not
expressible; PushDevice carries the Supabase user id and role that makes it so.
"""
import logging

from firebase_admin.messaging import UnregisteredError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from Alltechmanagement.FCMManager import send_push
from Alltechmanagement.models import PushDevice
from Alltechmanagement.permissions import IsEmployeeOrManager
from Alltechmanagement.supabase_auth import ROLE_MANAGER
from Alltechmanagement.throttles import InventoryCheckThrottle

logger = logging.getLogger('django')

# FCM rejects a multicast larger than this.
FCM_BATCH_SIZE = 500


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def register_device(request):
    """Register this browser or device for push.

    Re-registering the same token updates its owner rather than creating a
    duplicate: a shared counter tablet gets handed between staff, and the
    token then belongs to whoever signed in last.
    """
    token = (request.data.get('token') or '').strip()
    if not token:
        return Response({'error': 'token is required.'}, status=400)

    device, created = PushDevice.objects.update_or_create(
        token=token,
        defaults={'user_id': request.user.id, 'role': request.user.role},
    )
    return Response(
        {'registered': True, 'created': created},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(['DELETE'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def unregister_device(request):
    token = (request.data.get('token') or '').strip()
    if token:
        PushDevice.objects.filter(token=token).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


def notify_managers(title, body, data=None):
    """Send a notification to every registered manager device.

    Returns the number of devices it was sent to. Never raises: this is called
    from scheduled reporting, and a push failing is not a reason for the report
    itself to fail.
    """
    tokens = list(
        PushDevice.objects
        .filter(role=ROLE_MANAGER)
        .values_list('token', flat=True)
        .distinct()
    )
    if not tokens:
        logger.info("No manager devices registered; skipping push %r", title)
        return 0

    sent = 0
    for start in range(0, len(tokens), FCM_BATCH_SIZE):
        batch = tokens[start:start + FCM_BATCH_SIZE]
        try:
            response = send_push(title, body, batch, data or {})
            if response is None:
                # Firebase is not configured. Already logged there.
                continue
            sent += getattr(response, 'success_count', len(batch))

            # Drop tokens Firebase rejected as unregistered, or the list grows
            # forever with devices that will never receive anything again.
            #
            # This used to match on 'not-registered' (hyphenated) appearing in
            # str(exception), which never matched anything: the SDK raises
            # UnregisteredError('NotRegistered') -- no hyphen, and str() of it
            # is just the message, not the class name either way. So every
            # dead token Firebase ever rejected stayed in the table forever,
            # rejected again on every future send. Checking the exception
            # type directly, rather than guessing at its string form, is what
            # the SDK actually documents for telling this case apart.
            responses = getattr(response, 'responses', []) or []
            dead = [
                batch[i] for i, item in enumerate(responses)
                if not getattr(item, 'success', True)
                and isinstance(getattr(item, 'exception', None), UnregisteredError)
            ]
            if dead:
                PushDevice.objects.filter(token__in=dead).delete()
                logger.info("Removed %d unregistered push devices", len(dead))
        except Exception as exc:
            logger.error("Push notification failed: %s", exc)

    return sent
