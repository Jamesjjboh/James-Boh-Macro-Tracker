"""
Telegram Macros & Calories Logging Bot
Logs daily calories, macros, and nutrition score to Google Sheets.
Powered by Gemini 3.6 Flash Vision & gspread.
"""

import asyncio
from datetime import datetime
import html
import logging
import os
import sys
from typing import List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
import gspread
from PIL import Image
from pydantic import BaseModel, Field
import pytz
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
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

# User access control (optional)
# Comma-separated list of allowed usernames or user IDs (e.g., "james,partner_username")
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

# Google Sheets Headers (11 columns including Dietary Fiber)
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
# Google Sheets Integration
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
        self.subscribers_worksheet = None
        self._lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return bool(self.credentials_json) or os.path.exists(self.credentials_path)

    def _get_worksheet_sync(self) -> gspread.Worksheet:
        if not self.is_configured():
            raise FileNotFoundError(
                f"Google Service Account credentials not found. Please provide GOOGLE_SERVICE_ACCOUNT_JSON or a file at '{self.credentials_path}'"
            )

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        if self.credentials_json:
            import json
            service_account_info = json.loads(self.credentials_json)
            gc = gspread.service_account_from_dict(service_account_info, scopes=scopes)
        else:
            gc = gspread.service_account(filename=self.credentials_path, scopes=scopes)
        
        try:
            sh = gc.open(self.sheet_name)
            self.spreadsheet = sh
        except gspread.SpreadsheetNotFound:
            raise ValueError(
                f"Google Sheet '{self.sheet_name}' not found. Please create it and share it with your Service Account email."
            )

        ws = sh.sheet1
        
        # Check if sheet is empty or needs headers
        existing_values = ws.get_all_values()
        if not existing_values or len(existing_values) == 0 or not any(existing_values[0]):
            logger.info("Sheet is empty. Adding standard headers...")
            ws.update(range_name="A1:K1", values=[SHEET_HEADERS])
            try:
                ws.format("A1:K1", {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                })
            except Exception as e:
                logger.warning(f"Could not format header row: {e}")
        elif existing_values[0] != SHEET_HEADERS:
            logger.info("Header mismatch or sheet partially initialized. Ensuring row 1 has standard headers.")
            first_val = str(existing_values[0][0]).strip()
            # If row 1 contains an actual meal entry (starts with timestamp digits)
            if len(first_val) >= 4 and first_val[:4].isdigit():
                logger.info("Row 1 contains meal data instead of headers. Inserting headers at Row 1...")
                ws.insert_row(SHEET_HEADERS, 1)
            else:
                # Row 1 is an older or mismatched header, update in place
                ws.update(range_name="A1:K1", values=[SHEET_HEADERS])
            try:
                ws.format("A1:K1", {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                })
            except Exception as e:
                logger.warning(f"Could not format header row: {e}")

        return ws

    async def get_worksheet(self) -> gspread.Worksheet:
        if self.worksheet is None:
            self.worksheet = await asyncio.to_thread(self._get_worksheet_sync)
        return self.worksheet

    def _get_subscribers_worksheet_sync(self) -> gspread.Worksheet:
        if self.spreadsheet is None:
            self._get_worksheet_sync()
        try:
            return self.spreadsheet.worksheet("Subscribers")
        except gspread.WorksheetNotFound:
            logger.info("Creating 'Subscribers' worksheet in Google Sheet...")
            ws = self.spreadsheet.add_worksheet(title="Subscribers", rows=100, cols=4)
            ws.update(range_name="A1:D1", values=[["Chat ID", "User Name", "First Active", "Last Active"]])
            try:
                ws.format("A1:D1", {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                })
            except Exception as e:
                logger.warning(f"Could not format Subscribers header row: {e}")
            return ws

    async def get_subscribers_worksheet(self) -> gspread.Worksheet:
        if self.subscribers_worksheet is None:
            self.subscribers_worksheet = await asyncio.to_thread(self._get_subscribers_worksheet_sync)
        return self.subscribers_worksheet

    async def register_subscriber(self, chat_id: int, user_name: str) -> None:
        """Saves or updates subscriber chat_id and user_name in Google Sheets."""
        if not self.is_configured():
            return
        async with self._lock:
            try:
                ws = await self.get_subscribers_worksheet()
                rows = await asyncio.to_thread(ws.get_all_values)
                now_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                for i, row in enumerate(rows[1:], start=2):
                    if len(row) > 0 and str(row[0]).strip() == str(chat_id):
                        await asyncio.to_thread(ws.update_cell, i, 4, now_str)
                        return
                await asyncio.to_thread(
                    ws.append_row,
                    [str(chat_id), user_name, now_str, now_str],
                    value_input_option="USER_ENTERED",
                )
                logger.info(f"Registered new subscriber: {user_name} ({chat_id})")
            except Exception as e:
                logger.warning(f"Could not register subscriber {chat_id}: {e}")

    async def get_all_subscriber_chat_ids(self) -> List[int]:
        """Returns list of unique chat_ids of all registered subscribers."""
        if not self.is_configured():
            return []
        async with self._lock:
            try:
                ws = await self.get_subscribers_worksheet()
                rows = await asyncio.to_thread(ws.get_all_values)
                chat_ids = []
                for row in rows[1:]:
                    if row and str(row[0]).strip().lstrip("-").isdigit():
                        chat_ids.append(int(str(row[0]).strip()))
                return list(set(chat_ids))
            except Exception as e:
                logger.error(f"Error fetching subscribers: {e}")
                return []

    async def append_food_items(self, rows: list) -> None:
        async with self._lock:
            ws = await self.get_worksheet()
            await asyncio.to_thread(
                ws.append_rows, rows, value_input_option="USER_ENTERED"
            )

    async def get_user_today_totals(self, user_name: str, today_prefix: str) -> dict:
        async with self._lock:
            ws = await self.get_worksheet()
            all_rows = await asyncio.to_thread(ws.get_all_values)

        totals = {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "item_count": 0,
        }

        if not all_rows or len(all_rows) == 0:
            return totals

        # Detect whether row 0 is header or data
        has_header = False
        first_cell = str(all_rows[0][0]).strip().lower()
        if "date" in first_cell or "time" in first_cell or (len(all_rows[0]) > 1 and "user" in str(all_rows[0][1]).lower()):
            has_header = True

        data_rows = all_rows[1:] if has_header else all_rows
        clean_user = user_name.strip().lower()

        # Columns:
        # 0: Date & Time, 1: User Name, 2: Item Name, 3: Category,
        # 4: Calories, 5: Protein, 6: Carbohydrates, 7: Fat, 8: Fiber, 9: Desc, 10: Score
        for row in data_rows:
            if len(row) < 8:
                continue
            row_date = str(row[0]).strip()
            row_user = str(row[1]).strip().lower()

            if row_date.startswith(today_prefix) and (row_user == clean_user):
                try:
                    totals["calories"] += float(str(row[4]).replace(",", "").strip())
                except (ValueError, IndexError):
                    pass
                try:
                    totals["protein"] += float(str(row[5]).replace(",", "").strip())
                except (ValueError, IndexError):
                    pass
                try:
                    totals["carbs"] += float(str(row[6]).replace(",", "").strip())
                except (ValueError, IndexError):
                    pass
                try:
                    totals["fat"] += float(str(row[7]).replace(",", "").strip())
                except (ValueError, IndexError):
                    pass
                if len(row) >= 11:
                    try:
                        totals["fiber"] += float(str(row[8]).replace(",", "").strip())
                    except (ValueError, IndexError):
                        pass
                totals["item_count"] += 1

        totals["calories"] = round(totals["calories"], 1)
        totals["protein"] = round(totals["protein"], 1)
        totals["carbs"] = round(totals["carbs"], 1)
        totals["fat"] = round(totals["fat"], 1)
        totals["fiber"] = round(totals["fiber"], 1)
        return totals


