"""
Unit and integration tests for Telegram Macros & Calories Logging Bot.
Validates Pydantic schemas, intent classification, analytics chart rendering,
Firestore CRUD operations, CSV exports, and privacy workflows.
"""

import asyncio
import os
import unittest
from unittest.mock import MagicMock

from bot import (
    FoodItem,
    MealAnalysisResponse,
    GoogleSheetsService,
    FirestoreService,
    AnalyticsService,
    SHEET_HEADERS,
    format_telegram_reply,
    classify_text_intent,
)


class TestMacroTrackerBot(unittest.TestCase):
    def test_pydantic_schema_validation(self):
        """Test valid Pydantic model instantiation and constraints."""
        valid_json = """
        {
            "items": [
                {
                    "item_name": "Hainanese Steamed Chicken Breast",
                    "category": "Lunch",
                    "calories": 240.0,
                    "protein": 38.0,
                    "carbohydrates": 0.0,
                    "fat": 4.5,
                    "fiber": 0.0,
                    "short_description": "~150g steamed skinless chicken breast with sliced cucumbers",
                    "nutrition_score": 94
                },
                {
                    "item_name": "Fragrant Chicken Rice",
                    "category": "Lunch",
                    "calories": 260.0,
                    "protein": 5.0,
                    "carbohydrates": 52.0,
                    "fat": 4.0,
                    "fiber": 1.5,
                    "short_description": "1 bowl (~180g) white rice cooked in chicken broth",
                    "nutrition_score": 68
                }
            ],
            "motivational_note": "Great lean protein choice! The steamed chicken breast keeps your protein high while keeping calories low."
        }
        """
        response = MealAnalysisResponse.model_validate_json(valid_json)
        self.assertEqual(len(response.items), 2)
        self.assertEqual(response.items[0].item_name, "Hainanese Steamed Chicken Breast")
        self.assertEqual(response.items[0].nutrition_score, 94)
        self.assertEqual(response.items[0].fiber, 0.0)
        self.assertEqual(response.items[1].fiber, 1.5)
        self.assertEqual(response.items[1].calories, 260.0)

    def test_nutrition_score_boundaries(self):
        """Test that nutrition score enforces 0 <= score <= 100."""
        item = FoodItem(
            item_name="Egg Whites",
            category="Breakfast",
            calories=50,
            protein=10,
            carbohydrates=1,
            fat=0,
            fiber=0,
            short_description="3 large egg whites",
            nutrition_score=98,
        )
        self.assertEqual(item.nutrition_score, 98)

        with self.assertRaises(Exception):
            FoodItem(
                item_name="Invalid Food",
                category="Snack",
                calories=100,
                protein=5,
                carbohydrates=10,
                fat=2,
                fiber=0,
                short_description="Test",
                nutrition_score=105,
            )

    def test_sheet_headers_integrity(self):
        """Verify the 11 columns with Fiber."""
        expected = [
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
        self.assertEqual(SHEET_HEADERS, expected)
        self.assertEqual(len(SHEET_HEADERS), 11)

    def test_format_telegram_reply(self):
        """Verify HTML formatting produces expected structure and escapes special chars."""
        item1 = FoodItem(
            item_name="Grilled Salmon & Asparagus",
            category="Dinner",
            calories=380.0,
            protein=42.0,
            carbohydrates=4.0,
            fat=18.0,
            fiber=3.5,
            short_description="200g Atlantic salmon fillet with grilled asparagus & lemon",
            nutrition_score=90,
        )
        totals = {
            "calories": 1450.0,
            "protein": 130.5,
            "carbs": 110.0,
            "fat": 42.0,
            "fiber": 22.0,
            "item_count": 4,
        }
        msg = format_telegram_reply(
            user_name="@jamesboh",
            datetime_str="2026-09-07 19:30:00",
            items=[item1],
            today_totals=totals,
            motivational_note="Outstanding high-protein dinner! Healthy omega-3s and high satiety for fat loss.",
            sheet_saved=True,
        )
        self.assertIn("🍽️ <b>Meal Logged Successfully!</b>", msg)
        self.assertIn("@jamesboh", msg)
        self.assertIn("Grilled Salmon &amp; Asparagus", msg)
        self.assertIn("380 kcal", msg)
        self.assertIn("42.0g", msg)
        self.assertIn("3.5g", msg)
        self.assertIn("22.0 g", msg)
        self.assertIn("90/100", msg)
        self.assertIn("1450 kcal", msg)

    def test_natural_language_intent_classification(self):
        """Test routing of user text into appropriate command categories."""
        cases = [
            ("How did I do this week?", "analytics_7d"),
            ("Show my weekly trends", "analytics_7d"),
            ("Show my monthly charts", "analytics_30d"),
            ("Can I export my data?", "export"),
            ("Download my logs as CSV", "export"),
            ("Is my data private?", "privacy"),
            ("Where is my data stored?", "privacy"),
            ("Delete my account and all data", "delete"),
            ("Wipe all my data", "delete"),
            ("Undo that", "undo"),
            ("Delete last meal", "undo"),
            ("Actually no sugar in the tea", "edit"),
            ("Wait, change chicken to 200g", "edit"),
            ("Correction: brown rice, not white", "edit"),
            ("Chicken rice with iced lemon tea", "food_log"),
            ("2 hard boiled eggs with oatmeal and blueberries", "food_log"),
        ]
        for phrase, expected in cases:
            actual = classify_text_intent(phrase)
            self.assertEqual(actual, expected, f"Failed on '{phrase}': got {actual}, expected {expected}")

    def test_analytics_chart_generation(self):
        """Verify Matplotlib headless generation produces a non-empty PNG buffer."""
        mock_records = [
            {
                "date": "2026-09-08",
                "calories": 1850.0,
                "protein": 140.0,
                "carbs": 160.0,
                "fat": 50.0,
                "fiber": 24.0,
                "nutrition_score_sum": 85.0,
                "nutrition_score_count": 1,
            },
            {
                "date": "2026-09-09",
                "calories": 2100.0,
                "protein": 160.0,
                "carbs": 180.0,
                "fat": 62.0,
                "fiber": 28.0,
                "nutrition_score_sum": 90.0,
                "nutrition_score_count": 1,
            },
        ]
        png_bytes = AnalyticsService.generate_trend_chart(
            daily_records=mock_records,
            calorie_target=2000,
            protein_target=150,
            fiber_target=25,
            days_window=7,
        )
        self.assertIsInstance(png_bytes, bytes)
        self.assertGreater(len(png_bytes), 5000)
        # Verify PNG magic bytes (\x89PNG)
        self.assertTrue(png_bytes.startswith(b"\x89PNG"))

    def test_analytics_text_digest(self):
        """Verify coaching text summary accurately computes averages and adherence."""
        mock_records = [
            {
                "date": "2026-09-08",
                "calories": 1900.0,
                "protein": 150.0,
                "carbs": 160.0,
                "fat": 50.0,
                "fiber": 25.0,
                "nutrition_score_sum": 85.0,
                "nutrition_score_count": 1,
            },
            {
                "date": "2026-09-09",
                "calories": 2000.0,
                "protein": 155.0,
                "carbs": 170.0,
                "fat": 55.0,
                "fiber": 26.0,
                "nutrition_score_sum": 90.0,
                "nutrition_score_count": 1,
            },
        ]
        summary = AnalyticsService.format_analytics_text(
            daily_records=mock_records,
            user_name="@jamesboh",
            days_window=7,
            targets={"daily_calorie_target": 2000, "daily_protein_target": 150, "daily_fiber_target": 25},
        )
        self.assertIn("1,950 kcal", summary)
        self.assertIn("152.5g", summary)
        self.assertIn("25.5g", summary)
        self.assertIn("2/7 days", summary)

    def test_firestore_crud_and_export_lifecycle(self):
        """Integration test: registers user, logs meal, reads totals, exports CSV, and deletes data."""
        if not os.path.exists("service_account.json"):
            self.skipTest("service_account.json not found, skipping live Firestore integration test.")

        service = FirestoreService(
            project_id="james-boh-macro-tracker",
            credentials_path="service_account.json",
        )

        test_chat_id = 999999999
        test_username = "test_user_integration"

        async def run_lifecycle():
            # 1. Register user
            await service.register_or_update_user(test_chat_id, test_username, "TestBot")
            profile = await service.get_user_profile(test_chat_id)
            self.assertEqual(profile.get("user_name"), test_username)

            # 2. Save meal
            meal_data = {
                "date": "2026-09-10",
                "time": "12:00:00",
                "timestamp": "2026-09-10 12:00:00",
                "category": "Lunch",
                "total_calories": 500.0,
                "total_protein": 40.0,
                "total_carbs": 45.0,
                "total_fat": 15.0,
                "total_fiber": 5.0,
                "nutrition_score": 88,
                "motivational_note": "Great test meal!",
                "items": [
                    {
                        "item_name": "Chicken Breast",
                        "category": "Lunch",
                        "calories": 250.0,
                        "protein": 35.0,
                        "carbohydrates": 0.0,
                        "fat": 5.0,
                        "fiber": 0.0,
                        "short_description": "Grilled",
                        "nutrition_score": 95,
                    },
                    {
                        "item_name": "Brown Rice",
                        "category": "Lunch",
                        "calories": 250.0,
                        "protein": 5.0,
                        "carbohydrates": 45.0,
                        "fat": 10.0,
                        "fiber": 5.0,
                        "short_description": "Steamed",
                        "nutrition_score": 80,
                    },
                ],
            }
            meal_id = await service.save_meal(test_chat_id, test_username, meal_data)
            self.assertTrue(bool(meal_id))

            # 3. Read today's totals
            totals = await service.get_user_today_totals(test_chat_id, "2026-09-10")
            self.assertEqual(totals["calories"], 500.0)
            self.assertEqual(totals["protein"], 40.0)
            self.assertEqual(totals["fiber"], 5.0)
            self.assertEqual(totals["item_count"], 2)

            # 4. Read last meal
            last = await service.get_last_meal(test_chat_id)
            self.assertIsNotNone(last)
            self.assertEqual(last["id"], meal_id)

            # 5. Export CSV
            csv_str = await service.export_user_meals_csv(test_chat_id, test_username)
            lines = csv_str.strip().splitlines()
            self.assertEqual(len(lines), 3) # Header + 2 items
            self.assertIn("Chicken Breast", lines[1])
            self.assertIn("Brown Rice", lines[2])

            # 6. Delete all user data (PDPA right to erasure)
            deleted_count = await service.delete_all_user_data(test_chat_id)
            self.assertGreaterEqual(deleted_count, 1)

            # Verify empty after deletion
            after_totals = await service.get_user_today_totals(test_chat_id, "2026-09-10")
            self.assertEqual(after_totals["item_count"], 0)

        asyncio.run(run_lifecycle())


if __name__ == "__main__":
    unittest.main()
