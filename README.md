# 🥗 James Boh Macro Tracker

_Created by James Boh ([LinkedIn](https://www.linkedin.com/in/jamesboh/) | [GitHub Repository](https://github.com/Jamesjjboh/James-Boh-Macro-Tracker))_

A high-performance Telegram bot for logging daily calories, macronutrients (Protein, Carbs, Fat), and a dedicated **Nutrition & Fat Loss Score** (0–100) directly into a shared Google Sheet.

Powered by:

- **`python-telegram-bot`** (v20+ async architecture)
- **Google Gemini 3.6 Flash Vision** (`google-genai` SDK with strict Pydantic JSON schema)
- **Google Sheets API** (`gspread` & `google-auth` via Service Account)

---

## 🌟 Key Features

1. **Dual-Input Logging**:
   - 📸 **Photo Logging**: Take a photo of your meal or beverage. You can also add a caption with portion notes (e.g. _"Only ate half"_ or _"No dressing"_).
   - ✍️ **Text Logging**: Describe what you ate in natural language (e.g., _"Hainanese chicken rice with iced lemon tea"_).
   - 📸 **Photo Logging**: Take a photo of your meal or beverage. You can type in the photo caption to specify what it is or the meal type (e.g. _"Lunch: with NutriSoy soy milk no sugar"_ or _"Half portion only"_), which the bot treats as ground truth!
   - ✍️ **Text Logging**: Describe what you ate in natural language (e.g., _"Hainanese chicken rice with iced lemon tea"_).
2. **Composite Meal Breakdown**:
   - Multiple items in a single photo or text prompt are separated into individual rows (e.g., separating chicken, rice, and sweet tea).
   - Multiple items in a single photo or text prompt are separated into individual rows (e.g., separating chicken, rice, and soy milk).
3. **Dynamic Nutrition & Fat Loss Score (0–100)**:
   - Evaluates protein-to-calorie density, satiety/fullness index, whole-food quality, minimal added sugar, and cut suitability.
   - Evaluates protein-to-calorie density, dietary fiber (g), satiety/fullness index, whole-food quality, minimal added sugar, and cut suitability.
4. **Partner Tracking (Distinguishable Users)**:
   - Logs Telegram name/handle (`@username`) per row so you and your partner's logs are tracked independently in the same sheet.
5. **Real-time Cumulative Totals**:
   - Calculates today's cumulative calories and macros specifically for the person who logged.
6. **Encouraging Coach's Note**:
   - Personalized motivational remarks and fat-cutting tips delivered with every log.

---

## 📋 Google Sheet Structure (10 Columns)

## 📋 Google Sheet Structure (11 Columns)

When initialized, the bot checks your Google Sheet and automatically sets up bolded headers:

| #   | Column Name                | Example Value                         |
| --- | -------------------------- | ------------------------------------- |
| 1   | **Date & Time**            | `2026-09-07 12:45:00`                 |
| 2   | **User Name**              | `@james`                              |
| 3   | **Item Name**              | `Steamed Chicken Breast`              |
| 4   | **Category**               | `Lunch`                               |
| 5   | **Calories (kcal)**        | `240`                                 |
| 6   | **Protein (g)**            | `38.0`                                |
| 7   | **Carbohydrates (g)**      | `0.0`                                 |
| 8   | **Fat (g)**                | `4.5`                                 |
| 9   | **Short Description**      | `~150g skinless breast with cucumber` |
| 10  | **Nutrition Score (/100)** | `94`                                  |
| 9   | **Fiber (g)**              | `0.0`                                 |
| 10  | **Short Description**      | `~150g skinless breast with cucumber` |
| 11  | **Nutrition Score (/100)** | `94`                                  |

---

## 🚀 Setup Guide

### 1. Environment & Dependencies

Make sure you have Python 3.9+ (or 3.10+) installed.

```bash
# Clone or navigate to the directory
cd Fitness_Macro_tracker
cd "James Calories & Macros Tracker"
cd "James Boh Products/James Boh Macros Tracker"

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

### 2. Get Your Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to name your bot and choose a username (e.g., `MyMacroTrackerBot`).
3. BotFather will provide an API token (e.g. `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`).
4. Copy this token.

---

### 3. Get Your Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **"Get API key"** and create a key in a new or existing project.
4. Copy the API key.

---

### 4. Setup Google Service Account & Google Sheet

To allow the bot to read and write rows in your Google Sheet automatically:

#### A. Create a Google Cloud Project & Enable APIs

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Fitness-Tracker-Bot`).
3. In the search bar at the top, search for and enable these two APIs:
   - **Google Sheets API**
   - **Google Drive API**

#### B. Create a Service Account

1. In the left navigation menu, go to **IAM & Admin** > **Service Accounts**.
2. Click **"+ CREATE SERVICE ACCOUNT"**.
3. Enter a Service account name (e.g. `macro-tracker-sheets`) and click **Create and Continue**.
4. (Optional) For Role, select **Editor** or skip to finish. Click **Done**.

#### C. Download Credentials JSON

1. Click on the newly created Service Account email.
2. Navigate to the **"Keys"** tab.
3. Click **"ADD KEY"** > **"Create new key"**.
4. Choose **JSON** and click **Create**.
5. A `.json` file will download to your computer.
6. Rename this downloaded file to **`service_account.json`** and move it into the `Fitness_Macro_tracker` root directory.
7. Rename this downloaded file to **`service_account.json`** and move it into the project root directory (`James Calories & Macros Tracker`).
8. Note down the service account email inside this file (it looks like `macro-tracker-sheets@<your-project-id>.iam.gserviceaccount.com`).

#### D. Create & Share Your Google Sheet

1. Open [Google Sheets](https://sheets.new) and create a new spreadsheet.
2. Name the spreadsheet (e.g., `Macros & Calories Tracker`).
3. Click the **"Share"** button in the top right.
4. Paste the **Service Account email** you noted in the step above.
5. Set the permission to **Editor** and uncheck "Notify people" (since it's a bot account), then click **Share**.

---

### 5. Configure Environment Variables

Copy `.env.template` to `.env`:

```bash
cp .env.template .env
```

Open `.env` and fill in your details:

```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
GEMINI_API_KEY=AIzaSy...
GOOGLE_SHEET_NAME=Macros & Calories Tracker
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
TIMEZONE=Asia/Singapore
ALLOWED_USERS=
```

> **Tip on `TIMEZONE`**: Set to your local tz (e.g., `Asia/Singapore`, `America/New_York`, `Europe/London`) so timestamps and today's cumulative totals match your actual calendar day!
>
> **Tip on `ALLOWED_USERS`**: Optional security measure. Add comma-separated Telegram usernames (e.g. `james,partner_username`) to ensure only you and your partner can trigger the bot.

---

## 🏃 Running the Bot

Start the bot with:

```bash
source .venv/bin/activate
python bot.py
```

You should see:

```text
✅ Found Google Service Account file: 'service_account.json'
🚀 Starting Telegram Macros & Calories Logging Bot...
📅 Target Google Sheet: 'Macros & Calories Tracker'
🌐 Configured Timezone: Asia/Singapore
🤖 Bot is running! Press Ctrl+C to stop.
```

---

## 💬 Bot Commands & Interactions

| Action                 | Description                                                                             |
| ---------------------- | --------------------------------------------------------------------------------------- |
| **Send Photo**         | Sends food photo to Gemini 3.6 Flash Vision. Extracts macros, score, and logs to sheet. |
| **Send Text**          | E.g. _"Chicken breast with sweet potato and black coffee"_. Breaks down items and logs. |
| `/today` or `/summary` | Shows cumulative calories, protein, carbs, and fat logged by you today.                 |
| `/start`               | Displays welcome message and instructions.                                              |
| `/help`                | Detailed guide on logging tips and formatting.                                          |

---

## 🧪 Running Tests

Run the included test suite to verify schemas and aggregation logic:

```bash
source .venv/bin/activate
python -m unittest test_bot.py
```
