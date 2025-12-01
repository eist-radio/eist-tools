# add-eist-aris-shows.py

## Overview
Automates scheduling "éist arís" (replay) shows on Radiocult radio station by:
1. Finding eligible past shows
2. Detecting empty time slots in the target week
3. Mapping shows to available slots
4. Creating scheduled events via web automation

## Configuration & Setup

### Environment Variables (.env)
- `API_KEY` - Radiocult API authentication token (required)
- `RADIOCULT_USER` - Login email (required for --output-tracks and --execute)
- `RADIOCULT_PW` - Login password (required for --output-tracks and --execute)

### Constants
- `STATION_ID` = "eist-radio"
- `API_BASE_URL` = "https://api.radiocult.fm/api/station"
- `WEB_BASE_URL` = "https://app.radiocult.fm"

### Command-line Arguments
- `date` (required) - Target date in format YYYY-MM-DD or YYYY-MM-DD HH:MM:SS
- `--weeks-back N` - How many weeks to look back for eligible shows (default: 3)
- `--days N` - Number of days to process (default: 7)
- `--output PATH` - Custom output file path
- Mode flags (one required):
  - `--output-tracks` → generates tracks.json
  - `--output-schedule` → generates schedule.json
  - `--test-slots` → generates empty-slots.json
  - `--plan` → generates updated-slots.json
  - `--execute` → creates shows from updated-slots.json
- Optional flags:
  - `--headless` - Run browser in headless mode (for CI environments)
  - `--dry-run` - Print what would be done without making any changes (--execute mode only)

## Core Classes & Methods

### EistArisScheduler Class

#### Initialization
- Accept API key and optional login credentials
- Create requests.Session with Authorization header
- Compile regex pattern for detecting "éist arís" suffix: `\(?\s*[eé](?:ist|sit)\s+ar[ií]s\s*\)?`
- Set authenticated flag to False

#### authenticate_with_playwright()
**Purpose**: Get session cookies by logging into web UI

1. Check if already authenticated, return early if yes
2. Check if credentials are available, print warning if not
3. Launch headless Chromium browser
4. Navigate to login page
5. Wait for email input field (10s timeout)
6. Fill email and password fields
7. Click submit button
8. Wait 3 seconds for login to complete
9. Extract all cookies from browser context
10. Copy cookies into requests.Session
11. Verify authentication by calling track API endpoint
12. If 401 response, print error and return
13. Set authenticated flag to True
14. Close browser

#### fetch_schedule(start_date, end_date)
**Purpose**: Retrieve schedule items for a date range

1. Format start_date as YYYY-MM-DDTH:00:00Z
2. Format end_date as YYYY-MM-DDTH:59:59Z
3. Build URL: `{API_BASE_URL}/{STATION_ID}/schedule`
4. Set params: startDate, endDate
5. Make GET request with session
6. Parse JSON response
7. Return schedules array (or empty list on error)

#### fetch_track_details(track_id)
**Purpose**: Get track metadata (title, artist, duration)

1. Build URL: `{API_BASE_URL}/{STATION_ID}/media/track`
2. Set params: trackId
3. Make GET request with session
4. Return parsed JSON (or None on error)

#### fetch_artist_details(artist_id)
**Purpose**: Get artist metadata (name, bio, etc.)

1. Build URL: `{API_BASE_URL}/{STATION_ID}/artists/{artist_id}`
2. Make GET request with session
3. Return parsed JSON (or None on error)

#### has_eist_aris_suffix(title)
**Purpose**: Check if title contains éist arís suffix

1. Search title with compiled regex pattern
2. Return True if match found, False otherwise

#### is_eligible_show(show)
**Purpose**: Determine if a show qualifies for replay

Check criteria:
1. Has non-empty title
2. Does NOT already have éist arís suffix
3. Has media object
4. Media type is "mix"
5. Has trackId in media
6. Return True only if all criteria met

#### build_replay_list(start_date, weeks_back=3)
**Purpose**: Find eligible shows from past N weeks

