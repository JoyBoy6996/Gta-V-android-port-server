import hashlib
from contextlib import closing

from gwsc_local_termux import config, database
from gwsc_local_termux.security import hash_token, hash_password, verify_password


def test_info_reports_zero_online_when_empty(client):
    response = client.get("/gwsc/api/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["online"] == 0
    assert payload["accounts"] == 0


def test_register_login_and_profile(client, make_account):
    account = make_account(email="one@example.invalid", nickname="alpha")
    assert account["profile"]["rockstarId"] >= 10001
    assert account["profile"]["nickname"] == "alpha"

    profile = client.get("/gwsc/api/me", headers=account["headers"])
    assert profile.status_code == 200
    assert profile.json()["email"] == "one@example.invalid"
    assert "password_hash" not in profile.text

    login = client.post(
        "/gwsc/api/login",
        json={"email": "one@example.invalid", "password": account["password"], "client": "launcher"},
    )
    assert login.status_code == 200
    assert login.json()["profile"]["nickname"] == "alpha"


def test_duplicate_email_and_nickname_rejected(client, make_account):
    make_account(email="dupe@example.invalid", nickname="dupe")
    response = client.post(
        "/gwsc/api/register",
        json={"email": "dupe@example.invalid", "nickname": "other", "password": "password123"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "email_exists"

    response = client.post(
        "/gwsc/api/register",
        json={"email": "other@example.invalid", "nickname": "DUPE", "password": "password123"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "nickname_taken"


def test_invalid_registration_fields(client):
    response = client.post(
        "/gwsc/api/register",
        json={"email": "not-an-email", "nickname": "okname", "password": "password123"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_email"

    response = client.post(
        "/gwsc/api/register",
        json={"email": "ok@example.invalid", "nickname": "okname", "password": "short"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_password"


def test_invalid_login_is_generic(client, make_account):
    make_account(email="login@example.invalid", nickname="loginuser")
    wrong_password = client.post(
        "/gwsc/api/login",
        json={"email": "login@example.invalid", "password": "wrong-password"},
    )
    unknown_account = client.post(
        "/gwsc/api/login",
        json={"email": "missing@example.invalid", "password": "wrong-password"},
    )
    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json()["error"] == unknown_account.json()["error"]
    assert wrong_password.json()["message"] == unknown_account.json()["message"]


def test_missing_and_malformed_authorization(client):
    assert client.get("/gwsc/api/me").status_code == 401
    response = client.get("/gwsc/api/me", headers={"Authorization": "Token abc"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_expired_token_is_rejected(client, make_account):
    account = make_account()
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE tokens SET expires_at = ? WHERE token_hash = ?",
                ("2000-01-01T00:00:00+00:00", hash_token(account["token"])),
            )
    response = client.get("/gwsc/api/me", headers=account["headers"])
    assert response.status_code == 401


def test_revoked_token_is_rejected(client, make_account):
    account = make_account()
    logout = client.post("/gwsc/api/logout", headers=account["headers"])
    assert logout.status_code == 200
    assert client.get("/gwsc/api/me", headers=account["headers"]).status_code == 401


def test_logout_all_revokes_every_session(client, make_account):
    account = make_account()
    second = client.post(
        "/gwsc/api/login",
        json={"email": "player@example.invalid", "password": account["password"]},
    ).json()["token"]
    response = client.post("/gwsc/api/logout-all", headers=account["headers"])
    assert response.status_code == 200
    assert client.get("/gwsc/api/me", headers=account["headers"]).status_code == 401
    assert client.get(
        "/gwsc/api/me", headers={"Authorization": f"Bearer {second}"}
    ).status_code == 401


def test_disabled_account_token_is_rejected(client, make_account):
    account = make_account()
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE users SET status = 'disabled' WHERE email = ?",
                ("player@example.invalid",),
            )
    assert client.get("/gwsc/api/me", headers=account["headers"]).status_code == 401


def test_password_change_requires_current_and_revokes_other_sessions(client, make_account):
    account = make_account(password="old password 123")
    other = client.post(
        "/gwsc/api/login",
        json={"email": "player@example.invalid", "password": "old password 123"},
    ).json()["token"]

    wrong = client.post(
        "/gwsc/api/me/password",
        headers=account["headers"],
        json={"current": "wrong", "next": "new password 123"},
    )
    assert wrong.status_code == 400

    changed = client.post(
        "/gwsc/api/me/password",
        headers=account["headers"],
        json={"current": "old password 123", "next": "new password 123"},
    )
    assert changed.status_code == 200
    # Current session stays valid; the other session is revoked.
    assert client.get("/gwsc/api/me", headers=account["headers"]).status_code == 200
    assert client.get(
        "/gwsc/api/me", headers={"Authorization": f"Bearer {other}"}
    ).status_code == 401
    login = client.post(
        "/gwsc/api/login",
        json={"email": "player@example.invalid", "password": "new password 123"},
    )
    assert login.status_code == 200


def test_login_rate_limit(client, make_account, monkeypatch):
    make_account(email="rate@example.invalid", nickname="rateuser")
    monkeypatch.setattr(config, "LOGIN_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "LOGIN_RATE_WINDOW", 300)
    from gwsc_local_termux.security import rate_limiter

    rate_limiter.reset()
    for _ in range(2):
        response = client.post(
            "/gwsc/api/login",
            json={"email": "rate@example.invalid", "password": "wrong"},
        )
        assert response.status_code == 401
    limited = client.post(
        "/gwsc/api/login",
        json={"email": "rate@example.invalid", "password": "wrong"},
    )
    assert limited.status_code == 429
    assert limited.headers.get("retry-after")


def test_password_hash_roundtrip_and_legacy_format():
    hashed = hash_password("secret-password")
    assert verify_password("secret-password", hashed)
    assert not verify_password("wrong", hashed)

    salt = "00112233445566778899aabbccddeeff"
    digest = hashlib.pbkdf2_hmac("sha256", b"legacy-pass", salt.encode(), 100000).hex()
    legacy = f"{salt}:{digest}"
    assert verify_password("legacy-pass", legacy)
    assert not verify_password("nope", legacy)
