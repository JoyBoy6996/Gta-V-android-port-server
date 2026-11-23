"""LSO UGC content-lifecycle parity tranche.

``TESTED``: create, update, publish, soft delete and query behavior, including
source-confirmed XML namespaces, the ``&data=`` binary frame format, account
bound ownership, static heist triggers and object-storage keys.  Android
acceptance of these responses remains ``UNVERIFIED``.
"""

import json
import struct
import urllib.parse
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import config, database, object_storage
from gwsc_local_termux.routes.lso_ugc import SET_DELETED_XML
from gwsc_local_termux.time_utils import now_iso

LSO = "/gta5/11/gameservices/ugc.asmx"
NATIVE = "/gta5/11/ros/ugc.asmx"
ROOT = "/ugc.asmx"


def _ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "lso-ugc"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _account_id(account):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE rockstar_id = ?",
            (account["profile"]["rockstarId"],),
        ).fetchone()
    assert row is not None
    return row["id"]


def _binary_body(
    params,
    *,
    files=(),
    json_field="paramsJson",
    content_type="gta5mission",
    ticket=None,
    **extra,
):
    fields = {
        "contentType": content_type,
        json_field: json.dumps(params, separators=(",", ":")),
    }
    if ticket is not None:
        fields["ticket"] = ticket
    fields.update(extra)
    chunks = [urllib.parse.urlencode(fields).encode("utf-8"), b"&data="]
    for file_id, payload in files:
        chunks.append(struct.pack(">ii", len(payload), file_id))
        chunks.append(payload)
    return b"".join(chunks)


def _post_binary(client, method, body, prefix=LSO):
    return client.post(
        f"{prefix}/{method}",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def _form_post(client, method, values, prefix=LSO):
    return client.post(f"{prefix}/{method}", data=values)


def _root(response):
    return ET.fromstring(response.text)


def _ns(root):
    return root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""


def _status(root):
    return root.findtext("./{*}Status")


def _result(root):
    return root.find("./{*}Result")


def _ugc_row(content_id):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()
    assert row is not None
    return row


def _seed_ugc(account, content_id, *, root_id=None, published=1, category="gta5mission"):
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO ugc_contents
                    (content_id, root_content_id, category, name, created_date,
                     account_id, rockstar_id, user_id, is_published, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    content_id,
                    root_id or content_id,
                    category,
                    f"Seeded {content_id}",
                    now_iso(),
                    _account_id(account),
                    account["profile"]["rockstarId"],
                    _account_id(account),
                    published,
                ),
            )


def _params(name="Test Mission", *, publish=False, data=None):
    params = {
        "ContentName": name,
        "Description": "Created by the parity suite",
        "DataJson": data if data is not None else '{"seed":true}',
        "Language": "en",
        "Publish": publish,
    }
    return params


# ---------------------------------------------------------------------------
# CreateContent
# ---------------------------------------------------------------------------
def test_create_mission_persists_account_and_binary(client, make_account):
    account = make_account(email="ugc-create@example.invalid", nickname="ugccreate")
    ticket = _ticket(client, account)
    body = _binary_body(
        _params("Mission One", publish=True),
        files=[(1, b"mission-bytes")],
        ticket=ticket,
    )
    response = _post_binary(client, "CreateContent", body)
    assert response.status_code == 200, response.text

    root = _root(response)
    assert _ns(root) == "CreateContentResponse"
    assert _status(root) == "1"
    result = _result(root)
    content_id = result.attrib["ci"]
    assert len(content_id) == 22
    assert result.attrib["rci"] == content_id
    assert result.attrib["f0"] == "1"
    assert result.findtext("{*}da") == '{"seed":true}'

    row = _ugc_row(content_id)
    assert row["account_id"] == _account_id(account)
    assert row["user_id"] == _account_id(account)
    assert row["rockstar_id"] == account["profile"]["rockstarId"]
    assert row["username"] == account["profile"]["nickname"]
    assert row["is_published"] == 1
    assert row["category"] == "gta5mission"

    stored = object_storage.get_object_store(profile="ugc")
    assert stored.get(("ugc", f"{content_id}_00.ugc")) == b"mission-bytes"


