from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_schemas import UserSettingsResponse, UserSettingsUpdate
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.db.session import get_db
from app.services.user_records import get_or_create_user_settings, update_user_settings

router = APIRouter(prefix="/account", tags=["Account"])


@router.get(
    "/settings",
    response_model=UserSettingsResponse,
    summary="Get the authenticated user's matching settings",
)
def get_settings(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> UserSettingsResponse:
    settings = get_or_create_user_settings(db, user_id=context.user.id)
    return UserSettingsResponse.from_settings(settings)


@router.put(
    "/settings",
    response_model=UserSettingsResponse,
    summary="Update the authenticated user's matching settings",
)
def put_settings(
    payload: UserSettingsUpdate,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> UserSettingsResponse:
    settings = update_user_settings(
        db,
        user_id=context.user.id,
        payload=payload,
    )
    return UserSettingsResponse.from_settings(settings)
