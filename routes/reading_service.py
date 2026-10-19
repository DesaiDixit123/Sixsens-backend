import uuid
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

async def notify_advisor_new_reading(db, question_data: dict):
    """
    When a client submits a reading request:
    1. Send in-app notification to advisor (inserted into db.notifications)
    2. Send FCM push notification to advisor
    3. Broadcast WebSocket event to advisor
    """
    try:
        from routes.websocket import manager, expand_user_aliases
        from routes.push_notifications import send_firebase_push_to_user
    except Exception as e:
        logger.warning(f"Failed to import notification helpers: {e}")
        manager = None
        send_firebase_push_to_user = None

    psychic_id = question_data.get("psychic_id")
    psychic_email = question_data.get("psychic_email")
    client_name = question_data.get("client_name") or "Client"
    price = float(question_data.get("price") or 12.0)
    question_text = str(question_data.get("question_text") or question_data.get("question") or "")
    question_id = str(question_data.get("id") or question_data.get("_id") or "")
    preview_text = (question_text[:70] + "...") if len(question_text) > 70 else question_text

    # Find canonical psychic info if needed
    if psychic_id or psychic_email:
        psy_query = []
        if psychic_id:
            psy_query.append({"id": psychic_id})
        if psychic_email:
            psy_query.append({"email": psychic_email})
        if psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0"):
            psy_query.extend([
                {"id": "psy-001"},
                {"id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"},
                {"email": "testpsychic@sixthsense.com"}
            ])
        psy_doc = await db.psychics.find_one({"$or": psy_query})
        if psy_doc:
            if not psychic_id:
                psychic_id = psy_doc.get("id")
            if not psychic_email:
                psychic_email = psy_doc.get("email")

    now = datetime.utcnow()
    notif_title = "New Reading Request! 🔮"
    notif_body = f"New reading request from {client_name} (${price:.2f}): \"{preview_text}\""

    # Determine all target user IDs for the advisor
    target_ids = set()
    if psychic_id:
        target_ids.add(psychic_id)
    if psychic_email:
        target_ids.add(psychic_email)
    if psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0") or psychic_email == "testpsychic@sixthsense.com":
        target_ids.update(["psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com"])

    # 1. Insert In-App Notifications into db.notifications
    for tid in target_ids:
        try:
            notif_id = str(uuid.uuid4())
            await db.notifications.insert_one({
                "id": notif_id,
                "user_id": tid,
                "user_type": "psychic",
                "title": notif_title,
                "body": notif_body,
                "message": notif_body,
                "type": "reading",
                "notification_type": "reading",
                "related_id": question_id,
                "question_id": question_id,
                "price": price,
                "is_read": False,
                "read": False,
                "created_at": now,
                "data": {
                    "type": "reading_request",
                    "question_id": question_id,
                    "client_name": client_name,
                    "price": str(price),
                }
            })
        except Exception as e:
            logger.error(f"Error creating in-app notification for {tid}: {e}")

    # 2. Push Notification via Firebase
    if send_firebase_push_to_user:
        for tid in target_ids:
            try:
                await send_firebase_push_to_user(
                    db,
                    user_id=tid,
                    title=notif_title,
                    body=f"New reading request from {client_name} (${price:.2f})",
                    data={"type": "reading_request", "question_id": question_id, "price": str(price)}
                )
            except Exception as pe:
                logger.warning(f"Error sending push to {tid}: {pe}")

    # 3. WebSocket Real-time Event
    if manager:
        for tid in target_ids:
            try:
                await manager.send_personal_message({
                    "type": "new_reading_request",
                    "question_id": question_id,
                    "client_name": client_name,
                    "price": price,
                    "question_text": question_text,
                    "title": notif_title,
                    "message": f"New reading request from {client_name} (${price:.2f})"
                }, tid)
            except Exception as we:
                logger.warning(f"Error sending WS to {tid}: {we}")


