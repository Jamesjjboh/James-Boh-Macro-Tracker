# Changelog

All notable changes to the **James Boh Macro Tracker** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
