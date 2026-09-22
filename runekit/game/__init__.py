import logging
import os
import sys
from .instance import GameInstance
from .manager import GameManager

logger = logging.getLogger(__name__)


def _is_wayland_session() -> bool:
    """Detect a Wayland session on Linux.

    Checks the standard session-type env var first, falling back to the
    presence of WAYLAND_DISPLAY (some setups don't set XDG_SESSION_TYPE).
    Returns False for X11/XWayland sessions so those keep using
    X11GameManager.
    """
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return True

    return bool(os.environ.get("WAYLAND_DISPLAY"))


def get_platform_manager() -> GameManager:
    if sys.platform == "linux":
        if _is_wayland_session():
            logger.info("Detected Wayland session, using WaylandGameManager")
            from .wayland.manager import WaylandGameManager

            return WaylandGameManager()

        from .x11.manager import X11GameManager

        return X11GameManager()
    elif sys.platform == "darwin":
        from .quartz.manager import QuartzGameManager

        return QuartzGameManager()

    raise NotImplementedError
