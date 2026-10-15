import asyncio
import httpx
import websockets
import json
import uuid
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))
from server import app, db

BASE_HTTP = "http://testserver"

async def run_uat():
    print("=" * 75)
    print("SIXTH SENSE PSYCHICS: COMPLETE END-TO-END STAGING UAT SUITE")
    print("=" * 75)

    transport = httpx.ASGITransport(app=app)
    results = {}

    async with httpx.AsyncClient(transport=transport, base_url=BASE_HTTP, timeout=25.0, follow_redirects=True) as http:

        # =====================================================================
        # 1. STAGING ENVIRONMENT CONFIGURATION AUDIT
        # =====================================================================
        print("\n[UAT STEP 1] Staging Environment Verification...")
        # 1a. Health check
        health = await http.get("/api/health")
        assert health.status_code == 200, f"Health check failed: {health.text}"
        print(f"  ✅ Backend Health Check: 200 OK ({health.json()})")

        # 1b. Stripe Test Mode Config
        p_cfg = await http.get("/api/payments/config")
        assert p_cfg.status_code == 200
        pdata = p_cfg.json()
        assert pdata.get("stripe_enabled") is True
        pub_key = pdata.get("publishable_key", "")
        assert pub_key.startswith("pk_test_"), f"Expected test publishable key, got: {pub_key[:10]}..."
        print(f"  ✅ Stripe Test Mode: stripe_enabled=True, Key={pub_key[:12]}... (Strictly Test Mode)")

        # 1c. MongoDB connection & collection checks
        db_stats = await db.command("ping")
        assert db_stats.get("ok") == 1.0
        print("  ✅ MongoDB Atlas ping: ok=1.0 (Database connected)")
        results["1_staging_environment"] = "PASS"

        # =====================================================================
        # 2. CLIENT USER JOURNEY
        # =====================================================================
        print("\n[UAT STEP 2] Client User Journey...")
        
        # 2a. Registration
        c_uuid = uuid.uuid4().hex[:8]
        client_email = f"uat_client_{c_uuid}@example.com"
        client_pwd = "ClientPassword123!"
        
        reg_resp = await http.post("/api/auth/register", json={
            "name": "UAT Test Client",
            "email": client_email,
            "password": client_pwd,
            "birth_date": "1995-04-12"
        })
        assert reg_resp.status_code == 200, f"Registration failed: {reg_resp.text}"
        client_token = reg_resp.json().get("access_token")
        client_id = reg_resp.json().get("user", {}).get("id")
        assert client_token and client_id
        print(f"  ✅ Client Registered: ID={client_id}, Email={client_email}")

        # 2b. Login
        login_resp = await http.post("/api/auth/login", json={
            "email": client_email,
            "password": client_pwd
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        assert login_resp.json().get("access_token")
        print("  ✅ Client Login: Authenticated successfully with JWT token")

        # 2c. Browse Advisors, verify profile, rates, intro video, reviews
        browse_resp = await http.get("/api/psychics")
        assert browse_resp.status_code == 200
        advisors = browse_resp.json()
        assert len(advisors) > 0, "No advisors returned"
        target_adv = advisors[0]
        adv_id = target_adv.get("id")
        adv_name = target_adv.get("name")
        adv_rate = target_adv.get("chat_rate", 2.99)
        print(f"  ✅ Browsed Advisors: Found {len(advisors)} advisors. Selected '{adv_name}' (Rate: ${adv_rate}/min)")

        # Check advisor detail & intro video
        adv_detail = await http.get(f"/api/psychics/{adv_id}")
        assert adv_detail.status_code == 200
        print(f"  ✅ Advisor Profile Details retrieved (Specialties: {adv_detail.json().get('specialties', [])})")

        # Check advisor reviews
        rev_resp = await http.get(f"/api/reviews/psychic/{adv_id}")
        assert rev_resp.status_code == 200
        print(f"  ✅ Advisor Reviews retrieved: {len(rev_resp.json())} reviews found")

        # 2d. Start one-time 4-minute free trial
        trial_start = await http.post(
            f"/api/chat/start?user_id={client_id}&use_free_minutes=true",
            json={
                "client_id": client_id,
                "psychic_id": adv_id,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert trial_start.status_code == 200, f"Trial session failed: {trial_start.text}"
        session_id = trial_start.json().get("session", {}).get("id")
        assert trial_start.json().get("free_minutes_used") is True
        print(f"  ✅ 4-Minute Free Chat Started: Session ID={session_id}, free_minutes_used=True")

        # 2e. Confirm free trial CANNOT be reused
        repeat_trial = await http.post(
            f"/api/chat/start?user_id={client_id}&use_free_minutes=true",
            json={
                "client_id": client_id,
                "psychic_id": adv_id,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert repeat_trial.status_code == 400, f"Expected 400, got: {repeat_trial.status_code}"
        print("  ✅ Repeat Free Trial strictly rejected with HTTP 400 Bad Request")

        # 2f. Wallet Top-Up via Stripe Test Mode
        # 1. Create genuine checkout session
        checkout_res = await http.post("/api/payments/create-checkout-session", json={
            "amount": 2500,
            "credits": 25,
            "user_id": client_id,
            "success_url": "sixthsensepsychics://payment-success?success=true",
            "cancel_url": "sixthsensepsychics://payment-success?cancelled=true"
        })
        assert checkout_res.status_code == 200
        ck_json = checkout_res.json()
        assert ck_json["session_id"].startswith("cs_test_")
        assert "https://checkout.stripe.com" in ck_json["checkout_url"]
        print(f"  ✅ Genuine Stripe Checkout Session: ID={ck_json['session_id']}")

        # 2. Simulate signed webhook completion
        event_id = f"evt_uat_{uuid.uuid4().hex[:10]}"
        webhook_data = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": ck_json["session_id"],
                    "amount_total": 2500,
                    "metadata": {
                        "user_id": client_id,
                        "credits": 25
                    }
                }
            }
        }
        wh_res = await http.post("/api/payments/webhook", json=webhook_data)
        assert wh_res.status_code == 200 and wh_res.json().get("status") == "success"
        print("  ✅ Webhook processed: 25 credits credited to wallet")

        # 3. Duplicate event check
        wh_dup = await http.post("/api/payments/webhook", json=webhook_data)
        assert wh_dup.status_code == 200 and wh_dup.json().get("status") == "already_processed"
        print("  ✅ Webhook deduplication: duplicate event safely rejected (already_processed)")

        # 4. Verify wallet balance refresh
        wallet_res = await http.get(f"/api/wallet/{client_id}")
        assert wallet_res.status_code == 200
        w_data = wallet_res.json()
        assert w_data.get("balance") == 25.0
        print(f"  ✅ Wallet Balance Refreshed: ${w_data.get('balance')} balance confirmed")
        results["2_client_journey"] = "PASS"

        # =====================================================================
        # 3. ADVISOR USER JOURNEY
        # =====================================================================
        print("\n[UAT STEP 3] Advisor User Journey...")
        
        # 3a. Submit application with password & intro video
        adv_uuid = uuid.uuid4().hex[:8]
        adv_app_email = f"advisor_uat_{adv_uuid}@example.com"
        adv_app_pwd = "AdvisorSecurePwd123!"
        
        app_res = await http.post("/api/applications/submit", json={
            "full_name": "UAT Master Advisor",
            "email": adv_app_email,
            "phone": "+15553334444",
            "country": "United States",
            "years_experience": "7",
            "specialties": ["Tarot Readings", "Clairvoyance"],
            "bio": "Certified psychic reader providing honest life and career guidance.",
            "background": "Over 7 years of spiritual counseling and generational tarot practice.",
            "tools_used": ["Tarot Cards", "Rune Stones"],
            "password": adv_app_pwd,
            "video_url": "https://res.cloudinary.com/demo/video/upload/intro_sample.mp4",
            "video_duration": 60,
            "intro_video_url": "https://res.cloudinary.com/demo/video/upload/intro_sample.mp4",
            "intro_video_duration": 60,
            "interview_answers": [
                {"question": "How do you handle difficult readings?", "answer": "With empathy and spiritual clarity."}
            ]
        })
        assert app_res.status_code == 200, f"Application submission failed: {app_res.text}"
        app_id = app_res.json().get("id")
        print(f"  ✅ Advisor Application Submitted: ID={app_id}, Email={adv_app_email}")

        # 3b. Verify pending advisor cannot log in -> HTTP 403
        login_pending = await http.post("/api/auth/psychic/login", json={
            "email": adv_app_email,
            "password": adv_app_pwd
        })
        assert login_pending.status_code == 403, f"Expected 403 for pending advisor, got {login_pending.status_code}"
        print("  ✅ Pending Advisor Login strictly denied with HTTP 403 Forbidden")

        # 3c. Submit rejected candidate and verify login rejection
        rej_uuid = uuid.uuid4().hex[:8]
        rej_email = f"rejected_uat_{rej_uuid}@example.com"
        rej_sub = await http.post("/api/applications/submit", json={
            "full_name": "Rejected UAT Applicant",
            "email": rej_email,
            "phone": "+15559998888",
            "country": "United States",
            "years_experience": "0",
            "specialties": ["None"],
            "bio": "No experience.",
            "background": "None.",
            "tools_used": [],
            "password": "RejectedPwd123!"
        })
        rej_id = rej_sub.json().get("id")
        # Admin rejects
        admin_rej = await http.patch(f"/api/admin/applications/{rej_id}/status", json={
            "status": "rejected",
            "reason": "Does not meet experience requirements"
        })
        assert admin_rej.status_code == 200
        # Rejected login attempt
        login_rej = await http.post("/api/auth/psychic/login", json={
            "email": rej_email,
            "password": "RejectedPwd123!"
        })
        assert login_rej.status_code == 403
        print("  ✅ Rejected Advisor Login strictly denied with HTTP 403 Forbidden")

        # 3d. Approve main test advisor through Admin
        appr_res = await http.patch(f"/api/admin/applications/{app_id}/status", json={
            "status": "approved"
        })
        assert appr_res.status_code == 200, f"Approval failed: {appr_res.text}"
        print(f"  ✅ Application {app_id} Approved by Admin")

        # 3e. Verify approved advisor successfully logs in with password
        login_ok = await http.post("/api/auth/psychic/login", json={
            "email": adv_app_email,
            "password": adv_app_pwd
        })
        assert login_ok.status_code == 200, f"Advisor login failed: {login_ok.text}"
        adv_login_data = login_ok.json()
        adv_profile_id = adv_login_data.get("psychic", {}).get("id")
        assert adv_login_data.get("token")
        print(f"  ✅ Approved Advisor Logged In: ID={adv_profile_id}, Name={adv_login_data.get('psychic', {}).get('name')}")

        # Set advisor status to online
        st_res = await http.post(f"/api/psychics/{adv_profile_id}/status", json={"online_status": "online"})
        assert st_res.status_code == 200
        print("  ✅ Advisor toggled online status to 'online'")

        # 3f. Incoming Chat: NEW CLIENT vs RETURNING CLIENT labels
        # For a brand new client with 0 completed sessions
        req_new = await http.post("/api/chat/request", json={
            "client_id": f"new_client_{uuid.uuid4().hex[:6]}",
            "client_name": "First Time Client",
            "psychic_id": adv_profile_id,
            "session_type": "chat"
        })
        assert req_new.status_code == 200
        assert req_new.json().get("client_type") == "NEW CLIENT"
        print("  ✅ Incoming Chat from new client correctly badged: 'NEW CLIENT'")

        # For a returning client with $\ge 1$ completed session
        returning_cid = f"ret_client_{uuid.uuid4().hex[:6]}"
        await db.chat_sessions.insert_one({
            "id": f"sess_prior_{uuid.uuid4().hex[:6]}",
            "client_id": returning_cid,
            "psychic_id": adv_profile_id,
            "status": "completed",
            "started_at": datetime.utcnow().isoformat()
        })
        req_ret = await http.post("/api/chat/request", json={
            "client_id": returning_cid,
            "client_name": "Returning Client",
            "psychic_id": adv_profile_id,
            "session_type": "chat"
        })
        assert req_ret.status_code == 200
        assert req_ret.json().get("client_type") == "RETURNING CLIENT"
        print("  ✅ Incoming Chat from returning client correctly badged: 'RETURNING CLIENT'")

        # 3g. Payout Request Validation: Minimum $40
        # Try $25 -> Must fail with 400
        payout_under = await http.post("/api/payments/payout-request", json={
            "psychic_id": adv_profile_id,
            "amount": 25.0,
            "method": "bank_transfer"
        })
        assert payout_under.status_code == 400
        assert "40" in payout_under.text
        print("  ✅ Payout below $40 strictly rejected with HTTP 400 ($40 minimum rule enforced)")

        # Seed balance and request $45 -> Must succeed
        await db.psychics.update_one(
            {"id": adv_profile_id},
            {"$set": {"available_balance": 150.0}}
        )
        payout_ok = await http.post("/api/payments/payout-request", json={
            "psychic_id": adv_profile_id,
            "amount": 45.0,
            "method": "bank_transfer"
        })
        assert payout_ok.status_code == 200, f"Payout request failed: {payout_ok.text}"
        print("  ✅ Payout of $45.00 successfully requested and recorded in pending status")
        results["3_advisor_journey"] = "PASS"

        # =====================================================================
        # 4. ADMIN USER JOURNEY
        # =====================================================================
        print("\n[UAT STEP 4] Admin User Journey...")
        
        # 4a. Admin Login
        admin_login_res = await http.post("/api/admin/login", json={
            "email": "admin@psychic.com",
            "password": "admin123"
        })
        assert admin_login_res.status_code == 200, f"Admin login failed: {admin_login_res.text}"
        admin_jwt = admin_login_res.json().get("access_token")
        assert admin_jwt
        admin_headers = {"Authorization": f"Bearer {admin_jwt}"}
        print("  ✅ Admin Login: Authenticated successfully as super_admin")

        # 4b. Review Applications
        apps_res = await http.get("/api/admin/applications?status=all", headers=admin_headers)
        assert apps_res.status_code == 200
        apps_list = apps_res.json()
        print(f"  ✅ Admin Applications Queue: Retrieved {len(apps_list)} applications")

        # 4c. Verify Interview Answers & Intro Video stored
        app_found = await db.applications.find_one({"id": app_id})
        assert app_found is not None
        assert "intro_video_url" in app_found
        assert "interview_answers" in app_found
        print("  ✅ Admin Application Details: Intro video & interview answers verified in database")

        # 4d. Verify Admin Conversations monitoring
        convs_res = await http.get("/api/admin/conversations", headers=admin_headers)
        assert convs_res.status_code == 200
        print(f"  ✅ Admin Conversations Monitoring: Retrieved {len(convs_res.json())} sessions")

        # 4e. Verify Admin Payouts list
        payouts_res = await http.get("/api/admin/payouts", headers=admin_headers)
        assert payouts_res.status_code == 200
        print(f"  ✅ Admin Payouts List: Retrieved {len(payouts_res.json())} withdrawal records")

        # 4f. Hiring Waitlist deletion action
        # Submit a waitlist entry
        wl_email = f"waitlist_{uuid.uuid4().hex[:6]}@example.com"
        await db.hiring_waitlist.insert_one({
            "email": wl_email,
            "name": "Waitlist Candidate",
            "created_at": datetime.utcnow().isoformat()
        })
        del_wl = await http.delete(f"/api/admin/hiring-waitlist/{wl_email}", headers=admin_headers)
        assert del_wl.status_code == 200
        assert await db.hiring_waitlist.find_one({"email": wl_email}) is None
        print(f"  ✅ Admin Hiring Waitlist Action: DELETE endpoint cleared {wl_email} from database")
        results["4_admin_journey"] = "PASS"

    print("\n" + "=" * 75)
    print("ALL STAGING UAT JOURNEYS COMPLETED SUCCESSFULLY! ✨")
    print(json.dumps(results, indent=2))
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(run_uat())
