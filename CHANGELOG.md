# Changelog

All notable changes to the **James Boh Macro Tracker** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.1] - 2026-10-10

### Fixed
- **Robust Intent Detection for Meal Log Quote-Replies**: Resolved an issue where swiping to reply to the bot's generated meal log message (or photo) with conversational coaching questions (e.g. *"how I can improve nutrition score or lower calories"*, *"how to make it better"*, *"can you make this healthier"*, *"why is my score low"*) fell through to `execute_meal_edit` and triggered unwanted meal recalculation cards.
- **Explicit Edit Guards & Topic Disambiguation**: Added explicit prefix guards (`actually`, `wait`, `change to`, `move to`, `no sugar`, `ate half`, `instead of`) and balanced query matching requiring action verbs + nutrition topics, preventing conversational coaching from ever colliding with standard meal editing or broad status inquiries (*"how did I do this week"*).
- **Diagnostic Cloud Run Logging**: Added structured runtime logging in `handle_text` capturing incoming text, reply context, and coaching classification decisions.
- **Automated Regression Tests**: Expanded `test_is_meal_coaching_query` and added `test_quote_reply_to_photo_routes_to_coaching` and `test_quote_reply_to_bot_card_fallback_routes_to_coaching`, expanding the test suite to 30 passing tests.

---

## [1.5.0] - 2026-10-09

### Added
- **Interactive Meal Coaching & Score Optimization (`/coach`, `/improve`)**: Added dedicated culinary and nutrition coaching agent. Users can swipe to reply to any meal card or photo (or ask in chat) with phrases like *"how can I improve nutrition score or lower calories"*, *"how to make this healthier"*, or *"tips to lower calories"*.
- **Intelligent Quote-Reply Coaching Disambiguation**: Differentiated coaching/advice queries from edit instructions (`is_meal_coaching_query`). Swiping to ask for improvement suggestions no longer falsely triggers meal recalculation, database overwrite, or raw meal dump. Instead, Gemini delivers targeted culinary hacks:
  - 🥗 **How to Boost Nutrition & Cut Score**: Actionable steps to increase protein density, fiber/greens, and whole food quality.
  - 📉 **How to Lower Calories**: Practical swaps (e.g. half carbs/rice, sauce on side, skinless chicken) with estimated calorie savings (e.g. -150 to -250 kcal).
  - 🎯 **Estimated Impact**: Projected calorie reduction and score boost (e.g. ~420 kcal down from 650 kcal | Score 85+ up from 68).
  - 💡 **One-Tap Edit Follow-up**: Swipe to reply to apply any of the suggested tweaks directly to the log.
- **Typo & Colloquial Phrasing Tolerance**: Added automatic normalization for common typos (e.g. *"how ti mprove nutrition score"*, *"calroies"*, smart curly apostrophes).
- **Automated Regression & Unit Tests**: Added `test_is_meal_coaching_query` and `test_quote_reply_routes_to_coaching` in `test_bot.py`, bringing the automated test suite to 28 passing tests.

---

## [1.4.0] - 2026-10-09

### Added
- **Natural Language Historical Date Attribution from Captions (`extract_historical_meal_date`)**: Users who log late-night meals past midnight (e.g. at 1:11am) with captions such as *"This was yesterday's dinner"*, *"Dinner last night"*, or *"Logged for yesterday"* now have their meals automatically attributed to the intended historical date (`yesterday`). Cumulative totals, subtotals, and daily summaries immediately reflect the correct target date.
- **Quote-Reply Date Override & Meal Moving (`extract_date_override`)**: Users can now swipe to reply to any historical meal card to move it across dates using natural language (e.g. *"Change date to yesterday"*, *"Move to yesterday"*, *"This was for yesterday"*, *"Date: 2026-10-08"*). Firestore `date` and `timestamp` fields are atomically updated and daily totals recalculated.
- **Dynamic Yesterday & Historical Summary Formatting**: `format_telegram_reply` now dynamically renders clear headers when meals are attributed to past dates: *"Meal Logged for Yesterday (Thu, 08 Oct)!"* and *"Yesterday's Cumulative Totals"* instead of confusingly showing today's date and totals.
- **Automated Regression & Unit Tests**: Added `test_extract_historical_meal_date`, `test_extract_date_override`, and `test_format_telegram_reply_yesterday` in `test_bot.py`, bringing the automated test suite to 26 passing tests.

### Changed
- **Smart Apostrophe Normalization**: Updated `parse_historical_date` and date extractors to automatically normalize curly iOS/macOS smart apostrophes (`’` $\rightarrow$ `'`) and expanded phrase matching to recognize *"last night"*.

---

## [1.3.9] - 2026-10-06

