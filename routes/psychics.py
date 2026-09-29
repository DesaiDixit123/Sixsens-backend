from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
from typing import List, Optional, Set
from motor.motor_asyncio import AsyncIOMotorDatabase
from models.psychic import Psychic, PsychicListResponse
import random

router = APIRouter(prefix="/psychics", tags=["psychics"])

# Track deleted psychic IDs in memory (will be synced from DB on startup)
DELETED_PSYCHIC_IDS: Set[str] = set()

# Mock psychic data
# First 30 advisors have is_first_hired=True and are exempt from "New Advisor" classification
# New Advisor logic: < 300 readings = is_new:True, UNLESS is_first_hired=True
# Status: pending (awaiting approval), approved (visible to users), rejected, suspended, banned
MOCK_PSYCHICS = [
    {
        "id": "psy-001",
        "name": "Luna Starweaver",
        "email": "luna@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400",
        "description": "Gifted tarot cards reader with 10 years of experience helping people find clarity in love and life.",
        "about_me": "I've been reading tarot cards since I was 16. My grandmother passed down her gift to me.",
        "years_experience": 10,
        "specialties": ["Tarot Cards", "Intuitive"],
        "reading_methods": ["Tarot Cards", "Tarot Cards"],
        "topics": ["Love & Relationships", "Life Path", "Career"],
        "languages": ["English", "Spanish"],
        "chat_rate": 3.99,
        "phone_rate": 5.99,
        "video_call_rate": 7.99,
        "online_status": "online",
        "average_rating": 4.9,
        "total_reviews": 342,
        "total_readings": 1256,
        "is_featured": True,
        "is_new": False,
        "is_first_hired": True,  # Founding Advisor #1
        "advisor_number": 1,
        "status": "approved",  # Visible to users
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "offers_standard_readings": True,
        "offers_emergency_readings": True,
        "can_receive_recorded_questions": True,
        "free_chat_enabled": True
    },
    {
        "id": "psy-002",
        "name": "Mystic Rose",
        "email": "rose@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=400",
        "description": "Crystal ball gazing expert specializing in future predictions and spiritual guidance.",
        "about_me": "I connect with the spiritual realm through my crystal ball passed down through generations.",
        "years_experience": 8,
        "specialties": ["Crystal Ball", "Spiritual Guidance"],
        "reading_methods": ["Crystal Ball", "Pendulum"],
        "topics": ["Love & Relationships", "Spiritual Guidance", "Money & Abundance"],
        "languages": ["English"],
        "chat_rate": 4.99,
        "phone_rate": 6.99,
        "video_call_rate": 8.99,
        "online_status": "online",
        "average_rating": 4.8,
        "total_reviews": 218,
        "total_readings": 890,
        "is_featured": True,
        "is_new": False,
        "is_first_hired": True,  # Founding Advisor #2
        "advisor_number": 2,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "can_receive_recorded_questions": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-003",
        "name": "Celestia Moon",
        "email": "celestia@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=400",
        "description": "Astrology expert with deep knowledge of birth charts and planetary alignments.",
        "about_me": "Certified astrologer with a passion for helping people understand their cosmic path.",
        "years_experience": 12,
        "specialties": ["Astrology", "Numerology"],
        "reading_methods": ["Astrology", "Numerology"],
        "topics": ["Career & Finance", "Life Path", "Marriage & Family"],
        "languages": ["English", "French"],
        "chat_rate": 5.99,
        "phone_rate": 7.99,
        "video_call_rate": 9.99,
        "online_status": "busy",
        "average_rating": 4.95,
        "total_reviews": 456,
        "total_readings": 2100,
        "is_featured": True,
        "is_new": False,
        "is_first_hired": True,  # Founding Advisor #3
        "advisor_number": 3,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "can_receive_recorded_questions": False,  # Busy, not online
        "status": "approved",
        "free_chat_enabled": False
    },
    {
        "id": "psy-004",
        "name": "Phoenix Aura",
        "email": "phoenix@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400",
        "description": "Empathic reader specializing in energy healing and aura cleansing.",
        "about_me": "I can feel and interpret energy fields to provide deep spiritual insights.",
        "years_experience": 6,
        "specialties": ["Intuitive", "Energy Healing"],
        "reading_methods": ["Intuitive", "No Tools"],
        "topics": ["Spiritual Guidance", "Life Path", "Dream Analysis"],
        "languages": ["English"],
        "chat_rate": 2.99,
        "phone_rate": 4.49,
        "video_call_rate": 5.99,
        "online_status": "online",
        "average_rating": 4.7,
        "total_reviews": 167,
        "total_readings": 534,
        "is_featured": False,
        "is_new": False,  # 534 > 300
        "is_first_hired": True,  # Founding Advisor #4
        "advisor_number": 4,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": False,
        "offers_video_call": False,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-005",
        "name": "Sage Whisper",
        "email": "sage@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=400",
        "description": "Dream interpreter specialist.",
        "about_me": "I help unlock the messages hidden in your dreams and explore your past lives.",
        "years_experience": 15,
        "specialties": ["Dream Analysis", "Personal Growth"],
        "reading_methods": ["Intuitive", "Meditation"],
        "topics": ["Dream Analysis", "Personal Growth Readings", "Spiritual Guidance"],
        "languages": ["English", "Portuguese"],
        "chat_rate": 6.99,
        "phone_rate": 8.99,
        "video_call_rate": 10.99,
        "online_status": "offline",
        "average_rating": 4.85,
        "total_reviews": 389,
        "total_readings": 1678,
        "is_featured": True,
        "is_new": False,  # 1678 > 300
        "is_first_hired": True,  # Founding Advisor #5
        "advisor_number": 5,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": False,  # Has disabled recorded readings
        "status": "approved",
        "free_chat_enabled": False
    },
    {
        "id": "psy-006",
        "name": "Aurora Dawn",
        "email": "aurora@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1580489944761-15a19d654956?w=400",
        "description": "New to the platform but experienced in angel card readings and divine messages.",
        "about_me": "I channel messages from angels and spirit guides to bring you comfort and direction.",
        "years_experience": 4,
        "specialties": ["Angel Cards", "Tarot Cards"],
        "reading_methods": ["Tarot Cards", "Angel Cards"],
        "topics": ["Love & Relationships", "Spiritual Guidance", "Family & Friends"],
        "languages": ["English"],
        "chat_rate": 1.99,
        "phone_rate": 3.49,
        "video_call_rate": 4.99,
        "online_status": "online",
        "average_rating": 4.6,
        "total_reviews": 45,
        "total_readings": 98,  # Less than 300
        "is_featured": False,
        "is_new": True,  # NOT a founding advisor, < 300 readings = New
        "is_first_hired": False,
        "advisor_number": 35,  # Joined after first 30
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-007",
        "name": "Crystal Jade",
        "email": "jade@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400",
        "description": "Tarot Cards and rune expert bringing ancient wisdom to modern questions.",
        "about_me": "I combine tarot cards with Norse runes for deeply insightful readings.",
        "years_experience": 7,
        "specialties": ["Tarot Cards", "Runes"],
        "reading_methods": ["Tarot Cards", "Runes"],
        "topics": ["Career & Finance", "Love & Relationships", "Money & Abundance"],
        "languages": ["English", "German"],
        "chat_rate": 3.49,
        "phone_rate": 5.49,
        "video_call_rate": 6.99,
        "online_status": "online",
        "average_rating": 4.75,
        "total_reviews": 234,
        "total_readings": 756,
        "is_featured": False,
        "is_new": False,  # 756 > 300
        "is_first_hired": True,  # Founding Advisor #6
        "advisor_number": 6,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-008",
        "name": "Violet Spirit",
        "email": "violet@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1489424731084-a5d8b219a5bb?w=400",
        "description": "Pet psychic and animal communicator connecting you with your furry friends.",
        "about_me": "I've been communicating with animals since childhood. Let me help you understand your pets.",
        "years_experience": 9,
        "specialties": ["Pet Psychic", "Intuitive"],
        "reading_methods": ["Intuitive", "No Tools"],
        "topics": ["Pet Readings", "Family & Friends", "Spiritual Guidance"],
        "languages": ["English"],
        "chat_rate": 4.49,
        "phone_rate": 6.49,
        "video_call_rate": 7.99,
        "online_status": "busy",
        "average_rating": 4.9,
        "total_reviews": 312,
        "total_readings": 945,
        "is_featured": True,
        "is_new": False,
        "is_first_hired": True,  # Founding Advisor #7
        "advisor_number": 7,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": False,
        "offers_video_call": False,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": False
    },
    {
        "id": "psy-009",
        "name": "Indigo Soul",
        "email": "indigo@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1487412720507-e7ab37603c6f?w=400",
        "description": "Medium connecting you with loved ones who have passed on.",
        "about_me": "I bridge the gap between this world and the next with compassion and clarity.",
        "years_experience": 11,
        "specialties": ["Mediumship", "Intuitive"],
        "reading_methods": ["Mediumship", "Intuitive"],
        "topics": ["Spiritual Guidance", "Family & Friends", "Life Path"],
        "languages": ["English", "Italian"],
        "chat_rate": 5.49,
        "phone_rate": 7.49,
        "video_call_rate": 9.49,
        "online_status": "offline",
        "average_rating": 4.88,
        "total_reviews": 267,
        "total_readings": 823,
        "is_featured": False,
        "is_new": False,
        "is_first_hired": True,  # Founding Advisor #8
        "advisor_number": 8,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": False
    },
    {
        "id": "psy-010",
        "name": "Nova Light",
        "email": "nova@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=400",
        "description": "Fresh voice in psychic readings with a modern approach to ancient wisdom.",
        "about_me": "Combining traditional methods with contemporary insights for relatable readings.",
        "years_experience": 2,
        "specialties": ["Tarot Cards", "Intuitive"],
        "reading_methods": ["Tarot Cards", "Pendulum"],
        "topics": ["Love & Relationships", "Career & Finance", "Life Path"],
        "languages": ["English"],
        "chat_rate": 2.49,
        "phone_rate": 3.99,
        "video_call_rate": 5.49,
        "online_status": "online",
        "average_rating": 4.5,
        "total_reviews": 28,
        "total_readings": 67,  # Less than 300
        "is_featured": False,
        "is_new": True,  # NOT a founding advisor, < 300 = New
        "is_first_hired": False,
        "advisor_number": 42,  # Joined after first 30
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-011",
        "name": "Ember Flame",
        "email": "ember@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=400",
        "description": "Passionate love specialist helping you navigate matters of the heart.",
        "about_me": "I specialize exclusively in love readings because I believe love is the most powerful force.",
        "years_experience": 5,
        "specialties": ["Love Specialist", "Tarot Cards"],
        "reading_methods": ["Tarot Cards", "Intuitive"],
        "topics": ["Love & Relationships", "Marriage & Family", "Family & Friends"],
        "languages": ["English", "Spanish"],
        "chat_rate": 3.99,
        "phone_rate": 5.99,
        "video_call_rate": 7.49,
        "online_status": "online",
        "average_rating": 4.82,
        "total_reviews": 198,
        "total_readings": 612,
        "is_featured": False,
        "is_new": False,  # 612 > 300
        "is_first_hired": True,  # Founding Advisor #9
        "advisor_number": 9,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-012",
        "name": "Storm Oracle",
        "email": "storm@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=400",
        "description": "Career and finance expert with a gift for seeing business opportunities.",
        "about_me": "Former business consultant turned psychic. I help entrepreneurs and professionals.",
        "years_experience": 8,
        "specialties": ["Career Expert", "Numerology"],
        "reading_methods": ["Numerology", "Tarot Cards"],
        "topics": ["Career & Finance", "Money & Abundance", "Life Path"],
        "languages": ["English"],
        "chat_rate": 5.99,
        "phone_rate": 7.99,
        "video_call_rate": 9.99,
        "online_status": "online",
        "average_rating": 4.78,
        "total_reviews": 156,
        "total_readings": 489,
        "is_featured": True,
        "is_new": False,  # 489 > 300
        "is_first_hired": True,  # Founding Advisor #10
        "advisor_number": 10,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": False
    },
    {
        "id": "psy-013",
        "name": "Willow Dream",
        "email": "willow@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1502823403499-6ccfcf4fb453?w=400",
        "description": "Gentle soul specializing in grief counseling and spiritual healing.",
        "about_me": "I help people heal from loss and connect with those who have passed on.",
        "years_experience": 14,
        "specialties": ["Grief Counseling", "Mediumship"],
        "reading_methods": ["Mediumship", "Intuitive"],
        "topics": ["Spiritual Guidance", "Family & Friends", "Life Path"],
        "languages": ["English"],
        "chat_rate": 4.99,
        "phone_rate": 6.99,
        "video_call_rate": 8.49,
        "online_status": "online",
        "average_rating": 4.92,
        "total_reviews": 287,
        "total_readings": 120,  # Less than 300 but is founding advisor
        "is_featured": False,
        "is_new": False,  # Founding advisor - exempt from new classification
        "is_first_hired": True,  # Founding Advisor #11
        "advisor_number": 11,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": True,
        "offers_video_call": True,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    },
    {
        "id": "psy-014",
        "name": "Zephyr Wind",
        "email": "zephyr@psychic.com",
        "profile_picture": "https://images.unsplash.com/photo-1463453091185-61582044d556?w=400",
        "description": "Energy reader with expertise in chakra balancing and spiritual alignment.",
        "about_me": "I read the energy flow in your chakras to identify blockages and guide healing.",
        "years_experience": 6,
        "specialties": ["Chakra Healing", "Energy Healing"],
        "reading_methods": ["Intuitive", "No Tools"],
        "topics": ["Spiritual Guidance", "Life Path", "Health"],
        "languages": ["English", "Hindi"],
        "chat_rate": 3.49,
        "phone_rate": 5.49,
        "video_call_rate": 6.99,
        "online_status": "busy",
        "average_rating": 4.65,
        "total_reviews": 143,
        "total_readings": 85,  # Less than 300 - but founding advisor
        "is_featured": False,
        "is_new": False,  # Founding advisor - exempt
        "is_first_hired": True,  # Founding Advisor #12
        "advisor_number": 12,
        "offers_chat": True,
        "offers_phone": True,
        "offers_video": False,
        "offers_video_call": False,
        "offers_recorded_readings": True,
        "status": "approved",
        "free_chat_enabled": True
    }
]

