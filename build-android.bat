@echo off
title MS App - Local Android APK Builder
color 0A
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo    MS App ^| Local Android APK Builder
echo    No Expo account required
echo  ============================================
echo.

:: ── Resolve project root (where this bat file lives) ─────────────────────────
set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "OUTPUT_DIR=%PROJECT_ROOT%\APK_Output"

:: ── Step 1: Check Node.js ─────────────────────────────────────────────────────
echo [1/6] Checking Node.js...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo        Found: %%v

:: ── Step 2: Detect Java ───────────────────────────────────────────────────────
echo.
echo [2/6] Detecting Java (JDK)...

:: Priority 1: JAVA_HOME already set
if defined JAVA_HOME (
    if exist "%JAVA_HOME%\bin\java.exe" (
        echo        Using JAVA_HOME: %JAVA_HOME%
        goto :java_found
    )
)

:: Priority 2: Android Studio bundled JRE (most common on Windows)
for %%P in (
    "C:\Program Files\Android\Android Studio\jbr"
    "C:\Program Files\Android\Android Studio\jre"
    "C:\Program Files\Android\Android Studio\jbr\bin\java.exe"
) do (
    if exist "%%~P\bin\java.exe" (
        set "JAVA_HOME=%%~P"
        echo        Found Android Studio JDK: !JAVA_HOME!
        goto :java_found
    )
)

:: Priority 3: java in PATH
java -version >nul 2>&1
if %errorlevel% equ 0 (
    echo        Found java in system PATH
    goto :java_found
)

echo  [ERROR] Java (JDK) not found.
echo          Options:
echo          A) Install JDK 17: https://adoptium.net
echo          B) Ensure Android Studio is installed (includes a bundled JDK)
pause & exit /b 1

:java_found
if defined JAVA_HOME set "PATH=%JAVA_HOME%\bin;%PATH%"

:: ── Step 3: Detect Android SDK ────────────────────────────────────────────────
echo.
echo [3/6] Detecting Android SDK...

if not defined ANDROID_HOME (
    set "ANDROID_HOME=C:\Users\%USERNAME%\AppData\Local\Android\Sdk"
)

if not exist "%ANDROID_HOME%" (
    echo  [ERROR] Android SDK not found at: %ANDROID_HOME%
    echo          Install Android Studio or set ANDROID_HOME manually.
    pause & exit /b 1
)

echo        Found SDK: %ANDROID_HOME%
set "PATH=%ANDROID_HOME%\platform-tools;%ANDROID_HOME%\emulator;%PATH%"

:: ── Step 4: Generate native Android project ───────────────────────────────────
echo.
echo [4/6] Generating native Android project (expo prebuild)...
echo        This rewrites the android\ folder from your Expo config.
echo.

cd /d "%PROJECT_ROOT%"
call npx expo prebuild --platform android --clean

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] expo prebuild failed. Check the output above.
    pause & exit /b 1
)

:: ── Step 5: Build Debug APK with Gradle ──────────────────────────────────────
echo.
echo [5/6] Building Debug APK with Gradle...
echo        (First run downloads Gradle — may take a few minutes)
echo.

cd /d "%PROJECT_ROOT%\android"

call gradlew.bat assembleDebug --no-daemon

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Gradle build failed. Check the output above.
    cd /d "%PROJECT_ROOT%"
    pause & exit /b 1
)

cd /d "%PROJECT_ROOT%"

:: ── Step 6: Copy APK to output folder ────────────────────────────────────────
echo.
echo [6/6] Copying APK to output folder...

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

set "APK_SOURCE=%PROJECT_ROOT%\android\app\build\outputs\apk\debug\app-debug.apk"

:: Stamp filename with date+time
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set "DATE_STR=%%c%%b%%a"
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set "TIME_STR=%%a%%b"
set "TIME_STR=%TIME_STR: =0%"
set "STAMPED_APK=%OUTPUT_DIR%\MS-debug-%DATE_STR%-%TIME_STR%.apk"

copy "%APK_SOURCE%" "%STAMPED_APK%" >nul

if %errorlevel% neq 0 (
    echo  [ERROR] Could not copy APK. Check if the build actually produced a file:
    echo          %APK_SOURCE%
    pause & exit /b 1
)

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo  ============================================
echo    BUILD COMPLETE
echo  ============================================
echo.
echo  APK saved to:
echo  %STAMPED_APK%
echo.
echo  To install on Android device:
echo    1. Connect device via USB  (or transfer the APK file)
echo    2. On the device: Settings ^> Install unknown apps ^> Allow
echo    3. Tap the APK file to install
echo.
echo  Opening output folder...
explorer "%OUTPUT_DIR%"
echo.
pause