# Global Sheets Service instance
sheets_service = GoogleSheetsService(
    credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
    sheet_name=GOOGLE_SHEET_NAME,
    credentials_json=GOOGLE_SERVICE_ACCOUNT_JSON if GOOGLE_SERVICE_ACCOUNT_JSON else None,
)


# =====================================================================
# Gemini 3.6 Flash Vision & Nutrition Analysis
# =====================================================================
def get_gemini_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing in your .env configuration.")
    return genai.Client(api_key=GEMINI_API_KEY)


async def analyze_food_with_gemini(
    text_prompt: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    mime_type: str = "image/jpeg",
) -> MealAnalysisResponse:
    """
    Sends photo and/or text to Gemini 3.6 Flash Vision API with structured JSON output.
    """
    client = get_gemini_client()
    contents = [GEMINI_SYSTEM_PROMPT]

    if image_bytes:
        contents.append(
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            )
        )

    if text_prompt:
        contents.append(
            f"USER NOTE / PHOTO CAPTION (STRICT PRIORITY):\n"
            f"\"{text_prompt}\"\n\n"
            f"CRITICAL: The user's note/caption above is the absolute ground truth. "
            f"If the user specifies any food or drink item names, brands, or preparations (e.g. 'nutrisoy soy milk no sugar', 'oat milk', 'sugar-free iced tea', 'half portion'), "
            f"you MUST identify that exact food item and its corresponding nutritional values (protein, carbs, fat, fiber, calories) "
            f"rather than making generic visual guesses (e.g. do not guess whole cow milk if the user specifies soy milk). "
            f"If the user states a meal type (e.g. 'Lunch'), classify all items in this meal under that category."
        )
    elif not image_bytes:
        raise ValueError("Either text description or image must be provided.")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=MealAnalysisResponse,
        temperature=0.2,
    )

    candidate_models = ["gemini-3.6-flash", "gemini-3-flash-preview"]
    last_error = None

    for model_name in candidate_models:
        try:
            logger.info(f"Calling {model_name} for meal analysis...")
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=30.0,
            )
            # Response is parsed directly into Pydantic model by google-genai
            if response.parsed and isinstance(response.parsed, MealAnalysisResponse):
                return response.parsed
            
            # Fallback to model_validate_json if parsed is not directly populated
            if response.text:
                return MealAnalysisResponse.model_validate_json(response.text)
        except Exception as e:
            logger.warning(f"Model {model_name} encountered an issue: {e}. Trying fallback...")
            last_error = e
            continue

    if last_error:
        raise last_error
    raise ValueError("Gemini returned an empty or unparseable response.")


