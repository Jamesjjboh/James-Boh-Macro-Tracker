"""
Unit and smoke tests for Telegram Macros & Calories Logging Bot.
Validates Pydantic schemas, data parsing, daily totals calculation, and HTML formatting.
"""

import unittest
from unittest.mock import MagicMock
from bot import (
    FoodItem,
    MealAnalysisResponse,
    GoogleSheetsService,
    SHEET_HEADERS,
    format_telegram_reply,
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
        # Valid scores
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

        # Invalid score > 100
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
        """Verify the exact 10 columns requested by user."""
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
        """Verify HTML formatting produces expected structure, includes fiber, and avoids unwanted footers."""
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
        self.assertIn("3.5g", msg) # Fiber in item
        self.assertIn("22.0 g", msg) # Fiber in totals
        self.assertIn("90/100", msg)
        self.assertIn("1450 kcal", msg)
        # Ensure no "Built by" footer on meal logs
        self.assertNotIn("Built by", msg)

    def test_calculate_user_today_totals(self):
        """Test daily totals aggregation logic filtering by user, date, and summing fiber."""
        service = GoogleSheetsService("dummy.json", "dummy_sheet")
        mock_ws = MagicMock()
        mock_ws.get_all_values.return_value = [
            SHEET_HEADERS,
            # Row 1: Today, user James
            ["2026-09-07 08:30:00", "@James", "Oatmeal with whey", "Breakfast", "350", "30", "45", "5", "6.0", "1 cup oats + 1 scoop whey", "88"],
            # Row 2: Today, user Partner
            ["2026-09-07 09:00:00", "@Partner", "Avocado Toast", "Breakfast", "400", "12", "35", "22", "8.0", "2 slices with half avocado", "65"],
            # Row 3: Today, user James
            ["2026-09-07 12:45:00", "@james", "Chicken Breast Salad", "Lunch", "420", "48", "10", "12", "5.5", "200g chicken with greens", "95"],
            # Row 4: Yesterday, user James
            ["2026-09-06 20:00:00", "@james", "Steak", "Dinner", "600", "50", "0", "40", "0.0", "Sirloin steak", "80"],
        ]

        # Use an event loop to run async get_user_today_totals
        import asyncio
        service.worksheet = mock_ws
        totals = asyncio.run(service.get_user_today_totals("@james", "2026-09-07"))

        # James should have 350 + 420 = 770 kcal, 30 + 48 = 78 g protein, 45 + 10 = 55 g carbs, 5 + 12 = 17 g fat, 6.0 + 5.5 = 11.5 g fiber, 2 items
        self.assertEqual(totals["calories"], 770.0)
        self.assertEqual(totals["protein"], 78.0)
        self.assertEqual(totals["carbs"], 55.0)
        self.assertEqual(totals["fat"], 17.0)
        self.assertEqual(totals["fiber"], 11.5)
        self.assertEqual(totals["item_count"], 2)


if __name__ == "__main__":
    unittest.main()

