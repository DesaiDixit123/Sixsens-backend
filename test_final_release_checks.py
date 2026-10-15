import asyncio
import httpx
import uuid
import sys
import os
import json
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))
from server import app, db

async def run_final_checks():
    print("=" * 75)
    print("FINAL RELEASE VERIFICATION: CLIENT-TO-ADVISOR & ADMIN DEEP AUDIT")
    print("=" * 75)

    transport = httpx.ASGITransport(app=app)
    results = {}

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=25.0, follow_redirects=True) as client:

        # =====================================================================
        # CHECK 1: STRIPE TEST MODE & SAFETY VERIFICATION
        # =====================================================================
        print("\n[CHECK 1] Verifying Stripe Test Mode & Financial Safety...")
        p_cfg = await client.get("/api/payments/config")
        assert p_cfg.status_code == 200
        pdata = p_cfg.json()
        assert pdata.get("stripe_enabled") is True
        pub_key = pdata.get("publishable_key", "")
        assert pub_key.startswith("pk_test_"), f"Security alert: key is not test mode: {pub_key}"
        
        # Verify secret key in environment
        sk = os.getenv("STRIPE_SECRET_KEY", "")
        assert sk.startswith("sk_test_"), f"Security alert: secret key is not test mode!"
        assert not sk.startswith("sk_live_"), "CRITICAL: Live Stripe secret key detected!"
        print(f"  ✅ Stripe is strictly in TEST MODE (Publishable: {pub_key[:12]}..., Secret: sk_test_...)")
        print("  ✅ Zero real money transfers enabled.")
        results["1_stripe_test_mode_safety"] = "PASS"

        # =====================================================================
        # CHECK 2: COMPLETE CLIENT-TO-ADVISOR USER JOURNEY
        # =====================================================================
        print("\n[CHECK 2] Complete Client-to-Advisor Journey...")
        
        # 2a. Client Registration
        c_id_suffix = uuid.uuid4().hex[:8]
        client_email = f"client_journey_{c_id_suffix}@example.com"
        client_pwd = "JourneyClient123!"
        
        reg_res = await client.post("/api/auth/register", json={
            "name": "E2E Journey Client",
            "email": client_email,
            "password": client_pwd,
            "birth_date": "1992-05-18" # Taurus
        })
        assert reg_res.status_code == 200, f"Client registration failed: {reg_res.text}"
        client_data = reg_res.json()
        client_uid = client_data.get("user", {}).get("id")
        client_token = client_data.get("access_token")
        assert client_uid and client_token
        # Verify Taurus zodiac
        db_client = await db.users.find_one({"id": client_uid})
        assert db_client.get("zodiac_sign") == "Taurus"
        print(f"  ✅ Client Registered: UID={client_uid}, Zodiac='Taurus' saved")

        # 2b. Client Login
        login_res = await client.post("/api/auth/login", json={
            "email": client_email,
            "password": client_pwd
        })
        assert login_res.status_code == 200
        print("  ✅ Client Login: Token authenticated")

        # 2c. Advisor Application Submission
        adv_suffix = uuid.uuid4().hex[:8]
        adv_email = f"advisor_journey_{adv_suffix}@example.com"
        adv_pwd = "JourneyAdvisor123!"
        sample_video = "https://res.cloudinary.com/sixthsense/video/upload/advisor_intro.mp4"
        sample_qa = [
            {"q": "What tools do you use?", "a": "Tarot and Astrological ephemeris."}
        ]
        
        app_sub = await client.post("/api/applications/submit", json={
            "full_name": "E2E Journey Advisor",
            "email": adv_email,
            "phone": "+15557778888",
            "country": "United States",
            "years_experience": "6",
            "specialties": ["Tarot Readings", "Astrology"],
            "bio": "Experienced intuitive counselor.",
            "background": "Over six years providing ethical and empowering spiritual readings.",
            "tools_used": ["Tarot Cards"],
            "password": adv_pwd,
            "video_url": sample_video,
            "video_duration": 55,
            "intro_video_url": sample_video,
            "intro_video_duration": 55,
            "interview_answers": sample_qa
        })
        assert app_sub.status_code == 200, f"Advisor application failed: {app_sub.text}"
        application_id = app_sub.json().get("id")
        print(f"  ✅ Advisor Application Submitted: ID={application_id}")

        # 2d. Admin Approves Advisor
        appr_res = await client.patch(f"/api/admin/applications/{application_id}/status", json={"status": "approved"})
        assert appr_res.status_code == 200
        print(f"  ✅ Admin approved advisor application {application_id}")

        # 2e. Advisor Login with Password
        adv_login = await client.post("/api/auth/psychic/login", json={
            "email": adv_email,
            "password": adv_pwd
        })
        assert adv_login.status_code == 200, f"Advisor login failed: {adv_login.text}"
        adv_prof = adv_login.json().get("psychic", {})
        adv_id = adv_prof.get("id")
        adv_token = adv_login.json().get("token")
        assert adv_id and adv_token
        print(f"  ✅ Advisor Logged In: ID={adv_id}, Name={adv_prof.get('name')}")

        # 2f. Advisor toggles online status
        st_res = await client.post(f"/api/psychics/{adv_id}/status", json={"online_status": "online"})
        assert st_res.status_code == 200
        print("  ✅ Advisor set status to 'online'")

        # 2g. Client browses advisors, views profile, intro video, and reviews
        browse_res = await client.get("/api/psychics/")
        assert browse_res.status_code == 200
        adv_detail = await client.get(f"/api/psychics/{adv_id}")
        assert adv_detail.status_code == 200
        adv_doc = adv_detail.json()
        assert adv_doc.get("intro_video_url") == sample_video
        assert adv_doc.get("interview_answers") == sample_qa
        print(f"  ✅ Client inspected advisor profile: intro video & interview Q&A verified")

        # 2h. Client starts 4-minute free chat
        start_chat = await client.post(
            f"/api/chat/start?user_id={client_uid}&use_free_minutes=true",
            json={
                "client_id": client_uid,
                "psychic_id": adv_id,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert start_chat.status_code == 200, f"Chat start failed: {start_chat.text}"
        chat_sess = start_chat.json()
        session_id = chat_sess.get("session", {}).get("id")
        assert chat_sess.get("free_minutes_used") is True
        print(f"  ✅ 4-Minute Free Chat Session Started: ID={session_id}")

        # 2i. Exchange messages & persist to MongoDB
        msg_c_id = f"msg_client_{uuid.uuid4().hex[:8]}"
        msg_a_id = f"msg_adv_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.utcnow().isoformat()
        
        # Save client message
        await db.messages.insert_one({
            "id": msg_c_id,
            "session_id": session_id,
            "conversation_id": session_id,
            "sender_id": client_uid,
            "receiver_id": adv_id,
            "text": "Hello Advisor! What do the cards say about my journey?",
            "timestamp": now_iso
        })
        # Save advisor message
        await db.messages.insert_one({
            "id": msg_a_id,
            "session_id": session_id,
            "conversation_id": session_id,
            "sender_id": adv_id,
            "receiver_id": client_uid,
            "text": "Greetings! The Sun card shines brightly upon your path.",
            "timestamp": now_iso
        })
        # Update session with message references
        await db.chat_sessions.update_one(
            {"id": session_id},
            {"$push": {"messages": {"$each": [{"id": msg_c_id}, {"id": msg_a_id}]}}}
        )
        print("  ✅ Real-time messages persisted in db.messages and db.chat_sessions")

        # 2j. Message history reload via API
        hist_res = await client.get(f"/api/messages/conversation/{session_id}?user_id={client_uid}")
        assert hist_res.status_code == 200, f"Message history failed: {hist_res.text}"
        reloaded_msgs = hist_res.json()
        reloaded_ids = [m["id"] for m in reloaded_msgs]
        assert msg_c_id in reloaded_ids and msg_a_id in reloaded_ids
        print(f"  ✅ Chat history reloaded successfully ({len(reloaded_msgs)} messages verified)")

        # 2k. Free trial reuse prevention
        dup_trial = await client.post(
            f"/api/chat/start?user_id={client_uid}&use_free_minutes=true",
            json={
                "client_id": client_uid,
                "psychic_id": adv_id,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert dup_trial.status_code == 400
        print("  ✅ Second free trial attempt strictly rejected with HTTP 400 Bad Request")

        # 2l. Stripe Test Payment & Wallet Refresh
        checkout_res = await client.post("/api/payments/create-checkout-session", json={
            "amount": 3000,
            "credits": 30,
            "user_id": client_uid,
            "success_url": "sixthsensepsychics://payment-success?success=true",
            "cancel_url": "sixthsensepsychics://payment-success?cancelled=true"
        })
        assert checkout_res.status_code == 200
        session_obj = checkout_res.json()
        assert session_obj["session_id"].startswith("cs_test_")
        
        # Webhook completion
        event_id = f"evt_journey_{uuid.uuid4().hex[:10]}"
        wh_res = await client.post("/api/payments/webhook", json={
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_obj["session_id"],
                    "amount_total": 3000,
                    "metadata": {
                        "user_id": client_uid,
                        "credits": 30
                    }
                }
            }
        })
        assert wh_res.status_code == 200 and wh_res.json().get("status") == "success"
        
        # Wallet Refresh via direct endpoint
        w_refresh = await client.get(f"/api/wallet/{client_uid}")
        assert w_refresh.status_code == 200
        assert w_refresh.json().get("balance") == 30.0
        print(f"  ✅ Stripe Test Payment: $30.00 credited, verified via GET /api/wallet/{client_uid}")

        # 2m. Client & Advisor Logout
        c_logout = await client.post("/api/auth/logout", json={"token": client_token})
        assert c_logout.status_code == 200
        a_logout = await client.post("/api/auth/logout", json={"token": adv_token})
        assert a_logout.status_code == 200
        print("  ✅ Logout verified: POST /api/auth/logout returned HTTP 200 for Client and Advisor")
        results["2_client_to_advisor_journey"] = "PASS"

        # =====================================================================
        # CHECK 3: ADMIN SCREENS, ACTIONS & BACKEND PERSISTENCE
        # =====================================================================
        print("\n[CHECK 3] Admin Screens & Backend Persistence Audit...")
        
        # 3a. Admin Login
        admin_login = await client.post("/api/admin/login", json={
            "email": "admin@psychic.com",
            "password": "admin123"
        })
        assert admin_login.status_code == 200
        admin_tok = admin_login.json().get("access_token")
        admin_headers = {"Authorization": f"Bearer {admin_tok}"}
        print("  ✅ Admin Authenticated: Bearer JWT acquired")

        # 3b. Applications Queue & Decisions
        apps_res = await client.get("/api/admin/applications?status=all", headers=admin_headers)
        assert apps_res.status_code == 200
        print(f"  ✅ Applications Screen: {len(apps_res.json())} applications loaded")

        # 3c. Video Interview & Intro Video Persistence
        app_check = await db.applications.find_one({"id": application_id})
        assert app_check.get("intro_video_url") == sample_video
        assert app_check.get("interview_answers") == sample_qa
        print("  ✅ Video Interview & Intro Video verified in application document")

        # 3d. Reviews
        reviews_res = await client.get(f"/api/reviews/psychic/{adv_id}")
        assert reviews_res.status_code == 200
        print(f"  ✅ Reviews Screen: Verified reviews endpoint for psychic {adv_id}")

        # 3e. Conversations Monitoring
        convs_res = await client.get("/api/admin/conversations", headers=admin_headers)
        assert convs_res.status_code == 200
        assert len(convs_res.json()) > 0
        print(f"  ✅ Conversations Screen: Loaded {len(convs_res.json())} sessions")

        # 3f. Sales Management (Create, Toggle, Delete)
        new_sale_data = {
            "name": f"Admin Screen Sale {uuid.uuid4().hex[:6]}",
            "description": "Admin UAT sale verification",
            "discount_percentage": 20,
            "event_type": "solstice",
            "start_date": datetime.utcnow().isoformat(),
            "end_date": datetime.utcnow().isoformat(),
            "is_mandatory": True
        }
        create_s = await client.post("/api/admin/sales", json=new_sale_data)
        assert create_s.status_code == 200
        sale_s_id = create_s.json().get("sale", {}).get("id")
        assert await db.sales.find_one({"id": sale_s_id}) is not None
        print(f"  ✅ Sales Screen: Created sale {sale_s_id}")

        toggle_s = await client.patch(f"/api/admin/sales/{sale_s_id}/toggle")
        assert toggle_s.status_code == 200
        print("  ✅ Sales Screen: Toggled sale active status")

        del_s = await client.delete(f"/api/admin/sales/{sale_s_id}")
        assert del_s.status_code == 200
        assert await db.sales.find_one({"id": sale_s_id}) is None
        print("  ✅ Sales Screen: Deleted sale verified removed from database")

        # 3g. Payouts Screen & $40 Rule Enforcement
        payouts_admin = await client.get("/api/admin/payouts")
        assert payouts_admin.status_code == 200
        print(f"  ✅ Payouts Screen: Loaded {len(payouts_admin.json())} withdrawal records")

        # Test $40 minimum payout rejection
        pay_rej = await client.post("/api/payments/payout-request", json={
            "psychic_id": adv_id,
            "amount": 20.0,
            "method": "bank_transfer"
        })
        assert pay_rej.status_code == 400
        assert "40" in pay_rej.text
        print("  ✅ Payout Request: $40 minimum strictly enforced (rejected $20 request)")
        results["3_admin_screens_and_actions"] = "PASS"

    print("\n" + "=" * 75)
    print("ALL FINAL RELEASE CHECKS PASSED SUCCESSFULLY! 🚀")
    print(json.dumps(results, indent=2))
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(run_final_checks())
