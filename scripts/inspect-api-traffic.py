#!/usr/bin/env python3
"""
Script to inspect API traffic when browsing the Radiocult media library.
This helps discover undocumented API endpoints.
"""

import os
import json
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

login_username = os.getenv('RADIOCULT_USER')
login_password = os.getenv('RADIOCULT_PW')

if not login_username or not login_password:
    print("Error: RADIOCULT_USER and RADIOCULT_PW must be set in .env")
    exit(1)

# Store API calls
api_calls = []

def log_request(request):
    """Log all requests to radiocult API."""
    if 'radiocult.fm/api' in request.url:
        api_calls.append({
            'method': request.method,
            'url': request.url,
            'headers': dict(request.headers),
            'post_data': request.post_data if request.method == 'POST' else None
        })
        print(f"\n[REQUEST] {request.method} {request.url}")

def log_response(response):
    """Log all responses from radiocult API."""
    if 'radiocult.fm/api' in response.url:
        try:
            # Try to get response body
            body = response.body()
            try:
                # Try to parse as JSON
                json_body = json.loads(body)
                print(f"[RESPONSE] {response.status} {response.url}")
                print(f"Response preview: {json.dumps(json_body, indent=2)[:500]}")
            except:
                print(f"[RESPONSE] {response.status} {response.url}")
                print(f"Body length: {len(body)} bytes")
        except Exception as e:
            print(f"[RESPONSE] {response.status} {response.url} (couldn't read body: {e})")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # Set up request/response interceptors
    page.on("request", log_request)
    page.on("response", log_response)

    print("Navigating to login page...")
    page.goto("https://app.radiocult.fm/login")

    print("Logging in...")
    page.wait_for_selector('input[type="email"], input[name="email"]', timeout=10000)
    page.fill('input[type="email"], input[name="email"]', login_username)
    page.fill('input[type="password"], input[name="password"]', login_password)
    page.click('button[type="submit"]')

    # Wait for login to complete
    page.wait_for_load_state('networkidle', timeout=15000)

    if '/login' in page.url:
        print("Login failed!")
        browser.close()
        exit(1)

    print("\nLogin successful! Navigating to media library...")
    page.goto("https://app.radiocult.fm/media")
    page.wait_for_load_state('networkidle', timeout=15000)

    print("\n" + "="*80)
    print("Media library page loaded. Watch the network traffic above.")
    print("="*80)
    print("\nActions you can try:")
    print("1. Scroll through the media library")
    print("2. Click on a track to view details")
    print("3. Use the search/filter features")
    print("4. Click 'Configure Columns' to see available fields")
    print("\nPress Enter when done to save the captured API calls...")

    input()

    # Save captured API calls
    print(f"\n\nCaptured {len(api_calls)} API calls")

    # Save to file
    with open('api-traffic.json', 'w') as f:
        json.dump(api_calls, f, indent=2)
    print("Saved to api-traffic.json")

    # Print summary
    print("\n" + "="*80)
    print("API ENDPOINTS DISCOVERED:")
    print("="*80)
    unique_endpoints = set()
    for call in api_calls:
        # Extract path from URL
        url = call['url']
        if '/api/' in url:
            path = url.split('/api/')[1].split('?')[0]
            unique_endpoints.add(f"{call['method']} /api/{path}")

    for endpoint in sorted(unique_endpoints):
        print(endpoint)

    browser.close()
