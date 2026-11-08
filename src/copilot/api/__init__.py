"""api - HTTP layer and composition root (container.py)."""

from copilot.api.container import Container, get_answer_model, get_container
from copilot.api.main import app, create_app
from copilot.api.schemas import AskRequest, AskResponse, HealthResponse, ReadyResponse

__all__ = [
    "AskRequest",
    "AskResponse",
    "Container",
    "HealthResponse",
    "ReadyResponse",
    "app",
    "create_app",
    "get_answer_model",
    "get_container",
]
