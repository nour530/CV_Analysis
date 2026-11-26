from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_schemas import (
    AuthResponse,
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    RegistrationResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.api.errors import APIError
from app.api.settings import APISettings, get_api_settings
from app.auth.cookies import (
    clear_auth_cookies,
    new_csrf_token,
    set_auth_cookies,
    verify_csrf,
)
from app.auth.dependencies import get_auth_context, get_auth_service
from app.auth.service import AuthContext, AuthService, AuthServiceError, AuthTokens
from app.auth.settings import AuthSettings, get_auth_settings
from app.auth.passwords import verify_password
from app.auth.tokens import hash_token
from app.core.audit import audit_event, pseudonymize_identifier
from app.core.rate_limit import (
    RATE_LIMITER,
    client_ip,
    enforce_rate_limit,
    make_rate_limit_key,
)
from app.db.models import User
from app.services.account_data import delete_account

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _audit_auth_event(
    request: Request,
    settings: APISettings,
    event_type: str,
    outcome: str,
    *,
    user_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    audit_event(
        event_type,
        outcome,
        request=request,
        user_id=user_id,
        client_ip=client_ip(request, settings.trusted_proxy_networks),
        metadata=metadata,
    )


def _enforce_auth_rate_limit(
    request: Request,
    settings: APISettings,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
    discriminator: str | None = None,
    trusted_proxy_networks: tuple[str, ...] | None = None,
) -> None:
    try:
        enforce_rate_limit(
            request,
            scope=scope,
            limit=limit,
            window_seconds=window_seconds,
            discriminator=discriminator,
            trusted_proxy_networks=trusted_proxy_networks,
        )
    except APIError as exc:
        if exc.code == "rate_limit_exceeded":
            metadata: dict[str, object] = {"scope": scope}
            retry_after = (exc.headers or {}).get("Retry-After")
            if retry_after is not None:
                metadata["retry_after_seconds"] = retry_after
            _audit_auth_event(
                request,
                settings,
                "auth.rate_limit.rejected",
                "failure",
                metadata=metadata,
            )
        raise


def _verify_csrf_or_audit(
    request: Request,
    settings: APISettings,
    auth_settings: AuthSettings,
    *,
    action: str,
) -> None:
    try:
        verify_csrf(request, auth_settings)
    except APIError as exc:
        if exc.code == "csrf_failed":
            _audit_auth_event(
                request,
                settings,
                "auth.csrf.failed",
                "failure",
                metadata={"action": action},
            )
        raise


def _auth_response(
    user: User,
    tokens: AuthTokens,
    csrf_token: str,
    response: Response,
    settings: AuthSettings,
) -> AuthResponse:
    set_auth_cookies(
        response,
        refresh_token=tokens.refresh_token,
        csrf_token=csrf_token,
        settings=settings,
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserResponse.from_user(user),
        csrf_token=csrf_token,
    )


def _raise_auth_error(exc: AuthServiceError) -> APIError:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return APIError(
        status_code=exc.status_code,
        code=exc.code,
        message=str(exc),
        headers=headers,
    )


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=202,
    summary="Register a user account",
)
def register(
    request: Request,
    payload: RegisterRequest,
    settings: APISettings = Depends(get_api_settings),
    service: AuthService = Depends(get_auth_service),
) -> RegistrationResponse:
    normalized_email = str(payload.email).strip().casefold()
    account_ref = pseudonymize_identifier(normalized_email)
    ip_address = client_ip(request, settings.trusted_proxy_networks)
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="register-ip",
        limit=settings.registration_rate_limit_attempts,
        window_seconds=settings.registration_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="register-account",
        limit=settings.registration_rate_limit_attempts,
        window_seconds=settings.registration_rate_limit_window_seconds,
        discriminator=f"{account_ref}:{ip_address}",
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    _audit_auth_event(
        request,
        settings,
        "auth.registration.requested",
        "success",
        metadata={"account_ref": account_ref},
    )
    try:
        user = service.register(
            full_name=payload.full_name,
            email=normalized_email,
            password=payload.password,
            check_common_password=settings.common_password_check_enabled,
        )
    except AuthServiceError as exc:
        _audit_auth_event(
            request,
            settings,
            "auth.registration.failed",
            "failure",
            metadata={"account_ref": account_ref, "reason": exc.code},
        )
        if exc.code == "password_too_common":
            _audit_auth_event(
                request,
                settings,
                "auth.password.common_rejected",
                "failure",
                metadata={"account_ref": account_ref, "action": "registration"},
            )
        raise _raise_auth_error(exc) from exc
    if user is None:
        _audit_auth_event(
            request,
            settings,
            "auth.registration.duplicate",
            "failure",
            metadata={"account_ref": account_ref},
        )
    else:
        _audit_auth_event(
            request,
            settings,
            "auth.registration.succeeded",
            "success",
            user_id=user.id,
            metadata={"account_ref": account_ref},
        )
    return RegistrationResponse()


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Sign in and create a session",
)
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    settings: APISettings = Depends(get_api_settings),
    auth_settings: AuthSettings = Depends(get_auth_settings),
    service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    normalized_email = str(payload.email).strip().casefold()
    account_ref = pseudonymize_identifier(normalized_email)
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="login-ip",
        limit=settings.login_rate_limit_attempts * 4,
        window_seconds=settings.login_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="login-account",
        limit=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        discriminator=account_ref,
    )
    try:
        user = service.authenticate(
            email=normalized_email,
            password=payload.password,
        )
        tokens = service.create_session(user)
    except AuthServiceError as exc:
        if exc.audit_reason == "account_disabled":
            _audit_auth_event(
                request,
                settings,
                "auth.login.disabled",
                "failure",
                user_id=exc.audit_user_id,
                metadata={"account_ref": account_ref, "reason": "account_disabled"},
            )
        else:
            _audit_auth_event(
                request,
                settings,
                "auth.login.failed",
                "failure",
                metadata={"account_ref": account_ref, "reason": "invalid_credentials"},
            )
        raise _raise_auth_error(exc) from exc
    RATE_LIMITER.reset(make_rate_limit_key("login-account", account_ref))
    _audit_auth_event(
        request,
        settings,
        "auth.login.succeeded",
        "success",
        user_id=user.id,
        metadata={"account_ref": account_ref},
    )
    return _auth_response(user, tokens, new_csrf_token(), response, auth_settings)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Rotate the refresh token and issue a new access token",
)
def refresh(
    request: Request,
    response: Response,
    settings: APISettings = Depends(get_api_settings),
    auth_settings: AuthSettings = Depends(get_auth_settings),
    service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    refresh_token = request.cookies.get(auth_settings.refresh_cookie_name)
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="refresh-ip",
        limit=settings.refresh_rate_limit_attempts * 4,
        window_seconds=settings.refresh_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    if refresh_token:
        _enforce_auth_rate_limit(
            request,
            settings,
            scope="refresh-token",
            limit=settings.refresh_rate_limit_attempts,
            window_seconds=settings.refresh_rate_limit_window_seconds,
            discriminator=hash_token(refresh_token),
        )
    if not refresh_token:
        _audit_auth_event(
            request,
            settings,
            "auth.refresh.failed",
            "failure",
            metadata={"reason": "missing_refresh_token"},
        )
        raise APIError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _verify_csrf_or_audit(
        request,
        settings,
        auth_settings,
        action="refresh",
    )
    try:
        user, tokens = service.refresh_session(refresh_token)
    except AuthServiceError as exc:
        if exc.code == "refresh_token_reused":
            _audit_auth_event(
                request,
                settings,
                "auth.refresh.reuse_detected",
                "failure",
                user_id=exc.audit_user_id,
                metadata={"reason": "refresh_token_reused"},
            )
            _audit_auth_event(
                request,
                settings,
                "auth.session.revoked",
                "success",
                user_id=exc.audit_user_id,
                metadata={"reason": "refresh_token_reused"},
            )
        elif exc.code == "account_unavailable":
            _audit_auth_event(
                request,
                settings,
                "auth.refresh.failed",
                "failure",
                user_id=exc.audit_user_id,
                metadata={"reason": "account_unavailable"},
            )
            _audit_auth_event(
                request,
                settings,
                "auth.session.revoked",
                "success",
                user_id=exc.audit_user_id,
                metadata={"reason": "account_unavailable"},
            )
        else:
            _audit_auth_event(
                request,
                settings,
                "auth.refresh.failed",
                "failure",
                user_id=exc.audit_user_id,
                metadata={"reason": exc.code},
            )
        clear_auth_cookies(response, auth_settings)
        raise _raise_auth_error(exc) from exc
    RATE_LIMITER.reset(
        make_rate_limit_key("refresh-token", hash_token(refresh_token))
    )
    _audit_auth_event(
        request,
        settings,
        "auth.refresh.succeeded",
        "success",
        user_id=user.id,
    )
    return _auth_response(user, tokens, new_csrf_token(), response, auth_settings)


@router.post(
    "/logout",
    status_code=204,
    summary="Revoke the current session and clear authentication cookies",
)
def logout(
    request: Request,
    response: Response,
    settings: APISettings = Depends(get_api_settings),
    auth_settings: AuthSettings = Depends(get_auth_settings),
    service: AuthService = Depends(get_auth_service),
) -> None:
    refresh_token = request.cookies.get(auth_settings.refresh_cookie_name)
    refresh_revoked = False
    if refresh_token:
        try:
            _verify_csrf_or_audit(
                request,
                settings,
                auth_settings,
                action="logout",
            )
        except APIError:
            _audit_auth_event(
                request,
                settings,
                "auth.logout.failed",
                "failure",
                metadata={"reason": "csrf_failed"},
            )
            raise
        refresh_revoked = service.revoke_refresh_token(refresh_token)

    user_id: str | None = None
    access_revoked = False
    authorization = request.headers.get("Authorization", "")
    scheme, _, access_token = authorization.partition(" ")
    if scheme.casefold() == "bearer" and access_token:
        try:
            context = service.authenticate_access_token(access_token)
            user_id = context.user.id
            access_revoked = service.revoke_current_session(context.session.id)
        except AuthServiceError:
            pass

    clear_auth_cookies(response, auth_settings)
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    _audit_auth_event(
        request,
        settings,
        "auth.logout.succeeded",
        "success",
        user_id=user_id,
    )
    if refresh_revoked or access_revoked:
        _audit_auth_event(
            request,
            settings,
            "auth.session.revoked",
            "success",
            user_id=user_id,
            metadata={"reason": "logout"},
        )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the authenticated user",
)
def me(context: AuthContext = Depends(get_auth_context)) -> UserResponse:
    return UserResponse.from_user(context.user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update the authenticated user's profile",
)
def update_me(
    payload: UpdateProfileRequest,
    context: AuthContext = Depends(get_auth_context),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = service.update_profile(user=context.user, full_name=payload.full_name)
    return UserResponse.from_user(user)


@router.delete(
    "/me",
    status_code=204,
    summary="Delete the account and all of its data (asks for the password)",
)
def delete_me(
    request: Request,
    response: Response,
    payload: DeleteAccountRequest,
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    auth_settings: AuthSettings = Depends(get_auth_settings),
    service: AuthService = Depends(get_auth_service),
) -> None:
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="delete-account-ip",
        limit=settings.password_change_rate_limit_attempts * 4,
        window_seconds=settings.password_change_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="delete-account-user",
        limit=settings.password_change_rate_limit_attempts,
        window_seconds=settings.password_change_rate_limit_window_seconds,
        discriminator=context.user.id,
    )
    if not verify_password(payload.current_password, context.user.password_hash):
        _audit_auth_event(
            request,
            settings,
            "account.delete.failed",
            "failure",
            user_id=context.user.id,
            metadata={"reason": "invalid_current_password"},
        )
        raise APIError(status_code=401, code="invalid_current_password", message="Current password is incorrect.")
    user_id = context.user.id
    removed = delete_account(service.db, user_id)
    clear_auth_cookies(response, auth_settings)
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    _audit_auth_event(
        request,
        settings,
        "account.deleted",
        "success",
        user_id=user_id,
        metadata={"rows_removed": sum(removed.values())},
    )


