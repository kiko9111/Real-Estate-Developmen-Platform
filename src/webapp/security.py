"""Passwords, sessions, roles and CSRF protection."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


MIN_PASSWORD = 10


def check_password_rules(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


# ---------------------------------------------------------------- csrf

def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    """Every state-changing form carries the session's token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    form = await request.form()
    sent = form.get("csrf_token") or request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(str(sent), request.session.get("csrf", "")):
        raise HTTPException(status_code=403, detail="The form expired. Go back, reload the page and try again.")


# ---------------------------------------------------------------- roles

class NotLoggedIn(Exception):
    pass


def require_editor(user) -> None:
    if not user.can_edit:
        raise HTTPException(status_code=403, detail="Your account can view but not change data.")


def require_admin(user) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can do this.")
