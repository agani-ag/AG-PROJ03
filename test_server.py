"""
MS App - Flask Test Server
Run: pip install flask flask-cors requests
     python test_server.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import requests
import json

app = Flask(__name__)
CORS(app)  # Allow requests from React Native / WebView

# ── Mock user store (replace with real DB in Django) ──────────────────────────
MOCK_USERS = {
    "test@example.com": {
        "username": "ganesh",
        "password": "password123",
        "full_name": "Ganesh Saravanan",
        "business_name": "Microman Solutions",
        "urls": {
            "Test Page 1": "http://10.0.2.2:5000/test1",
            "Test Page 2": "http://10.0.2.2:5000/test2",
        },
    },
    "admin@ms.com": {
        "username": "admin",
        "password": "admin123",
        "full_name": "Admin User",
        "business_name": "MS Admin",
        "urls": {
            "Admin Panel": "https://microman2000.pythonanywhere.com/admin",
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
    Store: user_id → device_id → {push_token, platform, timestamp}
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

    # Store token
    if user_id not in DEVICE_TOKENS:
        DEVICE_TOKENS[user_id] = {}

    DEVICE_TOKENS[user_id][device_id] = {
        "push_token": push_token,
        "platform": platform,
        "registered_at": datetime.now().isoformat(),
    }

    print(f"[DEVICE] Registered: user={user_id}, device={device_id}, platform={platform}")
    print(f"[DEVICE] Token storage: {len(DEVICE_TOKENS)} users, {sum(len(devices) for devices in DEVICE_TOKENS.values())} devices")

    return jsonify({
        "success": True,
        "message": "Device registered successfully",
        "device_id": device_id,
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

    # Send notifications via Expo Push API
    if not tokens_to_send:
        return jsonify({"success": False, "message": "No devices to send to"}), 400

    sent_count, failed_count = send_expo_push_notifications(tokens_to_send, title, body, notification_data)

    print(f"[NOTIFICATIONS] Sent {sent_count} notifications, {failed_count} failed")

    return jsonify({
        "success": True,
        "sent": sent_count,
        "failed": failed_count,
        "total_tokens": len(tokens_to_send)
    })


def send_expo_push_notifications(tokens, title, body, data):
    """
    Send push notifications via Expo Push API
    Returns: (sent_count, failed_count)
    """
    messages = []
    for token in tokens:
        messages.append({
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data,
        })

    try:
        response = requests.post(
            'https://exp.host/--/api/v2/push/send',
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            json=messages
        )

        response.raise_for_status()
        result = response.json()

        sent = sum(1 for msg in result.get('data', []) if msg.get('status') == 'ok')
        failed = len(messages) - sent

        return sent, failed

    except Exception as e:
        print(f"[NOTIFICATIONS] Error sending: {e}")
        return 0, len(messages)


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
    print("=" * 55)
    print("  MS Flask Test Server")
    print("  POST /api/login            — authenticate user")
    print("  GET  /api/health           — health check")
    print("  POST /api/device/register  — register push token")
    print("  POST /api/notifications/send — send push notifications")
    print()
    print("  Test credentials (email OR username):")
    for email, info in MOCK_USERS.items():
        username = info.get('username', 'N/A')
        print(f"    {email} OR {username}  /  {info['password']}")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5000)
