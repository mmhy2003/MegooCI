"""
Step action handler registry.

Import this module to get the global ``ACTION_REGISTRY`` mapping from step
type name to handler instance.  The build executor calls
``get_handler(step_type)`` to look up the right handler at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import StepActionHandler

_REGISTRY: dict[str, StepActionHandler] = {}


def register(step_type: str, handler: StepActionHandler) -> None:
    _REGISTRY[step_type] = handler


def get_handler(step_type: str) -> StepActionHandler | None:
    return _REGISTRY.get(step_type)


def registered_types() -> list[str]:
    return list(_REGISTRY.keys())


def _bootstrap() -> None:
    """Eagerly import all built-in handler modules so they self-register."""
    from . import (  # noqa: F401
        shell,
        docker,
        git,
        ssh,
        wait,
        notify,
        trigger,
        write_file,
    )


_bootstrap()
