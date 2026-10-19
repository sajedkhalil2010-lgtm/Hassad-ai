@echo off
REM =========================================================================
REM  HASSAD AI - one-click backend launcher
REM  Double-click this file, or run it from a terminal:
REM      start.bat            -> auto port (starts at 8000), opens browser
REM      start.bat --port 8010
REM      start.bat --no-open
REM =========================================================================
setlocal
chcp 65001 >nul
title Hassad AI - Backend

REM Always run from THIS folder, wherever the .bat was double-clicked from.
pushd "%~dp0"
set "PYTHONIOENCODING=utf-8"

REM --- Prefer the project venv if one exists, else fall back to PATH python --
set "PYLAUNCHER=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYLAUNCHER=%~dp0.venv\Scripts\python.exe"

echo.
echo   Project : %CD%
echo   Python  : %PYLAUNCHER%
echo.

"%PYLAUNCHER%" run.py %*
set "CODE=%ERRORLEVEL%"

popd

if not "%CODE%"=="0" (
    echo.
    echo   Launch failed with exit code %CODE%.
    echo   If dependencies are missing, run:
    echo       "%PYLAUNCHER%" -m pip install -r requirements.txt
    echo.
    pause
)

endlocal & exit /b %CODE%