# =====================================================================
# Helper Formatting Functions
# =====================================================================
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
    username = (user.username or "").lower()
    user_id = str(user.id)
    return (username in ALLOWED_USERS) or (user_id in ALLOWED_USERS)


def format_telegram_reply(
    user_name: str,
    datetime_str: str,
    items: List[FoodItem],
    today_totals: dict,
    motivational_note: str,
    sheet_saved: bool = True,
) -> str:
    lines = [
        "🍽️ <b>Meal Logged Successfully!</b>" if sheet_saved else "⚠️ <b>Analysis Complete (Sheet Not Saved)</b>",
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

    for i, item in enumerate(items, 1):
        if item.nutrition_score >= 80:
            score_badge = "🟢"
        elif item.nutrition_score >= 55:
            score_badge = "🟡"
        else:
            score_badge = "🔴"

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
        ])

    lines.extend([
        "",
        "──────── <b>Today's Cumulative Totals</b> ────────",
        f"🔥 <b>Total Calories:</b> <b>{today_totals['calories']:.0f} kcal</b>",
        f"🥩 <b>Protein:</b> <b>{today_totals['protein']:.1f} g</b>",
        f"🍞 <b>Carbohydrates:</b> <b>{today_totals['carbs']:.1f} g</b>",
        f"🥑 <b>Fat:</b> <b>{today_totals['fat']:.1f} g</b>",
        f"🥗 <b>Fiber:</b> <b>{today_totals.get('fiber', 0.0):.1f} g</b>",
        f"📊 <i>Items logged today:</i> {today_totals['item_count']}",
        "",
        "──────── <b>Coach's Note</b> ────────",
        f"💪 <i>{html.escape(motivational_note)}</i>",
    ])

    return "\n".join(lines)


