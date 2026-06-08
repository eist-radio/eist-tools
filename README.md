# éist radio tools

Automation tools for éist radio on radiocult: replay show scheduling and media archival.

## Setup

### Installation

```bash
pip install -r requirements.txt
```

This installs `requests`, `python-dotenv`, and `playwright`. After installing the Python packages, install the Playwright browser binaries and their OS-level dependencies:

**Fedora:**

```bash
# Install system dependencies (Playwright's install-deps requires apt-get)
sudo dnf install -y alsa-lib atk at-spi2-atk cups-libs libdrm mesa-libgbm nspr nss pango libXcomposite libXdamage libXrandr libXtst
```

**Debian/Ubuntu:**

```bash
playwright install-deps
```

Then install the browser binaries:

```bash
playwright install chromium
```

Or to install all supported browsers:

```bash
playwright install
```

### Environment Variables

Create a `.env` file with your credentials:

```env
API_KEY=your_api_key_here
radioCULT_USER=your_email@example.com
radioCULT_PW=your_password
```

For local development, you can get the API key from:
https://api.radiocult.fm/api/station/eist-radio/media/track
(Log in to radioCult in the browser first)

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

### Hourly Slot Check

Checks if the next hour has an empty slot or a pre-record show without a file attached (including recurring events). If so, deletes the broken show (if any), picks a random eligible show from the last 4 weeks, and creates it as an éist arís replay via the RadioCult API — including the original artist.

```bash
# Check the next hour from a specific Irish time (dry run)
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --dry-run

# Check and auto-fix (live)
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot

# Headless mode for CI
python scripts/add-eist-aris-shows.py "2026-06-05 14:45:00" --check-slot --headless
```

The input time is in Irish time (Europe/Dublin) and is rounded up to the next full hour. Only 1hr shows are used as replacements. Shows already scheduled in the same week are excluded to avoid duplicates.

### Command-line Options

**Required:**
- `date` - Target date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)

**Mode flags (one required):**
- `--output-schedule` - Generate schedule.json
- `--output-tracks` - Generate tracks.json
- `--test-slots` - Generate empty-slots.json
- `--plan` - Generate updated-slots.json
- `--execute` - Create shows from plan
- `--check-slot` - Check next hour's slot and auto-fix if empty or fileless pre-record

**Optional flags:**
- `--weeks-back N` - Weeks to look back for shows (default: 3)
- `--days N` - Days to process (default: 7)
- `--output PATH` - Custom output file path
- `--headless` - Run browser in headless mode (for CI)
- `--dry-run` - Print what would be done without creating shows

## Media Archive Manager

Archives old radiocult media (tracks and recordings) to Google Drive and cleans up storage.

### Prerequisites

In addition to the standard setup above, the archive manager requires:

- **gcloud CLI** — handles Google Drive authentication. [Install here](https://cloud.google.com/sdk/docs/install).
- One-time login: `gcloud auth login eistcork@gmail.com --enable-gdrive-access`

### Modes

```bash
# Full run (default): delete state files, then scan → archive → cleanup from scratch
python scripts/eist-archive-manager.py

# Scan: list all tracks and recordings older than 8 weeks
python scripts/eist-archive-manager.py --scan

# Archive: download old media, upload to Google Drive, tag in radiocult
python scripts/eist-archive-manager.py --archive

# Cleanup: delete archived media from radiocult (verifies Drive upload + checks future schedule first)
python scripts/eist-archive-manager.py --cleanup

# Selective pipeline (preserves existing state files)
python scripts/eist-archive-manager.py --archive --cleanup

# Preview any mode without making changes
python scripts/eist-archive-manager.py --archive --dry-run
```

Files are uploaded to the `éist - archive` Google Drive folder, organised as `<year>/<MM - Month>/` based on upload date (e.g. `éist - archive/2026/03 - March/`).

The cleanup step checks the next 12 weeks of scheduled shows and will not delete any track that is still in a future show.

### Options

- `--weeks N` — age threshold in weeks (default: 8)
- `--output DIR` — temp download directory (default: `./archive-tmp`)
- `--drive-folder NAME` — root Drive folder name (default: `éist - archive`)
- `--dry-run` — preview without making changes
- `--interactive` — show the browser window

### State files

- `archive-scan.json` — last scan results (re-used by `--archive` if present)
- `archive-state.json` — tracks archive/delete status per file, ensures re-runs are idempotent

## Cold Storage (Google Drive → NAS)

Moves old files from the `éist - archive` Google Drive folder to the DS214play Synology NAS for long-term cold storage. Files older than 12 months are downloaded from Drive, uploaded to the NAS via the File Station API (QuickConnect), verified by size, then you manually delete the originals from Drive.

### Prerequisites

- **gcloud CLI** — same auth as the archive manager: `gcloud auth login eistcork@gmail.com --enable-gdrive-access`
- **Synology NAS** — File Station package installed, with a shared folder matching the `--nas-path` root (default: `music`)
- NAS credentials in `.env`:

```env
NAS_USER=your_nas_username
NAS_PASSWORD=your_nas_password
```

### Modes

```bash
# Full run (default): scan → transfer → cleanup instructions
python scripts/cold-storage.py --nas-url https://192.168.1.29:5001

# Scan: list archive files older than 12 months
python scripts/cold-storage.py --scan

# Transfer: download from Drive, upload to NAS, verify
python scripts/cold-storage.py --transfer --nas-url https://192.168.1.29:5001

# Cleanup: print instructions to manually delete originals from Drive
python scripts/cold-storage.py --cleanup

# Preview without making changes
python scripts/cold-storage.py --dry-run
```

### Options

- `--months N` — age threshold in months (default: 12)
- `--output DIR` — temp download directory (default: `./cold-storage-tmp`)
- `--nas-path PATH` — NAS destination path (default: `/music/eist-archive`)
- `--nas-url URL` — direct NAS URL, skips QuickConnect resolution (e.g. `https://192.168.1.29:5001`)
- `--dry-run` — preview without making changes

### NAS connection

By default, the script resolves the NAS address via Synology QuickConnect (ID: `eistcork`). For large file transfers, use `--nas-url` to connect directly over the LAN — the QuickConnect relay can't handle large uploads reliably.

### State files

- `cold-storage-scan.json` — last scan results (re-used by `--transfer` if present)
- `cold-storage-state.json` — tracks transfer status per file, ensures re-runs are idempotent

### Drive cleanup

After transferring, `--cleanup` prints instructions to find and delete the originals from Google Drive using the advanced search UI (Type: Audio, Date modified: before the cutoff date). Automated deletion is not supported due to gcloud's read-only Drive scope.

## Automated Workflow (GitHub Actions)

The `.github/workflows/schedule-repeat-shows.yml` workflow runs automatically:

- **Schedule**: Every Saturday at 11:00 PM UTC
- **Target week**: The following Monday's week
- **Mode**: DRY RUN (prints output, doesn't create shows)

### Manual Trigger

You can trigger the workflow manually:

1. Go to Actions → Schedule éist Arís Shows
2. Click "Run workflow"
3. Options:
   - **Target date**: Custom date (optional, defaults to next Monday)
   - **Dry run**: `true` (preview only) or `false` (create shows)

### Viewing Results

After a workflow run:

1. Check the workflow summary for show counts
2. Download artifacts to see JSON files
3. Review logs for detailed output

### Hourly Slot Check (GitHub Actions + Cloudflare Worker)

The `.github/workflows/check-slot.yml` workflow checks the next hour's slot and auto-fixes if empty or has a fileless pre-record (LIVE mode — creates/deletes shows as needed).

**Scheduling:** A Cloudflare Worker (`cloudflare-worker/`) dispatches the workflow every 30 minutes during broadcast hours (`:30` past each hour, 08:30–22:30 UTC). GitHub Actions cron runs hourly at `:07` past as a fallback. If a successful run already occurred in the last hour, the duplicate self-cancels.

You can also trigger it manually from Actions → Check and fix schedule slots with an optional target datetime (Irish time).

#### Cloudflare Worker setup

The worker calls the GitHub API `workflow_dispatch` endpoint to trigger the check-slot workflow.

```bash
cd cloudflare-worker
npm install

# Set the GitHub token (fine-grained PAT with Actions:write on eist-radio/eist-tools)
npx wrangler secret put GITHUB_TOKEN

# Deploy
npx wrangler deploy

# For local dev, create .dev.vars with: GITHUB_TOKEN=ghp_your_token_here
```

### Keepalive (GitHub Actions)

The `.github/workflows/keepalive.yml` workflow creates a dummy commit on the 1st of each month to prevent GitHub from disabling the scheduled workflows after 60 days of repo inactivity.
