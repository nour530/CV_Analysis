from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.passwords import PASSWORD_MAX_LENGTH, password_format_errors
from app.db.models import User, UserSettings

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
FULL_NAME_MIN_LENGTH = 2
FULL_NAME_MAX_LENGTH = 160


class AuthSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(AuthSchema):
    full_name: str = Field(min_length=FULL_NAME_MIN_LENGTH, max_length=FULL_NAME_MAX_LENGTH)
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < FULL_NAME_MIN_LENGTH:
            raise ValueError("Full name must contain at least two characters.")
        if CONTROL_CHARACTERS.search(normalized):
            raise ValueError("Full name contains invalid control characters.")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        errors = password_format_errors(value)
        if errors:
            raise ValueError(" ".join(errors))
        return value


class LoginRequest(AuthSchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()


class UpdateProfileRequest(AuthSchema):
    full_name: str = Field(min_length=FULL_NAME_MIN_LENGTH, max_length=FULL_NAME_MAX_LENGTH)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < FULL_NAME_MIN_LENGTH:
            raise ValueError("Full name must contain at least two characters.")
        if CONTROL_CHARACTERS.search(normalized):
            raise ValueError("Full name contains invalid control characters.")
        return normalized


class PasswordResetRequest(AuthSchema):
    email: EmailStr


class PasswordResetConfirm(AuthSchema):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        errors = password_format_errors(value)
        if errors:
            raise ValueError(" ".join(errors))
        return value


class VerifyEmailRequest(AuthSchema):
    token: str = Field(min_length=10, max_length=200)


class AcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class DeleteAccountRequest(AuthSchema):
    """Deleting an account needs the current password, so a stolen access
    token alone cannot erase it."""

    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class ChangePasswordRequest(AuthSchema):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        errors = password_format_errors(value)
        if errors:
            raise ValueError(" ".join(errors))
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    full_name: str
    email: EmailStr
    is_active: bool
    email_verified: bool = False
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            is_active=user.is_active,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        )


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = "If this email is available, the account can now sign in."


class AuthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=60)
    user: UserResponse
    csrf_token: str


class UserSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_limit: int = Field(ge=1, le=50)
    candidate_pool_size: int = Field(ge=1, le=200)
    preferred_category: str | None = Field(default=None, max_length=120)
    allow_remote_analysis: bool = False
    target_domain: str | None = Field(default=None, max_length=40)
    target_role: str | None = Field(default=None, max_length=160)
    updated_at: datetime | None = None

    @classmethod
    def from_settings(cls, settings: UserSettings) -> UserSettingsResponse:
        return cls(
            recommendation_limit=settings.recommendation_limit,
            candidate_pool_size=settings.candidate_pool_size,
            preferred_category=settings.preferred_category,
            allow_remote_analysis=settings.allow_remote_analysis,
            target_domain=settings.target_domain,
            target_role=settings.target_role,
            updated_at=settings.updated_at,
        )


class UserSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_limit: int = Field(ge=1, le=50)
    candidate_pool_size: int = Field(ge=1, le=200)
    preferred_category: str | None = Field(default=None, max_length=120)
    allow_remote_analysis: bool = False
    # Optional: only applied when sent, so older clients cannot erase them.
    target_domain: str | None = Field(default=None, max_length=40)
    target_role: str | None = Field(default=None, max_length=160)

    @field_validator("preferred_category", "target_domain", "target_role")
    @classmethod
    def normalize_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CVAnalysisSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    filename: str | None
    document_type: str | None
    page_count: int | None
    character_count: int | None
    skill_count: int
    resume_id: str | None
    category: str | None
    ats_score: int | None = None
    created_at: datetime


class CVAnalysisHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CVAnalysisSummary]


class MatchHistorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    kind: Literal["job", "recommendations"]
    job_id: str | None
    job_title: str | None
    resume_id: str | None
    score: float | None
    created_at: datetime


class MatchHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MatchHistorySummary]


class MatchHistoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    kind: Literal["job", "recommendations"]
    created_at: datetime
    payload: dict[str, Any]