# =====================================================================
# Telegram Command Handlers
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command."""
    user_name = get_user_display_name(update)
    if update.effective_chat:
        asyncio.create_task(sheets_service.register_subscriber(update.effective_chat.id, user_name))
    welcome_text = (
        f"🥗 <b>James Boh Macro Tracker</b>\n"
        f"<i>Created by James Boh (<a href=\"https://www.linkedin.com/in/jamesboh/\">LinkedIn</a> | <a href=\"https://github.com/Jamesjjboh/James-Boh-Macro-Tracker\">GitHub</a>)</i>\n\n"
        f"👋 <b>Welcome, {html.escape(user_name)}!</b>\n\n"
        "I'm your personal <b>Macros & Calories Logging Bot</b>, powered by Gemini 3.6 Flash Vision.\n\n"
        "✨ <b>How to Log Food:</b>\n"
        "1. 📸 <b>Send a Photo:</b> Snap your meal or beverage. <i>Tip: Type in the photo caption to specify what it is or the meal type (e.g. 'Lunch: with NutriSoy soy milk no sugar')!</i>\n"
        "2. ✍️ <b>Send a Text:</b> Type whatever you ate (e.g. <i>'Chicken rice with iced tea'</i> or <i>'2 boiled eggs, 1 slice wholewheat toast'</i>).\n\n"
        "📊 <b>What I Do:</b>\n"
        "• Break down composite meals into individual items\n"
        "• Estimate Calories, Protein, Carbs, Fat, and Dietary Fiber\n"
        "• Calculate a <b>Nutrition & Fat Loss Score</b> (0-100)\n"
        "• Log all rows automatically into your Google Sheet\n"
        "• Track today's cumulative macros for you and your partner!\n\n"
        "⚙️ <b>Commands:</b>\n"
        "/today - View your cumulative macro totals for today\n"
        "/changelog - View recent product updates & improvements\n"
        "/help - Display usage instructions"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /help command."""
    help_text = (
        "🥗 <b>James Boh Macro Tracker — Help Guide</b>\n"
        "<i>Created by James Boh (<a href=\"https://www.linkedin.com/in/jamesboh/\">LinkedIn</a> | <a href=\"https://github.com/Jamesjjboh/James-Boh-Macro-Tracker\">GitHub</a>)</i>\n\n"
        "📖 <b>How to Use:</b>\n\n"
        "• <b>Photo Logging:</b> Simply send a photo of your plate, drink, or snack.\n"
        "  💡 <b>Caption Tip:</b> When uploading a photo, type in the Telegram caption to specify what it is or the meal type (e.g. <code>Lunch: with NutriSoy soy milk no sugar</code> or <code>half portion</code>). The bot treats your caption as absolute ground truth!\n\n"
        "• <b>Text Logging:</b> Type naturally, like:\n"
        "  - <code>Chicken breast 200g with broccoli and 1 cup brown rice</code>\n"
        "  - <code>Flat white coffee with oat milk</code>\n"
        "  - <code>Salmon sushi set with miso soup</code>\n\n"
        "• <b>Commands:</b>\n"
        "  /today - View your macro totals logged so far today\n"
        "  /changelog - View recent product updates & release notes\n"
        "  /start - Show the welcome menu\n"
        "  /help - Show this guide\n\n"
        "💡 <i>Tip: The bot distinguishes entries between you and your partner using your Telegram username!</i>\n"
        "💼 <i>Connect with creator: <a href=\"https://www.linkedin.com/in/jamesboh/\">James Boh on LinkedIn</a></i>"
    )
    if is_admin(update):
        help_text += (
            "\n\n🛠️ <b>Admin Release Commands:</b>\n"
            "• <code>/release</code> - Broadcast latest release notes to all users\n"
            "• <code>/broadcast &lt;msg&gt;</code> - Broadcast a custom announcement"
        )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def is_admin(update: Update) -> bool:
    """Checks whether the sender is an authorized administrator."""
    user = update.effective_user
    if not user:
        return False
    username = (user.username or "").lower().lstrip("@")
    if ALLOWED_USERS and username in ALLOWED_USERS:
        return True
    return False


