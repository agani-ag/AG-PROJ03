@echo off
title SyncUp App - Release AAB Builder
color 0B
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo    SyncUp App ^| Release AAB Builder
echo    Play Store Upload  ^|  No Expo Account
echo  ============================================
echo.

:: ── Paths ────────────────────────────────────────────────────────────────────
set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "OUTPUT_DIR=%PROJECT_ROOT%\APK_Output"
set "KEYSTORE_FILE=%PROJECT_ROOT%\syncup-release.keystore"
set "SIGNING_PROPS=%PROJECT_ROOT%\signing.properties"


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 1 — Node.js
:: ════════════════════════════════════════════════════════════════════════════
echo [1/7] Checking Node.js...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo        Found: %%v


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 2 — Detect Java (needed for keytool + Gradle)
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [2/7] Detecting Java...

if defined JAVA_HOME (
    if exist "%JAVA_HOME%\bin\java.exe" (
        echo        Using JAVA_HOME: %JAVA_HOME%
        goto :java_ok
    )
)

:: Android Studio bundled JDK locations
for %%P in (
    "C:\Program Files\Android\Android Studio\jbr"
    "C:\Program Files\Android\Android Studio\jre"
) do (
    if exist "%%~P\bin\java.exe" (
        set "JAVA_HOME=%%~P"
        echo        Found Android Studio JDK: %%~P
        goto :java_ok
    )
)

java -version >nul 2>&1
if %errorlevel% equ 0 (
    echo        Found java in PATH
    goto :java_ok
)

echo  [ERROR] Java not found. Install Android Studio (includes JDK).
pause & exit /b 1
:java_ok
if defined JAVA_HOME set "PATH=%JAVA_HOME%\bin;%PATH%"


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 3 — Detect Android SDK
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [3/7] Detecting Android SDK...

if not defined ANDROID_HOME (
    set "ANDROID_HOME=C:\Users\%USERNAME%\AppData\Local\Android\Sdk"
)
if not exist "%ANDROID_HOME%" (
    echo  [ERROR] Android SDK not found at: %ANDROID_HOME%
    echo          Install Android Studio or set ANDROID_HOME.
    pause & exit /b 1
)
echo        Found SDK: %ANDROID_HOME%
set "PATH=%ANDROID_HOME%\platform-tools;%ANDROID_HOME%\emulator;%PATH%"


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 4 — Signing configuration
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [4/7] Reading signing configuration...

if not exist "%SIGNING_PROPS%" (
    echo.
    echo  signing.properties not found — first time setup.
    echo  This password protects your release keystore.
    echo  Use something strong and save it somewhere safe.
    echo.
    set /p "KEYSTORE_PASS=  Enter a keystore password (min 6 chars): "
    echo.

    (
        echo KEYSTORE_PASS=!KEYSTORE_PASS!
        echo KEY_ALIAS=syncup-key
        echo KEY_PASS=!KEYSTORE_PASS!
    ) > "%SIGNING_PROPS%"

    echo  Saved to signing.properties ^(this file is gitignored^)
    echo.
)

:: Read signing.properties into variables
for /f "usebackq tokens=1,* delims==" %%a in ("%SIGNING_PROPS%") do (
    set "prop_name=%%a"
    set "prop_val=%%b"
    if "!prop_name!"=="KEYSTORE_PASS" set "KEYSTORE_PASS=!prop_val!"
    if "!prop_name!"=="KEY_ALIAS"     set "KEY_ALIAS=!prop_val!"
    if "!prop_name!"=="KEY_PASS"      set "KEY_PASS=!prop_val!"
)

if "!KEYSTORE_PASS!"=="" (
    echo  [ERROR] Could not read KEYSTORE_PASS from signing.properties
    pause & exit /b 1
)
echo        Signing config loaded


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 5 — Generate keystore (one-time only)
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [5/7] Checking release keystore...

