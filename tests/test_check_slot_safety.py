"""Safety checks for the hourly slot check. Run with: python -m unittest discover tests"""

import contextlib
import importlib.util
import io
import pathlib
import sys
import types
import unittest
from datetime import datetime
from unittest import mock

import requests

# The script imports Playwright at module level; these tests replace it with a mock.
if "playwright.sync_api" not in sys.modules:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        stub = types.ModuleType("playwright.sync_api")
        stub.sync_playwright = None
        sys.modules["playwright"] = types.ModuleType("playwright")
        sys.modules["playwright.sync_api"] = stub

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "add-eist-aris-shows.py"
spec = importlib.util.spec_from_file_location("eist_aris_safety", SCRIPT)
eist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eist)

SLOT = datetime(2026, 11, 16, 17, 50)  # UTC, rounds up to the 18:00 slot


def show(title, start, end, track_id="t1", media_type="mix"):
    return {
        "title": title,
        "start": start,
        "end": end,
        "duration": 60,
        "media": {"type": media_type, "trackId": track_id},
    }


def response(status=200, payload=None):
    resp = mock.Mock(status_code=status)
    resp.json.return_value = payload if payload is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    return resp


def run_check_slot(scheduler):
    """Run the slot check with Playwright mocked out; return (exit code, output)."""
    out = io.StringIO()
    code = 0
    with mock.patch.object(eist, "sync_playwright", mock.MagicMock()):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                eist.mode_check_slot(scheduler, None, SLOT, "user", "pw", headless=True)
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue()


class TrackFileStatus(unittest.TestCase):
    def setUp(self):
        self.scheduler = eist.EistArisScheduler("key")
        self.scheduler.session = mock.Mock()

    def status_for(self, result):
        if isinstance(result, Exception):
            self.scheduler.session.get.side_effect = result
        else:
            self.scheduler.session.get.side_effect = None
            self.scheduler.session.get.return_value = result
        with contextlib.redirect_stderr(io.StringIO()):
            return self.scheduler.track_file_status("t1")

    def test_definite_answers(self):
        self.assertEqual(self.status_for(response(200, {"tracks": [{"id": "t1"}]})), "present")
        self.assertEqual(self.status_for(response(200, {"tracks": []})), "missing")

    def test_failures_are_never_missing(self):
        self.assertEqual(self.status_for(requests.exceptions.Timeout()), "unknown")
        self.assertEqual(self.status_for(requests.exceptions.ConnectionError()), "unknown")
        self.assertEqual(self.status_for(response(500)), "unknown")
        self.assertEqual(self.status_for(response(404)), "unknown")
        self.assertEqual(self.status_for(response(401)), "unauthorized")


class CheckSlotSafety(unittest.TestCase):
    def make_scheduler(self):
        scheduler = eist.EistArisScheduler("key", "user", "pw")
        scheduler.authenticated = True
        scheduler.create_show_from_mapping = mock.Mock()
        scheduler.delete_show_via_playwright = mock.Mock()
        scheduler.fetch_track_details = mock.Mock(return_value=None)
        scheduler.build_replay_list = mock.Mock(
            return_value=[
                {"title": "Replay", "track_id": "r1", "scheduled_duration": 60, "duration": 60}
            ]
        )
        return scheduler

    def test_schedule_fetch_failure_leaves_slot_untouched(self):
        scheduler = self.make_scheduler()
        scheduler.session = mock.Mock()
        scheduler.session.get.side_effect = requests.exceptions.ConnectionError("down")
        code, output = run_check_slot(scheduler)
        self.assertEqual(code, 1)
        self.assertIn("Could not fetch the schedule", output)
        scheduler.create_show_from_mapping.assert_not_called()
        scheduler.delete_show_via_playwright.assert_not_called()

    def test_track_lookup_timeout_does_not_replace_show(self):
        scheduler = self.make_scheduler()
        on_air = show("Scaoilte", "2026-11-16T18:00:00Z", "2026-11-16T19:00:00Z")
        scheduler.fetch_schedule = mock.Mock(return_value=[on_air])
        scheduler.track_file_status = mock.Mock(return_value="unknown")
        code, output = run_check_slot(scheduler)
        self.assertEqual(code, 0)
        self.assertIn("Slot is OK", output)
        scheduler.delete_show_via_playwright.assert_not_called()
        scheduler.create_show_from_mapping.assert_not_called()

    def test_missing_file_is_still_replaced(self):
        scheduler = self.make_scheduler()
        broken = show("Scaoilte", "2026-11-16T18:00:00Z", "2026-11-16T19:00:00Z")
        scheduler.fetch_schedule = mock.Mock(return_value=[broken])
        scheduler.track_file_status = mock.Mock(
            side_effect=lambda tid: "missing" if tid == "t1" else "present"
        )
        code, output = run_check_slot(scheduler)
        self.assertEqual(code, 0)
        self.assertIn("✓ SUCCESS: Created 1 show(s)", output)
        scheduler.delete_show_via_playwright.assert_called_once()
        scheduler.create_show_from_mapping.assert_called_once()

    def test_conflict_is_not_reported_as_success(self):
        scheduler = self.make_scheduler()
        scheduler.fetch_schedule = mock.Mock(return_value=[])
        scheduler.track_file_status = mock.Mock(return_value="present")
        scheduler.create_show_from_mapping.side_effect = Exception(
            "API creation failed: event conflicts with an existing event"
        )
        code, output = run_check_slot(scheduler)
        self.assertEqual(code, 1)
        self.assertNotIn("✓ SUCCESS: Created", output)
        self.assertIn("✗ FAILED", output)

    def test_null_media_does_not_crash(self):
        scheduler = self.make_scheduler()
        no_media = show("Mystery", "2026-11-16T18:00:00Z", "2026-11-16T19:00:00Z")
        no_media["media"] = None
        scheduler.fetch_schedule = mock.Mock(return_value=[no_media])
        code, output = run_check_slot(scheduler)
        self.assertEqual(code, 0)
        scheduler.delete_show_via_playwright.assert_not_called()


if __name__ == "__main__":
    unittest.main()