async def release_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to broadcast latest release notes to all registered subscribers."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Unauthorized. Only the admin can broadcast release announcements.")
        return

    subscribers = await sheets_service.get_all_subscriber_chat_ids()
    current_chat_id = update.effective_chat.id
    if current_chat_id not in subscribers:
        subscribers.append(current_chat_id)

    if not subscribers:
        await update.message.reply_text("⚠️ No registered subscribers found to broadcast to.")
        return

    release_announcement = (
        "🚀 <b>James Boh Macro Tracker — New Release!</b>\n"
        "<i>Version 1.2.0 is now live</i>\n\n"
        "<b>What's New:</b>\n"
        "• ☁️ <b>24/7 Cloud Uptime:</b> Runs around the clock on Google Cloud Run.\n"
        "• ⚡ <b>Instant Responses:</b> Serverless webhooks with zero downtime.\n"
        "• 📋 <b>Changelog:</b> Type /changelog anytime to view all updates.\n\n"
        "<i>Happy tracking! Snap your next meal photo to try it out.</i>"
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

    await status_msg.edit_text(
        f"✅ <b>Release Broadcast Complete!</b>\nDelivered to {sent}/{len(subscribers)} subscriber(s).",
        parse_mode=ParseMode.HTML,
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to broadcast a custom message to all subscribers: /broadcast <message>"""
    if not is_admin(update):
        await update.message.reply_text("⛔ Unauthorized. Only the admin can broadcast messages.")
        return

    raw_text = update.message.text or ""
    _, _, message_to_send = raw_text.partition(" ")
    message_to_send = message_to_send.strip()

    if not message_to_send:
        await update.message.reply_text(
            "ℹ️ <b>Usage:</b> <code>/broadcast &lt;your message&gt;</code>\n\n"
            "<i>Example:</i> <code>/broadcast Hey team, we just added fiber tracking!</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    subscribers = await sheets_service.get_all_subscriber_chat_ids()
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

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\nDelivered to {sent}/{len(subscribers)} subscriber(s).",
        parse_mode=ParseMode.HTML,
    )


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /changelog and /updates command."""
    changelog_text = (
        "🚀 <b>James Boh Macro Tracker — What's New</b>\n"
        "<i>Full changelog on <a href=\"https://github.com/Jamesjjboh/James-Boh-Macro-Tracker/blob/main/CHANGELOG.md\">GitHub</a></i>\n\n"
        "<b>v1.2.0 (Latest):</b>\n"
        "• ☁️ <b>24/7 Cloud Uptime:</b> Deployed to Google Cloud Run in Singapore. Runs 24/7 even when laptops are closed!\n"
        "• ⚡ <b>Serverless Webhooks:</b> Zero idle cost ($0/mo) with instant sub-second response times.\n"
        "• 📋 <b>In-Bot Changelog:</b> Type /changelog to see recent product updates.\n\n"
        "<b>v1.1.0:</b>\n"
        "• 🥗 <b>Dietary Fiber:</b> Expanded schema to track daily fiber (g) for satiety & gut health.\n"
        "• 🛡️ <b>503 Resilience:</b> Multi-model fallback chain to prevent API timeout stalls.\n"
        "• 🎯 <b>Caption Override:</b> Photo captions serve as absolute ground truth for hidden ingredients (e.g. unsweetened soy milk).\n\n"
        "<b>v1.0.0:</b>\n"
        "• 📸 Instant photo & text meal recognition powered by Gemini 3.6 Flash.\n"
        "• 📊 Automated logging to Google Sheets with itemized breakdowns & coach's scoring.\n\n"
        "💼 <i>Connect: <a href=\"https://www.linkedin.com/in/jamesboh/\">James Boh on LinkedIn</a></i>"
    )
    await update.message.reply_text(changelog_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /today command to display today's logged totals."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    user_name = get_user_display_name(update)
    if update.effective_chat:
        asyncio.create_task(sheets_service.register_subscriber(update.effective_chat.id, user_name))
    now = datetime.now(LOCAL_TZ)
    today_prefix = now.strftime("%Y-%m-%d")

    if not sheets_service.is_configured():
        await update.message.reply_text(
            "⚠️ Google Sheets service is not configured yet. Please check <code>service_account.json</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.message.reply_text(
        "📊 <i>Fetching your daily totals from Google Sheets...</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        totals = await sheets_service.get_user_today_totals(user_name, today_prefix)
        summary_text = (
            f"📊 <b>Today's Cumulative Macros ({today_prefix})</b>\n"
            f"👤 <b>User:</b> {html.escape(user_name)}\n\n"
            f"🔥 <b>Total Calories:</b> <b>{totals['calories']:.0f} kcal</b>\n"
            f"🥩 <b>Protein:</b> <b>{totals['protein']:.1f} g</b>\n"
            f"🍞 <b>Carbohydrates:</b> <b>{totals['carbs']:.1f} g</b>\n"
            f"🥑 <b>Fat:</b> <b>{totals['fat']:.1f} g</b>\n"
            f"🥗 <b>Fiber:</b> <b>{totals['fiber']:.1f} g</b>\n\n"
            f"📝 <i>Total items logged today:</i> {totals['item_count']}"
        )
        await status_msg.edit_text(summary_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error fetching today totals: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Error fetching totals:</b> {html.escape(str(e))}",
            parse_mode=ParseMode.HTML,
        )


# =====================================================================
# Food Logging Processors (Photo & Text)
# =====================================================================
async def process_and_log_meal(
    update: Update,
    text_prompt: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
) -> None:
    """Common pipeline for parsing food, logging to sheets, and sending reply."""
    if not is_user_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    user_name = get_user_display_name(update)
    if update.effective_chat:
        asyncio.create_task(sheets_service.register_subscriber(update.effective_chat.id, user_name))
    now = datetime.now(LOCAL_TZ)
    date_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today_prefix = now.strftime("%Y-%m-%d")

    # Send typing indicator and status message
    await update.message.chat.send_action(action=ChatAction.TYPING)
    status_msg = await update.message.reply_text(
        "🔍 <i>Analyzing meal with Gemini AI...</i>",
        parse_mode=ParseMode.HTML,
    )

    # 1. Analyze with Gemini 3.6 Flash
    try:
        meal_result = await analyze_food_with_gemini(
            text_prompt=text_prompt,
            image_bytes=image_bytes,
        )
    except Exception as e:
        logger.error(f"Gemini analysis failed: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Analysis Failed:</b> {html.escape(str(e))}\n\nPlease try again or provide a clearer photo/description.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if any food items were detected
    if not meal_result.items:
        note = meal_result.motivational_note or "No food or drink items detected."
        await status_msg.edit_text(
            f"🤔 <b>No Food Detected</b>\n\n{html.escape(note)}\n\n"
            "<i>Please send a clear photo or description of your food or drink.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. Build rows for Google Sheets (11 columns including Fiber)
    # 1. Date & Time (YYYY-MM-DD HH:MM:SS)
    # 2. User Name / Telegram Name
    # 3. Item Name
    # 4. Category
    # 5. Calories (kcal)
    # 6. Protein (g)
    # 7. Carbohydrates (g)
    # 8. Fat (g)
    # 9. Fiber (g)
    # 10. Short Description
    # 11. Nutrition Score (/100)
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

    # 3. Write to Google Sheets
    sheet_saved = False
    sheet_error = None
    today_totals = {
        "calories": sum(item.calories for item in meal_result.items),
        "protein": sum(item.protein for item in meal_result.items),
        "carbs": sum(item.carbohydrates for item in meal_result.items),
        "fat": sum(item.fat for item in meal_result.items),
        "fiber": sum(item.fiber for item in meal_result.items),
        "item_count": len(meal_result.items),
    }

    if sheets_service.is_configured():
        try:
            await status_msg.edit_text(
                "📝 <i>Writing to Google Sheet & calculating daily totals...</i>",
                parse_mode=ParseMode.HTML,
            )
            await sheets_service.append_food_items(rows_to_insert)
            sheet_saved = True
            
            # Recalculate today's cumulative totals including previous meals
            today_totals = await sheets_service.get_user_today_totals(
                user_name, today_prefix
            )
        except Exception as e:
            logger.error(f"Failed to write to Google Sheets: {e}", exc_info=True)
            sheet_error = str(e)
    else:
        sheet_error = "Google Sheets credentials ('service_account.json') not found. Entry was analyzed but not saved to cloud."

    # 4. Format and reply with the complete summary
    reply_html = format_telegram_reply(
        user_name=user_name,
        datetime_str=date_time_str,
        items=meal_result.items,
        today_totals=today_totals,
        motivational_note=meal_result.motivational_note,
        sheet_saved=sheet_saved,
    )

    if not sheet_saved and sheet_error:
        reply_html += f"\n\n⚠️ <i>Note: {html.escape(sheet_error)}</i>"

    await status_msg.edit_text(reply_html, parse_mode=ParseMode.HTML)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming food/drink photos."""
    if not update.message or not update.message.photo:
        return

    # Grab caption if user included notes with the photo
    caption = update.message.caption or ""

    # Get the highest resolution photo
    photo = update.message.photo[-1]
    
    status_msg = await update.message.reply_text("📥 <i>Downloading image...</i>", parse_mode=ParseMode.HTML)
    try:
        tg_file = await photo.get_file()
        photo_bytes = await tg_file.download_as_bytearray()
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Failed to download photo from Telegram: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Failed to download photo: {html.escape(str(e))}")
        return

    await process_and_log_meal(
        update=update,
        text_prompt=caption if caption.strip() else None,
        image_bytes=bytes(photo_bytes),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text food logs (e.g. 'Chicken rice with iced tea')."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    await process_and_log_meal(
        update=update,
        text_prompt=text,
        image_bytes=None,
    )


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
        print("Please copy .env.template to .env and fill in your TELEGRAM_BOT_TOKEN.\n")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("\n❌ ERROR: GEMINI_API_KEY is not set.")
        print("Please copy .env.template to .env and fill in your GEMINI_API_KEY from Google AI Studio.\n")
        sys.exit(1)

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        print("✅ Found Google Service Account configuration via GOOGLE_SERVICE_ACCOUNT_JSON environment variable.")
    elif os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        print(f"✅ Found Google Service Account file: '{GOOGLE_SERVICE_ACCOUNT_FILE}'")
    else:
        print(f"\n⚠️ WARNING: Google Service Account credentials not found (neither GOOGLE_SERVICE_ACCOUNT_JSON nor '{GOOGLE_SERVICE_ACCOUNT_FILE}').")
        print("Google Sheets integration will be disabled until configured.")
        print("See instructions in README.md to configure your Google Service Account.\n")

    print(f"🚀 Starting Telegram Macros & Calories Logging Bot...")
    print(f"📅 Target Google Sheet: '{GOOGLE_SHEET_NAME}'")
    print(f"🌐 Configured Timezone: {TIMEZONE_STR}")

    # Build the Telegram Bot application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("summary", today_command))
    app.add_handler(CommandHandler("changelog", changelog_command))
    app.add_handler(CommandHandler("updates", changelog_command))
    app.add_handler(CommandHandler("release", release_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))

    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Error handler
    app.add_error_handler(error_handler)

    # Start bot (Webhook mode for Cloud Run, Polling mode for Local Development)
    is_cloud_run = bool(os.getenv("K_SERVICE") or WEBHOOK_URL)
    if is_cloud_run:
        webhook_path = "webhook"
        full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{webhook_path}" if WEBHOOK_URL else None
        print(f"🌐 Running in Cloud Webhook Mode on port {PORT}...")
        if full_webhook_url:
            print(f"🔗 Setting Telegram Webhook to: {full_webhook_url}")
        else:
            print("⏳ HTTP server listening on port 8080. Waiting for WEBHOOK_URL configuration.")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=webhook_path,
            webhook_url=full_webhook_url,
            secret_token=TELEGRAM_WEBHOOK_SECRET if TELEGRAM_WEBHOOK_SECRET else None,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        print("🤖 Running in Long Polling Mode (Local). Press Ctrl+C to stop.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
