#!/usr/bin/env python3
"""
Test script to create a single show at 1pm Tuesday using Playwright automation.

Based on UI exploration, the workflow is:
1. Click on the time slot (Tuesday 13:00)
2. Fill in the title field
3. Check the "Pre-record" checkbox
4. Open media selection and search for track by filename
5. Select the track
6. Click "Create event"
"""

import os
import sys
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

login_username = os.getenv('RADIOCULT_USER')
login_password = os.getenv('RADIOCULT_PW')

if not login_username or not login_password:
    print("Error: RADIOCULT_USER and RADIOCULT_PW must be set in .env")
    sys.exit(1)

# Test data for the show
TEST_SHOW_TITLE = "Test Show (éist arís)"
TEST_SHOW_DESCRIPTION = "This is an éist arís replay show."
TEST_TRACK_TITLE = "Out of space"  # Update this with a real track title
TEST_ARTIST_NAME = "Damien"  # Artist name to search for
TEST_TIME = "13:00"  # 1pm in 24-hour format

print(f"Creating test show: {TEST_SHOW_TITLE}")
print(f"Time: Tuesday at {TEST_TIME}")
print(f"Track: {TEST_TRACK_TITLE}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=1000)  # Visible and slow for debugging
    context = browser.new_context()
    page = context.new_page()

    try:
        # Step 1: Login
        print("\n[Step 1] Logging in...")
        page.goto("https://app.radiocult.fm/login")
        page.wait_for_selector('input[type="email"]', timeout=10000)
        page.fill('input[type="email"]', login_username)
        page.fill('input[type="password"]', login_password)
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)
        print("  ✓ Logged in")

        # Step 2: Navigate to schedule for week of 2025-12-08
        print("\n[Step 2] Navigating to schedule...")
        page.goto("https://app.radiocult.fm/schedule?w=2025-12-08")
        page.wait_for_load_state('networkidle', timeout=15000)
        print("  ✓ On schedule page")

        # Step 3: Click the "Create" button to open create modal
        print(f"\n[Step 3] Opening create show modal...")

        # Find and click the Create button with the plus icon (more specific selector)
        # This targets the button with the SVG plus icon to avoid clicking on calendar cells
        create_btn = page.locator('button:has(svg[viewBox="0 0 256 256"]):has-text("Create")')
        create_btn.click()
        page.wait_for_timeout(2000)

        # Check if modal opened
        if page.locator('input[name="title"]').count() > 0:
            print("  ✓ Create show modal opened")
        else:
            print("  ❌ Modal did not open")
            raise Exception("Could not open create modal")

        # Step 4: Set the start time to 1pm (13:00 in 24-hour format)
        print(f"\n[Step 4] Setting start time to 13:00...")
        start_time_input = page.locator('input[aria-labelledby*="startTime"]')

        # Click to focus, clear, and type 24-hour format
        start_time_input.click()
        page.wait_for_timeout(300)
        page.keyboard.press('Control+A')
        page.wait_for_timeout(200)
        # Type 24-hour format: "13:00"
        page.keyboard.type('13:00', delay=100)
        page.wait_for_timeout(1000)

        # Press Enter to select the first matching entry
        page.keyboard.press('Enter')
        page.wait_for_timeout(500)
        print("  ✓ Start time set to 13:00")

        # Step 4b: Set the end time to 2pm (14:00 in 24-hour format)
        # Note: If you add date selection, ensure end date matches start date (shows never span days)
        print(f"\n[Step 4b] Setting end time to 14:00...")
        end_time_input = page.locator('input[aria-labelledby*="endTime"]')

        # Click to focus, clear, and type 24-hour format
        end_time_input.click()
        page.wait_for_timeout(300)
        page.keyboard.press('Control+A')
        page.wait_for_timeout(200)
        # Type 24-hour format: "14:00"
        page.keyboard.type('14:00', delay=100)
        page.wait_for_timeout(1000)

        # Press Enter to select the first matching entry
        page.keyboard.press('Enter')
        page.wait_for_timeout(500)
        print("  ✓ End time set to 14:00")

        # Step 5: Fill in the title
        print(f"\n[Step 5] Filling in title: {TEST_SHOW_TITLE}")
        title_input = page.locator('input[name="title"]')
        title_input.fill(TEST_SHOW_TITLE)
        print("  ✓ Title filled")

        # Step 6: Fill in description field
        print(f"\n[Step 6] Setting description...")
        description_field = page.locator('p[data-placeholder*="Enter event description"]')
        description_field.click()
        page.wait_for_timeout(300)
        # Type description
        page.keyboard.type(TEST_SHOW_DESCRIPTION, delay=50)
        page.wait_for_timeout(500)
        print("  ✓ Description filled")

        # Step 6b: Add artist
        print(f"\n[Step 6b] Adding artist: {TEST_ARTIST_NAME}...")
        artist_input = page.locator('input#artist-select')
        artist_input.click()
        page.wait_for_timeout(300)
        # Type artist name to search
        page.keyboard.type(TEST_ARTIST_NAME, delay=100)
        page.wait_for_timeout(1000)  # Wait for search results to filter
        # Press Enter to select first result
        page.keyboard.press('Enter')
        page.wait_for_timeout(500)
        print(f"  ✓ Artist '{TEST_ARTIST_NAME}' added")

        # Step 7: Click the "Mix Pre-record" button
        print("\n[Step 7] Enabling pre-record mode...")
        # There are multiple checkboxes with ID "live-media-checkbox"
        # We need to click specifically on the "Mix Pre-record" option
        prerecord_button = page.get_by_role("button", name="Mix Pre-record")
        prerecord_button.click()
        page.wait_for_timeout(1000)  # Wait for UI to update
        print("  ✓ Pre-record enabled")

        # Step 8: Open media selection modal
        print("\n[Step 8] Opening media selection...")
        # Look for the button/area to click to open media selection
        # This might be a button with "Select media" text or in the pre-record section
        try:
            # Try clicking on the pre-record section to open media selection
            media_button = page.locator('text=Select media').first
            media_button.click()
            page.wait_for_timeout(2000)
            print("  ✓ Media selection modal opened")
        except Exception as e:
            print(f"  ! Could not find 'Select media' button: {e}")
            print("  Trying alternative approach...")
            # Alternative: look for any button/clickable in the pre-record area
            # You may need to adjust this based on actual UI structure
            pass

        # Step 9: Search for track by title
        print(f"\n[Step 9] Searching for track: {TEST_TRACK_TITLE}")
        # Find the search input in the media selection modal
        search_input = page.locator('input[data-ds--text-field--input="true"]').last
        search_input.fill(TEST_TRACK_TITLE)
        page.wait_for_timeout(2000)  # Wait for search results
        print("  ✓ Search completed")

        # Step 10: Select the first result
        print("\n[Step 10] Selecting track from results...")
        # The track is displayed in a table row - click on the row to select it
        try:
            # Find the table row that contains the track title
            track_row = page.locator(f'tr:has-text("{TEST_TRACK_TITLE}")').first
            track_row.click()
            page.wait_for_timeout(1000)
            print("  ✓ Track selected")
        except Exception as e:
            print(f"  ! Could not select track automatically: {e}")
            print("  Please manually click on the track row in the table...")
            input("  Press Enter after selecting the track...")

        # Step 11: Click Create event button
        print("\n[Step 11] Clicking Create event button...")

        # Find the Create event button
        create_event_button = page.locator('button[type="submit"]:has-text("Create event")')

        # Click the Create event button
        print("  Clicking 'Create event' button...")
        create_event_button.click()
        page.wait_for_timeout(3000)
        print("  ✓ Create event button clicked!")

        # Verify the show appears on the schedule
        print("\n[Step 12] Verifying show was created...")
        page.wait_for_timeout(2000)
        # Look for the show title on the schedule
        if page.locator(f'text={TEST_SHOW_TITLE}').count() > 0:
            print("  ✓ Show appears on schedule!")
        else:
            print("  ? Could not verify show - check schedule manually")

        print("\n" + "="*80)
        print("SUCCESS! Test show created.")
        print("="*80)
        print("\nClosing browser in 5 seconds...")
        page.wait_for_timeout(5000)

    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()
        print("\nPress Enter to close browser...")
        input()
    finally:
        browser.close()

print("\nTest complete!")
