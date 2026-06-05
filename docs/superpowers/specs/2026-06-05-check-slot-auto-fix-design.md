# Check-Slot Auto-Fix: Design Spec

**Date:** 2026-06-05
**Status:** Draft

## Summary

Add a `--check-slot` mode to `add-eist-aris-shows.py` and a companion GitHub Actions workflow that runs every hour (at :45 past) to detect and auto-fix empty or broken schedule slots on eist radio. When the next hour's slot is empty or has a fileless pre-record, the script picks a random eligible show from the last month and creates a replacement show with the "(eist aris)" suffix.

**Note on accented characters:** Throughout this spec, "eist aris" refers to "eist aris" with fadas on the e and second i. The actual title suffix appended is `(éist arís)` matching the existing pattern in the codebase.

## Requirements

1. **New `--check-slot` mode** in `add-eist-aris-shows.py` that:
   - Takes a datetime input (same format as existing: `YYYY-MM-DD HH:MM:SS`)
   - Rounds up to the next full hour boundary to determine the target slot
   - Checks the RadioCult schedule for that 1-hour slot
   - Detects two problem conditions:
     a. The slot is empty (no show scheduled)
     b. The slot has a pre-record show (`media.type === "mix"`) without a file (`trackId` is missing/null)
   - If a problem is detected, selects a random eligible 1hr show from the last 4 weeks
   - If replacing a fileless pre-record, deletes the existing show first via Playwright
   - Creates the replacement show via Playwright with "(eist aris)" title suffix
   - Supports `--dry-run` and `--headless` flags

2. **New GitHub Actions workflow** `.github/workflows/check-slot.yml` that:
   - Runs on cron schedule: `45 8-23 * * *` (at :45 past every hour, 08:45-23:45 UTC)
   - Also supports manual triggering with optional datetime override
   - Calculates the next hour boundary from current UTC time
   - Calls the script with `--check-slot --headless`
   - Uploads error screenshots on failure
   - Displays a summary in the GitHub Actions step summary

## Architecture

### Approach

Extend the existing `add-eist-aris-shows.py` script with a new `--check-slot` mode. This reuses:
- `EistArisScheduler` class (API helpers, Playwright auth, show creation)
- `build_replay_list()` for fetching eligible replacement shows
- `is_eligible_show()` for filtering
- `create_show_from_mapping()` for Playwright-based show creation
- `parse_target_date()` for input parsing

One new method is needed: `delete_show_by_id()` for removing fileless pre-records via Playwright before creating the replacement.

### Slot Boundary Logic

The input datetime is rounded UP to the next full-hour boundary. If the input is already on an exact hour, it is used as-is (no rounding needed):
- `14:45:00` -> checks slot `15:00-16:00`
- `15:00:00` -> checks slot `15:00-16:00` (already on the hour)
- `23:45:00` -> checks slot `00:00-01:00` (next day, but outside broadcast window so would be skipped)

Only 1hr slots are created. This keeps the logic simple and predictable. If back-to-back hours are both empty, each gets its own 1hr show on successive CI runs.

### Decision Flow

```
Input: --check-slot "2026-06-05 14:45"
                    |
                    v
    Round up to next hour: 15:00 UTC
                    |
                    v
    Fetch schedule for 14:50-16:10 window (buffer)
    Filter to shows overlapping 15:00-16:00
                    |
          +---------+----------+
          v         v          v
      No show    Live show   Pre-record (mix)
      in slot    or playlist   scheduled
          |      scheduled      |
          |         |        Has trackId?
          |      Exit OK     +----+-----+
          |                 Yes        No
          |              Exit OK        |
          |                             v
          |                    Delete show via
          |                    Playwright
          |                             |
          v                             v
    Fetch eligible shows from last 4 weeks
    (1hr duration, has trackId, not eist aris)
                    |
                    v
    Pick random show from eligible list
                    |
                    v
    Create replacement show via Playwright:
    - Title: "{original_title} (eist aris)"
    - Time: 15:00-16:00
    - Pre-record mode with original track
                    |
                    v
                 Done
```

### Show Types and Handling

| `media.type` | `trackId` present | Action |
|---|---|---|
| `live` | n/a | No action (live broadcast) |
| `playlist` | n/a | No action (playlist-driven) |
| `mix` | yes | No action (pre-record with file) |
| `mix` | no/null | **Replace**: delete show, create eist aris |
| (no show) | n/a | **Fill**: create eist aris |

### Replacement Show Selection

1. Call `build_replay_list(target_date, weeks_back=4)` to get eligible shows from the last month
2. Filter to shows with `scheduled_duration == 60` (1hr only)
3. Exclude shows whose `track_id` appears in the current week's schedule (prevent duplicates)
4. Pick one at random
5. If no eligible shows found, log a warning and exit without error

### Show Deletion via Playwright