### Fixed
- **Meal Edit Flow Without Category Overrides**: Fixed an indentation bug in `edit_meal_with_gemini` where `return analysis` was nested inside the `if category_override:` block. When users edited meals without specifying a category change (e.g. adding dishes, portion tweaks, beverage additions), the return was inadvertently skipped, causing the bot to exhaust all fallback models and falsely report a 503 AI high-demand error.
- **Automated Regression Test**: Added `test_edit_meal_without_category_override` in `test_bot.py` to guarantee non-category meal edits return immediate recalculated analyses.

---

## [1.3.8] - 2026-10-05

### Added
- **High-Speed Vision Image Downscaling (Pillow)**: Added `optimize_image_for_vision` to automatically downscale 3MB–8MB high-res smartphone photos (4032x3024) to a maximum dimension of 1280px with JPEG quality 85. Slashing payload by >90% (from ~5MB down to ~150KB) and reducing Gemini vision token tiling from ~6,192 tokens to ~1,032 tokens, cutting 2–4 seconds off vision inference with zero degradation in food accuracy.
- **EXIF Transposition Support**: Added `ImageOps.exif_transpose` to preserve true camera orientation for smartphone photos before AI analysis.
- **Automated Image Optimization Unit Test**: Added `test_optimize_image_for_vision` to verify downscaling, compression ratio, and dimensional constraints.

### Changed
- **Ultra-Low-Latency Model Cascade**: Reordered `GEMINI_CANDIDATE_MODELS` to prioritize `gemini-3.5-flash-lite` and `gemini-flash-lite-latest` as primary flagship models. Provides sub-second text logging (< 0.9s), 1.7s meal edits, and sub-3.0s vision inference while avoiding global 503 capacity bottlenecks on heavy models.
- **Fail-Fast Fallback Delays**: Reduced per-model timeouts from 20.0s to 12.0s and sleep delays from 0.5s to 0.1s to guarantee rapid failover across TPU clusters.
- **Streamlined Status Messaging**: Replaced sequential status message creation, deletion, and re-creation in `handle_photo` with a single direct status card, saving 2 redundant Telegram network roundtrips (~300ms).
- **Tightened Album Debounce Window**: Optimized multi-photo media group coordinator sleep loop to 0.2s with a 0.6s idle threshold (down from 1.0s), capturing 100% of album photos while eliminating 600ms of dead wait time.

---

## [1.3.7] - 2026-10-01

### Added
- **Instant Text-First Analytics Dashboards (< 400ms)**: Replaced slow image-generation bottlenecks with zero-latency in-chat visual dashboards. Displays daily averages, adherence %, personalized coach feedback, and an interactive **Day-by-Day Calorie Tracker** using clean Unicode progress bars (`🟩🟩🟩⬜`, `🟨`, `🟧`, `⬜`).
- **On-Demand High-Res Chart Generation**: Users can tap `[ 🖼️ View High-Res Chart ]` to generate and deliver the full dark-mode 3-panel Matplotlib PNG chart whenever they specifically want the visual graphic.
- **Conversational Historical Date Lookups**: Users can inspect any past day naturally (e.g. *"What did I eat yesterday?"*, *"Show meals on 25 Sep"*, *"Calories last Friday"*, `2026-09-25`). Returns a consolidated single-day meal card with itemized items, categories, times, cumulative macros, and a calorie-weighted nutrition quality badge.
- **Conversational Analytical Q&A Agent**: Users can ask natural analytical questions about their past eating habits and goal progress (e.g. *"Did I hit my calorie goals for the past month?"*, *"How many days was I on target this week?"*, *"What was my average protein last week?"*). Gemini 3.6 Flash analyzes verified Firestore historical records to cite exact percentages, days on target, and actionable coaching insights.

---

## [1.3.6] - 2026-10-01

### Added
- **Meal Subtotal Nutrition & Cut Score**: When a logged meal contains multiple items, the `🥣 Meal Subtotal` section now calculates and displays an aggregate **calorie-weighted Nutrition & Cut Score** (out of 100) with dynamic status badge (🟢 `≥80`, 🟡 `55–79`, 🔴 `<55`).
- **Daily Cumulative Nutrition & Cut Score**: Both the post-meal confirmation card (`Today's Cumulative Totals`) and the `/today` command now display the daily aggregate calorie-weighted nutrition score, providing instant holistic diet quality feedback.
- **Calorie-Weighted Aggregation**: Prevents small condiments or low-calorie items from distorting the overall meal or daily score ($$Score = \frac{\sum calories_i \times score_i}{\sum calories_i}$$), with graceful fallback to simple average for 0-calorie items.

---

## [1.3.5] - 2026-09-15

