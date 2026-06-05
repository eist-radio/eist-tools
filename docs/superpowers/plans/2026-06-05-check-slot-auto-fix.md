# Check-Slot Auto-Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--check-slot` mode to `add-eist-aris-shows.py` that detects empty or fileless pre-record slots and auto-fills them with a random replay show, plus a GitHub Actions CI workflow to run this hourly.

**Architecture:** Extends the existing `EistArisScheduler` class with a deletion method and a new `mode_check_slot()` handler. The handler rounds the input time to the next hour, fetches the schedule for that slot, and either exits (slot is fine), fills an empty slot, or deletes a broken pre-record and creates a replacement. A new GitHub Actions workflow runs at :45 past each hour from 08:45-23:45 UTC.

**Tech Stack:** Python 3.11, requests, playwright, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-06-05-check-slot-auto-fix-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scripts/add-eist-aris-shows.py` | Modify | Add `round_up_to_hour()`, `delete_show_via_playwright()`, `mode_check_slot()`, new CLI flag, routing in `main()` |
| `.github/workflows/check-slot.yml` | Create | Hourly CI workflow |
| `README.md` | Modify | Document `--check-slot` mode and new CI workflow |

---

### Task 1: Add `round_up_to_hour()` utility function

**Files:**
- Modify: `scripts/add-eist-aris-shows.py:39-48` (after `parse_target_date()`)

- [ ] **Step 1: Add `round_up_to_hour()` after `parse_target_date()`**

Insert this function after the `parse_target_date()` function (after line 48):

```python
def round_up_to_hour(dt: datetime) -> datetime:
    """Round a datetime up to the next full hour. If already on the hour, return as-is."""
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
```

- [ ] **Step 2: Verify with a quick manual test**

Run:
```bash
python3 -c "
from datetime import datetime, timedelta
# Inline the function to test
def round_up_to_hour(dt):
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

print(round_up_to_hour(datetime(2026, 6, 5, 14, 45, 0)))  # expect 2026-06-05 15:00:00
print(round_up_to_hour(datetime(2026, 6, 5, 15, 0, 0)))   # expect 2026-06-05 15:00:00
print(round_up_to_hour(datetime(2026, 6, 5, 23, 45, 0)))  # expect 2026-06-06 00:00:00
print(round_up_to_hour(datetime(2026, 6, 5, 9, 0, 0)))    # expect 2026-06-05 09:00:00
"
```

Expected output:
```
2026-06-05 15:00:00
2026-06-05 15:00:00
2026-06-06 00:00:00
2026-06-05 09:00:00
```

- [ ] **Step 3: Commit**

```bash
git add scripts/add-eist-aris-shows.py
git commit -m "Add round_up_to_hour() utility for check-slot mode"
```

---

### Task 2: Add `delete_show_via_playwright()` method to `EistArisScheduler`

**Files:**
- Modify: `scripts/add-eist-aris-shows.py` (add method to `EistArisScheduler` class, after `create_show_from_mapping()` which ends around line 771)

- [ ] **Step 1: Add the delete method to the class**

Insert this method at the end of the `EistArisScheduler` class (after `create_show_from_mapping()`):

```python
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
```

**Note:** The Playwright selectors for deletion are best-effort based on common UI patterns. They may need adjustment after testing against the live RadioCult UI. The existing codebase has a precedent for this — see commit `a8e1dab` ("Remove brittle Playwright-based deletion from cleanup mode") and `796bd2e` ("Fix confirmation dialog: button is 'Delete media' not 'Confirm'") in the archive manager. The selectors above are a starting point; Task 6 covers live testing.

- [ ] **Step 2: Commit**

```bash
git add scripts/add-eist-aris-shows.py
git commit -m "Add delete_show_via_playwright() method"
```

---

### Task 3: Add `mode_check_slot()` handler function

**Files:**
- Modify: `scripts/add-eist-aris-shows.py` (add new function after `mode_execute()`, before `main()`)

This is the core logic. It goes after `mode_execute()` (which ends around line 1176) and before the `main()` function.

- [ ] **Step 1: Add the mode handler function**

Insert this function before `main()`:

```python
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

        if action == "replace":
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
            page.wait_for_timeout(2_000)
            print("✓ Logged in\n")

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
```

- [ ] **Step 2: Verify the file is syntactically valid**

Run:
```bash
python3 -c "import ast; ast.parse(open('scripts/add-eist-aris-shows.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/add-eist-aris-shows.py
git commit -m "Add mode_check_slot() handler for auto-fixing empty and fileless slots"
```

---

### Task 4: Add `--check-slot` CLI flag and wire up routing in `main()`

**Files:**
- Modify: `scripts/add-eist-aris-shows.py` — the `main()` function (starts around line 1184)

- [ ] **Step 1: Add `--check-slot` argument to the parser**

In the `main()` function, add this argument after the existing `--execute` argument (around line 1240):

```python
    parser.add_argument(
        "--check-slot",
        action="store_true",
        help="Check if the next hour's slot is empty or has a fileless pre-record, and auto-fix",
    )
