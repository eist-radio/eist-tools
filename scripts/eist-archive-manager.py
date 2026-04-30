#!/usr/bin/env python3
"""
Archive old Radiocult media (tracks + recordings) to Google Drive and clean up.

Modes:
- --scan       → list media older than N weeks, save to archive-scan.json
- --archive    → download old media, upload to Google Drive, tag in Radiocult
- --cleanup    → tag archived media as ready_to_delete (manual deletion in Radiocult UI)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

STATION_ID = "eist-radio"
API_BASE_URL = "https://api.radiocult.fm/api/station"
WEB_BASE_URL = "https://app.radiocult.fm"

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ---------------------------------------------------------------------------
# Radiocult API client
# ---------------------------------------------------------------------------


class RadiocultClient:
    def __init__(self, api_key: str, username: str, password: str) -> None:
        self.api_key = api_key
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        self.authenticated = False

    def authenticate(self, headless: bool = True) -> None:
        if self.authenticated:
            return
        print("Authenticating with Playwright to get session cookies...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(f"{WEB_BASE_URL}/login")
                page.wait_for_selector('input[type="email"]', timeout=10_000)
                page.fill('input[type="email"]', self.username)
                page.fill('input[type="password"]', self.password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(3_000)
                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path"),
                    )
                resp = self.session.get(
                    f"{API_BASE_URL}/{STATION_ID}/media/track"
                )
                if resp.status_code == 401:
                    print("Authentication failed — API returned 401", file=sys.stderr)
                    sys.exit(1)
                self.authenticated = True
                print("Authentication successful!")
            except Exception as exc:
                print(f"Authentication error: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                browser.close()

    def get_future_track_ids(self, weeks_ahead: int = 12) -> set:
        """Return track IDs used in any show scheduled from now onwards."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(weeks=weeks_ahead)
        start_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self.session.get(
            f"{API_BASE_URL}/{STATION_ID}/schedule",
            params={"startDate": start_str, "endDate": end_str},
        )
        resp.raise_for_status()
        schedules = resp.json().get("schedules", [])
        track_ids = set()
        for show in schedules:
            media = show.get("media") or {}
            tid = media.get("trackId")
            if tid:
                track_ids.add(tid)
        return track_ids

    def list_tracks(self) -> List[Dict]:
        resp = self.session.get(f"{API_BASE_URL}/{STATION_ID}/media/track")
        resp.raise_for_status()
        return resp.json().get("tracks", [])

    def list_recordings(self) -> List[Dict]:
        resp = self.session.get(f"{API_BASE_URL}/{STATION_ID}/media/recording")
        resp.raise_for_status()
        return resp.json().get("recordings", [])

    def list_all_media(self) -> List[Dict]:
        tracks = self.list_tracks()
        for t in tracks:
            t["_media_type"] = "track"
        recordings = self.list_recordings()
        for r in recordings:
            r["_media_type"] = "recording"
        return tracks + recordings

    def get_download_url(self, media_id: str, media_type: str = "track") -> str:
        resp = self.session.get(
            f"{API_BASE_URL}/{STATION_ID}/media/{media_type}/{media_id}/download-url"
        )
        resp.raise_for_status()
        return resp.json().get("url", "")

    def download_media(
        self, media_id: str, dest_path: str, media_type: str = "track",
        expected_size: int = 0,
    ) -> None:
        url = self.get_download_url(media_id, media_type)
        if not url:
            raise RuntimeError(f"No download URL returned for {media_type} {media_id}")
        resp = requests.get(url, stream=True, timeout=(15, 300))
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  Downloading: {pct}% ({downloaded // 1024 // 1024}MB)", end="", flush=True)
        print()
        actual_size = os.path.getsize(dest_path)
        if expected_size and abs(actual_size - expected_size) > 1024:
            os.remove(dest_path)
            raise RuntimeError(
                f"Download size mismatch: expected {expected_size} bytes, "
                f"got {actual_size} bytes"
            )

    def list_tags(self) -> List[Dict]:
        resp = self.session.get(f"{API_BASE_URL}/{STATION_ID}/media/tag")
        resp.raise_for_status()
        return resp.json().get("tags", [])

    def find_or_create_tag(self, name: str, color: str = "#998DD9") -> str:
        tags = self.list_tags()
        for tag in tags:
            if tag.get("name", "").lower() == name.lower():
                return tag["id"]
        resp = self.session.post(
            f"{API_BASE_URL}/{STATION_ID}/media/tag",
            json={"name": name, "color": color},
        )
        resp.raise_for_status()
        data = resp.json()
        tag_id = data.get("id") or data.get("tag", {}).get("id")
        if not tag_id:
            tags = self.list_tags()
            for tag in tags:
                if tag.get("name", "").lower() == name.lower():
                    return tag["id"]
            raise RuntimeError(f"Could not create tag '{name}': {data}")
        return tag_id

    def tag_media(self, media_id: str, tag_id: str, media_type: str = "track") -> None:
        resp = self.session.put(
            f"{API_BASE_URL}/{STATION_ID}/media/{media_type}/{media_id}/tag/{tag_id}"
        )
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Google Drive client
# ---------------------------------------------------------------------------


class GoogleDriveClient:
    DRIVE_API = "https://www.googleapis.com/drive/v3/files"
    UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"

    TOKEN_REFRESH_INTERVAL = 45 * 60  # refresh after 45 min (tokens last ~60 min)

    def __init__(self) -> None:
        self.token: Optional[str] = None
        self._token_acquired_at: float = 0

    def get_token(self) -> str:
        if self.token and (time.time() - self._token_acquired_at) < self.TOKEN_REFRESH_INTERVAL:
            return self.token
        self.token = None
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            self.token = result.stdout.strip()
            self._token_acquired_at = time.time()
            return self.token

        print("No active gcloud credentials. Authenticating...")
        login = subprocess.run(
            ["gcloud", "auth", "login", "--enable-gdrive-access"],
        )
        if login.returncode != 0:
            print("Error: gcloud authentication failed.", file=sys.stderr)
            sys.exit(1)

        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("Error: Could not obtain access token.", file=sys.stderr)
            sys.exit(1)
        self.token = result.stdout.strip()
        return self.token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _refresh_token_if_needed(self, resp: requests.Response) -> bool:
        if resp.status_code == 401:
            self.token = None
            self.get_token()
            return True
        return False

    def find_or_create_folder(
        self, name: str, parent_id: Optional[str] = None
    ) -> str:
        query = (
            f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        resp = requests.get(
            self.DRIVE_API,
            headers=self._headers(),
            params={"q": query, "fields": "files(id,name)", "spaces": "drive"},
        )
        if self._refresh_token_if_needed(resp):
            resp = requests.get(
                self.DRIVE_API,
                headers=self._headers(),
                params={"q": query, "fields": "files(id,name)", "spaces": "drive"},
            )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        if files:
            return files[0]["id"]

        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        resp = requests.post(
            self.DRIVE_API,
            headers={**self._headers(), "Content-Type": "application/json"},
            json=metadata,
        )
        resp.raise_for_status()
        folder_id = resp.json()["id"]
        print(f"  Created Drive folder: {name} ({folder_id})")
        return folder_id

    def ensure_folder_path(
        self, root_name: str, year: str, month_folder: str
    ) -> str:
        root_id = self.find_or_create_folder(root_name)
        year_id = self.find_or_create_folder(year, parent_id=root_id)
        month_id = self.find_or_create_folder(month_folder, parent_id=year_id)
        return month_id

    def find_existing_file(self, filename: str, folder_id: str) -> Optional[str]:
        query = (
            f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        )
        resp = requests.get(
            self.DRIVE_API,
            headers=self._headers(),
            params={"q": query, "fields": "files(id,name,size)", "spaces": "drive"},
        )
        if self._refresh_token_if_needed(resp):
            resp = requests.get(
                self.DRIVE_API,
                headers=self._headers(),
                params={"q": query, "fields": "files(id,name,size)", "spaces": "drive"},
            )
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            if files:
                return files[0]["id"]
        return None

    def upload_file(self, file_path: str, folder_id: str) -> str:
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        existing_id = self.find_existing_file(filename, folder_id)
        if existing_id:
            print(f"  File already exists in Drive ({existing_id}), skipping upload")
            return existing_id

        metadata = json.dumps(
            {"name": filename, "parents": [folder_id]}
        ).encode("utf-8")

        # Initiate resumable upload
        resp = requests.post(
            f"{self.UPLOAD_API}?uploadType=resumable",
            headers={
                **self._headers(),
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "audio/mpeg",
                "X-Upload-Content-Length": str(file_size),
            },
            data=metadata,
        )
        if self._refresh_token_if_needed(resp):
            resp = requests.post(
                f"{self.UPLOAD_API}?uploadType=resumable",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "audio/mpeg",
                    "X-Upload-Content-Length": str(file_size),
                },
                data=metadata,
            )
        resp.raise_for_status()
        upload_url = resp.headers["Location"]

        # Upload in chunks
        chunk_size = 10 * 1024 * 1024  # 10MB
        uploaded = 0

        with open(file_path, "rb") as f:
            while uploaded < file_size:
                chunk = f.read(chunk_size)
                end = uploaded + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
                }
                resp = requests.put(upload_url, headers=headers, data=chunk)

                if resp.status_code in (200, 201):
                    file_id = resp.json()["id"]
                    print(f"  Upload complete: {filename} ({file_id})")
                    return file_id
                elif resp.status_code == 308:
                    uploaded = end + 1
                    pct = uploaded * 100 // file_size
                    print(f"\r  Uploading: {pct}% ({uploaded // 1024 // 1024}MB)", end="", flush=True)
                else:
                    resp.raise_for_status()

        raise RuntimeError(f"Upload did not complete for {filename}")

    def verify_file(self, file_id: str, expected_size: int = 0) -> bool:
        resp = requests.get(
            f"{self.DRIVE_API}/{file_id}",
            headers=self._headers(),
            params={"fields": "id,name,size"},
        )
        if self._refresh_token_if_needed(resp):
            resp = requests.get(
                f"{self.DRIVE_API}/{file_id}",
                headers=self._headers(),
                params={"fields": "id,name,size"},
            )
        if resp.status_code != 200:
            return False
        if expected_size:
            drive_size = int(resp.json().get("size", 0))
            if abs(drive_size - expected_size) > 1024:
                return False
        return True


# ---------------------------------------------------------------------------
# Archive state manager
# ---------------------------------------------------------------------------


class ArchiveStateManager:
    def __init__(self, path: str = "archive-state.json") -> None:
        self.path = path
        self.state: Dict[str, Dict] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def is_archived(self, track_id: str) -> bool:
        entry = self.state.get(track_id)
        return entry is not None and entry.get("status") in ("archived", "deleted")

def mark(self, track_id: str, status: str, **metadata) -> None:
        if track_id not in self.state:
            self.state[track_id] = {}
        self.state[track_id]["status"] = status
        self.state[track_id].update(metadata)
        self.save()


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def folder_for_date(created_iso: str) -> Tuple[str, str]:
    dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    year = str(dt.year)
    month_folder = f"{dt.month:02d} - {MONTH_NAMES[dt.month]}"
    return year, month_folder


def mode_scan(rc: RadiocultClient, weeks: int) -> List[Dict]:
    print(f"\nScanning for media older than {weeks} weeks...\n")
    all_media = rc.list_all_media()
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)

    old_media = []
    for t in all_media:
        created = t.get("created")
        if not created:
            continue
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt < cutoff:
            old_media.append(t)

    old_media.sort(key=lambda t: t.get("created", ""))

    n_tracks = sum(1 for m in old_media if m.get("_media_type") == "track")
    n_recordings = sum(1 for m in old_media if m.get("_media_type") == "recording")
    total_size = sum(t.get("fileSize", 0) for t in old_media)
    print(f"Found {len(old_media)} items older than {weeks} weeks ({n_tracks} tracks, {n_recordings} recordings)")
    print(f"Total size: {total_size / 1024 / 1024 / 1024:.2f} GB")
    if old_media:
        print(f"Oldest: {old_media[0].get('created', 'N/A')[:10]}")
        print(f"Newest: {old_media[-1].get('created', 'N/A')[:10]}")

    print(f"\n{'Title':<40} {'Type':<6} {'Created':<12} {'Size (MB)':>10}")
    print("-" * 72)
    for t in old_media:
        title = (t.get("title") or t.get("filename", "?"))[:39]
        mtype = t.get("_media_type", "?")[:5]
        created = t.get("created", "")[:10]
        size_mb = t.get("fileSize", 0) / 1024 / 1024
        print(f"{title:<40} {mtype:<6} {created:<12} {size_mb:>9.1f}")

    scan_path = "archive-scan.json"
    with open(scan_path, "w", encoding="utf-8") as f:
        json.dump(old_media, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {scan_path}")

    return old_media


def mode_archive(
    rc: RadiocultClient,
    drive: GoogleDriveClient,
    state: ArchiveStateManager,
    weeks: int,
    output_dir: str,
    drive_folder: str,
    dry_run: bool,
) -> None:
    scan_path = "archive-scan.json"
    if os.path.exists(scan_path):
        with open(scan_path, "r", encoding="utf-8") as f:
            old_media = json.load(f)
        print(f"Loaded {len(old_media)} items from {scan_path}")
    else:
        old_media = mode_scan(rc, weeks)

    to_archive = [t for t in old_media if not state.is_archived(t["id"])]
    print(f"\n{len(to_archive)} items to archive ({len(old_media) - len(to_archive)} already done)")

    if not to_archive:
        print("Nothing to archive.")
        return

    if dry_run:
        print("\n[DRY RUN] Would archive:")
        for t in to_archive:
            year, month = folder_for_date(t["created"])
            title = t.get("title") or t.get("filename", "?")
            mtype = t.get("_media_type", "track")
            print(f"  [{mtype}] {title} → {drive_folder}/{year}/{month}/")
        return

    os.makedirs(output_dir, exist_ok=True)

    archive_tag_id = rc.find_or_create_tag("ready_to_archive")
    print(f"Tag 'ready_to_archive' ID: {archive_tag_id}")

    archived_count = 0
    for i, item in enumerate(to_archive, 1):
        item_id = item["id"]
        media_type = item.get("_media_type", "track")
        title = item.get("title") or item.get("filename", "?")
        filename = item.get("filename") or f"{item_id}.mp3"
        created = item.get("created", "")
        year, month_folder = folder_for_date(created)

        print(f"\n[{i}/{len(to_archive)}] [{media_type}] {title}")
        print(f"  Created: {created[:10]} → {drive_folder}/{year}/{month_folder}/")

        local_path = os.path.join(output_dir, filename)

        expected_size = item.get("fileSize", 0)

        try:
            print(f"  Downloading to {local_path}...")
            rc.download_media(item_id, local_path, media_type, expected_size=expected_size)

            folder_id = drive.ensure_folder_path(drive_folder, year, month_folder)
            drive_file_id = drive.upload_file(local_path, folder_id)

            if not drive.verify_file(drive_file_id, expected_size=expected_size):
                print(f"  WARNING: Could not verify upload (size mismatch or missing), skipping", file=sys.stderr)
                continue

            rc.tag_media(item_id, archive_tag_id, media_type)
            print(f"  Tagged as ready_to_archive")

            state.mark(
                item_id,
                "archived",
                title=title,
                filename=filename,
                created=created,
                media_type=media_type,
                drive_file_id=drive_file_id,
                archived_at=datetime.now(timezone.utc).isoformat(),
            )

            os.remove(local_path)
            archived_count += 1

        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            if os.path.exists(local_path):
                os.remove(local_path)
            continue

    print(f"\nArchived {archived_count}/{len(to_archive)} items")


def mode_cleanup(
    rc: RadiocultClient,
    drive: GoogleDriveClient,
    state: ArchiveStateManager,
    dry_run: bool,
) -> None:
    to_delete = {
        tid: entry
        for tid, entry in state.state.items()
        if entry.get("status") == "archived"
    }

    if not to_delete:
        print("No archived tracks pending cleanup.")
        return

    print(f"\n{len(to_delete)} archived items to clean up")

    # Check future schedule — never delete tracks used in upcoming shows
    print("\nChecking future schedule (next 12 weeks)...")
    future_track_ids = rc.get_future_track_ids(weeks_ahead=12)
    print(f"  {len(future_track_ids)} tracks in future shows")

    scheduled = {tid: entry for tid, entry in to_delete.items() if tid in future_track_ids}
    if scheduled:
        print(f"\n  Skipping {len(scheduled)} tracks still in future shows:")
        for tid, entry in scheduled.items():
            print(f"    {entry.get('title', tid)}")
        to_delete = {tid: entry for tid, entry in to_delete.items() if tid not in future_track_ids}

    if not to_delete:
        print("\nAll archived tracks are still in future shows. Nothing to delete.")
        return

    print(f"\n{len(to_delete)} items safe to delete\n")

    # Verify each file still exists in Drive
    verified = {}
    for tid, entry in to_delete.items():
        drive_file_id = entry.get("drive_file_id")
        title = entry.get("title", tid)
        if not drive_file_id:
            print(f"  {title}: no Drive file ID, skipping")
            continue
        if drive.verify_file(drive_file_id):
            verified[tid] = entry
            print(f"  {title}: verified in Drive")
        else:
            print(f"  {title}: NOT found in Drive, skipping", file=sys.stderr)

    if not verified:
        print("No tracks verified in Drive. Aborting cleanup.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would tag {len(verified)} items as ready_to_delete:")
        for tid, entry in verified.items():
            print(f"  {entry.get('title', tid)}")
        return

    # Tag as ready_to_delete so they can be filtered and deleted in the Radiocult UI
    delete_tag_id = rc.find_or_create_tag("ready_to_delete")
    tagged = 0
    for tid, entry in verified.items():
        title = entry.get("title", tid)
        try:
            mtype = entry.get("media_type", "track")
            rc.tag_media(tid, delete_tag_id, mtype)
            tagged += 1
            print(f"  Tagged: {title}")
        except Exception as exc:
            print(f"  Warning: could not tag {title}: {exc}", file=sys.stderr)

    print(f"\nTagged {tagged}/{len(verified)} tracks as ready_to_delete")
    print("Delete them manually in the Radiocult Media library using the Tag filter.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Archive old Radiocult media to Google Drive.",
    )
    parser.add_argument("--scan", action="store_true", help="List tracks and recordings older than --weeks")
    parser.add_argument("--archive", action="store_true", help="Download + upload to Drive")
    parser.add_argument("--cleanup", action="store_true", help="Tag archived media as ready_to_delete for manual removal")
    parser.add_argument("--weeks", type=int, default=8, help="Age threshold in weeks (default: 8)")
    parser.add_argument("--output", default="./archive-tmp", help="Temp download directory")
    parser.add_argument("--drive-folder", default="éist - archive", help="Root Google Drive folder name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--interactive", action="store_true", help="Show browser window")
    args = parser.parse_args()

    if not (args.scan or args.archive or args.cleanup):
        parser.print_help()
        sys.exit(1)

    api_key = os.getenv("API_KEY")
    username = os.getenv("RADIOCULT_USER")
    password = os.getenv("RADIOCULT_PW")

    if not api_key or not username or not password:
        print(
            "Error: API_KEY, RADIOCULT_USER, and RADIOCULT_PW must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    rc = RadiocultClient(api_key, username, password)
    state = ArchiveStateManager()

    headless = not args.interactive

    if args.scan:
        rc.authenticate(headless=headless)
        mode_scan(rc, args.weeks)

    if args.archive:
        rc.authenticate(headless=headless)
        drive = GoogleDriveClient()
        mode_archive(rc, drive, state, args.weeks, args.output, args.drive_folder, args.dry_run)

    if args.cleanup:
        rc.authenticate(headless=headless)
        drive = GoogleDriveClient()
        mode_cleanup(rc, drive, state, args.dry_run)


if __name__ == "__main__":
    main()