### Added
- **Calorie & Macro Target Management (`/targets`, `/goals`)**: Interactive goal setup with 1-tap presets (`[ 📉 Fat Loss (1,700 kcal) ]`, `[ ⚖️ Maintenance (2,000 kcal) ]`, `[ 💪 Lean Bulk (2,400 kcal) ]`, `[ ✏️ Custom Targets ]`) or direct numerical parameters (`/targets 1800 140 25`).
- **Baseline Mode vs. Custom Target Differentiation**: Added `targets_set` flag to user profiles. When unset, analytics charts and coaching digests clearly label lines and metrics as `Baseline` (e.g., `Baseline: 2,000 kcal`, `Protein (Baseline: 150g)`), eliminating uncalibrated critiques.
- **Analytics Inline Target Button**: Direct `[ 🎯 Set Daily Targets ]` (or `[ 🎯 Edit Targets ]`) button rendered directly beneath weekly and monthly analytics cards.
- **Natural Language Target Intent Routing**: Recognizes phrases like *"change targets"*, *"set goals"*, *"my targets"*, *"calorie target"*, routing straight to target configuration.
- **User Feedback & Suggestions System (`/feedback`, `/suggest`)**: Built-in channel for users to submit ideas, bug reports, and feature requests. Supports one-shot commands, interactive prompts, and natural language triggers (*"i have a suggestion"*, *"report a bug"*, *"feedback: ..."*).
- **Real-Time Admin Push Notifications**: Instant Telegram DM notifications pushed directly to the administrator (`@jamesjjboh`) the moment feedback is submitted.
- **Admin Feedback Viewer (`/viewfeedback`, `/feedbacks`)**: On-demand inspection tool for administrators to browse recent feedback submissions in Telegram.
- **Cloud Firestore Feedback Store**: Secure persistence under `feedback/{feedback_id}` tracking user ID, handle, text, category, and submission timestamp.
- **Admin Analytics & Growth Dashboard (`/metrics`, `/admin`)**: Real-time platform command for administrators to monitor user growth (Total Users, New Signups), activation rate (users logging $\ge 1$ meal), engagement (DAU, WAU, MAU, DAU/MAU habit stickiness), total meal & item volume, and active user leaderboard.
- **Continuous User Activity Tracking**: Lightweight non-blocking activity touchpoint updates `last_active_at` on every message or photo upload, ensuring 100% accurate DAU/WAU metrics.

### Fixed
- **Analytics Doughnut Chart Percentage Cut-Off**: Adjusted `pctdistance=0.76` and inner ring width to `0.42` in `AnalyticsService.generate_trend_chart` so percentages sit dead-center in the colored arcs. Swapped text color to high-contrast `#0f172a`, preventing digits from blending into dark backgrounds.

---

## [1.3.4] - 2026-09-14

### Added
- **Multi-Photo Album Uploads (Send Multiple Photos as One Meal)**: Asynchronous album coordinator (`MediaGroupBuffer`) aggregates 2–5 photos sent simultaneously in a single Telegram message. Evaluates the entire multi-dish spread collectively in one unified Gemini vision session, eliminating duplicate entries and preventing inflated calorie counts.
- **Quote-Reply Historical Meal Editing (`find_meal_by_message_id`)**: Users can swipe/reply to ANY past meal photo or status card to modify ingredients or portions. Tracks `user_message_id` and `bot_message_id` in Firestore to accurately pinpoint and edit historical meals.
- **Deterministic Category Overrides**: Automatically detects and extracts meal category modifications from natural language phrases (e.g. *"this entry is for lunch"*, *"change to dinner"*, *"mark as snack"*), updating both item and document category fields.

---

## [1.3.3] - 2026-09-11

### Added
- **Streamlined Onboarding Experience**: Replaced lengthy technical `/start` manual with a minimalist, welcoming 3-line message focused on immediate action.
- **Natural Language Daily Totals**: Added conversational intent routing for daily summaries (e.g. *"what are my calories today?"*, *"today summary"*, *"today"*).

### Fixed
- **Atomic Delivery Rollback**: Automatically rolls back Firestore meal creation if confirmation message delivery to Telegram fails completely, preventing ghost meal entries.

---

## [1.3.2] - 2026-09-10

### Fixed
- **Telegram Connection Resilience**: Configured explicit 20.0s `connect_timeout` and 30.0s `read_timeout` on `HTTPXRequest` to prevent outbound Telegram socket timeouts during container cold-starts.
- **Graceful Message Delivery Fallback**: Added auto-fallback from `edit_message_text` to `reply_text` if the original status message cannot be edited due to transient Telegram network glitches.

---

## [1.3.1] - 2026-09-10

