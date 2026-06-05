#!/usr/bin/env python3
"""
Populate weekly schedule with éist arís (replay) shows from the Radiocult API.

Modes:
- --output-tracks     → tracks.json (eligible shows + track/artist details)
- --output-schedule   → schedule.json (current schedule for week)
- --test-slots        → empty-slots.json (empty 1h/2h slots)
- --plan              → updated-slots.json (shows mapped to slots)
- --execute           → use Playwright to create shows from updated-slots.json
"""

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

STATION_ID = "eist-radio"
API_BASE_URL = "https://api.radiocult.fm/api/station"
WEB_BASE_URL = "https://app.radiocult.fm"


def save_json(data, default_filename: str, output_path: Optional[str] = None) -> str:
    """Write JSON to disk, returning the path used."""
    path = output_path or default_filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def parse_target_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD or YYYY-MM-DD HH:MM:SS into a datetime."""
    try:
        if " " in date_str:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        print(f"Error parsing date: {exc}", file=sys.stderr)
        print("Use: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS", file=sys.stderr)
        sys.exit(1)


def round_up_to_hour(dt: datetime) -> datetime:
    """Round a datetime up to the next full hour. If already on the hour, return as-is."""
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))


class EistArisScheduler:
    """Handles fetching and scheduling éist arís replay shows."""

    def __init__(
        self,
        api_key: str,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.login_username = login_username
        self.login_password = login_password

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        self.authenticated = False

        # Matches: "éist arís", "eist aris", "ésit arís", "(éist arís)", "(eist aris)" and variations
        self.eist_aris_pattern = re.compile(
            r"\(?\s*[eé](?:ist|sit)\s+ar[ií]s\s*\)?", re.IGNORECASE
        )

    # -------------------------------------------------------------------------
    # API helpers
    # -------------------------------------------------------------------------

    def authenticate_with_playwright(self) -> None:
        """Log in with Playwright and copy cookies into the requests session."""
        if self.authenticated:
            return

        if not self.login_username or not self.login_password:
            print(
                "Warning: Cannot authenticate - credentials not set",
                file=sys.stderr,
            )
            return

        print("Authenticating with Playwright to get session cookies...")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(f"{WEB_BASE_URL}/login")
                page.wait_for_selector("input[type=\"email\"]", timeout=10_000)

                page.fill('input[type="email"]', self.login_username)
                page.fill('input[type="password"]', self.login_password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(3_000)

                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path"),
                    )

                # Verify authentication
                test_url = f"{API_BASE_URL}/{STATION_ID}/media/track"
                resp = self.session.get(test_url)

                if resp.status_code == 401:
                    print("Authentication failed - API returned 401", file=sys.stderr)
                    browser.close()
                    return

                self.authenticated = True
                print("Authentication successful!")
            except Exception as exc:
                print(f"Authentication error: {exc}", file=sys.stderr)
            finally:
                browser.close()

    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """Fetch schedule items in a date range."""
        start_str = start_date.strftime("%Y-%m-%dT00:00:00Z")
        end_str = end_date.strftime("%Y-%m-%dT23:59:59Z")

        url = f"{API_BASE_URL}/{STATION_ID}/schedule"
        params = {"startDate": start_str, "endDate": end_str}

        try:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("schedules", [])
        except requests.exceptions.RequestException as exc:
            print(f"Error fetching schedule: {exc}", file=sys.stderr)
            return []

    def fetch_track_details(self, track_id: str) -> Optional[Dict]:
        """Fetch track metadata from the undocumented track API."""
        url = f"{API_BASE_URL}/{STATION_ID}/media/track"
        params = {"trackId": track_id}

        try:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            print(
                f"Warning: Could not fetch track details for {track_id}: {exc}",
                file=sys.stderr,
            )
            return None

    def fetch_artist_details(self, artist_id: str) -> Optional[Dict]:
        """Fetch artist metadata."""
        url = f"{API_BASE_URL}/{STATION_ID}/artists/{artist_id}"

        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            print(
                f"Warning: Could not fetch artist details for {artist_id}: {exc}",
                file=sys.stderr,
            )
            return None

    # -------------------------------------------------------------------------
    # Eligibility + selection logic
    # -------------------------------------------------------------------------

    def has_eist_aris_suffix(self, title: str) -> bool:
        """Return True if a title already contains an éist arís suffix."""
        return bool(self.eist_aris_pattern.search(title or ""))

    def is_eligible_show(self, show: Dict) -> bool:
        """Check if a show is eligible as an éist arís replay."""
        title = show.get("title", "")
        if not title:
            return False

        if self.has_eist_aris_suffix(title):
            return False

        media = show.get("media")
        if not media:
            return False

        if media.get("type") != "mix":
            return False

        if not media.get("trackId"):
            return False

        return True

    def build_replay_list(
        self, start_date: datetime, weeks_back: int = 3
    ) -> List[Dict]:
        """Return eligible shows from the last N weeks."""
        end_date = start_date
        start_date = start_date - timedelta(weeks=weeks_back)

        print(f"Fetching shows from {start_date.date()} to {end_date.date()}...")
        all_shows = self.fetch_schedule(start_date, end_date)
        print(f"Found {len(all_shows)} total shows")

        eligible: List[Dict] = []

        for show in all_shows:
            if not self.is_eligible_show(show):
                continue

            media = show.get("media", {})
            track_id = media.get("trackId")
            file_duration = show.get("duration", 60)  # minutes

            within_1hr = abs(file_duration - 60) <= 1
            within_2hr = abs(file_duration - 120) <= 1

            if not (within_1hr or within_2hr):
                continue

            scheduled_duration = 120 if abs(file_duration - 120) < abs(
                file_duration - 60
            ) else 60

            eligible.append(
                {
                    "title": show.get("title"),
                    "original_start": show.get("start"),
                    "original_end": show.get("end"),
                    "duration": file_duration,
                    "scheduled_duration": scheduled_duration,
                    "media_type": media.get("type"),
                    "track_id": track_id,
                    "description": show.get("description", ""),
                    "show_id": show.get("id"),
                    "color": show.get("color"),
                    "artist_ids": show.get("artistIds", []),
                    "artists": show.get("artists", []),
                }
            )

        print(f"Found {len(eligible)} eligible shows for éist arís replay")
        return eligible

    # -------------------------------------------------------------------------
    # Slot detection
    # -------------------------------------------------------------------------

    @staticmethod
    def get_week_start(target_date: datetime) -> datetime:
        """Return Monday of the week containing target_date."""
        days_since_monday = target_date.weekday()
        week_start = target_date - timedelta(days=days_since_monday)
        return week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    def find_empty_slots(self, target_date: datetime, days: int = 7) -> List[Dict]:
        """
        Find empty 1h/2h slots between 9:00 and 23:00 for target week.
        Returns slots with datetime objects (start, end).
        """
        week_start = self.get_week_start(target_date)
        end_date = week_start + timedelta(days=days)

        print(
            f"\nFinding empty slots for week of {week_start.date()} "
            f"to {end_date.date()}..."
        )

        current_schedule = self.fetch_schedule(week_start, end_date)

        occupied_slots = []
        for show in current_schedule:
            start = show.get("start")
            end = show.get("end")
            if not start or not end:
                continue

            occupied_slots.append(
                {
                    "start": datetime.fromisoformat(start.replace("Z", "+00:00")),
                    "end": datetime.fromisoformat(end.replace("Z", "+00:00")),
                    "title": show.get("title", ""),
                }
            )

        occupied_slots.sort(key=lambda x: x["start"])

        empty_gaps: List[Dict] = []

        for day_offset in range(days):
            current_day = week_start + timedelta(days=day_offset)
            day_start = current_day.replace(
                hour=9, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            day_end = current_day.replace(
                hour=23, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )

            day_shows = [
                s for s in occupied_slots if day_start <= s["start"] < day_end
            ]

            print(f"\n{current_day.strftime('%A %Y-%m-%d')}:")
            print(
                f"  Day bounds: {day_start:%Y-%m-%d %H:%M} "
                f"to {day_end:%Y-%m-%d %H:%M}"
            )
            print(f"  Found {len(day_shows)} shows on this day")

            if day_shows:
                for s in day_shows:
                    print(
                        f"    - '{s['title']}': "
                        f"{s['start']:%Y-%m-%d %H:%M} - {s['end']:%Y-%m-%d %H:%M}"
                    )

            if not day_shows:
                # Entire day is empty
                current_time = day_start
                while current_time < day_end:
                    slot_end = current_time + timedelta(hours=2)
                    if slot_end <= day_end:
                        empty_gaps.append(
                            {
                                "start": current_time,
                                "end": slot_end,
                                "duration_minutes": 120,
                            }
                        )
                        current_time = slot_end
                    else:
                        slot_end = current_time + timedelta(hours=1)
                        if slot_end <= day_end:
                            empty_gaps.append(
                                {
                                    "start": current_time,
                                    "end": slot_end,
                                    "duration_minutes": 60,
                                }
                            )
                            current_time = slot_end
                        else:
                            break
                continue

            # Gap before first show
            if day_shows[0]["start"] > day_start:
                gap_duration = (day_shows[0]["start"] - day_start).total_seconds() / 60
                print(
                    "  ✓ Gap before first show: "
                    f"{day_start:%H:%M} - {day_shows[0]['start']:%H:%M} "
                    f"({gap_duration:.0f} min)"
                )
                empty_gaps.append(
                    {
                        "start": day_start,
                        "end": day_shows[0]["start"],
                        "duration_minutes": gap_duration,
                    }
                )
            else:
                print(
                    "  ✗ No gap before first show "
                    f"(show starts at {day_shows[0]['start']:%H:%M}, "
                    f"day starts at {day_start:%H:%M})"
                )

            # Gaps between shows
            for i in range(len(day_shows) - 1):
                gap_start = day_shows[i]["end"]
                gap_end = day_shows[i + 1]["start"]
                if gap_start < gap_end:
                    gap_duration = (gap_end - gap_start).total_seconds() / 60
                    print(
                        f"  ✓ Gap found: {gap_start:%H:%M} - {gap_end:%H:%M} "
                        f"({gap_duration:.0f} min)"
                    )
                    print(
                        f"    Between '{day_shows[i]['title']}' and "
                        f"'{day_shows[i+1]['title']}'"
                    )
                    empty_gaps.append(
                        {
                            "start": gap_start,
                            "end": gap_end,
                            "duration_minutes": gap_duration,
                        }
                    )
                else:
                    print(
                        "  → Shows are back-to-back: "
                        f"'{day_shows[i]['title']}' ends at {gap_start:%H:%M}, "
                        f"'{day_shows[i+1]['title']}' starts at {gap_end:%H:%M}"
                    )

            # Gap after last show
            if day_shows[-1]["end"] < day_end:
                gap_duration = (day_end - day_shows[-1]["end"]).total_seconds() / 60
                print(
                    "  ✓ Gap after last show: "
                    f"{day_shows[-1]['end']:%H:%M} - {day_end:%H:%M} "
                    f"({gap_duration:.0f} min)"
                )
                empty_gaps.append(
                    {
                        "start": day_shows[-1]["end"],
                        "end": day_end,
                        "duration_minutes": gap_duration,
                    }
                )
            else:
                print(
                    "  ✗ No gap after last show "
                    f"(show ends at {day_shows[-1]['end']:%H:%M}, "
                    f"day ends at {day_end:%H:%M})"
                )

        print("\n" + "=" * 80)
        print("SPLITTING GAPS INTO 1HR AND 2HR SLOTS")
        print("=" * 80)

        valid_slots: List[Dict] = []

        for gap in empty_gaps:
            gap_start = gap["start"]
            gap_end = gap["end"]
            duration = gap["duration_minutes"]

            print(
                f"\nGap: {gap_start:%a %H:%M} - {gap_end:%H:%M} "
                f"({duration:.0f} min)"
            )

            current_time = gap_start
            slot_count = 0

            while current_time < gap_end:
                remaining = (gap_end - current_time).total_seconds() / 60

                if remaining >= 120:
                    slot_end = current_time + timedelta(hours=2)
                    valid_slots.append(
                        {
                            "start": current_time,
                            "end": slot_end,
                            "duration_minutes": 120,
                            "scheduled_duration": 120,
                        }
                    )
                    print(
                        f"  → Created 2hr slot: {current_time:%H:%M} - "
                        f"{slot_end:%H:%M}"
                    )
                    current_time = slot_end
                    slot_count += 1
                elif remaining >= 60:
                    slot_end = current_time + timedelta(hours=1)
                    valid_slots.append(
                        {
                            "start": current_time,
                            "end": slot_end,
                            "duration_minutes": 60,
                            "scheduled_duration": 60,
                        }
                    )
                    print(
                        f"  → Created 1hr slot: {current_time:%H:%M} - "
                        f"{slot_end:%H:%M}"
                    )
                    current_time = slot_end
                    slot_count += 1
                else:
                    print(f"  ✗ Skipped remaining {remaining:.0f} min (less than 1hr)")
                    break

            print(f"  Total: Created {slot_count} slot(s) from this gap")

        valid_slots.sort(key=lambda x: x["start"])

        print("\n" + "=" * 80)
        print(f"Total: {len(empty_gaps)} gaps found, split into {len(valid_slots)} slots")
        print("=" * 80)

        print("\nFirst 5 slots (chronological):")
        for i, slot in enumerate(valid_slots[:5]):
            print(
                f"  {i+1}. {slot['start']:%a %Y-%m-%d %H:%M} - "
                f"{slot['end']:%H:%M} ({slot['scheduled_duration']}min)"
            )

        return valid_slots

    # -------------------------------------------------------------------------
    # Playwright show creation
    # -------------------------------------------------------------------------

    @staticmethod
    def format_time_for_gui(time_str: str) -> str:
        """
        Format time for GUI input field.
        Convert 12:00 to 12:00pm (GUI reads 12:00 as midnight).
        Convert 15:00 to 3:00pm (workaround for GUI bug).
        Keep all other times in 24-hour format.
        """
        if time_str == "12:00":
            return "12:00pm"
        if time_str == "15:00":
            return "3:00pm"
        return time_str

    @staticmethod
    def close_any_open_modals(page) -> None:
        """Attempt to close any open modals by pressing Escape multiple times."""
        try:
            print("  ! Closing any open modals...")
            for _ in range(3):
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            print("  ✓ Modals closed")
        except Exception:
            pass

    def create_show_from_mapping(self, page, mapping: Dict) -> None:
        """Create a single show via Playwright from a slot/show mapping."""
        slot = mapping["slot"]
        show = mapping["show"]

        start_time = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
        scheduled_duration = slot["scheduled_duration"]
        end_time = start_time + timedelta(minutes=scheduled_duration)

        start_date = start_time.strftime("%Y-%m-%d")
        start_time_str = start_time.strftime("%H:%M")
        end_time_str = end_time.strftime("%H:%M")
        day_of_week = start_time.strftime("%A")

        print("\n" + "=" * 60)
        print(f"Creating: {show['title']} (éist arís)")
        print(f"When: {day_of_week}, {start_date}")
        print(f"Time: {start_time_str} - {end_time_str} UTC ({scheduled_duration}min)")
        print("=" * 60)

        week_start = start_time - timedelta(days=start_time.weekday())
        week_str = week_start.strftime("%Y-%m-%d")
        page.goto(f"{WEB_BASE_URL}/schedule?w={week_str}")
        page.wait_for_load_state("networkidle", timeout=15_000)

        # 1. Open create modal
        print("\n[1] Opening create modal...")
        try:
            # Try primary selector
            create_btn = page.locator(
                'button:has(svg[viewBox="0 0 256 256"]):has-text("Create")'
            )
            create_btn.wait_for(timeout=5_000)
            create_btn.click()
        except Exception:
            raise Exception("Could not find Create button with any selector")

        page.wait_for_timeout(1_000)
        print("  ✓ Modal opened")

        # 2. Start time (react-aria spinbutton inputs)
        print(f"\n[2] Setting start time to {start_time_str}...")
        start_h, start_m = start_time_str.split(":")
        time_groups = page.locator('div[role="group"][aria-label="Time"]')
        start_group = time_groups.nth(0)
        hour_spin = start_group.locator('div[data-type="hour"]')
        hour_spin.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Control+a")
        page.keyboard.type(start_h, delay=100)
        page.wait_for_timeout(300)
        minute_spin = start_group.locator('div[data-type="minute"]')
        minute_spin.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Control+a")
        page.keyboard.type(start_m, delay=100)
        page.wait_for_timeout(300)
        print("  ✓ Start time set")

        # 3. End time (react-aria spinbutton inputs)
        print(f"\n[3] Setting end time to {end_time_str}...")
        end_h, end_m = end_time_str.split(":")
        end_group = time_groups.nth(1)
        end_hour_spin = end_group.locator('div[data-type="hour"]')
        end_hour_spin.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Control+a")
        page.keyboard.type(end_h, delay=100)
        page.wait_for_timeout(300)
        end_minute_spin = end_group.locator('div[data-type="minute"]')
        end_minute_spin.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Control+a")
        page.keyboard.type(end_m, delay=100)
        page.wait_for_timeout(300)
        print("  ✓ End time set")

        # 4. Start date
        start_day = start_time.day
        print(f"\n[4] Setting start date to {start_date}...")
        try:
            date_input = page.locator('input[id^="startDate"]')
            date_input.click()
            page.wait_for_timeout(500)

            date_button = page.locator(
                f'button[role="gridcell"]:has-text("{start_day}"):not([data-sibling])'
            )
            matching_buttons = date_button.all()
            clicked = False

            for btn in matching_buttons:
                if btn.inner_text().strip() == str(start_day):
                    btn.click()
                    clicked = True
                    break

            if not clicked and matching_buttons:
                date_button.first.click()

            print(f"  ✓ Start date selected (day {start_day})")
            page.wait_for_timeout(1_000)
        except Exception as exc:
            print(f"  ✗ Could not select start date: {exc}")

        # 5. End date (same day as start)
        end_day = start_day
        print(f"\n[5] Setting end date to same day (day {end_day})...")

        try:
            end_input = page.locator('input[id^="endDate"]')
            if end_input.count() > 0:
                end_input.click()
                page.wait_for_timeout(500)

                date_button = page.locator(
                    f'button[role="gridcell"]:has-text("{end_day}"):not([data-sibling])'
                )
                matching_buttons = date_button.all()
                clicked = False

                for btn in matching_buttons:
                    if btn.inner_text().strip() == str(end_day):
                        btn.click()
                        clicked = True
                        break

                if not clicked and matching_buttons:
                    date_button.first.click()

                print(f"  ✓ End date selected (day {end_day})")
                page.wait_for_timeout(1_000)
            else:
                print("  ℹ End date selector not present, skipping")
        except Exception as exc:
            print(f"  ✗ Could not select end date: {exc}")

        # 6. Title
        show_title = f"{show['title']} (éist arís)"
        print(f"\n[6] Setting title: {show_title}")
        title_input = page.locator('input[name="title"]')
        title_input.fill(show_title)
        page.wait_for_timeout(100)
        print("  ✓ Title filled")

        # 7. Description
        print("\n[7] Setting description...")
        description = show.get("description", "")

        if description and isinstance(description, dict):
            text_parts: List[str] = []
            try:
                for block in description.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "paragraph":
                        for node in block.get("content", []):
                            if isinstance(node, dict) and node.get("type") == "text":
                                text_parts.append(node.get("text", ""))
                description = " ".join(text_parts)
            except Exception:
                description = "Éist arís replay show."

        if not description or not isinstance(description, str):
            description = "Éist arís replay show."

        desc_field = page.locator(
            'p[data-placeholder*="Enter event description"]'
        )
        desc_field.click()
        page.wait_for_timeout(100)
        page.keyboard.type(description)
        page.wait_for_timeout(200)
        print("  ✓ Description filled")

        # 8. Artist
        artist_name = show.get("artist_name") or show.get("track_artist") or ""
        if artist_name:
            print(f"\n[8] Adding artist: {artist_name}")
            try:
                artist_input = page.locator("input#artist-select")
                artist_input.click()
                page.wait_for_timeout(100)
                page.keyboard.type(artist_name)
                page.wait_for_timeout(500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                print("  ✓ Artist added")
            except Exception as exc:
                print(f"  ✗ Could not add artist: {exc}")
        else:
            print("\n[8] No artist available - skipping")

        # 9. Pre-record mode
        print("\n[9] Enabling pre-record mode...")
        prerecord_button = page.get_by_role("button", name="Mix Pre-record")
        prerecord_button.click()
        page.wait_for_timeout(500)
        print("  ✓ Pre-record enabled")

        # 10. Open media selection
        print("\n[10] Opening media selector...")
        media_button = page.locator("text=Select media").first
        media_button.click()
        page.wait_for_timeout(1_000)
        print("  ✓ Media selector opened")

        # 11. Search track
        track_title = show.get("track_title") or show.get("title", "")
        if not show.get("track_title"):
            print("  ! No track_title, falling back to show title")

        print(f"\n[11] Searching for track: {track_title}")
        search_input = page.locator(
            'input[data-ds--text-field--input="true"]'
        ).last
        search_input.fill(track_title)
        page.wait_for_timeout(1_000)
        print("  ✓ Search completed")

        # 12. Select track (click first search result in media table)
        print("\n[12] Selecting track from results...")
        try:
            # Media results table has an "Album" column (unique vs calendar tables)
            media_table = page.locator('table:has(th:has-text("Album"))')
            result_rows = media_table.locator("tr").filter(
                has=page.locator("td")
            )
            row_count = result_rows.count()
            if row_count == 0:
                raise Exception(
                    f"No media found matching '{track_title}'"
                )
            result_rows.first.click()
            page.wait_for_timeout(500)
            print(f"  ✓ Track selected (from {row_count} result(s))")
        except Exception as exc:
            print(f"  ! Could not select track: {exc}")
            raise Exception(f"Failed to select track '{track_title}'") from exc

        # 13. Create event
        print("\n[13] Creating event...")
        create_button = page.locator(
            'button[type="submit"]:has-text("Create event")'
        )
        create_button.click()
        page.wait_for_timeout(3_000)

        # Check for conflict error dialog
        conflict = page.locator('text="This event conflicts with another"')
        if conflict.count() > 0:
            close_btn = page.locator('button:has-text("Close")')
            if close_btn.count() > 0:
                close_btn.first.click()
                page.wait_for_timeout(500)
            raise Exception("Event conflicts with an existing show in this time slot")

        # Verify modal closed (indicates success)
        create_still_visible = page.locator(
            'button[type="submit"]:has-text("Create event")'
        ).count()
        if create_still_visible > 0:
            raise Exception("Create event form still open — submission may have failed")

        print("  ✓ Event created")

        print("\n" + "=" * 60)
        print(f"SUCCESS! Created '{show_title}'")
        print("=" * 60 + "\n")

    def delete_show_via_playwright(self, page, show_title: str, slot_start: datetime) -> None:
        """Delete an existing show from the schedule via Playwright."""
        week_start = slot_start - timedelta(days=slot_start.weekday())
        week_str = week_start.strftime("%Y-%m-%d")

        print(f"\nDeleting show: '{show_title}'")
        print(f"Navigating to schedule week: {week_str}")

        page.goto(f"{WEB_BASE_URL}/schedule?w={week_str}")
        page.wait_for_load_state("networkidle", timeout=15_000)

        # Click on the show in the calendar
        print("  Looking for show in calendar...")
        show_element = page.locator(f'text="{show_title}"').first
        show_element.click()
        page.wait_for_timeout(1_000)
        print("  ✓ Show clicked")

        # Click the delete/trash button in the show detail panel
        print("  Looking for delete button...")
        try:
            delete_btn = page.locator('button:has(svg), button:has-text("Delete")').filter(
                has_text="Delete"
            ).first
            delete_btn.click()
            page.wait_for_timeout(1_000)
            print("  ✓ Delete button clicked")
        except Exception:
            # Try alternative: look for a trash icon button
            delete_btn = page.locator('[aria-label*="delete" i], [aria-label*="remove" i]').first
            delete_btn.click()
            page.wait_for_timeout(1_000)
            print("  ✓ Delete button clicked (alt selector)")

        # Confirm the deletion dialog
        print("  Confirming deletion...")
        try:
            confirm_btn = page.locator(
                'button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes")'
            ).last
            confirm_btn.click()
            page.wait_for_timeout(2_000)
            print("  ✓ Deletion confirmed")
        except Exception as exc:
            print(f"  ✗ Could not confirm deletion: {exc}")
            raise

        print(f"  ✓ Show '{show_title}' deleted successfully")


# -----------------------------------------------------------------------------
# Mode handlers
# -----------------------------------------------------------------------------


def mode_output_tracks(scheduler: EistArisScheduler, args, target_date: datetime):
    print("\n" + "=" * 80)
    print("MODE: Generating tracks.json")
    print("=" * 80 + "\n")

    eligible_shows = scheduler.build_replay_list(target_date, args.weeks_back)

    print("\nAuthenticating to fetch track and artist details...")
    scheduler.authenticate_with_playwright()

    for i, show in enumerate(eligible_shows, 1):
        print(f"  [{i}/{len(eligible_shows)}] '{show['title']}'")

        track_id = show.get("track_id")
        if track_id:
            print(f"    - Fetching track {track_id}...")
            track_details = scheduler.fetch_track_details(track_id)
            if track_details:
                tracks = track_details.get("tracks") or []
                matching = next(
                    (t for t in tracks if t.get("id") == track_id), None
                )
                if matching:
                    title = matching.get("title", "")
                    if title:
                        show["track_title"] = title
                        print(f"      ✓ Track title: {title}")
                    else:
                        print("      ! No title in track data")
                else:
                    print(
                        f"      ! Track {track_id} not found in response "
                        f"(got {len(tracks)} tracks)"
                    )
            else:
                print("      ! Could not fetch track details")

        artist_ids = show.get("artist_ids") or []
        if artist_ids:
            first_artist_id = artist_ids[0]
            print(f"    - Fetching artist {first_artist_id}...")
            artist_details = scheduler.fetch_artist_details(first_artist_id)
            if artist_details:
                artist_obj = artist_details.get("artist") or {}
                artist_name = artist_obj.get("name", "")
                if artist_name:
                    show["artist_name"] = artist_name
                    print(f"      ✓ Artist: {artist_name}")
                else:
                    print("      ! No name in artist details")
            else:
                print("      ! Could not fetch artist details")
        else:
            print("    - No artist IDs available")

    out_path = save_json(eligible_shows, "tracks.json", args.output)

    print("\n" + "=" * 80)
    print(f"Saved {len(eligible_shows)} eligible shows to {out_path}")
    shows_1hr = [s for s in eligible_shows if s.get("scheduled_duration") == 60]
    shows_2hr = [s for s in eligible_shows if s.get("scheduled_duration") == 120]
    print(f"Summary: {len(shows_1hr)} x 1hr, {len(shows_2hr)} x 2hr")
    print("=" * 80 + "\n")


def mode_output_schedule(scheduler: EistArisScheduler, args, target_date: datetime):
    print("\n" + "=" * 80)
    print("MODE: Generating schedule.json")
    print("=" * 80 + "\n")

    week_start = scheduler.get_week_start(target_date)
    end_date = week_start + timedelta(days=args.days)

    print(f"Fetching schedule for {week_start.date()} to {end_date.date()}...")
    current_schedule = scheduler.fetch_schedule(week_start, end_date)

    schedule_json = []
    for show in current_schedule:
        media = show.get("media") or {}
        schedule_json.append(
            {
                "id": show.get("id"),
                "title": show.get("title"),
                "start": show.get("start"),
                "end": show.get("end"),
                "duration": show.get("duration"),
                "media_type": media.get("type"),
                "track_id": media.get("trackId"),
                "description": show.get("description"),
                "artist_ids": show.get("artistIds", []),
                "artists": show.get("artists", []),
                "color": show.get("color"),
            }
        )

    out_path = save_json(schedule_json, "schedule.json", args.output)

    print("\n" + "=" * 80)
    print(f"Saved {len(schedule_json)} shows to {out_path}")
    print("=" * 80 + "\n")


def mode_test_slots(scheduler: EistArisScheduler, args, target_date: datetime):
    print("\n" + "=" * 80)
    print("TEST MODE: Finding empty slots only")
    print("=" * 80 + "\n")

    empty_slots = scheduler.find_empty_slots(target_date, args.days)

    if not empty_slots:
        print("\n" + "=" * 80)
        print("✓ SCHEDULE IS COMPLETELY FILLED!")
        print("=" * 80)
        print("\nNo empty slots found.\n")
        out_path = save_json([], "empty-slots.json", args.output)
        print(f"Saved empty slots file: {out_path} (0 slots)\n")
        return

    slots_json = []
    for slot in empty_slots:
        slots_json.append(
            {
                "start": slot["start"].strftime("%Y-%m-%d %H:%M"),
                "end": slot["end"].strftime("%Y-%m-%d %H:%M"),
                "duration_minutes": slot["duration_minutes"],
                "scheduled_duration": slot.get("scheduled_duration"),
                "day_of_week": slot["start"].strftime("%A"),
                "date": slot["start"].strftime("%Y-%m-%d"),
            }
        )

    out_path = save_json(slots_json, "empty-slots.json", args.output)

    print("\n" + "=" * 80)
    print(f"Saved {len(slots_json)} empty slots to {out_path}")
    slots_1hr = [s for s in slots_json if s["scheduled_duration"] == 60]
    slots_2hr = [s for s in slots_json if s["scheduled_duration"] == 120]
    print(f"Summary: {len(slots_1hr)} x 1hr, {len(slots_2hr)} x 2hr")
    print("=" * 80 + "\n")


def mode_plan(scheduler: EistArisScheduler, args):
    print("\n" + "=" * 80)
    print("MODE: Generating updated-slots.json (show-to-slot mapping)")
    print("=" * 80 + "\n")

    tracks_file = "tracks.json"
    if not os.path.exists(tracks_file):
        print(f"Error: {tracks_file} not found. Run with --output-tracks first.")
        sys.exit(1)

    with open(tracks_file, "r", encoding="utf-8") as f:
        eligible_shows = json.load(f)

    print(f"Loaded {len(eligible_shows)} shows from {tracks_file}")

    schedule_file = "schedule.json"
    already_scheduled_track_ids = set()

    if os.path.exists(schedule_file):
        with open(schedule_file, "r", encoding="utf-8") as f:
            current_schedule = json.load(f)

        for show in current_schedule:
            track_id = show.get("track_id")
            if track_id:
                already_scheduled_track_ids.add(track_id)

        print(f"Loaded {len(current_schedule)} shows from {schedule_file}")
        print(f"Found {len(already_scheduled_track_ids)} shows with track IDs in current week")
    else:
        print(f"Warning: {schedule_file} not found - cannot check for duplicates")
        print("  All shows in tracks.json will be considered")

    original_count = len(eligible_shows)
    eligible_shows = [
        show
        for show in eligible_shows
        if show.get("track_id") not in already_scheduled_track_ids
    ]
    filtered_count = original_count - len(eligible_shows)

    if filtered_count:
        print(f"\n✓ Filtered out {filtered_count} already scheduled show(s)")
        print(f"  Remaining: {len(eligible_shows)} eligible\n")

    slots_file = "empty-slots.json"
    if not os.path.exists(slots_file):
        print(f"Error: {slots_file} not found. Run with --test-slots first.")
        sys.exit(1)

    with open(slots_file, "r", encoding="utf-8") as f:
        empty_slots_data = json.load(f)

    print(f"Loaded {len(empty_slots_data)} empty slots from {slots_file}")

    if not empty_slots_data:
        print("\n" + "=" * 80)
        print("✓ SCHEDULE IS COMPLETELY FILLED!")
        print("=" * 80)
        print("\nNo empty slots available for scheduling.\n")
        out_path = save_json([], "updated-slots.json", args.output)
        print(f"Saved empty mappings file: {out_path} (0 mappings)\n")
        return

    shows_1hr = [s for s in eligible_shows if s.get("scheduled_duration") == 60]
    shows_2hr = [s for s in eligible_shows if s.get("scheduled_duration") == 120]

    print(
        f"\nShows available: {len(shows_1hr)} x 1hr, "
        f"{len(shows_2hr)} x 2hr"
    )

    slots_1hr = [s for s in empty_slots_data if s.get("scheduled_duration") == 60]
    slots_2hr = [s for s in empty_slots_data if s.get("scheduled_duration") == 120]

    print(
        f"Slots available: {len(slots_1hr)} x 1hr, "
        f"{len(slots_2hr)} x 2hr"
    )

    # Shuffle shows separately by duration for random mixing
    available_1hr = shows_1hr.copy()
    available_2hr = shows_2hr.copy()
    random.shuffle(available_1hr)
    random.shuffle(available_2hr)

    updated_slots = []
    shows_1hr_used = 0
    shows_2hr_used = 0
    reuse_warned_1hr = False
    reuse_warned_2hr = False

    for slot in empty_slots_data:
        slot_duration = slot.get("scheduled_duration")
        show = None

        # Match shows to slots of the same duration only
        if slot_duration == 60:
            # 1hr slot: only assign 1hr shows
            if available_1hr:
                show = available_1hr.pop(0)
                shows_1hr_used += 1
            elif shows_1hr:
                # Reuse 1hr shows
                if not reuse_warned_1hr:
                    print("  ℹ Reusing 1hr shows - all unique 1hr shows already used.")
                    reuse_warned_1hr = True
                available_1hr = shows_1hr.copy()
                random.shuffle(available_1hr)
                show = available_1hr.pop(0)
                shows_1hr_used += 1

        elif slot_duration == 120:
            # 2hr slot: only assign 2hr shows
            if available_2hr:
                show = available_2hr.pop(0)
                shows_2hr_used += 1
            elif shows_2hr:
                # Reuse 2hr shows
                if not reuse_warned_2hr:
                    print("  ℹ Reusing 2hr shows - all unique 2hr shows already used.")
                    reuse_warned_2hr = True
                available_2hr = shows_2hr.copy()
                random.shuffle(available_2hr)
                show = available_2hr.pop(0)
                shows_2hr_used += 1

        if show:
            updated_slots.append({"slot": slot, "show": show})

    out_path = save_json(updated_slots, "updated-slots.json", args.output)

    print("\n" + "=" * 80)
    print(f"Saved {len(updated_slots)} mappings to {out_path}")
    print(f"  - {shows_1hr_used} x 1hr shows mapped")
    print(f"  - {shows_2hr_used} x 2hr shows mapped")
    print("=" * 80 + "\n")


def mode_execute(
    scheduler: EistArisScheduler,
    args,
    login_username: Optional[str],
    login_password: Optional[str],
    headless: bool = False,
    dry_run: bool = False,
):
    print("\n" + "=" * 80)
    print("MODE: Executing plan from updated-slots.json")
    print("=" * 80 + "\n")

    plan_file = "updated-slots.json"
    if not os.path.exists(plan_file):
        print(f"Error: {plan_file} not found. Run with --plan first.")
        sys.exit(1)

    with open(plan_file, "r", encoding="utf-8") as f:
        mappings = json.load(f)

    print(f"Loaded {len(mappings)} mappings from {plan_file}\n")

    if not mappings:
        print("No mappings to execute\n")
        return

    if dry_run:
        print("=" * 80)
        print("DRY RUN MODE - No shows will be created")
        print("=" * 80 + "\n")

        for i, mapping in enumerate(mappings, 1):
            slot = mapping["slot"]
            show = mapping["show"]

            print(f"\n[{i}/{len(mappings)}] Would create show:")
            print("-" * 60)
            print(f"  Title: {show['title']} (éist arís)")
            print(f"  Date: {slot['day_of_week']}, {slot['date']}")
            print(f"  Time: {slot['start']} - {slot['end']}")
            print(f"  Duration: {slot['scheduled_duration']} minutes")
            print(f"  Track ID: {show.get('track_id', 'N/A')}")
            print(f"  Track Title: {show.get('track_title', 'N/A')}")
            print(f"  Artist: {show.get('artist_name', 'N/A')}")

            description = show.get('description', '')
            if isinstance(description, dict):
                desc_text = "Éist arís replay show."
            elif description:
                desc_text = description[:100] + "..." if len(description) > 100 else description
            else:
                desc_text = "Éist arís replay show."
            print(f"  Description: {desc_text}")
            print("-" * 60)

        print("\n" + "=" * 80)
        print(f"DRY RUN COMPLETE - {len(mappings)} shows would be created")
        print("=" * 80 + "\n")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("Logging in...")
            page.goto(f"{WEB_BASE_URL}/login")
            page.wait_for_selector('input[type="email"]', timeout=10_000)
            page.fill('input[type="email"]', login_username or "")
            page.fill('input[type="password"]', login_password or "")
            page.click('button[type="submit"]')
            page.wait_for_timeout(2_000)
            print("✓ Logged in\n")

            success_count = 0
            error_count = 0

            for i, mapping in enumerate(mappings, 1):
                try:
                    print(f"[{i}/{len(mappings)}] Creating show...")
                    scheduler.create_show_from_mapping(page, mapping)
                    success_count += 1
                except Exception as exc:
                    error_count += 1
                    print(f"  ✗ Error creating show: {exc}")

                    # Save screenshot for debugging
                    try:
                        screenshot_path = f"error_screenshot_{i}.png"
                        page.screenshot(path=screenshot_path)
                        print(f"  📸 Screenshot saved to {screenshot_path}")
                    except Exception:
                        pass

                    # Close any open modals before continuing
                    scheduler.close_any_open_modals(page)

                    import traceback
                    traceback.print_exc()

                    print(f"\n  → Skipping to next event...\n")

            print("\n" + "=" * 80)
            print(
                f"COMPLETED: {success_count} shows created successfully, "
                f"{error_count} errors"
            )
            print("=" * 80 + "\n")

            page.wait_for_timeout(1_000)
        except Exception as exc:
            print(f"Error during execution: {exc}")
            import traceback

            traceback.print_exc()
        finally:
            browser.close()


def mode_check_slot(
    scheduler: EistArisScheduler,
    args,
    target_date: datetime,
    login_username: Optional[str],
    login_password: Optional[str],
    headless: bool = False,
    dry_run: bool = False,
):
    """Check the next hour's slot and auto-fix if empty or missing a file."""
    slot_start = round_up_to_hour(target_date)
    slot_end = slot_start + timedelta(hours=1)

    print("\n" + "=" * 80)
    print("MODE: Check Slot")
    print("=" * 80)
    print(f"\nInput time:  {target_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target slot: {slot_start.strftime('%Y-%m-%d %H:%M')} - {slot_end.strftime('%H:%M')} UTC")

    # Check broadcast window (09:00-23:00 UTC)
    if slot_start.hour < 9 or slot_start.hour >= 23:
        print(f"\n✓ Slot at {slot_start.strftime('%H:%M')} UTC is outside broadcast hours (09:00-23:00). No action needed.")
        return

    # Fetch schedule with a small buffer around the slot
    fetch_start = slot_start - timedelta(minutes=10)
    fetch_end = slot_end + timedelta(minutes=10)
    print(f"\nFetching schedule for {fetch_start.strftime('%H:%M')}-{fetch_end.strftime('%H:%M')} UTC...")

    schedule = scheduler.fetch_schedule(fetch_start, fetch_end)

    # Filter to shows that overlap our target slot
    overlapping = []
    for show in schedule:
        show_start_str = show.get("start") or show.get("startDateUtc")
        show_end_str = show.get("end") or show.get("endDateUtc")
        if not show_start_str or not show_end_str:
            continue

        show_start = datetime.fromisoformat(show_start_str.replace("Z", "+00:00")).replace(tzinfo=None)
        show_end = datetime.fromisoformat(show_end_str.replace("Z", "+00:00")).replace(tzinfo=None)

        # Show overlaps slot if it starts before slot ends AND ends after slot starts
        if show_start < slot_end and show_end > slot_start:
            overlapping.append(show)

    if not overlapping:
        print(f"\n⚠ Slot {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} is EMPTY. Needs filling.")
        action = "fill"
        broken_show = None
    else:
        # Check each overlapping show
        print(f"\nFound {len(overlapping)} show(s) in slot:")
        action = None
        broken_show = None

        for show in overlapping:
            title = show.get("title", "(no title)")
            media = show.get("media", {})
            media_type = media.get("type", "unknown")
            track_id = media.get("trackId")

            print(f"  - '{title}' | media.type={media_type} | trackId={'yes' if track_id else 'MISSING'}")

            if media_type == "live":
                print(f"    → Live show. No action needed.")
            elif media_type == "playlist":
                print(f"    → Playlist show. No action needed.")
            elif media_type == "mix" and track_id:
                print(f"    → Pre-record with file attached. No action needed.")
            elif media_type == "mix" and not track_id:
                print(f"    → ⚠ Pre-record WITHOUT file! Needs replacement.")
                action = "replace"
                broken_show = show
                break
            else:
                print(f"    → Unknown media type '{media_type}'. Skipping.")

        if action is None:
            print(f"\n✓ Slot is OK. No action needed.")
            return

    # --- Action needed: fill empty slot or replace broken pre-record ---

    print(f"\nAction: {action.upper()}")

    # Fetch eligible replacement shows from last 4 weeks
    print("\nFetching eligible replacement shows from last 4 weeks...")
    eligible_shows = scheduler.build_replay_list(slot_start, weeks_back=4)

    # Filter to 1hr shows only
    eligible_1hr = [s for s in eligible_shows if s.get("scheduled_duration") == 60]
    print(f"Eligible 1hr shows: {len(eligible_1hr)}")

    if not eligible_1hr:
        print("\n⚠ No eligible 1hr shows found for replacement. Exiting.")
        return

    # Exclude shows already in this week's schedule to prevent duplicates
    week_start = scheduler.get_week_start(slot_start)
    week_end = week_start + timedelta(days=7)
    week_schedule = scheduler.fetch_schedule(week_start, week_end)

    scheduled_track_ids = set()
    for show in week_schedule:
        media = show.get("media", {})
        tid = media.get("trackId")
        if tid:
            scheduled_track_ids.add(tid)

    eligible_1hr = [s for s in eligible_1hr if s.get("track_id") not in scheduled_track_ids]
    print(f"After duplicate filtering: {len(eligible_1hr)} eligible shows")

    if not eligible_1hr:
        print("\n⚠ All eligible shows are already scheduled this week. Exiting.")
        return

    # Pick a random replacement
    replacement = random.choice(eligible_1hr)
    print(f"\nSelected replacement: '{replacement['title']}'")
    print(f"  Track ID: {replacement.get('track_id')}")
    print(f"  Duration: {replacement.get('duration')} min")

    # Build the slot and show mapping for create_show_from_mapping()
    slot_data = {
        "start": slot_start.strftime("%Y-%m-%d %H:%M"),
        "end": slot_end.strftime("%Y-%m-%d %H:%M"),
        "duration_minutes": 60,
        "scheduled_duration": 60,
        "day_of_week": slot_start.strftime("%A"),
        "date": slot_start.strftime("%Y-%m-%d"),
    }

    mapping = {"slot": slot_data, "show": replacement}

    if dry_run:
        print("\n" + "=" * 80)
        print("DRY RUN - No changes will be made")
        print("=" * 80)

        if action == "replace" and broken_show:
            print(f"\nWould DELETE: '{broken_show.get('title', '(no title)')}'")

        print(f"\nWould CREATE:")
        print(f"  Title: {replacement['title']} (éist arís)")
        print(f"  Slot: {slot_data['day_of_week']}, {slot_data['date']}")
        print(f"  Time: {slot_data['start']} - {slot_data['end']}")
        print(f"  Track ID: {replacement.get('track_id', 'N/A')}")

        print("\n" + "=" * 80)
        print("DRY RUN COMPLETE")
        print("=" * 80)
        return

    # --- Live execution with Playwright ---

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("\nLogging in...")
            page.goto(f"{WEB_BASE_URL}/login")
            page.wait_for_selector('input[type="email"]', timeout=10_000)
            page.fill('input[type="email"]', login_username or "")
            page.fill('input[type="password"]', login_password or "")
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_timeout(2_000)
            print("✓ Logged in\n")

            # Copy browser cookies to API session for track lookups
            for cookie in context.cookies():
                scheduler.session.cookies.set(
                    cookie.get("name", ""),
                    cookie.get("value", ""),
                    domain=cookie.get("domain"),
                    path=cookie.get("path"),
                )

            # Fetch actual track title from media API (needs auth cookies)
            track_id = replacement.get("track_id")
            if track_id and not replacement.get("track_title"):
                track_details = scheduler.fetch_track_details(track_id)
                if track_details:
                    tracks = track_details.get("tracks") or []
                    matching = next(
                        (t for t in tracks if t.get("id") == track_id), None
                    )
                    if matching and matching.get("title"):
                        replacement["track_title"] = matching["title"]
                        print(f"Track title resolved: {replacement['track_title']}")
                    else:
                        print("⚠ Could not find track title in media API")

            # Step 1: Delete broken show if replacing
            if action == "replace" and broken_show:
                try:
                    scheduler.delete_show_via_playwright(
                        page,
                        broken_show.get("title", ""),
                        slot_start,
                    )
                except Exception as exc:
                    print(f"\n✗ Failed to delete show: {exc}")
                    try:
                        page.screenshot(path="error_screenshot_delete.png")
                        print("  Screenshot saved to error_screenshot_delete.png")
                    except Exception:
                        pass
                    raise

            # Step 2: Create replacement show
            try:
                print(f"\nCreating replacement show...")
                scheduler.create_show_from_mapping(page, mapping)
                print("\n" + "=" * 80)
                print(f"✓ SUCCESS: Created '{replacement['title']} (éist arís)'")
                print(f"  Slot: {slot_data['start']} - {slot_data['end']}")
                print("=" * 80)
            except Exception as exc:
                print(f"\n✗ Failed to create show: {exc}")
                try:
                    page.screenshot(path="error_screenshot_create.png")
                    print("  Screenshot saved to error_screenshot_create.png")
                except Exception:
                    pass
                scheduler.close_any_open_modals(page)
                raise

        except Exception as exc:
            print(f"\nError during check-slot execution: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            browser.close()


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    load_dotenv()

    api_key = os.getenv("API_KEY")
    login_username = os.getenv("RADIOCULT_USER")
    login_password = os.getenv("RADIOCULT_PW")

    if not api_key:
        print("Error: API_KEY not found in .env file", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Populate weekly schedule with éist arís replay shows"
    )
    parser.add_argument(
        "date",
        type=str,
        help="Target date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--weeks-back",
        type=int,
        default=3,
        help="Weeks to look back for shows (default: 3)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to process (default: 7)",
    )
    parser.add_argument("--output", type=str, help="Output JSON file path")
    parser.add_argument(
        "--test-slots",
        action="store_true",
        help="Only detect empty slots and write empty-slots.json",
    )
    parser.add_argument(
        "--output-tracks",
        action="store_true",
        help="Generate tracks.json with eligible shows and track details",
    )
    parser.add_argument(
        "--output-schedule",
        action="store_true",
        help="Generate schedule.json with current schedule",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Generate updated-slots.json mapping shows to slots",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute mappings in updated-slots.json and create shows",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (for CI environments)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes",
    )
    parser.add_argument(
        "--check-slot",
        action="store_true",
        help="Check if the next hour's slot is empty or has a fileless pre-record, and auto-fix",
    )

    args = parser.parse_args()

    target_date = parse_target_date(args.date)
    scheduler = EistArisScheduler(api_key, login_username, login_password)

    if args.output_tracks:
        mode_output_tracks(scheduler, args, target_date)
        return

    if args.output_schedule:
        mode_output_schedule(scheduler, args, target_date)
        return

    if args.test_slots:
        mode_test_slots(scheduler, args, target_date)
        return

    if args.plan:
        mode_plan(scheduler, args)
        return

    if args.execute:
        mode_execute(
            scheduler, args, login_username, login_password, args.headless, args.dry_run
        )
        return

    if args.check_slot:
        mode_check_slot(
            scheduler,
            args,
            target_date,
            login_username,
            login_password,
            args.headless,
            args.dry_run,
        )
        return

    print(
        "\nError: No mode specified. Use one of "
        "--output-tracks, --output-schedule, --test-slots, --plan, --execute, or --check-slot",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