async def process_reading_completion(db, question_id: str, video_response_url: str):
    """
    When an advisor completes a video reading:
    1. Update db.questions status='completed', video_response_url, completed_at
    2. Credit advisor wallet_balance, available_balance, total_earnings, total_readings
    3. Insert transaction into db.transactions
    4. Send earning notification & push to advisor
    5. Send completed reading notification & push to client
    """
    try:
        from routes.websocket import manager
        from routes.push_notifications import send_firebase_push_to_user
    except Exception as e:
        logger.warning(f"Failed to import notification helpers: {e}")
        manager = None
        send_firebase_push_to_user = None

    query = {"$or": [{"id": question_id}, {"_id": question_id}]}
    try:
        from bson import ObjectId
        if ObjectId.is_valid(question_id):
            query["$or"].append({"_id": ObjectId(question_id)})
    except Exception:
        pass

    question_doc = await db.questions.find_one(query)
    if not question_doc:
        logger.warning(f"Question {question_id} not found for completion")
        return None

    now = datetime.utcnow()
    # 1. Update question record
    await db.questions.update_one(
        query,
        {
            "$set": {
                "status": "completed",
                "video_response_url": video_response_url,
                "completed_at": now
            }
        }
    )

    price = float(question_doc.get("price") or 12.0)
    psychic_id = question_doc.get("psychic_id")
    psychic_email = question_doc.get("psychic_email")
    client_id = question_doc.get("client_id")
    client_name = question_doc.get("client_name") or "Client"

    # 2. Look up psychic record
    psy_query = []
    if psychic_id:
        psy_query.append({"id": psychic_id})
    if psychic_email:
        psy_query.append({"email": psychic_email})
    if psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0"):
        psy_query.extend([
            {"id": "psy-001"},
            {"id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"},
            {"email": "testpsychic@sixthsense.com"}
        ])

    psy_doc = await db.psychics.find_one({"$or": psy_query}) if psy_query else None
    actual_pid = (psy_doc or {}).get("id") or psychic_id
    actual_pemail = (psy_doc or {}).get("email") or psychic_email
    psychic_name = (psy_doc or {}).get("name") or "Your Advisor"

    # Target IDs for the psychic
    psychic_target_ids = set()
    if actual_pid: psychic_target_ids.add(actual_pid)
    if actual_pemail: psychic_target_ids.add(actual_pemail)
    if actual_pid in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0") or actual_pemail == "testpsychic@sixthsense.com":
        psychic_target_ids.update(["psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com"])

    # 3. Credit advisor wallet & earnings
    if psychic_target_ids:
        credit_or = [{"id": tid} for tid in psychic_target_ids] + [{"email": tid} for tid in psychic_target_ids]
        await db.psychics.update_one(
            {"$or": credit_or},
            {
                "$inc": {
                    "wallet_balance": price,
                    "available_balance": price,
                    "total_earnings": price,
                    "total_earned": price,
                    "completed_readings": 1,
                    "total_readings": 1,
                }
            }
        )
        await db.users.update_one(
            {"$or": credit_or},
            {
                "$inc": {
                    "balance": price,
                    "wallet_balance": price,
                    "total_earned": price,
                }
            }
        )

        # 4. Insert Earning Transaction
        tx_id = str(uuid.uuid4())
        await db.transactions.insert_one({
            "id": tx_id,
            "transaction_id": tx_id,
            "user_id": actual_pid or actual_pemail,
            "psychic_id": actual_pid,
            "type": "reading_earning",
            "amount": price,
            "gross": price,
            "description": f"Earned from video reading for {client_name}",
            "status": "completed",
            "created_at": now
        })

        # 5. Advisor Notification (💰 You Earned $X.00!)
        earning_title = f"💰 You Earned ${price:.2f}!"
        earning_body = f"Your video reading for {client_name} was delivered! ${price:.2f} has been added to your earnings."
        
        for tid in psychic_target_ids:
            try:
                notif_id = str(uuid.uuid4())
                await db.notifications.insert_one({
                    "id": notif_id,
                    "user_id": tid,
                    "user_type": "psychic",
                    "title": earning_title,
                    "body": earning_body,
                    "message": earning_body,
                    "type": "earning",
                    "notification_type": "earning",
                    "related_id": str(question_doc.get("id") or question_id),
                    "is_read": False,
                    "read": False,
                    "created_at": now,
                    "data": {
                        "type": "earning",
                        "amount": str(price),
                        "question_id": str(question_doc.get("id") or question_id),
                        "client_name": client_name
                    }
                })
            except Exception as ne:
                logger.error(f"Error creating earning notification for advisor {tid}: {ne}")

            # Push notification to advisor
            if send_firebase_push_to_user:
                try:
                    await send_firebase_push_to_user(
                        db,
                        user_id=tid,
                        title=earning_title,
                        body=f"Reading for {client_name} completed! ${price:.2f} added to your earnings.",
                        data={"type": "earning", "amount": str(price), "question_id": str(question_doc.get("id") or question_id)}
                    )
                except Exception as pe:
                    logger.warning(f"Error sending earning push to advisor {tid}: {pe}")

            # WebSocket notification to advisor
            if manager:
                try:
                    await manager.send_personal_message({
                        "type": "reading_completed",
                        "earned": price,
                        "title": earning_title,
                        "message": f"Reading for {client_name} completed."
                    }, tid)
                except Exception as we:
                    logger.warning(f"Error sending earning WS to advisor {tid}: {we}")

    # 6. Client Notification (Your Reading is Ready! ✨)
    if client_id and client_id != "guest":
        client_title = "Your Reading is Ready! ✨"
        client_body = f"{psychic_name} has delivered your video answer! Watch it now in your Readings tab."
        client_notif_id = str(uuid.uuid4())
        try:
            await db.notifications.insert_one({
                "id": client_notif_id,
                "user_id": client_id,
                "user_type": "client",
                "title": client_title,
                "body": client_body,
                "message": client_body,
                "type": "reading_completed",
                "notification_type": "reading_update",
                "related_id": str(question_doc.get("id") or question_id),
                "question_id": str(question_doc.get("id") or question_id),
                "is_read": False,
                "read": False,
                "created_at": now,
                "data": {
                    "type": "reading_completed",
                    "question_id": str(question_doc.get("id") or question_id),
                    "video_url": video_response_url,
                    "psychic_name": psychic_name
                }
            })
        except Exception as ne:
            logger.error(f"Error creating client reading notification: {ne}")

        if send_firebase_push_to_user:
            try:
                await send_firebase_push_to_user(
                    db,
                    user_id=client_id,
                    title=client_title,
                    body=f"{psychic_name} has completed your reading. Watch the video now!",
                    data={"type": "reading_completed", "question_id": str(question_doc.get("id") or question_id)}
                )
            except Exception as pe:
                logger.warning(f"Error sending client push: {pe}")

        if manager:
            try:
                await manager.send_personal_message({
                    "type": "reading_delivered",
                    "question_id": str(question_doc.get("id") or question_id),
                    "video_url": video_response_url,
                    "title": client_title,
                    "message": f"{psychic_name} has delivered your video answer!"
                }, client_id)
            except Exception as we:
                logger.warning(f"Error sending client WS: {we}")

    return question_doc
