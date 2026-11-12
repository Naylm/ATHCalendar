from datetime import date

from django.test import TestCase

from planning.errors import ValidationFailed
from planning.services.periods import weekend_dates


class WeekendGenerationTests(TestCase):
    def test_first_half_2027(self):
        days = weekend_dates(date(2027, 1, 1), date(2027, 6, 30))
        self.assertEqual(len(days), 52)
        self.assertEqual(days[0], date(2027, 1, 2))
        self.assertEqual(days[-1], date(2027, 6, 27))

    def test_second_half_2027(self):
        days = weekend_dates(date(2027, 7, 1), date(2027, 12, 31))
        self.assertEqual(len(days), 52)
        self.assertEqual(days[0], date(2027, 7, 3))
        self.assertEqual(days[-1], date(2027, 12, 26))

    def test_year_boundary(self):
        days = weekend_dates(date(2027, 12, 25), date(2028, 1, 2))
        self.assertEqual(
            days,
            [
                date(2027, 12, 25),
                date(2027, 12, 26),
                date(2028, 1, 1),
                date(2028, 1, 2),
            ],
        )

    def test_leap_year_2028_feb_mar(self):
        days = weekend_dates(date(2028, 2, 26), date(2028, 3, 5))
        self.assertEqual(
            days,
            [
                date(2028, 2, 26),
                date(2028, 2, 27),
                date(2028, 3, 4),
                date(2028, 3, 5),
            ],
        )

    def test_mid_july_2027(self):
        days = weekend_dates(date(2027, 7, 15), date(2027, 7, 31))
        self.assertEqual(
            days,
            [
                date(2027, 7, 17),
                date(2027, 7, 18),
                date(2027, 7, 24),
                date(2027, 7, 25),
                date(2027, 7, 31),
            ],
        )

    def test_single_weekend_day(self):
        days = weekend_dates(date(2027, 1, 2), date(2027, 1, 2))
        self.assertEqual(days, [date(2027, 1, 2)])

    def test_interval_without_weekend(self):
        days = weekend_dates(date(2027, 1, 4), date(2027, 1, 8))
        self.assertEqual(days, [])

    def test_leap_2000(self):
        days = weekend_dates(date(2000, 2, 26), date(2000, 3, 1))
        self.assertEqual(days, [date(2000, 2, 26), date(2000, 2, 27)])

    def test_non_leap_2100(self):
        days = weekend_dates(date(2100, 2, 27), date(2100, 3, 1))
        self.assertEqual(days, [date(2100, 2, 27), date(2100, 2, 28)])

    def test_rejects_inverted_bounds(self):
        with self.assertRaises(ValidationFailed):
            weekend_dates(date(2027, 6, 1), date(2027, 1, 1))