```

- [ ] **Step 2: Add routing for `--check-slot` mode**

In the `main()` function, add this block after the existing `if args.execute:` block and before the final error message. Place it right before the `print("\nError: No mode specified...")` block:

```python
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
```

- [ ] **Step 3: Update the error message to include `--check-slot`**

Change the final error message in `main()` from:

```python
    print(
        "\nError: No mode specified. Use one of "
        "--output-tracks, --output-schedule, --test-slots, --plan, or --execute",
        file=sys.stderr,
    )
```

To:

```python
    print(
        "\nError: No mode specified. Use one of "
        "--output-tracks, --output-schedule, --test-slots, --plan, --execute, or --check-slot",
        file=sys.stderr,
    )
```

- [ ] **Step 4: Verify syntax and dry-run works**

Run:
```bash
python3 -c "import ast; ast.parse(open('scripts/add-eist-aris-shows.py').read()); print('Syntax OK')"
```

Then test dry-run against live schedule:
```bash
cd /home/aireilly/fun/eist-tools
python3 scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --dry-run
```

Expected: The script should print the target slot, fetch the schedule, analyze the slot, and either say "no action needed" or show what it would do. No Playwright browser should launch in dry-run mode.

- [ ] **Step 5: Commit**

```bash
git add scripts/add-eist-aris-shows.py
git commit -m "Add --check-slot CLI flag and routing in main()"
```

---

### Task 5: Create GitHub Actions workflow

**Files:**
- Create: `.github/workflows/check-slot.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Check and fix schedule slots

on:
  schedule:
    # Run at :45 past every hour from 08:45-23:45 UTC
    - cron: '45 8-23 * * *'

  workflow_dispatch:
    inputs:
      target_datetime:
        description: 'Target datetime (YYYY-MM-DD HH:MM:SS UTC) - defaults to current time'
        required: false
        type: string
      dry_run:
        description: 'Dry run mode (only print, do not create/delete shows)'
        required: false
        type: boolean
        default: false

concurrency:
  group: check-slot
  cancel-in-progress: false

jobs:
  check-slot:
    runs-on: ubuntu-latest

    env:
      API_KEY: ${{ secrets.API_KEY }}
      RADIOCULT_USER: ${{ secrets.RADIOCULT_USER }}
      RADIOCULT_PW: ${{ secrets.RADIOCULT_PW }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install Python dependencies
        run: |
          pip install --upgrade pip
          pip install requests python-dotenv playwright

      - name: Install Playwright browsers
        run: |
          playwright install chromium
          playwright install-deps chromium

      - name: Calculate target datetime
        id: datetime
        run: |
          if [ -n "${{ inputs.target_datetime }}" ]; then
            TARGET_DATETIME="${{ inputs.target_datetime }}"
          else
            TARGET_DATETIME=$(date -u +"%Y-%m-%d %H:%M:%S")
          fi
          echo "target_datetime=$TARGET_DATETIME" >> $GITHUB_OUTPUT
          echo "Target datetime: $TARGET_DATETIME"

      - name: Check and fix slot
        run: |
          if [ "${{ inputs.dry_run }}" == "true" ]; then
            echo "Running in DRY RUN mode"
            python scripts/add-eist-aris-shows.py "${{ steps.datetime.outputs.target_datetime }}" --check-slot --headless --dry-run
          else
            python scripts/add-eist-aris-shows.py "${{ steps.datetime.outputs.target_datetime }}" --check-slot --headless
          fi

      - name: Upload error screenshots
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: check-slot-errors-${{ github.run_number }}
          path: error_screenshot_*.png
          retention-days: 7

      - name: Display summary
        if: always()
        run: |
          echo "## Check Slot Summary" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Target datetime:** ${{ steps.datetime.outputs.target_datetime }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ inputs.dry_run }}" == "true" ]; then
            echo "**Mode:** DRY RUN" >> $GITHUB_STEP_SUMMARY
          else
            echo "**Mode:** LIVE" >> $GITHUB_STEP_SUMMARY
          fi
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ job.status }}" == "success" ]; then
            echo "**Status:** Completed successfully" >> $GITHUB_STEP_SUMMARY
          else
            echo "**Status:** Failed - check logs and error screenshots" >> $GITHUB_STEP_SUMMARY
          fi
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/check-slot.yml
git commit -m "Add hourly check-slot CI workflow"
```

---

### Task 6: Test dry-run against live schedule data

**Files:**
- None (verification only)

- [ ] **Step 1: Test with current time (should find a show and report no action)**

```bash
cd /home/aireilly/fun/eist-tools
python3 scripts/add-eist-aris-shows.py "$(date -u +'%Y-%m-%d %H:%M:%S')" --check-slot --dry-run
```

Verify: The script prints the target slot, fetches the schedule, and reports whether action is needed.

- [ ] **Step 2: Test with a time known to have a show scheduled**

Look at today's schedule and pick a time that has a show:
```bash
python3 scripts/add-eist-aris-shows.py "2026-06-05 09:45:00" --check-slot --dry-run
```

Expected: Should find the show at 10:00 and report "No action needed" (since it has a trackId).

- [ ] **Step 3: Test outside broadcast hours**

```bash
python3 scripts/add-eist-aris-shows.py "2026-06-05 04:00:00" --check-slot --dry-run
```

Expected: Should print "outside broadcast hours" and exit without fetching schedule.

- [ ] **Step 4: Test with a time where a slot is likely empty (if any exist)**

Check the schedule for gaps:
```bash
python3 scripts/add-eist-aris-shows.py "2026-06-05 11:45:00" --check-slot --dry-run
```

If the 12:00 slot is empty, the script should find eligible shows and print what it would create. If filled, it reports no action needed. Both are valid outcomes.

---

### Task 7: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `--check-slot` to the command-line options section**

In the "**Mode flags (one required):**" list in README.md, add:

```markdown
- `--check-slot` - Check next hour's slot and auto-fix if empty or fileless pre-record
```

- [ ] **Step 2: Add a new section for the check-slot mode**

After the "### Manual Workflow" section and before "### Command-line Options", add:

```markdown
### Hourly Slot Check

