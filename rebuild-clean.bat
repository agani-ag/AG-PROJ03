@echo off
title SyncUp App - CLEAN Rebuild with ProjectID
color 0A

echo.
echo  ============================================
echo    SyncUp App ^| CLEAN REBUILD
echo    Ensuring ProjectID is embedded properly
echo  ============================================
echo.

cd /d "%~dp0"

echo [1/3] Removing old Android build folder...
if exist "android" (
    rmdir /s /q "android"
    echo        Android folder deleted
) else (
    echo        No android folder found
)

echo.
echo [2/3] Removing node_modules\.cache to force clean prebuild...
if exist "node_modules\.cache" (
    rmdir /s /q "node_modules\.cache"
    echo        Cache cleared
)

echo.
echo [3/3] Running full build with build-android.bat...
echo.
call build-android.bat

echo.
echo ============================================
echo  CLEAN REBUILD COMPLETE
echo ============================================
pause
