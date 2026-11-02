@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if /i "%~1"=="service" goto :service

set "LOG_DIR=%~dp0logs"
set "MAIN_LOG=%LOG_DIR%\arranque.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
>"%MAIN_LOG%" echo [%date% %time%] Inicio del diagnostico de AUDITAXES

call :status "ETAPA 1/6 - Revisando Node.js y pnpm"

rem Usa el runtime de Codex cuando exista; en el servidor usa Node y pnpm globales.
set "CODEX_RUNTIME=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies"
if exist "%CODEX_RUNTIME%\node\bin\node.exe" (
  set "PATH=%CODEX_RUNTIME%\bin\override;%CODEX_RUNTIME%\node\bin;%CODEX_RUNTIME%\bin\fallback;%PATH%"
)

where node >>"%MAIN_LOG%" 2>&1 || (
  call :fail "No se encontro Node.js. Instala Node.js 22 o superior."
  exit /b 1
)
where pnpm >>"%MAIN_LOG%" 2>&1 || (
  call :fail "No se encontro pnpm. Ejecuta: corepack enable"
  exit /b 1
)

for /f "delims=" %%V in ('node --version') do set "NODE_VERSION=%%V"
for /f "delims=" %%V in ('pnpm --version') do set "PNPM_VERSION=%%V"
call :status "Node %NODE_VERSION% - pnpm %PNPM_VERSION%"

node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)" || (
  call :fail "AUDITAXES requiere Node.js 22 o superior. Version detectada: %NODE_VERSION%"
  exit /b 1
)

if not defined AUDITAXES_HOST set "AUDITAXES_HOST=0.0.0.0"

call :status "ETAPA 2/6 - Revisando dependencias"
if not exist "node_modules\.bin\vinext.cmd" (
  call :status "Las dependencias no estan completas. La primera instalacion puede tardar varios minutos."
  call pnpm install --frozen-lockfile --reporter=append-only
  if errorlevel 1 (
    call :fail "La instalacion de dependencias fallo."
    exit /b 1
  )
) else (
  call :status "Dependencias completas; no se instalaran otra vez."
)

call :status "ETAPA 3/6 - Construyendo la version de produccion"
call pnpm build
if errorlevel 1 (
  call :fail "La construccion de produccion fallo."
  exit /b 1
)

call :status "ETAPA 4/6 - Comprobando que los puertos esten libres"
for %%P in (4321 4322 4323) do (
  call :port_open %%P
  if not errorlevel 1 (
    echo El puerto %%P pertenece a este proceso:
    netstat -ano | findstr /R /C:":%%P .*LISTENING"
    call :fail "El puerto %%P ya esta ocupado. Cierra el proceso anterior o reinicia el servidor."
    exit /b 1
  )
)

call :status "ETAPA 5/6 - Iniciando Global, Mexico y El Salvador"
del /q "%LOG_DIR%\global-4321.log" "%LOG_DIR%\mexico-4322.log" "%LOG_DIR%\elsalvador-4323.log" >nul 2>&1
start "AUDITAXES Global" /b cmd /d /c ""%~f0" service global 4321"
start "AUDITAXES Mexico" /b cmd /d /c ""%~f0" service mexico 4322"
start "AUDITAXES El Salvador" /b cmd /d /c ""%~f0" service elsalvador 4323"

call :status "ETAPA 6/6 - Esperando respuesta de los tres servicios (maximo 45 segundos)"
call :wait_port 4321 global-4321.log
if errorlevel 1 exit /b 1
call :wait_port 4322 mexico-4322.log
if errorlevel 1 exit /b 1
call :wait_port 4323 elsalvador-4323.log
if errorlevel 1 exit /b 1

echo.
call :status "AUDITAXES ESTA LISTO"
echo   auditaxes.suitmx.com            -^> http://localhost:4321
echo   mexico-auditaxes.suitmx.com     -^> http://localhost:4322
echo   elsalvador-auditaxes.suitmx.com -^> http://localhost:4323
echo.
echo Los registros quedaron en: "%LOG_DIR%"
echo Conserva esta ventana abierta. Presiona una tecla solo para cerrarla.
pause >nul
exit /b 0

:service
set "SERVICE_NAME=%~2"
set "SERVICE_PORT=%~3"
if not defined AUDITAXES_HOST set "AUDITAXES_HOST=0.0.0.0"
if not exist "%~dp0logs" mkdir "%~dp0logs"
>>"%~dp0logs\%SERVICE_NAME%-%SERVICE_PORT%.log" echo [%date% %time%] Iniciando %SERVICE_NAME% en %AUDITAXES_HOST%:%SERVICE_PORT%
call pnpm exec vinext start --hostname %AUDITAXES_HOST% --port %SERVICE_PORT% >>"%~dp0logs\%SERVICE_NAME%-%SERVICE_PORT%.log" 2>&1
set "SERVICE_EXIT=%errorlevel%"
>>"%~dp0logs\%SERVICE_NAME%-%SERVICE_PORT%.log" echo [%date% %time%] El servicio termino con codigo %SERVICE_EXIT%
exit /b %SERVICE_EXIT%

:wait_port
set "WAIT_PORT=%~1"
set "WAIT_LOG=%~2"
set /a WAIT_COUNT=0
:wait_port_loop
call :port_open %WAIT_PORT%
if not errorlevel 1 (
  call :status "Puerto %WAIT_PORT% activo."
  exit /b 0
)
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 23 (
  echo.
  echo Ultimos mensajes de "%LOG_DIR%\%WAIT_LOG%":
  powershell -NoProfile -Command "if(Test-Path -LiteralPath '%LOG_DIR%\%WAIT_LOG%'){Get-Content -LiteralPath '%LOG_DIR%\%WAIT_LOG%' -Tail 40}else{Write-Host 'El servicio no alcanzo a crear su registro.'}"
  echo.
  call :fail "El servicio del puerto %WAIT_PORT% no respondio en 45 segundos."
  exit /b 1
)
echo   Esperando puerto %WAIT_PORT%... intento %WAIT_COUNT% de 23
timeout /t 2 /nobreak >nul
goto :wait_port_loop

:port_open
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',%~1);exit 0}catch{exit 1}finally{$c.Dispose()}" >nul 2>&1
exit /b %errorlevel%

:status
echo.
echo [%date% %time%] %~1
>>"%MAIN_LOG%" echo [%date% %time%] %~1
exit /b 0

:fail
echo.
echo ============================================================
echo ERROR: %~1
echo ============================================================
>>"%MAIN_LOG%" echo [%date% %time%] ERROR: %~1
echo Registro principal: "%MAIN_LOG%"
pause
exit /b 0
