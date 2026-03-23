# 🔔 Push Notifications Guide

## Features

✅ **Broadcast** — Send to all users
✅ **Targeted** — Send to specific user
✅ **Device-specific** — Send to specific device
✅ **Auto-registration** — Tokens registered on login
✅ **No Firebase setup needed** — Uses Expo Push Service

---

## How It Works

1. **User logs in** → App gets Expo Push Token → Sends to backend
2. **Backend stores:** `{user_id: {device_id: token}}`
3. **Backend sends notification** → Expo Push Service → User's device
4. **App displays notification** (foreground/background/quit)

---

## Testing (Development Mode)

### **Step 1: Build & Install on Physical Device**

**Notifications only work on physical devices (not emulator)**

```bash
# Option A: Build APK
build-android.bat

# Option B: Run on connected device
adb devices
npx expo start
# Press 'a' to install on device
```

### **Step 2: Login on Device**

- Open app on physical device
- Login with `ganesh` / `password123`
- Check Flask server logs — should see:
  ```
  [DEVICE] Registered: user=ganesh, device=..., platform=android
  [DEVICE] Token storage: 1 users, 1 devices
  ```

### **Step 3: Send Test Notification**

Open a new terminal and run:

```bash
# Send to all users
python send_notification.py --all "Hello!" "This is a test notification"

# Send to specific user
python send_notification.py --user "test@example.com" "Hi Ganesh" "You have a new message"

#Send with custom data (for navigation)
python send_notification.py --all "Alert" "Check this out" --data '{"screen":"home","id":123}'
```

---

## API Endpoints

### **1. Register Device Token**

```http
POST /api/device/register
Content-Type: application/json

{
  "device_id": "unique-device-id",
  "user_id": "user@example.com",
  "push_token": "ExponentPushToken[...]",
  "platform": "android"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Device registered successfully",
  "device_id": "..."
}
```

---

### **2. Send Notification**

```http
POST /api/notifications/send
Content-Type: application/json

{
  "target": "all" | "user" | "device",
  "title": "Notification Title",
  "body": "Notification message",
  "data": { "screen": "home", "id": 123 }
}
```

**Target Options:**

#### **Broadcast to all users:**
```json
{
  "target": "all",
  "title": "Server Maintenance",
  "body": "Scheduled downtime at 2 AM"
}
```

#### **Send to specific user:**
```json
{
  "target": "user",
  "user_id": "test@example.com",
  "title": "New Order",
  "body": "Order #12345 has been delivered"
}
```

#### **Send to specific device:**
```json
{
  "target": "device",
  "user_id": "test@example.com",
  "device_id": "abc123",
  "title": "Security Alert",
  "body": "New login from Windows PC"
}
```

**Response:**
```json
{
  "success": true,
  "sent": 5,
  "failed": 0,
  "total_tokens": 5
}
```

---

## Production Deployment

### **Option 1: Expo Push Service (Current Setup)**

- ✅ **No Firebase needed**
- ✅ Works out of the box
- ✅ Free tier: 600 notifications/hour
- ⚠️ Requires Expo SDK (can't use bare React Native)

### **Option 2: Firebase Cloud Messaging**

If you need:
- More than 600 notifications/hour
- Full control over delivery
- Bare React Native support

**Switch to FCM:**
1. Create Firebase project
2. Download `google-services.json`
3. Replace `expo-notifications` with `@react-native-firebase/messaging`
4. Update backend to use FCM Admin SDK instead of Expo Push API

---

## Handling Notifications in App

**Notification is received:**
- **Foreground** — Shows banner at top (dismisses after 4s)
- **Background** — Shows in system notification tray
- **Quit** — Shows in system notification tray

**User taps notification:**
- App opens
- `onNotificationTap` handler fires
- Navigate based on `data.screen`

---

## Token Storage (Production)

**Current:** In-memory dict (resets on server restart)

**Upgrade for production:**

```python
# Store in database
CREATE TABLE device_tokens (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255),
    device_id VARCHAR(255) UNIQUE,
    push_token TEXT,
    platform VARCHAR(20),
    registered_at TIMESTAMP,
    last_active TIMESTAMP
);
```

---

## Troubleshooting

### **"Must use physical device"**
- Notifications don't work on emulator
- Build APK and install on real Android device

### **No token registered**
- Check Flask logs after login
- Make sure `registerForPushNotifications` is called
- Check device permissions: Settings → Apps → MS → Notifications

### **Notification not received**
- Check token storage: Flask logs show device count
- Verify Expo Push Token format: `ExponentPushToken[...]`
- Test with: `python send_notification.py --all "Test" "Message"`

### **Permission denied**
- App prompts for notification permission on first launch
- If denied: Settings → Apps → MS → Permissions → Notifications → Allow

---

## Next Steps

1. **Test on physical device** — Build APK and install
2. **Send test notifications** — Use `send_notification.py`
3. **Wire up navigation** — Handle `data.screen` in notification tap
4. **Move to production** — Store tokens in database
5. **Admin dashboard** — Build UI to send notifications from Django

---

## Quick Reference

```bash
# Start server
python test_server.py

# Send to all
python send_notification.py --all "Title" "Message"

# Send to user
python send_notification.py --user "email@example.com" "Title" "Message"

# Send with data
python send_notification.py --all "Alert" "Check this" --data '{"screen":"home"}'
```
