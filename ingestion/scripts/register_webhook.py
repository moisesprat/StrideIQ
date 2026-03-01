#!/usr/bin/env python3
"""
Register the Strava webhook subscription.

Run this ONCE after deploying the ingestion service. Strava will immediately
send a GET verification request to your /webhook endpoint, so the service
must be publicly reachable before running this script.

Usage:
    cd ingestion
    python scripts/register_webhook.py

    # To list existing subscriptions:
    python scripts/register_webhook.py --list

    # To delete a subscription:
    python scripts/register_webhook.py --delete <subscription_id>
"""

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

STRAVA_API_BASE = "https://www.strava.com/api/v3"

client_id = os.getenv("STRAVA_CLIENT_ID", "")
client_secret = os.getenv("STRAVA_CLIENT_SECRET", "")
verify_token = os.getenv("STRAVA_VERIFY_TOKEN", "strideiq_verify")
app_base_url = os.getenv("APP_BASE_URL", "").rstrip("/")

callback_url = f"{app_base_url}/webhook"


def register() -> None:
    if not all([client_id, client_secret, app_base_url]):
        print("ERROR: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, and APP_BASE_URL must be set.")
        sys.exit(1)

    print(f"Registering webhook subscription...")
    print(f"  Callback URL : {callback_url}")
    print(f"  Verify token : {verify_token}")
    print()

    resp = httpx.post(
        f"{STRAVA_API_BASE}/push_subscriptions",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "callback_url": callback_url,
            "verify_token": verify_token,
        },
    )

    if resp.status_code == 201:
        data = resp.json()
        print(f"✓ Subscription created! ID: {data['id']}")
        print(f"  Save this ID if you ever need to delete the subscription.")
    elif resp.status_code == 422:
        print("✓ A subscription already exists for this app + callback URL.")
        print(f"  Response: {resp.json()}")
    else:
        print(f"✗ Failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


def list_subscriptions() -> None:
    resp = httpx.get(
        f"{STRAVA_API_BASE}/push_subscriptions",
        params={"client_id": client_id, "client_secret": client_secret},
    )
    resp.raise_for_status()
    subs = resp.json()
    if not subs:
        print("No active subscriptions.")
    for sub in subs:
        print(f"  ID: {sub['id']}  Callback: {sub['callback_url']}")


def delete_subscription(subscription_id: int) -> None:
    resp = httpx.delete(
        f"{STRAVA_API_BASE}/push_subscriptions/{subscription_id}",
        params={"client_id": client_id, "client_secret": client_secret},
    )
    if resp.status_code == 204:
        print(f"✓ Subscription {subscription_id} deleted.")
    else:
        print(f"✗ Failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Strava webhook subscriptions")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="List existing subscriptions")
    group.add_argument("--delete", type=int, metavar="ID", help="Delete a subscription by ID")
    args = parser.parse_args()

    if args.list:
        list_subscriptions()
    elif args.delete:
        delete_subscription(args.delete)
    else:
        register()
