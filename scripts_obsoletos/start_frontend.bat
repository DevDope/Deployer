@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

rem ===========================================================
rem  SCRIPT: start_frontend.bat
rem  OBJETIVO: Iniciar frontend FODA en puerto interno 7004 sin HMR
rem  APACHE: publicar externamente en https://foda.suitmx.com/ via proxy
rem ===========================================================

echo ============================
echo Iniciando Frontend FODA
echo ============================

rem ====== AJUSTES ======
set "FRONTEND_DIR=%~dp0"
set "FRONTEND_PORT=7004"
set "PUBLIC_URL=https://foda.suitmx.com/"
set "VITE_API_BASE_URL=/foda-api"

call :kill_port %FRONTEND_PORT%

rem ====== COMPROBACIONES ======
where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm no esta disponible en PATH.
  goto end
)

if not exist node_modules (
  echo Instalando dependencias...
  call npm install
  if errorlevel 1 (
    echo Error instalando dependencias.
    goto end
  )
)

rem ====== INICIAR FRONTEND ======
echo Compilando frontend...
call npm run build
if errorlevel 1 (
  echo [ERROR] Error compilando frontend.
  goto end
)

echo Iniciando Vite preview en puerto interno %FRONTEND_PORT%...
echo Apache debera publicar este servicio en %PUBLIC_URL%.
echo.
echo URL interna: http://127.0.0.1:%FRONTEND_PORT%/
echo URL publica esperada: %PUBLIC_URL%
echo Backend proxy: %VITE_API_BASE_URL% -^> http://192.168.0.103:86
echo.

call npm run preview -- --host 127.0.0.1 --port %FRONTEND_PORT%

:end
pause
exit /b

:kill_port
set "TARGET_PORT=%~1"
echo Cerrando procesos previos en puerto %TARGET_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %TARGET_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 } | ForEach-Object { Write-Host ('- Cerrando PID ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul
exit /b