def test_create_photo_uses_configured_cdn_metadata(monkeypatch, client, make_account):
    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-photo-secret", raising=False)
    monkeypatch.setattr(config, "UGC_PUBLIC_BASE_URL", "https://cdn.example.invalid", raising=False)
    account = make_account(email="ugc-photo@example.invalid", nickname="ugcphoto")
    ticket = _ticket(client, account)
    body = _binary_body(
        _params("Photo One", data='{"original":"kept"}'),
        files=[(7, b"\xff\xd8\xff\xd9")],
        content_type="gta5photo",
        ticket=ticket,
    )
    response = _post_binary(client, "CreateContent", body)
    assert response.status_code == 200, response.text

    root = _root(response)
    assert _ns(root) == "CreateContent"
    assert _status(root) == "1"
    result = _result(root)
    content_id = result.attrib["ci"]
    assert result.attrib["f1"] == "-1"
    assert result.attrib["f2"] == "-1"
    metadata = json.loads(result.findtext("{*}da"))
    assert metadata["original"] == "kept"
    assert metadata["cdndir"] == f"/ugc/gta5photo/{content_id}/"
    assert metadata["cdnfn"] == "0_0.jpg"
    assert metadata["cdnurl"] == "https://cdn.example.invalid"
    assert metadata["cdnat"].startswith("__token__=")
    assert _ugc_row(content_id)["category"] == "gta5photo"


def test_create_rejects_unsupported_type_and_missing_credential(client, make_account):
    account = make_account(email="ugc-type@example.invalid", nickname="ugctype")
    ticket = _ticket(client, account)
    unsupported = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params(), content_type="not-a-type", ticket=ticket),
    )
    assert unsupported.status_code == 400
    assert unsupported.text == "Unsupported content type"

    anonymous = _post_binary(client, "CreateContent", _binary_body(_params()))
    assert anonymous.status_code == 401
    assert anonymous.json() == {"message": "Unauthorized"}


# ---------------------------------------------------------------------------
# UpdateContent
# ---------------------------------------------------------------------------
def test_update_enforces_ownership_bumps_version_and_writes_files(client, make_account):
    owner = make_account(email="ugc-update-owner@example.invalid", nickname="ugcowner")
    other = make_account(email="ugc-update-other@example.invalid", nickname="ugcother")
    owner_ticket = _ticket(client, owner)
    other_ticket = _ticket(client, other)

    created = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params("Original"), files=[(1, b"first")], ticket=owner_ticket),
    )
    content_id = _result(_root(created)).attrib["ci"]

    forbidden = _post_binary(
        client,
        "UpdateContent",
        _binary_body(
            {"ContentName": "Stolen"},
            files=[],
            json_field="updateJson",
            contentId=content_id,
            ticket=other_ticket,
        ),
    )
    assert forbidden.status_code == 403
    assert forbidden.json() == {"message": "Forbidden - you don't own this content"}
    assert _ugc_row(content_id)["name"] == "Original"

    updated = _post_binary(
        client,
        "UpdateContent",
        _binary_body(
            {"ContentName": "Revised", "Publish": True},
            files=[(9, b"second")],
            json_field="updateJson",
            contentId=content_id,
            ticket=owner_ticket,
        ),
    )
    assert updated.status_code == 200, updated.text
    root = _root(updated)
    assert _ns(root) == "UpdateContentResponse"
    assert _status(root) == "1"
    result = _result(root)
    assert result.findtext("{*}ci") == content_id
    assert result.findtext("{*}n") == "Revised"
    assert result.find("{*}pd") is not None

    row = _ugc_row(content_id)
    assert row["version"] == 2
    assert row["name"] == "Revised"
    assert row["is_published"] == 1
    assert row["file_version0"] == "9"
    assert (
        object_storage.get_object_store(profile="ugc").get(
            ("ugc", f"{content_id}_00.ugc")
        )
        == b"second"
    )


def test_update_unknown_content_returns_404(client, make_account):
    account = make_account(email="ugc-update-404@example.invalid", nickname="ugc404")
    response = _post_binary(
        client,
        "UpdateContent",
        _binary_body(
            {"ContentName": "Nope"},
            json_field="updateJson",
            contentId="MissingContentId1234",
            ticket=_ticket(client, account),
        ),
    )
    assert response.status_code == 404
    assert response.json() == {"message": "Content not found"}


