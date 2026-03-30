@echo off
title MS App - Release APK Builder
color 0A
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo    MS App ^| Release APK Builder
echo    Internal Distribution  ^|  No Expo Account
echo  ============================================
echo.

:: ── Paths ────────────────────────────────────────────────────────────────────
set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "OUTPUT_DIR=%PROJECT_ROOT%\APK_Output"
set "KEYSTORE_FILE=%PROJECT_ROOT%\ms-release.keystore"
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
        echo KEY_ALIAS=ms-key
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
    echo        ^(Keep ms-release.keystore safe — losing it means you cannot update the app^)
    echo.

    keytool -genkey -v ^
        -keystore "%KEYSTORE_FILE%" ^
        -alias "!KEY_ALIAS!" ^
        -keyalg RSA ^
        -keysize 2048 ^
        -validity 36500 ^
        -storepass "!KEYSTORE_PASS!" ^
        -keypass "!KEY_PASS!" ^
        -dname "CN=MS App, OU=Internal, O=MS, L=City, S=State, C=IN" ^
        -noprompt

    if !errorlevel! neq 0 (
        echo  [ERROR] Keystore generation failed.
        pause & exit /b 1
    )
    echo.
    echo        Keystore generated: ms-release.keystore
) else (
    echo        Found: ms-release.keystore
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


:: ════════════════════════════════════════════════════════════════════════════
:: STEP 7 — Gradle release build
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo [7/7] Building Release APK...
echo        ^(First run downloads Gradle ~250MB subsequent runs are fast^)
echo.

cd /d "%PROJECT_ROOT%\android"
call gradlew.bat assembleRelease --no-daemon

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Gradle build failed. See output above.
    cd /d "%PROJECT_ROOT%"
    pause & exit /b 1
)

cd /d "%PROJECT_ROOT%"


:: ── Copy APK to output folder ─────────────────────────────────────────────────
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

set "APK_SRC=%PROJECT_ROOT%\android\app\build\outputs\apk\release\app-release.apk"

for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set "D=%%c%%a%%b"
for /f "tokens=1-2 delims=: " %%a in ('time /t') do (
    set "T=%%a%%b"
    set "T=!T: =0!"
)
set "APK_OUT=%OUTPUT_DIR%\MS-release-%D%-%T%.apk"

copy "%APK_SRC%" "%APK_OUT%" >nul

if %errorlevel% neq 0 (
    echo  [ERROR] APK copy failed. Source: %APK_SRC%
    pause & exit /b 1
)


:: ════════════════════════════════════════════════════════════════════════════
:: DONE
:: ════════════════════════════════════════════════════════════════════════════
echo.
echo  ============================================
echo    RELEASE APK READY
echo  ============================================
echo.
echo  File : %APK_OUT%
echo  Type : Release ^(signed, production-ready^)
echo.
echo  Install on device:
echo    Option A ^(USB^) : adb install "%APK_OUT%"
echo    Option B ^(File^): Copy APK to phone, tap to install
echo              ^(Allow "Install unknown apps" if prompted^)
echo.
echo  Opening output folder...
explorer "%OUTPUT_DIR%"
pause
