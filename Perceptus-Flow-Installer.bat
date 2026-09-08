@echo off
title Perceptus Flow - Installer
setlocal enabledelayedexpansion

echo.
echo   PERCEPTUS FLOW - one click setup
echo   --------------------------------
echo   This makes PerceptusFlow.exe on your Desktop.
echo   Leave it running, it can take 5-10 minutes the first time.
echo.

set "WORK=%LOCALAPPDATA%\PerceptusFlow"
if not exist "%WORK%" mkdir "%WORK%"
cd /d "%WORK%"

where python >nul 2>nul
if errorlevel 1 (
  echo [1/5] Installing Python...
  winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
  set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"
) else (
  echo [1/5] Python found.
)

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python could not be installed automatically.
  echo   Please install it from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

echo [2/5] Getting the app...
curl -L -o perceptus_flow.py https://raw.githubusercontent.com/NeelAniGamer/perceptus-flow/main/perceptus_flow.py
if not exist perceptus_flow.py (
  echo   Download failed - check your internet connection.
  pause
  exit /b 1
)

echo [3/5] Installing the pieces it needs...
python -m pip install --upgrade pip >nul
python -m pip install pyinstaller pyautogui pillow pystray pygetwindow requests SpeechRecognition keyboard opencv-python mediapipe pyttsx3 pyaudio

echo [4/5] Building PerceptusFlow.exe ...
python -m PyInstaller --noconfirm --onefile --windowed --name PerceptusFlow ^
  --collect-all mediapipe --collect-all pyttsx3 ^
  --hidden-import comtypes --hidden-import win32com.client ^
  --hidden-import pyttsx3.drivers --hidden-import pyttsx3.drivers.sapi5 ^
  perceptus_flow.py

if not exist "dist\PerceptusFlow.exe" (
  echo   Build failed. Copy the messages above and send them over.
  pause
  exit /b 1
)

echo [5/5] Putting it on your Desktop...
copy /y "dist\PerceptusFlow.exe" "%USERPROFILE%\Desktop\PerceptusFlow.exe" >nul

echo.
echo   Done. PerceptusFlow.exe is on your Desktop.
echo   Double-click it, then hold Ctrl+Space and talk.
echo.
start "" "%USERPROFILE%\Desktop\PerceptusFlow.exe"
pause