1. Calculate date range: (start_date - N weeks) to start_date
2. Print date range being fetched
3. Call fetch_schedule() for that range
4. Print total shows found
5. Initialize empty eligible list
6. For each show in all_shows:
   - Check if eligible using is_eligible_show()
   - Get file duration (default 60 if missing)
   - Check if within 1hr tolerance: abs(duration - 60) <= 1
   - Check if within 2hr tolerance: abs(duration - 120) <= 1
   - Skip if neither tolerance matches
   - Calculate scheduled_duration: 120 if closer to 120, else 60
   - Append to eligible list with fields:
     - title, original_start, original_end
     - duration, scheduled_duration
     - media_type, track_id
     - description, show_id, color
     - artist_ids, artists
7. Print count of eligible shows
8. Return eligible list

#### get_week_start(target_date) [static]
**Purpose**: Get Monday of the week containing target_date

1. Get weekday index (0=Monday, 6=Sunday)
2. Subtract that many days from target_date
3. Reset time to 00:00:00.000
4. Return result

#### find_empty_slots(target_date, days=7)
**Purpose**: Find empty 1hr/2hr slots in target week between 9:00-23:00

**IMPORTANT**: Treats ALL shows as occupied, including existing "(éist arís)" shows.
If a slot has ANY show scheduled, it is NOT considered available for new shows.

**Phase 1: Fetch Schedule**
1. Calculate week_start using get_week_start()
2. Calculate end_date as week_start + days
3. Print date range
4. Fetch current schedule for that range
5. Build occupied_slots list:
   - For EVERY show (no filtering):
     - Parse start/end times as datetime objects
     - Store start, end, title
   - This includes existing "(éist arís)" shows
6. Sort occupied_slots by start time

**Phase 2: Detect Gaps Per Day**
1. For each day in range:
   - Set day_start = 09:00 UTC
   - Set day_end = 23:00 UTC
   - Filter shows for this day
   - Print day header and show count

   **Case A: No shows on this day**
   - Iterate from day_start to day_end
   - Try to create 2hr slots first
   - Create 1hr slot if 2hr doesn't fit
   - Add all slots to empty_gaps list

   **Case B: Shows exist**
   - Check gap BEFORE first show:
     - If first show starts after day_start
     - Calculate gap duration
     - Add to empty_gaps

   - Check gaps BETWEEN consecutive shows:
     - For each pair of adjacent shows
     - If gap exists between end of show[i] and start of show[i+1]
     - Calculate gap duration
     - Add to empty_gaps

   - Check gap AFTER last show:
     - If last show ends before day_end
     - Calculate gap duration
     - Add to empty_gaps

**Phase 3: Split Gaps into 1hr/2hr Slots**
1. Print section header
2. Initialize empty valid_slots list
3. For each gap in empty_gaps:
   - Get gap_start, gap_end, duration
   - Print gap info
   - Initialize current_time = gap_start
   - While current_time < gap_end:
     - Calculate remaining minutes
     - If remaining >= 120:
       - Create 2hr slot (current_time to current_time+2hr)
       - Add to valid_slots
       - Move current_time forward 2hr
     - Else if remaining >= 60:
       - Create 1hr slot (current_time to current_time+1hr)
       - Add to valid_slots
       - Move current_time forward 1hr
     - Else:
       - Print skip message (less than 1hr)
       - Break loop
4. Sort valid_slots by start time
5. Print summary statistics
6. Print first 5 slots
7. Return valid_slots

#### format_time_for_gui(time_str) [static]
**Purpose**: Work around GUI bugs in time input

1. If time_str == "12:00", return "12:00pm"
2. If time_str == "15:00", return "3:00pm"
3. Otherwise return time_str unchanged

#### close_any_open_modals(page) [static]
**Purpose**: Close any open modal dialogs

1. Press Escape key 3 times with 300ms delays between
2. Ignore any exceptions

#### create_show_from_mapping(page, mapping)
**Purpose**: Create a single show via Playwright automation

