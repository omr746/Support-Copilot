"""API routes package."""

from copilot.api.routes.ask import router as ask_router
from copilot.api.routes.health import router as health_router

__all__ = ["ask_router", "health_router"]
