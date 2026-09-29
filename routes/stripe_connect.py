from fastapi import APIRouter, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import stripe
import os

def create_stripe_connect_router(db: AsyncIOMotorDatabase):
    router = APIRouter(prefix="/stripe-connect", tags=["stripe-connect"])
    
    # Initialize Stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    
    @router.get("/account-status/{psychic_id}")
    async def get_account_status(psychic_id: str):
        """Get Stripe Connect account status for a psychic"""
        try:
            # Get psychic from database
            psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"_id": psychic_id}]})
            
            if not psychic:
                return {
                    "connected": False,
                    "account_id": None,
                    "charges_enabled": False,
                    "payouts_enabled": False,
                    "details_submitted": False,
                    "requirements": [],
                    "bank_account": None
                }
            
            stripe_account_id = psychic.get("stripe_account_id")
            
            if not stripe_account_id:
                return {
                    "connected": False,
                    "account_id": None,
                    "charges_enabled": False,
                    "payouts_enabled": False,
                    "details_submitted": False,
                    "requirements": [],
                    "bank_account": None
                }
            
            # Get account details from Stripe
            try:
                account = stripe.Account.retrieve(stripe_account_id)
                
                # Get bank account info if available
                bank_account = None
                if account.external_accounts and account.external_accounts.data:
                    ext_account = account.external_accounts.data[0]
                    bank_account = {
                        "id": ext_account.id,
                        "bank_name": ext_account.bank_name or "Bank Account",
                        "last4": ext_account.last4,
                        "status": "verified" if account.payouts_enabled else "pending",
                        "connected_at": psychic.get("stripe_connected_at", datetime.utcnow().isoformat())
                    }
                
                # Get currently due requirements
                requirements = []
                if account.requirements:
                    requirements = account.requirements.currently_due or []
                
                return {
                    "connected": True,
                    "account_id": stripe_account_id,
                    "charges_enabled": account.charges_enabled,
                    "payouts_enabled": account.payouts_enabled,
                    "details_submitted": account.details_submitted,
                    "requirements": requirements,
                    "bank_account": bank_account
                }
            except stripe.error.InvalidRequestError:
                # Account doesn't exist in Stripe
                return {
                    "connected": False,
                    "account_id": None,
                    "charges_enabled": False,
                    "payouts_enabled": False,
                    "details_submitted": False,
                    "requirements": [],
                    "bank_account": None
                }
                
        except Exception as e:
            print(f"Error getting account status: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/create-account")
    async def create_stripe_account(data: dict):
        """Create a Stripe Connect Express account for a psychic"""
        psychic_id = data.get("psychic_id")
        email = data.get("email")
        name = data.get("name")
        country = data.get("country", "US")
        
        if not psychic_id or not email:
            raise HTTPException(status_code=400, detail="Missing psychic_id or email")
        
        try:
            # Check if psychic already has a Stripe account
            psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"_id": psychic_id}]})
            
            if psychic and psychic.get("stripe_account_id"):
                # Already has account, generate new onboarding link
                account_id = psychic["stripe_account_id"]
            else:
                # Map country names to country codes
                country_mapping = {
                    "United States": "US",
                    "United Kingdom": "GB",
                    "Canada": "CA",
                    "Australia": "AU",
                    "Germany": "DE",
                    "France": "FR",
                    "Spain": "ES",
                    "Italy": "IT",
                    "Netherlands": "NL",
                    "Ireland": "IE",
                    "India": "IN",
                    "Philippines": "PH",
                }
                country_code = country_mapping.get(country, country if len(country) == 2 else "US")
                
                # Create new Stripe Connect Express account
                account = stripe.Account.create(
                    type="express",
                    country=country_code,
                    email=email,
                    capabilities={
                        "transfers": {"requested": True},
                    },
                    business_type="individual",
                    business_profile={
                        "mcc": "7299",  # Miscellaneous personal services
                        "product_description": "Psychic advisor services",
                    },
                    metadata={
                        "psychic_id": psychic_id,
                        "name": name,
                    }
                )
                account_id = account.id
                
                # Save Stripe account ID to psychic profile
                await db.psychics.update_one(
                    {"$or": [{"id": psychic_id}, {"_id": psychic_id}]},
                    {
                        "$set": {
                            "stripe_account_id": account_id,
                            "stripe_connected_at": datetime.utcnow().isoformat(),
                        }
                    }
                )
            
            # Create account onboarding link
            frontend_url = os.getenv("FRONTEND_URL", "https://sixthsensepsychics.com")
            
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=f"{frontend_url}/bank-account?refresh=true",
                return_url=f"{frontend_url}/bank-account?success=true",
                type="account_onboarding",
                collect="eventually_due",
            )
            
            return {
                "success": True,
                "account_id": account_id,
                "onboarding_url": account_link.url
            }
            
        except stripe.error.StripeError as e:
            print(f"Stripe error: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"Error creating Stripe account: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/account-link")
    async def create_account_link(data: dict):
        """Create a new account link for updating bank info"""
        psychic_id = data.get("psychic_id")
        account_id = data.get("account_id")
        
        if not account_id:
            raise HTTPException(status_code=400, detail="Missing account_id")
        
        try:
            frontend_url = os.getenv("FRONTEND_URL", "https://sixthsensepsychics.com")
            
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=f"{frontend_url}/bank-account?refresh=true",
                return_url=f"{frontend_url}/bank-account?success=true",
                type="account_onboarding",
            )
            
            return {"url": account_link.url}
            
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    @router.get("/dashboard-link/{psychic_id}")
    async def get_dashboard_link(psychic_id: str):
        """Get Stripe Express dashboard link for a psychic"""
        try:
            psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"_id": psychic_id}]})
            
            if not psychic or not psychic.get("stripe_account_id"):
                raise HTTPException(status_code=404, detail="Stripe account not found")
            
            login_link = stripe.Account.create_login_link(
                psychic["stripe_account_id"]
            )
            
            return {"url": login_link.url}
            
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    # Admin endpoints for payouts
    @router.get("/admin/connected-accounts")
    async def get_connected_accounts():
        """Get all psychics with connected Stripe accounts (for admin)"""
        try:
            psychics = await db.psychics.find(
                {"stripe_account_id": {"$exists": True, "$ne": None}}
            ).to_list(100)
            
            accounts = []
            for psychic in psychics:
                try:
                    account = stripe.Account.retrieve(psychic["stripe_account_id"])
                    
                    # Get available balance
                    balance = stripe.Balance.retrieve(
                        stripe_account=psychic["stripe_account_id"]
                    )
                    available = sum(b.amount for b in balance.available) / 100 if balance.available else 0
                    
                    accounts.append({
                        "psychic_id": str(psychic.get("id") or psychic.get("_id")),
                        "name": psychic.get("name"),
                        "email": psychic.get("email"),
                        "stripe_account_id": psychic["stripe_account_id"],
                        "payouts_enabled": account.payouts_enabled,
                        "available_balance": available,
                        "bank_connected": len(account.external_accounts.data) > 0 if account.external_accounts else False,
                    })
                except Exception as e:
                    print(f"Error getting account {psychic['stripe_account_id']}: {e}")
            
            return {"accounts": accounts}
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/admin/process-payout")
    async def process_payout(data: dict):
        """Process a payout to a psychic with minimum threshold and fee calculation (BE-037 & BE-039)"""
        psychic_id = data.get("psychic_id")
        amount = float(data.get("amount", 0))  # Amount in dollars
        is_instant = data.get("instant", False)
        
        if not psychic_id or amount <= 0:
            raise HTTPException(status_code=400, detail="Missing psychic_id or invalid amount")
        
        # BE-039: Minimum $50 payout threshold
        if amount < 50.0:
            raise HTTPException(status_code=400, detail="Minimum payout threshold is $50.00")
        
        # BE-037: Instant payout fee calculation (1.5% fee, min $0.50)
        fee = max(0.50, round(amount * 0.015, 2)) if is_instant else 0.0
        net_amount = round(amount - fee, 2)
        
        try:
            psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"_id": psychic_id}]})
            
            if not psychic or not psychic.get("stripe_account_id"):
                raise HTTPException(status_code=404, detail="Psychic Stripe account not found")
            
            # Check if Stripe Connect KYC payouts are enabled (BE-032)
            if not psychic.get("payouts_enabled", True):
                raise HTTPException(status_code=400, detail="Advisor Stripe account payouts are currently restricted or pending KYC verification")

            # Create a transfer to the connected account
            transfer = stripe.Transfer.create(
                amount=int(net_amount * 100),  # Convert to cents
                currency="usd",
                destination=psychic["stripe_account_id"],
                description=f"Payout for {psychic.get('name', 'Advisor')} (Net: ${net_amount:.2f}, Fee: ${fee:.2f})",
                metadata={
                    "psychic_id": psychic_id,
                    "gross_amount": amount,
                    "fee": fee,
                    "type": "advisor_payout"
                }
            )
            
            # Record the payout
            await db.payouts.insert_one({
                "psychic_id": psychic_id,
                "stripe_transfer_id": transfer.id,
                "amount": amount,
                "currency": "usd",
                "status": "completed",
                "created_at": datetime.utcnow().isoformat(),
            })
            
            return {
                "success": True,
                "transfer_id": transfer.id,
                "amount": amount,
            }
            
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/admin/payout-history")
    async def get_payout_history(limit: int = 50):
        """Get payout history (admin only)"""
        try:
            payouts = await db.payouts.find().sort("created_at", -1).limit(limit).to_list(limit)
            
            for payout in payouts:
                payout["_id"] = str(payout["_id"])
            
            return {"payouts": payouts}
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router