# Pending psychic applications (awaiting admin approval)
PENDING_APPLICATIONS = [
    {
        "id": "psy-pending-001",
        "name": "Seraphina Cosmos",
        "email": "seraphina@email.com",
        "profile_picture": "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=400",
        "description": "Experienced astrologer with a passion for helping people understand their cosmic journey.",
        "about_me": "I have been practicing astrology for 8 years, specializing in natal charts and compatibility readings.",
        "years_experience": 8,
        "previous_platforms": "PsychicSource, Keen",
        "specialties": ["Astrology", "Numerology"],
        "reading_methods": ["Astrology", "Numerology", "Tarot Cards"],
        "topics": ["Love & Relationships", "Career & Finance", "Life Path"],
        "languages": ["English", "French"],
        "chat_rate": 3.99,
        "phone_rate": 5.99,
        "video_call_rate": 7.99,
        "status": "pending",
        "applied_at": "2026-02-20T10:30:00Z"
    },
    {
        "id": "psy-pending-002",
        "name": "Marcus Visions",
        "email": "marcus.visions@email.com",
        "profile_picture": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400",
        "description": "Clairvoyant medium connecting you with spirit guides and departed loved ones.",
        "about_me": "My gift of mediumship developed at age 12. I've helped hundreds of families find closure.",
        "years_experience": 15,
        "previous_platforms": "Spiritual Guidance Network",
        "specialties": ["Mediumship", "Clairvoyance"],
        "reading_methods": ["Mediumship", "Intuitive", "No Tools"],
        "topics": ["Spiritual Guidance", "Family & Friends", "Life Path"],
        "languages": ["English"],
        "chat_rate": 4.99,
        "phone_rate": 6.99,
        "video_call_rate": 8.99,
        "status": "pending",
        "applied_at": "2026-02-21T14:15:00Z"
    },
    {
        "id": "psy-pending-003",
        "name": "Elena Starlight",
        "email": "elena.starlight@email.com",
        "profile_picture": "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=400",
        "description": "Love and relationship specialist with tarot cards expertise.",
        "about_me": "I specialize in love readings and have helped couples reconnect for over 5 years.",
        "years_experience": 5,
        "previous_platforms": "Kasamba",
        "specialties": ["Love Specialist", "Tarot Cards"],
        "reading_methods": ["Tarot Cards", "Tarot Cards"],
        "topics": ["Love & Relationships", "Marriage & Family"],
        "languages": ["English", "Spanish"],
        "chat_rate": 2.99,
        "phone_rate": 4.99,
        "video_call_rate": 5.99,
        "status": "pending",
        "applied_at": "2026-02-22T09:45:00Z"
    }
]

