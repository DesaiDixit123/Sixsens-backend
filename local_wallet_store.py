import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncio

STORE_FILE = os.path.join(os.path.dirname(__file__), "local_wallet_data.json")

# Initial seed data for active test user
INITIAL_DATA = {
    "users": {
        "usr_client_1792000155236": {
            "id": "usr_client_1792000155236",
            "email": "client@sixthsense.com",
            "name": "Client User",
            "balance": 200.0,
            "is_new_user": False,
            "first_reading_free_used": True,
            "free_minutes_available": 0,
            "saved_payment_methods": [
                {
                    "id": "pm_default_visa",
                    "type": "card",
                    "last_four": "4242",
                    "brand": "Visa",
                    "is_default": True
                },
                {
                    "id": "pm_google_pay",
                    "type": "google_pay",
                    "last_four": "GPay",
                    "brand": "Google Pay",
                    "is_default": False
                },
                {
                    "id": "pm_apple_pay",
                    "type": "apple_pay",
                    "last_four": "APay",
                    "brand": "Apple Pay",
                    "is_default": False
                }
            ]
        }
    },
    "transactions": [
        {
            "id": "tx_topup_100",
            "user_id": "usr_client_1792000155236",
            "type": "credit_purchase",
            "amount": 90.0,
            "credits": 100,
            "description": "Added 100 Credits",
            "status": "completed",
            "payment_intent_id": "pi_mock_100",
            "created_at": "2026-10-14T17:56:00Z"
        },
        {
            "id": "tx_topup_50",
            "user_id": "usr_client_1792000155236",
            "type": "credit_purchase",
            "amount": 45.0,
            "credits": 50,
            "description": "Added 50 Credits",
            "status": "completed",
            "payment_intent_id": "pi_mock_50",
            "created_at": "2026-10-14T17:50:00Z"
        },
        {
            "id": "tx_topup_25",
            "user_id": "usr_client_1792000155236",
            "type": "credit_purchase",
            "amount": 23.0,
            "credits": 25,
            "description": "Added 25 Credits",
            "status": "completed",
            "payment_intent_id": "pi_mock_25",
            "created_at": "2026-10-14T17:48:00Z"
        },
        {
            "id": "tx_topup_10",
            "user_id": "usr_client_1792000155236",
            "type": "credit_purchase",
            "amount": 10.0,
            "credits": 10,
            "description": "Added 10 Credits",
            "status": "completed",
            "payment_intent_id": "pi_mock_10",
            "created_at": "2026-10-14T17:40:00Z"
        }
    ]
}

def load_data() -> Dict[str, Any]:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return INITIAL_DATA.copy()

def save_data(data: Dict[str, Any]):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving local wallet store: {e}")

# In-memory working copy
_store = load_data()
save_data(_store)

def get_local_user(user_id: str) -> Optional[Dict[str, Any]]:
    global _store
    user = _store.get("users", {}).get(user_id)
    if not user and user_id:
        user = {
            "id": user_id,
            "email": "client@sixthsense.com",
            "name": "Client User",
            "balance": 200.0,
            "is_new_user": False,
            "first_reading_free_used": True,
            "free_minutes_available": 0,
            "saved_payment_methods": [
                {
                    "id": "pm_default_visa",
                    "type": "card",
                    "last_four": "4242",
                    "brand": "Visa",
                    "is_default": True
                },
                {
                    "id": "pm_google_pay",
                    "type": "google_pay",
                    "last_four": "GPay",
                    "brand": "Google Pay",
                    "is_default": False
                },
                {
                    "id": "pm_apple_pay",
                    "type": "apple_pay",
                    "last_four": "APay",
                    "brand": "Apple Pay",
                    "is_default": False
                }
            ]
        }
        if "users" not in _store:
            _store["users"] = {}
        _store["users"][user_id] = user
        save_data(_store)
    return user

def update_local_user_balance(user_id: str, added_amount: float) -> float:
    global _store
    user = get_local_user(user_id)
    current_bal = float(user.get("balance", 0.0))
    new_bal = current_bal + added_amount
    user["balance"] = new_bal
    _store["users"][user_id] = user
    save_data(_store)
    return new_bal

def get_local_transactions(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    global _store
    all_tx = _store.get("transactions", [])
    user_tx = [t for t in all_tx if t.get("user_id") == user_id]
    if not user_tx and all_tx:
        # Fallback to seed transactions with user_id mapped
        res = []
        for t in all_tx:
            tc = t.copy()
            tc["user_id"] = user_id
            res.append(tc)
        return res[:limit]
    return user_tx[:limit]

def add_local_transaction(user_id: str, amount: float, credits: int, description: str, payment_intent_id: str = ""):
    global _store
    tx = {
        "id": f"tx_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "type": "credit_purchase",
        "amount": amount,
        "credits": credits,
        "description": description,
        "status": "completed",
        "payment_intent_id": payment_intent_id or f"pi_{uuid.uuid4().hex[:10]}",
        "created_at": datetime.utcnow().isoformat()
    }
    if "transactions" not in _store:
        _store["transactions"] = []
    _store["transactions"].insert(0, tx)
    save_data(_store)
    return tx

def get_local_payment_methods(user_id: str) -> List[Dict[str, Any]]:
    user = get_local_user(user_id)
    return user.get("saved_payment_methods", []) if user else []

def add_local_payment_method(user_id: str, pm: Dict[str, Any]):
    global _store
    user = get_local_user(user_id)
    methods = user.get("saved_payment_methods", [])
    if pm.get("is_default"):
        for m in methods:
            m["is_default"] = False
    methods.append(pm)
    user["saved_payment_methods"] = methods
    _store["users"][user_id] = user
    save_data(_store)
    return methods

def set_local_default_payment_method(user_id: str, method_id: str):
    global _store
    user = get_local_user(user_id)
    methods = user.get("saved_payment_methods", [])
    for m in methods:
        m["is_default"] = (m.get("id") == method_id)
    user["saved_payment_methods"] = methods
    _store["users"][user_id] = user
    save_data(_store)
    return methods
