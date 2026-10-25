from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_session
from app.models import User
from app.schemas import Credentials, Token, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def register(body: Credentials, session: AsyncSession = Depends(get_session)) -> UserOut:
    existing = await session.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username is already taken.")

    user = User(username=body.username, password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    return UserOut(id=user.id, username=user.username, is_admin=user.is_admin)


@router.post("/login", response_model=Token)
async def login(body: Credentials, session: AsyncSession = Depends(get_session)) -> Token:
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    return Token(access_token=create_token(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username, is_admin=user.is_admin)