async def load_deleted_psychic_ids(db: AsyncIOMotorDatabase):
    """Load deleted psychic IDs from MongoDB on startup"""
    global DELETED_PSYCHIC_IDS
    try:
        deleted_doc = await db.deleted_psychics.find_one({"_id": "deleted_ids"})
        if deleted_doc and deleted_doc.get("ids"):
            DELETED_PSYCHIC_IDS = set(deleted_doc["ids"])
            print(f"✅ Loaded {len(DELETED_PSYCHIC_IDS)} deleted psychic IDs from database")
    except Exception as e:
        print(f"Error loading deleted psychic IDs: {e}")

def mask_pii_fields(p: dict) -> dict:
    """BE-009: Mask sensitive PII data (SSN, Tax ID, Bank Account Number) in response payloads"""
    if not isinstance(p, dict):
        return p
    clean_p = p.copy()
    if "ssn" in clean_p and clean_p["ssn"]:
        val = str(clean_p["ssn"])
        clean_p["ssn"] = f"***-**-{val[-4:]}" if len(val) >= 4 else "***-**-****"
    if "tax_id" in clean_p and clean_p["tax_id"]:
        val = str(clean_p["tax_id"])
        clean_p["tax_id"] = f"XX-XXX{val[-4:]}" if len(val) >= 4 else "XX-XXXXXXX"
    if "bank_account_number" in clean_p and clean_p["bank_account_number"]:
        val = str(clean_p["bank_account_number"])
        clean_p["bank_account_number"] = f"*****{val[-4:]}" if len(val) >= 4 else "**********"
    return clean_p