@router.post(
    "/change-password",
    status_code=204,
    summary="Change the password and revoke other sessions",
)
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: AuthService = Depends(get_auth_service),
) -> None:
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="change-password-ip",
        limit=settings.password_change_rate_limit_attempts * 4,
        window_seconds=settings.password_change_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )
    _enforce_auth_rate_limit(
        request,
        settings,
        scope="change-password-user",
        limit=settings.password_change_rate_limit_attempts,
        window_seconds=settings.password_change_rate_limit_window_seconds,
        discriminator=context.user.id,
    )
    try:
        revoked_sessions = service.change_password(
            user=context.user,
            current_session_id=context.session.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
            check_common_password=settings.common_password_check_enabled,
        )
    except AuthServiceError as exc:
        _audit_auth_event(
            request,
            settings,
            "auth.password_change.failed",
            "failure",
            user_id=context.user.id,
            metadata={"reason": exc.code},
        )
        if exc.code == "password_too_common":
            _audit_auth_event(
                request,
                settings,
                "auth.password.common_rejected",
                "failure",
                user_id=context.user.id,
                metadata={"action": "password_change"},
            )
        raise _raise_auth_error(exc) from exc
    RATE_LIMITER.reset(make_rate_limit_key("change-password-user", context.user.id))
    _audit_auth_event(
        request,
        settings,
        "auth.password_change.succeeded",
        "success",
        user_id=context.user.id,
    )
    if revoked_sessions:
        _audit_auth_event(
            request,
            settings,
            "auth.session.mass_revoked",
            "success",
            user_id=context.user.id,
            metadata={"revoked_sessions": revoked_sessions},
        )
