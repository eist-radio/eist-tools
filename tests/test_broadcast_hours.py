"""DST checks for the broadcast window. Run with: python -m unittest discover tests"""

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

# The script imports Playwright at module level; the helpers under test don't need it.
if "playwright.sync_api" not in sys.modules:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        stub = types.ModuleType("playwright.sync_api")
        stub.sync_playwright = None
        sys.modules["playwright"] = types.ModuleType("playwright")
        sys.modules["playwright.sync_api"] = stub

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "add-eist-aris-shows.py"
spec = importlib.util.spec_from_file_location("eist_aris", SCRIPT)
eist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eist)


def checked_slots(year, month, day):
    """Irish start times of every slot the hourly worker cron would act on."""
    slots = []
    for hour in range(7, 24):  # wrangler.toml: 50 7-23 * * *
        fired = datetime(year, month, day, hour, 50)
        slot = eist.round_up_to_hour(fired)
        if eist.in_broadcast_hours(slot):
            slots.append(eist.to_irish(slot).strftime("%H:%M"))
    return slots


class BroadcastHours(unittest.TestCase):
    def test_same_irish_window_in_summer_and_winter(self):
        summer = checked_slots(2026, 7, 15)
        winter = checked_slots(2026, 11, 15)
        self.assertEqual(summer, winter)
        self.assertEqual(summer[0], "09:00")
        self.assertEqual(summer[-1], "23:00")

    def test_winter_morning_slot_before_nine_is_off_air(self):
        self.assertFalse(eist.in_broadcast_hours(datetime(2026, 11, 15, 8, 0)))
        self.assertTrue(eist.in_broadcast_hours(datetime(2026, 11, 15, 9, 0)))

    def test_last_hour_before_midnight_is_on_air(self):
        self.assertTrue(eist.in_broadcast_hours(datetime(2026, 11, 15, 23, 0)))
        self.assertTrue(eist.in_broadcast_hours(datetime(2026, 7, 15, 22, 0)))
        self.assertFalse(eist.in_broadcast_hours(datetime(2026, 7, 15, 23, 0)))

    def test_planner_day_bounds_follow_dublin_time(self):
        start, end = eist.broadcast_bounds_utc(datetime(2026, 7, 15))
        self.assertEqual(start, datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 7, 15, 23, 0, tzinfo=timezone.utc))
        start, end = eist.broadcast_bounds_utc(datetime(2026, 11, 15))
        self.assertEqual(start, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc))

    def test_day_length_on_clock_change_days(self):
        for day in (datetime(2026, 3, 29), datetime(2026, 10, 25)):
            start, end = eist.broadcast_bounds_utc(day)
            self.assertEqual(end - start, timedelta(hours=15))


if __name__ == "__main__":
    unittest.main()
