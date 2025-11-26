#!/usr/bin/env python3
"""
Script to populate weekly schedule with éist arís (replay) shows from the Radiocult API.

This script:
1. Fetches shows from the past 3 weeks via the schedule API
2. Filters for prerecord shows with MP3s that don't already have "éist arís" suffix
3. Fetches track metadata for each show using the track API
4. Builds a JSON list of eligible shows with track filenames
5. Uses Playwright to populate the schedule for the next 7 days (TODO)
"""

import os
import sys
import re
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import argparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


# Constants
STATION_ID = "eist-radio"
API_BASE_URL = "https://api.radiocult.fm/api/station"
WEB_BASE_URL = "https://app.radiocult.fm"


class EistArisScheduler:
    """Handles fetching and scheduling éist arís replay shows."""

    def __init__(self, api_key: str, login_username: Optional[str] = None, login_password: Optional[str] = None):
        """
        Initialize the scheduler.

        Args:
            api_key: Radiocult API key
            login_username: Login username for Playwright automation
            login_password: Login password for Playwright automation
        """
        self.api_key = api_key
        self.login_username = login_username
        self.login_password = login_password
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        })
        self.authenticated = False

        # Regex pattern to detect shows that already have "éist arís" suffix
        # Matches: "éist arís", "eist aris", "(éist arís)", "(eist aris)" and variations
        self.eist_aris_pattern = re.compile(
            r'\(?\s*[eé]ist\s+ar[ií]s\s*\)?',
            re.IGNORECASE
        )

    def authenticate_with_playwright(self):
        """
        Log in to Radiocult using Playwright and extract session cookies.
        This is needed to access undocumented API endpoints.
        """
        if self.authenticated:
            return

        if not self.login_username or not self.login_password:
            print("Warning: Cannot authenticate - credentials not set", file=sys.stderr)
            return

        print("Authenticating with Playwright to get session cookies...")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Navigate to login page (it redirects to /sign-in)
                page.goto(f"{WEB_BASE_URL}/login")
                page.wait_for_selector('input[type="email"]', timeout=10000)

                # Fill in credentials
                page.fill('input[type="email"]', self.login_username)
                page.fill('input[type="password"]', self.login_password)

                # Submit form
                page.click('button[type="submit"]')

                # Wait a bit for cookies to be set
                page.wait_for_timeout(3000)

                # Extract cookies and add to requests session
                cookies = context.cookies()
                for cookie in cookies:
                    self.session.cookies.set(
                        cookie['name'],
                        cookie['value'],
                        domain=cookie.get('domain'),
                        path=cookie.get('path')
                    )

                # Verify authentication by testing an API call
                test_url = f"{API_BASE_URL}/{STATION_ID}/media/track"
                # We need at least one track ID to test - we'll just try and catch the error
                # If we have cookies, the endpoint should return 400 (missing trackId) not 401
                test_response = self.session.get(test_url)

                if test_response.status_code == 401:
                    print("Authentication failed - API returned 401", file=sys.stderr)
                    browser.close()
                    return

                # Any other status code (including 400 for missing params) means we're authenticated
                self.authenticated = True
                print("Authentication successful!")

            except Exception as e:
                print(f"Authentication error: {e}", file=sys.stderr)
            finally:
                browser.close()

    def fetch_schedule(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """
        Fetch schedule from the Radiocult API for a date range.

        Args:
            start_date: Start date for schedule fetch
            end_date: End date for schedule fetch

        Returns:
            List of schedule items
        """
        # Format dates as ISO 8601 strings
        start_str = start_date.strftime('%Y-%m-%dT00:00:00Z')
        end_str = end_date.strftime('%Y-%m-%dT23:59:59Z')

        url = f"{API_BASE_URL}/{STATION_ID}/schedule"
        params = {
            'startDate': start_str,
            'endDate': end_str,
        }

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Return the schedules array if it exists
            return data.get('schedules', [])
        except requests.exceptions.RequestException as e:
            print(f"Error fetching schedule: {e}", file=sys.stderr)
            return []

    def fetch_track_details(self, track_id: str) -> Optional[Dict]:
        """
        Fetch track metadata from the undocumented track API.

        Args:
            track_id: The track ID to fetch details for

        Returns:
            Dictionary with track metadata, or None if fetch fails
        """
        url = f"{API_BASE_URL}/{STATION_ID}/media/track"
        params = {'trackId': track_id}

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Warning: Could not fetch track details for {track_id}: {e}", file=sys.stderr)
            return None

    def fetch_artist_details(self, artist_id: str) -> Optional[Dict]:
        """
        Fetch artist details from the API.

        Args:
            artist_id: The artist ID to fetch details for

        Returns:
            Dictionary with artist metadata, or None if fetch fails
        """
        url = f"{API_BASE_URL}/{STATION_ID}/artists/{artist_id}"

        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Warning: Could not fetch artist details for {artist_id}: {e}", file=sys.stderr)
            return None

    def has_eist_aris_suffix(self, title: str) -> bool:
        """
        Check if a show title already has an "éist arís" suffix.

        Args:
            title: Show title to check

        Returns:
            True if the title contains an éist arís suffix
        """
        return bool(self.eist_aris_pattern.search(title))

    def is_eligible_show(self, show: Dict) -> bool:
        """
        Check if a show is eligible to be used as an éist arís replay.

        A show is eligible if:
        - It has a title
        - It doesn't already have an "éist arís" suffix
        - It's in prerecord format (media.type = "mix")
        - It has a track/MP3 attachment (media.trackId exists)

        Args:
            show: Show data from API

        Returns:
            True if the show is eligible
        """
        # Check if show has a title
        title = show.get('title', '')
        if not title:
            return False

        # Check if already has éist arís suffix
        if self.has_eist_aris_suffix(title):
            return False

        # Check if it has media object
        media = show.get('media')
        if not media:
            return False

        # Check if it's prerecord format (type = "mix" indicates a prerecorded show)
        media_type = media.get('type', '')
        if media_type != 'mix':
            return False

        # Check if it has a track/MP3 attachment
        track_id = media.get('trackId')
        if not track_id:
            return False

        return True

    def build_replay_list(self, start_date: datetime, weeks_back: int = 3) -> List[Dict]:
        """
        Build a list of eligible shows from the past N weeks.

        Args:
            start_date: The reference date to look back from
            weeks_back: Number of weeks to look back (default: 3)

        Returns:
            List of eligible shows with their metadata
        """
        # Calculate date range (go back N weeks from start_date)
        end_date = start_date
        start_date = start_date - timedelta(weeks=weeks_back)

        print(f"Fetching shows from {start_date.date()} to {end_date.date()}...")

        # Fetch all shows in the date range
        all_shows = self.fetch_schedule(start_date, end_date)

        print(f"Found {len(all_shows)} total shows")

        # Filter for eligible shows
        # Note: We don't fetch track details via API here because it requires
        # browser session authentication. Instead, we'll search for tracks by
        # title when creating shows with Playwright.
        eligible_shows = []
        for show in all_shows:
            if self.is_eligible_show(show):
                media = show.get('media', {})
                track_id = media.get('trackId')

                # Determine if show should be 1hr or 2hr based on file duration
                file_duration = show.get('duration', 60)  # in minutes

                # Only include shows within 1 minute of 60 or 120 minutes
                within_1hr = abs(file_duration - 60) <= 1
                within_2hr = abs(file_duration - 120) <= 1

                if not (within_1hr or within_2hr):
                    # Skip shows that aren't close to 1hr or 2hr
                    continue

                # Determine which is closer
                if abs(file_duration - 120) < abs(file_duration - 60):
                    scheduled_duration = 120
                else:
                    scheduled_duration = 60

                show_data = {
                    'title': show.get('title'),
                    'original_start': show.get('start'),
                    'original_end': show.get('end'),
                    'duration': show.get('duration'),
                    'scheduled_duration': scheduled_duration,  # 60 or 120
                    'media_type': media.get('type'),
                    'track_id': track_id,
                    'description': show.get('description', ''),
                    'show_id': show.get('id'),
                    'color': show.get('color'),
                    'artist_ids': show.get('artistIds', []),
                    'artists': show.get('artists', []),  # May contain artist objects with names
                }

                eligible_shows.append(show_data)

        print(f"Found {len(eligible_shows)} eligible shows for éist arís replay")

        return eligible_shows

    def get_week_start(self, target_date: datetime) -> datetime:
        """
        Get the Monday of the week containing the target date.

        Args:
            target_date: Any date in the week

        Returns:
            The Monday of that week
        """
        # 0 = Monday, 6 = Sunday
        days_since_monday = target_date.weekday()
        week_start = target_date - timedelta(days=days_since_monday)
        return week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    def find_empty_slots(self, target_date: datetime, days: int = 7) -> List[Dict]:
        """
        Find empty time slots in the schedule for the target week.

        Args:
            target_date: Any date in the target week
            days: Number of days to check (default: 7 for full week)

        Returns:
            List of empty slot dictionaries with start_time, end_time, duration info
        """
        # Get the Monday of the week
        week_start = self.get_week_start(target_date)
        end_date = week_start + timedelta(days=days)

        print(f"\nFinding empty slots for week of {week_start.date()} to {end_date.date()}...")

        # Fetch current schedule
        current_schedule = self.fetch_schedule(week_start, end_date)

        # Build a timeline of occupied slots
        occupied_slots = []
        for show in current_schedule:
            if show.get('start') and show.get('end'):
                occupied_slots.append({
                    'start': datetime.fromisoformat(show['start'].replace('Z', '+00:00')),
                    'end': datetime.fromisoformat(show['end'].replace('Z', '+00:00')),
                    'title': show.get('title', '')
                })

        # Sort by start time
        occupied_slots.sort(key=lambda x: x['start'])

        # Find gaps in the schedule
        # Assume broadcasting hours are 9am to midnight (00:00)
        empty_slots = []

        for day_offset in range(days):
            current_day = week_start + timedelta(days=day_offset)
            # Make timezone-aware to match occupied_slots
            day_start = current_day.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            day_end = current_day.replace(hour=23, minute=59, second=59, microsecond=0, tzinfo=timezone.utc)

            # Get shows for this day
            # Include shows that START on this day (between 9am and end of day)
            day_shows = [s for s in occupied_slots if day_start <= s['start'] < day_end]

            print(f"\n{current_day.strftime('%A %Y-%m-%d')}:")
            print(f"  Day bounds: {day_start.strftime('%Y-%m-%d %H:%M')} to {day_end.strftime('%Y-%m-%d %H:%M')}")
            print(f"  Found {len(day_shows)} shows on this day")

            if day_shows:
                for show in day_shows:
                    print(f"    - '{show['title']}': {show['start'].strftime('%Y-%m-%d %H:%M')} - {show['end'].strftime('%Y-%m-%d %H:%M')}")

            if not day_shows:
                # Entire day is empty - split into 1-hour blocks from 9am to midnight
                current_time = day_start
                while current_time < day_end:
                    # Create 1 or 2 hour slots
                    # Try 2-hour slot first
                    slot_end = current_time + timedelta(hours=2)
                    if slot_end <= day_end:
                        empty_slots.append({
                            'start': current_time,
                            'end': slot_end,
                            'duration_minutes': 120
                        })
                        current_time = slot_end
                    else:
                        # Try 1-hour slot
                        slot_end = current_time + timedelta(hours=1)
                        if slot_end <= day_end:
                            empty_slots.append({
                                'start': current_time,
                                'end': slot_end,
                                'duration_minutes': 60
                            })
                            current_time = slot_end
                        else:
                            break
            else:
                # Check for gaps between shows
                # Gap before first show
                if day_shows[0]['start'] > day_start:
                    gap_duration = (day_shows[0]['start'] - day_start).total_seconds() / 60
                    print(f"  ✓ Gap before first show: {day_start.strftime('%H:%M')} - {day_shows[0]['start'].strftime('%H:%M')} ({gap_duration:.0f} min)")
                    empty_slots.append({
                        'start': day_start,
                        'end': day_shows[0]['start'],
                        'duration_minutes': gap_duration
                    })
                else:
                    print(f"  ✗ No gap before first show (show starts at {day_shows[0]['start'].strftime('%H:%M')}, day starts at {day_start.strftime('%H:%M')})")

                # Gaps between shows
                for i in range(len(day_shows) - 1):
                    gap_start = day_shows[i]['end']
                    gap_end = day_shows[i + 1]['start']
                    if gap_start < gap_end:
                        gap_duration = (gap_end - gap_start).total_seconds() / 60
                        print(f"  ✓ Gap found: {gap_start.strftime('%H:%M')} - {gap_end.strftime('%H:%M')} ({gap_duration:.0f} min)")
                        print(f"    Between '{day_shows[i]['title']}' and '{day_shows[i+1]['title']}'")
                        empty_slots.append({
                            'start': gap_start,
                            'end': gap_end,
                            'duration_minutes': gap_duration
                        })
                    else:
                        print(f"  → Shows are back-to-back: '{day_shows[i]['title']}' ends at {gap_start.strftime('%H:%M')}, '{day_shows[i+1]['title']}' starts at {gap_end.strftime('%H:%M')}")

                # Gap after last show
                if day_shows[-1]['end'] < day_end:
                    gap_duration = (day_end - day_shows[-1]['end']).total_seconds() / 60
                    print(f"  ✓ Gap after last show: {day_shows[-1]['end'].strftime('%H:%M')} - {day_end.strftime('%H:%M')} ({gap_duration:.0f} min)")
                    empty_slots.append({
                        'start': day_shows[-1]['end'],
                        'end': day_end,
                        'duration_minutes': gap_duration
                    })
                else:
                    print(f"  ✗ No gap after last show (show ends at {day_shows[-1]['end'].strftime('%H:%M')}, day ends at {day_end.strftime('%H:%M')})")

        # Split gaps into 1hr and 2hr slots
        print(f"\n{'='*80}")
        print(f"SPLITTING GAPS INTO 1HR AND 2HR SLOTS")
        print(f"{'='*80}")

        valid_slots = []

        for slot in empty_slots:
            gap_start = slot['start']
            gap_end = slot['end']
            duration = slot['duration_minutes']

            print(f"\nGap: {gap_start.strftime('%a %H:%M')} - {gap_end.strftime('%H:%M')} ({duration:.0f} min)")

            # Split this gap into 1hr and 2hr slots
            current_time = gap_start
            slot_count = 0

            while current_time < gap_end:
                remaining_minutes = (gap_end - current_time).total_seconds() / 60

                # Try to create a 2-hour slot first
                if remaining_minutes >= 120:
                    slot_end = current_time + timedelta(hours=2)
                    valid_slots.append({
                        'start': current_time,
                        'end': slot_end,
                        'duration_minutes': 120,
                        'scheduled_duration': 120
                    })
                    print(f"  → Created 2hr slot: {current_time.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}")
                    current_time = slot_end
                    slot_count += 1
                # Try to create a 1-hour slot
                elif remaining_minutes >= 60:
                    slot_end = current_time + timedelta(hours=1)
                    valid_slots.append({
                        'start': current_time,
                        'end': slot_end,
                        'duration_minutes': 60,
                        'scheduled_duration': 60
                    })
                    print(f"  → Created 1hr slot: {current_time.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}")
                    current_time = slot_end
                    slot_count += 1
                else:
                    # Remaining time is less than 1 hour - skip it
                    print(f"  ✗ Skipped remaining {remaining_minutes:.0f} min (less than 1hr)")
                    break

            print(f"  Total: Created {slot_count} slot(s) from this gap")

        # Sort slots by start time (chronological order)
        valid_slots.sort(key=lambda x: x['start'])

        print(f"\n{'='*80}")
        print(f"Total: {len(empty_slots)} gaps found, split into {len(valid_slots)} usable slots")
        print(f"{'='*80}")

        # Print first few slots to verify order
        print(f"\nFirst 5 slots (chronological):")
        for i, slot in enumerate(valid_slots[:5]):
            print(f"  {i+1}. {slot['start'].strftime('%a %Y-%m-%d %H:%M')} - {slot['end'].strftime('%H:%M')} ({slot['scheduled_duration']}min)")

        return valid_slots

    def create_show_from_mapping(self, page, mapping: Dict):
        """
        Create a single show from a slot-show mapping using Playwright.
        Based on test-create-show.py's working logic.

        Args:
            page: Playwright page object
            mapping: Dictionary with 'slot' and 'show' keys from updated-slots.json
        """
        slot = mapping['slot']
        show = mapping['show']

        # Parse times from slot
        from datetime import datetime
        start_time = datetime.fromisoformat(slot['start'].replace('Z', '+00:00'))

        # Calculate end time from slot duration
        scheduled_duration = slot['scheduled_duration']
        end_time = start_time + timedelta(minutes=scheduled_duration)

        # Format times - convert to 12hr format for certain times due to GUI bugs
        def format_time_for_gui(dt):
            """Format time for GUI, using 12hr format for 15:00 and 00:00 to avoid bugs."""
            time_str = dt.strftime('%H:%M')
            if time_str == '15:00':
                return '3:00pm'
            elif time_str == '00:00':
                return '12:00am'
            else:
                return time_str

        start_date = start_time.strftime('%Y-%m-%d')
        start_time_str = format_time_for_gui(start_time)
        end_time_str = format_time_for_gui(end_time)
        day_of_week = start_time.strftime('%A')

        print(f"\n{'='*60}")
        print(f"Creating: {show['title']} (éist arís)")
        print(f"When: {day_of_week}, {start_date}")
        print(f"Time: {start_time_str} - {end_time_str} ({scheduled_duration}min)")
        print(f"{'='*60}")

        # Navigate to schedule page for this week
        week_start = start_time - timedelta(days=start_time.weekday())
        week_str = week_start.strftime('%Y-%m-%d')
        page.goto(f"{WEB_BASE_URL}/schedule?w={week_str}")
        page.wait_for_load_state('networkidle', timeout=15000)

        # Click Create button
        print("\n[Step 1] Opening create modal...")
        create_btn = page.locator('button:has-text("Create")')
        create_btn.click()
        page.wait_for_timeout(1000)
        print("  ✓ Modal opened")

        # Set start time - type and let it auto-select
        print(f"\n[Step 2] Setting start time to {start_time_str}...")
        start_time_input = page.locator('input[aria-labelledby*="startTime"]')
        start_time_input.click()
        page.wait_for_timeout(500)

        # Clear the field
        page.keyboard.press('Control+A')
        page.wait_for_timeout(100)

        # Type the time - this should filter to exact match
        page.keyboard.type(start_time_str, delay=150)
        page.wait_for_timeout(1200)  # Give more time for dropdown to stabilize

        # The first (top) option should be the exact match - press Enter to select it
        page.keyboard.press('Enter')
        page.wait_for_timeout(600)
        print(f"  ✓ Start time set to {start_time_str}")

        # Set end time - type and let it auto-select
        print(f"\n[Step 3] Setting end time to {end_time_str}...")
        end_time_input = page.locator('input[aria-labelledby*="endTime"]')
        end_time_input.click()
        page.wait_for_timeout(500)

        # Clear the field
        page.keyboard.press('Control+A')
        page.wait_for_timeout(100)

        # Type the time - this should filter to exact match
        page.keyboard.type(end_time_str, delay=150)
        page.wait_for_timeout(1200)  # Give more time for dropdown to stabilize

        # The first (top) option should be the exact match - press Enter to select it
        page.keyboard.press('Enter')
        page.wait_for_timeout(600)
        print(f"  ✓ End time set to {end_time_str}")

        # Set date (new step - not in test script, but needed)
        target_day = start_time.day
        print(f"\n[Step 4] Setting date to {start_date} (day {target_day})...")
        try:
            date_selector = page.locator('input[id^="startDate"]')
            date_selector.click()
            page.wait_for_timeout(500)

            date_button = page.locator(f'button[role="gridcell"]:has-text("{target_day}"):not([data-sibling])')
            matching_buttons = date_button.all()
            clicked = False

            for btn in matching_buttons:
                btn_text = btn.inner_text().strip()
                if btn_text == str(target_day):
                    btn.click()
                    print(f"  ✓ Date selected: day {target_day}")
                    clicked = True
                    break

            if not clicked and len(matching_buttons) > 0:
                date_button.first.click()
                print(f"  ✓ Date selected: day {target_day}")

            page.wait_for_timeout(1000)  # Wait for UI to stabilize
        except Exception as e:
            print(f"  ✗ Could not select date: {e}")

        # Fill in title (from test-create-show.py:113)
        show_title = f"{show['title']} (éist arís)"
        print(f"\n[Step 5] Filling in title: {show_title}...")
        title_input = page.locator('input[name="title"]')
        title_input.fill(show_title)
        page.wait_for_timeout(100)
        print("  ✓ Title filled")

        # Fill in description (from test-create-show.py:119)
        print(f"\n[Step 6] Setting description...")
        description = show.get('description', '')

        # Extract text from ProseMirror format if needed
        if description and isinstance(description, dict):
            text_parts = []
            try:
                content = description.get('content', [])
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'paragraph':
                        paragraph_content = block.get('content', [])
                        for text_node in paragraph_content:
                            if isinstance(text_node, dict) and text_node.get('type') == 'text':
                                text_parts.append(text_node.get('text', ''))
                description = ' '.join(text_parts)
            except:
                description = 'Éist arís replay show.'

        if not description or not isinstance(description, str):
            description = 'Éist arís replay show.'

        description_field = page.locator('p[data-placeholder*="Enter event description"]')
        description_field.click()
        page.wait_for_timeout(100)
        page.keyboard.type(description)
        page.wait_for_timeout(200)
        print("  ✓ Description filled")

        # Add artist (from test-create-show.py:129)
        # Try artist_name first (from API), then fall back to track_artist
        artist_name = show.get('artist_name', '') or show.get('track_artist', '')
        if artist_name:
            print(f"\n[Step 7] Adding artist: {artist_name}...")
            try:
                artist_input = page.locator('input#artist-select')
                artist_input.click()
                page.wait_for_timeout(100)
                page.keyboard.type(artist_name)
                page.wait_for_timeout(500)
                page.keyboard.press('Enter')
                page.wait_for_timeout(300)
                print(f"  ✓ Artist '{artist_name}' added")
            except Exception as e:
                print(f"  ✗ Could not add artist: {e}")
        else:
            print(f"\n[Step 7] No artist available - skipping")

        # Click Mix Pre-record button (from test-create-show.py:142)
        print("\n[Step 8] Enabling pre-record mode...")
        prerecord_button = page.get_by_role("button", name="Mix Pre-record")
        prerecord_button.click()
        page.wait_for_timeout(500)
        print("  ✓ Pre-record enabled")

        # Open media selection (from test-create-show.py:151)
        print("\n[Step 9] Opening media selection...")
        media_button = page.locator('text=Select media').first
        media_button.click()
        page.wait_for_timeout(1000)
        print("  ✓ Media selection modal opened")

        # Search for track using track title from track API (from test-create-show.py:169)
        track_title = show.get('track_title', '')
        if not track_title:
            # Fallback to show title if track_title not available
            track_title = show.get('title', '')
            print(f"  ! No track_title available, using show title as fallback")

        print(f"\n[Step 10] Searching for track: {track_title}...")
        search_input = page.locator('input[data-ds--text-field--input="true"]').last
        search_input.fill(track_title)
        page.wait_for_timeout(1000)
        print("  ✓ Search completed")

        # Select track from results (from test-create-show.py:177)
        print("\n[Step 11] Selecting track from results...")
        try:
            track_row = page.locator(f'tr:has-text("{track_title}")').first
            track_row.click()
            page.wait_for_timeout(500)
            print("  ✓ Track selected")
        except Exception as e:
            print(f"  ! Could not select track: {e}")

        # Click Create event button (from test-create-show.py:191)
        print("\n[Step 12] Clicking Create event button...")
        create_event_button = page.locator('button[type="submit"]:has-text("Create event")')
        create_event_button.click()
        page.wait_for_timeout(2000)
        print("  ✓ Create event button clicked!")

        print(f"\n{'='*60}")
        print(f"SUCCESS! Created '{show_title}'")
        print(f"{'='*60}\n")



def main():
    """Main entry point for the script."""
    # Load environment variables
    load_dotenv()

    api_key = os.getenv('API_KEY')
    login_username = os.getenv('RADIOCULT_USER')
    login_password = os.getenv('RADIOCULT_PW')

    if not api_key:
        print("Error: API_KEY not found in .env file", file=sys.stderr)
        sys.exit(1)

    # Set up argument parser
    parser = argparse.ArgumentParser(
        description='Populate weekly schedule with éist arís replay shows'
    )
    parser.add_argument(
        'date',
        type=str,
        help='Target date to start populating from (format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)'
    )
    parser.add_argument(
        '--weeks-back',
        type=int,
        default=3,
        help='Number of weeks to look back for shows (default: 3)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days to populate (default: 7)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Output JSON file path (optional)'
    )
    parser.add_argument(
        '--test-slots',
        action='store_true',
        help='Only test slot detection - output empty slots to JSON and exit'
    )
    parser.add_argument(
        '--output-tracks',
        action='store_true',
        help='Generate tracks.json with eligible shows and track details'
    )
    parser.add_argument(
        '--output-schedule',
        action='store_true',
        help='Generate schedule.json with current schedule for the week'
    )
    parser.add_argument(
        '--plan',
        action='store_true',
        help='Generate updated-slots.json mapping shows to slots'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute the plan from updated-slots.json and create shows'
    )

    args = parser.parse_args()

    # Parse the input date
    try:
        # Try parsing with time first
        if ' ' in args.date:
            target_date = datetime.strptime(args.date, '%Y-%m-%d %H:%M:%S')
        else:
            target_date = datetime.strptime(args.date, '%Y-%m-%d')
    except ValueError as e:
        print(f"Error parsing date: {e}", file=sys.stderr)
        print("Please use format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS", file=sys.stderr)
        sys.exit(1)

    # Initialize scheduler
    scheduler = EistArisScheduler(api_key, login_username, login_password)

    # If output-tracks mode, generate tracks.json
    if args.output_tracks:
        print("\n" + "="*80)
        print("MODE: Generating tracks.json")
        print("="*80 + "\n")

        # Build list of eligible shows
        eligible_shows = scheduler.build_replay_list(target_date, args.weeks_back)

        # Authenticate and fetch track/artist details
        print("\nAuthenticating to fetch track and artist details...")
        scheduler.authenticate_with_playwright()

        # Fetch track and artist details for each show
        for i, show in enumerate(eligible_shows, 1):
            print(f"  [{i}/{len(eligible_shows)}] Processing '{show['title']}'...")

            # Fetch track details to get the track title
            track_id = show.get('track_id')
            if track_id:
                print(f"    - Fetching track {track_id}...")
                track_details = scheduler.fetch_track_details(track_id)
                if track_details:
                    # The API returns: {"tracks": [{"id": "...", "title": "...", ...}, ...]}
                    # Find the track that matches our track_id
                    tracks_array = track_details.get('tracks', [])

                    if tracks_array and len(tracks_array) > 0:
                        # Find the matching track by ID
                        matching_track = None
                        for track in tracks_array:
                            if track.get('id') == track_id:
                                matching_track = track
                                break

                        if matching_track:
                            track_title = matching_track.get('title', '')
                            if track_title:
                                show['track_title'] = track_title
                                print(f"      ✓ Track title: {track_title}")
                            else:
                                print(f"      ! No title found in track data")
                        else:
                            print(f"      ! Could not find track with ID {track_id} in response")
                            print(f"      Found {len(tracks_array)} tracks in response")
                    else:
                        print(f"      ! No tracks found in API response")
                else:
                    print(f"      ! Could not fetch track details")

            # Fetch artist details from artist IDs
            artist_ids = show.get('artist_ids', [])
            if artist_ids and len(artist_ids) > 0:
                # Get the first artist
                first_artist_id = artist_ids[0]
                print(f"    - Fetching artist {first_artist_id}...")
                artist_details = scheduler.fetch_artist_details(first_artist_id)
                if artist_details:
                    # Extract artist name from artist.name
                    artist_obj = artist_details.get('artist', {})
                    artist_name = artist_obj.get('name', '')

                    if artist_name:
                        show['artist_name'] = artist_name
                        print(f"      ✓ Artist: {artist_name}")
                    else:
                        print(f"      ! No name found in artist details")
                else:
                    print(f"      ! Could not fetch artist details")
            else:
                print(f"    - No artist IDs available")

        # Save to JSON
        output_file = args.output or 'tracks.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(eligible_shows, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}")
        print(f"Saved {len(eligible_shows)} eligible shows to {output_file}")
        print(f"{'='*80}\n")

        # Print summary
        shows_1hr = [s for s in eligible_shows if s.get('scheduled_duration') == 60]
        shows_2hr = [s for s in eligible_shows if s.get('scheduled_duration') == 120]
        print(f"Summary: {len(shows_1hr)} x 1hr shows, {len(shows_2hr)} x 2hr shows\n")

        return

    # If output-schedule mode, generate schedule.json
    if args.output_schedule:
        print("\n" + "="*80)
        print("MODE: Generating schedule.json")
        print("="*80 + "\n")

        # Get the week start (Monday)
        week_start = scheduler.get_week_start(target_date)
        end_date = week_start + timedelta(days=args.days)

        print(f"Fetching schedule for week of {week_start.date()} to {end_date.date()}...")

        # Fetch current schedule
        current_schedule = scheduler.fetch_schedule(week_start, end_date)

        # Convert to JSON-friendly format
        schedule_json = []
        for show in current_schedule:
            schedule_json.append({
                'id': show.get('id'),
                'title': show.get('title'),
                'start': show.get('start'),
                'end': show.get('end'),
                'duration': show.get('duration'),
                'media_type': show.get('media', {}).get('type'),
                'track_id': show.get('media', {}).get('trackId'),
                'description': show.get('description'),
                'artist_ids': show.get('artistIds', []),
                'artists': show.get('artists', []),
                'color': show.get('color')
            })

        # Save to JSON
        output_file = args.output or 'schedule.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(schedule_json, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}")
        print(f"Saved {len(schedule_json)} shows to {output_file}")
        print(f"{'='*80}\n")

        return

    # If test-slots mode, just find and output empty slots
    if args.test_slots:
        print("\n" + "="*80)
        print("TEST MODE: Finding empty slots only")
        print("="*80 + "\n")

        empty_slots = scheduler.find_empty_slots(target_date, args.days)

        # Convert datetime objects to strings for JSON serialization
        slots_json = []
        for slot in empty_slots:
            slots_json.append({
                'start': slot['start'].strftime('%Y-%m-%d %H:%M'),
                'end': slot['end'].strftime('%Y-%m-%d %H:%M'),
                'duration_minutes': slot['duration_minutes'],
                'scheduled_duration': slot.get('scheduled_duration'),
                'day_of_week': slot['start'].strftime('%A'),
                'date': slot['start'].strftime('%Y-%m-%d')
            })

        # Save to JSON
        output_file = args.output or 'empty-slots.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(slots_json, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}")
        print(f"Saved {len(slots_json)} empty slots to {output_file}")
        print(f"{'='*80}\n")

        # Print summary
        slots_1hr = [s for s in slots_json if s['scheduled_duration'] == 60]
        slots_2hr = [s for s in slots_json if s['scheduled_duration'] == 120]
        print(f"Summary: {len(slots_1hr)} x 1hr slots, {len(slots_2hr)} x 2hr slots\n")

        return

    # If plan mode, generate updated-slots.json
    if args.plan:
        print("\n" + "="*80)
        print("MODE: Generating updated-slots.json (show-to-slot mapping)")
        print("="*80 + "\n")

        # Load tracks.json
        tracks_file = 'tracks.json'
        if not os.path.exists(tracks_file):
            print(f"Error: {tracks_file} not found. Run with --output-tracks first.")
            sys.exit(1)

        with open(tracks_file, 'r', encoding='utf-8') as f:
            eligible_shows = json.load(f)

        print(f"Loaded {len(eligible_shows)} shows from {tracks_file}")

        # Load empty-slots.json
        slots_file = 'empty-slots.json'
        if not os.path.exists(slots_file):
            print(f"Error: {slots_file} not found. Run with --test-slots first.")
            sys.exit(1)

        with open(slots_file, 'r', encoding='utf-8') as f:
            empty_slots_data = json.load(f)

        print(f"Loaded {len(empty_slots_data)} empty slots from {slots_file}")

        # Separate shows by duration
        shows_1hr = [s for s in eligible_shows if s.get('scheduled_duration') == 60]
        shows_2hr = [s for s in eligible_shows if s.get('scheduled_duration') == 120]

        print(f"\nShows available: {len(shows_1hr)} x 1hr, {len(shows_2hr)} x 2hr")

        # Separate slots by duration
        slots_1hr = [s for s in empty_slots_data if s.get('scheduled_duration') == 60]
        slots_2hr = [s for s in empty_slots_data if s.get('scheduled_duration') == 120]

        print(f"Slots available: {len(slots_1hr)} x 1hr, {len(slots_2hr)} x 2hr")

        # Match shows to slots
        updated_slots = []
        shows_1hr_index = 0
        shows_2hr_index = 0

        for slot in empty_slots_data:
            slot_duration = slot.get('scheduled_duration')
            show = None

            if slot_duration == 60 and shows_1hr_index < len(shows_1hr):
                show = shows_1hr[shows_1hr_index]
                shows_1hr_index += 1
            elif slot_duration == 120 and shows_2hr_index < len(shows_2hr):
                show = shows_2hr[shows_2hr_index]
                shows_2hr_index += 1

            if show:
                updated_slots.append({
                    'slot': slot,
                    'show': show
                })

        # Save to JSON
        output_file = args.output or 'updated-slots.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(updated_slots, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}")
        print(f"Saved {len(updated_slots)} show-to-slot mappings to {output_file}")
        print(f"  - {shows_1hr_index} x 1hr shows mapped")
        print(f"  - {shows_2hr_index} x 2hr shows mapped")
        print(f"{'='*80}\n")

        return

    # If execute mode, load updated-slots.json and create shows
    if args.execute:
        print("\n" + "="*80)
        print("MODE: Executing plan from updated-slots.json")
        print("="*80 + "\n")

        # Load updated-slots.json
        plan_file = 'updated-slots.json'
        if not os.path.exists(plan_file):
            print(f"Error: {plan_file} not found. Run with --plan first.")
            sys.exit(1)

        with open(plan_file, 'r', encoding='utf-8') as f:
            mappings = json.load(f)

        print(f"Loaded {len(mappings)} show-to-slot mappings from {plan_file}\n")

        if not mappings:
            print("No mappings to execute")
            return

        # Launch Playwright and create shows
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Login
                print("Logging in...")
                page.goto(f"{WEB_BASE_URL}/login")
                page.wait_for_selector('input[type="email"]', timeout=10000)
                page.fill('input[type="email"]', login_username)
                page.fill('input[type="password"]', login_password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(2000)
                print("✓ Logged in\n")

                # Process each mapping
                success_count = 0
                error_count = 0

                for i, mapping in enumerate(mappings, 1):
                    try:
                        print(f"[{i}/{len(mappings)}] Processing mapping...")
                        scheduler.create_show_from_mapping(page, mapping)
                        success_count += 1
                    except Exception as e:
                        error_count += 1
                        print(f"  ✗ Error creating show: {e}")
                        import traceback
                        traceback.print_exc()
                        # Continue with next mapping

                print(f"\n{'='*80}")
                print(f"COMPLETED: {success_count} shows created successfully, {error_count} errors")
                print(f"{'='*80}\n")

                # Brief wait before closing
                page.wait_for_timeout(1000)

            except Exception as e:
                print(f"Error during execution: {e}")
                import traceback
                traceback.print_exc()
            finally:
                browser.close()

        return

    print("\nError: No mode specified. Use --output-tracks, --output-schedule, --test-slots, --plan, or --execute")
    sys.exit(1)


if __name__ == '__main__':
    main()
