from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import uuid
import asyncio

router = APIRouter(prefix="/wallet", tags=["wallet"])

# Max time to wait for MongoDB before returning a clear 503 to the app
DB_TIMEOUT = 8.0


class AddFundsRequest(BaseModel):
    amount: float
    payment_method_id: Optional[str] = None

class SavePaymentMethodRequest(BaseModel):
    user_id: Optional[str] = None
    type: str = "card"  # card, apple_pay, google_pay
    last_four: str = "4242"
    brand: Optional[str] = "Visa"  # Visa, Mastercard, Amex, Discover
    cardholder_name: Optional[str] = None
    is_default: bool = True

class PaymentMethod(BaseModel):
    id: str
    type: str
    last_four: str
    brand: Optional[str] = None
    is_default: bool = False

class Transaction(BaseModel):
    id: str
    user_id: str
    type: str  # credit_purchase, video_reading, live_chat, tip, refund
    amount: float
    description: str
    status: str  # pending, completed, failed
    created_at: datetime

class WalletResponse(BaseModel):
    balance: float
    free_credits: float = 0.0
    total_available: float
    is_new_user: bool = True
    first_reading_free_used: bool = False
    free_minutes_available: int = 0  # From loyalty program
    payment_methods: List[PaymentMethod]


async def _db(coro):
    """Run a MongoDB operation with a timeout; surface DB outages as 503."""
    try:
        return await asyncio.wait_for(coro, timeout=DB_TIMEOUT)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {type(e).__name__}")


def _serialize_tx(t: dict) -> dict:
    t["_id"] = str(t.get("_id", t.get("id", "")))
    for date_key in ["created_at", "updated_at", "ended_at", "started_at"]:
        val = t.get(date_key)
        if isinstance(val, datetime):
            t[date_key] = val.isoformat()
    if "amount" in t and t["amount"] is not None:
        try:
            t["amount"] = float(t["amount"])
        except Exception:
            pass
    if "duration_minutes" in t and t["duration_minutes"] is not None:
        try:
            t["duration_minutes"] = float(t["duration_minutes"])
        except Exception:
            pass
    return t


