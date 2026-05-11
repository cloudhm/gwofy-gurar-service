"""
Legacy module name — stacks are split into `storage_stack` and `api_stack`.

Deploy via `app.py` (GwofyGuardStorage + GwofyGuardApi).
"""

from gwofy_guard_service.api_stack import ApiStack
from gwofy_guard_service.storage_stack import StorageStack

__all__ = ["ApiStack", "StorageStack"]