def get_active_psychics():
    """Get mock psychics excluding deleted ones, ensuring all have standard/emergency reading fields"""
    psychics = [mask_pii_fields(p) for p in MOCK_PSYCHICS if p["id"] not in DELETED_PSYCHIC_IDS]
    for p in psychics:
        if "offers_standard_readings" not in p:
            p["offers_standard_readings"] = True
        if "offers_emergency_readings" not in p:
            p["offers_emergency_readings"] = True
    return psychics

def create_psychic_routes(db: AsyncIOMotorDatabase):
    @router.get("/psychic-of-the-week")
    async def get_psychic_of_the_week():
        """Get the top performing psychic of the week with auto-generated praise"""
        import datetime
        import hashlib
        
        # Get approved psychics only (excluding deleted)
        psychics = [p for p in get_active_psychics() if p.get("status") == "approved"]
        
        # Calculate a score based on ratings + reviews + readings
        def calculate_weekly_score(psychic):
            rating = psychic.get("average_rating", 0)
            reviews = psychic.get("total_reviews", 0)
            readings = psychic.get("total_readings", 0)
            return (rating * 25) + (reviews / 5) + (readings / 50)
        
        # Sort by score
        ranked_psychics = sorted(psychics, key=calculate_weekly_score, reverse=True)
        
        # Use current week number to rotate the winner
        current_week = datetime.datetime.now().isocalendar()[1]
        current_year = datetime.datetime.now().year
        
        # Create a seed based on year and week
        seed = f"{current_year}-{current_week}"
        seed_hash = int(hashlib.md5(seed.encode()).hexdigest(), 16)
        
        # Select from top 5 performers
        top_performers = ranked_psychics[:5]
        if len(top_performers) > 0:
            selected_index = seed_hash % len(top_performers)
            winner = top_performers[selected_index]
        else:
            winner = ranked_psychics[0] if ranked_psychics else None
        
        if not winner:
            return {"show_section": False, "reason": "Not enough data yet"}
        
        # Check if we have enough data to show
        total_system_readings = sum(p.get("total_readings", 0) for p in psychics)
        if total_system_readings < 100:
            return {"show_section": False, "reason": "System is still learning"}
        
        # Simple praise messages about their experience and skills
        years_exp = winner.get("years_experience", 5)
        topics = winner.get("topics", ["spiritual guidance"])
        main_topic = topics[0] if topics else "spiritual guidance"
        
        praise_templates = [
            f"With {years_exp} years of experience in {main_topic}, {winner['name']} has been helping clients find clarity and peace.",
            f"{winner['name']} brings {years_exp} years of wisdom in {main_topic}. Clients praise their compassionate and accurate readings.",
            f"A gifted advisor with {years_exp} years specializing in {main_topic}. {winner['name']} truly connects with every client.",
            f"Known for insightful {main_topic} readings, {winner['name']} has dedicated {years_exp} years to spiritual guidance.",
            f"{winner['name']} combines {years_exp} years of experience with a genuine gift for {main_topic}.",
        ]
        
        praise_index = seed_hash % len(praise_templates)
        praise = praise_templates[praise_index]
        
        # TODO: Email notification would be sent here
        # In production, this would trigger an email to the psychic:
        # send_email(
        #     to=winner.get("email"),
        #     subject="Congratulations! You're our Psychic of the Week!",
        #     body=f"Dear {winner['name']}, we're thrilled to announce that you've been selected as our Psychic of the Week..."
        # )
        
        return {
            "show_section": True,
            "psychic": PsychicListResponse(**winner),
            "praise": praise,
            "week_number": current_week,
            "year": current_year
        }
    
    @router.get("/suggested/{user_id}")
    async def get_suggested_psychics(user_id: str):
        """Get psychics suggested based on user's reading history and interests"""
        # Start with approved psychics only (excluding deleted)
        psychics = [p for p in get_active_psychics() if p.get("status") == "approved"]
        
        # Try to get user's reading history to determine their interests
        user_topics = []
        try:
            questions = await db.questions.find({"client_id": user_id}).to_list(100)
            if questions:
                # Extract topics from previous readings
                for q in questions:
                    if q.get("question_type"):
                        # Map question types to topics
                        q_type = q.get("question_type", "").lower()
                        q_text = q.get("question_text", "").lower()
                        
                        # Analyze question text for topics
                        if any(word in q_text for word in ["love", "relationship", "partner", "boyfriend", "girlfriend", "husband", "wife", "dating"]):
                            user_topics.append("Love & Relationships")
                        if any(word in q_text for word in ["career", "job", "work", "business", "promotion"]):
                            user_topics.append("Career & Finance")
                        if any(word in q_text for word in ["money", "finance", "wealth", "investment"]):
                            user_topics.append("Money & Abundance")
                        if any(word in q_text for word in ["dream", "dreaming", "nightmare"]):
                            user_topics.append("Dream Analysis")
                        if any(word in q_text for word in ["marriage", "wedding", "family", "children"]):
                            user_topics.append("Marriage & Family")
                        if any(word in q_text for word in ["spiritual", "soul", "energy", "meditation"]):
                            user_topics.append("Spiritual Guidance")
                        if any(word in q_text for word in ["life", "purpose", "path", "destiny"]):
                            user_topics.append("Life Path")
        except Exception as e:
            print(f"Error fetching user history: {e}")
        
        # Remove duplicates and get unique topics
        user_topics = list(set(user_topics))
        
        if user_topics:
            # User has interests - find psychics matching those topics
            def calculate_match_score(psychic):
                score = 0
                psychic_topics = psychic.get("topics", [])
                for topic in user_topics:
                    if topic in psychic_topics:
                        score += 10
                # Also factor in rating
                score += psychic.get("average_rating", 0) * 2
                return score
            
            # Sort by match score
            psychics = sorted(psychics, key=calculate_match_score, reverse=True)[:10]
        else:
            # New user or no history - show popular psychics across common categories
            # Prioritize: Love, Career, Spiritual (most popular topics)
            popular_topics = ["Love & Relationships", "Career & Finance", "Spiritual Guidance"]
            
            def has_popular_topic(psychic):
                return any(topic in psychic.get("topics", []) for topic in popular_topics)
            
            # Filter to popular topics and sort by rating
            psychics = [p for p in psychics if has_popular_topic(p)]
            psychics = sorted(psychics, key=lambda x: x.get("average_rating", 0), reverse=True)[:10]
            
            # If not enough, add more from all psychics
            if len(psychics) < 5:
                all_sorted = sorted(get_active_psychics(), key=lambda x: x.get("average_rating", 0), reverse=True)
                for p in all_sorted:
                    if p not in psychics and p.get("status") == "approved":
                        psychics.append(p)
                    if len(psychics) >= 10:
                        break
        
        return {
            "psychics": [PsychicListResponse(**p) for p in psychics],
            "user_interests": user_topics if user_topics else ["Love & Relationships", "Career & Finance", "Spiritual Guidance"],
            "is_personalized": len(user_topics) > 0
        }
    
    @router.get("/", response_model=List[PsychicListResponse])
    async def get_psychics(
        category: Optional[str] = Query(None, description="Filter by category: new, highly_rated, recommended, top_rated"),
        topic: Optional[str] = Query(None, description="Filter by topic"),
        min_rating: Optional[float] = Query(None, description="Minimum rating"),
        max_price: Optional[float] = Query(None, description="Maximum price per minute"),
        min_price: Optional[float] = Query(None, description="Minimum price per minute"),
        online_only: Optional[bool] = Query(False, description="Only show online psychics"),
        sort_by: Optional[str] = Query("rating", description="Sort by: rating, price_low, price_high, reviews, newest"),
        search: Optional[str] = Query(None, description="Search by name")
    ):
        # Start with mock data - ONLY show approved psychics to regular users (excluding deleted)
        psychics = [p for p in get_active_psychics() if p.get("status") == "approved"]
        
        # Also fetch real psychics from database (approved applications)
        try:
            db_psychics = await db.psychics.find({}).to_list(100)
            for dbp in db_psychics:
                # Convert DB psychic to API format
                # Get service availability - these are toggled by the psychic in their Advisor app
                is_online = dbp.get("online_status", "offline") == "online"
                offers_chat = dbp.get("offers_chat", True)
                offers_phone = dbp.get("offers_phone", True)
                offers_video = dbp.get("offers_video", False)
                offers_recorded = dbp.get("offers_recorded_readings", True)
                offers_standard = dbp.get("offers_standard_readings", True)
                offers_emergency = dbp.get("offers_emergency_readings", False)
                
                real_psychic = {
                    "id": dbp.get("id", str(dbp.get("_id", ""))),
                    "name": dbp.get("name", ""),
                    "email": dbp.get("email", ""),
                    "profile_picture": dbp.get("profile_picture", "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400"),
                    "specialties": dbp.get("specialties", []),
                    "topics": dbp.get("specialties", []),  # Use specialties as topics
                    "reading_methods": dbp.get("tools_used", ["Chat", "Video"]),
                    "bio": dbp.get("bio", ""),
                    "description": dbp.get("bio", ""),  # Required field
                    "years_experience": dbp.get("years_experience", "1-3 years"),
                    "price_per_minute": dbp.get("chat_rate", 2.99),
                    "chat_rate": dbp.get("chat_rate", 2.99),
                    "phone_rate": dbp.get("phone_rate", 3.99),
                    "video_rate": dbp.get("video_rate", 4.99),
                    "average_rating": dbp.get("average_rating", 5.0),
                    "total_reviews": dbp.get("total_readings", 0),
                    "total_readings": dbp.get("total_readings", 0),  # Required field
                    "is_online": is_online,
                    "online_status": dbp.get("online_status", "offline"),
                    "is_verified": dbp.get("is_verified", True),
                    "is_featured": False,
                    "is_new": True,
                    "status": "approved",
                    "country": dbp.get("country", ""),
                    "intro_video_url": dbp.get("intro_video_url"),
                    # Service offerings - used to show/hide service icons
                    "offers_chat": offers_chat,
                    "offers_phone": offers_phone,
                    "offers_video": offers_video,
                    "offers_video_call": offers_video,
                    "offers_recorded_readings": offers_recorded,
                    "offers_standard_readings": offers_standard,
                    "offers_emergency_readings": offers_emergency,
                    "recorded_video_price": 12.0,
                }
                # Add to list if not duplicate
                if not any(p.get("email") == real_psychic["email"] for p in psychics if p.get("email")):
                    psychics.append(real_psychic)
        except Exception as e:
            print(f"Error fetching DB psychics: {e}")
        
        # Apply category filter
        if category == "new":
            psychics = [p for p in psychics if p.get("is_new", False)]
        elif category == "highly_rated":
            # Sort by average rating - highest rated psychics
            psychics = sorted(psychics, key=lambda x: x.get("average_rating", 0), reverse=True)
            psychics = [p for p in psychics if p.get("average_rating", 0) >= 4.5]
        elif category == "recommended":
            psychics = [p for p in psychics if p.get("is_featured", False)]
        elif category == "top_rated":
            # Top rated = sorted by average rating (highest first)
            psychics = sorted(psychics, key=lambda x: x.get("average_rating", 0), reverse=True)[:10]
        elif category == "trending":
            # Trending = combination of high rating + recent activity (total_reviews)
            # Score = rating * 20 + (reviews / 10) to balance both factors
            psychics = sorted(psychics, key=lambda x: (x.get("average_rating", 0) * 20) + (x.get("total_reviews", 0) / 10), reverse=True)[:10]
        elif category == "free_chat":
            # Psychics who offer free 5-min chat - shown at top for eligible clients
            psychics = [p for p in psychics if p.get("free_chat_enabled", False)]
            # Sort by rating to show best ones first
            psychics = sorted(psychics, key=lambda x: x.get("average_rating", 0), reverse=True)
        
        # Apply topic filter
        if topic:
            psychics = [p for p in psychics if topic in p.get("topics", [])]
        
        # Apply rating filter
        if min_rating:
            psychics = [p for p in psychics if p.get("average_rating", 0) >= min_rating]
        
        # Apply price filter
        if max_price:
            psychics = [p for p in psychics if p.get("chat_rate", 0) <= max_price]
        if min_price:
            psychics = [p for p in psychics if p.get("chat_rate", 0) >= min_price]
        
        # Apply online filter
        if online_only:
            psychics = [p for p in psychics if p.get("online_status") == "online"]
        
        # Apply search
        if search:
            search_lower = search.lower()
            psychics = [p for p in psychics if search_lower in p.get("name", "").lower()]
        
        # Apply sorting
        if sort_by == "rating":
            psychics = sorted(psychics, key=lambda x: x.get("average_rating", 0), reverse=True)
        elif sort_by == "price_low":
            psychics = sorted(psychics, key=lambda x: x.get("chat_rate", 0))
        elif sort_by == "price_high":
            psychics = sorted(psychics, key=lambda x: x.get("chat_rate", 0), reverse=True)
        elif sort_by == "reviews":
            psychics = sorted(psychics, key=lambda x: x.get("total_reviews", 0), reverse=True)
        elif sort_by == "newest":
            psychics = [p for p in psychics if p.get("is_new")] + [p for p in psychics if not p.get("is_new")]
        
        return [PsychicListResponse(**p) for p in psychics]
    
    @router.get("/{psychic_id}", response_model=PsychicListResponse)
    async def get_psychic(psychic_id: str):
        # Check if deleted
        if psychic_id in DELETED_PSYCHIC_IDS:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        # First check mock psychics
        for p in MOCK_PSYCHICS:
            if p["id"] == psychic_id:
                # Auto-update tags based on ratings
                update_psychic_tags(p)
                return PsychicListResponse(**p)
        
        # Then check database for real psychics (BE-018: Safe ObjectId lookup)
        try:
            db_psychic = await db.psychics.find_one({"id": psychic_id})
            if not db_psychic:
                try:
                    from bson.objectid import ObjectId
                    if len(psychic_id) == 24:
                        db_psychic = await db.psychics.find_one({"_id": ObjectId(psychic_id)})
                except Exception:
                    db_psychic = None
            if not db_psychic:
                db_psychic = await db.psychics.find_one({"_id": psychic_id})
            if not db_psychic:
                db_psychic = await db.psychics.find_one({"email": "advisor@psychic.com"}) if "luna" in psychic_id.lower() or "test" in psychic_id.lower() else None
            
            if db_psychic:
                # Get actual service availability from database
                is_online = db_psychic.get("online_status", "offline") == "online"
                offers_chat = db_psychic.get("offers_chat", True)
                offers_phone = db_psychic.get("offers_phone", True)
                offers_video = db_psychic.get("offers_video", False)
                offers_recorded = db_psychic.get("offers_recorded_readings", True)
                offers_standard = db_psychic.get("offers_standard_readings", True)
                offers_emergency = db_psychic.get("offers_emergency_readings", False)
                
                real_psychic = {
                    "id": db_psychic.get("id", str(db_psychic.get("_id", ""))),
                    "name": db_psychic.get("name", ""),
                    "email": db_psychic.get("email", ""),
                    "profile_picture": db_psychic.get("profile_picture", "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=400"),
                    "specialties": db_psychic.get("specialties", []),
                    "topics": db_psychic.get("specialties", []),
                    "reading_methods": db_psychic.get("tools_used", ["Tarot Cards", "Intuitive"]),
                    "bio": db_psychic.get("bio", ""),
                    "description": db_psychic.get("bio", "Experienced psychic advisor."),
                    "years_experience": db_psychic.get("years_experience", "5-10 years"),
                    "price_per_minute": db_psychic.get("chat_rate", 2.99),
                    "chat_rate": db_psychic.get("chat_rate", 2.99),
                    "phone_rate": db_psychic.get("phone_rate", 3.99),
                    "video_call_rate": db_psychic.get("video_rate", 4.99),
                    "average_rating": db_psychic.get("average_rating", 4.9),
                    "total_reviews": db_psychic.get("total_readings", 0),
                    "total_readings": db_psychic.get("total_readings", 0),
                    "is_online": is_online,
                    "online_status": db_psychic.get("online_status", "online"),
                    "is_verified": db_psychic.get("is_verified", True),
                    "is_featured": False,
                    "is_new": True,
                    "status": "approved",
                    # Service availability - reflects what psychic has toggled ON
                    "offers_chat": offers_chat,
                    "offers_phone": offers_phone,
                    "offers_video": offers_video,
                    "offers_video_call": offers_video,
                    "offers_recorded_readings": offers_recorded,
                    "offers_standard_readings": offers_standard,
                    "offers_emergency_readings": offers_emergency,
                }
                return PsychicListResponse(**real_psychic)
        except Exception as e:
            print(f"Error fetching psychic from DB: {e}")
        
        raise HTTPException(status_code=404, detail="Psychic not found")
    
    @router.post("/{psychic_id}/status")
    async def update_psychic_status(psychic_id: str, status_data: dict):
        """Update psychic online status and notify followers if coming online (BE-073)"""
        new_status = status_data.get("online_status", "online")
        is_online = new_status == "online"

        await db.psychics.update_one(
            {"id": psychic_id},
            {"$set": {"online_status": new_status, "is_online": is_online, "last_seen": datetime.utcnow().isoformat()}}
        )

        if is_online:
            # Trigger push notification to users who favorited this advisor (BE-073)
            try:
                favorites = await db.favorites.find({"psychic_id": psychic_id}).to_list(500)
                psychic = await db.psychics.find_one({"id": psychic_id})
                psychic_name = psychic.get("name", "Your favorite advisor") if psychic else "Your favorite advisor"

                for fav in favorites:
                    u_id = fav.get("user_id")
                    notif = {
                        "id": str(uuid.uuid4()),
                        "user_id": u_id,
                        "title": f"✨ {psychic_name} is Now Online!",
                        "body": f"{psychic_name} is available for chat and video readings. Connect now!",
                        "type": "favorite_advisor_online",
                        "psychic_id": psychic_id,
                        "is_read": False,
                        "created_at": datetime.utcnow()
                    }
                    await db.notifications.insert_one(notif)
            except Exception as e:
                print(f"Error triggering favorite advisor online pushes: {e}")

        return {"success": True, "online_status": new_status}

    @router.delete("/{psychic_id}")
    async def delete_psychic(psychic_id: str):
        """Delete a psychic (admin only) - persists deletion to MongoDB"""
        global DELETED_PSYCHIC_IDS
        
        # Check if psychic exists
        psychic_name = None
        for p in MOCK_PSYCHICS:
            if p["id"] == psychic_id:
                psychic_name = p["name"]
                break
        
        if not psychic_name and psychic_id not in DELETED_PSYCHIC_IDS:
            # Also try to find in DB
            try:
                db_psychic = await db.psychics.find_one({"id": psychic_id})
                if db_psychic:
                    psychic_name = db_psychic.get("name", psychic_id)
            except:
                pass
        
        if not psychic_name:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        # Add to deleted set
        DELETED_PSYCHIC_IDS.add(psychic_id)
        
        # Persist to MongoDB
        try:
            await db.deleted_psychics.update_one(
                {"_id": "deleted_ids"},
                {"$addToSet": {"ids": psychic_id}},
                upsert=True
            )
            print(f"✅ Persisted deletion of psychic {psychic_id} to database")
        except Exception as e:
            print(f"Error persisting deletion: {e}")
        
        # Soft-delete from actual psychics collection to preserve reading logs (BE-028)
        try:
            await db.psychics.update_one(
                {"id": psychic_id},
                {"$set": {"is_deleted": True, "deleted_at": datetime.utcnow().isoformat()}}
            )
        except Exception as e:
            print(f"Error soft-deleting from psychics collection: {e}")
        
        return {"success": True, "message": f"Psychic {psychic_name} deleted successfully"}
    
    @router.post("/admin/restore-all")
    async def restore_all_psychics():
        """Admin endpoint to restore all deleted psychics - clears the deleted_psychics collection"""
        global DELETED_PSYCHIC_IDS
        try:
            # Clear the in-memory set
            DELETED_PSYCHIC_IDS = set()
            
            # Clear the MongoDB collection
            result = await db.deleted_psychics.delete_many({})
            
            return {
                "success": True, 
                "message": f"All psychics restored. Cleared {result.deleted_count} documents from deleted_psychics collection.",
                "total_psychics_available": len(MOCK_PSYCHICS)
            }
        except Exception as e:
            print(f"Error restoring psychics: {e}")
            return {"success": False, "message": str(e)}
    
    @router.get("/admin/status")
    async def get_psychics_status():
        """Admin endpoint to check psychics status"""
        try:
            deleted_doc = await db.deleted_psychics.find_one({"_id": "deleted_ids"})
            deleted_ids = deleted_doc.get("ids", []) if deleted_doc else []
        except:
            deleted_ids = []
        
        return {
            "total_mock_psychics": len(MOCK_PSYCHICS),
            "deleted_psychic_ids": list(DELETED_PSYCHIC_IDS),
            "deleted_in_db": deleted_ids,
            "active_psychics": len([p for p in MOCK_PSYCHICS if p["id"] not in DELETED_PSYCHIC_IDS])
        }
    
    def update_psychic_tags(psychic: dict):
        """Automatically update tags based on current ratings"""
        rating = psychic.get("average_rating", 0)
        total_reviews = psychic.get("total_reviews", 0)
        
        # Top Rated tag logic
        if rating >= 4.8 and total_reviews >= 50:
            psychic["is_top_rated"] = True
        elif rating < 4.5:
            # Remove tag if rating drops below 4.5
            psychic["is_top_rated"] = False
        
        # Featured status - can be revoked for low ratings
        if psychic.get("is_featured", False) and rating < 4.0:
            psychic["is_featured"] = False
        
        # Trending logic based on reviews and rating
        if rating >= 4.5 and total_reviews >= 100:
            psychic["is_trending"] = True
        elif total_reviews < 50 or rating < 4.3:
            psychic["is_trending"] = False
        
        # Rising Star for newer advisors with good ratings
        if not psychic.get("is_first_hired", False) and total_reviews < 100 and rating >= 4.7:
            psychic["is_rising_star"] = True
        elif total_reviews >= 100 or rating < 4.5:
            psychic["is_rising_star"] = False
    
    @router.get("/topics/list")
    async def get_topics():
        return {
            "topics": [
                {"id": "love", "name": "Love & Relationships", "icon": "heart"},
                {"id": "career", "name": "Career & Finance", "icon": "briefcase"},
                {"id": "marriage", "name": "Marriage & Family", "icon": "people"},
                {"id": "life_path", "name": "Life Path & Purpose", "icon": "compass"},
                {"id": "spiritual", "name": "Spiritual Guidance", "icon": "sparkles"},
                {"id": "dreams", "name": "Dream Analysis", "icon": "moon"},
                {"id": "past_life", "name": "Personal Growth Readings", "icon": "time"},
                {"id": "pets", "name": "Pet Readings", "icon": "paw"},
                {"id": "money", "name": "Money & Abundance", "icon": "cash"},
                {"id": "general", "name": "General Reading", "icon": "crystal-ball"}
            ]
        }
    
    return router