**Setup**
1. Extract slot and show from mapping
2. Parse start_time from slot
3. Get scheduled_duration from slot
4. Calculate end_time = start_time + duration
5. Format strings: start_date, start_time_str, end_time_str, day_of_week
6. Print show creation header

**Navigate to Schedule**
1. Calculate week_start (Monday of show's week)
2. Navigate to: {WEB_BASE_URL}/schedule?w={week_str}
3. Wait for networkidle state (15s timeout)

**Step 1: Open Create Modal**
1. Locate Create button by SVG icon and text
2. Click button (5s timeout)
3. Wait 1 second
4. Print success

**Step 2: Set Start Time**
1. Locate input by aria-labelledby containing "startTime"
2. Click input
3. Wait 500ms
4. Clear field
5. Wait 200ms
6. Format time using format_time_for_gui()
7. Type formatted time with 150ms delay per character
8. Wait 1200ms
9. Press Enter
10. Wait 600ms
11. Print success

**Step 3: Set End Time**
1. Locate input by aria-labelledby containing "endTime"
2. Click input
3. Wait 500ms
4. Clear field
5. Wait 200ms
6. Format time using format_time_for_gui()
7. Type formatted time with 150ms delay per character
8. Wait 1200ms
9. Press Enter
10. Wait 600ms
11. Print success

**Step 4: Set Start Date**
1. Get day number from start_time
2. Locate input with id starting with "startDate"
3. Click input
4. Wait 500ms
5. Locate button with role="gridcell" containing day number
6. Iterate through matching buttons
7. Click button where text exactly matches day number
8. If no exact match, click first button
9. Print success or error
10. Wait 1000ms

**Step 5: Set End Date**
1. Use same day as start_day
2. Locate input with id starting with "endDate"
3. If input exists:
   - Click input
   - Wait 500ms
   - Locate button with role="gridcell" containing day number
   - Iterate through matching buttons
   - Click button where text exactly matches day number
   - If no exact match, click first button
   - Print success
   - Wait 1000ms
4. If input doesn't exist, print info message

**Step 6: Set Title**
1. Create title: "{show['title']} (éist arís)"
2. Locate input with name="title"
3. Fill with title
4. Wait 100ms
5. Print success

**Step 7: Set Description**
1. Get description from show
2. If description is dict (structured content):
   - Initialize empty text_parts list
   - Iterate through content blocks
   - For each paragraph block:
     - For each text node:
       - Extract text and append to text_parts
   - Join all parts with spaces
   - If extraction fails, use default: "Éist arís replay show."
3. If description is empty or not a string, use default
4. Locate paragraph with data-placeholder containing "Enter event description"
5. Click field
6. Wait 100ms
7. Type description
8. Wait 200ms
9. Print success

**Step 8: Add Artist**
1. Get artist_name from show (try artist_name, then track_artist)
2. If artist_name exists:
   - Locate input with id="artist-select"
   - Click input
   - Wait 100ms
   - Type artist name
   - Wait 500ms
   - Press Enter
   - Wait 300ms
   - Print success
3. If no artist, print skip message

**Step 9: Enable Pre-record Mode**
1. Get button with role="button" and name="Mix Pre-record"
2. Click button
3. Wait 500ms
4. Print success

**Step 10: Open Media Selector**
1. Locate text "Select media" and get first match
2. Click button
3. Wait 1000ms
4. Print success

**Step 11: Search for Track**
1. Get track_title from show (or fallback to title)
2. If no track_title, print warning
3. Locate input with data-ds--text-field--input="true" (last match)
4. Fill with track_title
5. Wait 1000ms
6. Print success

**Step 12: Select Track**
1. Locate table row containing track_title (first match)
2. Click row
3. Wait 500ms
4. Print success
5. If error, raise exception

**Step 13: Create Event**
1. Locate button with type="submit" and text "Create event"
2. Click button
3. Wait 2000ms
4. Print success
5. Print final success message

## Mode Workflows

### MODE 1: --output-tracks
**Generates**: tracks.json (eligible shows with full metadata)

1. Print mode header
2. Call build_replay_list() with target_date and weeks_back
3. Get list of eligible shows
4. Authenticate using authenticate_with_playwright()
5. For each show in eligible_shows:
   - Print progress counter
   - If track_id exists:
     - Call fetch_track_details(track_id)
     - Parse response to get tracks array
     - Find matching track by id
     - Extract title and add to show as "track_title"
   - If artist_ids exists:
     - Get first artist_id
     - Call fetch_artist_details(artist_id)
     - Extract artist name
     - Add to show as "artist_name"
6. Save eligible_shows to tracks.json
7. Print summary: total shows, 1hr count, 2hr count

**Output**: tracks.json
```json
[
  {
    "title": "Original Show Name",
    "original_start": "2024-11-24T14:00:00Z",
    "original_end": "2024-11-24T16:00:00Z",
    "duration": 120,
    "scheduled_duration": 120,
    "media_type": "mix",
    "track_id": "abc123",
    "description": "...",
    "show_id": "xyz789",
    "color": "#FF5733",
    "artist_ids": ["artist1"],
    "artists": [...],
    "track_title": "Actual Track Title",
    "artist_name": "DJ Name"
  }
]
```

### MODE 2: --output-schedule
**Generates**: schedule.json (current week's schedule)

1. Print mode header
2. Calculate week_start using get_week_start(target_date)
3. Calculate end_date = week_start + days
4. Call fetch_schedule(week_start, end_date)
5. For each show in schedule:
   - Extract fields: id, title, start, end, duration
   - Extract media_type and track_id from media object
   - Extract description, artist_ids, artists, color
   - Append to schedule_json list
6. Save schedule_json to schedule.json
7. Print summary: total shows saved

**Output**: schedule.json
```json
[
  {
    "id": "show123",
    "title": "Current Show Name",
    "start": "2024-11-25T10:00:00Z",
    "end": "2024-11-25T11:00:00Z",
    "duration": 60,
    "media_type": "mix",
    "track_id": "track456",
    "description": "...",
    "artist_ids": ["artist1"],
    "artists": [...],
    "color": "#00FF00"
  }
]
```

### MODE 3: --test-slots
**Generates**: empty-slots.json (available time slots)

1. Print mode header
2. Call find_empty_slots(target_date, days)
3. Get list of empty slots
4. If no slots found:
   - Print "SCHEDULE IS COMPLETELY FILLED"
   - Save empty array to empty-slots.json
   - Return
5. For each slot:
   - Format start and end as "YYYY-MM-DD HH:MM"
   - Extract duration_minutes and scheduled_duration
   - Format day_of_week and date
   - Append to slots_json
6. Save slots_json to empty-slots.json
7. Print summary: total slots, 1hr count, 2hr count

**Output**: empty-slots.json
```json
[
  {
    "start": "2024-11-25 14:00",
    "end": "2024-11-25 16:00",
    "duration_minutes": 120,
    "scheduled_duration": 120,
    "day_of_week": "Monday",
    "date": "2024-11-25"
  }
]
```

### MODE 4: --plan
**Generates**: updated-slots.json (show-to-slot mappings)

**Phase 1: Load Input Files**
1. Print mode header
2. Check if tracks.json exists, exit if not
3. Load tracks.json into eligible_shows list
4. Print count loaded
5. Initialize empty already_scheduled_track_ids set
6. Check if schedule.json exists:
   - Load schedule.json
   - For each show in schedule:
     - Get track_id
     - If track_id exists, add to already_scheduled_track_ids set
   - Print count of shows loaded
   - Print count of track IDs found
7. If schedule.json missing:
   - Print warning
   - All shows will be considered

**Phase 2: Filter Duplicates**
1. Store original count of eligible_shows
2. Filter eligible_shows:
   - Keep only shows where track_id NOT in already_scheduled_track_ids
3. Calculate filtered_count = original - remaining
4. If any filtered:
   - Print count filtered
   - Print count remaining

**Phase 3: Load Slots**
1. Check if empty-slots.json exists, exit if not
2. Load empty-slots.json into empty_slots_data
3. Print count loaded
4. If no slots:
   - Print "SCHEDULE IS COMPLETELY FILLED"
   - Save empty array to updated-slots.json
   - Return

**Phase 4: Separate by Duration**
1. Split eligible_shows into:
   - shows_1hr (scheduled_duration == 60)
   - shows_2hr (scheduled_duration == 120)
2. Print counts
3. Split empty_slots_data into:
   - slots_1hr (scheduled_duration == 60)
   - slots_2hr (scheduled_duration == 120)
4. Print counts

**Phase 5: Shuffle and Match**
1. Copy shows_1hr to available_1hr
2. Copy shows_2hr to available_2hr
3. Shuffle both lists randomly
4. Initialize updated_slots = []
5. Initialize counters: shows_1hr_used, shows_2hr_used
6. Initialize reuse flags: reuse_warned_1hr, reuse_warned_2hr
7. For each slot in empty_slots_data:
   - Get slot_duration
   - If slot_duration == 60:
     - If available_1hr not empty:
       - Pop first show from available_1hr
       - Increment shows_1hr_used
     - Else if shows_1hr not empty (reuse scenario):
       - If not reuse_warned_1hr, print warning and set flag
       - Copy shows_1hr to available_1hr
       - Shuffle available_1hr
       - Pop first show
       - Increment shows_1hr_used
   - If slot_duration == 120:
     - If available_2hr not empty:
       - Pop first show from available_2hr
       - Increment shows_2hr_used
     - Else if shows_2hr not empty (reuse scenario):
       - If not reuse_warned_2hr, print warning and set flag
       - Copy shows_2hr to available_2hr
       - Shuffle available_2hr
       - Pop first show
       - Increment shows_2hr_used
   - If show was assigned:
     - Append {slot, show} to updated_slots
8. Save updated_slots to updated-slots.json
9. Print summary: total mappings, 1hr count, 2hr count

**Output**: updated-slots.json
```json
[
  {
    "slot": {
      "start": "2024-11-25 14:00",
      "end": "2024-11-25 16:00",
      "duration_minutes": 120,
      "scheduled_duration": 120,
      "day_of_week": "Monday",
      "date": "2024-11-25"
    },
    "show": {
      "title": "Show Name",
      "track_id": "abc123",
      "track_title": "Track Title",
      "artist_name": "Artist Name",
      ...
    }
  }
]
```

### MODE 5: --execute
**Executes**: Creates shows from updated-slots.json via Playwright

**Phase 1: Load Plan**
1. Print mode header
2. Check if updated-slots.json exists, exit if not
3. Load updated-slots.json into mappings list
4. Print count loaded
5. If no mappings, print message and return

**Phase 1a: Dry Run Mode (if --dry-run flag set)**
1. Print "DRY RUN MODE" header
2. For each mapping:
   - Print show details that would be created:
     - Title with "(éist arís)" suffix
     - Date and day of week
     - Time slot (start - end)
     - Duration in minutes
     - Track ID
     - Track title
     - Artist name
     - Description (truncated to 100 chars)
3. Print "DRY RUN COMPLETE" summary
4. Return without launching browser or creating shows

**Phase 2: Browser Setup (skipped if --dry-run)**
1. Launch Playwright
2. Create Chromium browser (headless mode determined by --headless flag)
3. Create browser context
4. Create new page

**Phase 3: Login**
1. Navigate to {WEB_BASE_URL}/login
2. Wait for email input (10s timeout)
3. Fill email field with login_username
4. Fill password field with login_password
5. Click submit button
6. Wait 2 seconds
7. Print success

**Phase 4: Create Shows**
1. Initialize counters: success_count, error_count
2. For each mapping in mappings:
   - Print progress counter
   - Try:
     - Call create_show_from_mapping(page, mapping)
     - Increment success_count
   - Catch Exception:
     - Increment error_count
     - Print error message
     - Try to save screenshot: error_screenshot_{i}.png
     - Call close_any_open_modals(page)
     - Print traceback
     - Continue to next mapping
3. Print final summary: success count, error count
4. Wait 1 second
5. Close browser

## Data Flow Summary

### Typical Usage Pattern
```
1. python script.py 2024-12-02 --output-schedule
   → Generates schedule.json (current week's shows)

2. python script.py 2024-12-02 --output-tracks
   → Generates tracks.json (eligible past shows)

3. python script.py 2024-12-02 --test-slots
   → Generates empty-slots.json (available time slots)

4. python script.py 2024-12-02 --plan
   → Reads: tracks.json, schedule.json, empty-slots.json
   → Filters duplicates by track_id
   → Generates: updated-slots.json (mappings)

5. python script.py 2024-12-02 --execute
   → Reads: updated-slots.json
   → Creates shows via browser automation
```

## Key Algorithms

### Slot Availability vs Duplicate Prevention

**Two Separate Concerns:**

1. **Slot Availability** (in --test-slots mode via find_empty_slots):
   - ALL shows are treated as occupied (including existing éist arís shows)
   - Only truly empty time slots are considered available
   - Result: empty-slots.json contains only gaps in the schedule

2. **Duplicate Prevention** (in --plan mode):
   - Prevents scheduling the SAME track_id multiple times
   - Even if slots are available, won't schedule a duplicate track
   - Result: Filters eligible shows before mapping to slots

### Duplicate Detection (in --plan mode)
1. Load schedule.json for target week
2. Extract all track_id values into a set
3. Filter eligible shows by excluding any with track_id in that set
4. Only remaining shows (with unique track_ids) are mapped to slots
5. This prevents the same show/track from appearing twice in the target week

### Slot Matching (in --plan mode)
- **Strict Duration Matching**: 1hr shows only fill 1hr slots, 2hr shows only fill 2hr slots
- **Randomization**: Shows are shuffled before assignment
- **Reuse**: If more slots than unique shows, shows are reused (with warning)
- **No Duration Conversion**: Script never tries to fit a 2hr show into a 1hr slot or vice versa

### Gap Splitting (in find_empty_slots)
1. Detect all time gaps between shows
2. Greedily create 2hr slots first
3. Then create 1hr slots from remaining time
4. Discard gaps less than 1hr

## Error Handling

### API Failures
- fetch_schedule(): Returns empty list on error
- fetch_track_details(): Returns None on error
- fetch_artist_details(): Returns None on error
- Missing track/artist details: Continues with partial data

### Authentication Failures
- Prints warning if credentials missing
- Sets authenticated=False if API returns 401
- --output-tracks mode requires authentication

### Playwright Failures (--execute mode)
- Each show creation wrapped in try/catch
- On error:
  - Screenshot saved
  - Modals closed
  - Continues to next show
- Final report shows success/error counts

### Missing Files (--plan mode)
- Exits if tracks.json missing
- Exits if empty-slots.json missing
- Warns if schedule.json missing (no duplicate detection)

## Known Issues & Workarounds

### GUI Time Input Bug
- Problem: GUI interprets "12:00" as midnight
- Workaround: format_time_for_gui() converts to "12:00pm"
- Problem: GUI has issues with "15:00"
- Workaround: Convert to "3:00pm"

### Modal Dialog Issues
- Problem: Failed operations can leave modals open
- Workaround: close_any_open_modals() presses Escape 3x

### Date Picker Ambiguity
- Problem: Multiple buttons can have same day number
- Workaround: Click button where inner_text exactly matches day
- Fallback: Click first matching button

## Configuration Notes

### Time Window (9am-11pm)
- Hard-coded in find_empty_slots()
- day_start = 09:00 UTC
- day_end = 23:00 UTC

### Eligibility Criteria
- Media type must be "mix"
- Duration must be 60±1 or 120±1 minutes
- Cannot already have éist arís suffix
- Must have track_id

### Show Durations
- Actual file duration can vary slightly
- Scheduled durations are normalized to exactly 60 or 120
- Closer to 120 → scheduled as 2hr
- Closer to 60 → scheduled as 1hr