Checks if the next hour has an empty slot or a pre-record show without a file attached. If so, picks a random eligible show from the last 4 weeks and creates it as an éist arís replay.

```bash
# Check the next hour from a specific time (dry run)
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --dry-run

# Check and auto-fix (live)
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot

# Headless mode for CI
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --headless
```

The input time is rounded up to the next full hour. Only 1hr shows are used as replacements.
```

- [ ] **Step 3: Update the Automated Workflow section**

Add a new subsection after the existing "### Manual Trigger" section:

```markdown
### Hourly Slot Check (GitHub Actions)

The `.github/workflows/check-slot.yml` workflow runs automatically:

- **Schedule**: Every hour at :45 past (08:45-23:45 UTC)
- **Action**: Checks the next hour's slot and auto-fixes if empty or has a fileless pre-record
- **Mode**: LIVE (creates/deletes shows as needed)

You can trigger it manually from Actions → Check and fix schedule slots with an optional target datetime and dry-run flag.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document --check-slot mode and hourly CI workflow"
```

---

### Task 8: Live Playwright test (manual, interactive)

**Files:**
- None (manual testing)

This task tests the full Playwright flow. Run interactively (not headless) to observe and debug selector issues.

- [ ] **Step 1: Find or create a test scenario**

Either find a genuinely empty slot in the schedule, or temporarily create a test pre-record show without a file using the RadioCult web UI.

- [ ] **Step 2: Run check-slot in interactive mode**

```bash
python3 scripts/add-eist-aris-shows.py "<datetime-of-empty-slot>" --check-slot
```

Watch the browser. Verify:
1. Login succeeds
2. For empty slots: show is created correctly with "(éist arís)" suffix
3. For fileless pre-records: show is deleted, then replacement is created

- [ ] **Step 3: Fix selectors if needed**

If the delete flow fails (most likely point of failure), inspect the RadioCult UI manually and update the selectors in `delete_show_via_playwright()`. Common issues based on git history:
- Delete button might be labeled "Delete event" or have an icon-only button
- Confirmation dialog button text varies ("Delete", "Confirm", "Delete event")
- The show element in the calendar may need a more specific selector

- [ ] **Step 4: Re-test and commit any selector fixes**

```bash
git add scripts/add-eist-aris-shows.py
git commit -m "Fix Playwright selectors for show deletion"
```

---

## Summary

| Task | What | Depends on |
|---|---|---|
| 1 | `round_up_to_hour()` utility | — |
| 2 | `delete_show_via_playwright()` method | — |
| 3 | `mode_check_slot()` handler | 1, 2 |
| 4 | CLI flag + routing | 3 |
| 5 | GitHub Actions workflow | 4 |
| 6 | Dry-run testing | 4 |
| 7 | README docs | 4 |
| 8 | Live Playwright testing | 4 |