def create_user_routes(db: AsyncIOMotorDatabase):
    """Create user-related routes including recently visited"""
    user_router = APIRouter(prefix="/users", tags=["users"])
    
    @user_router.get("/{user_id}/recently-visited")
    async def get_recently_visited(user_id: str):
        """Get recently visited psychics for a user"""
        try:
            # Fetch user's recently visited psychics from DB
            user = await db.users.find_one({"id": user_id})
            if not user:
                return {"psychics": []}
            
            visited_ids = user.get("recently_visited_psychics", [])
            if not visited_ids:
                return {"psychics": []}
            
            # Get psychic details for visited IDs
            psychics = []
            for pid in visited_ids[:10]:  # Max 10 recent
                for p in MOCK_PSYCHICS:
                    if p["id"] == pid:
                        psychics.append(PsychicListResponse(**p))
                        break
            
            return {"psychics": psychics}
        except Exception as e:
            print(f"Error fetching recently visited: {e}")
            return {"psychics": []}
    
    @user_router.post("/{user_id}/recently-visited/{psychic_id}")
    async def add_recently_visited(user_id: str, psychic_id: str):
        """Add a psychic to user's recently visited list"""
        try:
            # Get current list
            user = await db.users.find_one({"id": user_id})
            if not user:
                return {"success": False}
            
            visited = user.get("recently_visited_psychics", [])
            
            # Remove if already exists (to move to front)
            if psychic_id in visited:
                visited.remove(psychic_id)
            
            # Add to front
            visited.insert(0, psychic_id)
            
            # Keep only last 20
            visited = visited[:20]
            
            # Update in DB
            await db.users.update_one(
                {"id": user_id},
                {"$set": {"recently_visited_psychics": visited}}
            )
            
            return {"success": True}
        except Exception as e:
            print(f"Error adding recently visited: {e}")
            return {"success": False}
    
    return user_router