### Fixed
- **Gemini 503 UNAVAILABLE Resilience**: Replaced throttled `gemini-3-flash-preview` endpoint with a multi-tier fallback cascade across 5 battle-tested models on separate physical TPU clusters (`gemini-3.6-flash` -> `gemini-3.5-flash` -> `gemini-3.5-flash-lite` -> `gemini-flash-lite-latest` -> `gemini-3.1-flash-lite`).
- **Faster Failover Latency**: Reduced per-model timeout from 30.0s to 20.0s with retry jitter, allowing the bot to pivot seamlessly within seconds without leaving the user waiting.
- **Friendly High-Demand Messaging**: Masked transient Google API 503 capacity spikes with reassuring, formatted Telegram notifications instead of raw JSON tracebacks.

---

## [1.3.0] - 2026-09-10

### Added
- **Google Cloud Firestore Migration**: Migrated primary database from single-user Google Sheets to Google Cloud Firestore (Firebase Native mode in Singapore `asia-southeast1`).
- **Multi-Tenant Privacy & Data Siloing**: Each Telegram user owns an isolated document store (`users/{chat_id}/meals`). No user can view or query another's nutritional logs.
- **Natural Language Intent Router**: Seamless zero-command routing for food logging, editing, undo, analytics, CSV export, and privacy inquiries.
- **Visual Nutrition Analytics**: Generates high-res dark-mode 3-panel charts (`matplotlib`) for 7-day and 30-day trends (Calories vs Target, Protein & Fiber, Energy Distribution Donut) with coaching digests via `/analytics`, `/weekly`, `/monthly`.
- **Smart Meal Editing & Undo**: Natural language corrections (e.g. *"actually no sugar in the tea"*, *"change chicken to 200g"*) and `/edit` & `/undo` commands.
- **Data Portability (/export)**: Generates and delivers full 11-column `.csv` downloads in Telegram.
- **Data Governance & Legal Compliance**: Singapore PDPA compliance, transparent `/privacy` notice, and self-service account & data wipe (`/delete`).
- **Historical Migration Utility**: Added `scripts/migrate_sheets_to_firestore.py` to seamlessly backfill historical Google Sheets entries into Firestore.

---

## [1.2.0] - 2026-09-10

### Added
- **24/7 Google Cloud Run Deployment**: Containerized the application and deployed to Google Cloud Run in Singapore (`asia-southeast1`).
- **Serverless Webhook Architecture**: Transitioned from battery-draining continuous polling to an event-driven webhook HTTP server using Tornado/asyncio.
- **Scale-to-Zero ($0/mo cost)**: Configured autoscaling (`min-instances = 0`) so the container sleeps when idle and wakes in <1s upon receiving a Telegram update.
- **Docker Containerization**: Added production `Dockerfile` (Python 3.11-slim) and `.dockerignore` for immutable, reproducible builds.
- **Zero-File Credentials Handling**: Enhanced `GoogleSheetsService` to load Google Service Account credentials directly from the `GOOGLE_SERVICE_ACCOUNT_JSON` environment variable.
- **In-App Changelog Command**: Added `/changelog` and `/updates` commands in Telegram for instant visibility of new features.

### Fixed
- **Port 8080 Startup Probe**: Configured webhook server to bind to `0.0.0.0:8080` immediately upon container startup, ensuring Cloud Run health check probes pass with zero downtime.

---

## [1.1.0] - 2026-09-09

### Added
- **Dietary Fiber Tracking**: Expanded Google Sheets schema to 11 columns, adding Column 9 for Dietary Fiber (g).
- **Multi-Model Fallback Chain**: Implemented intelligent model failover (`gemini-3.6-flash` -> `gemini-3-flash-preview`) with a 30-second timeout to prevent 503 Service Unavailable stalls.
- **Photo Caption Ground Truth Override**: User photo captions (e.g., *"Lunch: steak with salad, unsweetened soy milk"*) now override visual guesses as absolute ground truth.
- **Smart Meal Categorization**: Beverages and side items consumed with a meal automatically inherit the parent meal category (e.g. Lunch).

### Fixed
- **Off-by-One Google Sheets Bug**: Fixed row counting logic when querying daily macro totals to accurately reflect the user's current day intake.

---

## [1.0.0] - 2026-09-08

### Added
- **Initial MVP Release**: Headless nutrition and calorie tracking bot on Telegram.
- **Gemini 3.6 Flash Vision**: Zero-shot meal recognition and nutritional estimation from photos and text descriptions.
- **Structured Pydantic Output**: Strictly typed JSON response parsing for calories, protein, carbs, fat, and nutrition score.
- **Google Sheets Database Integration**: Real-time appending of logged items and daily totals using `gspread`.
- **Itemized Telegram Cards**: Clean HTML output formatting with item-by-item breakdown, daily macro progress, and AI coaching remarks.
- **Timezone Awareness**: Singapore timezone (`Asia/Singapore`) localization for all timestamps and daily totals.
