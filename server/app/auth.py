"""Accounts for the web app: email + password, with a server-side session.

- Passwords are never stored: each is hashed with scrypt (stdlib ``hashlib``)
  under its own random salt, and checked in constant time.
- Logging in creates a random session token; the browser holds it in an
  HttpOnly cookie (script cannot read it) and the database holds only its
  SHA-256, so a leaked database does not leak live sessions.
- Repeated wrong passwords for one email are throttled.

Storage is one SQLite file (``AUTH_DB_PATH``, default ``server/data/users.sqlite3``,
git-ignored). Everything runs locally - no external service, no key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COOKIE_NAME = "routevoice_session"
SESSION_TTL_S = 7 * 24 * 3600
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_FAILURES, _FAILURE_WINDOW_S = 5, 300

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "users.sqlite3"


class AuthError(ValueError):
    """A request the user can fix; ``code`` is stable for the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "email": self.email}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Used when the email is unknown, so a miss costs the same time as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class AuthStore:
    def __init__(self, path: str | Path | None = None) -> None:
        path = str(path or os.environ.get("AUTH_DB_PATH") or DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                """
            )

    # -- accounts --------------------------------------------------------------
    def create_user(self, name: str, email: str, password: str) -> User:
        name, email = (name or "").strip(), (email or "").strip().lower()
        if not 1 <= len(name) <= 60:
            raise AuthError("invalid_name", "Please enter your name.")
        if not _EMAIL.match(email) or len(email) > 254:
            raise AuthError("invalid_email", "Please enter a valid email address.")
        if not 8 <= len(password or "") <= 128:
            raise AuthError("weak_password", "Use a password of at least 8 characters.")
        try:
            with self._lock, self._db:
                cur = self._db.execute(
                    "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (name, email, hash_password(password), time.time()),
                )
        except sqlite3.IntegrityError:
            raise AuthError("email_taken", "An account with this email already exists. Log in instead.") from None
        return User(int(cur.lastrowid), name, email)

    def authenticate(self, email: str, password: str) -> User:
        email = (email or "").strip().lower()
        now = time.time()
        recent = [t for t in self._failures.get(email, []) if now - t < _FAILURE_WINDOW_S]
        if len(recent) >= _MAX_FAILURES:
            raise AuthError("too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
        with self._lock:
            row = self._db.execute(
                "SELECT id, name, email, password_hash FROM users WHERE email = ?", (email,)
            ).fetchone()
        if not check_password(password or "", row[3] if row else _DUMMY_HASH) or row is None:
            self._failures[email] = recent + [now]
            raise AuthError("invalid_credentials", "Wrong email or password.")
        self._failures.pop(email, None)
        return User(int(row[0]), row[1], row[2])

    # -- sessions --------------------------------------------------------------
    def start_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            self._db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (_token_hash(token), user.id, time.time() + SESSION_TTL_S),
            )
        return token

    def user_for(self, token: str | None) -> User | None:
        if not token:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT u.id, u.name, u.email, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
        if row is None or row[3] < time.time():
            return None
        return User(int(row[0]), row[1], row[2])

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self._db:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
