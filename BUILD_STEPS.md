# MS App — Android APK Build Guide
### Local Build — No Expo Account Required

---

## How the Build Works

```
expo prebuild  →  generates android\ folder  →  Gradle builds APK
```

- `expo prebuild` converts your Expo project into a native Android project
- Gradle (Android's build tool) compiles it into an `.apk` file
- Everything runs locally on your machine — no cloud, no account needed

---

## Prerequisites (one-time setup)

| Requirement | Status | Notes |
|---|---|---|
| Node.js | Already installed | v24+ |
| Android SDK | Already installed | `C:\Users\ganesh.s\AppData\Local\Android\Sdk` |
| Java (JDK) | Auto-detected | From Android Studio bundled JDK |
| ANDROID_HOME | Set via env var | BAT file sets it automatically |

---

## Before Every Build — Checklist

### 1. Switch API Endpoint to Live (Django)

File: `screens/LoginScreen.js`

```js
// CHANGE THIS before building:
const API_BASE = 'https://your-django-domain.com';

// Remove the Platform.OS check — use a single live URL
```

---

### 2. Update App Version

File: `app.json`

```json
{
  "expo": {
    "name": "MS",
    "version": "1.0.0",
    "android": {
      "package": "com.yourcompany.ms",
      "versionCode": 1
    }
  }
}
```

> Bump `version` (e.g. `1.0.1`) and `versionCode` (e.g. `2`) for each new release.

---

### 3. Replace Placeholder App Icons

| File | Size |
|---|---|
| `assets/icon.png` | 1024×1024 px |
| `assets/adaptive-icon.png` | 1024×1024 px |
| `assets/splash-icon.png` | 1242×2436 px |

---

## Running the Build

Simply double-click or run:
```
build-android.bat
```

### What the BAT file does automatically:
1. Checks Node.js is installed
2. Detects Java — checks Android Studio bundled JDK first, then system PATH
3. Detects Android SDK from `ANDROID_HOME` or default install path
4. Runs `expo prebuild --platform android --clean` — regenerates `android\` folder
5. Runs `gradlew assembleDebug` — builds the APK
6. Copies APK to `APK_Output\` folder with a date-time stamp
7. Opens the `APK_Output\` folder automatically

---

## Output

APK is saved to:
```
APK_Output\MS-debug-YYYYMMDD-HHMM.apk
```

Each build gets a unique filename so old builds are never overwritten.

---

## Installing on Android Device

**Via USB:**
1. Connect Android phone via USB
2. Enable USB Debugging on phone (Settings → Developer Options)
3. Run: `adb install APK_Output\MS-debug-*.apk`

**Via File Transfer:**
1. Copy the APK file to the phone
2. On phone: Settings → Apps → Install unknown apps → Allow
3. Tap the APK file to install

---

## Debug vs Release APK

| | Debug (current) | Release |
|---|---|---|
| Expo account needed | No | No |
| Signing | Auto (debug key) | Requires keystore |
| Install method | Direct sideload | Direct sideload / Play Store |
| Performance | Slightly slower | Optimised |
| File size | Larger | Smaller |

> Debug APK is fine for internal distribution and testing.
> For Play Store publishing, a Release APK/AAB with a keystore is required.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Java not found` | Install Android Studio (includes bundled JDK) |
| `SDK not found` | Set `ANDROID_HOME` in Windows environment variables |
| `gradlew build failed` | Run `cd android && gradlew clean` then retry |
| `expo prebuild failed` | Delete `android\` folder manually, then retry |
| APK installs but crashes | Check API endpoint URL is correct and server is live |