# ---------------------------------------------------------------------------
# Publish and SetDeleted
# ---------------------------------------------------------------------------
def test_publish_unpublishes_siblings_and_returns_source_shape(client, make_account):
    account = make_account(email="ugc-publish@example.invalid", nickname="ugcpublish")
    ticket = _ticket(client, account)
    created = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params("First Version"), ticket=ticket),
    )
    content_id = _result(_root(created)).attrib["ci"]
    root_id = _ugc_row(content_id)["root_content_id"]
    _seed_ugc(account, "SiblingVersion0001", root_id=root_id, published=1)

    response = _form_post(
        client,
        "Publish",
        {"ticket": ticket, "contentId": content_id},
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "PublishResponse"
    assert _status(root) == "1"
    result = _result(root)
    assert result.findtext("{*}ci") == content_id
    assert result.findtext("{*}rci") == root_id
    assert result.findtext("{*}n") == "First Version"
    assert int(result.findtext("{*}pd")) > 0

    assert _ugc_row(content_id)["is_published"] == 1
    assert _ugc_row("SiblingVersion0001")["is_published"] == 0


def test_publish_rejects_non_owner(client, make_account):
    owner = make_account(email="ugc-pub-owner@example.invalid", nickname="ugcpubowner")
    other = make_account(email="ugc-pub-other@example.invalid", nickname="ugcpubother")
    ticket = _ticket(client, owner)
    created = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params(), ticket=ticket),
    )
    content_id = _result(_root(created)).attrib["ci"]

    response = _form_post(
        client,
        "Publish",
        {"ticket": _ticket(client, other), "contentId": content_id},
    )
    assert response.status_code == 403
    assert _ugc_row(content_id)["is_published"] == 0


def test_set_deleted_is_owner_only_soft_unpublish(client, make_account):
    owner = make_account(email="ugc-delete-owner@example.invalid", nickname="ugcdelowner")
    other = make_account(email="ugc-delete-other@example.invalid", nickname="ugcdelother")
    ticket = _ticket(client, owner)
    created = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params(publish=True), ticket=ticket),
    )
    content_id = _result(_root(created)).attrib["ci"]

    forbidden = _form_post(
        client,
        "SetDeleted",
        {"ticket": _ticket(client, other), "contentId": content_id},
    )
    assert forbidden.status_code == 403
    assert _ugc_row(content_id)["is_published"] == 1

    deleted = _form_post(
        client, "SetDeleted", {"ticket": ticket, "contentId": content_id}
    )
    assert deleted.status_code == 200
    assert deleted.text == SET_DELETED_XML
    row = _ugc_row(content_id)
    assert row["is_published"] == 0
    assert row["published_date"] is None

    missing = _form_post(
        client,
        "SetDeleted",
        {"ticket": ticket, "contentId": "MissingContentId1234"},
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# QueryContent
# ---------------------------------------------------------------------------
def test_query_my_content_scopes_to_caller_and_publish_flag(client, make_account):
    owner = make_account(email="ugc-query-owner@example.invalid", nickname="ugcqowner")
    other = make_account(email="ugc-query-other@example.invalid", nickname="ugcqother")
    ticket = _ticket(client, owner)

    def create(label, publish):
        response = _post_binary(
            client,
            "CreateContent",
            _binary_body(_params(label, publish=publish), ticket=ticket),
        )
        return _result(_root(response)).attrib["ci"]

    published_id = create("Mine Published", True)
    draft_id = create("Mine Draft", False)
    _seed_ugc(other, "OtherPublished1234", published=1)

    all_mine = _form_post(
        client,
        "QueryContent",
        {
            "ticket": ticket,
            "queryName": "GetMyContent",
            "queryParams": "",
        },
    )
    assert all_mine.status_code == 200, all_mine.text
    result = _result(_root(all_mine))
    ids = {item.attrib["c"] for item in result.findall("{*}r")}
    assert ids == {published_id, draft_id}
    assert result.attrib["Count"] == "2"
    assert result.attrib["Total"] == "2"

    published_only = _form_post(
        client,
        "QueryContent",
        {
            "ticket": ticket,
            "queryName": "GetMyContent",
            "queryParams": json.dumps({"published": True}),
        },
    )
    published_result = _result(_root(published_only))
    assert {item.attrib["c"] for item in published_result.findall("{*}r")} == {
        published_id
    }


def test_public_query_hides_unpublished_and_rejects_bad_ticket(client, make_account):
    account = make_account(email="ugc-query-public@example.invalid", nickname="ugcqpublic")
    ticket = _ticket(client, account)

    def create(publish):
        response = _post_binary(
            client,
            "CreateContent",
            _binary_body(_params(publish=publish), ticket=ticket),
        )
        return _result(_root(response)).attrib["ci"]

    visible_id = create(True)
    hidden_id = create(False)

    response = _form_post(
        client,
        "QueryContent",
        {
            "queryName": "GetContentByContentId",
            "queryParams": json.dumps({"contentids": [visible_id, hidden_id]}),
            "contentType": "gta5mission",
        },
    )
    assert response.status_code == 200, response.text
    result = _result(_root(response))
    assert {item.attrib["c"] for item in result.findall("{*}r")} == {visible_id}

    bad_ticket = _form_post(
        client,
        "QueryContent",
        {
            "ticket": "not-a-real-ticket",
            "queryName": "GetContentByContentId",
            "queryParams": json.dumps({"contentids": [visible_id]}),
        },
    )
    assert bad_ticket.status_code == 401


def test_bookmarked_query_returns_only_bookmarked_content(client, make_account):
    account = make_account(email="ugc-bookmark@example.invalid", nickname="ugcbookmark")
    ticket = _ticket(client, account)
    created = _post_binary(
        client,
        "CreateContent",
        _binary_body(_params("Bookmarked"), ticket=ticket),
    )
    content_id = _result(_root(created)).attrib["ci"]
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "INSERT INTO ugc_mission_bookmarks (user_id, content_id, created_at) "
                "VALUES (?, ?, ?)",
                (_account_id(account), content_id, now_iso()),
            )

    response = _form_post(
        client,
        "QueryContent",
        {
            "ticket": ticket,
            "queryName": "GetMyBookmarkedContent",
            "queryParams": "",
        },
    )
    assert response.status_code == 200, response.text
    result = _result(_root(response))
    assert [item.attrib["c"] for item in result.findall("{*}r")] == [content_id]
    assert result.attrib["Total"] == "1"