if not exist "%KEYSTORE_FILE%" (
    echo        Keystore not found — generating one-time release keystore...
    echo        ^(Keep syncup-release.keystore safe — losing it means you cannot update the app^)
    echo.

    keytool -genkey -v ^
        -keystore "%KEYSTORE_FILE%" ^
        -alias "!KEY_ALIAS!" ^
        -keyalg RSA ^
        -keysize 2048 ^
        -validity 36500 ^
        -storepass "!KEYSTORE_PASS!" ^
        -keypass "!KEY_PASS!" ^
        -dname "CN=SyncUp App, OU=Internal, O=SyncUp, L=City, S=State, C=IN" ^
        -noprompt

    if !errorlevel! neq 0 (
        echo  [ERROR] Keystore generation failed.
        pause & exit /b 1
    )
    echo.
    echo        Keystore generated: syncup-release.keystore
) else (
    echo        Found: syncup-release.keystore
)


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 6 — expo prebuild + patch signing
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [6/7] Generating native Android project...
echo.

cd /d "%PROJECT_ROOT%"
call npx expo prebuild --platform android --clean

if %errorlevel% neq 0 (
    echo  [ERROR] expo prebuild failed.
    pause & exit /b 1
)

:: Inject signing config into the generated build.gradle
echo.
echo        Patching build.gradle with signing config...
set "KEYSTORE_FILE_ENV=%KEYSTORE_FILE%"
set "KEYSTORE_PASS_ENV=%KEYSTORE_PASS%"
set "KEY_ALIAS_ENV=%KEY_ALIAS%"
set "KEY_PASS_ENV=%KEY_PASS%"

set "KEYSTORE_FILE=%KEYSTORE_FILE_ENV%"
set "KEYSTORE_PASS=%KEYSTORE_PASS_ENV%"
set "KEY_ALIAS=%KEY_ALIAS_ENV%"
set "KEY_PASS=%KEY_PASS_ENV%"

node "%PROJECT_ROOT%\scripts\patch-signing.js"

if %errorlevel% neq 0 (
    echo  [ERROR] Failed to patch signing config.
    pause & exit /b 1
)

:: Patch AndroidManifest.xml to fix Firebase notification icon merge conflict
echo.
echo        Patching AndroidManifest for notification icon...
node "%PROJECT_ROOT%\scripts\patch-notification-manifest.js"

if %errorlevel% neq 0 (
    echo  [ERROR] Failed to patch notification manifest.
    pause & exit /b 1
)


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 7 — Gradle release bundle (AAB)
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [7/7] Building Release AAB (App Bundle)...
echo        ^(First run downloads Gradle ~250MB subsequent runs are fast^)
echo.

cd /d "%PROJECT_ROOT%\android"
call gradlew.bat bundleRelease --no-daemon

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Gradle build failed. See output above.
    cd /d "%PROJECT_ROOT%"
    pause & exit /b 1
)

cd /d "%PROJECT_ROOT%"


:: ── Copy AAB to output folder ─────────────────────────────────────────────────
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

set "AAB_SRC=%PROJECT_ROOT%\android\app\build\outputs\bundle\release\app-release.aab"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "DT=%%i"
set "AAB_OUT=%OUTPUT_DIR%\SyncUp-release-%DT%.aab"

copy "%AAB_SRC%" "%AAB_OUT%" >nul

if %errorlevel% neq 0 (
    echo  [ERROR] AAB copy failed. Source: %AAB_SRC%
    pause & exit /b 1
)


:: ════════════════════════════════════════════════════════════════════════════
:: DONE
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo  ============================================
echo    RELEASE AAB READY
echo  ============================================
echo.
echo  File : %AAB_OUT%
echo  Type : Release AAB ^(signed, Play Store ready^)
echo.
echo  Upload to Play Store:
echo    1. Go to https://play.google.com/console
echo    2. Select your app ^> Production ^> Create new release
echo    3. Upload the .aab file
echo.
echo  Opening output folder...
explorer "%OUTPUT_DIR%"

echo  Closing in 5 seconds...
timeout /t 5 /nobreak >nul
exit
