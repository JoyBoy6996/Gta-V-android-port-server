import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, status
from ..database import get_db
from ..auth import get_current_user

log = logging.getLogger("gwsc.sharkcards")
router = APIRouter(tags=["sharkcards"])

SHARK_CARDS = [
    {"id": "red_shark", "name": "Red Shark Cash Card", "amount": 100000},
    {"id": "tiger_shark", "name": "Tiger Shark Cash Card", "amount": 200000},
    {"id": "bull_shark", "name": "Bull Shark Cash Card", "amount": 500000},
    {"id": "great_white", "name": "Great White Shark Cash Card", "amount": 1250000},
    {"id": "whale_shark", "name": "Whale Shark Cash Card", "amount": 3500000},
    {"id": "megalodon", "name": "Megalodon Shark Cash Card", "amount": 8000000}
]

@router.get("/sharkcards")
@router.get("/gwsc/api/sharkcards")
async def get_sharkcards(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bank, starter_pack_owned FROM users WHERE id = ?", (current_user["id"],))
    row = cursor.fetchone()
    bank = row["bank"] if row else 0
    starter_owned = bool(row["starter_pack_owned"]) if row else False
    conn.close()

    return {
        "bank": bank,
        "cards": SHARK_CARDS,
        "starterPack": {
            "owned": starter_owned,
            "amount": 1000000
        }
    }

@router.post("/sharkcards/{card_id}")
@router.post("/gwsc/api/sharkcards/{card_id}")
async def claim_sharkcard(card_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    card = next((c for c in SHARK_CARDS if c["id"] == card_id), None)
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "card_not_found", "message": f"Shark Card '{card_id}' not found"}
        )

    amount = card["amount"]
    conn = get_db()
    with conn:
        conn.execute("UPDATE users SET bank = bank + ? WHERE id = ?", (amount, current_user["id"]))
        cursor = conn.cursor()
        cursor.execute("SELECT bank FROM users WHERE id = ?", (current_user["id"],))
        new_bank = cursor.fetchone()[0]
    conn.close()

    log.info("User %s claimed %s (+$%d, new bank: $%d)", current_user["nickname"], card["name"], amount, new_bank)
    return {
        "status": "ok",
        "card": card_id,
        "amount": amount,
        "bank": new_bank
    }

@router.post("/starterpack")
@router.post("/gwsc/api/starterpack")
async def claim_starterpack(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT starter_pack_owned, bank FROM users WHERE id = ?", (current_user["id"],))
    row = cursor.fetchone()
    if row and row["starter_pack_owned"]:
        conn.close()
        return {"status": "ok", "message": "Starter Pack already owned", "bank": row["bank"]}

    bonus = 1000000
    with conn:
        conn.execute("""
            UPDATE users
            SET starter_pack_owned = 1, bank = bank + ?
            WHERE id = ?
        """, (bonus, current_user["id"]))
        cursor.execute("SELECT bank FROM users WHERE id = ?", (current_user["id"],))
        new_bank = cursor.fetchone()[0]
    conn.close()

    log.info("User %s claimed Criminal Enterprise Starter Pack (+$%d, new bank: $%d)", current_user["nickname"], bonus, new_bank)
    return {
        "status": "ok",
        "owned": True,
        "amount": bonus,
        "bank": new_bank
    }