@pytest.mark.parametrize(
    "trigger,expected_count",
    [("QS6WYcjJFk2YxqYDMN8mjQ", "12"), ("hK5OgJk1BkinXGGXghhTMg", "17")],
)
def test_heist_triggers_return_byte_exact_static_xml(
    client, make_account, trigger, expected_count
):
    account = make_account(
        email=f"ugc-heist-{expected_count}@example.invalid",
        nickname=f"ugcheist{expected_count}",
    )
    response = _form_post(
        client,
        "QueryContent",
        {
            "ticket": _ticket(client, account),
            "queryName": "GetContentByContentId",
            "queryParams": json.dumps({"contentids": [trigger]}),
        },
    )
    assert response.status_code == 200, response.text
    result = _result(_root(response))
    assert result.attrib["Count"] == expected_count
    assert result.attrib["Total"] == expected_count


def test_unknown_query_returns_status_zero(client):
    response = _form_post(client, "QueryContent", {"queryName": "NoSuchQuery"})
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "QueryContent"
    assert _status(root) == "0"
    assert _result(root).attrib == {"Count": "0", "Total": "0", "Hash": "0"}


# ---------------------------------------------------------------------------
# Validation, aliases and body limits
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        b"ticket=abc",
        urllib.parse.urlencode({"ticket": "abc", "paramsJson": "{}"}).encode()
        + b"&data="
        + b"\x00\x01",
        urllib.parse.urlencode({"ticket": "abc", "paramsJson": "{}"}).encode()
        + b"&data="
        + struct.pack(">ii", 10_000_000, 1),
        urllib.parse.urlencode({"ticket": "abc", "paramsJson": "{}"}).encode()
        + b"&data="
        + b"".join(struct.pack(">ii", 1, i) + b"x" for i in range(4)),
        urllib.parse.urlencode({"ticket": "abc", "paramsJson": "not-json"}).encode()
        + b"&data=",
    ],
)
def test_create_rejects_invalid_binary_or_json(client, body):
    response = _post_binary(client, "CreateContent", body)
    assert response.status_code == 400


def test_native_and_root_aliases_reach_the_same_handler(client, make_account):
    account = make_account(email="ugc-alias@example.invalid", nickname="ugcalias")
    ticket = _ticket(client, account)
    body = _binary_body(_params("Alias Mission"), ticket=ticket)
    for prefix in (LSO, NATIVE, ROOT):
        response = _post_binary(client, "CreateContent", body, prefix=prefix)
        assert response.status_code == 200, response.text
        assert _status(_root(response)) == "1"


def test_ugc_create_uses_object_body_limit(monkeypatch, client, make_account):
    account = make_account(email="ugc-limit@example.invalid", nickname="ugclimit")
    ticket = _ticket(client, account)
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 64)
    monkeypatch.setattr(config, "OBJECT_MAX_BYTES", 4096)
    body = _binary_body(
        _params("Large Mission"), files=[(1, b"x" * 200)], ticket=ticket
    )
    assert len(body) > 64

    accepted = _post_binary(client, "CreateContent", body)
    assert accepted.status_code == 200, accepted.text

    rejected = client.post("/gwsc/api/register", content=b"x" * 200)
    assert rejected.status_code == 413
