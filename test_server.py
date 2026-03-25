"""
SyncUp App - Flask Test Server
Run: pip install flask flask-cors requests google-auth
     python test_server.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import requests
import json
import os

app = Flask(__name__)
CORS(app)  # Allow requests from React Native / WebView

# ── FCM v1 API Configuration ─────────────────────────────────────────────────
# Download service account JSON from:
#   Firebase Console → Project Settings → Service accounts → Generate new private key
FIREBASE_PROJECT_ID = "syncup-f470b"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase-service-account.json")
# ─────────────────────────────────────────────────────────────────────────────

def get_fcm_access_token():
    """Get OAuth2 access token for FCM v1 API using service account"""
    try:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        credentials.refresh(google_auth_request())
        return credentials.token
    except FileNotFoundError:
        print(f"[FCM] firebase-service-account.json not found at: {SERVICE_ACCOUNT_FILE}")
        return None
    except Exception as e:
        print(f"[FCM] Failed to get access token: {e}")
        return None

def google_auth_request():
    """Create a google-auth compatible request object"""
    import google.auth.transport.requests
    return google.auth.transport.requests.Request()

# ── Mock user store (replace with real DB in Django) ──────────────────────────
MOCK_USERS = {
    "test@example.com": {
        "username": "ganesh",
        "password": "gs22",
        "full_name": "Ganesh Saravanan",
        "business_name": "ACha Farm",
        "urls": {
            "Test Page 1": "https://promoting-formal-ten-defense.trycloudflare.com/test1",  # Replace YOUR_PC_IP with ipconfig result
            "Test Page 2": "https://promoting-formal-ten-defense.trycloudflare.com/test2",
            "Customers": "https://microman1000.pythonanywhere.com/mobile/v1/customers",
        },
    },
    "admin@ms.com": {
        "username": "admin",
        "password": "admin123",
        "full_name": "Admin User",
        "business_name": "MS Admin",
        "urls": {
            "Admin Panel": "https://bowling-names-developments-front.trycloudflare.com/test2",
        },
    },
}
# ─────────────────────────────────────────────────────────────────────────────

# In-memory token storage (replace with database in production)
DEVICE_TOKENS = {}
# Structure: {
#   "user_id": {
#       "device_id_1": { "push_token": "...", "platform": "android", "registered_at": "..." },
#       "device_id_2": { "push_token": "...", "platform": "ios", "registered_at": "..." }
#   }
# }


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "server": "MS Flask Test Server", "time": datetime.now().isoformat()})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email_or_username = (data.get("email") or "").strip().lower()
    password          = data.get("password") or ""
    device_id         = data.get("device_id") or "unknown-device"

    # ── Validation ────────────────────────────────────────────────────────────
    if not email_or_username or not password:
        return jsonify({"success": False, "message": "Email/username and password are required."}), 400

    # Try to find user by email first, then by username
    user = MOCK_USERS.get(email_or_username)
    if not user:
        # Search by username
        for email_key, user_data in MOCK_USERS.items():
            if user_data.get("username", "").lower() == email_or_username:
                user = user_data
                break

    if not user or user["password"] != password:
        return jsonify({"success": False, "message": "Invalid email/username or password."}), 401

    # ── Build response ────────────────────────────────────────────────────────
    username         = user["full_name"]
    reversed_password = password[::-1]   # Reverse the password string

    print(f"[LOGIN] {email_or_username} | device={device_id} | reversed_pw={reversed_password}")

    return jsonify({
        "success":           True,
        "username":          username,
        "reversed_password": reversed_password,
        "device_id":         device_id,
        "business_name":     user.get("business_name", "MS"),
        "fallback_url":      "http://10.0.2.2:5000",  # Fallback API endpoint
        "message":           f"Welcome back, {username}!",
        "urls":              user["urls"],
    })


@app.route("/api/device/register", methods=["POST"])
def register_device():
    """
    Register device push token for notifications.
    IMPORTANT: One device_id can only belong to ONE user at a time.
    If device_id exists under a different user, it will be MOVED to the new user.
    """
    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    user_id = data.get("user_id")
    push_token = data.get("push_token")
    platform = data.get("platform", "unknown")

    if not device_id or not user_id or not push_token:
        return jsonify({
            "success": False,
            "message": "Missing required fields: device_id, user_id, push_token"
        }), 400

    current_time = datetime.now().isoformat()
    is_update = False
    moved_from_user = None

    # Step 1: Check if this device_id exists under ANY user
    for existing_user_id, devices in list(DEVICE_TOKENS.items()):
        if device_id in devices:
            if existing_user_id == user_id:
                # Same user, same device - just UPDATE
                is_update = True
            else:
                # Different user! MOVE device from old user to new user
                moved_from_user = existing_user_id
                print(f"\n{'='*60}")
                print(f"[DEVICE] 🔄 Device Reassignment Detected!")
                print(f"  Device: {device_id}")
                print(f"  Moving from user: {existing_user_id}")
                print(f"  Moving to user: {user_id}")
                print(f"{'='*60}\n")

                # Remove from old user
                del DEVICE_TOKENS[existing_user_id][device_id]

                # If old user has no more devices, remove user entry
                if not DEVICE_TOKENS[existing_user_id]:
                    del DEVICE_TOKENS[existing_user_id]
            break

    # Step 2: Add/Update device under the current user
    if user_id not in DEVICE_TOKENS:
        DEVICE_TOKENS[user_id] = {}

    if is_update:
        # Update existing device (same user)
        existing = DEVICE_TOKENS[user_id][device_id]
        DEVICE_TOKENS[user_id][device_id] = {
            "push_token": push_token,
            "platform": platform,
            "registered_at": existing.get("registered_at", current_time),
            "last_login": current_time,
        }
        action = "UPDATED"
    else:
        # New registration (either brand new device, or moved from another user)
        DEVICE_TOKENS[user_id][device_id] = {
            "push_token": push_token,
            "platform": platform,
            "registered_at": current_time,
            "last_login": current_time,
        }
        action = "MOVED" if moved_from_user else "REGISTERED"

    # Step 3: Log the action
    print(f"\n{'='*60}")
    if action == "MOVED":
        print(f"[DEVICE] 🔄 Device MOVED!")
        print(f"  Previous user: {moved_from_user}")
        print(f"  New user: {user_id}")
    elif action == "UPDATED":
        print(f"[DEVICE] 🔄 Device UPDATED!")
        print(f"  User: {user_id}")
    else:
        print(f"[DEVICE] ✅ Device REGISTERED!")
        print(f"  User: {user_id}")

    print(f"  Device: {device_id}")
    print(f"  Platform: {platform}")
    print(f"  Token: {push_token[:40]}...")

    if action == "UPDATED":
        print(f"  First registered: {DEVICE_TOKENS[user_id][device_id]['registered_at']}")
    print(f"  Last login: {current_time}")
    print(f"[DEVICE] Total: {len(DEVICE_TOKENS)} users, {sum(len(devices) for devices in DEVICE_TOKENS.values())} devices")
    print(f"{'='*60}\n")

    return jsonify({
        "success": True,
        "message": f"Device {action.lower()} successfully",
        "device_id": device_id,
        "action": action,
        "moved_from_user": moved_from_user,
    })


@app.route("/api/device/unregister", methods=["POST"])
def unregister_device():
    """
    Unregister device push token (called on logout).
    Removes the device from DEVICE_TOKENS storage.
    """
    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    user_id = data.get("user_id")

    if not device_id or not user_id:
        return jsonify({
            "success": False,
            "message": "Missing required fields: device_id, user_id"
        }), 400

    # Remove token
    if user_id in DEVICE_TOKENS and device_id in DEVICE_TOKENS[user_id]:
        del DEVICE_TOKENS[user_id][device_id]

        # If user has no more devices, remove user entry
        if not DEVICE_TOKENS[user_id]:
            del DEVICE_TOKENS[user_id]

        print(f"\n{'='*60}")
        print(f"[DEVICE] 🚪 Device Unregistered (Logout)")
        print(f"  User: {user_id}")
        print(f"  Device: {device_id}")
        print(f"[DEVICE] Remaining: {len(DEVICE_TOKENS)} users, {sum(len(devices) for devices in DEVICE_TOKENS.values())} devices")
        print(f"{'='*60}\n")

        return jsonify({
            "success": True,
            "message": "Device unregistered successfully"
        })
    else:
        print(f"[DEVICE] ⚠️ Unregister attempt for non-existent device: {device_id} (user: {user_id})")
        return jsonify({
            "success": False,
            "message": "Device not found"
        }), 404


@app.route("/api/device/list", methods=["GET"])
def list_devices():
    """Debug endpoint - List all registered devices"""
    devices_list = []
    for user_id, devices in DEVICE_TOKENS.items():
        for device_id, info in devices.items():
            devices_list.append({
                "user_id": user_id,
                "device_id": device_id,
                "platform": info["platform"],
                "registered_at": info.get("registered_at", "N/A"),
                "last_login": info.get("last_login", "N/A"),
                "token_preview": info["push_token"][:40] + "..."
            })

    return jsonify({
        "total_users": len(DEVICE_TOKENS),
        "total_devices": sum(len(devices) for devices in DEVICE_TOKENS.values()),
        "devices": devices_list
    })


@app.route("/api/notifications/send", methods=["POST"])
def send_notification():
    """
    Send push notification to users.
    Body: {
        "target": "all" | "user" | "device",
        "user_id": "email@example.com",  # if target=user
        "device_id": "...",              # if target=device
        "title": "Notification Title",
        "body": "Notification message",
        "data": { "screen": "home", ... }  # optional
    }
    """
    data = request.get_json(silent=True) or {}
    target = data.get("target", "all")
    title = data.get("title", "MS App")
    body = data.get("body", "You have a new notification")
    notification_data = data.get("data", {})

    tokens_to_send = []

    # Collect tokens based on target
    if target == "all":
        # Send to all registered devices
        for user_devices in DEVICE_TOKENS.values():
            for device_info in user_devices.values():
                tokens_to_send.append(device_info["push_token"])

    elif target == "user":
        user_id = data.get("user_id")
        if not user_id or user_id not in DEVICE_TOKENS:
            return jsonify({"success": False, "message": f"User '{user_id}' not found"}), 404

        # Send to all devices of this user
        for device_info in DEVICE_TOKENS[user_id].values():
            tokens_to_send.append(device_info["push_token"])

    elif target == "device":
        device_id = data.get("device_id")
        user_id = data.get("user_id")

        if not device_id or not user_id:
            return jsonify({"success": False, "message": "device_id and user_id required for device target"}), 400

        if user_id not in DEVICE_TOKENS or device_id not in DEVICE_TOKENS[user_id]:
            return jsonify({"success": False, "message": "Device not found"}), 404

        tokens_to_send.append(DEVICE_TOKENS[user_id][device_id]["push_token"])

    else:
        return jsonify({"success": False, "message": f"Invalid target: {target}"}), 400

    # Send notifications via Firebase Cloud Messaging (FCM)
    if not tokens_to_send:
        return jsonify({"success": False, "message": "No devices to send to"}), 400

    sent_count, failed_count = send_fcm_notifications(tokens_to_send, title, body, notification_data)

    print(f"[NOTIFICATIONS] Sent {sent_count} notifications, {failed_count} failed")

    return jsonify({
        "success": True,
        "sent": sent_count,
        "failed": failed_count,
        "total_tokens": len(tokens_to_send)
    })


def send_fcm_notifications(tokens, title, body, data):
    """
    Send push notifications via Firebase Cloud Messaging v1 API
    Returns: (sent_count, failed_count)
    """
    access_token = get_fcm_access_token()
    if not access_token:
        print("[FCM] Failed to get access token. Check firebase-service-account.json")
        return 0, len(tokens)

    sent = 0
    failed = 0
    url = f"https://fcm.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/messages:send"

    for token in tokens:
        try:
            payload = {
                "message": {
                    "token": token,
                    "notification": {
                        "title": title,
                        "body": body,
                    },
                    "android": {
                        "notification": {
                            "sound": "default",
                        }
                    },
                    "data": {k: str(v) for k, v in (data or {}).items()},
                }
            }

            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code == 200:
                sent += 1
                print(f"[FCM] Sent to {token[:30]}...")
            else:
                failed += 1
                error_msg = response.json().get("error", {}).get("message", response.text[:100])
                print(f"[FCM] Failed for {token[:30]}... Error: {error_msg}")

        except Exception as e:
            failed += 1
            print(f"[FCM] Exception: {e}")

    return sent, failed


# ==================== SYNC ENDPOINTS ====================

# Store synced data (in-memory for testing)
SYNCED_DATA = {}  # Format: { user_id: { contacts: [...], last_sync: "timestamp" } }

@app.route("/api/synced", methods=["GET"])
def list_synced_data():
    """Debug endpoint - List all synced data"""
    return jsonify(SYNCED_DATA)

@app.route("/api/sync/data", methods=["POST"])
def sync_data():
    """
    Sync contacts from mobile device.
    Body: {
        "user_id": "email@example.com",
        "device_id": "...",
        "timestamp": "ISO timestamp",
        "contacts": [{ "id": "", "name": "", "phone_numbers": [], "emails": [] }]
    }
    """
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    device_id = data.get("device_id")
    contacts = data.get("contacts", [])
    timestamp = data.get("timestamp", "")

    if not user_id or not device_id:
        return jsonify({
            "success": False,
            "message": "user_id and device_id are required"
        }), 400

    # Store synced data
    SYNCED_DATA[user_id] = {
        "device_id": device_id,
        "contacts": contacts,
        "last_sync": timestamp,
        "contact_count": len(contacts),
    }

    print(f"\n{'='*60}")
    print(f"📊 DATA SYNC RECEIVED")
    print(f"{'='*60}")
    print(f"User ID: {user_id}")
    print(f"Device ID: {device_id}")
    print(f"Timestamp: {timestamp}")
    print(f"Contacts: {len(contacts)} items")

    # Print first 3 contacts as sample
    if contacts:
        print(f"\nSample Contacts:")
        for i, contact in enumerate(contacts[:3]):
            print(f"  {i+1}. {contact.get('name', 'Unknown')} - {', '.join(contact.get('phone_numbers', []))}")

    print(f"{'='*60}\n")

    return jsonify({
        "success": True,
        "message": "Data synced successfully",
        "synced_contacts": len(contacts)
    })


@app.route("/api/sync/status", methods=["GET"])
def sync_status():
    """Get sync status for a user"""
    user_id = request.args.get("user_id")

    if not user_id:
        return jsonify({
            "success": False,
            "message": "user_id parameter required"
        }), 400

    sync_data = SYNCED_DATA.get(user_id)

    if not sync_data:
        return jsonify({
            "success": True,
            "synced": False,
            "message": "No sync data found for this user"
        })

    return jsonify({
        "success": True,
        "synced": True,
        "device_id": sync_data.get("device_id"),
        "last_sync": sync_data.get("last_sync"),
        "contact_count": sync_data.get("contact_count", 0)
    })


# ==================== AUDIT LOG ENDPOINTS ====================

# Store audit logs (in-memory for testing)
AUDIT_LOGS = []  # Format: [{ user_id, device_id, event_type, timestamp, metadata }]


@app.route("/api/audit/log", methods=["POST"])
def audit_log():
    """
    Receive and store audit log with comprehensive device metadata.
    Body: {
        "user_id": "email@example.com",
        "device_id": "...",
        "event_type": "login" | "logout" | "action",
        "timestamp": "ISO timestamp",
        "metadata": {
            "location": { latitude, longitude, is_gps, is_approximate, ... },
            "device": { brand, model, os_version, memory, battery, ... },
            "network": { type, ip_address, wifi_ssid, ... },
            "sim": { sim_count, cards: [...] },
            "system": { platform, storage, ... }
        }
    }
    """
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    device_id = data.get("device_id")
    event_type = data.get("event_type", "unknown")
    timestamp = data.get("timestamp", "")
    metadata = data.get("metadata", {})

    if not user_id or not device_id:
        return jsonify({
            "success": False,
            "message": "user_id and device_id are required"
        }), 400

    # Store audit log
    audit_entry = {
        "user_id": user_id,
        "device_id": device_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "metadata": metadata,
        "server_received_at": datetime.now().isoformat(),
    }

    AUDIT_LOGS.append(audit_entry)

    # Print detailed audit log
    print(f"\n{'='*80}")
    print(f"🔍 AUDIT LOG - {event_type.upper()}")
    print(f"{'='*80}")
    print(f"User ID: {user_id}")
    print(f"Device ID: {device_id}")
    print(f"Event: {event_type}")
    print(f"Timestamp: {timestamp}")
    print(f"{'='*80}")

    # Location info
    if metadata.get("location"):
        loc = metadata["location"]
        loc_type = "GPS" if loc.get("is_gps") else "Approximate"
        print(f"\n📍 LOCATION ({loc_type}):")
        print(f"  Coordinates: {loc.get('latitude', 'N/A')}, {loc.get('longitude', 'N/A')}")
        print(f"  Accuracy: {loc.get('accuracy', 'N/A')}m")
        if loc.get('altitude'):
            print(f"  Altitude: {loc.get('altitude')}m")
        if loc.get('speed'):
            print(f"  Speed: {loc.get('speed')}m/s")
    else:
        print(f"\n📍 LOCATION: Not available")

    # Device info
    if metadata.get("device"):
        dev = metadata["device"]
        print(f"\n📱 DEVICE:")
        print(f"  Brand: {dev.get('brand', 'N/A')}")
        print(f"  Model: {dev.get('model_name', 'N/A')}")
        print(f"  Device ID: {dev.get('device_id', 'N/A')}")
        print(f"  OS: {dev.get('system_name', 'N/A')} {dev.get('system_version', 'N/A')}")
        print(f"  Memory: {dev.get('used_memory', 0) / 1e9:.2f}GB / {dev.get('total_memory', 0) / 1e9:.2f}GB")
        print(f"  Battery: {int(dev.get('battery_level', 0) * 100)}% {'(Charging)' if dev.get('is_charging') else ''}")
        print(f"  Screen: {dev.get('screen_width', 0)}x{dev.get('screen_height', 0)}")
        print(f"  Carrier: {dev.get('carrier', 'N/A')}")
        print(f"  Timezone: {dev.get('timezone', 'N/A')}")
        print(f"  Is Emulator: {dev.get('is_emulator', False)}")

    # Network info
    if metadata.get("network"):
        net = metadata["network"]
        print(f"\n🌐 NETWORK:")
        print(f"  Type: {net.get('type', 'N/A')}")
        print(f"  IP Address: {net.get('ip_address', 'N/A')}")
        if net.get('wifi_ssid'):
            print(f"  WiFi SSID: {net.get('wifi_ssid')}")
        print(f"  Connected: {net.get('is_connected', False)}")
        print(f"  Internet: {net.get('is_internet_reachable', False)}")

    # SIM info
    if metadata.get("sim"):
        sim = metadata["sim"]
        print(f"\n📞 SIM CARDS: {sim.get('sim_count', 0)} detected")
        if sim.get('cards'):
            for i, card in enumerate(sim['cards'], 1):
                print(f"  SIM {i}:")
                print(f"    Carrier: {card.get('carrier_name', 'Unknown')}")
                if card.get('phone_number'):
                    print(f"    Number: {card.get('phone_number')}")
                if card.get('country_code'):
                    print(f"    Country: {card.get('country_code')}")
                print(f"    Roaming: {card.get('is_network_roaming', False)}")

    # System info
    if metadata.get("system"):
        sys = metadata["system"]
        print(f"\n💾 SYSTEM:")
        print(f"  Platform: {sys.get('platform', 'N/A')} v{sys.get('platform_version', 'N/A')}")
        print(f"  Storage: {sys.get('free_disk_storage', 0) / 1e9:.2f}GB free / {sys.get('total_disk_capacity', 0) / 1e9:.2f}GB total")
        print(f"  Physical Device: {sys.get('is_physical_device', False)}")

    print(f"{'='*80}\n")

    return jsonify({
        "success": True,
        "message": "Audit log recorded successfully",
        "log_id": len(AUDIT_LOGS)
    })


@app.route("/api/audit/logs", methods=["GET"])
def get_audit_logs():
    """Get audit logs for a user (optional user_id parameter)"""
    user_id = request.args.get("user_id")

    if user_id:
        # Filter logs for specific user
        user_logs = [log for log in AUDIT_LOGS if log["user_id"] == user_id]
        return jsonify({
            "success": True,
            "total_logs": len(user_logs),
            "logs": user_logs
        })
    else:
        # Return all logs
        return jsonify({
            "success": True,
            "total_logs": len(AUDIT_LOGS),
            "logs": AUDIT_LOGS
        })


@app.route("/test1", methods=["GET"])
def test1():
    """Test page 1 — full featured HTML to test WebView bridges"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test Page 1</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #f5f5f5; }
            h1 { color: #1a1a2e; }
            button {
                padding: 12px 24px; margin: 8px 4px; border: none;
                border-radius: 8px; background: #1a1a2e; color: white;
                font-size: 16px; cursor: pointer;
            }
            button:active { background: #333; }
            .section {
                background: white; padding: 16px; margin: 16px 0;
                border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            a { color: #4a90e2; text-decoration: none; font-size: 18px; display: block; margin: 8px 0; }
            #preview { max-width: 100%; margin-top: 12px; border-radius: 8px; }
        </style>
    </head>
    <body>
        <h1>🧪 WebView Test Page</h1>

        <div class="section">
            <h3>🔗 Deep Links</h3>
            <a href="tel:+1234567890">📞 Click to Call</a>
            <a href="sms:+1234567890">💬 Send SMS</a>
            <a href="https://wa.me/1234567890">📱 WhatsApp</a>
            <a href="upi://pay?pa=test@upi&pn=TestName&am=10">💰 UPI Payment</a>
        </div>

        <div class="section">
            <h3>📍 Geolocation</h3>
            <button onclick="getLocation()">Get My Location</button>
            <p id="location"></p>
        </div>

        <div class="section">
            <h3>📸 Camera</h3>
            <button onclick="takePhoto()">Take Photo</button>
            <button onclick="recordVideo()">Record Video</button>
            <p id="camera-result"></p>
            <img id="preview" style="display:none;" />
        </div>

        <div class="section">
            <h3>🔔 Notification</h3>
            <button onclick="showNotification()">Show Notification</button>
        </div>

        <div class="section">
            <h3>📄 File Picker</h3>
            <button onclick="pickFile()">Pick File</button>
            <p id="file-result"></p>
        </div>

        <div class="section">
            <h3>📱 Live QR Code Generator</h3>
            <input type="text" id="qr-input" placeholder="Enter text or URL"
                   style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 12px;">
            <button onclick="generateQR()">Generate QR Code</button>
            <button onclick="generateDeviceQR()">Generate Device ID QR</button>
            <button onclick="generateTimestampQR()">Generate Timestamp QR</button>
            <div id="qr-display" style="text-align: center; margin-top: 16px;"></div>
        </div>

        <div class="section">
            <h3>📷 Live QR Code Scanner</h3>
            <button id="start-scan-btn" onclick="startScanner()">Start Scanner</button>
            <button id="stop-scan-btn" onclick="stopScanner()" style="display:none; background: #d32f2f;">Stop Scanner</button>
            <div id="scanner-container" style="display:none; margin-top: 12px;">
                <div id="reader" style="width: 100%; max-width: 500px; margin: 0 auto; border: 2px solid #1a1a2e; border-radius: 8px;"></div>
            </div>
            <div id="scan-result" style="margin-top: 16px; padding: 12px; background: #e8f5e9; border-radius: 8px; display: none;">
                <h4 style="margin: 0 0 8px 0; color: #2e7d32;">✅ Scanned Successfully!</h4>
                <p id="scan-result-text" style="margin: 0; color: #1b5e20; word-break: break-all; font-family: monospace;"></p>
                <button onclick="copyScanResult()" style="margin-top: 8px; background: #4caf50;">Copy Result</button>
            </div>
        </div>

        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <script>
            function getLocation() {
                navigator.geolocation.getCurrentPosition(
                    pos => {
                        document.getElementById('location').innerHTML =
                            `Lat: ${pos.coords.latitude}<br>Lng: ${pos.coords.longitude}`;
                    },
                    err => alert('Location error: ' + err.message)
                );
            }

            function takePhoto() {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'image/*';
                input.capture = 'camera';

                input.onchange = (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        const reader = new FileReader();
                        reader.onload = (event) => {
                            const preview = document.getElementById('preview');
                            preview.src = event.target.result;
                            preview.style.display = 'block';
                            document.getElementById('camera-result').innerHTML =
                                `Photo captured: ${file.name} (${(file.size/1024).toFixed(2)} KB)`;
                        };
                        reader.readAsDataURL(file);
                    }
                };

                input.click();
            }

            function recordVideo() {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'video/*';
                input.capture = 'camcorder';

                input.onchange = (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        document.getElementById('camera-result').innerHTML =
                            `Video recorded: ${file.name} (${(file.size/1024/1024).toFixed(2)} MB)`;
                        document.getElementById('preview').style.display = 'none';
                    }
                };

                input.click();
            }

            function showNotification() {
                new Notification('Test Notification', {
                    body: 'This is a test notification from WebView!'
                });
            }

            function pickFile() {
                window.ReactNativeWebView.postMessage(JSON.stringify({
                    type: 'OPEN_FILE_PICKER',
                    accept: '*/*'
                }));

                document.addEventListener('ms_file_picked', (e) => {
                    document.getElementById('file-result').innerHTML =
                        `File: ${e.detail.name}<br>Size: ${e.detail.size} bytes`;
                }, { once: true });
            }

            // QR Code Functions
            function generateQR() {
                const text = document.getElementById('qr-input').value;
                if (!text) {
                    alert('Please enter text or URL');
                    return;
                }
                displayQRCode(text);
            }

            function generateDeviceQR() {
                const deviceId = 'DEVICE-' + Math.random().toString(36).substr(2, 9).toUpperCase();
                document.getElementById('qr-input').value = deviceId;
                displayQRCode(deviceId);
            }

            function generateTimestampQR() {
                const timestamp = new Date().toISOString();
                document.getElementById('qr-input').value = timestamp;
                displayQRCode(timestamp);
            }

            function displayQRCode(text) {
                const encodedText = encodeURIComponent(text);
                const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=${encodedText}`;

                const display = document.getElementById('qr-display');
                display.innerHTML = `
                    <img src="${qrUrl}" alt="QR Code" style="max-width: 100%; height: auto; border: 2px solid #ddd; border-radius: 8px; margin-top: 12px;">
                    <p style="margin-top: 12px; color: #666; font-size: 14px; word-break: break-all;">${text}</p>
                `;
            }

            // QR Scanner Functions
            let html5QrcodeScanner = null;

            function startScanner() {
                document.getElementById('scanner-container').style.display = 'block';
                document.getElementById('start-scan-btn').style.display = 'none';
                document.getElementById('stop-scan-btn').style.display = 'inline-block';
                document.getElementById('scan-result').style.display = 'none';

                html5QrcodeScanner = new Html5QrcodeScanner(
                    "reader",
                    {
                        fps: 10,
                        qrbox: { width: 250, height: 250 },
                        aspectRatio: 1.0
                    },
                    false
                );

                html5QrcodeScanner.render(onScanSuccess, onScanError);
            }

            function stopScanner() {
                if (html5QrcodeScanner) {
                    html5QrcodeScanner.clear();
                    html5QrcodeScanner = null;
                }
                document.getElementById('scanner-container').style.display = 'none';
                document.getElementById('start-scan-btn').style.display = 'inline-block';
                document.getElementById('stop-scan-btn').style.display = 'none';
            }

            function onScanSuccess(decodedText, decodedResult) {
                console.log(`QR Code scanned: ${decodedText}`, decodedResult);

                // Display result
                document.getElementById('scan-result').style.display = 'block';
                document.getElementById('scan-result-text').textContent = decodedText;

                // Vibrate if supported
                if (navigator.vibrate) {
                    navigator.vibrate(200);
                }

                // Auto-stop scanner after successful scan
                stopScanner();
            }

            function onScanError(error) {
                // Ignore scanning errors (they're common while scanning)
                // console.warn(`QR scan error: ${error}`);
            }

            function copyScanResult() {
                const text = document.getElementById('scan-result-text').textContent;

                // Try modern clipboard API first
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(() => {
                        alert('Copied to clipboard!');
                    }).catch(() => {
                        fallbackCopy(text);
                    });
                } else {
                    fallbackCopy(text);
                }
            }

            function fallbackCopy(text) {
                // Fallback for older browsers
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                try {
                    document.execCommand('copy');
                    alert('Copied to clipboard!');
                } catch (err) {
                    alert('Failed to copy. Text: ' + text);
                }
                document.body.removeChild(textarea);
            }
        </script>
    </body>
    </html>
    """


@app.route("/test2", methods=["GET"])
def test2():
    """Test page 2 — simple page"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test Page 2</title>
        <style>
            body {
                font-family: Arial; padding: 40px; background: #e3f2fd;
                text-align: center;
            }
            h1 { color: #1976d2; font-size: 48px; margin-bottom: 20px; }
            p { font-size: 20px; color: #555; line-height: 1.6; }
            .card {
                background: white; padding: 24px; margin: 20px auto;
                max-width: 400px; border-radius: 12px;
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }
        </style>
    </head>
    <body>
        <h1>✅ Test Page 2</h1>
        <div class="card">
            <p>WebView is working correctly!</p>
            <p>Navigation, back button, and all features are functional.</p>
            <p><strong>Current Time:</strong> <span id="time"></span></p>
        </div>
        <script>
            setInterval(() => {
                document.getElementById('time').textContent = new Date().toLocaleTimeString();
            }, 1000);
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    print("=" * 60)
    print("  SyncUp Flask Test Server")
    print("  POST /api/login                — authenticate user")
    print("  GET  /api/health               — health check")
    print("  POST /api/device/register      — register push token")
    print("  POST /api/device/unregister    — unregister device (logout)")
    print("  GET  /api/device/list          — list registered devices (DEBUG)")
    print("  POST /api/notifications/send   — send push notifications")
    print("  POST /api/sync/data            — sync contacts")
    print("  GET  /api/sync/status          — get sync status")
    print("  POST /api/audit/log            — submit audit log with metadata")
    print("  GET  /api/audit/logs           — get audit logs (optional: ?user_id=...)")
    print()
    print("  Test credentials (email OR username):")
    for email, info in MOCK_USERS.items():
        username = info.get('username', 'N/A')
        print(f"    {email} OR {username}  /  {info['password']}")
    print("=" * 60)
    app.run(debug=True, host="0.0.0.0", port=5000)
