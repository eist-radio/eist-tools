# Éist arís show scheduler thingy

Automates scheduling "éist arís" (replay) shows

See [flow.md](flow.md) for details.

## Setup

### Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

### Environment Variables

Create a `.env` file with your credentials:

```env
API_KEY=your_api_key_here
RADIOCULT_USER=your_email@example.com
RADIOCULT_PW=your_password
```

For local development, you can get the API key from:
https://api.radiocult.fm/api/station/eist-radio/media/track
(Log in to RadioCult in the browser first)

## Usage

### Manual Workflow

Run these commands in sequence for a specific week:

```bash
# 1. Download the current schedule for the week → schedule.json
python scripts/add-eist-aris-shows.py "2025-12-08" --output-schedule

# 2. Fetch eligible replay shows from past 3 weeks → tracks.json
python scripts/add-eist-aris-shows.py "2025-12-08" --output-tracks

# 3. Find all empty time slots in the schedule → empty-slots.json
python scripts/add-eist-aris-shows.py "2025-12-08" --test-slots

# 4. Map shows to slots (filters duplicates) → updated-slots.json
python scripts/add-eist-aris-shows.py "2025-12-08" --plan

# 5a. Preview what would be created (DRY RUN)
python scripts/add-eist-aris-shows.py "2025-12-08" --execute --dry-run

# 5b. Actually create the shows (LIVE)
python scripts/add-eist-aris-shows.py "2025-12-08" --execute
```

### Command-line Options

**Required:**
- `date` - Target date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)

**Mode flags (one required):**
- `--output-schedule` - Generate schedule.json
- `--output-tracks` - Generate tracks.json
- `--test-slots` - Generate empty-slots.json
- `--plan` - Generate updated-slots.json
- `--execute` - Create shows from plan

**Optional flags:**
- `--weeks-back N` - Weeks to look back for shows (default: 3)
- `--days N` - Days to process (default: 7)
- `--output PATH` - Custom output file path
- `--headless` - Run browser in headless mode (for CI)
- `--dry-run` - Print what would be done without creating shows

## Automated Workflow (GitHub Actions)

The `.github/workflows/schedule-repeat-shows.yml` workflow runs automatically:

- **Schedule**: Every Saturday at 11:00 PM UTC
- **Target week**: The following Monday's week
- **Mode**: DRY RUN (prints output, doesn't create shows)

### Manual Trigger

You can trigger the workflow manually:

1. Go to Actions → Schedule Éist Arís Shows
2. Click "Run workflow"
3. Options:
   - **Target date**: Custom date (optional, defaults to next Monday)
   - **Dry run**: `true` (preview only) or `false` (create shows)

### Viewing Results

After a workflow run:

1. Check the workflow summary for show counts
2. Download artifacts to see JSON files
3. Review logs for detailed output
