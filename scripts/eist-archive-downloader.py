#!/usr/bin/env python3
"""
Bulk-download media files from the Radiocult media library as a zip archive.

Logs in, configures columns to show "Uploaded on", sorts by that column
in descending order, selects all files, and triggers "download as zip".
"""

import argparse
import os
import shutil
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

login_username = os.getenv("RADIOCULT_USER")
login_password = os.getenv("RADIOCULT_PW")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download all media from Radiocult media library as a zip."
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Directory to save the downloaded zip (default: current directory)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Show the browser window (default: headless)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Download timeout in seconds (default: 600)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not login_username or not login_password:
        print(
            "Error: RADIOCULT_USER and RADIOCULT_PW must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.interactive)
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            # --- Login ---
            print("Navigating to Radiocult...")
            page.goto("https://app.radiocult.fm/")
            page.wait_for_load_state("networkidle", timeout=15_000)

            print("Logging in...")
            page.wait_for_selector(
                'input[type="email"], input[name="email"]', timeout=10_000
            )
            page.fill('input[type="email"], input[name="email"]', login_username)
            page.fill(
                'input[type="password"], input[name="password"]', login_password
            )
            page.click('button[type="submit"]')

            # Wait for login form to disappear
            page.wait_for_selector(
                'input[type="email"], input[name="email"]',
                state="hidden",
                timeout=30_000,
            )

            print(f"Login successful! Redirected to {page.url}")

            # --- Navigate to media page via nav bar ---
            print("Navigating to media library...")
            page.get_by_role("link", name="Media").click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            print(f"Media page URL: {page.url}")

            # --- Configure columns to show "Uploaded on" ---
            print('Configuring columns to show "Uploaded on"...')
            # The configure columns button is an icon-only dropdown trigger
            page.locator('[id^="ds--dropdown"]').first.click()
            page.wait_for_timeout(1_000)
            page.get_by_label("Uploaded on").click()
            page.wait_for_timeout(500)
            # Close the configure panel
            page.keyboard.press("Escape")
            page.wait_for_timeout(1_000)

            # --- Sort by "Uploaded on" descending ---
            print('Sorting by "Uploaded on"...')
            uploaded_on_header = page.locator('th').filter(has_text="Uploaded on")
            uploaded_on_header.click()
            page.wait_for_timeout(1_000)
            # Click again to reverse sort (descending — most recent first)
            uploaded_on_header.click()
            page.wait_for_timeout(1_000)

            # --- Select all files ---
            print("Selecting all files...")
            select_all_checkbox = page.locator(
                'thead input[type="checkbox"], '
                'th input[type="checkbox"], '
                '[aria-label="Select all"]'
            ).first
            select_all_checkbox.click()
            page.wait_for_timeout(1_000)

            # --- Download as zip ---
            print("Triggering download as zip...")
            download_button = page.get_by_text("Download as zip")
            # If the button is inside a menu, try to open it first
            if not download_button.is_visible():
                # Look for a bulk-actions menu or similar trigger
                for label in ("Actions", "Bulk actions", "More"):
                    trigger = page.get_by_text(label)
                    if trigger.is_visible():
                        trigger.click()
                        page.wait_for_timeout(500)
                        break

            with page.expect_download(timeout=args.timeout * 1_000) as download_info:
                download_button.click()

            download = download_info.value
            dest = os.path.join(output_dir, download.suggested_filename or "media.zip")
            download.save_as(dest)
            print(f"Download complete: {dest}")

        except Exception as exc:
            screenshot_path = os.path.join(output_dir, "error-screenshot.png")
            try:
                page.screenshot(path=screenshot_path)
                print(f"Screenshot saved to {screenshot_path}", file=sys.stderr)
            except Exception:
                pass
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
