"""Reset one account's two-step verification from the server.

    python -m app.reset_two_step <email>

For an administrator who has lost their phone, passkeys and recovery codes
when no other administrator is left to reset them from the Users page. Run it
where the backend runs, for example:

    docker compose exec backend python -m app.reset_two_step admin@example.com

It removes the account's authenticator app, passkeys and recovery codes, and
records that it did. The password is not changed.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app.core.audit import record as audit_record
from app.models.user import User
from app.services.two_step.reset import reset


async def reset_by_email(session_factory, email: str) -> tuple[bool, str]:
    """Reset the account with *email*. Returns whether it was found, and what
    to tell the person at the keyboard."""
    wanted = email.strip().lower()
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == wanted))
        ).scalar_one_or_none()
        if user is None:
            return False, f"No account has the email {email.strip()}."
        was_on = await reset(db, user.id)
        await audit_record(
            action="two_step_reset_from_server",
            target_type="user",
            target_id=user.id,
            metadata={"was_on": was_on},
            db=db,
        )
        await db.commit()
    if was_on:
        return True, f"Two-step verification is reset for {user.email}. They can sign in with their password."
    return True, f"{user.email} had no two-step verification set up. Nothing else was changed."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].strip() or args[0].startswith("-"):
        print("usage: python -m app.reset_two_step <email>", file=sys.stderr)
        return 2
    from app.database import async_session

    found, message = asyncio.run(reset_by_email(async_session, args[0]))
    print(message, file=sys.stdout if found else sys.stderr)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