New method `delete_show_via_playwright(page, show_id, show_title, slot_start)`:

1. Navigate to the schedule page for the show's week
2. Locate and click on the show in the calendar view (by title or time)
3. Click the delete/trash button in the show detail/edit panel
4. Confirm the deletion dialog
5. Wait for the UI to update

This is the riskiest new functionality since deletion hasn't been automated before. The selectors will need to be discovered through UI inspection, similar to how the create flow was built.

### New Mode Handler

New function `mode_check_slot(scheduler, args, target_date, login_username, login_password)`:

1. Calculate target slot: round `target_date` up to next hour, set end = start + 1hr
2. Fetch schedule for the target slot window
3. Filter shows overlapping the slot
4. Analyze the slot state (empty, live, pre-record with/without file)
5. If no action needed, print status and exit
6. If action needed:
   a. Authenticate with Playwright
   b. Fetch eligible replacement shows (4 weeks back, 1hr duration)
   c. Filter out shows already in this week's schedule
   d. Pick random replacement
   e. If replacing fileless pre-record: delete existing show
   f. Create replacement show using existing `create_show_from_mapping()` pattern
7. Print summary

### CLI Changes

New arguments added to the argument parser:
- `--check-slot` (action flag): Enable check-slot mode
- No new required arguments; uses the existing `date` positional argument

The `date` argument now accepts full timestamps for this mode (e.g., `"2026-06-05 14:45:00"`), which is already supported by `parse_target_date()`.

## GitHub Actions Workflow

### File: `.github/workflows/check-slot.yml`

**Name:** Check and fix schedule slots

**Triggers:**
- `schedule`: cron `45 8-23 * * *` (16 runs/day, at :45 past each hour from 08:45-23:45 UTC)
- `workflow_dispatch`: manual trigger with optional `target_datetime` input

**Timezone note:** Uses UTC. Covers 9am-midnight Irish time in winter (GMT=UTC). In summer (IST=UTC+1), the window shifts to 9:45am-12:45am Irish time, which is acceptable.

**Steps:**
1. Checkout repository
2. Set up Python 3.11 with pip cache
3. Install Python dependencies (requests, python-dotenv, playwright)
4. Install Playwright Chromium browser + system deps
5. Calculate target datetime:
   - If manual with `target_datetime` input: use that value
   - Else: use current UTC time (the script handles rounding to next hour)
6. Run: `python scripts/add-eist-aris-shows.py "$TARGET_DATETIME" --check-slot --headless`
7. Upload error screenshots on failure
8. Display summary in step summary

**Secrets required:** (same as existing workflow)
- `API_KEY`
- `RADIOCULT_USER`
- `RADIOCULT_PW`

**Cost consideration:** 16 runs/day * 7 days = 112 runs/week. Each run takes ~2-5 min if no action needed (just API check), or ~3-8 min if Playwright creates a show. Well within GitHub Actions free tier limits.

## Edge Cases

1. **No eligible replacement shows:** Log warning, exit 0. The CI reports "no eligible shows found" in the summary. This is not an error.

2. **Multiple shows in the target slot:** If more than one show overlaps the target hour, use the one that starts closest to the hour boundary. If one is a fileless pre-record, handle it; ignore the others.

3. **Show spans multiple hours:** A show starting at 14:00 and ending at 16:00 overlaps the 15:00-16:00 slot. Treat this as "slot is filled" — no action needed.

4. **Midnight boundary:** The CI runs at 23:45. The next hour is 00:00 (next day). This is outside broadcast hours (9am-midnight), so the script should check and skip if the target slot is before 09:00 or after 23:00 UTC.

5. **Playwright failure during deletion:** If deletion fails, do NOT proceed to create. Log the error, take a screenshot, exit with error code.

6. **Race condition:** Two CI runs could theoretically overlap if one runs long. GitHub Actions doesn't run duplicate scheduled workflows concurrently by default, and the `concurrency` key can be used to prevent this.

## Files Changed

| File | Change |
|---|---|
| `scripts/add-eist-aris-shows.py` | Add `--check-slot` flag, `mode_check_slot()` function, `delete_show_via_playwright()` method |
| `.github/workflows/check-slot.yml` | New workflow file |
| `README.md` | Add documentation for `--check-slot` mode and the new CI workflow |
| `flow.md` | Update with check-slot flow (if applicable) |

## Testing

- **Dry-run test:** `python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --dry-run`
  - Should show what would happen without creating/deleting anything
- **Manual CI trigger:** Use `workflow_dispatch` with a known problematic slot to verify end-to-end
- **API-only test:** With `--dry-run`, verify the slot detection logic works correctly against live schedule data

## Concurrency Guard

The CI workflow should use:
```yaml
concurrency:
  group: check-slot
  cancel-in-progress: false
```
This prevents multiple runs from overlapping but doesn't cancel in-progress runs.
