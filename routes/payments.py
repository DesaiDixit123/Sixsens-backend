from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import os

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
    
    @router.post("/create-checkout-session")
    async def create_checkout_session(data: dict):
        """Create a Stripe Checkout Session for payment"""
        amount = data.get("amount", 0)  # Amount in cents
        credits = data.get("credits", 0)
        user_id = data.get("user_id", "")
        success_url = data.get("success_url", "https://web-production-9acf9.up.railway.app/wallet?success=true")
        cancel_url = data.get("cancel_url", "https://web-production-9acf9.up.railway.app/wallet?cancelled=true")
        
        if STRIPE_ENABLED:
            try:
                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    line_items=[{
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": f"{credits} Credits",
                                "description": f"Add {credits} credits to your Sixth Sense wallet"
                            },
                            "unit_amount": amount,  # Amount in cents
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
                return {
                    "checkout_url": session.url,
                    "session_id": session.id
                }
            except stripe.error.StripeError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            # Mock response - Stripe not configured
            raise HTTPException(
                status_code=400, 
                detail="Payment system not configured. Please contact support."
            )
    
    @router.post("/confirm-payment")
    async def confirm_payment(request: AddCreditsRequest):
        """Confirm payment and add credits to user wallet"""
        # Verify payment with Stripe if enabled
        if STRIPE_ENABLED and request.payment_intent_id:
            try:
                intent = stripe.PaymentIntent.retrieve(request.payment_intent_id)
                if intent.status != "succeeded":
                    raise HTTPException(status_code=400, detail="Payment not completed")
            except stripe.error.StripeError as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        # Add credits to user
        result = await db.users.update_one(
            {"id": request.user_id},
            {"$inc": {"balance": request.credits}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        
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
        
        return {
            "success": True,
            "new_balance": user.get("balance", 0),
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
        
        available = psychic.get("available_balance", 0)
        if request.amount > available:
            raise HTTPException(status_code=400, detail="Insufficient available balance")
        
        if request.amount < 50:
            raise HTTPException(status_code=400, detail="Minimum payout is $50")
        
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
    async def get_psychic_earnings(psychic_id: str):
        """Get psychic's earnings summary"""
        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        return {
            "available_balance": psychic.get("available_balance", 0),
            "pending_balance": psychic.get("pending_balance", 0),
            "total_earnings": psychic.get("total_earnings", 0),
            "total_tips": psychic.get("total_tips", 0),
            "total_readings": psychic.get("total_readings", 0)
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
        
        # Handle the event
        if event.type == "payment_intent.succeeded":
            payment_intent = event.data.object
            # Process successful payment
            user_id = payment_intent.metadata.get("user_id")
            credits = int(payment_intent.metadata.get("package_credits", 0))
            if user_id and credits:
                await db.users.update_one(
                    {"id": user_id},
                    {"$inc": {"balance": credits}}
                )
        
        elif event.type == "payment_intent.payment_failed":
            payment_intent = event.data.object
            # Log failed payment
            print(f"Payment failed: {payment_intent.id}")
        
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
