@echo off
echo ========================================
echo Setting up Expo Project for Push Notifications
echo ========================================
echo.

REM Make sure logged in
echo Step 1: Verifying Expo login...
call npx expo whoami
if errorlevel 1 (
    echo Please login to Expo first:
    call npx expo login
)

echo.
echo Step 2: Initializing EAS project...
call eas project:init

echo.
echo ========================================
echo Setup Complete!
echo.
echo The projectId has been added to app.json
echo Now rebuild your app using build-android.bat
echo ========================================
pause
