@echo off
chcp 65001 > nul
title DTMF IVR — Instalador de dependencias

echo.
echo ==========================================
echo   DTMF IVR AUTOMATOR — SETUP
echo   Instalador de dependencias
echo ==========================================
echo.

:: Verificar Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado en el sistema.
    echo         Instala Python 3.11 desde https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detectado

:: Verificar version minima (3.10+)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" > nul 2>&1
if errorlevel 1 (
    echo [ADVERTENCIA] Se recomienda Python 3.10 o superior.
    echo               Version actual: %PYVER%
    echo.
)

echo.
echo [1/3] Actualizando pip...
python -m pip install --upgrade pip --quiet
echo       pip actualizado.

echo.
echo [2/3] Instalando dependencias desde requirements.txt...
echo       Esto puede tardar unos minutos...
echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Hubo un problema instalando algunas dependencias.
    echo         Revisa los errores de arriba y vuelve a intentar.
    pause
    exit /b 1
)

echo.
echo [3/3] Verificando instalacion...
python -c "
import sys
ok = True
libs = [
    ('flask',           'Flask'),
    ('flask_socketio',  'Flask-SocketIO'),
    ('eventlet',        'eventlet'),
    ('openpyxl',        'openpyxl'),
    ('pygame',          'pygame'),
    ('numpy',           'numpy'),
    ('scipy',           'scipy'),
    ('sounddevice',     'sounddevice'),
    ('soundfile',       'soundfile'),
    ('selenium',        'selenium'),
    ('PIL',             'Pillow'),
    ('requests',        'requests'),
    ('webdriver_manager','webdriver-manager'),
]
for mod, name in libs:
    try:
        m = __import__(mod)
        ver = getattr(m, '__version__', 'ok')
        print(f'  [OK] {name:<25} {ver}')
    except ImportError:
        print(f'  [FALTA] {name}')
        ok = False

print()
if ok:
    print('  Todas las dependencias instaladas correctamente.')
else:
    print('  ADVERTENCIA: Algunas dependencias no se instalaron.')
    sys.exit(1)
"
if errorlevel 1 (
    echo.
    echo [!] Algunas librerias pueden necesitar instalacion manual.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   INSTALACION COMPLETADA
echo   Puedes ejecutar: iniciar_ivr.bat
echo ==========================================
echo.
pause
