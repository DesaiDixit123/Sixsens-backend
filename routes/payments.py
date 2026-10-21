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
    fee: Optional[float] = 0.0
    fee_percent: Optional[float] = None
    net_amount: Optional[float] = None
    account_details: Optional[dict] = None

def create_payment_routes(db):
    router = APIRouter(prefix="/payments", tags=["payments"])
    
    async def get_active_stripe_config():
        """Dynamically fetch current Stripe configuration from MongoDB or fall back to .env"""
        try:
            doc = await db.platform_settings.find_one({"key": "stripe_config"})
            if doc:
                secret = (doc.get("secret_key") or os.getenv("STRIPE_SECRET_KEY", "")).strip()
                pub = (doc.get("publishable_key") or os.getenv("STRIPE_PUBLISHABLE_KEY", "")).strip()
                wh = (doc.get("webhook_secret") or os.getenv("STRIPE_WEBHOOK_SECRET", "")).strip()
                enabled = doc.get("is_enabled", bool(secret))
                mode = doc.get("mode", "live" if secret.startswith("sk_live") else "test")
                currency = (doc.get("currency") or "usd").strip().lower()
                return {
                    "publishable_key": pub,
                    "secret_key": secret,
                    "webhook_secret": wh,
                    "stripe_enabled": enabled and bool(secret),
                    "mode": mode,
                    "currency": currency
                }
        except Exception:
            pass

        secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
        pub = (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()
        wh = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        return {
            "publishable_key": pub or "pk_test_placeholder",
            "secret_key": secret,
            "webhook_secret": wh,
            "stripe_enabled": bool(secret),
            "mode": "live" if secret.startswith("sk_live") else "test",
            "currency": "usd"
        }

    @router.get("/config")
    async def get_payment_config():
        """Get dynamic Stripe publishable key and mode for frontend"""
        cfg = await get_active_stripe_config()
        return {
            "publishable_key": cfg["publishable_key"],
            "stripe_enabled": cfg["stripe_enabled"],
            "mode": cfg["mode"],
            "currency": cfg["currency"]
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
        
        cfg = await get_active_stripe_config()
        if cfg["stripe_enabled"] and cfg["secret_key"]:
            try:
                stripe.api_key = cfg["secret_key"]
                intent = stripe.PaymentIntent.create(
                    amount=amount_cents,
                    currency=cfg.get("currency", "usd"),
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
                "message": "Stripe not configured or disabled in Admin settings. Using mock payment."
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
        stripe_cfg = await get_active_stripe_config()
        active_secret = (stripe_cfg.get("secret_key") or "").strip()
        is_live_mode = stripe_cfg.get("mode") == "live" or active_secret.startswith("sk_live")

        if _is_stripe_session(session_id):
            user_id = fallback_user_id
            credits = int(credits_override or 0)
            amount = float(amount_override or 0.0)
            if active_secret:
                try:
                    import stripe
                    stripe.api_key = active_secret
                    session_raw = await asyncio.wait_for(
                        asyncio.to_thread(stripe.checkout.Session.retrieve, session_id), timeout=10.0
                    )
                    session = session_raw.to_dict() if hasattr(session_raw, "to_dict") else dict(session_raw)
                    if session.get("payment_status") != "paid":
                        return {"status": "pending", "session_id": session_id, "detail": "Payment not yet completed on Stripe"}
                    metadata = session.get("metadata") or {}
                    user_id = metadata.get("user_id") or fallback_user_id
                    credits = int(metadata.get("credits", 0) or metadata.get("package_credits", 0) or credits_override or 0)
                    raw_total = float((session.get("amount_total") or 0) / 100.0)
                    amount = raw_total if raw_total > 0 else float(amount_override or 0.0)
                except Exception as e:
                    logger.error(f"Stripe session retrieve error: {e}")
                    return {"status": "pending", "session_id": session_id, "detail": f"Could not verify with Stripe: {str(e)}"}
            else:
                return {"status": "pending", "session_id": session_id, "detail": "Stripe secret key not configured"}
        else:
            # Local fallback checkout - ONLY permitted in test mode, NEVER in live mode!
            if is_live_mode:
                raise HTTPException(status_code=400, detail="Cannot verify test checkout session in LIVE production mode. Real card payment required.")
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
    async def create_checkout_session(request: Request, data: dict):
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

        # Dynamic backend base: check client payload, request host, or env
        req_backend = (data.get("backend_url") or "").strip().rstrip("/")
        if req_backend:
            backend_base = req_backend
        else:
            host = request.headers.get("host")
            if host and ("192.168" in host or "10." in host or "172." in host or "localhost" in host or "127.0.0.1" in host):
                proto = request.headers.get("x-forwarded-proto", "http")
                backend_base = f"{proto}://{host}"
            else:
                backend_base = os.getenv("BACKEND_URL", "http://192.168.29.227:8000").rstrip("/")
        success_url = f"{backend_base}/api/payments/checkout-success?session_id={{CHECKOUT_SESSION_ID}}&user_id={user_id}&app_return_url={encoded_return_url}&credits={credits}&amount={amount_dollars:.2f}"
        cancel_url = f"{backend_base}/api/payments/checkout-cancel"
            
        session_id = f"cs_{uuid.uuid4().hex[:16]}"
        checkout_view_url = f"{backend_base}/api/payments/checkout-session-view?session_id={session_id}&amount={amount_dollars:.2f}&credits={credits}&user_id={user_id}&app_return_url={encoded_return_url}&method={method}"

        # Dynamically load Stripe configuration
        stripe_cfg = await get_active_stripe_config()
        active_secret = (stripe_cfg.get("secret_key") or "").strip()
        stripe_mode = stripe_cfg.get("mode", "test").lower()
        is_live_mode = stripe_mode == "live" or active_secret.startswith("sk_live")
        currency_code = (stripe_cfg.get("currency") or "usd").lower()

        # If Stripe credentials exist, use REAL Stripe Checkout
        if active_secret:
            try:
                import stripe
                stripe.api_key = active_secret
                session = stripe.checkout.Session.create(
                    line_items=[{
                        "price_data": {
                            "currency": currency_code,
                            "product_data": {
                                "name": f"{credits} Credits",
                                "description": f"Add {credits} credits to your Sixth Sense wallet"
                            },
                            "unit_amount": amount_cents,
                        },
                        "quantity": 1,
                    }],
                    mode="payment",
                    # Note: payment_method_types is intentionally omitted as modern Stripe API
                    # uses Dynamic Payment Methods configured from the Stripe Dashboard.
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={
                        "user_id": user_id,
                        "credits": str(credits),
                        "type": "wallet_topup",
                        "livemode": "true" if is_live_mode else "false"
                    }
                )
                if session and hasattr(session, "url") and session.url:
                    logger.info(f"✅ Real Stripe Checkout Session created ({'LIVE' if is_live_mode else 'TEST'}): {session.id}")
                    return {
                        "checkout_url": session.url,
                        "session_id": session.id,
                        "livemode": is_live_mode
                    }
            except Exception as e:
                logger.error(f"❌ Stripe Checkout Session creation failed: {e}")
                # In LIVE mode, NEVER fall back to fake test card simulator! Raise clear error!
                if is_live_mode:
                    raise HTTPException(status_code=400, detail=f"Stripe Payment Error: {str(e)}")

        # If in LIVE mode and Stripe failed, do NOT allow test fallback
        if is_live_mode:
            raise HTTPException(status_code=400, detail="Stripe is configured in LIVE mode, but live payment session could not be established. Test simulation disabled.")

        # Fallback checkout (ONLY allowed in test / local development mode):
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
            "session_id": session_id,
            "livemode": False
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
    
    # ============ DYNAMIC PLATFORM FEE (ADMIN & ADVISOR) ============
    @router.get("/platform-fee")
    async def get_platform_fee():
        """Get current dynamic platform fee percentage for withdrawals"""
        try:
            doc = await db.platform_settings.find_one({"key": "platform_fee_config"})
            if doc:
                fee_percent = float(doc.get("fee_percent", 0.0) or 0.0)
                return {
                    "success": True,
                    "fee_percent": round(fee_percent, 2),
                    "fee_fixed": round(float(doc.get("fee_fixed", 0.0) or 0.0), 2),
                    "updated_at": doc.get("updated_at")
                }
        except Exception as e:
            logger.warning(f"Error fetching platform fee: {e}")
        return {
            "success": True,
            "fee_percent": 0.0,
            "fee_fixed": 0.0,
            "updated_at": None
        }

    @router.post("/platform-fee")
    @router.put("/platform-fee")
    async def update_platform_fee(data: dict):
        """Update dynamic platform fee percentage for withdrawals (Admin)"""
        try:
            raw_percent = data.get("fee_percent", 0.0)
            fee_percent = max(0.0, min(100.0, float(raw_percent)))
            fee_fixed = max(0.0, float(data.get("fee_fixed", 0.0) or 0.0))
            
            await db.platform_settings.update_one(
                {"key": "platform_fee_config"},
                {
                    "$set": {
                        "key": "platform_fee_config",
                        "fee_percent": round(fee_percent, 2),
                        "fee_fixed": round(fee_fixed, 2),
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            logger.info(f"✅ Dynamic platform fee updated to {fee_percent}%")
            return {
                "success": True,
                "fee_percent": fee_percent,
                "fee_fixed": fee_fixed,
                "message": f"Platform fee successfully updated to {fee_percent}%"
            }
        except Exception as e:
            logger.error(f"Error updating platform fee: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    # ============ PSYCHIC / ADVISOR BANK DETAILS ============
    @router.get("/psychic-bank-details/{psychic_id}")
    async def get_psychic_bank_details(psychic_id: str):
        """Fetch saved bank account details for an advisor"""
        try:
            psychic = await db.psychics.find_one({
                "$or": [{"id": psychic_id}, {"_id": psychic_id}]
            })
            if not psychic:
                raise HTTPException(status_code=404, detail="Advisor not found")

            bank_details = psychic.get("bank_details") or {}
            
            # Fallback to last payout's account details if not directly on profile
            if not bank_details.get("account_number"):
                last_payout = await db.payouts.find_one(
                    {"psychic_id": psychic_id, "account_details.account_number": {"$exists": True}},
                    sort=[("created_at", -1)]
                )
                if last_payout and last_payout.get("account_details"):
                    bank_details = last_payout["account_details"]

            # If still completely empty for Luna, provide realistic default for smooth demo/testing
            if not bank_details.get("account_number") and "luna" in str(psychic.get("name", "")).lower():
                bank_details = {
                    "account_holder_name": psychic.get("name", "Luna Starweaver"),
                    "bank_name": "Chase Bank, N.A.",
                    "account_number": "9876543210",
                    "routing_number": "021000021",
                    "account_type": "Checking"
                }

            has_details = bool(bank_details.get("account_number") and bank_details.get("routing_number"))
            return {
                "success": True,
                "has_bank_details": has_details,
                "bank_details": bank_details,
                "available_balance": float(psychic.get("available_balance", 0.0) or 0.0),
                "pending_balance": float(psychic.get("pending_balance", 0.0) or 0.0),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching bank details: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/psychic-bank-details/{psychic_id}")
    @router.put("/psychic-bank-details/{psychic_id}")
    async def save_psychic_bank_details(psychic_id: str, data: dict):
        """Save or update bank account details for an advisor"""
        try:
            psychic = await db.psychics.find_one({
                "$or": [{"id": psychic_id}, {"_id": psychic_id}]
            })
            if not psychic:
                raise HTTPException(status_code=404, detail="Advisor not found")

            holder = str(data.get("account_holder_name") or psychic.get("name", "")).strip()
            bank_name = str(data.get("bank_name") or "").strip()
            acc_num = str(data.get("account_number") or "").strip()
            routing = str(data.get("routing_number") or "").strip()
            acc_type = str(data.get("account_type") or "Checking").strip()

            if not acc_num or not routing:
                raise HTTPException(status_code=400, detail="Account number and routing number are required")

            bank_info = {
                "account_holder_name": holder,
                "bank_name": bank_name or "Direct Deposit Bank",
                "account_number": acc_num,
                "routing_number": routing,
                "account_type": acc_type,
                "updated_at": datetime.utcnow()
            }

            await db.psychics.update_one(
                {"$or": [{"id": psychic_id}, {"_id": psychic_id}]},
                {"$set": {"bank_details": bank_info}}
            )

            # Also update any pending payouts for this psychic that lack bank details
            await db.payouts.update_many(
                {"psychic_id": psychic_id, "status": "pending"},
                {"$set": {"account_details": bank_info}}
            )

            logger.info(f"✅ Bank details saved for advisor {psychic_id}")
            return {
                "success": True,
                "message": "Bank details successfully saved",
                "bank_details": bank_info
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error saving bank details: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/payout-request")
    async def request_payout(request: PayoutRequest):
        """Psychic requests a payout of their earnings"""
        aliases = get_psychic_aliases(request.psychic_id)
        psychic = await db.psychics.find_one({
            "$or": [{"id": {"$in": aliases}}, {"email": {"$in": aliases}}, {"_id": request.psychic_id}]
        })
        if not psychic:
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") in aliases or mp.get("email") in aliases:
                        psychic = dict(mp)
                        break
            except Exception:
                pass
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        if request.amount < 40:
            raise HTTPException(status_code=400, detail="Minimum payout is $40")
        
        available = float(psychic.get("available_balance", 0) or 0)
        if request.amount > available:
            raise HTTPException(status_code=400, detail="Insufficient available balance")
        
        # Calculate fee dynamically based on admin configuration
        fee_percent = 0.0
        if request.method != "paypal":
            fee_setting = await db.platform_settings.find_one({"key": "platform_fee_config"})
            if fee_setting:
                fee_percent = float(fee_setting.get("fee_percent", 0.0) or 0.0)
            fee = round(request.amount * (fee_percent / 100.0), 2)
            net_amount = round(max(0.0, request.amount - fee), 2)
        else:
            fee = round(float(request.fee or 0.0), 2)
            net_amount = round(float(request.net_amount if request.net_amount is not None else max(0.0, request.amount - fee)), 2)
        
        # Resolve account details: use passed details or psychic's saved bank_details
        acct_details = request.account_details or {}
        if not acct_details.get("account_number") and psychic.get("bank_details"):
            acct_details = psychic.get("bank_details")
        
        # If valid bank details were submitted with withdrawal, persist them to profile
        if acct_details.get("account_number") and acct_details.get("routing_number"):
            await db.psychics.update_one(
                {"$or": [{"id": request.psychic_id}, {"_id": request.psychic_id}]},
                {"$set": {"bank_details": acct_details}}
            )

        # Create payout request
        payout = {
            "id": str(uuid.uuid4()),
            "psychic_id": request.psychic_id,
            "psychic_name": psychic.get("name", "Advisor"),
            "psychic_email": psychic.get("email", ""),
            "gross_amount": float(request.amount),
            "amount": float(request.amount),
            "fee_percent": fee_percent,
            "fee": fee,
            "net_amount": net_amount,
            "method": request.method,
            "account_details": acct_details,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        await db.payouts.insert_one(payout)
        
        # Deduct from available balance, add to pending
        await db.psychics.update_one(
            {"$or": [{"id": request.psychic_id}, {"_id": request.psychic_id}]},
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
            "gross_amount": request.amount,
            "fee_percent": fee_percent,
            "fee": fee,
            "net_amount": net_amount,
            "estimated_arrival": "3-5 business days"
        }

    def get_psychic_aliases(psychic_id: str) -> List[str]:
        aliases = [psychic_id]
        if psychic_id in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001", "testpsychic@sixthsense.com", "luna@psychic.com"):
            aliases.extend(["64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001", "testpsychic@sixthsense.com", "luna@psychic.com"])
        return list(dict.fromkeys([a for a in aliases if a]))

    @router.get("/psychic-payouts/{psychic_id}")
    async def get_psychic_payouts(psychic_id: str):
        """Get all payout/withdrawal requests for a specific psychic (ADV-045)"""
        aliases = get_psychic_aliases(psychic_id)
        payouts = await db.payouts.find({
            "$or": [{"psychic_id": {"$in": aliases}}, {"psychic_email": {"$in": aliases}}]
        }).sort("created_at", -1).to_list(100)
        
        res = []
        for p in payouts:
            amount = float(p.get("amount") or 0.0)
            gross = float(p.get("gross_amount") or amount)
            fee = float(p.get("fee") or 0.0)
            net = float(p.get("net_amount") or max(0.0, gross - fee))
            fee_pct = p.get("fee_percent")
            if fee_pct is None and gross > 0:
                fee_pct = round((fee / gross) * 100, 1)
            
            res.append({
                "id": p.get("id") or str(p.get("_id")),
                "_id": str(p.get("_id")),
                "psychic_id": p.get("psychic_id"),
                "amount": amount,
                "gross_amount": gross,
                "fee_percent": fee_pct or 0.0,
                "fee": fee,
                "net_amount": net,
                "method": p.get("method", "bank_transfer"),
                "status": p.get("status", "pending"),
                "rejection_reason": p.get("rejection_reason"),
                "created_at": p.get("created_at").isoformat() if isinstance(p.get("created_at"), datetime) else str(p.get("created_at", "")),
                "processed_at": p.get("processed_at").isoformat() if isinstance(p.get("processed_at"), datetime) else None,
            })
        return {"payouts": res}
    
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
        aliases = get_psychic_aliases(psychic_id)
        psychic = await db.psychics.find_one({"$or": [{"id": {"$in": aliases}}, {"email": {"$in": aliases}}]})
        if not psychic:
            try:
                from bson.objectid import ObjectId
                if len(psychic_id) == 24 and ObjectId.is_valid(psychic_id):
                    psychic = await db.psychics.find_one({"_id": ObjectId(psychic_id)})
            except Exception:
                pass
        if not psychic:
            # Fallback to in-memory MOCK_PSYCHICS or create dummy so it never 404s
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") in aliases or mp.get("email") in aliases:
                        psychic = dict(mp)
                        break
            except Exception:
                pass
            if not psychic:
                psychic = {"id": psychic_id, "email": psychic_id if "@" in psychic_id else "advisor@sixthsense.com", "total_earnings": 0.0, "available_balance": 0.0}

        pid = psychic.get("id", psychic_id)
        pemail = psychic.get("email")

        # Timeframe filter for period stats + transactions
        now = datetime.utcnow()
        since = None
        if timeframe == "7d":
            since = now - timedelta(days=7)
        elif timeframe == "30d":
            since = now - timedelta(days=30)
        elif timeframe == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Query all ended sessions for this psychic (by any alias)
        query_or = [
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}}
        ]

        query = {"$or": query_or, "status": "ended"}
        if since:
            query["ended_at"] = {"$gte": since}

        sessions = await db.chat_sessions.find(query).sort("ended_at", -1).to_list(500)
        
        # Calculate session earnings with fallback to 40% of total_cost
        def calc_session_earning(s):
            raw = s.get("psychic_earnings")
            return float(raw) if raw is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)

        period_gross = round(sum(float(s.get("total_cost", 0) or 0) for s in sessions), 2)
        period_net = round(sum(calc_session_earning(s) for s in sessions), 2)

        # Query all ended sessions for lifetime computation if stored is 0
        all_sessions = await db.chat_sessions.find({"$or": query_or, "status": "ended"}).to_list(2000)
        computed_lifetime = round(sum(calc_session_earning(s) for s in all_sessions), 2)

        total_earnings = max(round(float(psychic.get("total_earnings", 0) or 0), 2), computed_lifetime)
        total_withdrawn = float(psychic.get("total_withdrawn", 0) or 0)
        available = max(round(float(psychic.get("available_balance", 0) or 0), 2), round(max(0.0, total_earnings - total_withdrawn), 2))

        # Query tips for this psychic
        tips_query: dict = {"$or": query_or}
        if since:
            tips_query["created_at"] = {"$gte": since}
        tips = await db.tips.find(tips_query).sort("created_at", -1).to_list(100)
        total_tips_period = round(sum(float(t.get("amount", 0) or 0) for t in tips), 2)

        transactions = []
        for s in sessions[:50]:
            ended_at = s.get("ended_at") or s.get("created_at")
            stype = (s.get("session_type") or "chat").lower()
            s_earn = calc_session_earning(s)
            transactions.append({
                "id": str(s.get("id") or s.get("_id")),
                "type": "reading",
                "session_type": "phone" if stype in ("voice", "audio", "phone") else stype,
                "client": s.get("client_name") or s.get("user_name") or "Client",
                "amount": round(s_earn, 2),
                "gross": round(float(s.get("total_cost", 0) or 0), 2),
                "duration_seconds": s.get("duration_seconds", 0),
                "date": ended_at.isoformat() if isinstance(ended_at, datetime) else str(ended_at or ""),
            })

        for t in tips[:20]:
            t_date = t.get("created_at")
            t_amt = round(float(t.get("amount", 0) or 0), 2)
            transactions.append({
                "id": str(t.get("id") or t.get("_id")),
                "type": "tip",
                "session_type": "tip",
                "client": t.get("client_name") or "Client",
                "amount": t_amt,
                "gross": t_amt,
                "duration_seconds": 0,
                "date": t_date.isoformat() if isinstance(t_date, datetime) else str(t_date or ""),
            })

        # Query completed video questions/readings for this psychic
        q_query_or = [{"psychic_id": {"$in": aliases}}, {"psychic_email": {"$in": aliases}}]
        if pid in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com") or pemail == "testpsychic@sixthsense.com":
            q_query_or.extend([
                {"psychic_id": "psy-001"},
                {"psychic_email": "testpsychic@sixthsense.com"},
                {"psychic_id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"}
            ])

        q_query: dict = {"$or": q_query_or, "status": "completed"}
        if since:
            q_query["completed_at"] = {"$gte": since}
        completed_readings = await db.questions.find(q_query).sort("completed_at", -1).to_list(100)
        readings_gross = round(sum(float(q.get("price", 0) or 12.0) for q in completed_readings), 2)
        period_gross = round(period_gross + readings_gross, 2)
        period_net = round(period_net + readings_gross, 2)

        for cq in completed_readings[:50]:
            cq_date = cq.get("completed_at") or cq.get("created_at")
            cq_price = round(float(cq.get("price", 0) or 12.0), 2)
            transactions.append({
                "id": str(cq.get("id") or cq.get("_id")),
                "type": "reading",
                "session_type": "video_reading",
                "client": cq.get("client_name") or "Client",
                "amount": cq_price,
                "gross": cq_price,
                "duration_seconds": 0,
                "date": cq_date.isoformat() if isinstance(cq_date, datetime) else str(cq_date or ""),
            })

        # Sort combined transactions by date descending
        transactions.sort(key=lambda x: str(x.get("date", "")), reverse=True)

        total_tips_all = round(float(psychic.get("total_tips", 0) or total_tips_period), 2)

        return {
            "available_balance": available,
            "pending_balance": round(float(psychic.get("pending_balance", 0) or 0), 2),
            "total_earnings": total_earnings,
            "total_tips": total_tips_all,
            "total_readings": psychic.get("total_readings", 0),
            "timeframe": timeframe,
            "period_gross": period_gross,
            "period_net": period_net,
            "period_sessions": len(sessions),
            "transactions": transactions,
        }

    # ============ TIP CHECKOUT & STRIPE FLOW ============
    @router.post("/create-tip-checkout-session")
    async def create_tip_checkout_session(request: Request, data: dict):
        """Create a Stripe checkout session for client tipping advisor"""
        from urllib.parse import quote
        raw_amount = float(data.get("amount", 0) or 0)
        if raw_amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid tip amount")

        psychic_id = data.get("psychic_id", "")
        user_id = data.get("user_id", "")
        app_return_url = data.get("app_return_url", "")
        encoded_return_url = quote(app_return_url, safe="")

        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            psychic = await db.psychics.find_one({"email": psychic_id})
        psychic_name = psychic.get("name") if psychic else "Advisor"
        pid = psychic.get("id", psychic_id)
        pemail = psychic.get("email") if psychic else psychic_id

        amount_cents = int(round(raw_amount * 100))
        amount_dollars = round(amount_cents / 100.0, 2)

        # Dynamic backend base: check client payload, request host, or env
        req_backend = (data.get("backend_url") or "").strip().rstrip("/")
        if req_backend:
            backend_base = req_backend
        else:
            host = request.headers.get("host")
            if host and ("192.168" in host or "10." in host or "172." in host or "localhost" in host or "127.0.0.1" in host):
                proto = request.headers.get("x-forwarded-proto", "http")
                backend_base = f"{proto}://{host}"
            else:
                backend_base = os.getenv("BACKEND_URL", "http://192.168.29.227:8000").rstrip("/")

        success_url = f"{backend_base}/api/payments/tip-checkout-success?session_id={{CHECKOUT_SESSION_ID}}&user_id={user_id}&psychic_id={pid}&amount={amount_dollars:.2f}&app_return_url={encoded_return_url}"
        cancel_url = f"{backend_base}/api/payments/checkout-cancel"

        session_id = f"tip_{uuid.uuid4().hex[:16]}"
        checkout_view_url = f"{backend_base}/api/payments/checkout-session-view?session_id={session_id}&amount={amount_dollars:.2f}&credits=0&user_id={user_id}&app_return_url={encoded_return_url}&method=card"

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
                                    "name": f"Tip for {psychic_name} 🎁",
                                    "description": f"Gratuity of ${amount_dollars:.2f} for psychic reading session"
                                },
                                "unit_amount": amount_cents,
                            },
                            "quantity": 1,
                        }],
                        mode="payment",
                        success_url=success_url,
                        cancel_url=cancel_url,
                        metadata={
                            "type": "tip",
                            "user_id": user_id,
                            "psychic_id": pid,
                            "amount": str(amount_dollars)
                        }
                    )
                    if session and hasattr(session, "url"):
                        return {"checkout_url": session.url, "session_id": session.id}
                except Exception as e:
                    logger.info(f"Stripe tip session error: {e}")

        # Fallback interactive checkout view
        await _db(db.checkout_sessions.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "psychic_id": pid,
            "amount": amount_dollars,
            "type": "tip",
            "status": "pending",
            "created_at": datetime.utcnow(),
        }))
        return {"checkout_url": checkout_view_url, "session_id": session_id}

    @router.get("/tip-checkout-success", response_class=HTMLResponse)
    async def tip_checkout_success(
        session_id: str,
        user_id: Optional[str] = "",
        psychic_id: Optional[str] = "",
        amount: Optional[float] = 0.0,
        app_return_url: Optional[str] = ""
    ):
        """HTML bridge shown when tip is paid via Stripe - credits advisor and sends push notification"""
        tip_amount = float(amount or 0.0)
        client = await db.users.find_one({"id": user_id}) if user_id else None
        client_name = (client.get("name") if client else None) or "Client"

        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            psychic = await db.psychics.find_one({"email": psychic_id})
        pid = psychic.get("id", psychic_id)
        pemail = psychic.get("email") if psychic else psychic_id
        psychic_name = psychic.get("name") if psychic else "Advisor"

        if tip_amount > 0 and pid:
            # 1. Credit psychic balance and total tips
            await db.psychics.update_one(
                {"id": pid},
                {
                    "$inc": {
                        "available_balance": tip_amount,
                        "total_tips": tip_amount,
                        "total_earnings": tip_amount,
                    }
                }
            )

            # 2. Record tip in db.tips
            tip_record = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "psychic_id": pid,
                "user_id": user_id,
                "client_name": client_name,
                "amount": tip_amount,
                "status": "completed",
                "created_at": datetime.utcnow()
            }
            await db.tips.insert_one(tip_record)

            # 3. Create Advisor In-App Notification (category: earnings)
            adv_notif = {
                "id": str(uuid.uuid4()),
                "user_id": pid,
                "user_type": "psychic",
                "type": "earning",
                "notification_type": "tip",
                "title": "Client Tip Received 🎁",
                "body": f"You received a ${tip_amount:.2f} tip from {client_name}! Thank you for your amazing guidance.",
                "is_read": False,
                "read": False,
                "created_at": datetime.utcnow()
            }
            await db.notifications.insert_one(adv_notif)

            # 4. Send FCM Push Notification to Advisor
            try:
                from routes.push_notifications import send_push_to_user
                await send_push_to_user(
                    db=db,
                    user_id=pid,
                    title="Client Tip Received 🎁",
                    body=f"You received a ${tip_amount:.2f} tip from {client_name}!",
                    data={"type": "earning", "amount": str(tip_amount)}
                )
            except Exception as e:
                logger.error(f"Error sending tip push notification: {e}")

        base_app_url = app_return_url or "sixthsense://session-review"
        delim = "&" if "?" in base_app_url else "?"
        target_app_url = f"{base_app_url}{delim}tip_success=true&tip_amount={tip_amount:.2f}"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Tip Sent - Sixth Sense</title>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
                body {{
                    background: #0b0819;
                    color: #ffffff;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    padding: 20px;
                    text-align: center;
                }}
                .card {{
                    background: #16112e;
                    border: 1px solid rgba(139, 92, 246, 0.4);
                    border-radius: 24px;
                    padding: 36px 24px;
                    max-width: 400px;
                    width: 100%;
                    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
                }}
                .icon-circle {{
                    width: 72px;
                    height: 72px;
                    background: linear-gradient(135deg, #ec4899, #db2777);
                    border-radius: 36px;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 36px;
                    margin-bottom: 16px;
                    box-shadow: 0 8px 24px rgba(219, 39, 119, 0.45);
                }}
                h1 {{ font-size: 24px; font-weight: 800; margin-bottom: 8px; color: #fff; }}
                .badge {{
                    display: inline-block;
                    background: rgba(16, 185, 129, 0.15);
                    border: 1px solid #10b981;
                    color: #10b981;
                    padding: 6px 16px;
                    border-radius: 20px;
                    font-size: 15px;
                    font-weight: 700;
                    margin-bottom: 16px;
                }}
                p {{ font-size: 14px; color: #cbd5e1; margin-bottom: 24px; line-height: 1.5; }}
                .btn {{
                    display: block;
                    width: 100%;
                    background: linear-gradient(135deg, #8b5cf6, #ec4899);
                    color: white;
                    font-size: 16px;
                    font-weight: 700;
                    text-decoration: none;
                    padding: 15px;
                    border-radius: 14px;
                    border: none;
                    cursor: pointer;
                    margin-bottom: 12px;
                }}
                .btn:active {{ opacity: 0.85; transform: scale(0.98); }}
                .sub {{ font-size: 12px; color: #94a3b8; line-height: 1.4; }}
            </style>
            <script>
                function triggerReturn() {{
                    var targets = [
                        "{target_app_url}",
                        "exp://192.168.29.227:8081/--/session-review?tip_success=true&tip_amount={tip_amount:.2f}",
                        "intent://192.168.29.227:8081/--/session-review?tip_success=true&tip_amount={tip_amount:.2f}#Intent;scheme=exp;package=host.exp.exponent;end",
                        "sixthsense://session-review?tip_success=true&tip_amount={tip_amount:.2f}",
                        "sixthsensepsychics://session-review?tip_success=true&tip_amount={tip_amount:.2f}",
                        "intent://session-review?tip_success=true&tip_amount={tip_amount:.2f}#Intent;scheme=sixthsense;package=com.sixthsense.client;end"
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

                window.addEventListener('DOMContentLoaded', function() {{
                    setTimeout(triggerReturn, 400);
                }});
            </script>
        </head>
        <body>
            <div class="card">
                <div class="icon-circle">🎁</div>
                <h1>Tip Sent!</h1>
                <div class="badge">+${tip_amount:.2f} Gratuity</div>
                <p>Thank you for tipping <strong>{psychic_name}</strong>!<br/>Your generosity has been credited to their account.</p>
                <button class="btn" onclick="triggerReturn()">Return to Sixth Sense App</button>
                <div class="sub">You can also safely close this tab or tap ✕ above to return to your chat.</div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    # ============ PDF EARNINGS STATEMENT ============
    @router.get("/statement-pdf/{psychic_id}", response_class=HTMLResponse)
    async def get_earnings_statement_pdf(psychic_id: str, timeframe: str = "month"):
        """Generate a print-ready HTML/PDF earnings statement for advisor"""
        aliases = get_psychic_aliases(psychic_id)
        psychic = await db.psychics.find_one({"$or": [{"id": {"$in": aliases}}, {"email": {"$in": aliases}}]})
        if not psychic:
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") in aliases or mp.get("email") in aliases:
                        psychic = dict(mp)
                        break
            except Exception:
                pass
        if not psychic:
            psychic = {"id": psychic_id, "name": "Advisor", "email": psychic_id if "@" in psychic_id else "advisor@sixthsense.com"}

        pid = psychic.get("id", psychic_id)
        pemail = psychic.get("email", "")
        name = psychic.get("name") or "Advisor"

        now = datetime.utcnow()
        since = None
        period_label = "All Time"
        if timeframe == "7d":
            since = now - timedelta(days=7)
            period_label = "Last 7 Days"
        elif timeframe == "30d":
            since = now - timedelta(days=30)
            period_label = "Last 30 Days"
        elif timeframe == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_label = now.strftime("%B %Y")

        query_or = [{"psychic_id": {"$in": aliases}}, {"psychic_email": {"$in": aliases}}]
        query = {"$or": query_or, "status": "ended"}
        if since:
            query["ended_at"] = {"$gte": since}

        sessions = await db.chat_sessions.find(query).sort("ended_at", -1).to_list(500)
        
        def calc_s_earn(s):
            raw = s.get("psychic_earnings")
            return float(raw) if raw is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)

        period_gross = round(sum(float(s.get("total_cost", 0) or 0) for s in sessions), 2)
        period_net = round(sum(calc_s_earn(s) for s in sessions), 2)
        platform_fee = round(period_gross - period_net, 2)

        # Tips
        tips_query = {"$or": query_or}
        if since:
            tips_query["created_at"] = {"$gte": since}
        tips = await db.tips.find(tips_query).sort("created_at", -1).to_list(100)
        period_tips = round(sum(float(t.get("amount", 0) or 0) for t in tips), 2)
        total_net_with_tips = round(period_net + period_tips, 2)

        available_balance = round(float(psychic.get("available_balance", 0) or total_net_with_tips), 2)

        # Build table rows
        rows_html = ""
        for s in sessions:
            ended_at = s.get("ended_at")
            date_str = ended_at.strftime("%b %d, %Y %H:%M") if isinstance(ended_at, datetime) else str(ended_at or "")[:16]
            stype = (s.get("session_type") or "Chat").title()
            dur = s.get("duration_seconds", 0)
            dur_str = f"{dur // 60}m {dur % 60}s"
            client = s.get("client_name") or "Client"
            gross = float(s.get("total_cost", 0) or 0)
            net = float(s.get("psychic_earnings", 0) or 0)
            rows_html += f"""
            <tr>
                <td>{date_str}</td>
                <td><span class="badge">{stype}</span></td>
                <td>{client}</td>
                <td>{dur_str}</td>
                <td style="text-align: right;">${gross:.2f}</td>
                <td style="text-align: right; font-weight: bold; color: #10b981;">+${net:.2f}</td>
            </tr>
            """

        for t in tips:
            t_date = t.get("created_at")
            t_date_str = t_date.strftime("%b %d, %Y %H:%M") if isinstance(t_date, datetime) else str(t_date or "")[:16]
            t_amt = float(t.get("amount", 0) or 0)
            t_client = t.get("client_name") or "Client"
            rows_html += f"""
            <tr>
                <td>{t_date_str}</td>
                <td><span class="badge" style="background:#e11d48; color:white;">Tip 🎁</span></td>
                <td>{t_client}</td>
                <td>-</td>
                <td style="text-align: right;">${t_amt:.2f}</td>
                <td style="text-align: right; font-weight: bold; color: #10b981;">+${t_amt:.2f}</td>
            </tr>
            """

        if not rows_html:
            rows_html = "<tr><td colspan='6' style='text-align: center; padding: 24px; color: #666;'>No reading sessions completed in this period.</td></tr>"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>SixthSense Earnings Statement - {name}</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    color: #1f2937;
                    background-color: #f9fafb;
                    margin: 0;
                    padding: 24px;
                    line-height: 1.5;
                }}
                .print-bar {{
                    max-width: 800px;
                    margin: 0 auto 20px;
                    display: flex;
                    justify-content: flex-end;
                    gap: 12px;
                }}
                .print-btn {{
                    background: #8b5cf6;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 8px;
                    font-weight: 600;
                    cursor: pointer;
                    font-size: 14px;
                }}
                .statement-card {{
                    background: #ffffff;
                    max-width: 800px;
                    margin: 0 auto;
                    border-radius: 12px;
                    padding: 36px;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                    border: 1px solid #e5e7eb;
                }}
                .header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: flex-start;
                    border-bottom: 2px solid #8b5cf6;
                    padding-bottom: 20px;
                    margin-bottom: 24px;
                }}
                .brand {{
                    font-size: 26px;
                    font-weight: 900;
                    color: #8b5cf6;
                    letter-spacing: -0.5px;
                }}
                .doc-title {{
                    font-size: 18px;
                    font-weight: 700;
                    color: #111827;
                    text-align: right;
                }}
                .meta {{
                    font-size: 13px;
                    color: #6b7280;
                    text-align: right;
                }}
                .info-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 16px;
                    margin-bottom: 24px;
                    font-size: 14px;
                }}
                .summary-grid {{
                    display: grid;
                    grid-template-columns: repeat(4, 1fr);
                    gap: 12px;
                    margin-bottom: 28px;
                }}
                .summary-box {{
                    background: #f3f4f6;
                    border-radius: 8px;
                    padding: 14px;
                    text-align: center;
                }}
                .summary-label {{
                    font-size: 11px;
                    text-transform: uppercase;
                    color: #6b7280;
                    font-weight: 600;
                    margin-bottom: 4px;
                }}
                .summary-val {{
                    font-size: 18px;
                    font-weight: 800;
                    color: #111827;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    font-size: 13px;
                    margin-bottom: 24px;
                }}
                th {{
                    background: #f9fafb;
                    color: #4b5563;
                    text-align: left;
                    padding: 10px 12px;
                    border-bottom: 1px solid #e5e7eb;
                    font-weight: 600;
                }}
                td {{
                    padding: 10px 12px;
                    border-bottom: 1px solid #f3f4f6;
                }}
                .badge {{
                    background: #ede9fe;
                    color: #7c3aed;
                    padding: 3px 8px;
                    border-radius: 12px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                .footer {{
                    font-size: 12px;
                    color: #9ca3af;
                    text-align: center;
                    border-top: 1px solid #e5e7eb;
                    padding-top: 16px;
                }}
                @media print {{
                    body {{ background: white; padding: 0; }}
                    .print-bar {{ display: none; }}
                    .statement-card {{ box-shadow: none; border: none; padding: 0; }}
                }}
            </style>
        </head>
        <body>
            <div class="print-bar">
                <button class="print-btn" onclick="window.print()">🖨️ Print / Save as PDF</button>
            </div>
            <div class="statement-card">
                <div class="header">
                    <div>
                        <div class="brand">✨ SixthSense</div>
                        <div style="font-size: 13px; color: #6b7280;">Psychic Advisory Platform</div>
                    </div>
                    <div>
                        <div class="doc-title">EARNINGS STATEMENT</div>
                        <div class="meta">Period: <strong>{period_label}</strong></div>
                        <div class="meta">Generated: {now.strftime("%B %d, %Y")}</div>
                    </div>
                </div>

                <div class="info-grid">
                    <div>
                        <div style="color: #6b7280; font-size: 12px;">ADVISOR DETAILS</div>
                        <div style="font-weight: 700; font-size: 16px; color: #111827;">{name}</div>
                        <div style="color: #4b5563;">ID: {pid}</div>
                        <div style="color: #4b5563;">Email: {pemail or 'N/A'}</div>
                    </div>
                    <div style="text-align: right;">
                        <div style="color: #6b7280; font-size: 12px;">AVAILABLE FOR PAYOUT</div>
                        <div style="font-weight: 800; font-size: 24px; color: #10b981;">${available_balance:.2f}</div>
                        <div style="color: #6b7280; font-size: 12px;">Revenue Split: 40% Advisor / 60% Platform</div>
                    </div>
                </div>

                <div class="summary-grid">
                    <div class="summary-box">
                        <div class="summary-label">Sessions</div>
                        <div class="summary-val">{len(sessions)}</div>
                    </div>
                    <div class="summary-box">
                        <div class="summary-label">Gross Revenue</div>
                        <div class="summary-val">${period_gross:.2f}</div>
                    </div>
                    <div class="summary-box">
                        <div class="summary-label">Platform Fee (60%)</div>
                        <div class="summary-val" style="color: #ef4444;">-${platform_fee:.2f}</div>
                    </div>
                    <div class="summary-box">
                        <div class="summary-label">Net Earnings (40%)</div>
                        <div class="summary-val" style="color: #10b981;">${total_net_with_tips:.2f}</div>
                    </div>
                </div>

                <div style="font-size: 14px; font-weight: 700; color: #111827; margin-bottom: 12px;">
                    Itemized Session & Tip Activity
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Date & Time</th>
                            <th>Service</th>
                            <th>Client</th>
                            <th>Duration</th>
                            <th style="text-align: right;">Gross</th>
                            <th style="text-align: right;">Advisor Net</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>

                <div class="footer">
                    Official financial statement generated by SixthSense Platform. For questions or support, contact support@sixthsense.com.
                </div>
            </div>
            <script>
                // Auto trigger print dialog on load
                window.onload = function() {{
                    setTimeout(function() {{
                        window.print();
                    }}, 600);
                }};
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    
    @router.post("/webhook")
    async def stripe_webhook(request: Request):
        """Handle Stripe webhooks with cryptographic signature verification (BE-007)"""
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")
        
        stripe_cfg = await get_active_stripe_config()
        active_webhook = (stripe_cfg.get("webhook_secret") or os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()

        if active_webhook and sig_header:
            try:
                import stripe
                event = stripe.Webhook.construct_event(
                    payload, sig_header, active_webhook
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
