"""Transaction-neutral, principal-scoped repositories.

API/services own commit/rollback. Pass a currently authenticated User to every
resource query; administrator status alone never authorizes private/shared data.
"""

from .access import AccessRepository, AuthorizationError, NotFound
from .private import PrivateRepository
from .workspaces import WorkspaceRepository

__all__ = [
    "AccessRepository",
    "AuthorizationError",
    "NotFound",
    "PrivateRepository",
    "WorkspaceRepository",
]
