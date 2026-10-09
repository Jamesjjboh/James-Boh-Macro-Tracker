"""
Telegram Macros & Calories Logging Bot
Multi-tenant Cloud Nutrition Platform powered by Google Cloud Firestore & Gemini 3.6 Flash Vision.
Features:
- Google Cloud Firestore (multi-tenant privacy & data siloing)
- Smart meal editing & undo (/edit, /undo, natural language corrections)
- Nutrition analytics & trends (/analytics, /weekly, /monthly with dark-mode charts)
- Data governance (/privacy, /export CSV, /delete Right to Erasure)
- Dual-write to Google Sheets (optional backward compatibility)
"""

import asyncio
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
import html
import io
import json
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account
from google.oauth2.service_account import Credentials
import gspread
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
import pytz
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "James Boh Macro Tracker").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
TIMEZONE_STR = os.getenv("TIMEZONE", "Asia/Singapore").strip()

# Cloud Run Webhook Configuration
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
PORT = int(os.getenv("PORT", "8080"))
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

# Admin Access Control (only admins can use /broadcast and /release)
ADMIN_USERS_RAW = os.getenv("ADMIN_USERS", "jamesjjboh").strip()
ADMIN_USERS = (
    {u.strip().lstrip("@").lower() for u in ADMIN_USERS_RAW.split(",") if u.strip()}
    if ADMIN_USERS_RAW
    else {"jamesjjboh"}
)

# General User Access Control (optional whitelist; if empty, public access is enabled)
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "").strip()
ALLOWED_USERS = (
    {u.strip().lstrip("@").lower() for u in ALLOWED_USERS_RAW.split(",") if u.strip()}
    if ALLOWED_USERS_RAW
    else None
)

# Timezone setup
try:
    LOCAL_TZ = pytz.timezone(TIMEZONE_STR)
except Exception:
    logger.warning(f"Invalid timezone '{TIMEZONE_STR}', falling back to UTC.")
    LOCAL_TZ = pytz.UTC

# Standard Data Headers (11 columns including Dietary Fiber)
SHEET_HEADERS = [
    "Date & Time",
    "User Name",
    "Item Name",
    "Category",
    "Calories (kcal)",
    "Protein (g)",
    "Carbohydrates (g)",
    "Fat (g)",
    "Fiber (g)",
    "Short Description",
    "Nutrition Score (/100)",
]


# =====================================================================
# Pydantic Schemas for Structured Gemini Output
# =====================================================================
class FoodItem(BaseModel):
    item_name: str = Field(
        description="Standardized name of the food or beverage item."
    )
    category: str = Field(
        description=(
            "Meal category: Breakfast, Lunch, Dinner, or Snack. "
            "If an item (including a beverage like tea or soy milk) is consumed as part of a meal, "
            "assign that meal's category (e.g. Lunch). Standalone drinks between meals are Snacks."
        )
    )
    calories: float = Field(
        description="Estimated energy in kilocalories (kcal)."
    )
    protein: float = Field(
        description="Estimated dietary protein in grams (g)."
    )
    carbohydrates: float = Field(
        description="Estimated dietary carbohydrates in grams (g)."
    )
    fat: float = Field(
        description="Estimated dietary fat in grams (g)."
    )
    fiber: float = Field(
        default=0.0,
        description="Estimated dietary fiber in grams (g)."
    )
    short_description: str = Field(
        description="Brief breakdown of key ingredients, estimated portion size, fiber sources, and preparation method."
    )
    nutrition_score: int = Field(
        ge=0,
        le=100,
        description=(
            "Nutrition & Fat Loss Score out of 100. Base the score on: high protein-to-calorie ratio, "
            "high dietary fiber content, satiety/fullness index (fiber/volume/lean protein), "
            "whole food quality, minimal added sugars/refined fats, and suitability for fat cutting."
        ),
    )


class MealAnalysisResponse(BaseModel):
    items: List[FoodItem] = Field(
        default_factory=list,
        description=(
            "List of individual food and beverage items identified. If composite meal, break down "
            "into separate individual items (e.g. Steak + Salad + Soy Milk -> 3 separate items). "
            "Leave empty if no food or beverage is identified."
        ),
    )
    motivational_note: str = Field(
        description="A warm, encouraging, concise 1-2 sentence motivational remark tailored for fat loss and healthy habits."
    )


# System instructions for Gemini
GEMINI_SYSTEM_PROMPT = """You are an elite sports dietitian and calorie/macro tracking specialist focusing on fat loss and healthy habits.

Your task:
1. Analyze the provided meal description or photo.
2. Break down composite meals into individual distinct items in the `items` array. For example, "Steak with salad and soy milk" must be broken down into 3 separate items: 1) Steak, 2) Salad, 3) Soy Milk.
3. For each item:
   - Provide realistic estimates for Calories (kcal), Protein (g), Carbohydrates (g), Fat (g), and Dietary Fiber (g).
   - Assign Category: Breakfast, Lunch, Dinner, or Snack. If an item (such as a beverage, side soup, or dessert) is consumed as part of a meal (e.g. lunch), classify it under that meal's category (e.g. "Lunch") rather than "Beverage".
   - Detail portion estimation, ingredients, and fiber sources in `short_description`.
   - Calculate the "Nutrition & Fat Loss Score" from 0 to 100 strictly following these principles:
     * High protein-to-calorie density yields higher scores.
     * High dietary fiber (from legumes, vegetables, whole grains) significantly boosts the score (higher satiety, slower glucose spike, gut health).
     * Whole, minimally processed foods score higher than ultra-processed items.
     * High added sugar, deep frying, or empty calories result in significant score penalties.
     * Score guidelines:
       - 85-100: Top-tier cut foods (e.g., grilled lean chicken/beef/fish, egg whites, unsweetened high-protein soy milk, cruciferous veggies, Greek yogurt).
       - 65-84: Solid, balanced meal components (e.g., salads with light dressing, sweet corn, brown rice, whole eggs).
       - 45-64: Calorie-dense or higher-fat items needing moderation (e.g., creamy dressings, croutons, fatty cuts of meat).
       - 0-44: Low satiety, high sugar/fat items not ideal for cutting (e.g., sugary drinks, pastries, deep-fried fast foods).
4. CRITICAL USER NOTE & PHOTO CAPTION RULES (GROUND TRUTH):
   - The user may include a caption or note with photos (e.g. 'Lunch: steak with salad and NutriSoy soy milk no sugar', 'ate half', 'no dressing').
   - Whenever a caption or user note is provided, it is the ABSOLUTE GROUND TRUTH.
   - If the user specifies an item name or brand (e.g., 'NutriSoy soy milk no sugar', 'oat milk', 'sugar-free iced tea'), you MUST use that exact food item and its corresponding nutritional profile instead of making visual guesses (e.g. do not guess cow milk if the user specifies soy milk).
   - If the user specifies the meal name (e.g. 'Lunch'), set the Category for all items in that meal to that meal name.
5. Provide a warm, personalized 1-2 sentence motivational note in `motivational_note` acknowledging good choices, highlighting protein and fiber intake, giving actionable tips for fat loss, or cheering the user on.
6. If the input contains NO edible food or beverage (e.g., random objects, scenery, text unrelated to food), return an empty `items` array and explain in `motivational_note` that no food was detected.
"""


# =====================================================================
# Multi-Photo Album (Media Group) Collector State
# =====================================================================
class MediaGroupBuffer:
    """Buffers multiple photos sent concurrently in a single Telegram album."""
    def __init__(self, media_group_id: str, initial_update: Update):
        self.media_group_id = media_group_id
        self.initial_update = initial_update
        self.caption: str = ""
        self.images: List[bytes] = []
        self.lock = asyncio.Lock()
        self.last_received_time = time.monotonic()


_MEDIA_GROUP_BUFFERS: Dict[str, MediaGroupBuffer] = {}
_MEDIA_GROUP_GLOBAL_LOCK = asyncio.Lock()


