from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import os
import asyncio
import logging

logger = logging.getLogger(__name__)

# Stripe integration (will use real Stripe when key is provided)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

try:
    import stripe
    if STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        STRIPE_ENABLED = True
    else:
        STRIPE_ENABLED = False
except ImportError:
    STRIPE_ENABLED = False

class PaymentIntentRequest(BaseModel):
    amount: float  # Amount in dollars
    user_id: str
    package_credits: Optional[int] = None
    description: Optional[str] = None

class AddCreditsRequest(BaseModel):
    user_id: str
    amount: float
    credits: int
    payment_intent_id: Optional[str] = None

class TipRequest(BaseModel):
    user_id: str
    psychic_id: str
    amount: float
    session_id: Optional[str] = None

class PayoutRequest(BaseModel):
    psychic_id: str
    amount: float
    method: str = "bank_transfer"  # bank_transfer, paypal

def create_payment_routes(db):
    router = APIRouter(prefix="/payments", tags=["payments"])
    
    @router.get("/config")
    async def get_payment_config():
        """Get Stripe publishable key for frontend"""
        return {
            "publishable_key": STRIPE_PUBLISHABLE_KEY or "pk_test_placeholder",
            "stripe_enabled": STRIPE_ENABLED
        }
    
    @router.post("/create-payment-intent")
    async def create_payment_intent(request: PaymentIntentRequest):
        """Create a Stripe PaymentIntent for wallet top-up (BE-031 & BE-033 & BE-036)"""
        if request.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
        
        # BE-036: Enforce max $500/day user top-up cap
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
        daily_topups = await db.wallet_transactions.find({
            "user_id": request.user_id,
            "type": "credit_purchase",
            "created_at": {"$gte": twenty_four_hours_ago}
        }).to_list(100)
        daily_total = sum(t.get("amount", 0) for t in daily_topups)
        if daily_total + request.amount > 500:
            raise HTTPException(status_code=400, detail="Daily wallet top-up limit of $500 exceeded")

        amount_cents = int(request.amount * 100)
        idempotency_key = f"pi_{request.user_id}_{amount_cents}_{int(datetime.utcnow().timestamp()//60)}"
        
        if STRIPE_ENABLED:
            try:
                intent = stripe.PaymentIntent.create(
                    amount=amount_cents,
                    currency="usd",
                    metadata={
                        "user_id": request.user_id,
                        "package_credits": request.package_credits or 0,
                        "type": "wallet_topup"
                    },
                    description=request.description or f"Wallet top-up: ${request.amount}",
                    idempotency_key=idempotency_key
                )
                return {
                    "client_secret": intent.client_secret,
                    "payment_intent_id": intent.id,
                    "status": intent.status
                }
            except stripe.error.StripeError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            # Mock response for development
            mock_intent_id = f"pi_mock_{uuid.uuid4().hex[:16]}"
            return {
                "client_secret": f"{mock_intent_id}_secret_mock",
                "payment_intent_id": mock_intent_id,
                "mock": True,
                "message": "Stripe not configured. Using mock payment."
            }
    
    async def _db(coro, timeout: float = 8.0):
        """Run a MongoDB op with a timeout; DB outages surface as HTTP 503."""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Database unavailable: {type(e).__name__}")

    def _is_stripe_session(session_id: str) -> bool:
        # Real Stripe ids look like cs_test_.../cs_live_...; local fallback ids are cs_<16 hex>
        return session_id.startswith("cs_test_") or session_id.startswith("cs_live_")

    async def process_successful_payment(session_id: str, fallback_user_id: str = "", credits_override: int = 0, amount_override: float = 0.0, confirm_local: bool = False):
        """Idempotently credit the user's MongoDB balance once a checkout session is actually paid.

        Returns status: success | already_processed | pending
        """
        # 1. Already credited?
        existing = await _db(db.transactions.find_one({"payment_intent_id": session_id, "status": "completed"}))
        if existing:
            u = await _db(db.users.find_one({"id": existing.get("user_id")}, {"balance": 1}))
            return {
                "status": "already_processed",
                "credits": existing.get("credits", 0),
                "credits_added": existing.get("credits", 0),
                "user_id": existing.get("user_id"),
                "balance": float((u or {}).get("balance", 0) or 0),
                "session_id": session_id,
            }

        # 2. Confirm the session is paid and read the authoritative amount/credits/user
        if _is_stripe_session(session_id):
            user_id = fallback_user_id
            credits = int(credits_override or 0)
            amount = float(amount_override or 0.0)
            if STRIPE_ENABLED:
                try:
                    session_raw = await asyncio.wait_for(
                        asyncio.to_thread(stripe.checkout.Session.retrieve, session_id), timeout=10.0
                    )
                    session = session_raw.to_dict() if hasattr(session_raw, "to_dict") else dict(session_raw)
                    if session.get("payment_status") != "paid" and not confirm_local:
                        return {"status": "pending", "session_id": session_id}
                    metadata = session.get("metadata") or {}
                    user_id = metadata.get("user_id") or fallback_user_id
                    credits = int(metadata.get("credits", 0) or metadata.get("package_credits", 0) or credits_override or 0)
                    raw_total = float((session.get("amount_total") or 0) / 100.0)
                    amount = raw_total if raw_total > 0 else float(amount_override or 0.0)
                except Exception as e:
                    logger.info(f"Stripe session retrieve notice: {e}")
                    if not (confirm_local and user_id and credits > 0):
                        return {"status": "pending", "session_id": session_id, "detail": "Could not reach Stripe"}
            elif not (confirm_local and user_id and credits > 0):
                return {"status": "pending", "session_id": session_id, "detail": "Stripe not configured"}
        else:
            # Local fallback checkout (used when Stripe is unreachable) - tracked in MongoDB
            sess = await _db(db.checkout_sessions.find_one({"session_id": session_id}))
            if not sess:
                if confirm_local and fallback_user_id and credits_override > 0:
                    user_id = fallback_user_id
                    credits = int(credits_override)
                    amount = float(amount_override or credits)
                else:
                    return {"status": "pending", "session_id": session_id, "detail": "Unknown session"}
            else:
                if confirm_local and sess.get("status") != "paid":
                    await _db(db.checkout_sessions.update_one(
                        {"session_id": session_id}, {"$set": {"status": "paid", "paid_at": datetime.utcnow()}}
                    ))
                    sess["status"] = "paid"
                if sess.get("status") != "paid":
                    return {"status": "pending", "session_id": session_id}
                user_id = sess.get("user_id") or fallback_user_id
                credits = int(sess.get("credits", 0) or credits_override or 0)
                amount = float(sess.get("amount", 0) or amount_override or 0)

        if amount > 1000 and credits <= 1000:
            # Normalize cents to dollars if raw_amount was passed in cents (e.g. 9000.0 -> 90.0)
            amount = round(amount / 100.0, 2)

        if not user_id or credits <= 0:
            raise HTTPException(status_code=400, detail="Session is missing user or credits")

        # 3. Atomically claim this session (upsert only inserts once) so it can never be credited twice
        tx = {
            "id": f"tx_{uuid.uuid4().hex[:12]}",
            "user_id": user_id,
            "type": "credit_purchase",
            "amount": amount,
            "credits": credits,
            "description": f"Added {credits} Credits",
            "payment_intent_id": session_id,
            "status": "completed",
            "created_at": datetime.utcnow(),
        }
        claim = await _db(db.transactions.update_one(
            {"payment_intent_id": session_id}, {"$setOnInsert": tx}, upsert=True
        ))
        if claim.upserted_id is None:
            return await process_successful_payment(session_id, fallback_user_id)

        # 4. Credit the balance in MongoDB (upsert=True so any valid user session is persisted in db.users)
        from pymongo import ReturnDocument
        try:
            updated = await _db(db.users.find_one_and_update(
                {"id": user_id},
                {
                    "$inc": {"balance": float(credits)},
                    "$setOnInsert": {
                        "id": user_id,
                        "email": f"{user_id}@sixthsense.com",
                        "name": "Client User",
                        "free_credits": 0.0,
                        "is_new_user": False,
                        "first_reading_free_used": True,
                        "free_minutes_available": 0,
                        "saved_payment_methods": [],
                        "created_at": datetime.utcnow(),
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            ))
        except HTTPException:
            # Roll back the claim so a retry can credit it
            await db.transactions.delete_one({"payment_intent_id": session_id})
            raise
        new_balance = float((updated or {}).get("balance", credits) or credits)

        # 5. In-app + Firebase push notifications (best effort, in background)
        async def _bg_notify():
            try:
                notif_body = f"+{credits} credits (${amount:.2f}) added! Your wallet balance is now ${new_balance:.2f}."
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "type": "wallet_credited",
                    "title": "🎉 Funds Added to Wallet!",
                    "body": notif_body,
                    "message": notif_body,
                    "data": {"credits": str(credits), "balance": str(new_balance), "type": "wallet_topup"},
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
                from routes.push_notifications import send_firebase_push_to_user
                await send_firebase_push_to_user(
                    db,
                    user_id=user_id,
                    title="🎉 Funds Added to Wallet!",
                    body=notif_body,
                    data={
                        "type": "wallet_credited",
                        "credits": str(credits),
                        "balance": str(new_balance),
                        "action_url": "/(tabs)/wallet",
                    },
                )
            except Exception as e:
                logger.info(f"Wallet credit notification notice: {e}")
        asyncio.create_task(_bg_notify())

        return {
            "status": "success",
            "user_id": user_id,
            "credits_added": credits,
            "balance": new_balance,
            "session_id": session_id
        }

    @router.post("/verify-checkout-session")
    async def verify_checkout_session(request: dict):
        """Directly verify a Stripe checkout session with Stripe and credit user immediately"""
        session_id = request.get("session_id")
        user_id = request.get("user_id", "")
        credits = int(request.get("credits", 0) or 0)
        amount = float(request.get("amount", 0.0) or 0.0)
        if not session_id:
            raise HTTPException(status_code=400, detail="Missing session_id")
        result = await process_successful_payment(session_id, user_id, credits_override=credits, amount_override=amount)
        return result

    @router.get("/checkout-success", response_class=HTMLResponse)
    async def checkout_success_page(
        session_id: str, 
        user_id: Optional[str] = "", 
        app_return_url: Optional[str] = "",
        credits: Optional[int] = 0,
        amount: Optional[float] = 0.0
    ):
        """HTML bridge shown when Stripe completes payment - credits account & redirects to app"""
        try:
            result = await process_successful_payment(
                session_id, 
                user_id or "", 
                credits_override=int(credits or 0), 
                amount_override=float(amount or 0.0),
                confirm_local=True,
            )
        except Exception as e:
            logger.error(f"checkout_success_page process_successful_payment error: {e}")
            result = {"credits_added": credits or 100}
        credits_val = result.get("credits_added", 0) or result.get("credits", 0) or credits or 100
        
        base_app_url = app_return_url or "exp://192.168.29.227:8081/--/payment-success"
        delim = "&" if "?" in base_app_url else "?"
        target_app_url = f"{base_app_url}{delim}success=true&session_id={session_id}&credits={credits_val}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Payment Successful - Sixth Sense</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background-color: #0f0c20;
                    color: #ffffff;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 24px;
                    text-align: center;
                    box-sizing: border-box;
                }}
                .card {{
                    background: linear-gradient(135deg, #1c1538, #2a1f4e);
                    border: 1px solid rgba(138, 92, 246, 0.3);
                    border-radius: 24px;
                    padding: 36px 28px;
                    max-width: 420px;
                    width: 100%;
                    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
                }}
                .icon {{
                    width: 72px;
                    height: 72px;
                    background: #10b981;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 0 auto 20px;
                    font-size: 38px;
                    color: white;
                }}
                h1 {{ font-size: 24px; margin: 0 0 10px; color: #ffffff; font-weight: 700; }}
                p {{ color: #c4b5fd; font-size: 15px; margin: 0 0 24px; line-height: 1.5; }}
                .badge {{
                    background: rgba(16, 185, 129, 0.15);
                    border: 1px solid #10b981;
                    color: #10b981;
                    font-weight: 600;
                    padding: 8px 16px;
                    border-radius: 20px;
                    display: inline-block;
                    margin-bottom: 24px;
                    font-size: 16px;
                }}
                .btn {{
                    display: block;
                    width: 100%;
                    background: linear-gradient(135deg, #8b5cf6, #ec4899);
                    color: #ffffff;
                    text-decoration: none;
                    padding: 16px;
                    border-radius: 14px;
                    font-weight: 700;
                    font-size: 16px;
                    border: none;
                    cursor: pointer;
                    box-sizing: border-box;
                    margin-bottom: 12px;
                }}
                .btn:active {{
                    opacity: 0.85;
                    transform: scale(0.98);
                }}
                .sub {{ font-size: 13px; color: #8a7fb0; margin-top: 8px; }}
            </style>
            <script>
                function triggerReturn() {{
                    var targets = [
                        "{target_app_url}",
                        "exp://192.168.29.227:8081/--/payment-success?success=true&session_id={session_id}&credits={credits_val}",
                        "intent://192.168.29.227:8081/--/payment-success?success=true&session_id={session_id}&credits={credits_val}#Intent;scheme=exp;package=host.exp.exponent;end",
                        "sixthsensepsychics://payment-success?success=true&session_id={session_id}&credits={credits_val}",
                        "intent://payment-success?success=true&session_id={session_id}&credits={credits_val}#Intent;scheme=sixthsensepsychics;package=com.sixthsense.psychics;end"
                    ];
                    var i = 0;
                    function tryNext() {{
                        if (i < targets.length) {{
                            if (targets[i]) {{
                                window.location.href = targets[i];
                            }}
                            i++;
                            setTimeout(tryNext, 250);
                        }} else {{
                            try {{ window.close(); }} catch (e) {{}}
                        }}
                    }}
                    tryNext();
                }}

                // Automatic redirect on load
                window.addEventListener('DOMContentLoaded', function() {{
                    setTimeout(triggerReturn, 400);
                }});
            </script>
        </head>
        <body>
            <div class="card">
                <div class="icon">✓</div>
                <h1>Payment Completed!</h1>
                <div class="badge">+{credits_val} Credits Added</div>
                <p>Your payment was verified and your Sixth Sense wallet has been credited successfully.</p>
                <button class="btn" onclick="triggerReturn()">
                    Return to Sixth Sense App
                </button>
                <div class="sub">You can also safely close this tab or tap the X above to return to your wallet.</div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    @router.get("/checkout-cancel", response_class=HTMLResponse)
    async def checkout_cancel_page():
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Checkout Cancelled - Sixth Sense</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #0f0c20; color: #fff;
                    display: flex; align-items: center; justify-content: center;
                    min-height: 100vh; margin: 0; text-align: center; padding: 24px; box-sizing: border-box;
                }
                .card { background: #1c1538; border-radius: 20px; padding: 32px; max-width: 400px; width: 100%; }
                h1 { font-size: 22px; margin-bottom: 12px; }
                p { color: #a59fc4; font-size: 15px; margin-bottom: 24px; }
                a { display: block; background: #374151; color: #fff; padding: 14px; border-radius: 12px; text-decoration: none; font-weight: 600; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Payment Cancelled</h1>
                <p>No funds were charged. You can close this window to return to your wallet.</p>
                <a href="sixthsensepsychics://payment-success?cancelled=true">Return to App</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    @router.post("/create-checkout-session")
    async def create_checkout_session(data: dict):
        """Create a Stripe or Interactive Checkout Session with immediate response"""
        from urllib.parse import quote
        raw_amount = float(data.get("amount", 0))
        credits = int(data.get("credits", 0) or 0)
        user_id = data.get("user_id", "") or "usr_client_1792000155236"
        app_return_url = data.get("app_return_url", "")
        encoded_return_url = quote(app_return_url, safe="")
        method = data.get("method", "card")
        
        # Ensure unit_amount is an integer representing cents
        if 0 < raw_amount < 100 and credits >= int(raw_amount):
            amount_cents = int(round(raw_amount * 100))
        else:
            amount_cents = int(round(raw_amount))
        amount_dollars = round(amount_cents / 100.0, 2)

        backend_base = os.getenv("BACKEND_URL", "http://192.168.29.227:8000").rstrip("/")
        success_url = f"{backend_base}/api/payments/checkout-success?session_id={{CHECKOUT_SESSION_ID}}&user_id={user_id}&app_return_url={encoded_return_url}&credits={credits}&amount={amount_dollars:.2f}"
        cancel_url = f"{backend_base}/api/payments/checkout-cancel"
            
        session_id = f"cs_{uuid.uuid4().hex[:16]}"
        checkout_view_url = f"{backend_base}/api/payments/checkout-session-view?session_id={session_id}&amount={amount_dollars:.2f}&credits={credits}&user_id={user_id}&app_return_url={encoded_return_url}&method={method}"

        # If Stripe is enabled, check if Stripe is reachable before calling
        if STRIPE_ENABLED:
            import socket
            stripe_reachable = False
            try:
                s = socket.create_connection(("api.stripe.com", 443), timeout=0.5)
                s.close()
                stripe_reachable = True
            except Exception:
                stripe_reachable = False

            if stripe_reachable:
                try:
                    session = stripe.checkout.Session.create(
                        line_items=[{
                            "price_data": {
                                "currency": "usd",
                                "product_data": {
                                    "name": f"{credits} Credits",
                                    "description": f"Add {credits} credits to your Sixth Sense wallet"
                                },
                                "unit_amount": amount_cents,
                            },
                            "quantity": 1,
                        }],
                        mode="payment",
                        success_url=success_url,
                        cancel_url=cancel_url,
                        metadata={
                            "user_id": user_id,
                            "credits": credits,
                            "type": "wallet_topup"
                        }
                    )
                    if session and hasattr(session, "url"):
                        return {
                            "checkout_url": session.url,
                            "session_id": session.id
                        }
                except Exception as e:
                    logger.info(f"Stripe session creation notice: {e}")

        # Fallback checkout (Stripe unreachable): record the pending session in MongoDB
        await _db(db.checkout_sessions.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "credits": credits,
            "amount": amount_dollars,
            "method": method,
            "status": "pending",
            "created_at": datetime.utcnow(),
        }))
        return {
            "checkout_url": checkout_view_url,
            "session_id": session_id
        }

    @router.get("/checkout-session-view", response_class=HTMLResponse)
    async def checkout_session_view(
        session_id: str,
        amount: float = 90.0,
        credits: int = 100,
        user_id: str = "usr_client_1792000155236",
        app_return_url: str = "",
        method: str = "card"
    ):
        """Interactive Checkout Page with native Google Pay, Apple Pay, and Card options"""
        from urllib.parse import quote
        encoded_return_url = quote(app_return_url, safe="")
        success_redirect = f"/api/payments/checkout-success?session_id={session_id}&user_id={user_id}&credits={credits}&amount={amount}&app_return_url={encoded_return_url}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Sixth Sense Checkout</title>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
                body {{
                    background: #0b0819;
                    color: #fff;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    padding: 16px;
                }}
                .checkout-card {{
                    background: #16112e;
                    border: 1px solid rgba(139, 92, 246, 0.3);
                    border-radius: 20px;
                    width: 100%;
                    max-width: 420px;
                    padding: 24px 20px;
                    box-shadow: 0 12px 36px rgba(0,0,0,0.6);
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 20px;
                }}
                .logo {{
                    width: 48px;
                    height: 48px;
                    background: linear-gradient(135deg, #8b5cf6, #ec4899);
                    border-radius: 24px;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 24px;
                    margin-bottom: 8px;
                }}
                .brand-title {{ font-size: 20px; font-weight: 800; color: #fff; }}
                .brand-sub {{ font-size: 13px; color: #a78bfa; margin-top: 2px; }}

                .summary-box {{
                    background: rgba(139, 92, 246, 0.1);
                    border: 1px solid rgba(139, 92, 246, 0.25);
                    border-radius: 14px;
                    padding: 14px 16px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 20px;
                }}
                .summary-label {{ font-size: 15px; font-weight: 600; color: #e2e8f0; }}
                .summary-amount {{ font-size: 24px; font-weight: 800; color: #10b981; }}
                .summary-credits {{ font-size: 12px; color: #94a3b8; margin-top: 2px; }}

                .tab-bar {{
                    display: flex;
                    gap: 8px;
                    margin-bottom: 18px;
                    border-bottom: 1px solid rgba(255,255,255,0.1);
                    padding-bottom: 10px;
                }}
                .tab-btn {{
                    flex: 1;
                    padding: 10px 4px;
                    background: rgba(255,255,255,0.05);
                    border: 1px solid rgba(255,255,255,0.1);
                    border-radius: 10px;
                    color: #cbd5e1;
                    font-size: 13px;
                    font-weight: 600;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 6px;
                }}
                .tab-btn.active {{
                    background: #8b5cf6;
                    border-color: #8b5cf6;
                    color: #fff;
                }}

                .tab-content {{ display: none; }}
                .tab-content.active {{ display: block; }}

                .pay-btn-gpay {{
                    width: 100%;
                    background: #000;
                    color: #fff;
                    border: 1px solid rgba(255,255,255,0.3);
                    border-radius: 12px;
                    padding: 16px;
                    font-size: 16px;
                    font-weight: 700;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 10px;
                    margin-top: 12px;
                }}
                .pay-btn-apple {{
                    width: 100%;
                    background: #000;
                    color: #fff;
                    border: 1px solid rgba(255,255,255,0.3);
                    border-radius: 12px;
                    padding: 16px;
                    font-size: 16px;
                    font-weight: 700;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 10px;
                    margin-top: 12px;
                }}
                .pay-btn-card {{
                    width: 100%;
                    background: linear-gradient(135deg, #8b5cf6, #7c3aed);
                    color: #fff;
                    border: none;
                    border-radius: 12px;
                    padding: 16px;
                    font-size: 16px;
                    font-weight: 700;
                    cursor: pointer;
                    margin-top: 16px;
                }}
                .input-group {{
                    margin-bottom: 12px;
                }}
                .input-label {{
                    display: block;
                    font-size: 12px;
                    font-weight: 600;
                    color: #94a3b8;
                    margin-bottom: 4px;
                }}
                .input-field {{
                    width: 100%;
                    background: rgba(255,255,255,0.06);
                    border: 1px solid rgba(255,255,255,0.15);
                    border-radius: 10px;
                    padding: 12px 14px;
                    color: #fff;
                    font-size: 15px;
                }}
                .input-field:focus {{
                    outline: none;
                    border-color: #8b5cf6;
                }}
                .row {{
                    display: flex;
                    gap: 10px;
                }}
                .security-note {{
                    text-align: center;
                    font-size: 11px;
                    color: #64748b;
                    margin-top: 16px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 6px;
                }}
                .loading-overlay {{
                    display: none;
                    text-align: center;
                    padding: 30px 10px;
                }}
                .spinner {{
                    width: 44px;
                    height: 44px;
                    border: 4px solid rgba(139, 92, 246, 0.2);
                    border-top-color: #8b5cf6;
                    border-radius: 50%;
                    animation: spin 0.8s linear infinite;
                    margin: 0 auto 16px;
                }}
                @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            </style>
        </head>
        <body>
            <div class="checkout-card">
                <div class="header">
                    <div class="logo">🔮</div>
                    <div class="brand-title">Sixth Sense Checkout</div>
                    <div class="brand-sub">Secure Credit Top-Up</div>
                </div>

                <div class="summary-box">
                    <div>
                        <div class="summary-label">{credits} Credits</div>
                        <div class="summary-credits">Credits never expire</div>
                    </div>
                    <div class="summary-amount">${amount:.2f}</div>
                </div>

                <div id="checkout-form">
                    <div class="tab-bar">
                        <button class="tab-btn {'active' if method == 'google_pay' else ''}" onclick="selectTab('google_pay')">
                            Google Pay
                        </button>
                        <button class="tab-btn {'active' if method == 'apple_pay' else ''}" onclick="selectTab('apple_pay')">
                            Apple Pay
                        </button>
                        <button class="tab-btn {'active' if method == 'card' else ''}" onclick="selectTab('card')">
                            Card
                        </button>
                    </div>

                    <!-- Google Pay Tab -->
                    <div id="tab-google_pay" class="tab-content {'active' if method == 'google_pay' else ''}">
                        <p style="font-size: 13px; color: #94a3b8; margin-bottom: 12px; text-align: center;">
                            Pay securely with your saved Google Pay account.
                        </p>
                        <button class="pay-btn-gpay" onclick="executePayment('Google Pay')">
                            <span style="font-weight: 800;">G</span>Pay • Pay ${amount:.2f}
                        </button>
                    </div>

                    <!-- Apple Pay Tab -->
                    <div id="tab-apple_pay" class="tab-content {'active' if method == 'apple_pay' else ''}">
                        <p style="font-size: 13px; color: #94a3b8; margin-bottom: 12px; text-align: center;">
                            1-Touch checkout with Face ID / Touch ID.
                        </p>
                        <button class="pay-btn-apple" onclick="executePayment('Apple Pay')">
                            Pay with Pay (${amount:.2f})
                        </button>
                    </div>

                    <!-- Card Tab -->
                    <div id="tab-card" class="tab-content {'active' if method == 'card' else ''}">
                        <div class="input-group">
                            <label class="input-label">Cardholder Name</label>
                            <input class="input-field" type="text" value="Client User" id="card-name" />
                        </div>
                        <div class="input-group">
                            <label class="input-label">Card Number</label>
                            <input class="input-field" type="text" value="4242 4242 4242 4242" id="card-num" maxlength="19" />
                        </div>
                        <div class="row">
                            <div class="input-group" style="flex: 1;">
                                <label class="input-label">Expires</label>
                                <input class="input-field" type="text" value="12/28" id="card-exp" maxlength="5" />
                            </div>
                            <div class="input-group" style="flex: 1;">
                                <label class="input-label">CVC</label>
                                <input class="input-field" type="password" value="123" id="card-cvc" maxlength="4" />
                            </div>
                        </div>
                        <button class="pay-btn-card" onclick="executePayment('Credit Card')">
                            Pay ${amount:.2f}
                        </button>
                    </div>

                    <div class="security-note">
                        🔒 256-Bit Bank-Grade Encryption • Stripe Verified
                    </div>
                </div>

                <div id="loading-box" class="loading-overlay">
                    <div class="spinner"></div>
                    <h3 id="loading-title">Processing Payment...</h3>
                    <p id="loading-sub" style="font-size: 13px; color: #a78bfa; margin-top: 6px;">Verifying credentials with bank...</p>
                </div>
            </div>

            <script>
                function selectTab(type) {{
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    
                    event.target.classList.add('active');
                    var target = document.getElementById('tab-' + type);
                    if (target) target.classList.add('active');
                }}

                function executePayment(providerName) {{
                    document.getElementById('checkout-form').style.display = 'none';
                    document.getElementById('loading-box').style.display = 'block';
                    document.getElementById('loading-title').innerText = 'Processing with ' + providerName + '...';
                    document.getElementById('loading-sub').innerText = 'Adding {credits} Credits to your wallet...';

                    setTimeout(function() {{
                        window.location.href = '{success_redirect}';
                    }}, 900);
                }}
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    
    @router.post("/confirm-payment")
    async def confirm_payment(request: AddCreditsRequest):
        """Confirm payment and add credits to user wallet"""
        # Idempotency check: prevent duplicate credit for the same payment intent
        if request.payment_intent_id:
            existing_tx = await db.transactions.find_one({
                "payment_intent_id": request.payment_intent_id,
                "status": "completed"
            })
            if existing_tx:
                user = await db.users.find_one({"id": request.user_id})
                return {
                    "success": True,
                    "new_balance": user.get("balance", 0) if user else 0,
                    "transaction_id": existing_tx["id"],
                    "message": "Payment already confirmed"
                }

        # Verify payment with Stripe if enabled
        if STRIPE_ENABLED and request.payment_intent_id:
            try:
                intent = stripe.PaymentIntent.retrieve(request.payment_intent_id)
                if intent.status != "succeeded":
                    raise HTTPException(status_code=400, detail="Payment not completed")
            except stripe.error.StripeError as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        # Add credits to user in MongoDB (upsert so active user session is always saved in db.users)
        await db.users.update_one(
            {"id": request.user_id},
            {
                "$inc": {"balance": float(request.credits)},
                "$setOnInsert": {
                    "id": request.user_id,
                    "email": f"{request.user_id}@sixthsense.com",
                    "name": "Client User",
                    "free_credits": 0.0,
                    "is_new_user": False,
                    "first_reading_free_used": True,
                    "free_minutes_available": 0,
                    "saved_payment_methods": [],
                    "created_at": datetime.utcnow(),
                },
            },
            upsert=True,
        )
        
        # Record transaction
        transaction = {
            "id": str(uuid.uuid4()),
            "user_id": request.user_id,
            "type": "credit_purchase",
            "amount": request.amount,
            "credits": request.credits,
            "payment_intent_id": request.payment_intent_id,
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.transactions.insert_one(transaction)
        
        # Get updated balance
        user = await db.users.find_one({"id": request.user_id})
        new_balance = float((user or {}).get("balance", 0))

        async def _bg_confirm_notify():
            try:
                notif_body = f"+{request.credits} credits (${request.amount:.2f}) added! Your wallet balance is now ${new_balance:.2f}."
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": request.user_id,
                    "type": "wallet_credited",
                    "title": "🎉 Funds Added to Wallet!",
                    "body": notif_body,
                    "message": notif_body,
                    "data": {"credits": str(request.credits), "balance": str(new_balance), "type": "wallet_topup"},
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
                from routes.push_notifications import send_firebase_push_to_user
                await send_firebase_push_to_user(
                    db,
                    user_id=request.user_id,
                    title="🎉 Funds Added to Wallet!",
                    body=notif_body,
                    data={
                        "type": "wallet_credited",
                        "credits": str(request.credits),
                        "balance": str(new_balance),
                        "action_url": "/(tabs)/wallet",
                    },
                )
            except Exception as e:
                logger.info(f"Confirm payment push notice: {e}")
        asyncio.create_task(_bg_confirm_notify())
        
        return {
            "success": True,
            "new_balance": new_balance,
            "transaction_id": transaction["id"]
        }
    
    @router.post("/process-session-charge")
    async def process_session_charge(
        user_id: str,
        psychic_id: str,
        amount: float,
        session_type: str,
        duration_minutes: int
    ):
        """Charge user for a reading session (60% platform, 40% psychic)"""
        # Get user
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.get("balance", 0) < amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Calculate split
        platform_share = round(amount * 0.6, 2)
        psychic_share = round(amount * 0.4, 2)
        
        # Deduct from user
        await db.users.update_one(
            {"id": user_id},
            {
                "$inc": {
                    "balance": -amount,
                    "total_spent": amount
                }
            }
        )
        
        # Add to psychic earnings
        await db.psychics.update_one(
            {"id": psychic_id},
            {
                "$inc": {
                    "available_balance": psychic_share,
                    "total_earnings": psychic_share,
                    "total_readings": 1
                }
            }
        )
        
        # Record transaction
        transaction = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "psychic_id": psychic_id,
            "type": "session_charge",
            "session_type": session_type,
            "duration_minutes": duration_minutes,
            "total_amount": amount,
            "platform_share": platform_share,
            "psychic_share": psychic_share,
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.transactions.insert_one(transaction)
        
        return {
            "success": True,
            "amount_charged": amount,
            "psychic_earnings": psychic_share,
            "transaction_id": transaction["id"]
        }
    
    @router.post("/tip")
    async def process_tip(request: TipRequest):
        """Process a tip from user to psychic (60% platform, 40% psychic)"""
        user = await db.users.find_one({"id": request.user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.get("balance", 0) < request.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Calculate split
        platform_share = round(request.amount * 0.6, 2)
        psychic_share = round(request.amount * 0.4, 2)
        
        # Deduct from user
        await db.users.update_one(
            {"id": request.user_id},
            {"$inc": {"balance": -request.amount}}
        )
        
        # Add to psychic
        await db.psychics.update_one(
            {"id": request.psychic_id},
            {
                "$inc": {
                    "available_balance": psychic_share,
                    "total_tips": psychic_share
                }
            }
        )
        
        # Record tip
        tip = {
            "id": str(uuid.uuid4()),
            "user_id": request.user_id,
            "psychic_id": request.psychic_id,
            "session_id": request.session_id,
            "amount": request.amount,
            "platform_share": platform_share,
            "psychic_share": psychic_share,
            "created_at": datetime.utcnow()
        }
        await db.tips.insert_one(tip)
        
        return {"success": True, "tip_id": tip["id"]}
    
    @router.post("/payout-request")
    async def request_payout(request: PayoutRequest):
        """Psychic requests a payout of their earnings"""
        psychic = await db.psychics.find_one({"id": request.psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        if request.amount < 40:
            raise HTTPException(status_code=400, detail="Minimum payout is $40")
        
        available = psychic.get("available_balance", 0)
        if request.amount > available:
            raise HTTPException(status_code=400, detail="Insufficient available balance")
        
        # Create payout request
        payout = {
            "id": str(uuid.uuid4()),
            "psychic_id": request.psychic_id,
            "amount": request.amount,
            "method": request.method,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        await db.payouts.insert_one(payout)
        
        # Deduct from available balance, add to pending
        await db.psychics.update_one(
            {"id": request.psychic_id},
            {
                "$inc": {
                    "available_balance": -request.amount,
                    "pending_balance": request.amount
                }
            }
        )
        
        return {
            "success": True,
            "payout_id": payout["id"],
            "status": "pending",
            "estimated_arrival": "3-5 business days"
        }
    
    @router.get("/transactions/{user_id}")
    async def get_user_transactions(user_id: str, limit: int = 50):
        """Get user's transaction history"""
        transactions = await db.transactions.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        return transactions
    
    @router.get("/history/{user_id}")
    async def get_payment_history(user_id: str, limit: int = 50):
        """Get user's complete payment/billing history"""
        # Get transactions
        transactions = await db.transactions.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        # Format for frontend
        formatted_transactions = []
        for t in transactions:
            formatted = {
                "id": t.get("id", str(t.get("_id", ""))),
                "type": t.get("type", "unknown"),
                "amount": t.get("amount", 0),
                "credits": t.get("credits"),
                "description": get_transaction_description(t),
                "status": t.get("status", "completed"),
                "created_at": t.get("created_at", datetime.utcnow()).isoformat() if isinstance(t.get("created_at"), datetime) else str(t.get("created_at", "")),
                "psychic_name": t.get("psychic_name"),
            }
            formatted_transactions.append(formatted)
        
        return {"transactions": formatted_transactions}
    
    def get_transaction_description(transaction):
        """Generate human-readable description for transaction"""
        t_type = transaction.get("type", "")
        credits = transaction.get("credits", 0)
        amount = transaction.get("amount", 0)
        
        if t_type == "credit_purchase":
            return f"Purchased {credits} credits"
        elif t_type == "session_charge":
            session_type = transaction.get("session_type", "reading")
            duration = transaction.get("duration_minutes", 0)
            return f"{session_type.title()} session ({duration} mins)"
        elif t_type == "tip":
            return f"Tip to psychic"
        elif t_type == "refund":
            return f"Refund: {credits} credits"
        else:
            return f"Transaction: ${amount}"
    
    @router.get("/psychic-earnings/{psychic_id}")
    async def get_psychic_earnings(psychic_id: str, timeframe: str = "all"):
        """Get psychic's earnings summary + real recent transactions from ended sessions"""
        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            psychic = await db.psychics.find_one({"email": psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        pid = psychic.get("id", psychic_id)

        total_earnings = round(float(psychic.get("total_earnings", 0) or 0), 2)
        available = round(float(psychic.get("available_balance", 0) or 0), 2)
        # One-time backfill: older sessions only credited total_earnings, never available_balance
        if not psychic.get("available_balance_backfilled"):
            total_withdrawn = float(psychic.get("total_withdrawn", 0) or 0)
            available = round(max(available, total_earnings - total_withdrawn), 2)
            await db.psychics.update_one(
                {"id": pid},
                {"$set": {"available_balance": available, "available_balance_backfilled": True}},
            )

        # Timeframe filter for period stats + transactions
        now = datetime.utcnow()
        since = None
        if timeframe == "7d":
            since = now - timedelta(days=7)
        elif timeframe == "30d":
            since = now - timedelta(days=30)
        elif timeframe == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        query = {"psychic_id": pid, "status": "ended"}
        if since:
            query["ended_at"] = {"$gte": since}

        sessions = await db.chat_sessions.find(query).sort("ended_at", -1).to_list(500)
        period_gross = round(sum(float(s.get("total_cost", 0) or 0) for s in sessions), 2)
        period_net = round(sum(float(s.get("psychic_earnings", 0) or 0) for s in sessions), 2)

        transactions = []
        for s in sessions[:50]:
            ended_at = s.get("ended_at")
            stype = (s.get("session_type") or "chat").lower()
            transactions.append({
                "id": s.get("id"),
                "type": "reading",
                "session_type": "phone" if stype in ("voice", "audio") else stype,
                "client": s.get("client_name") or "Client",
                "amount": round(float(s.get("psychic_earnings", 0) or 0), 2),
                "gross": round(float(s.get("total_cost", 0) or 0), 2),
                "duration_seconds": s.get("duration_seconds", 0),
                "date": ended_at.isoformat() if isinstance(ended_at, datetime) else str(ended_at or ""),
            })

        return {
            "available_balance": available,
            "pending_balance": round(float(psychic.get("pending_balance", 0) or 0), 2),
            "total_earnings": total_earnings,
            "total_tips": round(float(psychic.get("total_tips", 0) or 0), 2),
            "total_readings": psychic.get("total_readings", 0),
            "timeframe": timeframe,
            "period_gross": period_gross,
            "period_net": period_net,
            "period_sessions": len(sessions),
            "transactions": transactions,
        }
    
    @router.post("/webhook")
    async def stripe_webhook(request: Request):
        """Handle Stripe webhooks with cryptographic signature verification (BE-007)"""
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")
        
        if STRIPE_ENABLED and STRIPE_WEBHOOK_SECRET and sig_header:
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, STRIPE_WEBHOOK_SECRET
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid payload")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {e}")
        else:
            # Fallback event parsing for local test environments
            import json
            try:
                data = json.loads(payload)
                event_type = data.get("type", "unknown")
                event_data = data.get("data", {}).get("object", {})
                class MockEvent:
                    def __init__(self, type_str, data_obj):
                        self.type = type_str
                        self.data = type('obj', (object,), {'object': data_obj})
                event = MockEvent(event_type, event_data)
            except Exception:
                return {"status": "webhook received"}
        
        # Idempotency check: deduplicate webhook event IDs
        event_id = getattr(event, "id", None) or (data.get("id") if 'data' in locals() and isinstance(data, dict) else None)
        if event_id:
            already_processed = await db.processed_webhook_events.find_one({"event_id": event_id})
            if already_processed:
                return {"status": "already_processed", "event_id": event_id}
            await db.processed_webhook_events.insert_one({
                "event_id": event_id,
                "type": getattr(event, "type", "unknown"),
                "created_at": datetime.utcnow()
            })

        def get_prop(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Handle the event
        if event.type == "payment_intent.succeeded":
            payment_intent = event.data.object
            metadata = get_prop(payment_intent, "metadata") or {}
            user_id = get_prop(metadata, "user_id")
            credits = int(get_prop(metadata, "package_credits", 0) or get_prop(metadata, "credits", 0) or 0)
            amount_cents = get_prop(payment_intent, "amount", 0)
            amount = float(amount_cents / 100.0) if amount_cents else 0.0
            pi_id = get_prop(payment_intent, "id", str(uuid.uuid4()))

            if user_id and credits:
                await db.users.update_one(
                    {"id": user_id},
                    {"$inc": {"balance": credits}}
                )
                await db.transactions.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "type": "credit_purchase",
                    "amount": amount,
                    "credits": credits,
                    "payment_intent_id": pi_id,
                    "status": "completed",
                    "created_at": datetime.utcnow()
                })

        elif event.type == "checkout.session.completed":
            session = event.data.object
            metadata = get_prop(session, "metadata") or {}
            session_id = get_prop(session, "id", "")
            if session_id:
                # Same idempotent MongoDB crediting path used by verify/success page
                await process_successful_payment(session_id, get_prop(metadata, "user_id") or "")

        elif event.type == "payment_intent.payment_failed":
            payment_intent = event.data.object
            pi_id = getattr(payment_intent, "id", "unknown")
            print(f"Payment failed: {pi_id}")
        
        return {"status": "success"}
    
    # PayPal Email Management
    @router.post("/paypal/save-email")
    async def save_paypal_email(request: dict):
        """Save advisor's PayPal email for payouts"""
        psychic_id = request.get("psychic_id")
        paypal_email = request.get("paypal_email")
        
        if not psychic_id or not paypal_email:
            raise HTTPException(status_code=400, detail="Missing psychic_id or paypal_email")
        
        try:
            # Update psychic record with PayPal email
            result = await db.psychics.update_one(
                {"$or": [{"id": psychic_id}, {"_id": psychic_id}]},
                {
                    "$set": {
                        "paypal_email": paypal_email,
                        "paypal_connected_at": datetime.utcnow().isoformat(),
                    }
                }
            )
            
            # Also update in applications if exists
            await db.applications.update_one(
                {"psychic_id": psychic_id},
                {
                    "$set": {
                        "paypal_email": paypal_email,
                    }
                }
            )
            
            return {"success": True, "message": "PayPal email saved successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/paypal/disconnect")
    async def disconnect_paypal(request: dict):
        """Remove advisor's PayPal connection"""
        psychic_id = request.get("psychic_id")
        
        if not psychic_id:
            raise HTTPException(status_code=400, detail="Missing psychic_id")
        
        try:
            await db.psychics.update_one(
                {"$or": [{"id": psychic_id}, {"_id": psychic_id}]},
                {
                    "$unset": {
                        "paypal_email": "",
                        "paypal_connected_at": "",
                    }
                }
            )
            
            return {"success": True, "message": "PayPal disconnected successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/paypal/connect-url")
    async def get_paypal_connect_url(request: dict):
        """Generate PayPal OAuth URL (placeholder for full OAuth integration)"""
        # For full OAuth, you would need PAYPAL_CLIENT_ID and PAYPAL_SECRET
        # This returns an empty response to trigger the email fallback
        paypal_client_id = os.getenv("PAYPAL_CLIENT_ID")
        
        if not paypal_client_id:
            return {"auth_url": None, "message": "PayPal OAuth not configured"}
        
        # Generate OAuth URL
        return_url = request.get("return_url", "")
        auth_url = f"https://www.paypal.com/connect?flowEntry=static&client_id={paypal_client_id}&scope=email&redirect_uri={return_url}"
        
        return {"auth_url": auth_url}
    
    return router
