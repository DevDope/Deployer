@echo off
cd /d "%~dp0"

set "DJANGO_SETTINGS_MODULE=proyecto_suit.tunnel_settings"
set "DEBUG=False"
set "WORKSTATION_URL=http://192.168.0.107:9090/infer"
echo Workstation IA: %WORKSTATION_URL%
if not defined SITE_ORIGIN set "SITE_ORIGIN=https://tpsm.suitmx.com"
if not defined PDF_RENDER_ORIGIN set "PDF_RENDER_ORIGIN=http://127.0.0.1:8105"

set "PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=D:\TSPM\Sistema\SUIT_prod\SUIT_prod\venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo No se encontro Python. Crea venv local o revisa el venv productivo.
  pause
  exit /b 1
)

if not exist "%~dp0proyecto_suit\settings.py" (
  echo Falta proyecto_suit\settings.py. Copialo desde produccion antes de iniciar.
  pause
  exit /b 1
)

mkdir media 2>nul
mkdir staticfiles 2>nul
mkdir logs 2>nul

powershell -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort 8015 -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
  echo El puerto 8015 ya esta ocupado. No se detuvo ningun proceso.
  pause
  exit /b 1
)

"%PYTHON%" manage.py check || goto :error
"%PYTHON%" manage.py migrate procesamiento --noinput || goto :migration_error
"%PYTHON%" manage.py test || goto :error
echo Actualizando archivos estaticos nuevos o modificados...
"%PYTHON%" manage.py collectstatic --noinput || goto :static_error

echo.
echo Levantando TPSM tunnel en http://127.0.0.1:8015
if "%DEPLOYER_MANAGED%"=="1" goto :managed_waitress
echo Logs: %~dp0logs\waitress-out.log y %~dp0logs\waitress-error.log
echo.
"%PYTHON%" -m waitress --listen=127.0.0.1:8015 --trusted-proxy=127.0.0.1 --trusted-proxy-headers=x-forwarded-proto proyecto_suit.wsgi:application 1>>"%~dp0logs\waitress-out.log" 2>>"%~dp0logs\waitress-error.log"
exit /b %errorlevel%

:managed_waitress
echo Logs: salida administrada por Deployer.
"%PYTHON%" -m waitress --listen=127.0.0.1:8015 --trusted-proxy=127.0.0.1 --trusted-proxy-headers=x-forwarded-proto proyecto_suit.wsgi:application
exit /b %errorlevel%

:static_error
echo.
echo No se pudieron actualizar los archivos estaticos. El servicio no se inicio.
echo Revisa el error anterior y los permisos de escritura de STATIC_ROOT.
pause
exit /b 1

:migration_error
echo.
echo No se pudieron aplicar las migraciones. Revisa el error de PostgreSQL.
echo Si falta una columna, la conexion debe ser propietaria de la tabla para crearla.
echo Si ya ejecutaste el SQL, revisa la definicion de la columna y el error anterior.
pause
exit /b 1

:error
echo.
echo Fallo la validacion. Revisa el mensaje anterior.
pause
exit /b 1