# =====================================================================
# Google Cloud Firestore Integration (Primary Multi-Tenant Database)
# =====================================================================
class FirestoreService:
    def __init__(
        self,
        project_id: str = "james-boh-macro-tracker",
        credentials_path: str = "service_account.json",
        credentials_json: Optional[str] = None,
        database: str = "(default)",
    ):
        self.project_id = project_id
        self.credentials_path = credentials_path
        self.credentials_json = credentials_json
        self.database = database
        self._client: Optional[firestore.AsyncClient] = None
        self._lock = asyncio.Lock()

    def _get_credentials(self) -> Optional[service_account.Credentials]:
        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/datastore",
        ]
        if self.credentials_json:
            info = json.loads(self.credentials_json)
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)
        elif os.path.exists(self.credentials_path):
            return service_account.Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
        # On Cloud Run, returns None to seamlessly use Application Default Credentials (ADC)
        return None

    async def get_client(self) -> firestore.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or getattr(self, "_client_loop", None) is not loop:
            creds = self._get_credentials()
            self._client = firestore.AsyncClient(
                project=self.project_id,
                credentials=creds,
                database=self.database,
            )
            self._client_loop = loop
        return self._client

    async def register_or_update_user(
        self,
        chat_id: int,
        user_name: str,
        first_name: str = "",
    ) -> None:
        """Upserts user profile in users/{chat_id}."""
        try:
            db = await self.get_client()
            user_ref = db.collection("users").document(str(chat_id))
            doc = await user_ref.get()
            now_iso = datetime.now(LOCAL_TZ).isoformat()
            if doc.exists:
                await user_ref.update({
                    "user_name": user_name,
                    "first_name": first_name,
                    "last_active_at": now_iso,
                })
            else:
                await user_ref.set({
                    "chat_id": chat_id,
                    "user_name": user_name,
                    "first_name": first_name,
                    "created_at": now_iso,
                    "last_active_at": now_iso,
                    "daily_calorie_target": 2000,
                    "daily_protein_target": 150,
                    "daily_fiber_target": 25,
                    "timezone": TIMEZONE_STR,
                })
                logger.info(f"Registered new Firestore user: {user_name} ({chat_id})")
        except Exception as e:
            logger.warning(f"Could not register user in Firestore: {e}")

    async def get_user_profile(self, chat_id: int) -> dict:
        """Fetches user profile dict including targets."""
        try:
            db = await self.get_client()
            doc = await db.collection("users").document(str(chat_id)).get()
            if doc.exists:
                return doc.to_dict()
        except Exception as e:
            logger.error(f"Error fetching user profile {chat_id}: {e}")
        return {
            "chat_id": chat_id,
            "daily_calorie_target": 2000,
            "daily_protein_target": 150,
            "daily_fiber_target": 25,
            "timezone": TIMEZONE_STR,
            "targets_set": False,
        }

    async def update_user_targets(
        self,
        chat_id: int,
        calorie_target: Optional[float] = None,
        protein_target: Optional[float] = None,
        fiber_target: Optional[float] = None,
    ) -> dict:
        """Updates user daily calorie, protein, and fiber targets and flags them as custom set."""
        db = await self.get_client()
        user_ref = db.collection("users").document(str(chat_id))
        doc = await user_ref.get()
        current = doc.to_dict() if doc.exists else {}

        updates = {"targets_set": True}
        if calorie_target is not None:
            updates["daily_calorie_target"] = round(float(calorie_target), 0)
        if protein_target is not None:
            updates["daily_protein_target"] = round(float(protein_target), 1)
        if fiber_target is not None:
            updates["daily_fiber_target"] = round(float(fiber_target), 1)

        if doc.exists:
            await user_ref.update(updates)
        else:
            updates.update({
                "chat_id": chat_id,
                "created_at": datetime.now(LOCAL_TZ).isoformat(),
                "timezone": TIMEZONE_STR,
            })
            await user_ref.set(updates)

        current.update(updates)
        return current

    async def save_meal(self, chat_id: int, user_name: str, meal_data: dict) -> str:
        """Saves a meal document under users/{chat_id}/meals."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        new_meal_doc = meals_ref.document()
        meal_data["id"] = new_meal_doc.id
        meal_data["user_name"] = user_name
        await new_meal_doc.set(meal_data)
        asyncio.create_task(self.register_or_update_user(chat_id, user_name))
        return new_meal_doc.id

    async def get_last_meal(self, chat_id: int) -> Optional[dict]:
        """Retrieves the most recent meal logged by the user."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        query = meals_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1)
        docs = [doc async for doc in query.stream()]
        if docs:
            data = docs[0].to_dict()
            data["id"] = docs[0].id
            return data
        return None

    async def find_meal_by_message_id(self, chat_id: int, message_id: int) -> Optional[dict]:
        """Finds a meal document by either its bot confirmation message_id or original user message_id."""
        try:
            db = await self.get_client()
            meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
            q1 = meals_ref.where(filter=FieldFilter("bot_message_id", "==", message_id)).limit(1)
            docs = [d async for d in q1.stream()]
            if docs:
                data = docs[0].to_dict()
                data["id"] = docs[0].id
                return data

            q2 = meals_ref.where(filter=FieldFilter("user_message_id", "==", message_id)).limit(1)
            docs = [d async for d in q2.stream()]
            if docs:
                data = docs[0].to_dict()
                data["id"] = docs[0].id
                return data
        except Exception as e:
            logger.warning(f"Error finding meal by message_id {message_id}: {e}")
        return None

    async def update_meal(self, chat_id: int, meal_id: str, updated_data: dict) -> None:
        """Updates an existing meal document."""
        db = await self.get_client()
        meal_ref = db.collection("users").document(str(chat_id)).collection("meals").document(meal_id)
        await meal_ref.update(updated_data)

    async def delete_meal(self, chat_id: int, meal_id: str) -> bool:
        """Deletes a specific meal document."""
        db = await self.get_client()
        meal_ref = db.collection("users").document(str(chat_id)).collection("meals").document(meal_id)
        doc = await meal_ref.get()
        if doc.exists:
            await meal_ref.delete()
            return True
        return False

    async def get_user_today_totals(self, chat_id: int, today_prefix: str) -> dict:
        """Calculates cumulative totals for a specific user and date."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        query = meals_ref.where(filter=FieldFilter("date", "==", today_prefix))
        totals = {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "item_count": 0,
            "meal_count": 0,
            "nutrition_score": None,
        }
        weighted_score_sum = 0.0
        simple_score_sum = 0.0
        score_count = 0

        async for doc in query.stream():
            m = doc.to_dict()
            meal_cals = float(m.get("total_calories", 0.0))
            totals["calories"] += meal_cals
            totals["protein"] += float(m.get("total_protein", 0.0))
            totals["carbs"] += float(m.get("total_carbs", 0.0))
            totals["fat"] += float(m.get("total_fat", 0.0))
            totals["fiber"] += float(m.get("total_fiber", 0.0))
            totals["item_count"] += len(m.get("items", []))
            totals["meal_count"] += 1

            items = m.get("items", [])
            has_item_scores = False
            if items:
                for it in items:
                    it_cal = float(it.get("calories", 0.0))
                    it_score = it.get("nutrition_score")
                    if it_score is not None:
                        weighted_score_sum += it_cal * float(it_score)
                        simple_score_sum += float(it_score)
                        score_count += 1
                        has_item_scores = True

            if not has_item_scores:
                score = m.get("nutrition_score")
                if score is not None:
                    score_val = float(score)
                    weighted_score_sum += meal_cals * score_val
                    simple_score_sum += score_val
                    score_count += 1

        totals["calories"] = round(totals["calories"], 1)
        totals["protein"] = round(totals["protein"], 1)
        totals["carbs"] = round(totals["carbs"], 1)
        totals["fat"] = round(totals["fat"], 1)
        totals["fiber"] = round(totals["fiber"], 1)

        if totals["calories"] > 0 and weighted_score_sum > 0:
            totals["nutrition_score"] = round(weighted_score_sum / totals["calories"])
        elif score_count > 0:
            totals["nutrition_score"] = round(simple_score_sum / score_count)
        else:
            totals["nutrition_score"] = None
        return totals

    async def get_user_meals_for_date(self, chat_id: int, date_str: str) -> List[dict]:
        """Fetches all meal documents logged by a user for a specific date (YYYY-MM-DD)."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        query = meals_ref.where(filter=FieldFilter("date", "==", date_str))
        meals = []
        async for doc in query.stream():
            m = doc.to_dict()
            m["id"] = doc.id
            meals.append(m)
        meals.sort(key=lambda x: x.get("time", ""))
        return meals

    async def get_user_date_range_records(
        self,
        chat_id: int,
        start_date_str: str,
        end_date_str: str,
    ) -> List[dict]:
        """Aggregates meals grouped by day within a date range."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        query = (
            meals_ref.where(filter=FieldFilter("date", ">=", start_date_str))
            .where(filter=FieldFilter("date", "<=", end_date_str))
            .order_by("date")
        )
        meals_by_date = {}
        async for doc in query.stream():
            m = doc.to_dict()
            d = m.get("date", "")
            if not d:
                continue
            if d not in meals_by_date:
                meals_by_date[d] = {
                    "date": d,
                    "calories": 0.0,
                    "protein": 0.0,
                    "carbs": 0.0,
                    "fat": 0.0,
                    "fiber": 0.0,
                    "nutrition_score_sum": 0.0,
                    "nutrition_score_count": 0,
                    "meal_count": 0,
                    "items": [],
                }
            meals_by_date[d]["calories"] += float(m.get("total_calories", 0.0))
            meals_by_date[d]["protein"] += float(m.get("total_protein", 0.0))
            meals_by_date[d]["carbs"] += float(m.get("total_carbs", 0.0))
            meals_by_date[d]["fat"] += float(m.get("total_fat", 0.0))
            meals_by_date[d]["fiber"] += float(m.get("total_fiber", 0.0))
            score = m.get("nutrition_score")
            if score is not None:
                meals_by_date[d]["nutrition_score_sum"] += float(score)
                meals_by_date[d]["nutrition_score_count"] += 1
            meals_by_date[d]["meal_count"] += 1
            for it in m.get("items", []):
                meals_by_date[d]["items"].append(it)

        return sorted(meals_by_date.values(), key=lambda x: x["date"])

    async def export_user_meals_csv(self, chat_id: int, fallback_user_name: str) -> str:
        """Generates a complete 11-column CSV export of all user meals."""
        db = await self.get_client()
        meals_ref = db.collection("users").document(str(chat_id)).collection("meals")
        query = meals_ref.order_by("timestamp")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(SHEET_HEADERS)
        async for doc in query.stream():
            m = doc.to_dict()
            dt_str = str(m.get("timestamp") or f"{m.get('date', '')} {m.get('time', '')}").strip()
            u_name = m.get("user_name") or fallback_user_name
            cat = m.get("category", "Meal")
            score = m.get("nutrition_score", 70)
            items = m.get("items", [])
            if not items:
                writer.writerow([
                    dt_str,
                    u_name,
                    "Meal Entry",
                    cat,
                    m.get("total_calories", 0.0),
                    m.get("total_protein", 0.0),
                    m.get("total_carbs", 0.0),
                    m.get("total_fat", 0.0),
                    m.get("total_fiber", 0.0),
                    m.get("motivational_note", ""),
                    score,
                ])
            else:
                for item in items:
                    writer.writerow([
                        dt_str,
                        u_name,
                        item.get("item_name", "Food Item"),
                        item.get("category", cat),
                        item.get("calories", 0.0),
                        item.get("protein", 0.0),
                        item.get("carbohydrates", 0.0),
                        item.get("fat", 0.0),
                        item.get("fiber", 0.0),
                        item.get("short_description", ""),
                        item.get("nutrition_score", score),
                    ])
        return output.getvalue()

    async def delete_all_user_data(self, chat_id: int) -> int:
        """Permanently erases user profile and all meal documents (PDPA Right to Erasure)."""
        db = await self.get_client()
        user_ref = db.collection("users").document(str(chat_id))
        meals_ref = user_ref.collection("meals")
        deleted_count = 0
        async for doc in meals_ref.stream():
            await doc.reference.delete()
            deleted_count += 1
        await user_ref.delete()
        return deleted_count

    async def get_all_subscriber_chat_ids(self) -> List[int]:
        """Returns all registered Telegram chat IDs."""
        try:
            db = await self.get_client()
            chat_ids = []
            async for doc in db.collection("users").stream():
                try:
                    chat_ids.append(int(doc.id))
                except ValueError:
                    pass
            return list(set(chat_ids))
        except Exception as e:
            logger.error(f"Error fetching subscribers from Firestore: {e}")
            return []

    async def save_feedback(
        self,
        chat_id: int,
        user_name: str,
        feedback_text: str,
        category: str = "general",
    ) -> str:
        """Stores user feedback in the root feedback collection."""
        db = await self.get_client()
        feedback_ref = db.collection("feedback").document()
        now_iso = datetime.now(LOCAL_TZ).isoformat()

        doc_data = {
            "feedback_id": feedback_ref.id,
            "chat_id": chat_id,
            "user_name": user_name,
            "feedback_text": feedback_text.strip(),
            "category": category,
            "status": "new",
            "created_at": now_iso,
        }
        await feedback_ref.set(doc_data)
        logger.info(f"Saved user feedback from {user_name} ({chat_id}): {feedback_ref.id}")
        return feedback_ref.id

    async def get_recent_feedback(self, limit: int = 10) -> List[dict]:
        """Retrieves recent user feedback submissions."""
        try:
            db = await self.get_client()
            query = db.collection("feedback").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
            feedbacks = []
            async for doc in query.stream():
                data = doc.to_dict()
                data["id"] = doc.id
                feedbacks.append(data)
            return feedbacks
        except Exception as e:
            logger.error(f"Error retrieving feedback from Firestore: {e}")
            return []

    async def get_admin_chat_ids(self) -> List[int]:
        """Returns chat IDs corresponding to authorized admin users."""
        admin_cids = set()
        admin_cids.add(102325434)  # Default fallback for James Boh
        try:
            db = await self.get_client()
            async for doc in db.collection("users").stream():
                d = doc.to_dict()
                uname = (d.get("user_name") or "").lstrip("@").lower()
                cid = d.get("chat_id")
                if (uname in ADMIN_USERS or str(cid) in ADMIN_USERS) and cid:
                    try:
                        admin_cids.add(int(cid))
                    except ValueError:
                        pass
        except Exception as e:
            logger.error(f"Error finding admin chat IDs: {e}")
        return list(admin_cids)

    async def touch_user_activity(
        self,
        chat_id: int,
        user_name: str,
        first_name: str = "",
    ) -> None:
        """Lightweight non-blocking update of last_active_at and user handle."""
        try:
            db = await self.get_client()
            user_ref = db.collection("users").document(str(chat_id))
            doc = await user_ref.get()
            now_iso = datetime.now(LOCAL_TZ).isoformat()
            if doc.exists:
                update_data = {
                    "last_active_at": now_iso,
                    "user_name": user_name,
                }
                if first_name:
                    update_data["first_name"] = first_name
                await user_ref.update(update_data)
            else:
                await user_ref.set({
                    "chat_id": chat_id,
                    "user_name": user_name,
                    "first_name": first_name,
                    "created_at": now_iso,
                    "last_active_at": now_iso,
                    "daily_calorie_target": 2000,
                    "daily_protein_target": 150,
                    "daily_fiber_target": 25,
                    "timezone": TIMEZONE_STR,
                })
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if "Event loop is closed" not in str(e):
                logger.warning(f"Could not touch user activity for {chat_id}: {e}")

    async def get_platform_metrics(self) -> dict:
        """Computes platform-wide growth, active users, log volume, and engagement metrics."""
        db = await self.get_client()
        now = datetime.now(LOCAL_TZ)
        today_str = now.strftime("%Y-%m-%d")
        seven_days_ago_dt = now - timedelta(days=7)
        seven_days_ago_str = seven_days_ago_dt.strftime("%Y-%m-%d")
        thirty_days_ago_dt = now - timedelta(days=30)
        twenty_four_hours_ago_dt = now - timedelta(hours=24)

        users = []
        async for doc in db.collection("users").stream():
            data = doc.to_dict()
            data["id"] = doc.id
            users.append(data)

        total_users = len(users)
        total_meals = 0
        total_items = 0
        meals_today = 0
        meals_7d = 0
        dau_users = set()
        wau_users = set()
        mau_users = set()
        activated_users = 0
        new_users_today = 0
        new_users_7d = 0
        user_breakdown = []

        for u in users:
            cid = u.get("chat_id") or u["id"]
            uname = u.get("user_name") or f"User_{cid}"
            created_at_str = u.get("created_at")
            last_active_str = u.get("last_active_at")

            # Signups
            if created_at_str:
                try:
                    c_dt = datetime.fromisoformat(created_at_str)
                    if c_dt.tzinfo is None:
                        c_dt = LOCAL_TZ.localize(c_dt)
                    if c_dt.date() == now.date():
                        new_users_today += 1
                    if c_dt >= seven_days_ago_dt:
                        new_users_7d += 1
                except Exception:
                    pass

            # Activity & Recency
            if last_active_str:
                try:
                    la_dt = datetime.fromisoformat(last_active_str)
                    if la_dt.tzinfo is None:
                        la_dt = LOCAL_TZ.localize(la_dt)
                    if la_dt >= twenty_four_hours_ago_dt or la_dt.date() == now.date():
                        dau_users.add(cid)
                    if la_dt >= seven_days_ago_dt:
                        wau_users.add(cid)
                    if la_dt >= thirty_days_ago_dt:
                        mau_users.add(cid)
                except Exception:
                    pass

            # Meals
            meals_ref = db.collection("users").document(str(cid)).collection("meals")
            u_meal_count = 0
            async for mdoc in meals_ref.stream():
                mdata = mdoc.to_dict()
                total_meals += 1
                u_meal_count += 1
                items = mdata.get("items") or []
                total_items += len(items)

                m_date = mdata.get("date") or (mdata.get("timestamp", "")[:10])
                if m_date == today_str:
                    meals_today += 1
                if m_date >= seven_days_ago_str:
                    meals_7d += 1

            if u_meal_count > 0:
                activated_users += 1

            user_breakdown.append({
                "chat_id": cid,
                "user_name": uname,
                "meals_count": u_meal_count,
                "last_active": last_active_str or "Never",
            })

        feedback_count = 0
        try:
            async for _ in db.collection("feedback").stream():
                feedback_count += 1
        except Exception:
            pass

        user_breakdown.sort(key=lambda x: x["meals_count"], reverse=True)

        activation_rate = round((activated_users / total_users * 100), 1) if total_users else 0.0
        stickiness = round((len(dau_users) / len(mau_users) * 100), 1) if mau_users else 0.0
        avg_meals_per_active = round(total_meals / activated_users, 1) if activated_users else 0.0

        return {
            "total_users": total_users,
            "activated_users": activated_users,
            "activation_rate": activation_rate,
            "new_users_today": new_users_today,
            "new_users_7d": new_users_7d,
            "dau": len(dau_users),
            "wau": len(wau_users),
            "mau": len(mau_users),
            "stickiness": stickiness,
            "total_meals": total_meals,
            "total_items": total_items,
            "meals_today": meals_today,
            "meals_7d": meals_7d,
            "avg_meals_per_active": avg_meals_per_active,
            "feedback_count": feedback_count,
            "user_breakdown": user_breakdown,
            "generated_at": now.strftime("%d %b %Y, %H:%M SGT"),
        }




# Global Firestore Service
firestore_service = FirestoreService(
    project_id="james-boh-macro-tracker",
    credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
    credentials_json=GOOGLE_SERVICE_ACCOUNT_JSON if GOOGLE_SERVICE_ACCOUNT_JSON else None,
)


# =====================================================================
# Google Sheets Integration (Optional Dual-Write / Legacy Fallback)
# =====================================================================
class GoogleSheetsService:
    def __init__(
        self,
        credentials_path: str,
        sheet_name: str,
        credentials_json: Optional[str] = None,
    ):
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.credentials_json = credentials_json
        self.spreadsheet = None
        self.worksheet = None
        self._lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return bool(self.credentials_json) or os.path.exists(self.credentials_path)

    def _get_worksheet_sync(self) -> gspread.Worksheet:
        if not self.is_configured():
            raise FileNotFoundError("Google Service Account credentials not found.")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        if self.credentials_json:
            service_account_info = json.loads(self.credentials_json)
            gc = gspread.service_account_from_dict(service_account_info, scopes=scopes)
        else:
            gc = gspread.service_account(filename=self.credentials_path, scopes=scopes)

        sh = gc.open(self.sheet_name)
        self.spreadsheet = sh
        ws = sh.sheet1
        existing_values = ws.get_all_values()
        if not existing_values or len(existing_values) == 0 or not any(existing_values[0]):
            ws.update(range_name="A1:K1", values=[SHEET_HEADERS])
        return ws

    async def get_worksheet(self) -> gspread.Worksheet:
        if self.worksheet is None:
            self.worksheet = await asyncio.to_thread(self._get_worksheet_sync)
        return self.worksheet

    async def append_food_items(self, rows: list) -> None:
        if not self.is_configured():
            return
        async with self._lock:
            ws = await self.get_worksheet()
            await asyncio.to_thread(ws.append_rows, rows, value_input_option="USER_ENTERED")


# Global Sheets Service instance (for optional dual-write)
sheets_service = GoogleSheetsService(
    credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
    sheet_name=GOOGLE_SHEET_NAME,
    credentials_json=GOOGLE_SERVICE_ACCOUNT_JSON if GOOGLE_SERVICE_ACCOUNT_JSON else None,
)


# =====================================================================
# Analytics & Chart Generation Service (Dark-Mode Visuals)
# =====================================================================
class AnalyticsService:
    @staticmethod
    def generate_trend_chart(
        daily_records: List[dict],
        calorie_target: float = 2000,
        protein_target: float = 150,
        fiber_target: float = 25,
        days_window: int = 7,
        targets_set: bool = False,
    ) -> bytes:
        """Generates a high-resolution dark-mode 3-panel PNG chart using matplotlib."""
        today = datetime.now(LOCAL_TZ).date()
        date_list = [today - timedelta(days=i) for i in reversed(range(days_window))]
        date_str_list = [d.strftime("%Y-%m-%d") for d in date_list]
        date_labels = [d.strftime("%a\n%d/%m") for d in date_list]

        record_map = {r["date"]: r for r in daily_records}
        cals = [record_map.get(d, {}).get("calories", 0.0) for d in date_str_list]
        proteins = [record_map.get(d, {}).get("protein", 0.0) for d in date_str_list]
        fibers = [record_map.get(d, {}).get("fiber", 0.0) for d in date_str_list]
        carbs = [record_map.get(d, {}).get("carbs", 0.0) for d in date_str_list]
        fats = [record_map.get(d, {}).get("fat", 0.0) for d in date_str_list]

        # Dark Theme Palette matching Telegram Dark Mode
        BG_COLOR = "#18222d"
        CARD_COLOR = "#232e3c"
        TEXT_PRIMARY = "#ffffff"
        TEXT_MUTED = "#8e99a4"
        GRID_COLOR = "#2e3b4b"
        GREEN_ACCENT = "#22c55e"
        AMBER_ACCENT = "#f59e0b"
        CYAN_ACCENT = "#38bdf8"
        PURPLE_ACCENT = "#a855f7"
        RED_ACCENT = "#ef4444"

        fig = plt.figure(figsize=(10, 8), facecolor=BG_COLOR)
        gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], hspace=0.35, wspace=0.25)

        # 1. Daily Calories vs Target (Top Left)
        ax1 = fig.add_subplot(gs[0, 0])
        ax1.set_facecolor(CARD_COLOR)
        bar_colors = [
            GREEN_ACCENT if 0 < c <= calorie_target * 1.08 else (AMBER_ACCENT if c > 0 else "#475569")
            for c in cals
        ]
        ax1.bar(range(len(date_labels)), cals, color=bar_colors, width=0.55, zorder=3)
        cal_label = f"Target: {int(calorie_target)} kcal" if targets_set else f"Baseline: {int(calorie_target)} kcal"
        ax1.axhline(
            calorie_target,
            color=RED_ACCENT,
            linestyle="--",
            linewidth=1.5,
            zorder=4,
            label=cal_label,
        )
        ax1.set_title("Daily Calories vs Target" if targets_set else "Daily Calories (Baseline: 2,000)", color=TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=10)
        ax1.set_xticks(range(len(date_labels)))
        ax1.set_xticklabels(date_labels, color=TEXT_MUTED, fontsize=8)
        ax1.tick_params(colors=TEXT_MUTED)
        ax1.grid(color=GRID_COLOR, linestyle=":", linewidth=0.8, axis="y", zorder=0)
        ax1.legend(facecolor=CARD_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_PRIMARY, fontsize=8)
        for spine in ax1.spines.values():
            spine.set_color(GRID_COLOR)

        # 2. Macro Calorie Distribution Donut (Top Right)
        ax2 = fig.add_subplot(gs[0, 1])
        ax2.set_facecolor(CARD_COLOR)
        tot_p = sum(proteins)
        tot_c = sum(carbs)
        tot_f = sum(fats)
        total_energy = (tot_p * 4) + (tot_c * 4) + (tot_f * 9)
        if total_energy > 0:
            macro_sizes = [tot_p * 4, tot_c * 4, tot_f * 9]
            macro_labels = ["Protein", "Carbs", "Fat"]
            macro_colors = [CYAN_ACCENT, "#fbbf24", "#f87171"]
            wedges, texts, autotexts = ax2.pie(
                macro_sizes,
                labels=macro_labels,
                autopct="%1.0f%%",
                pctdistance=0.76,
                startangle=140,
                colors=macro_colors,
                wedgeprops=dict(width=0.42, edgecolor=BG_COLOR, linewidth=2),
                textprops=dict(color=TEXT_PRIMARY, fontsize=9, fontweight="bold"),
            )
            for at in autotexts:
                at.set_color("#0f172a")
                at.set_fontsize(9)
                at.set_fontweight("bold")
            ax2.set_title("Energy Breakdown", color=TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=10)
        else:
            ax2.text(0.5, 0.5, "No meals logged\nin period", ha="center", va="center", color=TEXT_MUTED, fontsize=10)
            ax2.set_title("Energy Breakdown", color=TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=10)
            ax2.axis("off")

        # 3. Protein & Fiber Bars (Bottom Full Width)
        ax3 = fig.add_subplot(gs[1, :])
        ax3.set_facecolor(CARD_COLOR)
        x = range(len(date_labels))
        width = 0.35
        pro_lbl = f"Protein (Target: {int(protein_target)}g)" if targets_set else f"Protein (Baseline: {int(protein_target)}g)"
        fib_lbl = f"Fiber (Target: {int(fiber_target)}g)" if targets_set else f"Fiber (Baseline: {int(fiber_target)}g)"
        ax3.bar([i - width/2 for i in x], proteins, width=width, color=CYAN_ACCENT, label=pro_lbl, zorder=3)
        ax3.bar([i + width/2 for i in x], fibers, width=width, color=PURPLE_ACCENT, label=fib_lbl, zorder=3)
        ax3.axhline(protein_target, color=CYAN_ACCENT, linestyle=":", linewidth=1.2, zorder=4)
        ax3.axhline(fiber_target, color=PURPLE_ACCENT, linestyle=":", linewidth=1.2, zorder=4)
        ax3.set_title("Daily Protein & Dietary Fiber (g)", color=TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=10)
        ax3.set_xticks(range(len(date_labels)))
        ax3.set_xticklabels(date_labels, color=TEXT_MUTED, fontsize=8)
        ax3.tick_params(colors=TEXT_MUTED)
        ax3.grid(color=GRID_COLOR, linestyle=":", linewidth=0.8, axis="y", zorder=0)
        ax3.legend(facecolor=CARD_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_PRIMARY, fontsize=8, loc="upper left")
        for spine in ax3.spines.values():
            spine.set_color(GRID_COLOR)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor=BG_COLOR)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    @staticmethod
    def format_analytics_text(
        daily_records: List[dict],
        user_name: str,
        days_window: int = 7,
        targets: Optional[dict] = None,
    ) -> str:
        """Formats the executive text coaching summary for the analytics report."""
        targets = targets or {}
        targets_set = bool(targets.get("targets_set", False))
        cal_target = targets.get("daily_calorie_target", 2000)
        pro_target = targets.get("daily_protein_target", 150)
        fib_target = targets.get("daily_fiber_target", 25)

        logged_days = [r for r in daily_records if r.get("calories", 0) > 0]
        days_logged_count = len(logged_days)

        if not logged_days:
            return (
                f"📊 <b>Nutrition Trends ({days_window}-Day Summary)</b>\n"
                f"👤 <b>User:</b> {html.escape(user_name)}\n\n"
                f"<i>No meals logged in the past {days_window} days.</i>\n\n"
                "💡 <i>Snap a photo or type what you ate to start seeing your trend graphs!</i>"
            )

        avg_cals = round(sum(r["calories"] for r in logged_days) / days_logged_count, 1)
        avg_pro = round(sum(r["protein"] for r in logged_days) / days_logged_count, 1)
        avg_carbs = round(sum(r["carbs"] for r in logged_days) / days_logged_count, 1)
        avg_fat = round(sum(r["fat"] for r in logged_days) / days_logged_count, 1)
        avg_fiber = round(sum(r["fiber"] for r in logged_days) / days_logged_count, 1)

        scores = [
            r["nutrition_score_sum"] / r["nutrition_score_count"]
            for r in logged_days
            if r.get("nutrition_score_count", 0) > 0
        ]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 75

        # Adherence calculation
        on_track_days = sum(1 for r in logged_days if abs(r["calories"] - cal_target) <= (cal_target * 0.15))
        adherence_pct = round((on_track_days / days_logged_count) * 100)

        lbl = "Target" if targets_set else "Baseline"
        lines = [
            f"📊 <b>Nutrition Trends ({days_window}-Day Digest)</b>",
            f"👤 <b>User:</b> {html.escape(user_name)}",
            f"📅 <b>Logged:</b> {days_logged_count}/{days_window} days | <b>Adherence:</b> {adherence_pct}%",
            "",
            "──────── <b>Daily Averages</b> ────────",
            f"🔥 <b>Calories:</b> {avg_cals:,.0f} kcal <i>({lbl}: {cal_target:,.0f})</i>",
            f"🥩 <b>Protein:</b> {avg_pro:.1f}g <i>({lbl}: {pro_target}g)</i>",
            f"🌾 <b>Fiber:</b> {avg_fiber:.1f}g <i>({lbl}: {fib_target}g)</i>",
            f"🍞 <b>Carbohydrates:</b> {avg_carbs:.1f}g",
            f"🥑 <b>Fat:</b> {avg_fat:.1f}g",
            f"⭐ <b>Average Nutrition Score:</b> {avg_score:.0f}/100",
            "",
            "──────── <b>Day-by-Day Calorie Tracker</b> ────────",
        ]

        today = datetime.now(LOCAL_TZ).date()
        date_list = [today - timedelta(days=i) for i in reversed(range(days_window))]
        tracker_dates = date_list[-7:] if days_window > 7 else date_list
        record_map = {r["date"]: r for r in daily_records}

        for d in tracker_dates:
            d_str = d.strftime("%Y-%m-%d")
            d_label = d.strftime("%a %d/%m")
            rec = record_map.get(d_str)
            cals = rec.get("calories", 0.0) if rec else 0.0
            if cals <= 0:
                lines.append(f"{d_label}: <i>No meals</i> ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜")
            else:
                ratio = cals / cal_target
                if ratio <= 1.08:
                    filled = min(10, max(1, round(ratio * 10)))
                    bar = ("🟩" * filled) + ("⬜" * (10 - filled))
                    status = "On target" if ratio >= 0.85 else f"-{int(cal_target - cals)} kcal"
                elif ratio <= 1.25:
                    bar = "🟨" * 10
                    status = f"+{int(cals - cal_target)} kcal"
                else:
                    bar = "🟧" * 10
                    status = f"+{int(cals - cal_target)} kcal"
                lines.append(f"{d_label}: <b>{cals:,.0f} kcal</b> {bar} <i>({status})</i>")

        lines.append("")
        lines.append("💡 <b>Coach Analysis:</b>")

        if not targets_set:
            lines.append("🎯 <i>Notice: Comparisons use standard reference baselines (2,000 kcal / 150g protein). To set your personal goals, tap 'Set Daily Targets' below or type /targets!</i>")
        elif avg_pro >= pro_target and avg_cals <= cal_target:
            lines.append("🏆 <i>Outstanding discipline! You're consistently hitting high protein while maintaining your calorie target. Lean mass preservation is on track!</i>")
        elif avg_cals > cal_target * 1.1:
            diff = int(avg_cals - cal_target)
            lines.append(f"⚠️ <i>Calories averaged +{diff} kcal/day above target. Keep an eye out for cooking oils, dressings, or sugary drinks.</i>")
        elif avg_pro < pro_target * 0.8:
            lines.append(f"🥩 <i>Protein intake averaged {avg_pro:.0f}g vs {pro_target}g target. Boost lean protein sources (chicken breast, egg whites, Greek yogurt, whey).</i>")
        else:
            lines.append("👍 <i>Solid, steady habits! Consistency is the #1 driver of long-term body composition changes.</i>")

        return "\n".join(lines)


# Multi-tiered model cascade engineered for ultra-low latency & 99.99% availability:
# 1. Primary low-latency flagship: gemini-3.5-flash-lite (sub-second response, high throughput)
# 2. Standard flash-lite alias: gemini-flash-lite-latest (always available fast fallback)
# 3. Dedicated backup lite: gemini-3.1-flash-lite (reliable failsafe)
# 4. Multimodal reasoning fallback: gemini-3.6-flash (tertiary fallback)
# 5. Legacy safety net: gemini-3.5-flash (battle-tested last-resort fallback)
GEMINI_CANDIDATE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


def optimize_image_for_vision(image_bytes: bytes, max_dim: int = 1280, quality: int = 85) -> bytes:
    """
    Downscales and optimizes food photos for Gemini Vision.
    Reduces 3MB-8MB smartphone captures to ~100-200KB, slashing payload by >90%
    and reducing vision token tiling from 6,000+ to ~1,000 for sub-3-second inference.
    """
    if not image_bytes:
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out_buf = io.BytesIO()
            img.save(out_buf, format="JPEG", quality=quality, optimize=True)
            return out_buf.getvalue()
    except Exception as e:
        logger.warning(f"Image optimization skipped ({e}), using raw bytes.")
        return image_bytes


def get_gemini_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing in your .env configuration.")
    return genai.Client(api_key=GEMINI_API_KEY)


async def analyze_food_with_gemini(
    text_prompt: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    images: Optional[List[bytes]] = None,
    mime_type: str = "image/jpeg",
) -> MealAnalysisResponse:
    """Sends photo(s) and/or text to Gemini with structured JSON output."""
    client = get_gemini_client()
    contents = [GEMINI_SYSTEM_PROMPT]

    all_images = images if images else ([image_bytes] if image_bytes else [])
    for img in all_images:
        opt_img = optimize_image_for_vision(img)
        contents.append(types.Part.from_bytes(data=opt_img, mime_type="image/jpeg"))

    if len(all_images) > 1:
        contents.append(
            f"MULTI-PHOTO MEAL CONTEXT:\n"
            f"The user uploaded {len(all_images)} photos representing a SINGLE meal spread or restaurant order.\n"
            f"Evaluate all photos collectively as ONE meal. Do NOT duplicate identical dishes that appear across photos."
        )

    if text_prompt:
        contents.append(
            f"USER NOTE / FOOD DESCRIPTION (STRICT PRIORITY):\n"
            f"\"{text_prompt}\"\n\n"
            f"CRITICAL: The user's input above is absolute ground truth. "
            f"If the user specifies any item names, preparations, restaurant/brand names (e.g. 'Ajumma', 'Hai Di Lao', 'nutrisoy'), "
            f"or portions (e.g. 'half of each', 'shared between 2'), apply your knowledge of that restaurant/brand menu "
            f"and accurately calculate the corresponding macro profile."
        )
    elif not all_images:
        raise ValueError("Either text description or image must be provided.")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=MealAnalysisResponse,
        temperature=0.2,
    )

    last_error = None
    for model_name in GEMINI_CANDIDATE_MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=12.0,
            )
            if response.parsed and isinstance(response.parsed, MealAnalysisResponse):
                return response.parsed
            if response.text:
                return MealAnalysisResponse.model_validate_json(response.text)
        except Exception as e:
            logger.warning(f"Model {model_name} failed: {e}. Trying fallback...")
            last_error = e
            await asyncio.sleep(0.1)
            continue

    if last_error:
        raise last_error
    raise ValueError("Gemini returned an empty or unparseable response.")


def extract_category_override(instruction: str) -> Optional[str]:
    """Extracts explicit meal category target from natural language edit instruction."""
    low = instruction.lower().strip()
    if any(k in low for k in ["for breakfast", "as breakfast", "to breakfast", "is breakfast", "was breakfast", "my breakfast"]):
        return "Breakfast"
    if any(k in low for k in ["for lunch", "as lunch", "to lunch", "is lunch", "was lunch", "my lunch"]):
        return "Lunch"
    if any(k in low for k in ["for dinner", "as dinner", "to dinner", "is dinner", "was dinner", "my dinner"]):
        return "Dinner"
    if any(k in low for k in ["for snack", "as snack", "to snack", "is snack", "was snack", "my snack"]):
        return "Snack"
    if low in ["breakfast", "lunch", "dinner", "snack"]:
        return low.capitalize()
    return None


async def edit_meal_with_gemini(
    existing_meal: dict,
    edit_instructions: str,
) -> MealAnalysisResponse:
    """Applies user modification instructions to an existing meal and recalculates macros."""
    client = get_gemini_client()
    existing_items_summary = []
    for item in existing_meal.get("items", []):
        existing_items_summary.append(
            f"- {item.get('item_name')}: {item.get('calories')} kcal, "
            f"{item.get('protein')}g P, {item.get('carbohydrates')}g C, "
            f"{item.get('fat')}g F, {item.get('fiber', 0)}g fiber ({item.get('category')})"
        )
    existing_text = "\n".join(existing_items_summary) or "1 Meal entry"

    prompt = (
        f"CURRENT LOGGED MEAL (Category: {existing_meal.get('category', 'Meal')}):\n{existing_text}\n\n"
        f"USER MODIFICATION REQUEST:\n\"{edit_instructions}\"\n\n"
        f"TASK:\n"
        f"1. Apply the user's adjustments (e.g. portion size, remove items, add items, meal category like Breakfast/Lunch/Dinner/Snack, sugar-free substitutions).\n"
        f"2. If the user indicates a change in meal type/category (e.g. 'this is lunch', 'change to dinner', 'mark as snack'), update the `category` of all items to match that meal type.\n"
        f"3. Return the complete updated list of `items` with precise recalculated macros.\n"
        f"4. Provide a warm, concise note in `motivational_note` summarizing the update."
    )

    contents = [GEMINI_SYSTEM_PROMPT, prompt]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=MealAnalysisResponse,
        temperature=0.1,
    )

    last_error = None
    for model_name in GEMINI_CANDIDATE_MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=12.0,
            )
            analysis = None
            if response.parsed and isinstance(response.parsed, MealAnalysisResponse):
                analysis = response.parsed
            elif response.text:
                analysis = MealAnalysisResponse.model_validate_json(response.text)

            if analysis:
                category_override = extract_category_override(edit_instructions)
                if category_override:
                    for it in analysis.items:
                        it.category = category_override
                return analysis
        except Exception as e:
            logger.warning(f"Model {model_name} failed during edit: {e}. Trying fallback...")
            last_error = e
            await asyncio.sleep(0.1)
            continue

    if last_error:
        raise last_error
    raise ValueError("Failed to modify meal with Gemini.")


async def answer_analytics_qa_with_gemini(
    user_question: str,
    context_data: str,
    user_name: str,
) -> str:
    """Answers analytical nutrition questions using verified historical data context."""
    client = get_gemini_client()
    system_prompt = (
        "You are an elite, encouraging sports nutritionist and data analyst for the user's Macro Tracker bot.\n"
        "You are answering questions about the user's past eating history, consistency, and goal progress.\n"
        "RULES:\n"
        "1. Answer using ONLY the provided verified daily nutrition records and targets.\n"
        "2. Provide exact numbers, averages, percentages, and dates from the data.\n"
        "3. Format response in clean Telegram HTML format (use <b>, <i>, <code>). Do NOT use markdown asterisks (* or **).\n"
        "4. Keep the answer concise (2-4 short paragraphs max), direct, motivating, and actionable.\n"
        "5. If the user asks about hitting goals, calculate their exact adherence % (days on target / days logged) and highlight strengths."
    )
    prompt = (
        f"USER: {user_name}\n\n"
        f"VERIFIED HISTORICAL DATA & TARGETS:\n{context_data}\n\n"
        f"USER'S QUESTION:\n\"{user_question}\""
    )
    contents = [system_prompt, prompt]
    config = types.GenerateContentConfig(
        temperature=0.2,
    )
    last_error = None
    for model_name in GEMINI_CANDIDATE_MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=12.0,
            )
            if response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Model {model_name} failed during analytics QA: {e}. Trying fallback...")
            last_error = e
            await asyncio.sleep(0.1)
            continue

    if last_error:
        raise last_error
    raise ValueError("Failed to answer analytical query with Gemini.")


# =====================================================================
# Historical Date Parsing & Analytics Helpers
# =====================================================================
MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_historical_date(text: str, reference_date: Optional[date] = None) -> Optional[date]:
    """Parses natural language date references relative to Singapore local time."""
    if reference_date is None:
        reference_date = datetime.now(LOCAL_TZ).date()
    t = text.strip().lower().replace("’", "'")

    if "yesterday" in t or "last night" in t:
        return reference_date - timedelta(days=1)

    ago_m = re.search(r"\b(\d+)\s+days?\s+ago\b", t)
    if ago_m:
        return reference_date - timedelta(days=int(ago_m.group(1)))

    # ISO Format: YYYY-MM-DD
    iso_m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", t)
    if iso_m:
        try:
            return date(int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3)))
        except ValueError:
            pass

    # DD/MM/YYYY or DD/MM
    slash_m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d\d))?\b", t)
    if slash_m:
        d = int(slash_m.group(1))
        m = int(slash_m.group(2))
        y = int(slash_m.group(3)) if slash_m.group(3) else reference_date.year
        try:
            res = date(y, m, d)
            if not slash_m.group(3) and res > reference_date:
                res = date(y - 1, m, d)
            return res
        except ValueError:
            pass

    # 25 Sep or 25th September
    d_m_pattern = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-zA-Z]+)(?:\s+(20\d\d))?\b", t)
    if d_m_pattern:
        d = int(d_m_pattern.group(1))
        mon_str = d_m_pattern.group(2)
        if mon_str in MONTH_NAMES:
            m = MONTH_NAMES[mon_str]
            y = int(d_m_pattern.group(3)) if d_m_pattern.group(3) else reference_date.year
            try:
                res = date(y, m, d)
                if not d_m_pattern.group(3) and res > reference_date:
                    res = date(y - 1, m, d)
                return res
            except ValueError:
                pass

    # September 25 or Sep 25th
    m_d_pattern = re.search(r"\b([a-zA-Z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(20\d\d))?\b", t)
    if m_d_pattern:
        mon_str = m_d_pattern.group(1)
        d = int(m_d_pattern.group(2))
        if mon_str in MONTH_NAMES:
            m = MONTH_NAMES[mon_str]
            y = int(m_d_pattern.group(3)) if m_d_pattern.group(3) else reference_date.year
            try:
                res = date(y, m, d)
                if not m_d_pattern.group(3) and res > reference_date:
                    res = date(y - 1, m, d)
                return res
            except ValueError:
                pass

    # Weekdays: 'last friday', 'on friday', 'friday'
    for name, wd in WEEKDAYS.items():
        if re.search(rf"\b(last|on|this)?\s*{name}\b", t):
            today_wd = reference_date.weekday()
            delta = (today_wd - wd) % 7
            if delta == 0:
                delta = 7
            return reference_date - timedelta(days=delta)

    return None


def extract_historical_meal_date(text: Optional[str], reference_date: Optional[date] = None) -> date:
    """
    Extracts intended target date for a meal from caption or text prompt.
    Supports phrases like:
      - 'this was yesterday's dinner', 'yesterday dinner', 'dinner yesterday'
      - 'last night's dinner', 'dinner last night', 'last night'
      - 'for yesterday', 'from yesterday', 'eaten yesterday', 'was yesterday'
      - '2 days ago', 'on friday', 'dated 2026-10-08'
    Defaults to reference_date (today) if no historical date phrase is found.
    """
    if reference_date is None:
        reference_date = datetime.now(LOCAL_TZ).date()

    if not text:
        return reference_date

    low = text.lower().strip().replace("’", "'")

    # Fast check for yesterday or last night
    if any(k in low for k in ["yesterday", "last night"]):
        return reference_date - timedelta(days=1)

    # General date parsing
    parsed = parse_historical_date(low, reference_date=reference_date)
    if parsed and parsed <= reference_date:
        date_intent_indicators = [
            "for", "on", "from", "was", "eaten", "logged", "dated", "ago", "last", "yesterday", "night"
        ]
        if any(w in low for w in date_intent_indicators) or re.search(r"\b(20\d\d-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2})\b", low):
            return parsed

    return reference_date


def extract_date_override(instruction: str, reference_date: Optional[date] = None) -> Optional[date]:
    """Detects explicit date modifications in natural language edit instructions."""
    if reference_date is None:
        reference_date = datetime.now(LOCAL_TZ).date()
    low = instruction.lower().strip().replace("’", "'")

    date_indicators = [
        "yesterday", "last night", "change date", "move to", "for yesterday",
        "was yesterday", "eaten yesterday", "from yesterday", "date:", "dated"
    ]
    if any(k in low for k in date_indicators) or re.search(r"\b(20\d\d-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2})\b", low):
        return parse_historical_date(low, reference_date)
    return None


def is_date_lookup(text: str, reference_date: Optional[date] = None) -> bool:
    """Determines whether a message is requesting past meals for a specific date."""
    parsed = parse_historical_date(text, reference_date)
    if not parsed:
        return False
    t = text.strip().lower()

    # Direct date words
    if t in ["yesterday", "yesterday meals", "yesterday summary", "yesterday intake", "yesterday calories"]:
        return True

    clean = re.sub(r"^(on|show|show me|view|get|see)\s+", "", t).strip()
    if clean in ["yesterday", "last friday", "last monday", "last tuesday", "last wednesday", "last thursday", "last saturday", "last sunday"]:
        return True
    if re.fullmatch(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[a-zA-Z]+(?:\s+\d{4})?\b", clean):
        return True
    if re.fullmatch(r"\b[a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?\b", clean):
        return True
    if re.fullmatch(r"\b\d{4}-\d{1,2}-\d{1,2}\b", clean) or re.fullmatch(r"\b\d{1,2}/\d{1,2}(?:/\d{4})?\b", clean):
        return True

    lookup_keywords = [
        "what did i eat", "what i ate", "what was my intake", "what did i have",
        "meals on", "food on", "calories on", "intake on", "summary on", "log on",
        "logs on", "history on", "show meals", "show meal", "show food", "show intake",
        "show log", "show logs", "show date", "show me meals", "show me what i ate",
        "how many calories on", "did i eat anything on", "how much did i eat on",
        "meals yesterday", "calories yesterday", "intake yesterday"
    ]
    return any(k in t for k in lookup_keywords)


def is_analytics_qa(text: str) -> bool:
    """Determines whether a message is asking an analytical question about past nutrition data."""
    t = text.strip().lower()

    qa_starters = [
        "did i hit", "did i reach", "did i meet", "have i hit", "have i reached",
        "was i on track", "am i on track", "was i on target", "am i on target",
        "how many days", "how often", "what was my average", "what is my average",
        "highest calorie", "lowest calorie", "best day", "worst day",
        "most protein", "least protein", "did i achieve", "did i exceed",
        "how was my diet", "how were my macros",
        "how is my adherence", "how was my adherence", "calorie goal", "calorie target"
    ]
    if any(k in t for k in qa_starters):
        return True

    has_metric_or_eval = any(k in t for k in [
        "goal", "goals", "target", "targets", "adherence", "average", "avg",
        "track", "on track", "discipline", "deficit", "surplus"
    ])
    has_timeframe = any(k in t for k in [
        "past month", "this month", "last month", "past week", "this week",
        "last week", "past 7 days", "past 30 days", "30 days", "7 days"
    ])
    has_query_intent = any(k in t for k in ["did i", "was i", "how", "what", "check", "evaluate", "analyze"])

    if has_metric_or_eval and (has_timeframe or has_query_intent):
        return True

    return False


def format_specific_date_summary(
    date_obj: date,
    meals: List[dict],
    user_name: str,
    targets: Optional[dict] = None,
) -> str:
    """Formats a single-day historical meal summary with item breakdown and cumulative totals."""
    targets = targets or {}
    targets_set = bool(targets.get("targets_set", False))
    cal_target = targets.get("daily_calorie_target", 2000)
    pro_target = targets.get("daily_protein_target", 150)
    fib_target = targets.get("daily_fiber_target", 25)

    date_str_formatted = date_obj.strftime("%A, %d %b %Y")
    header = (
        f"📅 <b>Meals Logged on {date_str_formatted}</b>\n"
        f"👤 <b>User:</b> {html.escape(user_name)}\n"
    )

    if not meals:
        return (
            header + "\n"
            "<i>No meals were logged on this date.</i>\n\n"
            "💡 <i>Tip: Snap a photo or type what you ate to log your meals for today!</i>"
        )

    lines = [header]
    cat_emojis = {
        "breakfast": "🍳",
        "lunch": "🥣",
        "dinner": "🥩",
        "snack": "🥪",
        "drink": "☕",
    }

    tot_cals = 0.0
    tot_pro = 0.0
    tot_carbs = 0.0
    tot_fat = 0.0
    tot_fiber = 0.0
    weighted_score_sum = 0.0
    score_sum = 0.0
    score_count = 0

    for idx, m in enumerate(meals, start=1):
        cat = m.get("category", "Meal")
        emoji = cat_emojis.get(cat.lower(), "🍽️")
        cals = float(m.get("total_calories", 0.0))
        pro = float(m.get("total_protein", 0.0))
        carbs = float(m.get("total_carbs", 0.0))
        fat = float(m.get("total_fat", 0.0))
        fiber = float(m.get("total_fiber", 0.0))
        time_str = m.get("time", "")

        tot_cals += cals
        tot_pro += pro
        tot_carbs += carbs
        tot_fat += fat
        tot_fiber += fiber

        score = m.get("nutrition_score")
        if score is not None:
            score_val = float(score)
            weighted_score_sum += cals * score_val
            score_sum += score_val
            score_count += 1

        items = m.get("items", [])
        if items:
            item_desc = ", ".join(it.get("name", "Item") for it in items)
        else:
            item_desc = "Meal Entry"

        time_part = f" ({time_str})" if time_str else ""
        lines.append(
            f"{idx}. {emoji} <b>{cat}</b>{time_part}: {html.escape(item_desc)}\n"
            f"   └ <b>{cals:,.0f} kcal</b> | 🥩 {pro:.1f}g | 🍞 {carbs:.1f}g | 🥑 {fat:.1f}g"
        )

    # Calculate day nutrition score
    if tot_cals > 0 and weighted_score_sum > 0:
        day_score = round(weighted_score_sum / tot_cals)
    elif score_count > 0:
        day_score = round(score_sum / score_count)
    else:
        day_score = None

    lines.append("")
    lines.append("──────── <b>Day Cumulative Totals</b> ────────")
    cal_lbl = f" / {int(cal_target)}" if targets_set else ""
    pro_lbl = f" / {int(pro_target)}g" if targets_set else ""
    fib_lbl = f" / {int(fib_target)}g" if targets_set else ""
    lines.append(f"🔥 <b>Calories:</b> {tot_cals:,.0f} kcal{cal_lbl}")
    lines.append(f"🥩 <b>Protein:</b> {tot_pro:.1f} g{pro_lbl}")
    lines.append(f"🍞 <b>Carbs:</b> {tot_carbs:.1f} g | 🥑 <b>Fat:</b> {tot_fat:.1f} g | 🌾 <b>Fiber:</b> {tot_fiber:.1f} g{fib_lbl}")

    if day_score is not None:
        if day_score >= 80:
            badge = "🟢 <b>Excellent Quality & Cut Score:</b>"
        elif day_score >= 60:
            badge = "🟡 <b>Balanced Quality & Cut Score:</b>"
        else:
            badge = "🟠 <b>Moderate Quality & Cut Score:</b>"
        lines.append(f"{badge} {day_score}/100")

    return "\n".join(lines)


# =====================================================================
# Natural Language Intent Classification & User Helpers
# =====================================================================
def classify_text_intent(text: str) -> str:
    """Classifies incoming text messages to route actions naturally without slash commands."""
    t = text.strip().lower()

    # 0. Today's Totals Check
    today_phrases = [
        "today", "today totals", "today's totals", "my calories today",
        "how many calories today", "show today", "today summary", "today's summary",
        "what are my calories today", "what did i eat today", "what's my intake today",
        "summary today", "my macros today", "how much did i eat today", "today's macros"
    ]
    if t in today_phrases or any(k in t for k in ["calories today", "macros today", "total calories today", "summary today"]):
        return "today"

    # 1. Undo
    if t in ["undo", "undo that", "delete last meal", "delete my last meal", "cancel last meal", "remove last meal", "undo last"]:
        return "undo"

    # 2. Export / Data Portability
    if any(k in t for k in ["export", "download csv", "download spreadsheet", "download my logs", "export my data", "send me my data", "get csv", "export to excel", "download my data"]):
        return "export"

    # 3. Privacy & Residency
    if any(k in t for k in ["is my data private", "where is my data stored", "what happens to my data", "what do you do with my photo", "privacy policy", "data privacy", "who sees my data"]):
        return "privacy"

    # 4. Deletion / Right to Erasure
    if any(k in t for k in ["delete my account", "wipe my data", "wipe all my data", "clear my history", "delete all my data", "reset my account", "delete everything"]):
        return "delete"

    # 5. Targets & Goals Configuration
    target_commands = [
        "targets", "goals", "my targets", "my goals", "set targets",
        "set goals", "change targets", "change goals", "edit targets",
        "show targets", "what are my targets", "view targets", "my daily targets",
        "check targets", "calorie target", "protein target", "target calories", "target protein",
    ]
    if t in target_commands or t.startswith(("set target", "set goal", "change target", "change goal", "edit target")):
        return "targets"

    # 6. Meal Editing / Corrections / Category Updates
    edit_prefixes = (
        "actually", "wait", "edit", "update", "correction", "change",
        "instead of", "i meant", "remove the", "no sugar", "make that",
        "this is for", "this was for", "this entry is for", "this meal is for",
        "this entry was", "this was my", "this is my", "mark as", "switch to",
        "set category", "category to", "category is",
    )
    exact_category_edits = [
        "for lunch", "for dinner", "for breakfast", "for snack",
        "lunch", "dinner", "breakfast", "snack",
        "change to lunch", "change to dinner", "change to breakfast", "change to snack",
        "it was lunch", "it was dinner", "it was breakfast", "it was snack",
        "this is lunch", "this is dinner", "this is breakfast", "this is snack",
        "this was lunch", "this was dinner", "this was breakfast", "this was snack",
    ]
    if (
        t in exact_category_edits
        or t.startswith(edit_prefixes)
        or "change to" in t
        or "actually it was" in t
        or "this entry is" in t
        or "this entry was" in t
    ):
        return "edit"

    # 7. User Feedback & Suggestions
    feedback_prefixes = ("feedback:", "feedback -", "feedback ", "suggest:", "suggestion:", "bug:", "bug report:")
    feedback_phrases = [
        "feedback", "give feedback", "send feedback", "submit feedback",
        "i have feedback", "leave feedback", "have feedback",
        "feature request", "i have a feature request", "make a feature request",
        "suggestion", "i have a suggestion", "suggestions",
        "bug report", "report a bug", "found a bug", "there is a bug", "i found a bug"
    ]
    if (
        t in feedback_phrases
        or t.startswith(feedback_prefixes)
        or any(k in t for k in ["give feedback", "submit feedback", "send feedback", "feature request", "report a bug", "i have a suggestion"])
    ):
        return "feedback"

    # 8. Conversational Analytical Q&A (e.g. 'did i hit my calorie goals for the past month?')
    if is_analytics_qa(t):
        return "analytics_qa"

    # 9. Historical Specific Date Lookup (e.g. 'what did i eat yesterday?', '25 sep')
    if is_date_lookup(t):
        return "date_query"

    # 10. Direct Analytics Digest Shortcuts (e.g. 'weekly', 'monthly', 'trends')
    analytics_keywords = [
        "analytics", "trend", "trends", "weekly", "monthly", "how did i do",
        "show me my week", "show my progress", "stats", "charts", "chart",
        "my intake this week", "summary for this week", "progress report",
        "macro breakdown for the week", "macro trend"
    ]
    if any(k in t for k in analytics_keywords):
        if "month" in t or "30 day" in t or "30-day" in t:
            return "analytics_30d"
        return "analytics_7d"

    # 11. Food Logging (Default)
    return "food_log"


def get_user_display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Unknown"
    if user.username:
        return f"@{user.username}"
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or f"User_{user.id}"


def is_user_authorized(update: Update) -> bool:
    if ALLOWED_USERS is None:
        return True
    user = update.effective_user
    if not user:
        return False
    username = (user.username or "").lower().lstrip("@")
    user_id = str(user.id)
    return (username in ALLOWED_USERS) or (user_id in ALLOWED_USERS)


def is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    username = (user.username or "").lower().lstrip("@")
    return username in ADMIN_USERS


def get_score_badge(score: float | int) -> str:
    """Returns colored status emoji badge for a nutrition score out of 100."""
    if score >= 80:
        return "🟢"
    elif score >= 55:
        return "🟡"
    return "🔴"


def calculate_weighted_nutrition_score(items: List[FoodItem]) -> float:
    """Calculates calorie-weighted nutrition score, falling back to simple average if calories sum to 0."""
    if not items:
        return 0.0
    total_cal = sum(item.calories for item in items)
    if total_cal > 0:
        return sum(item.calories * item.nutrition_score for item in items) / total_cal
    return sum(item.nutrition_score for item in items) / len(items)


def format_telegram_reply(
    user_name: str,
    datetime_str: str,
    items: List[FoodItem],
    today_totals: dict,
    motivational_note: str,
    sheet_saved: bool = True,
    target_date: Optional[date] = None,
) -> str:
    today = datetime.now(LOCAL_TZ).date()
    if target_date is not None:
        is_today = (target_date == today)
        is_yesterday = (target_date == today - timedelta(days=1))
        if is_today:
            header_title = "🍽️ <b>Meal Logged Successfully!</b>"
            totals_header = "──────── <b>Today's Cumulative Totals</b> ────────"
            score_label = "Today's Nutrition & Cut Score:"
            items_count_label = "Items logged today:"
        elif is_yesterday:
            header_title = f"🍽️ <b>Meal Logged for Yesterday ({target_date.strftime('%a, %d %b')})!</b>"
            totals_header = f"──────── <b>Yesterday's Cumulative Totals ({target_date.strftime('%a, %d %b')})</b> ────────"
            score_label = "Yesterday's Nutrition & Cut Score:"
            items_count_label = "Items logged for yesterday:"
        else:
            date_fmt = target_date.strftime("%a, %d %b %Y")
            header_title = f"🍽️ <b>Meal Logged for {date_fmt}!</b>"
            totals_header = f"──────── <b>Cumulative Totals for {date_fmt}</b> ────────"
            score_label = f"Nutrition & Cut Score ({date_fmt}):"
            items_count_label = f"Items logged on {date_fmt}:"
    else:
        header_title = "🍽️ <b>Meal Logged Successfully!</b>"
        totals_header = "──────── <b>Today's Cumulative Totals</b> ────────"
        score_label = "Today's Nutrition & Cut Score:"
        items_count_label = "Items logged today:"

    lines = [
        header_title,
        f"👤 <b>Logged by:</b> {html.escape(user_name)}",
        f"🕒 <b>Time:</b> <code>{html.escape(datetime_str)}</code>",
        "",
        "──────── <b>Logged Items</b> ────────",
    ]

    meal_cal = sum(item.calories for item in items)
    meal_pro = sum(item.protein for item in items)
    meal_carb = sum(item.carbohydrates for item in items)
    meal_fat = sum(item.fat for item in items)
    meal_fib = sum(item.fiber for item in items)
    meal_score = round(calculate_weighted_nutrition_score(items))
    meal_score_badge = get_score_badge(meal_score)

    for i, item in enumerate(items, 1):
        score_badge = get_score_badge(item.nutrition_score)

        lines.extend([
            f"\n<b>{i}. {html.escape(item.item_name)}</b>",
            f"  🏷️ <i>Category:</i> {html.escape(item.category)}",
            f"  📝 <i>Details:</i> {html.escape(item.short_description)}",
            f"  🔥 <i>Energy:</i> <b>{item.calories:.0f} kcal</b>",
            f"  🥩 <i>Protein:</i> <b>{item.protein:.1f}g</b>  |  🍞 <i>Carbs:</i> <b>{item.carbohydrates:.1f}g</b>  |  🥑 <i>Fat:</i> <b>{item.fat:.1f}g</b>  |  🥗 <i>Fiber:</i> <b>{item.fiber:.1f}g</b>",
            f"  {score_badge} <i>Nutrition & Cut Score:</i> <b>{item.nutrition_score}/100</b>",
        ])

    if len(items) > 1:
        lines.extend([
            "",
            "<b>🥣 Meal Subtotal:</b>",
            f"🔥 <b>{meal_cal:.0f} kcal</b>  |  🥩 <b>{meal_pro:.1f}g P</b>  |  🍞 <b>{meal_carb:.1f}g C</b>  |  🥑 <b>{meal_fat:.1f}g F</b>  |  🥗 <b>{meal_fib:.1f}g Fiber</b>",
            f"{meal_score_badge} <i>Meal Nutrition & Cut Score:</i> <b>{meal_score}/100</b>",
        ])

    lines.extend([
        "",
        totals_header,
        f"🔥 <b>Total Calories:</b> <b>{today_totals['calories']:.0f} kcal</b>",
        f"🥩 <b>Protein:</b> <b>{today_totals['protein']:.1f} g</b>",
        f"🍞 <b>Carbohydrates:</b> <b>{today_totals['carbs']:.1f} g</b>",
        f"🥑 <b>Fat:</b> <b>{today_totals['fat']:.1f} g</b>",
        f"🥗 <b>Fiber:</b> <b>{today_totals.get('fiber', 0.0):.1f} g</b>",
    ])

    day_score = today_totals.get("nutrition_score")
    if day_score is not None:
        day_score_badge = get_score_badge(day_score)
        lines.append(f"{day_score_badge} <i>{score_label}</i> <b>{round(day_score)}/100</b>")

    lines.extend([
        f"📊 <i>{items_count_label}</i> {today_totals['item_count']}",
        "",
        "──────── <b>Coach's Note</b> ────────",
        f"💪 <i>{html.escape(motivational_note)}</i>",
    ])

    return "\n".join(lines)


# =====================================================================
# Telegram Command Handlers
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command: Clean, welcoming onboarding."""
    user_name = get_user_display_name(update)
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "" if update.effective_user else ""

    await firestore_service.register_or_update_user(chat_id, user_name, first_name)

    welcome_text = (
        "👋 <b>Welcome to your Macro Tracker!</b>\n\n"
        "Just send me a photo of your meal or type what you ate "
        "(e.g. <i>\"2 boiled eggs with black coffee\"</i>), and I'll track your calories and macros. 🥗\n\n"
        "📸 <i>Snap a photo or type below to log your first meal!</i>"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /help command."""
    help_text = (
        "🥗 <b>James Boh Macro Tracker — Help Guide</b>\n\n"
        "📖 <b>How to Use (Zero Commands Needed):</b>\n\n"
        "• <b>Photo & Album Logging:</b> Send single photos or multi-photo albums.\n"
        "  💡 <i>Tip: Captions serve as ground truth (e.g. '5 items from Ajumma, half of each').</i>\n\n"
        "• <b>Text Logging:</b> Type naturally:\n"
        "  - <code>Chicken breast 200g with broccoli and 1 cup brown rice</code>\n"
        "  - <code>Flat white with oat milk</code>\n"
        "  - <code>/log 2 hard boiled eggs</code>\n\n"
        "• <b>Smart Editing, Quote-Reply & Categories:</b>\n"
        "  - Swipe/reply to any past meal message: <code>this entry is for lunch</code>\n"
        "  - Or type: <code>Actually no sugar in the tea</code>\n"
        "  - <code>/edit change chicken to 250g</code>\n"
        "  - <code>/undo</code> - Instantly delete your last logged meal\n\n"
        "• <b>Daily Targets & Goals:</b>\n"
        "  - <code>/targets</code> or <code>/goals</code> - Set goals with 1-tap presets (Fat Loss, Maintenance, Bulk, Custom)\n"
        "  - Or type: <code>/targets 1800 140 25</code> (Calories, Protein, Fiber)\n"
        "  - Or ask: <i>'change targets'</i>, <i>'set goals'</i>\n\n"
        "• <b>Analytics & Visual Trends:</b>\n"
        "  - Ask: <i>'How did I do this week?'</i> or type <code>/analytics</code>\n"
        "  - <code>/weekly</code> - 7-day dark-mode chart card\n"
        "  - <code>/monthly</code> - 30-day trend digest\n\n"
        "• <b>Data Governance & Portability:</b>\n"
        "  - Ask: <i>'Can I export my data?'</i> or type <code>/export</code> (CSV download)\n"
        "  - <code>/privacy</code> - View data residency & privacy statement\n"
        "  - <code>/delete</code> - Permanently erase all your data (Right to Erasure)\n\n"
        "• <b>Feedback & Suggestions:</b>\n"
        "  - <code>/feedback &lt;your idea&gt;</code> - Share suggestions, feature requests, or report bugs\n"
        "  - Or ask: <i>'I have a suggestion'</i>, <i>'report a bug'</i>\n\n"
        "💼 <i>Connect: <a href=\"https://www.linkedin.com/in/jamesboh/\">James Boh on LinkedIn</a></i>"
    )
    if is_admin(update):
        help_text += (
            "\n\n🛠️ <b>Admin Commands:</b>\n"
            "• <code>/metrics</code> - View platform metrics, DAU/WAU/MAU & log volume\n"
            "• <code>/viewfeedback</code> - Review recent user feedback submissions\n"
            "• <code>/release</code> - Broadcast release notes\n"
            "• <code>/broadcast &lt;msg&gt;</code> - Broadcast custom message"
        )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Send Feedback", callback_data="prompt_feedback")]
    ])
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=keyboard)


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /changelog command."""
    changelog_text = (
        "📋 <b>Product Changelog — James Boh Macro Tracker</b>\n\n"
        "<b>v1.3.5 (Current):</b>\n"
        "• 🎯 <b>Target Management:</b> Set custom goals with 1-tap presets (/targets, /goals).\n"
        "• ⚖️ <b>Baseline Mode:</b> Clean baseline comparisons when targets are unconfigured.\n"
        "• 💬 <b>User Feedback System:</b> Submit ideas or bug reports (/feedback, /suggest) with real-time admin alerts.\n"
        "• 📈 <b>Admin Metrics Dashboard:</b> Live user growth, DAU/WAU/MAU & log volume (/metrics).\n"
        "• 📊 <b>Donut Chart Centering:</b> Perfect text alignment & high-contrast macro labels.\n\n"
        "<b>v1.3.4:</b>\n"
        "• 📸 <b>Multi-Photo Albums:</b> Evaluates multi-dish spreads in one go with zero duplicate entries.\n"
        "• 💬 <b>Quote-Reply Editing:</b> Swipe/reply to any past meal to update ingredients, portions, or categories.\n"
        "• 🏷️ <b>Category Overrides:</b> Change meal categories conversationally (e.g. <i>'this entry is for lunch'</i>).\n\n"
        "<b>v1.3.3:</b>\n"
        "• ✨ <b>Clean Onboarding:</b> Ultra-simple, friendly welcome message (/start).\n"
        "• 💬 <b>Natural Daily Totals:</b> Ask naturally (e.g. <i>'what are my calories today?'</i>) without slash commands.\n"
        "• 🛡️ <b>Atomic Rollback:</b> Guarantees no phantom entries if message delivery fails.\n\n"
        "<b>v1.3.0:</b>\n"
        "• 🔒 <b>Cloud Firestore Migration:</b> Multi-tenant database with strict private data isolation.\n"
        "• 📊 <b>Visual Analytics:</b> Dark-mode chart cards & weekly trends (/analytics, /weekly, /monthly).\n"
        "• ✏️ <b>Smart Meal Editing:</b> Correct entries naturally or with /edit & /undo.\n"
        "• 📥 <b>Data Portability:</b> Download your full history as a CSV file (/export).\n"
        "• 🛡️ <b>Data Governance:</b> Singapore PDPA compliance & self-service data erasure (/delete).\n\n"
        "💼 <i>Connect: <a href=\"https://www.linkedin.com/in/jamesboh/\">James Boh on LinkedIn</a></i>"
    )
    await update.message.reply_text(changelog_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /today command to display today's logged totals."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)
    now = datetime.now(LOCAL_TZ)
    today_prefix = now.strftime("%Y-%m-%d")

    status_msg = await update.message.reply_text("📊 <i>Fetching your daily totals...</i>", parse_mode=ParseMode.HTML)
    try:
        totals = await firestore_service.get_user_today_totals(chat_id, today_prefix)
        profile = await firestore_service.get_user_profile(chat_id)
        cal_target = profile.get("daily_calorie_target", 2000)
        pro_target = profile.get("daily_protein_target", 150)
        fib_target = profile.get("daily_fiber_target", 25)
        targets_set = profile.get("targets_set", False)

        cal_display = f"<b>{totals['calories']:.0f} kcal</b> / {cal_target:,.0f}" if targets_set else f"<b>{totals['calories']:.0f} kcal</b>"
        pro_display = f"<b>{totals['protein']:.1f} g</b> / {pro_target:,.0f}g" if targets_set else f"<b>{totals['protein']:.1f} g</b>"
        fib_display = f"<b>{totals['fiber']:.1f} g</b> / {fib_target:,.0f}g" if targets_set else f"<b>{totals['fiber']:.1f} g</b>"

        score_line = ""
        day_score = totals.get("nutrition_score")
        if day_score is not None:
            day_badge = get_score_badge(day_score)
            score_line = f"\n{day_badge} <i>Today's Nutrition & Cut Score:</i> <b>{round(day_score)}/100</b>\n"

        summary_text = (
            f"📊 <b>Today's Cumulative Macros ({today_prefix})</b>\n"
            f"👤 <b>User:</b> {html.escape(user_name)}\n\n"
            f"🔥 <b>Total Calories:</b> {cal_display}\n"
            f"🥩 <b>Protein:</b> {pro_display}\n"
            f"🍞 <b>Carbohydrates:</b> <b>{totals['carbs']:.1f} g</b>\n"
            f"🥑 <b>Fat:</b> <b>{totals['fat']:.1f} g</b>\n"
            f"🥗 <b>Dietary Fiber:</b> {fib_display}"
            f"{score_line}\n"
            f"📝 <i>Meals logged today:</i> {totals['meal_count']} ({totals['item_count']} items)\n\n"
            "💡 <i>Tip: Type /analytics to see your 7-day trend chart!</i>"
        )
        await status_msg.edit_text(summary_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error fetching today totals: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ <b>Error fetching totals:</b> {html.escape(str(e))}", parse_mode=ParseMode.HTML)


async def send_analytics_report(
    chat_id: int,
    user_name: str,
    context: ContextTypes.DEFAULT_TYPE,
    days_window: int = 7,
    reply_to_message_id: Optional[int] = None,
    generate_image: bool = False,
) -> None:
    """Delivers fast text-first analytics digest with on-demand high-res chart buttons."""
    now = datetime.now(LOCAL_TZ).date()
    start_date = (now - timedelta(days=days_window - 1)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    records = await firestore_service.get_user_date_range_records(chat_id, start_date, end_date)
    profile = await firestore_service.get_user_profile(chat_id)
    cal_target = profile.get("daily_calorie_target", 2000)
    pro_target = profile.get("daily_protein_target", 150)
    fib_target = profile.get("daily_fiber_target", 25)
    targets_set = profile.get("targets_set", False)

    text_summary = AnalyticsService.format_analytics_text(
        daily_records=records,
        user_name=user_name,
        days_window=days_window,
        targets=profile,
    )

    btn_target_label = "🎯 Edit Targets" if targets_set else "🎯 Set Daily Targets"
    btn_other_window = "🗓️ 30 Days" if days_window == 7 else "📊 7 Days"
    btn_other_callback = "analytics_30d" if days_window == 7 else "analytics_7d"

    has_data = any(r.get("calories", 0) > 0 for r in records)
    keyboard_buttons = []
    if has_data:
        keyboard_buttons.append([
            InlineKeyboardButton("🖼️ View High-Res Chart", callback_data=f"chart_{days_window}d"),
        ])
    keyboard_buttons.append([
        InlineKeyboardButton(btn_other_window, callback_data=btn_other_callback),
        InlineKeyboardButton(btn_target_label, callback_data="menu_targets"),
    ])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)

    if generate_image and has_data:
        chart_bytes = await asyncio.to_thread(
            AnalyticsService.generate_trend_chart,
            daily_records=records,
            calorie_target=cal_target,
            protein_target=pro_target,
            fiber_target=fib_target,
            days_window=days_window,
            targets_set=targets_set,
        )
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=chart_bytes,
            caption=text_summary,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            reply_to_message_id=reply_to_message_id,
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_summary,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            reply_to_message_id=reply_to_message_id,
        )


async def send_analytics_chart(
    chat_id: int,
    user_name: str,
    context: ContextTypes.DEFAULT_TYPE,
    days_window: int = 7,
    reply_to_message_id: Optional[int] = None,
) -> None:
    """Generates and delivers dark-mode 3-panel Matplotlib chart photo on-demand."""
    now = datetime.now(LOCAL_TZ).date()
    start_date = (now - timedelta(days=days_window - 1)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    records = await firestore_service.get_user_date_range_records(chat_id, start_date, end_date)
    profile = await firestore_service.get_user_profile(chat_id)
    cal_target = profile.get("daily_calorie_target", 2000)
    pro_target = profile.get("daily_protein_target", 150)
    fib_target = profile.get("daily_fiber_target", 25)
    targets_set = profile.get("targets_set", False)

    has_data = any(r.get("calories", 0) > 0 for r in records)
    if not has_data:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"ℹ️ <b>No meal data found in the past {days_window} days to chart.</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to_message_id,
        )
        return

    chart_bytes = await asyncio.to_thread(
        AnalyticsService.generate_trend_chart,
        daily_records=records,
        calorie_target=cal_target,
        protein_target=pro_target,
        fiber_target=fib_target,
        days_window=days_window,
        targets_set=targets_set,
    )
    caption = f"📈 <b>{days_window}-Day High-Resolution Trend Chart</b>\n👤 <b>User:</b> {html.escape(user_name)}"
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=chart_bytes,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_to_message_id=reply_to_message_id,
    )


async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /analytics command (defaults to 7-day instant text digest)."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    user_name = get_user_display_name(update)
    chat_id = update.effective_chat.id
    try:
        await send_analytics_report(chat_id, user_name, context, days_window=7)
    except Exception as e:
        logger.error(f"Analytics generation failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to generate analytics: {html.escape(str(e))}")


async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /weekly shortcut (7-day instant text digest)."""
    await analytics_command(update, context)


async def monthly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /monthly shortcut (30-day instant text digest)."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    user_name = get_user_display_name(update)
    chat_id = update.effective_chat.id
    try:
        await send_analytics_report(chat_id, user_name, context, days_window=30)
    except Exception as e:
        logger.error(f"Monthly analytics failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to generate analytics: {html.escape(str(e))}")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /export: Generates and delivers full CSV export."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)
    status_msg = await update.message.reply_text("📦 <i>Preparing your CSV export...</i>", parse_mode=ParseMode.HTML)

    try:
        csv_content = await firestore_service.export_user_meals_csv(chat_id, user_name)
        if not csv_content or len(csv_content.strip().splitlines()) <= 1:
            await status_msg.edit_text(
                "ℹ️ <b>No meal records found to export.</b>\nLog your first meal by sending a photo or typing what you ate!",
                parse_mode=ParseMode.HTML,
            )
            return

        csv_bytes = csv_content.encode("utf-8")
        filename = f"macro_history_{datetime.now(LOCAL_TZ).strftime('%Y%m%d')}.csv"

        await update.message.reply_document(
            document=csv_bytes,
            filename=filename,
            caption=(
                "📥 <b>Here is your exported meal history!</b>\n\n"
                f"• Total Rows: {len(csv_content.splitlines()) - 1}\n"
                "• Compatible with Microsoft Excel, Apple Numbers & Google Sheets."
            ),
            parse_mode=ParseMode.HTML,
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Export failed: {html.escape(str(e))}")


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /privacy: Explains Singapore PDPA compliance, residency, and AI data usage."""
    privacy_text = (
        "🔒 <b>Privacy Policy & Data Governance</b>\n\n"
        "<b>1. Data Residency & Infrastructure:</b>\n"
        "• Your meal logs are securely stored in <b>Google Cloud Firestore</b> in the <b>Singapore region (`asia-southeast1`)</b>.\n"
        "• All data is encrypted at rest (AES-256) and in transit (TLS 1.3 / HTTPS).\n\n"
        "<b>2. Multi-Tenancy & Data Isolation:</b>\n"
        "• Each user's data is strictly partitioned under your unique Telegram ID (`users/{chat_id}`).\n"
        "• No other user can view or query your meal history.\n\n"
        "<b>3. Artificial Intelligence (Gemini API):</b>\n"
        "• Food photos and text descriptions are processed ephemerally in memory by Google Gemini API.\n"
        "• Under enterprise API terms, customer inputs and meal photos are <b>NOT used to train Google foundation models</b>.\n"
        "• Raw meal photos are not retained permanently on our servers—only the calculated nutritional macros are saved to your account.\n\n"
        "<b>4. Your Rights (Singapore PDPA):</b>\n"
        "• <b>Data Portability:</b> Type /export anytime to download a CSV file of all your data.\n"
        "• <b>Right to Erasure:</b> Type /delete anytime to permanently wipe your account and all meal records.\n\n"
        "<i>Questions? Reach out to creator James Boh via /start links.</i>"
    )
    await update.message.reply_text(privacy_text, parse_mode=ParseMode.HTML)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /delete: Prompts confirmation for permanent account wipe."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚠️ Yes, Delete All My Data", callback_data="confirm_delete"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete"),
        ]
    ])
    await update.message.reply_text(
        "⚠️ <b>Delete Account & Meal Data</b>\n\n"
        "Are you sure you want to permanently erase your profile and all logged meals?\n"
        "This action is irreversible.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /undo: Rollback of the most recent meal."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    last_meal = await firestore_service.get_last_meal(chat_id)
    if not last_meal:
        await update.message.reply_text("ℹ️ No recent meal found to undo.")
        return

    await firestore_service.delete_meal(chat_id, last_meal["id"])
    now = datetime.now(LOCAL_TZ)
    today_prefix = now.strftime("%Y-%m-%d")
    updated_totals = await firestore_service.get_user_today_totals(chat_id, today_prefix)

    items_str = ", ".join(i.get("item_name", "Item") for i in last_meal.get("items", [])) or "Meal"
    reply_text = (
        "🗑️ <b>Meal Undone Successfully!</b>\n\n"
        f"Removed: <i>{html.escape(items_str)}</i> (-{last_meal.get('total_calories', 0):.0f} kcal)\n\n"
        f"📊 <b>Updated Today's Totals:</b>\n"
        f"🔥 Calories: <b>{updated_totals['calories']:.0f} kcal</b>\n"
        f"🥩 Protein: <b>{updated_totals['protein']:.1f}g</b> | 🍞 Carbs: <b>{updated_totals['carbs']:.1f}g</b> | 🥑 Fat: <b>{updated_totals['fat']:.1f}g</b> | 🥗 Fiber: <b>{updated_totals['fiber']:.1f}g</b>"
    )
    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def execute_meal_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_name: str,
    edit_instructions: str,
    target_meal: Optional[dict] = None,
) -> None:
    """Helper to modify a logged meal (either target_meal or most recent) with Gemini."""
    if target_meal is None:
        target_meal = await firestore_service.get_last_meal(chat_id)
    if not target_meal:
        await update.message.reply_text("ℹ️ You have no logged meals to edit yet. Send a photo or text to log your first meal!")
        return

    status_msg = await update.message.reply_text("✏️ <i>Recalculating meal with Gemini AI...</i>", parse_mode=ParseMode.HTML)
    try:
        updated_analysis = await edit_meal_with_gemini(target_meal, edit_instructions)
        if not updated_analysis.items:
            await firestore_service.delete_meal(chat_id, target_meal["id"])
            await status_msg.edit_text("🗑️ Meal was cleared based on your instruction.", parse_mode=ParseMode.HTML)
            return

        now = datetime.now(LOCAL_TZ)
        meal_date_str = target_meal.get("date") or now.strftime("%Y-%m-%d")

        updated_meal_data = {
            "category": updated_analysis.items[0].category if updated_analysis.items else target_meal.get("category", "Meal"),
            "total_calories": round(sum(i.calories for i in updated_analysis.items), 1),
            "total_protein": round(sum(i.protein for i in updated_analysis.items), 1),
            "total_carbs": round(sum(i.carbohydrates for i in updated_analysis.items), 1),
            "total_fat": round(sum(i.fat for i in updated_analysis.items), 1),
            "total_fiber": round(sum(i.fiber for i in updated_analysis.items), 1),
            "nutrition_score": round(calculate_weighted_nutrition_score(updated_analysis.items)),
            "motivational_note": updated_analysis.motivational_note,
            "items": [i.model_dump() for i in updated_analysis.items],
        }

        date_override = extract_date_override(edit_instructions, reference_date=now.date())
        if date_override:
            meal_date_str = date_override.strftime("%Y-%m-%d")
            updated_meal_data["date"] = meal_date_str
            old_time = target_meal.get("time") or now.strftime("%H:%M:%S")
            updated_meal_data["timestamp"] = f"{meal_date_str} {old_time}"

        await firestore_service.update_meal(chat_id, target_meal["id"], updated_meal_data)
        today_totals = await firestore_service.get_user_today_totals(chat_id, meal_date_str)

        target_date_obj = date_override if date_override else (datetime.strptime(meal_date_str, "%Y-%m-%d").date() if meal_date_str else None)

        reply_html = format_telegram_reply(
            user_name=user_name,
            datetime_str=updated_meal_data.get("timestamp") or target_meal.get("timestamp") or str(now),
            items=updated_analysis.items,
            today_totals=today_totals,
            motivational_note=updated_analysis.motivational_note,
            sheet_saved=True,
            target_date=target_date_obj,
        )
        if date_override:
            reply_html = f"📅 <b>Meal Date Moved to {date_override.strftime('%a, %d %b %Y')}!</b>\n\n" + reply_html
        else:
            reply_html = f"✏️ <b>Meal Updated Successfully!</b>\n\n" + reply_html
        try:
            await status_msg.edit_text(reply_html, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Could not edit status message ({e}), sending new reply message instead.")
            await update.message.reply_text(reply_html, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Meal edit failed: {e}", exc_info=True)
        err_msg = str(e)
        if "503" in err_msg or "UNAVAILABLE" in err_msg or "high demand" in err_msg:
            user_friendly_err = (
                "⚠️ <b>AI Service High Demand</b>\n\n"
                "Google's Gemini servers are experiencing a brief spike in traffic. "
                "Please wait a few seconds and try editing again."
            )
        else:
            user_friendly_err = f"❌ Could not update meal: {html.escape(str(e))}"
        await status_msg.edit_text(user_friendly_err, parse_mode=ParseMode.HTML)


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /edit: Interactive or one-line (/edit <notes>)."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    raw_text = update.message.text or ""
    _, _, instructions = raw_text.partition(" ")
    instructions = instructions.strip()

    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)

    if instructions:
        await execute_meal_edit(update, context, chat_id, user_name, instructions)
    else:
        last_meal = await firestore_service.get_last_meal(chat_id)
        if not last_meal:
            await update.message.reply_text("ℹ️ You have no recent meals to edit. Log a meal first!")
            return

        items_str = ", ".join(i.get("item_name", "") for i in last_meal.get("items", []))
        context.user_data["awaiting_edit"] = True
        await update.message.reply_text(
            f"✏️ <b>Edit Most Recent Meal:</b>\n"
            f"<i>Current items: {html.escape(items_str)} ({last_meal.get('total_calories', 0):.0f} kcal)</i>\n\n"
            "Reply with your changes, for example:\n"
            "• <i>'No sugar in the iced tea'</i>\n"
            "• <i>'Change chicken to 250g'</i>\n"
            "• <i>'Ate half the rice'</i>\n"
            "• <i>'Remove the soup'</i>",
            parse_mode=ParseMode.HTML,
        )


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles explicit /log <food> command."""
    raw_text = update.message.text or ""
    _, _, food_text = raw_text.partition(" ")
    food_text = food_text.strip()
    if not food_text:
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/log &lt;what you ate&gt;</code>\n<i>Example: /log 2 eggs with avocado toast</i>", parse_mode=ParseMode.HTML)
        return
    await process_and_log_meal(update=update, text_prompt=food_text, image_bytes=None)


async def targets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /targets: view or customize daily calorie and macro goals."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    raw_text = update.message.text or "" if update.message else ""
    _, _, args_str = raw_text.partition(" ")
    args = [a for a in args_str.split() if a]

    profile = await firestore_service.get_user_profile(chat_id)
    cal = profile.get("daily_calorie_target", 2000)
    pro = profile.get("daily_protein_target", 150)
    fib = profile.get("daily_fiber_target", 25)
    is_custom = profile.get("targets_set", False)

    # If user provided arguments: /targets 1800 140 25
    if args:
        try:
            new_cal = float(args[0])
            new_pro = float(args[1]) if len(args) > 1 else pro
            new_fib = float(args[2]) if len(args) > 2 else fib
            await firestore_service.update_user_targets(
                chat_id, calorie_target=new_cal, protein_target=new_pro, fiber_target=new_fib
            )
            msg = (
                "✅ <b>Daily Targets Updated!</b>\n\n"
                f"🔥 <b>Calories:</b> {new_cal:,.0f} kcal\n"
                f"🥩 <b>Protein:</b> {new_pro:.1f} g\n"
                f"🥗 <b>Fiber:</b> {new_fib:.1f} g\n\n"
                "Your trend charts and coaching analysis will now calibrate to these goals! 🎯"
            )
            if update.message:
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            elif update.callback_query:
                await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
            return
        except ValueError:
            pass

    status_badge = "✅ <b>Customized</b>" if is_custom else "⚠️ <b>Default Baseline (Unset)</b>"
    text = (
        f"🎯 <b>Your Daily Nutrition Targets</b>\n\n"
        f"Status: {status_badge}\n"
        f"🔥 <b>Calories:</b> {cal:,.0f} kcal\n"
        f"🥩 <b>Protein:</b> {pro:.1f} g\n"
        f"🥗 <b>Dietary Fiber:</b> {fib:.1f} g\n\n"
        "Choose a goal preset below or reply with your custom numbers:\n"
        "• <code>/targets 1800 140 25</code> <i>(&lt;calories&gt; &lt;protein&gt; &lt;fiber&gt;)</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📉 Fat Loss (1,700 kcal)", callback_data="preset_cut"),
            InlineKeyboardButton("⚖️ Maintenance (2,000 kcal)", callback_data="preset_maintain"),
        ],
        [
            InlineKeyboardButton("💪 Lean Bulk (2,400 kcal)", callback_data="preset_bulk"),
            InlineKeyboardButton("✏️ Custom Targets", callback_data="prompt_custom_targets"),
        ]
    ])

    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def release_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to broadcast latest release notes."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Unauthorized. Only the admin can broadcast release announcements.")
        return

    subscribers = await firestore_service.get_all_subscriber_chat_ids()
    current_chat_id = update.effective_chat.id
    if current_chat_id not in subscribers:
        subscribers.append(current_chat_id)

    release_announcement = (
        "🚀 <b>James Boh Macro Tracker — New Release!</b>\n"
        "<i>Version 1.3.0 is now live</i>\n\n"
        "<b>What's New:</b>\n"
        "• 🔒 <b>Cloud Firestore:</b> Multi-tenant database ensuring 100% private data isolation.\n"
        "• 📊 <b>Visual Analytics:</b> Dark-mode chart cards & weekly trends (/analytics, /weekly, /monthly).\n"
        "• ✏️ <b>Smart Meal Editing:</b> Correct meals naturally (e.g. <i>'actually no sugar'</i>) or type /edit & /undo.\n"
        "• 📥 <b>Data Portability:</b> Download your full meal history as a CSV file anytime (/export).\n"
        "• 🛡️ <b>Privacy & Governance:</b> Singapore data residency & self-service erasure (/delete).\n\n"
        "<i>Happy tracking! Snap a meal photo or type what you ate to try it out.</i>"
    )

    status_msg = await update.message.reply_text(f"📢 Broadcasting release to {len(subscribers)} subscriber(s)...")
    sent = 0
    for cid in subscribers:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=release_announcement,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send release broadcast to {cid}: {e}")

    await status_msg.edit_text(f"✅ <b>Release Broadcast Complete!</b>\nDelivered to {sent}/{len(subscribers)} subscriber(s).", parse_mode=ParseMode.HTML)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to broadcast a custom message: /broadcast <message>"""
    if not is_admin(update):
        await update.message.reply_text("⛔ Unauthorized. Only the admin can broadcast messages.")
        return

    raw_text = update.message.text or ""
    _, _, message_to_send = raw_text.partition(" ")
    message_to_send = message_to_send.strip()

    if not message_to_send:
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/broadcast &lt;your message&gt;</code>", parse_mode=ParseMode.HTML)
        return

    subscribers = await firestore_service.get_all_subscriber_chat_ids()
    current_chat_id = update.effective_chat.id
    if current_chat_id not in subscribers:
        subscribers.append(current_chat_id)

    formatted_broadcast = (
        "📢 <b>Announcement from James</b>\n\n"
        f"{html.escape(message_to_send)}\n\n"
        "<i>— James Boh Macro Tracker</i>"
    )

    status_msg = await update.message.reply_text(f"📢 Broadcasting message to {len(subscribers)} subscriber(s)...")
    sent = 0
    for cid in subscribers:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=formatted_broadcast,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send broadcast to {cid}: {e}")

    await status_msg.edit_text(f"✅ <b>Broadcast Complete!</b>\nDelivered to {sent}/{len(subscribers)} subscriber(s).", parse_mode=ParseMode.HTML)


async def notify_admins_of_feedback(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_name: str,
    feedback_text: str,
    category: str,
) -> None:
    """Dispatches a real-time notification to all authorized administrators."""
    admin_cids = await firestore_service.get_admin_chat_ids()
    now_str = datetime.now(LOCAL_TZ).strftime("%d %b %Y, %H:%M SGT")

    category_badge = {
        "bug_report": "🐛 Bug Report",
        "feature_request": "💡 Feature Request",
        "general": "💬 General Feedback",
    }.get(category, "💬 Feedback")

    admin_msg = (
        "📬 <b>New User Feedback Received!</b>\n\n"
        f"👤 <b>From:</b> {html.escape(user_name)} (<code>{chat_id}</code>)\n"
        f"📅 <b>Time:</b> {now_str}\n"
        f"🏷️ <b>Category:</b> {category_badge}\n\n"
        f"💬 <b>Message:</b>\n"
        f"<i>\"{html.escape(feedback_text)}\"</i>"
    )

    for admin_id in admin_cids:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(f"Could not dispatch admin feedback notification to {admin_id}: {e}")


async def submit_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    feedback_text: str,
    category: str = "general",
) -> None:
    """Saves feedback to Firestore, notifies admins, and sends confirmation to the user."""
    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)
    clean_text = feedback_text.strip()
    if not clean_text:
        context.user_data["awaiting_feedback"] = True
        if update.message:
            await update.message.reply_text("⚠️ Feedback message cannot be empty. Please type your message below.")
        return

    # Auto-detect category from contents
    lower_f = clean_text.lower()
    if any(k in lower_f for k in ["bug", "error", "broken", "issue", "crash", "wrong", "fail"]):
        category = "bug_report"
    elif any(k in lower_f for k in ["feature", "add ", "would be great", "can you", "request", "support for", "integrate"]):
        category = "feature_request"

    await firestore_service.save_feedback(
        chat_id=chat_id,
        user_name=user_name,
        feedback_text=clean_text,
        category=category,
    )

    await notify_admins_of_feedback(context, chat_id, user_name, clean_text, category)

    category_label = {
        "bug_report": "🐛 Bug Report",
        "feature_request": "💡 Feature Request",
        "general": "💬 Feedback",
    }.get(category, "💬 Feedback")

    confirm_msg = (
        f"🙏 <b>Thank You for Your Feedback!</b>\n\n"
        f"Your {category_label.lower()} has been delivered directly to the developer:\n"
        f"<i>\"{html.escape(clean_text)}\"</i>\n\n"
        "We review every message to make James Boh Macro Tracker even better! 🚀"
    )
    if update.message:
        await update.message.reply_text(confirm_msg, parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await update.callback_query.message.reply_text(confirm_msg, parse_mode=ParseMode.HTML)


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /feedback and /suggest commands."""
    if not is_user_authorized(update):
        if update.message:
            await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    raw_text = (update.message.text or "") if update.message else ""
    _, _, message_to_send = raw_text.partition(" ")
    message_to_send = message_to_send.strip()

    if message_to_send:
        await submit_feedback(update, context, message_to_send)
    else:
        context.user_data["awaiting_feedback"] = True
        prompt_text = (
            "💬 <b>We'd Love Your Feedback!</b>\n\n"
            "Have an idea for a new feature, found an issue, or want to share your experience with the tracker?\n\n"
            "👉 <i>Simply reply to this message or type your feedback below:</i>"
        )
        if update.message:
            await update.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)
        elif update.callback_query:
            await update.callback_query.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)


async def viewfeedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only command to view recent user feedbacks: /viewfeedback or /feedbacks"""
    if not is_admin(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized. Only the admin can review feedback.")
        return

    feedbacks = await firestore_service.get_recent_feedback(limit=10)
    if not feedbacks:
        if update.message:
            await update.message.reply_text("📭 No user feedback submissions found yet.")
        return

    lines = ["📋 <b>Recent User Feedback Submissions (Last 10):</b>\n"]
    for i, fb in enumerate(feedbacks, start=1):
        uname = fb.get("user_name", "Unknown")
        cid = fb.get("chat_id", "")
        text = fb.get("feedback_text", "")
        cat = fb.get("category", "general")
        created = fb.get("created_at", "")
        time_display = created[:16].replace("T", " ") if created else ""
        lines.append(
            f"<b>{i}. {html.escape(uname)}</b> (<code>{cid}</code>) — <i>{time_display} SGT</i> [{cat}]\n"
            f"   <i>\"{html.escape(text)}\"</i>\n"
        )

    if update.message:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only command to view platform metrics, active users, and log volume."""
    if not is_admin(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized. Only the bot administrator can view metrics.")
        return

    status_msg = None
    if update.message:
        status_msg = await update.message.reply_text("⏳ <i>Aggregating platform metrics from Firestore...</i>", parse_mode=ParseMode.HTML)

    try:
        m = await firestore_service.get_platform_metrics()

        user_lines = []
        for i, u in enumerate(m["user_breakdown"][:10], start=1):
            la = u["last_active"]
            la_display = la[:16].replace("T", " ") if la and la != "Never" else "Never"
            user_lines.append(f"<b>{i}. {html.escape(u['user_name'])}</b>: {u['meals_count']} meals <i>(Active: {la_display})</i>")

        leaderboard_text = "\n".join(user_lines) if user_lines else "<i>No users yet</i>"

        report = (
            "📊 <b>James Boh Macro Tracker — Platform Metrics</b>\n"
            f"📅 <i>As of {m['generated_at']}</i>\n\n"
            "👥 <b>User Growth & Community:</b>\n"
            f"• Total Registered Users: <b>{m['total_users']}</b>\n"
            f"• Activated Users (≥1 meal): <b>{m['activated_users']} ({m['activation_rate']}%)</b>\n"
            f"• New Signups Today: <b>+{m['new_users_today']}</b> | This Week: <b>+{m['new_users_7d']}</b>\n\n"
            "⚡ <b>Active Users & Retention:</b>\n"
            f"• DAU (Active 24h / Today): <b>{m['dau']}</b>\n"
            f"• WAU (Active 7 Days): <b>{m['wau']}</b>\n"
            f"• MAU (Active 30 Days): <b>{m['mau']}</b>\n"
            f"• Habit Stickiness (DAU/MAU): <b>{m['stickiness']}%</b>\n\n"
            "🍽️ <b>Log Volume & Velocity:</b>\n"
            f"• Total Meals Logged: <b>{m['total_meals']}</b> ({m['total_items']} items)\n"
            f"• Meals Logged Today: <b>{m['meals_today']}</b>\n"
            f"• Meals Logged (Last 7 Days): <b>{m['meals_7d']}</b>\n"
            f"• Avg Meals / Active User: <b>{m['avg_meals_per_active']}</b>\n\n"
            "💬 <b>Voice of Customer:</b>\n"
            f"• Total Feedback Submissions: <b>{m['feedback_count']}</b> (use /viewfeedback)\n\n"
            "🏆 <b>User Activity Breakdown:</b>\n"
            f"{leaderboard_text}"
        )

        if status_msg:
            await status_msg.edit_text(report, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(report, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to generate metrics: {e}", exc_info=True)
        err_text = f"⚠️ Error computing platform metrics: {html.escape(str(e))}"
        if status_msg:
            await status_msg.edit_text(err_text, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(err_text, parse_mode=ParseMode.HTML)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline buttons for analytics time ranges and delete confirmation."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)

    if data == "analytics_7d":
        await send_analytics_report(chat_id, user_name, context, days_window=7)
    elif data == "analytics_30d":
        await send_analytics_report(chat_id, user_name, context, days_window=30)
    elif data.startswith("chart_"):
        days = 30 if "30" in data else 7
        status_msg = await query.message.reply_text(
            f"🎨 <i>Rendering {days}-day high-resolution trend chart...</i>",
            parse_mode=ParseMode.HTML,
        )
        try:
            await send_analytics_chart(chat_id, user_name, context, days_window=days)
            await status_msg.delete()
        except Exception as e:
            logger.error(f"Error rendering chart: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Failed to render chart: {html.escape(str(e))}")
    elif data == "today_summary":
        await today_command(update, context)
    elif data == "confirm_delete":
        deleted = await firestore_service.delete_all_user_data(chat_id)
        await query.edit_message_text(
            f"✅ <b>Account & Data Deleted</b>\n\nAll your meal logs ({deleted} items) and user profile have been permanently deleted from our servers.\n\nType /start anytime if you wish to begin fresh.",
            parse_mode=ParseMode.HTML,
        )
    elif data == "cancel_delete":
        await query.edit_message_text("✅ Deletion canceled. Your data remains safe.", parse_mode=ParseMode.HTML)
    elif data == "menu_targets":
        await targets_command(update, context)
    elif data == "preset_cut":
        await firestore_service.update_user_targets(chat_id, calorie_target=1700, protein_target=140, fiber_target=25)
        await query.edit_message_text(
            "✅ <b>Daily Targets Set: Fat Loss / Cut</b>\n\n"
            "🔥 <b>Calories:</b> 1,700 kcal\n"
            "🥩 <b>Protein:</b> 140.0 g\n"
            "🥗 <b>Dietary Fiber:</b> 25.0 g\n\n"
            "Your analytics and trend charts will now calibrate against these goals! 🎯\n"
            "<i>Change anytime via /targets.</i>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "preset_maintain":
        await firestore_service.update_user_targets(chat_id, calorie_target=2000, protein_target=130, fiber_target=25)
        await query.edit_message_text(
            "✅ <b>Daily Targets Set: Maintenance</b>\n\n"
            "🔥 <b>Calories:</b> 2,000 kcal\n"
            "🥩 <b>Protein:</b> 130.0 g\n"
            "🥗 <b>Dietary Fiber:</b> 25.0 g\n\n"
            "Your analytics and trend charts will now calibrate against these goals! 🎯\n"
            "<i>Change anytime via /targets.</i>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "preset_bulk":
        await firestore_service.update_user_targets(chat_id, calorie_target=2400, protein_target=160, fiber_target=30)
        await query.edit_message_text(
            "✅ <b>Daily Targets Set: Lean Muscle Gain</b>\n\n"
            "🔥 <b>Calories:</b> 2,400 kcal\n"
            "🥩 <b>Protein:</b> 160.0 g\n"
            "🥗 <b>Dietary Fiber:</b> 30.0 g\n\n"
            "Your analytics and trend charts will now calibrate against these goals! 🎯\n"
            "<i>Change anytime via /targets.</i>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "prompt_custom_targets":
        context.user_data["awaiting_targets"] = True
        await query.edit_message_text(
            "✏️ <b>Enter Custom Daily Targets</b>\n\n"
            "Reply with your numbers in this format:\n"
            "<code>&lt;calories&gt; &lt;protein&gt; [fiber]</code>\n\n"
            "<i>Example: 1800 145 25</i>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "prompt_feedback":
        context.user_data["awaiting_feedback"] = True
        await query.message.reply_text(
            "💬 <b>Feedback & Suggestions</b>\n\n"
            "We'd love to hear your thoughts! Whether it's a feature request, bug report, or idea to improve the tracker, please let us know.\n\n"
            "👉 <i>Type your feedback below:</i>",
            parse_mode=ParseMode.HTML,
        )


# =====================================================================
# Food Logging Processors (Photo & Text)
# =====================================================================
async def process_and_log_meal(
    update: Update,
    text_prompt: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    images: Optional[List[bytes]] = None,
    status_msg: Optional[object] = None,
) -> None:
    """Common pipeline for parsing food, saving to Firestore, dual-writing to sheets, and sending reply."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    user_name = get_user_display_name(update)
    chat_id = update.effective_chat.id
    now = datetime.now(LOCAL_TZ)
    target_meal_date = extract_historical_meal_date(text_prompt, reference_date=now.date())
    meal_date_str = target_meal_date.strftime("%Y-%m-%d")
    meal_time_str = now.strftime("%H:%M:%S")
    date_time_str = f"{meal_date_str} {meal_time_str}"

    await update.message.chat.send_action(action=ChatAction.TYPING)
    if status_msg is None:
        status_msg = await update.message.reply_text("🔍 <i>Analyzing meal with Gemini AI...</i>", parse_mode=ParseMode.HTML)

    try:
        meal_result = await analyze_food_with_gemini(
            text_prompt=text_prompt,
            image_bytes=image_bytes,
            images=images,
        )
    except Exception as e:
        logger.error(f"Gemini analysis failed: {e}", exc_info=True)
        err_msg = str(e)
        if "503" in err_msg or "UNAVAILABLE" in err_msg or "high demand" in err_msg:
            user_friendly_err = (
                "⚠️ <b>AI Service High Demand</b>\n\n"
                "Google's Gemini servers are experiencing a brief spike in traffic. "
                "Please wait 5–10 seconds and try sending your meal again."
            )
        else:
            user_friendly_err = f"❌ <b>Analysis Failed:</b> {html.escape(str(e))}\n\nPlease try again with a clearer photo or description."
        await status_msg.edit_text(user_friendly_err, parse_mode=ParseMode.HTML)
        return

    if not meal_result.items:
        note = meal_result.motivational_note or "No food or beverage detected."
        await status_msg.edit_text(
            f"🤔 <b>No Food Detected</b>\n\n{html.escape(note)}\n\n"
            "<i>Please send a clear photo or description of what you ate or drank.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    user_msg_id = update.message.message_id if update.message else None
    bot_msg_id = getattr(status_msg, "message_id", None)

    # Save to Firestore (Primary Multi-Tenant Store)
    meal_doc = {
        "date": meal_date_str,
        "time": meal_time_str,
        "timestamp": date_time_str,
        "category": meal_result.items[0].category if meal_result.items else "Meal",
        "total_calories": round(sum(i.calories for i in meal_result.items), 1),
        "total_protein": round(sum(i.protein for i in meal_result.items), 1),
        "total_carbs": round(sum(i.carbohydrates for i in meal_result.items), 1),
        "total_fat": round(sum(i.fat for i in meal_result.items), 1),
        "total_fiber": round(sum(i.fiber for i in meal_result.items), 1),
        "nutrition_score": round(calculate_weighted_nutrition_score(meal_result.items)),
        "motivational_note": meal_result.motivational_note,
        "raw_text_prompt": text_prompt,
        "items": [i.model_dump() for i in meal_result.items],
        "user_message_id": user_msg_id,
        "bot_message_id": bot_msg_id,
    }

    saved_meal_id = None
    try:
        saved_meal_id = await firestore_service.save_meal(chat_id, user_name, meal_doc)
        day_totals = await firestore_service.get_user_today_totals(chat_id, meal_date_str)
    except Exception as e:
        logger.error(f"Firestore save error: {e}", exc_info=True)
        day_totals = {
            "calories": sum(i.calories for i in meal_result.items),
            "protein": sum(i.protein for i in meal_result.items),
            "carbs": sum(i.carbohydrates for i in meal_result.items),
            "fat": sum(i.fat for i in meal_result.items),
            "fiber": sum(i.fiber for i in meal_result.items),
            "item_count": len(meal_result.items),
            "nutrition_score": round(calculate_weighted_nutrition_score(meal_result.items)),
        }

    # Optional Dual-Write to Google Sheets (if configured)
    if sheets_service.is_configured():
        rows_to_insert = []
        for item in meal_result.items:
            rows_to_insert.append([
                date_time_str,
                user_name,
                item.item_name,
                item.category,
                round(item.calories, 1),
                round(item.protein, 1),
                round(item.carbohydrates, 1),
                round(item.fat, 1),
                round(item.fiber, 1),
                item.short_description,
                item.nutrition_score,
            ])
        asyncio.create_task(sheets_service.append_food_items(rows_to_insert))

    reply_html = format_telegram_reply(
        user_name=user_name,
        datetime_str=date_time_str,
        items=meal_result.items,
        today_totals=day_totals,
        motivational_note=meal_result.motivational_note,
        sheet_saved=True,
        target_date=target_meal_date,
    )
    try:
        await status_msg.edit_text(reply_html, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Could not edit status message ({e}), sending new reply message instead.")
        try:
            sent_msg = await update.message.reply_text(reply_html, parse_mode=ParseMode.HTML)
            if saved_meal_id and sent_msg:
                await firestore_service.update_meal(chat_id, saved_meal_id, {"bot_message_id": sent_msg.message_id})
        except Exception as err2:
            logger.error(f"Failed to deliver meal confirmation to user: {err2}. Rolling back meal {saved_meal_id}.")
            if saved_meal_id:
                await firestore_service.delete_meal(chat_id, saved_meal_id)
            raise err2


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming food/drink photos, including multi-photo albums (media groups)."""
    if not update.message or not update.message.photo:
        return

    user_name = get_user_display_name(update)
    first_name = update.effective_user.first_name or "" if update.effective_user else ""
    asyncio.create_task(firestore_service.touch_user_activity(update.effective_chat.id, user_name, first_name))

    caption = update.message.caption or ""
    photo = update.message.photo[-1]
    media_group_id = update.message.media_group_id

    # If it's a standalone single photo, process immediately
    if not media_group_id:
        status_msg = await update.message.reply_text("🔍 <i>Analyzing meal with Gemini AI...</i>", parse_mode=ParseMode.HTML)
        try:
            tg_file = await photo.get_file()
            photo_bytes = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Failed to download photo: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Failed to download photo: {html.escape(str(e))}")
            return

        opt_bytes = optimize_image_for_vision(bytes(photo_bytes))
        await process_and_log_meal(
            update=update,
            text_prompt=caption if caption.strip() else None,
            image_bytes=opt_bytes,
            status_msg=status_msg,
        )
        return

    # Multi-Photo Album (Media Group) Handling
    is_leader = False
    async with _MEDIA_GROUP_GLOBAL_LOCK:
        if media_group_id not in _MEDIA_GROUP_BUFFERS:
            buffer = MediaGroupBuffer(media_group_id, update)
            _MEDIA_GROUP_BUFFERS[media_group_id] = buffer
            is_leader = True
        else:
            buffer = _MEDIA_GROUP_BUFFERS[media_group_id]

    status_msg = None
    if is_leader:
        status_msg = await update.message.reply_text(
            "📥 <i>Receiving photo album...</i>",
            parse_mode=ParseMode.HTML
        )

    try:
        tg_file = await photo.get_file()
        photo_bytes = await tg_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"Failed to download album photo ({media_group_id}): {e}", exc_info=True)
        photo_bytes = None

    async with buffer.lock:
        if photo_bytes:
            opt_bytes = optimize_image_for_vision(bytes(photo_bytes))
            buffer.images.append(opt_bytes)
        if caption.strip() and not buffer.caption:
            buffer.caption = caption.strip()
        buffer.last_received_time = time.monotonic()

    # Non-leader tasks finish here once photo is buffered
    if not is_leader:
        return

    # Leader task waits for remaining album photos to arrive
    start_time = time.monotonic()
    while True:
        await asyncio.sleep(0.2)
        now_t = time.monotonic()
        async with buffer.lock:
            idle_time = now_t - buffer.last_received_time
            total_time = now_t - start_time
        if idle_time >= 0.6 or total_time >= 5.0:
            break

    # Evict buffer from global map
    async with _MEDIA_GROUP_GLOBAL_LOCK:
        _MEDIA_GROUP_BUFFERS.pop(media_group_id, None)

    if not buffer.images:
        if status_msg:
            await status_msg.edit_text("❌ Failed to download album images. Please try again.")
        return

    if status_msg:
        await status_msg.edit_text(
            f"🔍 <i>Analyzing meal ({len(buffer.images)} photos) with Gemini AI...</i>",
            parse_mode=ParseMode.HTML
        )

    await process_and_log_meal(
        update=buffer.initial_update,
        text_prompt=buffer.caption if buffer.caption else None,
        images=buffer.images,
        status_msg=status_msg,
    )


async def handle_specific_date_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_name: str,
    text: str,
) -> None:
    """Handles single-date meal history lookups (e.g. 'What did I eat yesterday?', '25 Sep')."""
    target_date = parse_historical_date(text)
    if not target_date:
        await update.message.reply_text(
            "❓ <i>I couldn't identify the date you asked for.</i>\n\n"
            "Try phrases like:\n"
            "• <i>'What did I eat yesterday?'</i>\n"
            "• <i>'Show meals on 25 Sep'</i>\n"
            "• <i>'Calories on Friday'</i>\n"
            "• <code>2026-09-25</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.message.reply_text(
        f"📅 <i>Looking up meals for {target_date.strftime('%A, %d %b')}...</i>",
        parse_mode=ParseMode.HTML,
    )
    try:
        date_str = target_date.strftime("%Y-%m-%d")
        meals = await firestore_service.get_user_meals_for_date(chat_id, date_str)
        profile = await firestore_service.get_user_profile(chat_id)
        summary = format_specific_date_summary(
            date_obj=target_date,
            meals=meals,
            user_name=user_name,
            targets=profile,
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 7-Day Digest", callback_data="analytics_7d"),
                InlineKeyboardButton("🔥 Today's Totals", callback_data="today_summary"),
            ]
        ])
        await status_msg.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error handling date lookup: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Error retrieving date summary:</b> {html.escape(str(e))}",
            parse_mode=ParseMode.HTML,
        )


async def handle_analytics_qa(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_name: str,
    text: str,
) -> None:
    """Processes conversational analytical questions about past nutrition data."""
    status_msg = await update.message.reply_text("🔍 <i>Analyzing your past nutrition data...</i>", parse_mode=ParseMode.HTML)
    try:
        now = datetime.now(LOCAL_TZ).date()
        lower_t = text.lower()
        if any(w in lower_t for w in ["month", "30 day", "30-day"]):
            days_window = 30
        elif any(w in lower_t for w in ["two weeks", "14 day", "14-day", "fortnight"]):
            days_window = 14
        else:
            days_window = 7 if any(w in lower_t for w in ["week", "7 day", "7-day"]) else 30

        start_date = (now - timedelta(days=days_window - 1)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        records = await firestore_service.get_user_date_range_records(chat_id, start_date, end_date)
        profile = await firestore_service.get_user_profile(chat_id)
        cal_target = profile.get("daily_calorie_target", 2000)
        pro_target = profile.get("daily_protein_target", 150)
        fib_target = profile.get("daily_fiber_target", 25)
        targets_set = profile.get("targets_set", False)

        logged_days = [r for r in records if r.get("calories", 0) > 0]
        if not logged_days:
            await status_msg.edit_text(
                f"📊 <b>No Meal History Found ({days_window}-Day Window)</b>\n\n"
                f"You haven't logged any meals in the past {days_window} days yet.\n\n"
                "💡 <i>Snap a photo or type what you ate to start seeing your trend graphs!</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        total_days = days_window
        days_logged = len(logged_days)
        avg_cals = sum(r["calories"] for r in logged_days) / days_logged
        avg_pro = sum(r["protein"] for r in logged_days) / days_logged
        avg_carbs = sum(r["carbs"] for r in logged_days) / days_logged
        avg_fat = sum(r["fat"] for r in logged_days) / days_logged
        avg_fiber = sum(r["fiber"] for r in logged_days) / days_logged

        on_track_days = sum(1 for r in logged_days if abs(r["calories"] - cal_target) <= (cal_target * 0.15))
        over_target_days = sum(1 for r in logged_days if r["calories"] > (cal_target * 1.15))
        under_target_days = sum(1 for r in logged_days if r["calories"] < (cal_target * 0.85))
        hit_protein_days = sum(1 for r in logged_days if r["protein"] >= (pro_target * 0.90))

        highest_cal_day = max(logged_days, key=lambda x: x["calories"])
        lowest_cal_day = min(logged_days, key=lambda x: x["calories"])

        context_lines = [
            f"Analysis Window: Past {days_window} days ({start_date} to {end_date})",
            f"User Targets Configured: {'Yes' if targets_set else 'No (Using standard 2000 kcal / 150g protein baselines)'}",
            f"Daily Calorie Target: {int(cal_target)} kcal",
            f"Daily Protein Target: {int(pro_target)}g",
            f"Daily Fiber Target: {int(fib_target)}g",
            f"Days with Logged Meals: {days_logged}/{total_days} days",
            f"Days on Calorie Target (+/- 15%): {on_track_days}/{days_logged} ({round(on_track_days/days_logged*100)}%)",
            f"Days Over Calorie Target: {over_target_days}",
            f"Days Under Calorie Target: {under_target_days}",
            f"Days Meeting Protein Target: {hit_protein_days}/{days_logged} ({round(hit_protein_days/days_logged*100)}%)",
            f"Averages: {avg_cals:.0f} kcal, {avg_pro:.1f}g Protein, {avg_carbs:.1f}g Carbs, {avg_fat:.1f}g Fat, {avg_fiber:.1f}g Fiber",
            f"Highest Calorie Day: {highest_cal_day['date']} ({highest_cal_day['calories']:.0f} kcal)",
            f"Lowest Calorie Day: {lowest_cal_day['date']} ({lowest_cal_day['calories']:.0f} kcal)",
            "",
            "Day-by-Day Logged Details:",
        ]
        for r in logged_days:
            score_str = f"Score: {round(r['nutrition_score_sum'] / r['nutrition_score_count'])}" if r.get("nutrition_score_count", 0) > 0 else "No score"
            context_lines.append(
                f"- {r['date']}: {r['calories']:.0f} kcal | {r['protein']:.1f}g P | {r['carbs']:.1f}g C | {r['fat']:.1f}g F | {r['fiber']:.1f}g Fib | {score_str}"
            )

        context_data = "\n".join(context_lines)
        answer = await answer_analytics_qa_with_gemini(
            user_question=text,
            context_data=context_data,
            user_name=user_name,
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 7-Day Digest", callback_data="analytics_7d"),
                InlineKeyboardButton("🗓️ 30-Day Digest", callback_data="analytics_30d"),
            ],
            [
                InlineKeyboardButton("🖼️ View High-Res Chart", callback_data=f"chart_{days_window}d"),
            ]
        ])

        await status_msg.edit_text(answer, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error handling analytics Q&A: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Could not analyze past data:</b> {html.escape(str(e))}\n\n"
            "Try asking in a different way or view your summary via /analytics.",
            parse_mode=ParseMode.HTML,
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Intelligent Conversational Router:
    Routes incoming text messages to Analytics, Export, Privacy, Deletion, Editing, Undo, or Food Logging.
    Also handles Quote/Reply to edit previous meal entries directly.
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    chat_id = update.effective_chat.id
    user_name = get_user_display_name(update)
    first_name = update.effective_user.first_name or "" if update.effective_user else ""
    asyncio.create_task(firestore_service.touch_user_activity(chat_id, user_name, first_name))

    # Check if user is in an active targets prompt flow
    if context.user_data.get("awaiting_targets"):
        context.user_data["awaiting_targets"] = False
        parts = [p for p in text.replace(",", " ").split() if p]
        try:
            new_cal = float(parts[0])
            new_pro = float(parts[1]) if len(parts) > 1 else 150.0
            new_fib = float(parts[2]) if len(parts) > 2 else 25.0
            await firestore_service.update_user_targets(
                chat_id, calorie_target=new_cal, protein_target=new_pro, fiber_target=new_fib
            )
            await update.message.reply_text(
                "✅ <b>Custom Daily Targets Saved!</b>\n\n"
                f"🔥 <b>Calories:</b> {new_cal:,.0f} kcal\n"
                f"🥩 <b>Protein:</b> {new_pro:.1f} g\n"
                f"🥗 <b>Dietary Fiber:</b> {new_fib:.1f} g\n\n"
                "Your charts and coaching summaries are now calibrated to your numbers! 🎯",
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            await update.message.reply_text(
                "⚠️ Could not parse targets. Please reply with numbers like: <code>1800 140 25</code> or type /targets to pick a preset.",
                parse_mode=ParseMode.HTML,
            )
            return

    # Check if user is in an active edit prompt flow (e.g. from /edit without arguments)
    if context.user_data.get("awaiting_edit"):
        context.user_data["awaiting_edit"] = False
        await execute_meal_edit(update, context, chat_id, user_name, text)
        return

    # Check if user is in an active feedback prompt flow
    if context.user_data.get("awaiting_feedback"):
        context.user_data["awaiting_feedback"] = False
        await submit_feedback(update, context, text)
        return

    # Check if message is a Quote/Reply to an existing message
    reply_to = update.message.reply_to_message
    if reply_to:
        target_meal = await firestore_service.find_meal_by_message_id(chat_id, reply_to.message_id)
        if not target_meal and reply_to.from_user and reply_to.from_user.is_bot:
            # If replied to bot meal card or prompt, target the most recent meal
            target_meal = await firestore_service.get_last_meal(chat_id)

        if target_meal:
            await execute_meal_edit(update, context, chat_id, user_name, text, target_meal=target_meal)
            return

    # Classify intent via Natural Language Router
    intent = classify_text_intent(text)

    if intent == "today":
        await today_command(update, context)
    elif intent == "undo":
        await undo_command(update, context)
    elif intent == "export":
        await export_command(update, context)
    elif intent == "privacy":
        await privacy_command(update, context)
    elif intent == "delete":
        await delete_command(update, context)
    elif intent == "targets":
        await targets_command(update, context)
    elif intent == "analytics_qa":
        await handle_analytics_qa(update, context, chat_id, user_name, text)
    elif intent == "date_query":
        await handle_specific_date_query(update, context, chat_id, user_name, text)
    elif intent == "analytics_7d":
        await send_analytics_report(chat_id, user_name, context, days_window=7)
    elif intent == "analytics_30d":
        await send_analytics_report(chat_id, user_name, context, days_window=30)
    elif intent == "edit":
        await execute_meal_edit(update, context, chat_id, user_name, text)
    elif intent == "feedback":
        lower_t = text.lower()
        cleaned_msg = ""
        for prefix in ["feedback:", "feedback -", "feedback ", "suggest:", "suggestion:", "bug:", "bug report:"]:
            if lower_t.startswith(prefix):
                cleaned_msg = text[len(prefix):].strip()
                break
        if cleaned_msg:
            await submit_feedback(update, context, cleaned_msg)
        else:
            context.user_data["awaiting_feedback"] = True
            await update.message.reply_text(
                "💬 <b>We'd Love Your Feedback!</b>\n\n"
                "Have an idea for a new feature, found an issue, or want to share your experience with the tracker?\n\n"
                "👉 <i>Simply reply or type your feedback below:</i>",
                parse_mode=ParseMode.HTML,
            )
    else:
        # Default: Process as food log
        await process_and_log_meal(update=update, text_prompt=text, image_bytes=None)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler."""
    logger.error(f"Unhandled exception while processing update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred while processing your request. Please try again in a moment."
            )
        except Exception:
            pass


# =====================================================================
# Main Application Entry Point
# =====================================================================
def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("\n❌ ERROR: TELEGRAM_BOT_TOKEN is not set.")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("\n❌ ERROR: GEMINI_API_KEY is not set.")
        sys.exit(1)

    print("🚀 Starting Telegram Macros & Calories Logging Bot (Firestore Multi-Tenant)...")
    print(f"🌐 Configured Timezone: {TIMEZONE_STR}")
    print(f"🔒 Admin User(s): {ADMIN_USERS}")

    tg_request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=20.0,
        pool_timeout=10.0,
    )
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(tg_request).build()

    # Core Navigation & Information Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("summary", today_command))
    app.add_handler(CommandHandler("changelog", changelog_command))
    app.add_handler(CommandHandler("updates", changelog_command))

    # Analytics Commands
    app.add_handler(CommandHandler("analytics", analytics_command))
    app.add_handler(CommandHandler("stats", analytics_command))
    app.add_handler(CommandHandler("weekly", weekly_command))
    app.add_handler(CommandHandler("monthly", monthly_command))

    # Smart Editing & Meal Management Commands
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("targets", targets_command))
    app.add_handler(CommandHandler("goals", targets_command))
    app.add_handler(CommandHandler("target", targets_command))

    # Data Governance & Compliance Commands
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("privacy", privacy_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("reset", delete_command))

    # Admin Broadcast & Feedback Commands
    app.add_handler(CommandHandler("release", release_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("suggest", feedback_command))
    app.add_handler(CommandHandler("viewfeedback", viewfeedback_command))
    app.add_handler(CommandHandler("feedbacks", viewfeedback_command))
    app.add_handler(CommandHandler("metrics", metrics_command))
    app.add_handler(CommandHandler("admin", metrics_command))
    app.add_handler(CommandHandler("platform", metrics_command))

    # Inline Keyboard Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Message Handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Error Handler
    app.add_error_handler(error_handler)

    # Start Bot (Webhook for Cloud Run, Polling for Local Dev)
    is_cloud_run = bool(os.getenv("K_SERVICE") or WEBHOOK_URL)
    if is_cloud_run:
        webhook_path = "webhook"
        full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{webhook_path}" if WEBHOOK_URL else None
        print(f"🌐 Running in Cloud Webhook Mode on port {PORT}...")
        if full_webhook_url:
            print(f"🔗 Setting Telegram Webhook to: {full_webhook_url}")
            app.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=webhook_path,
                webhook_url=full_webhook_url,
                secret_token=TELEGRAM_WEBHOOK_SECRET if TELEGRAM_WEBHOOK_SECRET else None,
            )
        else:
            app.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=webhook_path,
            )
    else:
        print("💻 Running in Local Polling Mode...")
        app.run_polling()


if __name__ == "__main__":
    main()
