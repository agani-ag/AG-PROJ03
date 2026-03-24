"""
Send Push Notifications - Helper Script
Uses your Flask backend to send FCM notifications.

Usage:
    python send_notification.py --all "Hello" "Test message to all users"
    python send_notification.py --user test@example.com "Hi" "Message for specific user"
    python send_notification.py --device DEVICE_ID USER_ID "Alert" "Message for specific device"
    python send_notification.py --list   (show all registered devices)
"""

import requests
import argparse
import json

API_BASE = "https://bowling-names-developments-front.trycloudflare.com"


def list_devices():
    """Show all registered devices"""
    try:
        response = requests.get(f"{API_BASE}/api/device/list")
        data = response.json()

        print(f"\nRegistered Devices: {data.get('total_devices', 0)} ({data.get('total_users', 0)} users)")
        print("-" * 80)

        for device in data.get("devices", []):
            print(f"  User: {device['user_id']}")
            print(f"  Device: {device['device_id']}")
            print(f"  Platform: {device['platform']}")
            print(f"  Token: {device['token_preview']}")
            print(f"  First Registered: {device.get('registered_at', 'N/A')}")
            print(f"  Last Login: {device.get('last_login', 'N/A')}")
            print("-" * 80)

        if not data.get("devices"):
            print("  No devices registered yet.")

    except Exception as e:
        print(f"Error: {e}")


def send_notification(target, title, body, user_id=None, device_id=None, data=None):
    """Send push notification via Flask API → FCM"""

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

    print(f"\nSending notification...")
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

        result = response.json()

        if result.get("success"):
            print(f"\nSuccess! Sent: {result.get('sent')}, Failed: {result.get('failed')}")
        else:
            print(f"\nFailed: {result.get('message')}")

    except requests.exceptions.RequestException as e:
        print(f"\nError: {e}")


def main():
    parser = argparse.ArgumentParser(description="Send push notifications via FCM")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Send to all users")
    group.add_argument("--user", type=str, help="Send to specific user (email)")
    group.add_argument("--device", nargs=2, metavar=("DEVICE_ID", "USER_ID"), help="Send to specific device")
    group.add_argument("--list", action="store_true", help="List all registered devices")

    parser.add_argument("title", type=str, nargs="?", help="Notification title")
    parser.add_argument("body", type=str, nargs="?", help="Notification message")
    parser.add_argument("--data", type=str, help="JSON data payload (optional)")

    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    if not args.title or not args.body:
        print("Error: title and body are required")
        return

    data = None
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError:
            print("Invalid JSON in --data parameter")
            return

    if args.all:
        send_notification("all", args.title, args.body, data=data)
    elif args.user:
        send_notification("user", args.title, args.body, user_id=args.user, data=data)
    elif args.device:
        device_id, user_id = args.device
        send_notification("device", args.title, args.body, user_id=user_id, device_id=device_id, data=data)


if __name__ == "__main__":
    main()
