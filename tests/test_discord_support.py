"""Discord support groundwork: inert config, count data and redaction."""

from gwsc_local_termux import config
from gwsc_local_termux.logging_utils import redact_text


def test_discord_config_is_inert_and_keeps_secrets_out_of_fastapi():
    # SOURCE_CONFIRMED: the upstream bot's only credential is DISCORD_TOKEN.
    # It belongs to the external process; FastAPI must not read or expose it.
    assert not hasattr(config, "DISCORD_TOKEN")
    assert config.DISCORD_ENABLED is False
    assert config.DISCORD_GUILD_ID == ""
    assert config.DISCORD_PLAYER_WINDOW_SECONDS == 600
    assert config.DISCORD_STATUS_INTERVAL_SECONDS == 60


def test_log_redaction_covers_discord_credentials():
    text = (
        "DISCORD_TOKEN=supersecret-token "
        "discord bot token: another-secret "
        "Authorization: Bearer gwsc_abcdefghijklmnopqrstuvwxyz"
    )
    redacted = redact_text(text)
    assert "supersecret-token" not in redacted
    assert "another-secret" not in redacted
    assert "gwsc_abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "DISCORD_TOKEN=" in redacted


def test_health_endpoints_have_no_discord_credential_fields(client):
    for path in ("/health", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert not any("token" in key.lower() for key in payload)
