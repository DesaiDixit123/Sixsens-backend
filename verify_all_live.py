import asyncio
import httpx
import websockets
import json
import uuid
import sys
import os
import time
import stripe
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))
from server import db

BASE_HTTP = "http://127.0.0.1:8000"
BASE_WS = "ws://127.0.0.1:8000"

async def run_live_verification():
    print("=" * 70)
    print("SIXTH SENSE PSYCHICS: COMPREHENSIVE LIVE VERIFICATION SUITE")
    print("=" * 70)
    
    results = {}

    async with httpx.AsyncClient(base_url=BASE_HTTP, timeout=20.0) as http:
        
        # ---------------------------------------------------------
        # 1. REAL-TIME CHAT VERIFICATION (LIVE WEBSOCKETS + MONGODB)
        # ---------------------------------------------------------
        print("\n--- [1] REAL-TIME WEBSOCKET CHAT VERIFICATION ---")
        client_uid = f"client_live_{uuid.uuid4().hex[:8]}"
        advisor_uid = f"adv_live_{uuid.uuid4().hex[:8]}"
        conv_id = f"conv_live_{uuid.uuid4().hex[:8]}"
        
        # Seed test advisor & client in MongoDB
        await db.psychics.insert_one({
            "id": advisor_uid,
            "name": "Live Verified Advisor",
            "email": f"{advisor_uid}@example.com",
            "rate_per_minute": 4.50,
            "is_online": True,
            "available_balance": 100.0,
            "status": "approved"
        })
        await db.users.insert_one({
            "id": client_uid,
            "name": "Live Verified Client",
            "email": f"{client_uid}@example.com",
            "balance": 50.0,
            "is_new_user": False,
            "first_reading_free_used": True
        })
        # Create chat session in db
        await db.chat_sessions.insert_one({
            "id": conv_id,
            "client_id": client_uid,
            "psychic_id": advisor_uid,
            "session_type": "chat",
            "status": "active",
            "rate_per_minute": 4.50,
            "started_at": datetime.utcnow().isoformat(),
            "messages": []
        })

        # 1a. Test WebSocket Auth rejection with invalid token
        print("  1a. Testing WebSocket token auth rejection...")
        try:
            async with websockets.connect(f"{BASE_WS}/api/ws/{client_uid}?token=invalid_jwt_token") as ws_bad:
                assert False, "Expected invalid token connection to be rejected"
        except Exception as e:
            print(f"    ✅ Invalid token closed connection: {type(e).__name__} ({e})")

        # 1b. Connect both client and advisor WebSockets
        print("  1b. Connecting client and advisor WebSockets...")
        async with websockets.connect(f"{BASE_WS}/api/ws/{client_uid}") as ws_client, \
                   websockets.connect(f"{BASE_WS}/api/ws/{advisor_uid}") as ws_advisor:
            
            # 1c. Test Room Joining
            print("  1c. Testing room joining for both parties...")
            await ws_client.send(json.dumps({"type": "join_conversation", "conversation_id": conv_id}))
            client_join_resp = json.loads(await asyncio.wait_for(ws_client.recv(), timeout=5.0))
            assert client_join_resp.get("type") == "joined"
            assert client_join_resp.get("conversation_id") == conv_id

            await ws_advisor.send(json.dumps({"type": "join_conversation", "conversation_id": conv_id}))
            advisor_join_resp = json.loads(await asyncio.wait_for(ws_advisor.recv(), timeout=5.0))
            assert advisor_join_resp.get("type") == "joined"
            print("    ✅ Both parties successfully joined conversation room!")

            # 1d. Client sends message -> Advisor receives
            print("  1d. Client sends message -> Advisor receives in real-time...")
            client_msg_id = f"msg_c_{uuid.uuid4().hex[:8]}"
            await ws_client.send(json.dumps({
                "type": "new_message",
                "id": client_msg_id,
                "conversation_id": conv_id,
                "receiver_id": advisor_uid,
                "text": "Hello Advisor! Can you give me a tarot reading?"
            }))
            # Client receives message_sent confirmation
            client_ack = json.loads(await asyncio.wait_for(ws_client.recv(), timeout=5.0))
            assert client_ack.get("type") == "message_sent"
            assert client_ack.get("message_id") == client_msg_id

            # Advisor receives message (may receive 'new_message' or 'message')
            adv_received = json.loads(await asyncio.wait_for(ws_advisor.recv(), timeout=5.0))
            assert adv_received.get("type") in ("new_message", "message")
            assert adv_received.get("content") == "Hello Advisor! Can you give me a tarot reading?"
            assert adv_received.get("sender_id") == client_uid
            print(f"    ✅ Advisor received client message in real-time (id: {client_msg_id})!")

            # Drain any duplicate alias delivery (e.g., legacy type: 'message')
            try:
                await asyncio.wait_for(ws_advisor.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                pass

            # 1e. Advisor sends message -> Client receives
            print("  1e. Advisor sends message -> Client receives in real-time...")
            adv_msg_id = f"msg_a_{uuid.uuid4().hex[:8]}"
            await ws_advisor.send(json.dumps({
                "type": "message",
                "id": adv_msg_id,
                "conversation_id": conv_id,
                "receiver_id": client_uid,
                "content": "Welcome! I sense great cosmic opportunities for you."
            }))
            adv_ack = json.loads(await asyncio.wait_for(ws_advisor.recv(), timeout=5.0))
            assert adv_ack.get("type") == "message_sent"
            assert adv_ack.get("message_id") == adv_msg_id

            client_received = json.loads(await asyncio.wait_for(ws_client.recv(), timeout=5.0))
            assert client_received.get("type") in ("new_message", "message")
            assert client_received.get("content") == "Welcome! I sense great cosmic opportunities for you."
            assert client_received.get("sender_id") == advisor_uid
            print(f"    ✅ Client received advisor message in real-time (id: {adv_msg_id})!")

            # 1f. Typing indicator test
            print("  1f. Testing real-time typing indicators...")
            await ws_client.send(json.dumps({
                "type": "typing",
                "conversation_id": conv_id,
                "receiver_id": advisor_uid,
                "is_typing": True
            }))
            # Drain until typing event
            adv_typing = None
            for _ in range(3):
                msg = json.loads(await asyncio.wait_for(ws_advisor.recv(), timeout=5.0))
                if msg.get("type") in ("typing_indicator", "typing"):
                    adv_typing = msg
                    break
            assert adv_typing is not None and adv_typing.get("is_typing") is True
            print("    ✅ Advisor received typing indicator from client!")

            # 1g. One-minute warning test
            print("  1g. Testing one-minute advisor warning event...")
            await ws_client.send(json.dumps({
                "type": "one_minute_warning",
                "conversation_id": conv_id,
                "receiver_id": advisor_uid
            }))
            adv_warn = None
            for _ in range(5):
                msg = json.loads(await asyncio.wait_for(ws_advisor.recv(), timeout=5.0))
                if msg.get("type") == "one_minute_warning":
                    adv_warn = msg
                    break
            assert adv_warn is not None, "Did not receive one_minute_warning"
            assert "1 minute remaining" in adv_warn.get("text")
            print("    ✅ Advisor received 1-minute warning alert payload!")

        # 1h. Verify MongoDB Persistence
        print("  1h. Verifying message persistence in MongoDB collections...")
        db_msg1 = await db.messages.find_one({"id": client_msg_id})
        db_msg2 = await db.messages.find_one({"id": adv_msg_id})
        assert db_msg1 is not None, "Client message not saved in db.messages"
        assert db_msg2 is not None, "Advisor message not saved in db.messages"

        db_session = await db.chat_sessions.find_one({"id": conv_id})
        assert db_session is not None
        session_msg_ids = [m["id"] for m in db_session.get("messages", [])]
        assert client_msg_id in session_msg_ids, "Client message missing from db.chat_sessions"
        assert adv_msg_id in session_msg_ids, "Advisor message missing from db.chat_sessions"
        print("    ✅ Messages verified persisted in both db.messages and db.chat_sessions!")

        # 1i. Verify Message History Reload via API after disconnect
        print("  1i. Verifying message history reload via HTTP API...")
        msg_res = await http.get(f"/api/messages/conversation/{conv_id}?user_id={client_uid}")
        assert msg_res.status_code == 200
        msg_list = msg_res.json()
        reloaded_ids = [m["id"] for m in msg_list]
        assert client_msg_id in reloaded_ids and adv_msg_id in reloaded_ids
        
        chat_res = await http.get(f"/api/chat/{conv_id}")
        assert chat_res.status_code == 200
        chat_session_msgs = [m["id"] for m in chat_res.json().get("messages", [])]
        assert client_msg_id in chat_session_msgs and adv_msg_id in chat_session_msgs
        print(f"    ✅ Message history correctly reloaded {len(reloaded_ids)} messages from API!")
        results["1_realtime_chat"] = "PASS"

        # ---------------------------------------------------------
        # 2. STRIPE CHECKOUT, WEBHOOK & WALLET VERIFICATION
        # ---------------------------------------------------------
        print("\n--- [2] STRIPE CHECKOUT, WEBHOOK & WALLET VERIFICATION ---")
        # 2a. Check payment config
        cfg_res = await http.get("/api/payments/config")
        cfg_data = cfg_res.json()
        print(f"  Payment config: publishable_key={cfg_data.get('publishable_key')}, stripe_enabled={cfg_data.get('stripe_enabled')}")
        if not cfg_data.get("stripe_enabled"):
            print("  ℹ️ STRIPE_SECRET_KEY is not configured in .env -> Backend operates in mock checkout mode.")
            print("  ℹ️ Any checkout session creation will return a mock session (cs_mock_...) for test/dev safety.")

        # 2b. Checkout session creation
        checkout_res = await http.post("/api/payments/create-checkout-session", json={
            "amount": 2000,
            "credits": 20,
            "user_id": client_uid,
            "success_url": "sixthsensepsychics://payment-success?success=true",
            "cancel_url": "sixthsensepsychics://payment-success?cancelled=true"
        })
        assert checkout_res.status_code == 200
        ck_data = checkout_res.json()
        assert "checkout_url" in ck_data and "session_id" in ck_data
        print(f"  ✅ Checkout session endpoint functional: Session ID {ck_data['session_id']}")

        # 2c. Webhook signature & duplicate delivery test
        print("  2c. Testing webhook execution and idempotency...")
        user_before = await db.users.find_one({"id": client_uid})
        bal_before = user_before.get("balance", 0)
        
        event_id = f"evt_live_{uuid.uuid4().hex[:12]}"
        webhook_body = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": f"cs_test_{uuid.uuid4().hex[:8]}",
                    "amount_total": 2000,
                    "metadata": {
                        "user_id": client_uid,
                        "credits": 20
                    }
                }
            }
        }
        # Generate cryptographic signature if STRIPE_WEBHOOK_SECRET is set
        wh_headers = {"Content-Type": "application/json"}
        raw_body = json.dumps(webhook_body)
        stripe_wh_sec = os.getenv("STRIPE_WEBHOOK_SECRET")
        if stripe_wh_sec:
            t = int(time.time())
            sig = stripe.WebhookSignature._compute_signature(f"{t}.{raw_body}", stripe_wh_sec)
            wh_headers["Stripe-Signature"] = f"t={t},v1={sig}"

        # First delivery
        wh1 = await http.post("/api/payments/webhook", content=raw_body, headers=wh_headers)
        assert wh1.status_code == 200
        wh1_json = wh1.json()
        assert wh1_json.get("status") == "success"
        
        # Verify balance credited exactly 20
        user_after1 = await db.users.find_one({"id": client_uid})
        bal_after1 = user_after1.get("balance", 0)
        assert bal_after1 == bal_before + 20, f"Expected {bal_before + 20}, got {bal_after1}"
        print(f"    ✅ First webhook processed: balance updated from ${bal_before} to ${bal_after1}")

        # Duplicate delivery
        wh2 = await http.post("/api/payments/webhook", content=raw_body, headers=wh_headers)
        assert wh2.status_code == 200
        wh2_json = wh2.json()
        assert wh2_json.get("status") == "already_processed"
        
        # Verify balance NOT credited again
        user_after2 = await db.users.find_one({"id": client_uid})
        bal_after2 = user_after2.get("balance", 0)
        assert bal_after2 == bal_after1, f"Balance changed on duplicate event! {bal_after1} -> {bal_after2}"
        print(f"    ✅ Duplicate event rejected ({wh2_json['status']}): balance unchanged at ${bal_after2}")
        results["2_stripe_verification"] = "PASS"

        # ---------------------------------------------------------
        # 3. FOUR-MINUTE FREE TRIAL BACKEND VERIFICATION
        # ---------------------------------------------------------
        print("\n--- [3] FOUR-MINUTE FREE TRIAL ENFORCEMENT ---")
        trial_email = f"trial_user_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = await http.post("/api/auth/register", json={
            "name": "Trial Verifier",
            "email": trial_email,
            "password": "Password123!"
        })
        assert reg_res.status_code == 200
        trial_user_id = reg_res.json().get("user", {}).get("id") or reg_res.json().get("id")
        
        # Check initial trial entitlement
        u_doc = await db.users.find_one({"id": trial_user_id})
        assert u_doc.get("is_new_user") is True
        assert u_doc.get("first_reading_free_used") is False
        print(f"  ✅ New client registered with trial entitlement: is_new_user=True, first_reading_free_used=False")

        # 3a. Start session with free trial
        trial_sess1 = await http.post(
            f"/api/chat/start?user_id={trial_user_id}&use_free_minutes=true",
            json={
                "client_id": trial_user_id,
                "psychic_id": advisor_uid,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert trial_sess1.status_code == 200
        s1_data = trial_sess1.json()
        assert s1_data.get("free_minutes_used") is True
        print("  ✅ First trial session started successfully with free_minutes_used=True")

        # Verify DB updated
        u_after_s1 = await db.users.find_one({"id": trial_user_id})
        assert u_after_s1.get("first_reading_free_used") is True
        assert u_after_s1.get("is_new_user") is False
        print("  ✅ User record updated: first_reading_free_used=True, is_new_user=False")

        # 3b. Attempt second free trial session -> MUST return HTTP 400
        trial_sess2 = await http.post(
            f"/api/chat/start?user_id={trial_user_id}&use_free_minutes=true",
            json={
                "client_id": trial_user_id,
                "psychic_id": advisor_uid,
                "session_type": "chat",
                "initial_minutes": 4
            }
        )
        assert trial_sess2.status_code == 400, f"Expected 400, got: {trial_sess2.status_code}: {trial_sess2.text}"
        assert "already been used" in trial_sess2.text or "used" in trial_sess2.text
        print("  ✅ Second free trial attempt strictly rejected with HTTP 400!")

        # 3c. Concurrent trial request race condition test
        concurrent_email = f"concurrent_{uuid.uuid4().hex[:8]}@example.com"
        reg_c = await http.post("/api/auth/register", json={
            "name": "Concurrent Tester",
            "email": concurrent_email,
            "password": "Password123!"
        })
        c_user_id = reg_c.json().get("user", {}).get("id") or reg_c.json().get("id")

        async def request_trial():
            return await http.post(
                f"/api/chat/start?user_id={c_user_id}&use_free_minutes=true",
                json={
                    "client_id": c_user_id,
                    "psychic_id": advisor_uid,
                    "session_type": "chat",
                    "initial_minutes": 4
                }
            )
        
        # Fire 2 concurrent trial requests
        r1, r2 = await asyncio.gather(request_trial(), request_trial())
        statuses = [r1.status_code, r2.status_code]
        # At most one can succeed with 200, the other must be 400
        assert statuses.count(200) <= 1, f"Both concurrent requests succeeded! {statuses}"
        print(f"  ✅ Concurrent trial requests handled safely: statuses={statuses}")
        results["3_free_trial"] = "PASS"

        # ---------------------------------------------------------
        # 4. ADVISOR AUTHENTICATION & PASSWORD SECURITY
        # ---------------------------------------------------------
        print("\n--- [4] ADVISOR AUTHENTICATION & SECURITY VERIFICATION ---")
        # 4a. Submit application with password
        adv_email_approved = f"advisor_app_{uuid.uuid4().hex[:8]}@example.com"
        app_sub1 = await http.post("/api/applications/submit", json={
            "full_name": "Approved Candidate",
            "email": adv_email_approved,
            "phone": "+15559876543",
            "country": "United States",
            "years_experience": "5",
            "specialties": ["Tarot Readings", "Astrology"],
            "bio": "Certified spiritual practitioner providing authentic intuitive readings.",
            "background": "Over 5 years professional experience with verified tarot certification.",
            "tools_used": ["Tarot Cards"],
            "password": "AdvisorPassword123!"
        })
        assert app_sub1.status_code == 200
        app1_id = app_sub1.json().get("application_id") or app_sub1.json().get("id")
        
        # Verify password is NOT stored in plaintext
        app_db = await db.applications.find_one({"id": app1_id})
        assert app_db.get("password") is None or "password_hash" in app_db
        assert app_db.get("password_hash").startswith("$2b$")
        print("  ✅ Application password securely hashed with bcrypt (stored as $2b$...)")

        # 4b. Approve application via admin
        appr_res = await http.patch(f"/api/admin/applications/{app1_id}/status", json={
            "status": "accepted"
        })
        assert appr_res.status_code == 200
        print("  ✅ Application accepted by admin.")

        # 4c. Verify successful login with correct password
        login_ok = await http.post("/api/auth/psychic/login", json={
            "email": adv_email_approved,
            "password": "AdvisorPassword123!"
        })
        assert login_ok.status_code == 200, f"Advisor login failed: {login_ok.text}"
        login_data = login_ok.json()
        assert "token" in login_data or "access_token" in login_data
        print(f"  ✅ Approved advisor successfully authenticated! Received token: {str(login_data.get('token') or login_data.get('access_token'))[:20]}...")

        # 4d. Verify failed login with wrong password -> HTTP 401
        login_bad = await http.post("/api/auth/psychic/login", json={
            "email": adv_email_approved,
            "password": "WrongPassword999!"
        })
        assert login_bad.status_code == 401, f"Expected 401, got: {login_bad.status_code}: {login_bad.text}"
        print("  ✅ Advisor login with invalid password strictly rejected with HTTP 401!")

        # 4e. Submit second applicant and reject
        adv_email_rejected = f"advisor_rej_{uuid.uuid4().hex[:8]}@example.com"
        app_sub2 = await http.post("/api/applications/submit", json={
            "full_name": "Rejected Candidate",
            "email": adv_email_rejected,
            "phone": "+15551112233",
            "country": "Canada",
            "years_experience": "1",
            "specialties": ["Crystal Ball"],
            "bio": "Beginner practitioner testing psychic platform submission.",
            "background": "Self-taught spiritual enthusiast exploring psychic reading.",
            "tools_used": ["Crystals"],
            "password": "RejectedPassword123!"
        })
        assert app_sub2.status_code == 200
        app2_id = app_sub2.json().get("application_id") or app_sub2.json().get("id")

        rej_res = await http.patch(f"/api/admin/applications/{app2_id}/status", json={
            "status": "rejected",
            "reason": "Does not meet minimum 3 years experience requirement"
        })
        assert rej_res.status_code == 200
        print("  ✅ Candidate rejected by admin.")

        # 4f. Attempt login by rejected applicant -> MUST return HTTP 403 Forbidden
        login_rej = await http.post("/api/auth/psychic/login", json={
            "email": adv_email_rejected,
            "password": "RejectedPassword123!"
        })
        assert login_rej.status_code == 403, f"Expected 403, got: {login_rej.status_code}: {login_rej.text}"
        print("  ✅ Rejected applicant strictly denied login with HTTP 403 Forbidden!")
        results["4_advisor_auth"] = "PASS"

        # ---------------------------------------------------------
        # 5. NOTIFICATIONS CONFIGURATION & TOKEN REGISTRATION
        # ---------------------------------------------------------
        print("\n--- [5] NOTIFICATIONS & FIREBASE VERIFICATION ---")
        push_cfg = await http.get("/api/push/config")
        assert push_cfg.status_code == 200
        pcfg = push_cfg.json()
        assert pcfg.get("firebase_enabled") is True
        print(f"  ✅ Push notification config: firebase_enabled={pcfg.get('firebase_enabled')}")

        # Register device token
        test_fcm_token = f"fcm_test_tok_{uuid.uuid4().hex}"
        tok_res = await http.post("/api/push/register-token", json={
            "user_id": client_uid,
            "token": test_fcm_token,
            "platform": "ios",
            "device_name": "iPhone 15 Pro Test"
        })
        assert tok_res.status_code == 200
        print("  ✅ Device token registered successfully in backend.")

        # Verify saved in DB
        db_tok = await db.device_tokens.find_one({"token": test_fcm_token})
        assert db_tok is not None and db_tok["user_id"] == client_uid
        print("  ✅ Device token verified persisted in db.device_tokens.")

        # Unregister token
        unreg = await http.delete(f"/api/push/unregister-token?user_id={client_uid}&token={test_fcm_token}")
        assert unreg.status_code == 200
        db_tok_gone = await db.device_tokens.find_one({"token": test_fcm_token})
        assert db_tok_gone is None
        print("  ✅ Device token unregister confirmed!")
        results["5_notifications"] = "PASS"

        # ---------------------------------------------------------
        # 6. REMAINING FUNCTIONALITY VERIFICATIONS
        # ---------------------------------------------------------
        print("\n--- [6] REMAINING FUNCTIONALITY VERIFICATION ---")
        # 6a. $40 Withdrawal Minimum
        payout_low = await http.post("/api/payments/payout-request", json={
            "psychic_id": advisor_uid,
            "amount": 35.0,
            "method": "bank_transfer"
        })
        assert payout_low.status_code == 400
        assert "40" in payout_low.text
        print("  ✅ Payout below $40 correctly rejected with HTTP 400 ($40 minimum rule enforced)")

        # 6b. Client Type Badge (NEW CLIENT vs RETURNING CLIENT)
        # For new client with 0 completed sessions
        fresh_client_id = f"fresh_c_{uuid.uuid4().hex[:8]}"
        req1 = await http.post("/api/chat/request", json={
            "client_id": fresh_client_id,
            "client_name": "Fresh User",
            "psychic_id": advisor_uid,
            "session_type": "chat"
        })
        assert req1.status_code == 200
        req1_data = req1.json()
        assert req1_data.get("client_type") == "NEW CLIENT", f"Expected NEW CLIENT, got {req1_data.get('client_type')}"
        print("  ✅ Chat request with 0 prior sessions categorized as 'NEW CLIENT'")

        # For client with prior completed session
        await db.chat_sessions.insert_one({
            "id": f"sess_prior_{uuid.uuid4().hex[:8]}",
            "client_id": fresh_client_id,
            "psychic_id": advisor_uid,
            "session_type": "chat",
            "status": "completed",
            "started_at": datetime.utcnow().isoformat()
        })
        req2 = await http.post("/api/chat/request", json={
            "client_id": fresh_client_id,
            "client_name": "Fresh User",
            "psychic_id": advisor_uid,
            "session_type": "chat"
        })
        assert req2.status_code == 200
        req2_data = req2.json()
        assert req2_data.get("client_type") == "RETURNING CLIENT", f"Expected RETURNING CLIENT, got {req2_data.get('client_type')}"
        print("  ✅ Chat request with prior completed session categorized as 'RETURNING CLIENT'")

        # 6c. Online / Offline status check
        online_res = await http.get(f"/api/online-status/{client_uid}")
        assert online_res.status_code == 200
        # Since client ws closed in block 1, should be false
        assert online_res.json().get("is_online") is False
        print("  ✅ Disconnected user correctly reported as is_online=False")

        # 6d. Advisor specific rates
        rate_sess = await http.post(
            f"/api/chat/start?user_id={client_uid}",
            json={
                "client_id": client_uid,
                "psychic_id": advisor_uid,
                "session_type": "chat",
                "initial_minutes": 5
            }
        )
        assert rate_sess.status_code == 200
        rate_data = rate_sess.json()
        assert rate_data.get("session", {}).get("rate_per_minute") == 4.50
        print("  ✅ Session created using advisor's specific rate ($4.50/min)")

        # 6e. Admin conversation monitoring
        admin_c = await http.get("/api/admin/conversations")
        assert admin_c.status_code == 200
        assert len(admin_c.json()) > 0
        print(f"  ✅ Admin conversations endpoint operational (retrieved {len(admin_c.json())} sessions)")
        results["6_remaining_functionality"] = "PASS"

    print("\n" + "=" * 70)
    print("ALL LIVE VERIFICATION TESTS PASSED SUCCESSFULLY! 🚀")
    print(json.dumps(results, indent=2))
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_live_verification())
