import asyncio
import httpx
import uuid
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))
from server import app

BASE_URL = ""

async def test_all():
    print("=" * 60)
    print("RUNNING SIXTH SENSE PSYCHICS INTEGRATION VERIFICATION")
    print("=" * 60)
    
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=15.0) as client:
        # 1. Payout Minimum Test ($40 threshold)
        print("\n[TEST 1] Payout minimum threshold ($40 enforcement)...")
        from server import db
        test_psy_id = f"psy_test_{uuid.uuid4().hex[:8]}"
        await db.psychics.insert_one({
            "id": test_psy_id,
            "name": "Test Payout Advisor",
            "email": f"{test_psy_id}@example.com",
            "available_balance": 100.0,
            "status": "approved"
        })
        res = await client.post(f"{BASE_URL}/api/payments/payout-request", json={
            "psychic_id": test_psy_id,
            "amount": 25.0,
            "method": "bank_transfer"
        })
        assert res.status_code == 400, f"Expected 400 for $25 payout request, got: {res.status_code}: {res.text}"
        assert "40" in res.text, f"Expected $40 in error detail, got: {res.text}"
        print("  ✅ $40 minimum withdrawal correctly rejected $25 request!")

        # 2. Checkout Session Creation
        print("\n[TEST 2] Checkout session creation...")
        res = await client.post(f"{BASE_URL}/api/payments/create-checkout-session", json={
            "amount": 2000,
            "credits": 20,
            "user_id": "user_test_verify",
            "success_url": "sixthsensepsychics://payment-success?success=true",
            "cancel_url": "sixthsensepsychics://payment-success?cancelled=true"
        })
        assert res.status_code == 200, f"Checkout session creation failed: {res.text}"
        data = res.json()
        assert "checkout_url" in data and "session_id" in data
        print(f"  ✅ Checkout session created successfully! URL: {data['checkout_url'][:50]}... Session ID: {data['session_id']}")

        # 3. Webhook Idempotency & Deduplication
        print("\n[TEST 3] Webhook idempotency & deduplication...")
        evt_id = f"evt_verify_{uuid.uuid4().hex[:8]}"
        webhook_payload = {
            "id": evt_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": f"cs_verify_{uuid.uuid4().hex[:8]}",
                    "amount_total": 2000,
                    "metadata": {
                        "user_id": "user_test_webhook",
                        "credits": 20
                    }
                }
            }
        }
        res1 = await client.post(f"{BASE_URL}/api/payments/webhook", json=webhook_payload)
        assert res1.status_code == 200, f"First webhook failed: {res1.text}"
        res1_json = res1.json()
        assert res1_json.get("status") == "success", f"Unexpected status: {res1_json}"
        print("  ✅ First webhook event processed successfully!")

        res2 = await client.post(f"{BASE_URL}/api/payments/webhook", json=webhook_payload)
        assert res2.status_code == 200, f"Duplicate webhook call failed: {res2.text}"
        res2_json = res2.json()
        assert res2_json.get("status") == "already_processed", f"Expected already_processed, got: {res2_json}"
        print(f"  ✅ Duplicate event '{evt_id}' detected and blocked: {res2_json['status']}!")

        # 4. Free trial backend enforcement test
        print("\n[TEST 4] Free trial backend enforcement...")
        fake_user_id = f"user_trial_{uuid.uuid4().hex[:8]}"
        reg_res = await client.post(f"{BASE_URL}/api/auth/register", json={
            "name": "Trial Test User",
            "email": f"{fake_user_id}@example.com",
            "password": "Password123!"
        })
        assert reg_res.status_code in (200, 201), f"Registration failed: {reg_res.text}"
        user_data = reg_res.json()
        client_id = user_data.get("user", {}).get("id") or user_data.get("id") or fake_user_id
        
        # Mark first reading as used
        await client.put(f"{BASE_URL}/api/users/{client_id}/mark-first-reading-used")
        
        # Now attempt start_session with use_free_minutes=True
        trial_session_res = await client.post(
            f"{BASE_URL}/api/chat/start?user_id={client_id}&use_free_minutes=true",
            json={
                "client_id": client_id,
                "psychic_id": test_psy_id,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert trial_session_res.status_code == 400, f"Expected 400 rejection, got {trial_session_res.status_code}: {trial_session_res.text}"
        print("  ✅ Backend strictly rejected repeat free trial claim with 400!")

        # 5. Advisor rejected applicant login denial test
        print("\n[TEST 5] Advisor security: rejected applicant login denial...")
        test_email = f"applicant_{uuid.uuid4().hex[:8]}@example.com"
        app_res = await client.post(f"{BASE_URL}/api/applications/submit", json={
            "full_name": "Test Applicant",
            "email": test_email,
            "phone": "+15551234567",
            "country": "United States",
            "years_experience": "3",
            "specialties": ["Tarot Cards"],
            "bio": "Experienced psychic reader for over three years providing authentic guidance.",
            "background": "Studied traditional tarot and spiritual alignment through generational family practice.",
            "tools_used": ["Tarot Cards"],
            "password": "SecurePassword123!"
        })
        assert app_res.status_code in (200, 201), f"Submit application failed: {app_res.text}"
        app_id = app_res.json().get("application_id") or app_res.json().get("id")
        print(f"  Application submitted with ID: {app_id}")

        # Reject application via admin endpoint
        rej_res = await client.patch(f"{BASE_URL}/api/admin/applications/{app_id}/status", json={
            "status": "rejected",
            "reason": "Verification failed"
        })
        assert rej_res.status_code == 200, f"Rejection failed: {rej_res.text}"
        print("  Application rejected by admin.")

        # Attempt advisor login with rejected email
        login_res = await client.post(f"{BASE_URL}/api/auth/psychic/login", json={
            "email": test_email,
            "password": "SecurePassword123!"
        })
        assert login_res.status_code == 403, f"Expected 403 Forbidden for rejected advisor, got {login_res.status_code}: {login_res.text}"
        assert "not approved" in login_res.text or "rejected" in login_res.text, f"Unexpected error detail: {login_res.text}"
        print("  ✅ Login attempt by rejected applicant strictly denied with 403!")

        # 6. Admin conversations monitoring endpoint
        print("\n[TEST 6] Admin conversations monitoring...")
        admin_convs = await client.get(f"{BASE_URL}/api/admin/conversations")
        assert admin_convs.status_code == 200, f"Failed to get admin conversations: {admin_convs.text}"
        print(f"  ✅ Admin conversations endpoint operational (returned {len(admin_convs.json())} conversations)")

    print("\n" + "=" * 60)
    print("ALL INTEGRATION VERIFICATIONS PASSED SUCCESSFULLY! ✨")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_all())
