"""
Send Push Notifications - Helper Script
Usage:
    python send_notification.py --all "Hello" "Test message to all users"
    python send_notification.py --user test@example.com "Hi" "Message for specific user"
    python send_notification.py --device DEVICE_ID USER_ID "Alert" "Message for specific device"
"""

import requests
import argparse
import json

API_BASE = "https://microman2000.pythonanywhere.com"

def send_notification(target, title, body, user_id=None, device_id=None, data=None):
    """Send push notification via Flask API"""

    payload = {
        "target": target,
        "title": title,
        "body": body,
        "data": data or {}
    }

    if user_id:
        payload["user_id"] = user_id
    if device_id:
        payload["device_id"] = device_id

    print(f"\n📨 Sending notification...")
    print(f"   Target: {target}")
    if user_id:
        print(f"   User: {user_id}")
    if device_id:
        print(f"   Device: {device_id}")
    print(f"   Title: {title}")
    print(f"   Body: {body}")

    try:
        response = requests.post(
            f"{API_BASE}/api/notifications/send",
            headers={"Content-Type": "application/json"},
            json=payload
        )

        response.raise_for_status()
        result = response.json()

        if result.get("success"):
            print(f"\n✅ Success!")
            print(f"   Sent: {result.get('sent')} notifications")
            print(f"   Failed: {result.get('failed')}")
            print(f"   Total tokens: {result.get('total_tokens')}")
        else:
            print(f"\n❌ Failed: {result.get('message')}")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"   Response: {e.response.text}")


def main():
    parser = argparse.ArgumentParser(description="Send push notifications")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Send to all users")
    group.add_argument("--user", type=str, help="Send to specific user (email)")
    group.add_argument("--device", nargs=2, metavar=("DEVICE_ID", "USER_ID"), help="Send to specific device")

    parser.add_argument("title", type=str, help="Notification title")
    parser.add_argument("body", type=str, help="Notification message")
    parser.add_argument("--data", type=str, help="JSON data payload (optional)")

    args = parser.parse_args()

    # Parse data if provided
    data = None
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError:
            print("❌ Invalid JSON in --data parameter")
            return

    # Determine target
    if args.all:
        send_notification("all", args.title, args.body, data=data)
    elif args.user:
        send_notification("user", args.title, args.body, user_id=args.user, data=data)
    elif args.device:
        device_id, user_id = args.device
        send_notification("device", args.title, args.body, user_id=user_id, device_id=device_id, data=data)


if __name__ == "__main__":
    main()
