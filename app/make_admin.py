import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal, engine, init_db
from app.models import User


async def make_admin(username: str, admin: bool) -> None:
    await init_db()
    try:
        async with SessionLocal() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                print(f"User '{username}' not found.")
                return
            user.is_admin = admin
            await session.commit()
            print(f"User '{username}' is {'now' if admin else 'no longer'} an admin.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--remove"):
        print("Usage: python -m app.make_admin <username> [--remove]")
        sys.exit(1)
    asyncio.run(make_admin(sys.argv[1], admin=len(sys.argv) == 2))
