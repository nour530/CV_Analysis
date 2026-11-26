"""Career assistant chat (Anthropic Claude)."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_api_settings, get_matching_application_service
from app.api.errors import APIError
from app.api.routes.career import build_insights
from app.api.settings import APISettings
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.core.user_quota import enforce_user_quota
from app.db.models import ChatMessage, utcnow
from app.db.session import get_db
from app.schemas.common import StrictSchema
from app.schemas.hybrid_match import ResumeProfile
from app.services.assistant.context import build_personal_context
from app.services.assistant.service import AssistantUnavailableError, CareerAssistant
from app.services.assistant.settings import AssistantSettings
from app.services.assistant.tools import ToolExecutor
from app.services.matching.application import MatchingApplicationService
from app.services.user_records import get_or_create_user_settings, latest_cv_analysis

router = APIRouter(prefix="/assistant", tags=["Career assistant"])

ASSISTANT_QUOTA = "assistant"
MAX_STORED_MESSAGES = 200


class ChatMessageItem(StrictSchema):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class AssistantStatus(StrictSchema):
    enabled: bool
    personalised: bool
    reason: str | None = None
    provider: str = "Anthropic Claude"


class ChatHistoryResponse(StrictSchema):
    messages: list[ChatMessageItem] = Field(default_factory=list)


class ChatRequest(StrictSchema):
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message is empty.")
        return value


class ChatResponse(StrictSchema):
    user_message: ChatMessageItem
    reply: ChatMessageItem
    tools_used: list[str] = Field(default_factory=list)
    personalised: bool


@lru_cache(maxsize=1)
def get_assistant_settings() -> AssistantSettings:
    return AssistantSettings.from_env()


@lru_cache(maxsize=1)
def _assistant(settings: AssistantSettings) -> CareerAssistant:
    return CareerAssistant(settings)


def get_career_assistant(
    settings: AssistantSettings = Depends(get_assistant_settings),
) -> CareerAssistant:
    return _assistant(settings)


def _item(row: ChatMessage) -> ChatMessageItem:
    return ChatMessageItem(id=row.id, role=row.role, content=row.content, created_at=row.created_at)  # type: ignore[arg-type]


def _personalised(db: Session, user_id: str, api_settings: APISettings) -> bool:
    return api_settings.allow_remote_llm_analysis and get_or_create_user_settings(db, user_id=user_id).allow_remote_analysis


@router.get("/status", response_model=AssistantStatus, summary="Whether the assistant is available and personalised")
def assistant_status(
    context: AuthContext = Depends(get_auth_context),
    api_settings: APISettings = Depends(get_api_settings),
    settings: AssistantSettings = Depends(get_assistant_settings),
    db: Session = Depends(get_db),
) -> AssistantStatus:
    reason = None
    if not settings.enabled:
        reason = "The career assistant is turned off on this server."
    elif not settings.has_credentials:
        reason = "The career assistant is not configured yet (missing Anthropic API key)."
    return AssistantStatus(
        enabled=settings.ready,
        personalised=_personalised(db, context.user.id, api_settings),
        reason=reason,
        provider="Local AI (Ollama)" if settings.provider == "ollama" else "Anthropic Claude",
    )


@router.get("/messages", response_model=ChatHistoryResponse, summary="Your conversation with the assistant")
def chat_history(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ChatHistoryResponse:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == context.user.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(60)
    ).all()
    return ChatHistoryResponse(messages=[_item(row) for row in reversed(rows)])


@router.delete("/messages", status_code=204, summary="Delete your conversation")
def clear_chat(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> None:
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == context.user.id))
    db.commit()


@router.post("/messages", response_model=ChatResponse, summary="Ask the career assistant")
def send_message(
    http_request: Request,
    request: ChatRequest,
    context: AuthContext = Depends(get_auth_context),
    api_settings: APISettings = Depends(get_api_settings),
    settings: AssistantSettings = Depends(get_assistant_settings),
    assistant: CareerAssistant = Depends(get_career_assistant),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ChatResponse:
    if not settings.ready:
        raise APIError(
            status_code=503,
            code="assistant_unavailable",
            message="The career assistant is not available right now.",
        )
    if len(request.message) > settings.max_message_chars:
        raise APIError(
            status_code=413,
            code="message_too_long",
            message=f"Please keep messages under {settings.max_message_chars} characters.",
        )
    enforce_user_quota(
        http_request,
        user_id=context.user.id,
        operation=ASSISTANT_QUOTA,
        limit=settings.quota_attempts,
        window_seconds=settings.quota_window_seconds,
        settings=api_settings,
    )

    user_id = context.user.id
    personalised = _personalised(db, user_id, api_settings)
    profile: ResumeProfile | None = None
    cv_text: str | None = None
    personal_context: str | None = None
    language = "en"
    if personalised:
        insights = build_insights(db, user_id)
        language = insights.language
        personal_context = build_personal_context(
            db, context.user, service, insights=insights.model_dump(mode="json")
        )
        analysis = latest_cv_analysis(db, user_id=user_id)
        if analysis is not None:
            cv_text = analysis.resume_text
            try:
                profile = ResumeProfile.model_validate(analysis.profile_json)
            except ValueError:
                profile = None

    history_rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(settings.history_messages)
    ).all()
    history = [{"role": row.role, "content": row.content} for row in reversed(history_rows)]

    try:
        reply = assistant.reply(
            history=history,
            message=request.message,
            personal_context=personal_context,
            executor=ToolExecutor(service, profile=profile, cv_text=cv_text, language=language),
        )
    except AssistantUnavailableError as exc:
        raise APIError(status_code=503, code="assistant_unavailable", message=str(exc)) from exc

    now = utcnow()
    user_row = ChatMessage(user_id=user_id, role="user", content=request.message, created_at=now)
    reply_row = ChatMessage(
        user_id=user_id, role="assistant", content=reply.text[:20_000], created_at=now + timedelta(microseconds=1)
    )
    db.add_all([user_row, reply_row])
    db.flush()
    # Keep the conversation bounded.
    old_ids = db.scalars(
        select(ChatMessage.id)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .offset(MAX_STORED_MESSAGES)
    ).all()
    if old_ids:
        db.execute(delete(ChatMessage).where(ChatMessage.id.in_(old_ids)))
    db.commit()
    return ChatResponse(
        user_message=_item(user_row),
        reply=_item(reply_row),
        tools_used=reply.tools_used,
        personalised=personalised,
    )
