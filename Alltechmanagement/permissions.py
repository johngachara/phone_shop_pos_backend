"""Role permissions.

Two roles, from the redesign brief:

    Employee  CRUD on shop stock, and the sale operations that go with running
              the till. Nothing else.
    Manager   Everything, including analytics and user management.

Every endpoint states which it requires. An endpoint with only IsAuthenticated
is a bug: before this change every authenticated caller could reach every
endpoint, including the analytics that were only ever meant for the owner.
"""
from rest_framework.permissions import BasePermission

from Alltechmanagement.supabase_auth import ROLE_MANAGER, VALID_ROLES


class IsAlltechUser(BasePermission):
    """Authenticated, belongs to Alltech, and carries a recognised role."""

    message = 'Not authorized for this application.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'is_alltech', False)
            and getattr(user, 'role', None) in VALID_ROLES
        )


class IsManager(IsAlltechUser):
    """Manager only. Analytics, reporting and user management."""

    message = 'This action requires the manager role.'

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.role == ROLE_MANAGER
        )


class IsEmployeeOrManager(IsAlltechUser):
    """Either role.

    Employees run the till, so stock and sales operations are open to both.
    Written as its own class rather than reusing IsAlltechUser so that the
    intent is visible at the endpoint, and so narrowing it later is one edit.
    """

    message = 'This action requires an Alltech role.'


class IsMachineClient(BasePermission):
    """A background worker holding a machine token, not a person.

    The scheduler and the sales-report job have no Supabase identity and no
    role, so the role classes above would reject them. They are paired with
    CeleryJWTAuthentication, which is what actually proves the caller: this
    class only confirms that authentication produced a principal, and exists so
    that no endpoint is left with a permission class that happens to admit
    anyone who authenticated by any means.
    """

    message = 'This endpoint is for background jobs.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return False
        # A Supabase principal must not reach a machine endpoint by presenting a
        # user token; those endpoints send email and burn AI quota.
        return not isinstance(getattr(user, 'is_alltech', None), bool)
