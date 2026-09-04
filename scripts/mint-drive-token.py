#!/usr/bin/env python3
"""
One-time generator for a Google Drive OAuth refresh token.

Run this locally (not on the runner) to mint the GOOGLE_REFRESH_TOKEN that
eist-archive-manager.py uses for unattended uploads. It runs the Desktop-app
loopback flow: opens a browser, catches the redirect on localhost, and swaps
the code for a refresh token.

Prerequisites:
- A Desktop-type OAuth client in Google Cloud (Client ID + secret).
- The consent screen published to Production (Testing-mode tokens expire after
  7 days).

Usage:
    export GOOGLE_CLIENT_ID=...        # or pass --client-id
    export GOOGLE_CLIENT_SECRET=...    # or pass --client-secret
    python scripts/mint-drive-token.py

Then store the three values as repo secrets: GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.
"""

import argparse
import http.server
import os
import socket
import sys
import urllib.parse
import webbrowser

import requests

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _capture_code(port: int) -> str:
    """Serve one request on localhost and return the ?code= it carries."""
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured["code"] = params.get("code", [None])[0]
            captured["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            msg = "Authorisation received. You can close this tab."
            self.wfile.write(msg.encode())

        def log_message(self, format, *args):  # silence the default stderr logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.handle_request()
    server.server_close()

    if captured.get("error"):
        print(f"Error: authorisation denied ({captured['error']}).", file=sys.stderr)
        sys.exit(1)
    if not captured.get("code"):
        print("Error: no authorisation code received.", file=sys.stderr)
        sys.exit(1)
    return captured["code"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a Google Drive OAuth refresh token.")
    parser.add_argument("--client-id", default=os.getenv("GOOGLE_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.getenv("GOOGLE_CLIENT_SECRET"))
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Error: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
            "(env vars or --client-id / --client-secret).",
            file=sys.stderr,
        )
        sys.exit(1)

    port = _free_port()
    redirect_uri = f"http://localhost:{port}"

    auth_url = f"{AUTH_URI}?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",  # ask for a refresh token
            "prompt": "consent",       # force one even on re-auth
        }
    )

    print("Opening your browser to authorise Drive access...")
    print(f"If it does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    code = _capture_code(port)

    resp = requests.post(
        TOKEN_URI,
        data={
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Error: token exchange failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    refresh_token = resp.json().get("refresh_token")
    if not refresh_token:
        print(
            "Error: no refresh_token returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and retry, or confirm "
            "the consent screen is published to Production.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nSuccess. Store these as repo secrets:\n")
    print(f"  GOOGLE_CLIENT_ID={args.client_id}")
    print(f"  GOOGLE_CLIENT_SECRET={args.client_secret}")
    print(f"  GOOGLE_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()
