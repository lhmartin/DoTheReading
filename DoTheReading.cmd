@echo off
rem Double-click to open the DoTheReading app.
cd /d "%~dp0app"
start "" /b cmd /c "npm start"
