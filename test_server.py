"""
MS App - Flask Test Server
Run: pip install flask flask-cors
     python test_server.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Allow requests from React Native / WebView

# ── Mock user store (replace with real DB in Django) ──────────────────────────
MOCK_USERS = {
    "test@example.com": {
        "password": "password123",
        "full_name": "Ganesh Saravanan",
        "urls": {
            "Print Portal":  "http://microman2000.pythonanywhere.com/print",
            "Reports":       "http://microman2000.pythonanywhere.com/reports",
            "Dashboard":     "http://microman2000.pythonanywhere.com/dashboard",
        },
    },
    "admin@ms.com": {
        "password": "admin123",
        "full_name": "Admin User",
        "urls": {
            "Admin Panel": "http://microman2000.pythonanywhere.com/admin",
        },
    },
}
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "server": "MS Flask Test Server", "time": datetime.now().isoformat()})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email     = (data.get("email") or "").strip().lower()
    password  = data.get("password") or ""
    device_id = data.get("device_id") or "unknown-device"

    # ── Validation ────────────────────────────────────────────────────────────
    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required."}), 400

    user = MOCK_USERS.get(email)

    if not user or user["password"] != password:
        return jsonify({"success": False, "message": "Invalid email or password."}), 401

    # ── Build response ────────────────────────────────────────────────────────
    username         = user["full_name"]
    reversed_password = password[::-1]   # Reverse the password string

    print(f"[LOGIN] {email} | device={device_id} | reversed_pw={reversed_password}")

    return jsonify({
        "success":           True,
        "username":          username,
        "reversed_password": reversed_password,
        "device_id":         device_id,
        "message":           f"Welcome back, {username}!",
        "urls":              user["urls"],
    })


@app.route("/api/device/register", methods=["POST"])
def register_device():
    """
    Placeholder — will store device_id + FCM token for push notifications.
    Wire up when Firebase is ready.
    """
    data      = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    fcm_token = data.get("fcm_token")

    print(f"[DEVICE] registered device_id={device_id}  fcm_token={fcm_token}")

    return jsonify({
        "success":  True,
        "message":  "Device registered (stub — Firebase not yet wired).",
        "device_id": device_id,
    })


if __name__ == "__main__":
    print("=" * 55)
    print("  MS Flask Test Server")
    print("  POST /api/login          — authenticate user")
    print("  GET  /api/health         — health check")
    print("  POST /api/device/register — register FCM token (stub)")
    print()
    print("  Test credentials:")
    for email, info in MOCK_USERS.items():
        print(f"    {email}  /  {info['password']}")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5000)
