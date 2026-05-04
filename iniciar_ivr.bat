@echo off
title DTMF IVR Automator
echo ==========================================
echo    INICIANDO DTMF IVR AUTOMATOR
echo ==========================================
echo.

echo [1/2] Limpiando procesos en puerto 5050...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5050 ^| findstr LISTENING') do (
    echo Matando proceso PID: %%a
    taskkill /f /pid %%a >nul 2>&1
)

echo [2/2] Iniciando servidor Flask...
echo.
python dtmf_app/app.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: No se pudo iniciar la aplicacion. 
    echo Asegurate de tener instaladas las dependencias:
    echo pip install flask flask-socketio eventlet openpyxl pygame numpy scipy
    pause
)
