"""LSO SaveMigration stub parity tests.

``TESTED``: source-confirmed response shapes with account-bound authentication,
no raw session key and no fabricated upload/save timestamps.  Android
acceptance remains ``UNVERIFIED``.
"""

from contextlib import closing

import pytest

from gwsc_local_termux import database

LSO = "/gta5/11/gameservices"
METHODS = (
    ("SaveMigration.asmx", "GetSourceAccountsMP"),
    (
        "SaveMigrationSingleplayer.asmx",
        "GetUploadedSingleplayerSaveMetadata",
    ),
)


def _ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "lso-save-migration"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _rockstar_id(account):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT rockstar_id FROM users WHERE rockstar_id = ?",
            (account["profile"]["rockstarId"],),
        ).fetchone()
    assert row is not None
    return row["rockstar_id"]


@pytest.mark.parametrize("service,method", METHODS)
def test_save_migration_requires_authentication(client, service, method):
    response = client.post(f"{LSO}/{service}/{method}", data={"Platform": "PS4"})
    assert response.status_code == 401


@pytest.mark.parametrize("service,method", METHODS)
def test_native_and_root_aliases(client, make_account, service, method):
    account = make_account(
        email=f"save-migration-{method}@example.invalid", nickname=f"migration{method[:6]}"
    )
    ticket = _ticket(client, account)
    for prefix in (
        LSO,
        "/gta5/11/ros",
        "",
    ):
        url = f"{prefix}/{service}/{method}" if prefix else f"/{service}/{method}"
        response = client.post(url, data={"ticket": ticket, "Platform": "PS4"})
        assert response.status_code == 200, (url, response.text)


def test_source_accounts_mp_xml_is_pinned(client, make_account):
    account = make_account(email="source-accounts@example.invalid", nickname="sourceacct")
    response = client.post(
        f"{LSO}/SaveMigration.asmx/GetSourceAccountsMP",
        data={"ticket": _ticket(client, account), "Platform": "PS4"},
    )
    assert response.status_code == 200, response.text
    assert response.text == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Response xmlns="GetSourceAccountsMP">\n'
        "  <Result>\n"
        "    <Accounts />\n"
        "  </Result>\n"
        "  <Status>1</Status>\n"
        "</Response>\n"
    )


def test_singleplayer_metadata_pins_shape_without_secret_or_fake_time(client, make_account):
    account = make_account(
        email="singleplayer-metadata@example.invalid", nickname="singlemeta"
    )
    ticket = _ticket(client, account)
    response = client.post(
        f"{LSO}/SaveMigrationSingleplayer.asmx/GetUploadedSingleplayerSaveMetadata",
        data={"ticket": ticket, "Platform": "PS4"},
    )
    assert response.status_code == 200, response.text
    rockstar_id = _rockstar_id(account)
    assert response.text == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Response xmlns="GetUploadedSingleplayerSaveMetadata">\n'
        "  <SaveMigrationRecordMetadata>\n"
        "    <SaveMigrationRecordToken />\n"
        f"    <SourceRockstarId>{rockstar_id}</SourceRockstarId>\n"
        "    <SourcePlayerAccountId>42069</SourcePlayerAccountId>\n"
        "    <SourcePlatformId>3</SourcePlatformId>\n"
        "    <UploadPosixTime>0</UploadPosixTime>\n"
        "    <CompletionPercentage>0</CompletionPercentage>\n"
        "    <LastCompletedMissionId>0</LastCompletedMissionId>\n"
        "    <SavePosixTime>0</SavePosixTime>\n"
        "  </SaveMigrationRecordMetadata>\n"
        "  <Status>1</Status>\n"
        "</Response>\n"
    )
    # No launcher token, service ticket or session secret may be echoed.
    assert ticket not in response.text
    assert account["token"] not in response.text
