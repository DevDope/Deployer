@echo off
setlocal enabledelayedexpansion

rem ===========================================================
rem  Levantar PROD TPSM + Tunnel
rem ===========================================================

set "LOGFILE=C:\TSPM\Sistema\LevantarPROD-TPSM.log"

echo.
echo ============================
echo Iniciando Apache + PROD + Tunnel
echo ============================

rem ====== AJUSTES PROD ======
set "XAMPP_APACHE=C:\xampp\apache"
set "APACHE_BIN=%XAMPP_APACHE%\bin\httpd.exe"
set "APACHE_CONF=%XAMPP_APACHE%\conf\httpd.conf"

set "PROD_DIR=D:\TSPM\Sistema\SUIT_prod\SUIT_prod"
set "PROD_WAITRESS=%PROD_DIR%\venv\Scripts\waitress-serve.exe"
set "PROD_PORT=7003"
set "DJANGO_MODULE=proyecto_suit.wsgi:application"

rem ====== AJUSTES TUNNEL ======
set "TUNNEL_DIR=D:\TSPM\Sistema\SUIT_tunnel\SUIT_tunnel"
set "TUNNEL_BAT=%TUNNEL_DIR%\levantar_local.bat"
set "TUNNEL_PORT=8015"

rem ====== WORKSTATION RAG ======
set "WORKSTATION_URL=http://192.168.0.103:9090/infer"

rem ====== UTF-8 PARA PDF / VISTA PREVIA ======
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ====== VALIDACIONES BASICAS ======
if not exist "%APACHE_BIN%" (
    echo ERROR: No existe %APACHE_BIN%
    pause
    exit /b 1
)

if not exist "%APACHE_CONF%" (
    echo ERROR: No existe %APACHE_CONF%
    pause
    exit /b 1
)

if not exist "%PROD_WAITRESS%" (
    echo ERROR: No existe %PROD_WAITRESS%
    pause
    exit /b 1
)

rem ====== APACHE ======
tasklist /FI "IMAGENAME eq httpd.exe" | find /I "httpd.exe" >nul

if %ERRORLEVEL%==0 (
    echo Apache ya esta activo.
) else (
    echo Iniciando Apache...
    start "Apache" /D "%XAMPP_APACHE%\bin" cmd /k ""%APACHE_BIN%" -d "%XAMPP_APACHE%" -f "%APACHE_CONF%""
)

rem ====== WAITRESS PROD ======
echo Iniciando Waitress PROD en puerto %PROD_PORT%...

start "SUIT_prod" /D "%PROD_DIR%" cmd /k "set DJANGO_SETTINGS_MODULE=proyecto_suit.settings&& set WORKSTATION_URL=%WORKSTATION_URL%&& set PYTHONUTF8=1&& set PYTHONIOENCODING=utf-8&& "%PROD_WAITRESS%" --host=127.0.0.1 --port=%PROD_PORT% --threads=16 --max-request-body-size=104857600 --channel-timeout=900 %DJANGO_MODULE%"

rem ====== WAITRESS TUNNEL ======
if not exist "%TUNNEL_BAT%" (
    echo WARN: No existe %TUNNEL_BAT%
    goto languagetool
)

set "TUNNEL_PID="

echo Revisando Tunnel en puerto %TUNNEL_PORT%...

for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "$c = Get-NetTCPConnection -State Listen -LocalPort %TUNNEL_PORT% -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.OwningProcess }"`) do (
    set "TUNNEL_PID=%%P"
)

if defined TUNNEL_PID (
    echo Cerrando Tunnel activo PID %TUNNEL_PID%...
    powershell -NoProfile -Command "Stop-Process -Id %TUNNEL_PID% -Force"
    timeout /t 3 /nobreak >nul
)

echo Iniciando Tunnel en puerto %TUNNEL_PORT%...
start "SUIT_tunnel" /D "%TUNNEL_DIR%" cmd /k "set DJANGO_SETTINGS_MODULE=proyecto_suit.tunnel_settings&& set SITE_ORIGIN=https://tpsm.grupo-asicce.com&& set PDF_RENDER_ORIGIN=http://127.0.0.1:8105&& set WORKSTATION_URL=%WORKSTATION_URL%&& set PYTHONUTF8=1&& set PYTHONIOENCODING=utf-8&& call "%TUNNEL_BAT%""

:languagetool

rem ====== LANGUAGETOOL ======
set "LT_DIR=C:\LanguageTool-6.6"

tasklist /FI "IMAGENAME eq java.exe" | find /I "java.exe" >nul

if %ERRORLEVEL%==0 (
    echo LanguageTool o Java ya esta activo.
) else (
    echo Iniciando LanguageTool en puerto 85...
    start "LanguageTool" /D "%LT_DIR%" cmd /k "java -cp languagetool-server.jar org.languagetool.server.HTTPServer --port 85 --allow-origin"
)

echo.
echo Listo.
echo PROD:   http://127.0.0.1:7003
echo TUNNEL: http://127.0.0.1:8015
echo Apache: http://127.0.0.1:8105
echo URL nueva: https://tpsm.grupo-asicce.com
echo URL anterior: https://tpsm.suitmx.com
echo WORKSTATION_URL: %WORKSTATION_URL%
echo.

pause
exit /b 0