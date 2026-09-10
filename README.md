# 🥗 James Boh Macro Tracker

_Created by James Boh ([LinkedIn](https://www.linkedin.com/in/jamesboh/) | [GitHub Repository](https://github.com/Jamesjjboh/James-Boh-Macro-Tracker))_

A serverless, multi-tenant cloud nutrition platform on Telegram powered by **Google Cloud Firestore**, **Gemini 3.6 Flash Vision**, and **Google Cloud Run**. 

Effortlessly log daily calories, macros (Protein, Carbs, Fat, Fiber), and an intelligent **Nutrition & Fat Loss Score** (0–100) with complete data privacy, visual analytics, and smart editing.

---

## 🌟 Key Capabilities

### 1. Multi-Tenant Cloud Architecture & Data Privacy
- 🔒 **Private Tenant Siloing**: Powered by **Google Cloud Firestore (Firebase Native)** in Singapore (`asia-southeast1`). Each user's logs are completely isolated (`users/{chat_id}/meals`). User A can never see User B's entries.
- 🛡️ **Singapore PDPA Compliant**: Built with Privacy-by-Design. Includes transparent `/privacy` notice, data portability via `/export` (instant CSV download), and the Right to Erasure (`/delete` permanent data wipe).
- ☁️ **24/7 Uptime with Zero Idle Cost**: Runs on Google Cloud Run serverless webhooks with scale-to-zero ($0/mo cost).

### 2. Zero-Command Conversational Intent Router
No need to memorize slash commands! The bot understands natural language directly:
- **Food Logging**: *"Chicken rice with teh o kosong"*, *"2 boiled eggs and avocado toast"*
- **Visual Analytics**: *"How did I do this week?"*, *"Show my monthly charts"*
- **Smart Editing**: *"Actually no sugar in the tea"*, *"Wait, change chicken to 200g"*
- **Undo**: *"Undo that"*, *"Delete my last meal"*
- **Data Export**: *"Can I export my data?"*, *"Download my logs as CSV"*
- **Privacy Inquiries**: *"Is my data private?"*, *"Where is my data stored?"*

### 3. Visual Nutrition Analytics & Trend Cards
- 📊 **High-Res Dark-Mode Charts (`matplotlib`)**: Generates Telegram dark-mode dashboard cards directly in chat:
  - **Panel 1**: Daily Calories vs Target line (color-coded for on-track vs surplus).
  - **Panel 2**: Macronutrient Energy Split Donut (% Protein, % Carbs, % Fat).
  - **Panel 3**: Daily Protein & Dietary Fiber Intake Bars with recommended baseline lines.
- 💡 **Executive Coaching Digest**: Computes weekly averages, consistency score (% days on target), and actionable fat-loss / muscle retention coaching tips.

### 4. Smart Meal Editing & Undo
- ✏️ **Gemini Recalculation Engine**: Feed adjustments naturally (e.g., *"Ate only half the rice"* or *"Replace whole milk with oat milk"*). Gemini updates the exact itemized breakdown and recalculates today's totals atomically.
- ↩️ **Instant Rollback (`/undo`)**: Quickly remove an accidental photo upload or duplicate entry with one tap.

### 5. Multi-Modal Vision & Composite Breakdown
- 📸 **Photo Logging with Caption Override**: Snap a plate or beverage. Captions act as ground truth (e.g., *"Soy milk no sugar"*).
- 🥣 **Itemized Breakdown**: Separates composite meals into distinct components with individual macro profiles.
- 🥗 **Dietary Fiber & Nutrition Score (0–100)**: Evaluates protein-to-calorie density, dietary fiber, whole food quality, and satiety.

---

## 💬 Bot Commands & Interactions

| Command | Natural Language Trigger | Description |
| :--- | :--- | :--- |
| **Send Photo** | _(Upload image)_ | Multimodal AI vision analysis with macro breakdown. |
| **Send Text** | *"Chicken rice with iced tea"* | Zero-shot food parsing and automatic macro estimation. |
| `/today` | *"What did I eat today?"* | Cumulative calorie, protein, carb, fat, and fiber totals for today. |
| `/analytics` | *"How did I do this week?"* | 7-day dark-mode chart card, averages, and coaching digest. |
| `/monthly` | *"Show my monthly charts"* | 30-day macro trend analysis and consistency score. |
| `/edit` | *"Actually no sugar"* | Modify portion sizes or ingredients of your last logged meal. |
| `/undo` | *"Undo that"* | Instantly deletes your most recently logged meal. |
| `/log <food>` | — | Explicit text logging fallback. |
| `/export` | *"Download my data as CSV"* | Downloads your complete meal history as an 11-column CSV file. |
| `/privacy` | *"Is my data private?"* | Full transparency on Singapore GCP storage and AI data handling. |
| `/delete` | *"Delete my account"* | Permanently erases your user profile and all meal history. |
| `/changelog` | — | Displays recent product release notes and updates. |
| `/help` | — | Detailed user guide and tips. |

---

## 🛠️ Architecture & Tech Stack

```
Telegram Client (Mobile / Desktop)
       │ (HTTPS / TLS 1.3)
       ▼
Google Cloud Run (Serverless Webhook in asia-southeast1)
       │
       ├──> Natural Language Intent Router
       │
       ├──> Google Gemini 3.6 Flash Vision (Multimodal Nutrition Inference)
       │
       ├──> Google Cloud Firestore (Multi-Tenant Private NoSQL Store)
       │       └── users/{chat_id}/meals/{meal_id}
       │
       ├──> Headless Matplotlib (Dark-Mode Analytics Chart Engine)
       │
       └──> Google Sheets API (Optional Dual-Write Mirror)
```

* **Backend Framework**: `python-telegram-bot` (v20+ async)
* **Primary Database**: `google-cloud-firestore` (Native NoSQL in Singapore)
* **AI Vision Inference**: `google-genai` (Gemini 3.6 Flash with Pydantic JSON Schema)
* **Visualization**: `matplotlib` (Headless Agg engine)
* **Cloud Infrastructure**: Google Cloud Run + Artifact Registry + Cloud Build
* **Containerization**: Docker (Python 3.11-slim)

---

## 🚀 Setup & Local Development

### 1. Clone & Virtual Environment
```bash
git clone https://github.com/Jamesjjboh/James-Boh-Macro-Tracker.git
cd James-Boh-Macro-Tracker

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.template` to `.env`:
```bash
cp .env.template .env
```

Fill in your secrets:
```ini
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
TIMEZONE=Asia/Singapore
ADMIN_USERS=jamesjjboh
ALLOWED_USERS=
```
* **`ADMIN_USERS`**: Telegram usernames authorized for administrative announcements (`/broadcast`, `/release`).
* **`ALLOWED_USERS`**: Leave empty to allow any Telegram user to log meals privately.

### 3. Google Cloud Permissions
Grant `roles/datastore.user` to your service account:
```bash
gcloud projects add-iam-policy-binding <your-project-id> \
  --member="serviceAccount:<your-service-account>@<your-project-id>.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

### 4. Running the Bot Locally
```bash
source .venv/bin/activate
python bot.py
```

---

## 🧪 Automated Testing

Run the comprehensive unit and integration test suite:
```bash
source .venv/bin/activate
python -m unittest test_bot.py -v
```

Tests cover:
* Pydantic schema validation & score boundaries
* Natural Language Intent Classifier
* Headless dark-mode chart generation (valid PNG byte stream)
* Coaching digest calculations & adherence metrics
* Live Firestore CRUD, daily totals, CSV export, and deletion

---

## 📦 Historical Data Migration

To migrate historical meal rows from an existing Google Sheet into Firestore:
```bash
python scripts/migrate_sheets_to_firestore.py --chat-id <your-telegram-chat-id> --username <your-username>
```

---

## 📄 License & Disclaimer

Created by [James Boh](https://www.linkedin.com/in/jamesboh/). Released under the MIT License.

_Disclaimer: Calorie and macronutrient estimates are AI approximations for informational and wellness purposes only. Not intended as clinical dietary advice or medical treatment._
