from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from .services.auth_service import (
        extract_bearer_token,
        get_authenticated_user_from_token,
        login_guest_account,
        login_user_account,
        register_user_account,
    )
    from .services.auth_store import delete_session
except ImportError:
    from services.auth_service import (
        extract_bearer_token,
        get_authenticated_user_from_token,
        login_guest_account,
        login_user_account,
        register_user_account,
    )
    from services.auth_store import delete_session


router = APIRouter()


class AuthCredentialsPayload(BaseModel):
    email: str
    password: str


@router.post('/auth/register')
def register_auth_user(payload: AuthCredentialsPayload):
    try:
        registered = register_user_account(payload.email, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        **registered,
    }


@router.post('/auth/login')
def login_auth_user(payload: AuthCredentialsPayload):
    try:
        logged_in = login_user_account(payload.email, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=401, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        **logged_in,
    }


@router.post('/auth/guest')
def login_guest_auth_user():
    try:
        logged_in = login_guest_account()
    except ValueError as error:
        raise HTTPException(status_code=400, detail={'error': str(error)}) from error

    return {
        'status': 'ok',
        **logged_in,
    }


@router.post('/auth/logout')
def logout_auth_user(request: Request):
    token = extract_bearer_token(request.headers.get('authorization'))

    if token:
        delete_session(token)

    return {
        'status': 'ok',
    }


@router.get('/auth/me')
def get_auth_me(request: Request):
    auth = get_authenticated_user_from_token(
        extract_bearer_token(request.headers.get('authorization'))
    )

    if not auth:
        raise HTTPException(status_code=401, detail={'error': 'Not authenticated.'})

    return {
        'status': 'ok',
        'user': auth['user'],
        'session': auth['session'],
    }
