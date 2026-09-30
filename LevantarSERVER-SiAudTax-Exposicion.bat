@echo off
setlocal DisableDelayedExpansion

rem SiAudTax EXPOSICION - no modifica la instancia normal 7007/7008.
set "ROOT=%~dp0.."
set "FRONTEND_DIR=%ROOT%\frontend"
set "FRONTEND_PORT=7009"
set "BACKEND_PORT=7010"
set "PYTHON_CMD=%ROOT%\.venv\Scripts\python.exe"
set "ENV_FILE=%ROOT%\deployment\environment.bat"

if not exist "%ENV_FILE%" (
  echo [ERROR] Falta %ENV_FILE%
  pause
  exit /b 1
)
call "%ENV_FILE%"
if not defined SIAUDTAX_EXHIBITION_DB set "SIAUDTAX_EXHIBITION_DB=SiAudTax2"
if /I "%SIAUDTAX_EXHIBITION_DB%"=="%POSTGRES_DB%" (
  echo [ERROR] La BD de exposicion no puede ser la BD normal: %POSTGRES_DB%
  pause
  exit /b 1
)
set "POSTGRES_DB=%SIAUDTAX_EXHIBITION_DB%"
set "SIAUDTAX_EXHIBITION_MODE=1"
call :ApplyEnv

if not exist "%PYTHON_CMD%" (
  echo [ERROR] No existe .venv. Ejecuta primero el BAT normal para preparar el entorno.
  pause
  exit /b 1
)
pushd "%FRONTEND_DIR%"
call npm ls --depth=0 --include=dev >nul 2>nul
if errorlevel 1 (
  echo Instalando dependencias frontend de desarrollo...
  call npm ci --include=dev
  if errorlevel 1 ( popd & echo [ERROR] Fallo npm ci. & pause & exit /b 1 )
)
popd

pushd "%ROOT%"
"%PYTHON_CMD%" -c "import os,psycopg2; psycopg2.connect(dbname=os.environ['POSTGRES_DB'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],host=os.environ['POSTGRES_HOST'],port=os.environ['POSTGRES_PORT']).close(); print('Conexion PostgreSQL OK')"
if errorlevel 1 ( popd & echo [ERROR] No se pudo conectar a %POSTGRES_DB%. & pause & exit /b 1 )
"%PYTHON_CMD%" manage.py migrate
if errorlevel 1 ( popd & pause & exit /b 1 )
"%PYTHON_CMD%" manage.py bootstrap_reference_data
if errorlevel 1 ( popd & pause & exit /b 1 )
"%PYTHON_CMD%" manage.py ensure_suit_user
if errorlevel 1 ( popd & pause & exit /b 1 )
"%PYTHON_CMD%" manage.py check
if errorlevel 1 ( popd & pause & exit /b 1 )
popd

pushd "%FRONTEND_DIR%"
set "VITE_API_BASE_URL=/api"
set "VITE_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%"
call npm run build
if errorlevel 1 ( popd & pause & exit /b 1 )
popd

call :ReleasePort %BACKEND_PORT%
if errorlevel 1 ( pause & exit /b 1 )
call :ReleasePort %FRONTEND_PORT%
if errorlevel 1 ( pause & exit /b 1 )

start "SiAudTax Exposicion Backend :%BACKEND_PORT%" /D "%ROOT%" cmd /k ".venv\Scripts\python.exe -m waitress --listen=127.0.0.1:%BACKEND_PORT% siaudtax_backend.wsgi:application"
start "SiAudTax Exposicion Frontend :%FRONTEND_PORT%" /D "%FRONTEND_DIR%" cmd /k "set VITE_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%&& npm run preview -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"

echo Exposicion iniciada: http://127.0.0.1:%FRONTEND_PORT% via Apache :90
echo Backend: http://127.0.0.1:%BACKEND_PORT%
echo BD: %POSTGRES_DB%
pause
exit /b 0

:ApplyEnv
set "POSTGRES_DB=%POSTGRES_DB%"
set "POSTGRES_USER=%POSTGRES_USER%"
set "POSTGRES_PASSWORD=%POSTGRES_PASSWORD%"
set "POSTGRES_HOST=%POSTGRES_HOST%"
set "POSTGRES_PORT=%POSTGRES_PORT%"
set "DJANGO_ALLOWED_HOSTS=%DJANGO_ALLOWED_HOSTS%"
set "DJANGO_CSRF_TRUSTED_ORIGINS=%DJANGO_CSRF_TRUSTED_ORIGINS%"
set "DJANGO_DEBUG=%DJANGO_DEBUG%"
set "DJANGO_SECRET_KEY=%DJANGO_SECRET_KEY%"
set "SIAUDTAX_SUIT_EMAIL=%SIAUDTAX_SUIT_EMAIL%"
set "SIAUDTAX_SUIT_PASSWORD=%SIAUDTAX_SUIT_PASSWORD%"
set "SIAUDTAX_EXHIBITION_MODE=%SIAUDTAX_EXHIBITION_MODE%"
set "SIAUDTAX_EXHIBITION_DB=%SIAUDTAX_EXHIBITION_DB%"
exit /b 0

:ReleasePort
powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=%1; $owners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); foreach($ownerId in $owners){ $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$ownerId); if($p.CommandLine -notmatch 'SiAudTax|vite|waitress|node'){ Write-Host ('[ERROR] Puerto '+$port+' ocupado por proceso ajeno PID '+$ownerId); exit 1 }; Stop-Process -Id $ownerId -Force }; exit 0"
exit /b %ERRORLEVEL%
