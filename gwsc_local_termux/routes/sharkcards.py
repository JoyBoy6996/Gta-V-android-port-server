import logging
import sqlite3
from contextlib import closing
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header

from .. import config
from ..auth import get_current_user
from ..database import get_db
from ..errors import api_error
from ..time_utils import now_iso

log = logging.getLogger("gwsc.sharkcards")
router = APIRouter(tags=["sharkcards"])

SHARK_CARDS = [
    {"id": "red_shark", "name": "Red Shark Cash Card", "amount": 100000},
    {"id": "tiger_shark", "name": "Tiger Shark Cash Card", "amount": 200000},
    {"id": "bull_shark", "name": "Bull Shark Cash Card", "amount": 500000},
    {"id": "great_white", "name": "Great White Shark Cash Card", "amount": 1250000},
    {"id": "whale_shark", "name": "Whale Shark Cash Card", "amount": 3500000},
    {"id": "megalodon", "name": "Megalodon Shark Cash Card", "amount": 8000000},
]


def _transaction_exists(conn, account_id: int, idempotency_key: Optional[str]):
    if not idempotency_key:
        return None
    return conn.execute(
        "SELECT * FROM transactions WHERE account_id = ? AND idempotency_key = ?",
        (account_id, idempotency_key),
    ).fetchone()


@router.get("/sharkcards")
@router.get("/gwsc/api/sharkcards")
async def get_sharkcards(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT wallet, bank, starter_pack_owned FROM users WHERE id = ?",
            (current_user["id"],),
        ).fetchone()
        claimed = conn.execute(
            "SELECT 1 FROM starter_pack_claims WHERE account_id = ?",
            (current_user["id"],),
        ).fetchone()

    return {
        "wallet": row["wallet"] if row else 0,
        "bank": row["bank"] if row else 0,
        "cards": SHARK_CARDS,
        "claimsEnabled": config.ALLOW_SHARKCARD_CLAIMS,
        "starterPack": {
            "owned": bool(row["starter_pack_owned"]) if row else False,
            "claimed": bool(claimed),
            "amount": config.STARTER_PACK_AMOUNT,
        },
    }


@router.post("/sharkcards/{card_id}")
@router.post("/gwsc/api/sharkcards/{card_id}")
async def claim_sharkcard(
    card_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    # A real-money purchase cannot be verified from a game request.  Keep the
    # route for private development only, behind an explicit opt-in.
    if not config.ALLOW_SHARKCARD_CLAIMS:
        raise api_error(
            403,
            "purchases_disabled",
            "Shark card claims are disabled. Enable GWSC_ALLOW_SHARKCARD_CLAIMS only on a private development server.",
        )

    idempotency_key = (idempotency_key or "").strip() or None
    card = next((item for item in SHARK_CARDS if item["id"] == card_id), None)
    if not card:
        raise api_error(404, "card_not_found", f"Shark Card '{card_id}' not found")

    amount = int(card["amount"])
    now = now_iso()
    with closing(get_db()) as conn:
        existing = _transaction_exists(conn, current_user["id"], idempotency_key)
        if existing:
            bank = conn.execute(
                "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
            ).fetchone()[0]
            return {
                "status": "ok",
                "card": card_id,
                "amount": amount,
                "bank": bank,
                "idempotent": True,
            }

        try:
            with conn:
                row = conn.execute(
                    "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
                ).fetchone()
                if row["bank"] + amount > config.MAX_ECONOMY_AMOUNT:
                    raise api_error(400, "balance_limit", "Bank balance limit reached")
                cursor = conn.execute(
                    """
                    INSERT INTO transactions
                        (account_id, type, item_id, quantity, currency_delta,
                         idempotency_key, state, metadata, created_at, completed_at)
                    VALUES (?, 'sharkcard', ?, 1, ?, ?, 'completed', '{}', ?, ?)
                    """,
                    (
                        current_user["id"],
                        card_id,
                        amount,
                        idempotency_key,
                        now,
                        now,
                    ),
                )
                transaction_id = cursor.lastrowid
                conn.execute(
                    "UPDATE users SET bank = bank + ?, updated_at = ? WHERE id = ?",
                    (amount, now, current_user["id"]),
                )
                new_bank = conn.execute(
                    "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
                ).fetchone()[0]
        except sqlite3.IntegrityError:
            raise api_error(409, "duplicate_transaction", "Transaction already processed")

    log.info(
        "Account %s claimed %s (+%d bank) transaction=%d",
        current_user["id"],
        card_id,
        amount,
        transaction_id,
    )
    return {
        "status": "ok",
        "card": card_id,
        "amount": amount,
        "bank": new_bank,
        "transactionId": transaction_id,
    }


@router.get("/starterpack")
@router.get("/gwsc/api/starterpack")
async def get_starterpack(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT starter_pack_owned, bank FROM users WHERE id = ?",
            (current_user["id"],),
        ).fetchone()
        claim = conn.execute(
            "SELECT claimed_at FROM starter_pack_claims WHERE account_id = ?",
            (current_user["id"],),
        ).fetchone()
    return {
        "owned": bool(row["starter_pack_owned"]) if row else False,
        "claimed": claim is not None,
        "claimedAt": claim["claimed_at"] if claim else None,
        "amount": config.STARTER_PACK_AMOUNT,
        "bank": row["bank"] if row else 0,
    }


@router.post("/starterpack")
@router.post("/gwsc/api/starterpack")
async def claim_starterpack(
    current_user: Dict[str, Any] = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    now = now_iso()
    bonus = int(config.STARTER_PACK_AMOUNT)
    idempotency_key = (idempotency_key or "").strip() or None
    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM starter_pack_claims WHERE account_id = ?",
            (current_user["id"],),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
            ).fetchone()
            return {
                "status": "ok",
                "owned": True,
                "message": "Starter Pack already claimed",
                "bank": row["bank"] if row else 0,
            }

        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO transactions
                        (account_id, type, item_id, quantity, currency_delta,
                         idempotency_key, state, metadata, created_at, completed_at)
                    VALUES (?, 'starter_pack', 'criminal_enterprise', 1, ?,
                            ?, 'completed', '{}', ?, ?)
                    """,
                    (current_user["id"], bonus, idempotency_key, now, now),
                )
                transaction_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO starter_pack_claims (account_id, claimed_at, transaction_id) "
                    "VALUES (?, ?, ?)",
                    (current_user["id"], now, transaction_id),
                )
                conn.execute(
                    """
                    UPDATE users
                    SET starter_pack_owned = 1, bank = bank + ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (bonus, now, current_user["id"]),
                )
                new_bank = conn.execute(
                    "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
                ).fetchone()[0]
        except sqlite3.IntegrityError:
            # A concurrent claim won the race.
            row = conn.execute(
                "SELECT bank FROM users WHERE id = ?", (current_user["id"],)
            ).fetchone()
            return {
                "status": "ok",
                "owned": True,
                "message": "Starter Pack already claimed",
                "bank": row["bank"] if row else 0,
            }

    log.info(
        "Account %s claimed starter pack (+%d bank) transaction=%d",
        current_user["id"],
        bonus,
        transaction_id,
    )
    return {
        "status": "ok",
        "owned": True,
        "amount": bonus,
        "bank": new_bank,
        "transactionId": transaction_id,
    }
