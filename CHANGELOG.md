# Changelog

All notable changes to the **James Boh Macro Tracker** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.5] - 2026-09-15

### Added
- **Multi-Photo Album Uploads (Send Multiple Photos as One Meal)**: Select and send 2–5 photos simultaneously in a single Telegram message (e.g., multi-dish restaurant feasts, banchan, sides, drinks). The bot aggregates all images and evaluates them collectively in **one unified Gemini AI vision session**, recording the spread as **one single meal** with accurate calorie totals and zero duplicates.
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