def create_wallet_routes(db: AsyncIOMotorDatabase):

    async def _get_user_or_404(user_id: str) -> dict:
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        from pymongo import ReturnDocument
        default_fcm = f"fcm_{user_id}_{uuid.uuid4().hex[:16]}"
        user = await _db(db.users.find_one_and_update(
            {"id": user_id},
            {
                "$setOnInsert": {
                    "id": user_id,
                    "email": f"{user_id}@sixthsense.com",
                    "name": "Client User",
                    "balance": 0.0,
                    "free_credits": 0.0,
                    "is_new_user": False,
                    "first_reading_free_used": True,
                    "free_minutes_available": 0,
                    "saved_payment_methods": [],
                    "fcm_token": default_fcm,
                    "fcm_platform": "android",
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        ))
        if user and not user.get("fcm_token"):
            await _db(db.users.update_one(
                {"id": user_id},
                {"$set": {"fcm_token": default_fcm, "fcm_platform": "android", "fcm_updated_at": datetime.utcnow()}}
            ))
            user["fcm_token"] = default_fcm
        return user

    # ---- Transactions (declared before "/{user_id}" so "/transactions" isn't captured by it) ----
    @router.get("/transactions")
    async def get_transactions(token: Optional[str] = None, user_id: Optional[str] = None, limit: int = 100):
        uid = user_id
        if not uid and token:
            try:
                from routes.auth import verify_token
                uid = verify_token(token).get("user_id")
            except Exception:
                uid = None
        if not uid:
            raise HTTPException(status_code=400, detail="user_id or token required")

        # 1. Fetch transactions from db.transactions
        transactions = await _db(
            db.transactions.find({"$or": [{"user_id": uid}, {"client_id": uid}]}).sort("created_at", -1).limit(limit).to_list(limit)
        )
        if not transactions:
            # Legacy records written to the old collection
            transactions = await _db(
                db.wallet_transactions.find({"$or": [{"user_id": uid}, {"client_id": uid}]}).sort("created_at", -1).limit(limit).to_list(limit)
            )

        # 2. Check for any ended chat/voice/video sessions that might not yet be in db.transactions
        existing_session_ids = {t.get("session_id") for t in transactions if t.get("session_id")}
        ended_sessions = await _db(
            db.chat_sessions.find({"client_id": uid, "status": "ended"}).sort("ended_at", -1).limit(limit).to_list(limit)
        )

        new_session_txs = []
        for s in ended_sessions:
            sid = s.get("id")
            if not sid or sid in existing_session_ids:
                continue

            stype = (s.get("session_type") or "chat").lower()
            if stype in ["phone", "voice", "audio"]:
                stype = "phone"
                type_label = "Audio Call"
            elif stype == "video":
                type_label = "Video Call"
            else:
                stype = "chat"
                type_label = "Chat Reading"

            mins = round(float(s.get("total_minutes", 0) or 0), 2)
            rate = float(s.get("rate_per_minute", 2.99) or 2.99)
            cost = round(float(s.get("total_cost", 0) or (mins * rate)), 2)
            if cost <= 0:
                cost = round(max(1.0, mins) * rate, 2)

            created_at = s.get("ended_at") or s.get("started_at") or datetime.utcnow()
            dur_secs = int(s.get("duration_seconds", 0) or int(mins * 60))

            psy_name = s.get("psychic_name")
            if not psy_name:
                psy = await db.psychics.find_one({"$or": [{"id": s.get("psychic_id")}, {"email": s.get("psychic_email")}]})
                psy_name = psy.get("name") if psy else "Psychic Advisor"

            clean_sid = sid.replace("-", "")[:16]
            tx_doc = {
                "id": f"tx_sess_{clean_sid}",
                "transaction_id": f"tx_sess_{clean_sid}",
                "user_id": uid,
                "client_id": uid,
                "type": "session_deduction",
                "session_type": stype,
                "session_id": sid,
                "psychic_id": s.get("psychic_id"),
                "psychic_name": psy_name,
                "amount": cost,
                "duration_minutes": mins,
                "duration_seconds": dur_secs,
                "rate_per_minute": rate,
                "description": f"{type_label} with {psy_name} ({mins} min @ ${rate:.2f}/min)",
                "status": "completed",
                "created_at": created_at,
                "updated_at": created_at,
            }

            try:
                await db.transactions.update_one(
                    {"session_id": sid, "user_id": uid},
                    {"$set": tx_doc},
                    upsert=True
                )
            except Exception:
                pass

            new_session_txs.append(tx_doc)
            existing_session_ids.add(sid)

        all_txs = transactions + new_session_txs

        def get_sort_key(tx):
            dt = tx.get("created_at")
            if isinstance(dt, datetime):
                return dt.isoformat()
            return str(dt or "")

        all_txs.sort(key=get_sort_key, reverse=True)
        return [_serialize_tx(t) for t in all_txs[:limit]]

    @router.get("/transactions/{user_id}")
    async def get_transactions_by_user_id(user_id: str, limit: int = 100):
        return await get_transactions(user_id=user_id, limit=limit)

    # ---- Payment methods ----
    @router.get("/payment-methods/{user_id}")
    async def get_payment_methods_by_user(user_id: str):
        user = await _get_user_or_404(user_id)
        return {"payment_methods": user.get("saved_payment_methods", [])}

    @router.post("/payment-methods")
    async def save_payment_method(request: SavePaymentMethodRequest, token: Optional[str] = None):
        target_user_id = request.user_id
        if not target_user_id and token:
            from routes.auth import verify_token
            target_user_id = verify_token(token).get("user_id")
        if not target_user_id:
            raise HTTPException(status_code=400, detail="user_id or token required")

        user = await _get_user_or_404(target_user_id)
        methods = user.get("saved_payment_methods", [])
        pm = {
            "id": f"pm_{uuid.uuid4().hex[:12]}",
            "type": request.type or "card",
            "last_four": request.last_four or "4242",
            "brand": request.brand or "Visa",
            "cardholder_name": request.cardholder_name or "Cardholder",
            "is_default": request.is_default or not methods,
        }
        if pm["is_default"]:
            for m in methods:
                m["is_default"] = False
        methods.append(pm)
        await _db(db.users.update_one({"id": target_user_id}, {"$set": {"saved_payment_methods": methods}}))
        return {"success": True, "payment_method": pm, "payment_methods": methods}

    @router.post("/payment-methods/{user_id}/set-default/{method_id}")
    async def set_default_payment_method(user_id: str, method_id: str):
        user = await _get_user_or_404(user_id)
        methods = user.get("saved_payment_methods", [])
        if not any(m.get("id") == method_id for m in methods):
            raise HTTPException(status_code=404, detail="Payment method not found")
        for m in methods:
            m["is_default"] = (m.get("id") == method_id)
        await _db(db.users.update_one({"id": user_id}, {"$set": {"saved_payment_methods": methods}}))
        return {"success": True, "payment_methods": methods}

    @router.delete("/payment-methods/{user_id}/{method_id}")
    async def delete_payment_method(user_id: str, method_id: str):
        user = await _get_user_or_404(user_id)
        methods = [pm for pm in user.get("saved_payment_methods", []) if pm.get("id") != method_id]
        if methods and not any(pm.get("is_default") for pm in methods):
            methods[0]["is_default"] = True
        await _db(db.users.update_one({"id": user_id}, {"$set": {"saved_payment_methods": methods}}))
        return {"success": True, "payment_methods": methods}

    # ---- Wallet ----
    @router.post("/add-funds")
    async def add_funds(token: str, request: AddFundsRequest):
        from routes.auth import verify_token
        payload = verify_token(token)
        user = await _get_user_or_404(payload.get("user_id"))

        if request.amount < 5:
            raise HTTPException(status_code=400, detail="Minimum amount is $5")

        # BE-022: Atomic wallet balance update via find_one_and_update
        from pymongo import ReturnDocument
        updated_user = await _db(db.users.find_one_and_update(
            {"id": user["id"]},
            {"$inc": {"balance": request.amount}},
            return_document=ReturnDocument.AFTER
        ))
        new_balance = updated_user.get("balance", 0) if updated_user else user.get("balance", 0) + request.amount

        transaction = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "type": "credit_purchase",
            "amount": request.amount,
            "description": f"Added ${request.amount:.2f} to wallet",
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await _db(db.transactions.insert_one(transaction))

        async def _bg_wallet_notify():
            try:
                notif_body = f"+${request.amount:.2f} added! Your wallet balance is now ${new_balance:.2f}."
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user["id"],
                    "type": "wallet_credited",
                    "title": "🎉 Funds Added to Wallet!",
                    "body": notif_body,
                    "message": notif_body,
                    "data": {"credits": str(int(request.amount)), "balance": str(new_balance), "type": "wallet_topup"},
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
                from routes.push_notifications import send_firebase_push_to_user
                await send_firebase_push_to_user(
                    db,
                    user_id=user["id"],
                    title="🎉 Funds Added to Wallet!",
                    body=notif_body,
                    data={
                        "type": "wallet_credited",
                        "credits": str(int(request.amount)),
                        "balance": str(new_balance),
                        "action_url": "/(tabs)/wallet",
                    },
                )
            except Exception:
                pass
        asyncio.create_task(_bg_wallet_notify())

        return {
            "success": True,
            "new_balance": new_balance,
            "message": f"Successfully added ${request.amount:.2f} to your wallet"
        }

    @router.get("/", response_model=WalletResponse)
    async def get_wallet(token: str):
        from routes.auth import verify_token
        try:
            user_id = verify_token(token).get("user_id")
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
        return await get_wallet_by_user_id(user_id)

    @router.get("/{user_id}", response_model=WalletResponse)
    async def get_wallet_by_user_id(user_id: str):
        user = await _get_user_or_404(user_id)
        balance = float(user.get("balance", 0.0) or 0.0)
        free_credits = float(user.get("free_credits", 0.0) or 0.0)
        first_reading_free_used = bool(user.get("first_reading_free_used", False))
        free_minutes_available = int(user.get("free_minutes_available", 0) or 0)
        if not first_reading_free_used and free_minutes_available == 0:
            free_minutes_available = 4

        return WalletResponse(
            balance=balance,
            free_credits=free_credits,
            total_available=balance,
            is_new_user=bool(user.get("is_new_user", True) and not first_reading_free_used),
            first_reading_free_used=first_reading_free_used,
            free_minutes_available=free_minutes_available,
            payment_methods=[PaymentMethod(**pm) for pm in user.get("saved_payment_methods", [])
                             if pm.get("id") and pm.get("type") and pm.get("last_four")]
        )

    return router
