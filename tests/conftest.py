import os
import tempfile

# Configuration is read at import time, so set the test environment before
# importing the application modules.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="gwsc-tests-"))
os.environ["GWSC_ENV"] = "test"
os.environ["GWSC_SSL_ENABLED"] = "false"
os.environ["GWSC_RELAY_PORT"] = "0"
os.environ["GWSC_CAPTURE_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gwsc_local_termux import auth, config, database  # noqa: E402
from gwsc_local_termux.multiplayer_relay import relay_registry  # noqa: E402
from gwsc_local_termux.routes.events import event_bus  # noqa: E402
from gwsc_local_termux.security import rate_limiter  # noqa: E402
from gwsc_local_termux.server import app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    original_db_path = config.DB_PATH
    original_object_dir = config.OBJECT_STORAGE_DIR
    config.DB_PATH = tmp_path / "test.db"
    config.OBJECT_STORAGE_DIR = tmp_path / "objects"
    database.init_db()
    relay_registry.clear()
    event_bus.reset()
    rate_limiter.reset()
    auth._last_touch.clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        config.DB_PATH = original_db_path
        config.OBJECT_STORAGE_DIR = original_object_dir


@pytest.fixture()
def make_account(client):
    def _make(
        email: str = "player@example.invalid",
        nickname: str = "player",
        password: str = "correct horse battery",
    ):
        response = client.post(
            "/gwsc/api/register",
            json={
                "email": email,
                "nickname": nickname,
                "password": password,
                "client": "launcher",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        payload["password"] = password
        payload["headers"] = {"Authorization": f"Bearer {payload['token']}"}
        return payload

    return _make
