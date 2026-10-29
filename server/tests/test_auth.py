"""Accounts: sign-up, log-in, sessions, and the app locked behind them."""

from __future__ import annotations

import sqlite3
import time

import pytest
from app import main
from app.auth import AuthError, AuthStore, check_password, hash_password
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

PASSWORD = "correct-horse-battery"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> AuthStore:
    s = AuthStore(":memory:")
    monkeypatch.setattr(main, "_auth", s)
    return s


def signup(client: TestClient, email: str = "Asha@Example.com", password: str = PASSWORD):
    return client.post("/api/auth/signup", json={"name": "Asha", "email": email, "password": password})


def test_signup_logs_you_in_with_a_protected_cookie(store) -> None:
    with TestClient(main.app) as client:
        r = signup(client)
        assert r.status_code == 201 and r.json()["user"] == {"id": 1, "name": "Asha", "email": "asha@example.com"}
        cookie = r.headers["set-cookie"].lower()
        assert "routevoice_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/auth/me").json()["user"]["email"] == "asha@example.com"


def test_login_logout_and_wrong_passwords(store) -> None:
    with TestClient(main.app) as client:
        signup(client)
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401
        bad = client.post("/api/auth/login", json={"email": "asha@example.com", "password": "nope-nope"})
        assert bad.status_code == 401 and bad.json()["detail"]["code"] == "invalid_credentials"
        unknown = client.post("/api/auth/login", json={"email": "who@example.com", "password": PASSWORD})
        assert unknown.status_code == 401 and unknown.json()["detail"] == bad.json()["detail"]  # no account probing
        ok = client.post("/api/auth/login", json={"email": "  ASHA@example.com ", "password": PASSWORD})
        assert ok.status_code == 200 and client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("body, code", [
    ({"name": "", "email": "a@b.co", "password": PASSWORD}, "invalid_name"),
    ({"name": "A", "email": "not-an-email", "password": PASSWORD}, "invalid_email"),
    ({"name": "A", "email": "a@b.co", "password": "short"}, "weak_password"),
])
def test_signup_validation(store, body, code) -> None:
    with TestClient(main.app) as client:
        r = client.post("/api/auth/signup", json=body)
        assert r.status_code == 400 and r.json()["detail"]["code"] == code


def test_an_email_can_only_sign_up_once(store) -> None:
    with TestClient(main.app) as client:
        signup(client)
        again = signup(client, email="asha@EXAMPLE.com")
        assert again.status_code == 409 and again.json()["detail"]["code"] == "email_taken"


def test_repeated_wrong_passwords_are_throttled(store) -> None:
    store.create_user("Asha", "asha@example.com", PASSWORD)
    for _ in range(5):
        with pytest.raises(AuthError, match="Wrong email or password"):
            store.authenticate("asha@example.com", "guess-guess")
    with pytest.raises(AuthError) as err:
        store.authenticate("asha@example.com", PASSWORD)  # even the right one, until the window passes
    assert err.value.code == "too_many_attempts"


def test_passwords_and_sessions_are_never_stored_in_the_clear(store) -> None:
    user = store.create_user("Asha", "asha@example.com", PASSWORD)
    token = store.start_session(user)
    rows = store._db.execute("SELECT password_hash FROM users").fetchall() + store._db.execute(
        "SELECT token_hash FROM sessions").fetchall()
    dumped = " ".join(r[0] for r in rows)
    assert PASSWORD not in dumped and token not in dumped
    assert rows[0][0].startswith("scrypt$") and check_password(PASSWORD, rows[0][0])
    assert not check_password("wrong", rows[0][0]) and hash_password(PASSWORD) != hash_password(PASSWORD)


def test_expired_and_ended_sessions_stop_working(store) -> None:
    user = store.create_user("Asha", "asha@example.com", PASSWORD)
    token = store.start_session(user)
    assert store.user_for(token) == user
    store._db.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
    assert store.user_for(token) is None
    token = store.start_session(user)
    store.end_session(token)
    assert store.user_for(token) is None and store.user_for("forged-token") is None


def test_the_app_is_locked_until_you_log_in(store) -> None:
    with TestClient(main.app) as anon:
        assert anon.get("/api/health").status_code == 200  # public
        with anon.websocket_connect("/ws/drive") as ws:
            assert ws.receive_json()["code"] == "auth_required"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4401
    with TestClient(main.app) as client:
        signup(client)
        with client.websocket_connect("/ws/drive") as ws:
            assert ws.receive_json()["t"] == "ready"


def test_auth_can_be_switched_off_for_local_development(store, monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", main.settings.__class__(auth_required=False))
    with TestClient(main.app) as anon:
        with anon.websocket_connect("/ws/drive") as ws:
            assert ws.receive_json()["t"] == "ready"


def test_the_database_file_is_created_where_configured(tmp_path, monkeypatch) -> None:
    path = tmp_path / "nested" / "users.sqlite3"
    monkeypatch.setenv("AUTH_DB_PATH", str(path))
    AuthStore().create_user("Asha", "asha@example.com", PASSWORD)
    assert sqlite3.connect(path).execute("SELECT email FROM users").fetchone() == ("asha@example.com",)
