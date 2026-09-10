#!/usr/bin/env python3
"""
Migration Utility: Google Sheets to Cloud Firestore
Migrates historical meal logs from Google Sheets into Firestore multi-tenant subcollections.
Groups rows with matching Date & Time into unified composite meal documents.

Usage:
    python scripts/migrate_sheets_to_firestore.py [--chat-id CHAT_ID] [--username USERNAME]
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
import json
import os
import sys

# Add parent directory to path to import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import gspread
from google.cloud import firestore
from google.oauth2 import service_account

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "James Boh Macro Tracker").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()


def get_sheets_rows():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        gc = gspread.service_account_from_dict(info, scopes=scopes)
    elif os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        gc = gspread.service_account(filename=GOOGLE_SERVICE_ACCOUNT_FILE, scopes=scopes)
    else:
        raise FileNotFoundError(f"Service account file not found: {GOOGLE_SERVICE_ACCOUNT_FILE}")

    sh = gc.open(GOOGLE_SHEET_NAME)
    ws = sh.sheet1
    return ws.get_all_values()


async def migrate(target_chat_id: int, filter_username: str = None):
    print(f"📖 Fetching rows from Google Sheet '{GOOGLE_SHEET_NAME}'...")
    all_rows = get_sheets_rows()
    if not all_rows or len(all_rows) <= 1:
        print("⚠️ No data rows found in Google Sheet.")
        return

    data_rows = all_rows[1:]
    print(f"🔍 Found {len(data_rows)} raw food item rows.")

    # Group rows by (timestamp_str, user_name)
    meals_grouped = defaultdict(list)
    for row in data_rows:
        if len(row) < 8:
            continue
        dt_str = str(row[0]).strip()
        user = str(row[1]).strip()
        if filter_username and filter_username.lower().lstrip("@") not in user.lower():
            continue
        meals_grouped[(dt_str, user)].append(row)

    print(f"📦 Grouped into {len(meals_grouped)} distinct meal sessions.")

    # Initialize Firestore client
    creds = None
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/datastore"],
        )
    elif os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/datastore"],
        )

    db = firestore.AsyncClient(project="james-boh-macro-tracker", credentials=creds, database="(default)")

    # Ensure user document exists
    user_ref = db.collection("users").document(str(target_chat_id))
    user_doc = await user_ref.get()
    now_iso = datetime.now().isoformat()
    if not user_doc.exists:
        await user_ref.set({
            "chat_id": target_chat_id,
            "user_name": filter_username or f"user_{target_chat_id}",
            "first_name": "James",
            "created_at": now_iso,
            "last_active_at": now_iso,
            "daily_calorie_target": 2000,
            "daily_protein_target": 150,
            "daily_fiber_target": 25,
            "timezone": "Asia/Singapore",
        })
        print(f"👤 Created user document in Firestore: users/{target_chat_id}")

    meals_ref = user_ref.collection("meals")
    imported_count = 0

    for (dt_str, user), items_rows in meals_grouped.items():
        # Parse date and time
        parts = dt_str.split(" ")
        date_str = parts[0] if len(parts) > 0 else "2026-09-09"
        time_str = parts[1] if len(parts) > 1 else "12:00:00"

        items_list = []
        tot_cals = 0.0
        tot_pro = 0.0
        tot_carbs = 0.0
        tot_fat = 0.0
        tot_fiber = 0.0
        scores = []
        category = items_rows[0][3] if len(items_rows[0]) > 3 else "Meal"

        for r in items_rows:
            name = str(r[2]).strip()
            cat = str(r[3]).strip() if len(r) > 3 else "Meal"
            cals = float(str(r[4]).replace(",", "").strip() or 0)
            pro = float(str(r[5]).replace(",", "").strip() or 0)
            carbs = float(str(r[6]).replace(",", "").strip() or 0)
            fat = float(str(r[7]).replace(",", "").strip() or 0)
            fiber = float(str(r[8]).replace(",", "").strip() or 0) if len(r) > 8 else 0.0
            desc = str(r[9]).strip() if len(r) > 9 else ""
            score = int(str(r[10]).replace(",", "").strip() or 70) if len(r) > 10 else 70

            tot_cals += cals
            tot_pro += pro
            tot_carbs += carbs
            tot_fat += fat
            tot_fiber += fiber
            scores.append(score)

            items_list.append({
                "item_name": name,
                "category": cat,
                "calories": cals,
                "protein": pro,
                "carbohydrates": carbs,
                "fat": fat,
                "fiber": fiber,
                "short_description": desc,
                "nutrition_score": score,
            })

        avg_score = round(sum(scores) / len(scores)) if scores else 70

        doc_data = {
            "date": date_str,
            "time": time_str,
            "timestamp": dt_str,
            "user_name": user,
            "category": category,
            "total_calories": round(tot_cals, 1),
            "total_protein": round(tot_pro, 1),
            "total_carbs": round(tot_carbs, 1),
            "total_fat": round(tot_fat, 1),
            "total_fiber": round(tot_fiber, 1),
            "nutrition_score": avg_score,
            "motivational_note": "Imported from Google Sheets.",
            "raw_text_prompt": None,
            "items": items_list,
        }

        new_doc = meals_ref.document()
        doc_data["id"] = new_doc.id
        await new_doc.set(doc_data)
        imported_count += 1
        print(f"  ✅ Migrated meal: {date_str} {time_str} | {len(items_list)} item(s) | {tot_cals:.0f} kcal -> users/{target_chat_id}/meals/{new_doc.id}")

    print(f"\n🎉 Migration complete! Successfully imported {imported_count} meals ({len(data_rows)} items) into users/{target_chat_id}/meals.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Google Sheets logs to Cloud Firestore")
    parser.add_argument("--chat-id", type=int, default=1000001, help="Target Telegram Chat ID")
    parser.add_argument("--username", type=str, default="jamesjjboh", help="Filter username (e.g. jamesjjboh)")
    args = parser.parse_args()

    asyncio.run(migrate(target_chat_id=args.chat_id, filter_username=args.username))
