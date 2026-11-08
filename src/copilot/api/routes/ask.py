"""Customer support ask route (/ask)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from copilot.api.container import get_answer_model
from copilot.api.schemas import AskRequest, AskResponse
from copilot.llm.base import ChatModel
from copilot.llm.schemas import ChatMessage, ChatRequest

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit support query",
    description="Processes a user question through the configured answer role model.",
)
@router.post("/ask/", include_in_schema=False)
async def ask(
    payload: AskRequest,
    model: Annotated[ChatModel, Depends(get_answer_model)],
) -> AskResponse:
    """Submit a question to the LLM answer role model and return the generated answer."""
    req = ChatRequest(
        messages=(ChatMessage(role="user", content=payload.question),),
    )

    response = await model.complete(req)

    return AskResponse(
        answer=response.content,
        model=response.model,
        provider=response.provider,
        session_id=payload.session_id,
    )
