@echo off
REM =========================================================================
REM  HASSAD AI - STABLE production launcher
REM
REM  Runs Uvicorn directly against main:app on port 8000 with NO --reload,
REM  so the server never auto-restarts and never drops connections between
REM  image testing cycles.
REM
REM  Usage:  double-click this file, or run  start_stable.bat
REM
REM  To stop the server: press Ctrl+C in this window (or close the window).
REM =========================================================================
setlocal
chcp 65001 >nul
title Hassad AI - Stable Server (port 8000)

REM Always run from THIS folder, wherever the .bat was double-clicked from.
REM This also guarantees "main:app" and "agents" resolve from THIS project.
pushd "%~dp0"
set "PYTHONIOENCODING=utf-8"

REM --- Prefer the project venv if one exists, else fall back to PATH python --
set "PYLAUNCHER=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYLAUNCHER=%~dp0.venv\Scripts\python.exe"

REM --- Warn if port 8000 is already taken (avoids a confusing bind error) ----
netstat -ano -p TCP | findstr /R /C:"LISTENING" | findstr /C:":8000 " >nul
if %ERRORLEVEL%==0 (
    echo.
    echo   [!] Port 8000 is already in use.
    echo       Stop the other program using it, then run this file again.
    echo       To find it:  netstat -ano ^| findstr :8000
    echo.
    pause
    popd
    endlocal & exit /b 1
)

cls
echo.
echo   ============================================================
echo      HASSAD AI  -  stable server
echo   ============================================================
echo.
echo     Project  :  %CD%
echo     Python   :  %PYLAUNCHER%
echo     App      :  main:app
echo     Port     :  8000      (no --reload, no auto-restart)
echo.
echo   ------------------------------------------------------------
echo     OPEN THIS LINK IN YOUR BROWSER:
echo.
echo         http://127.0.0.1:8000/
echo.
echo       Test UI      :  http://127.0.0.1:8000/
echo       API docs     :  http://127.0.0.1:8000/docs
echo       Health check :  http://127.0.0.1:8000/health
echo   ------------------------------------------------------------
echo.
echo     A full analysis takes ~30-70s (real Gemini calls).
echo     Press Ctrl+C in THIS window to stop the server.
echo.

REM --- Start Uvicorn. NO --reload: the process stays up and stable. ---------
"%PYLAUNCHER%" -m uvicorn main:app --host 127.0.0.1 --port 8000
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
    echo   Uvicorn exited with code %CODE%.
    echo   If dependencies are missing, run:
    echo       "%PYLAUNCHER%" -m pip install -r requirements.txt
) else (
    echo   Server stopped.
)
echo.
pause

popd
endlocal & exit /b %CODE%
