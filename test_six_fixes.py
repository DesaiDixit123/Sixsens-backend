import asyncio
import httpx
import uuid
import sys
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))
from server import app, db

async def run_tests():
    print("=" * 70)
    print("VERIFYING THE SIX REPORTED BUG FIXES WITH REGRESSION TESTS")
    print("=" * 70)

    transport = httpx.ASGITransport(app=app)
    results = {}

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=25.0, follow_redirects=True) as client:

        # -------------------------------------------------------------
        # FIX 1: get_zodiac_sign helper function in routes/auth.py
        # -------------------------------------------------------------
        print("\n[FIX 1] Testing get_zodiac_sign in registration...")
        from routes.auth import get_zodiac_sign
        
        # Unit checks on zodiac calculation
        assert get_zodiac_sign("1990-07-25") == "Leo", f"Expected Leo, got {get_zodiac_sign('1990-07-25')}"
        assert get_zodiac_sign("1994-11-08") == "Scorpio", f"Expected Scorpio, got {get_zodiac_sign('1994-11-08')}"
        assert get_zodiac_sign("2000-03-21") == "Aries", f"Expected Aries, got {get_zodiac_sign('2000-03-21')}"
        assert get_zodiac_sign("1988-01-15") == "Capricorn", f"Expected Capricorn, got {get_zodiac_sign('1988-01-15')}"
        assert get_zodiac_sign("") is None
        print("  ✅ get_zodiac_sign() unit tests passed for all dates!")

        # Integration check: register endpoint with birth_date
        u_email = f"zodiac_fix_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = await client.post("/api/auth/register", json={
            "name": "Zodiac Test User",
            "email": u_email,
            "password": "Password123!",
            "birth_date": "1996-08-14"
        })
        assert reg_res.status_code == 200, f"Registration failed with birth_date: {reg_res.text}"
        u_data = reg_res.json().get("user", {})
        u_id = u_data.get("id")
        # Verify in DB that zodiac was correctly saved
        db_u = await db.users.find_one({"email": u_email})
        assert db_u is not None and db_u.get("zodiac_sign") == "Leo"
        print(f"  ✅ Registration succeeded with birth_date='1996-08-14' -> zodiac_sign='Leo' correctly saved in db.users!")
        results["fix_1_get_zodiac_sign"] = "PASS"

        # -------------------------------------------------------------
        # FIX 2: GET /api/wallet/{user_id} direct endpoint
        # -------------------------------------------------------------
        print("\n[FIX 2] Testing direct GET /api/wallet/{user_id} endpoint...")
        # Query wallet by user_id directly without token param
        w_res = await client.get(f"/api/wallet/{u_id}")
        assert w_res.status_code == 200, f"GET /api/wallet/{u_id} failed: {w_res.text}"
        w_json = w_res.json()
        assert "balance" in w_json
        assert "is_new_user" in w_json
        assert "first_reading_free_used" in w_json
        assert w_json.get("balance") == 0.0
        assert w_json.get("is_new_user") is True
        print(f"  ✅ GET /api/wallet/{u_id} returned HTTP 200 OK: balance=${w_json['balance']}, is_new_user={w_json['is_new_user']}!")
        
        # Test 404 for unknown user_id
        w_404 = await client.get(f"/api/wallet/non_existent_{uuid.uuid4().hex[:6]}")
        assert w_404.status_code == 404
        print("  ✅ GET /api/wallet/non_existent correctly returned HTTP 404 Not Found!")
        results["fix_2_wallet_user_id"] = "PASS"

        # -------------------------------------------------------------
        # FIX 3: Application approval profile ingestion
        # -------------------------------------------------------------
        print("\n[FIX 3] Testing application approval profile ingestion (password, video, answers)...")
        adv_email = f"ingest_test_{uuid.uuid4().hex[:8]}@example.com"
        adv_pwd = "IngestPassword123!"
        sample_intro_video = "https://res.cloudinary.com/sixthsense/video/upload/intro_adv.mp4"
        sample_answers = [
            {"q": "How long have you practiced tarot?", "a": "10 years certified tarot reading."},
            {"q": "What is your ethical reading philosophy?", "a": "Honesty, empathy, and positive empowerment."}
        ]
        
        sub_res = await client.post("/api/applications/submit", json={
            "full_name": "Ingestion Test Advisor",
            "email": adv_email,
            "phone": "+15554443322",
            "country": "United States",
            "years_experience": "10",
            "specialties": ["Tarot Readings", "Clairaudience"],
            "bio": "Experienced spiritual guide.",
            "background": "Generational psychic practitioner with accredited certification.",
            "tools_used": ["Tarot Cards"],
            "password": adv_pwd,
            "video_url": sample_intro_video,
            "video_duration": 45,
            "intro_video_url": sample_intro_video,
            "intro_video_duration": 45,
            "interview_answers": sample_answers
        })
        assert sub_res.status_code == 200, f"Submit application failed: {sub_res.text}"
        app_id = sub_res.json().get("id")
        print(f"  Application submitted with ID={app_id}")

        # Approve application via admin PATCH
        appr_res = await client.patch(f"/api/admin/applications/{app_id}/status", json={"status": "approved"})
        assert appr_res.status_code == 200, f"Approval failed: {appr_res.text}"

        # Verify psychic document in MongoDB
        db_psy = await db.psychics.find_one({"email": adv_email})
        assert db_psy is not None, "Psychic record was not created in db.psychics!"
        assert db_psy.get("status") == "approved", f"Expected status 'approved', got: {db_psy.get('status')}"
        assert db_psy.get("password_hash") is not None, "password_hash missing from psychic record!"
        assert db_psy.get("password_hash").startswith("$2b$"), "password_hash is not bcrypt!"
        assert db_psy.get("intro_video_url") == sample_intro_video, "intro_video_url not preserved!"
        assert db_psy.get("interview_answers") == sample_answers, "interview_answers not preserved!"
        print("  ✅ db.psychics verified: status='approved', password_hash bcrypt, intro_video_url, interview_answers fully preserved!")

        # Verify advisor can log in using their password
        adv_login = await client.post("/api/auth/psychic/login", json={
            "email": adv_email,
            "password": adv_pwd
        })
        assert adv_login.status_code == 200, f"Advisor login failed: {adv_login.text}"
        assert adv_login.json().get("token") is not None
        print("  ✅ Approved advisor logged in successfully using password submitted in application!")
        results["fix_3_application_ingestion"] = "PASS"

        # -------------------------------------------------------------
        # FIX 4: GET /api/admin/payouts endpoint
        # -------------------------------------------------------------
        print("\n[FIX 4] Testing GET /api/admin/payouts endpoint...")
        # Create a test payout request
        psy_payout_id = db_psy.get("id")
        await db.psychics.update_one({"id": psy_payout_id}, {"$set": {"available_balance": 100.0}})
        req_payout = await client.post("/api/payments/payout-request", json={
            "psychic_id": psy_payout_id,
            "amount": 55.0,
            "method": "bank_transfer"
        })
        assert req_payout.status_code == 200, f"Payout request failed: {req_payout.text}"
        
        # Query admin payouts list
        admin_payouts_res = await client.get("/api/admin/payouts")
        assert admin_payouts_res.status_code == 200, f"GET /api/admin/payouts failed: {admin_payouts_res.text}"
        payouts_list = admin_payouts_res.json()
        assert isinstance(payouts_list, list)
        matching = [p for p in payouts_list if p.get("psychic_id") == psy_payout_id and p.get("amount") == 55.0]
        assert len(matching) > 0, "Requested payout not found in /api/admin/payouts!"
        print(f"  ✅ GET /api/admin/payouts returned HTTP 200 OK with {len(payouts_list)} records (found created $55 payout)!")
        results["fix_4_admin_payouts_endpoint"] = "PASS"

        # -------------------------------------------------------------
        # FIX 5: Sales single creation and toggle/delete persistence
        # -------------------------------------------------------------
        print("\n[FIX 5] Testing sales backend operations (create, toggle, delete)...")
        sale_data = {
            "name": f"Regression Sale {uuid.uuid4().hex[:6]}",
            "description": "Exclusive 15% discount for full moon spiritual guidance",
            "discount_percentage": 15,
            "event_type": "full_moon",
            "start_date": datetime.utcnow().isoformat(),
            "end_date": datetime.utcnow().isoformat(),
            "is_mandatory": True
        }
        create_sale_res = await client.post("/api/admin/sales", json=sale_data)
        assert create_sale_res.status_code == 200, f"Create sale failed: {create_sale_res.text}"
        created_sale = create_sale_res.json().get("sale", {})
        sale_id = created_sale.get("id")
        assert sale_id is not None
        print(f"  ✅ Backend POST /api/admin/sales created sale ID={sale_id}")

        # Verify only 1 record exists in DB for this ID
        count = await db.sales.count_documents({"id": sale_id})
        assert count == 1, f"Expected exactly 1 sale document in DB, found: {count}"
        print("  ✅ Verified exactly 1 document created in MongoDB db.sales (no duplicate creation)!")

        # Toggle sale status
        toggle_res = await client.patch(f"/api/admin/sales/{sale_id}/toggle")
        assert toggle_res.status_code == 200
        print("  ✅ Backend PATCH /api/admin/sales/{id}/toggle toggled sale status successfully!")

        # Delete sale
        del_sale = await client.delete(f"/api/admin/sales/{sale_id}")
        assert del_sale.status_code == 200
        assert await db.sales.find_one({"id": sale_id}) is None
        print("  ✅ Backend DELETE /api/admin/sales/{id} successfully deleted sale from database!")
        results["fix_5_sales_persistence"] = "PASS"

        # -------------------------------------------------------------
        # FIX 6: Hiring credential approval & waitlist deletion action
        # -------------------------------------------------------------
        print("\n[FIX 6] Testing hiring waitlist deletion action...")
        test_wl_email = f"wl_action_{uuid.uuid4().hex[:6]}@example.com"
        await db.hiring_waitlist.insert_one({
            "id": str(uuid.uuid4()),
            "email": test_wl_email,
            "name": "Applicant Credential Test",
            "created_at": datetime.utcnow().isoformat()
        })
        assert await db.hiring_waitlist.find_one({"email": test_wl_email}) is not None

        # Call DELETE endpoint that handleApplicantDecision invokes
        wl_del_res = await client.delete(f"/api/admin/hiring-waitlist/{test_wl_email}")
        assert wl_del_res.status_code == 200
        assert await db.hiring_waitlist.find_one({"email": test_wl_email}) is None
        print(f"  ✅ DELETE /api/admin/hiring-waitlist/{test_wl_email} confirmed: candidate removed from database!")
        results["fix_6_hiring_waitlist_action"] = "PASS"

    print("\n" + "=" * 70)
    print("ALL 6 REPORTED BUG FIXES VERIFIED SUCCESSFULLY! 🚀")
    import json
    print(json.dumps(results, indent=2))
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_tests())
