$ErrorActionPreference = 'Stop'
$workers = @()
$exitCode = 1
$logs = $env:TPSM_LOG_DIR

function Test-Port([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connection = $client.ConnectAsync('127.0.0.1', $Port)
        return $connection.Wait(500) -and $client.Connected
    } catch { return $false }
    finally { $client.Dispose() }
}

function Start-Worker($Name, $Executable, $Arguments, $Directory) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs ($Name + '.stdout.log')) `
        -RedirectStandardError (Join-Path $logs ($Name + '.stderr.log'))
    Write-Host ('[PROCESO] ' + $Name + ': PID ' + $process.Id)
    return [pscustomobject]@{ Name = $Name; Process = $process }
}

function Get-LanguageToolOwner([int]$OwnerId) {
    $owner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $OwnerId) -ErrorAction Stop
    if (-not $owner -or $owner.Name -notmatch '^javaw?\.exe$' -or
        $owner.CommandLine -notmatch 'org\.languagetool\.server\.HTTPServer' -or
        $owner.CommandLine -notmatch '(?i)(?:^|[\\/\s"])languagetool-server\.jar(?:["\s]|$)') {
        throw ('El puerto de LanguageTool pertenece a otro proceso, PID ' + $OwnerId + '. No se detuvo.')
    }
    $process = Get-Process -Id $OwnerId -ErrorAction Stop
    $null = $process.Handle
    return $process
}

try {
    $python = $env:PROD_PYTHON
    $waitress = $env:PROD_WAITRESS
    $tunnelBat = Join-Path $env:TUNNEL_DIR 'levantar_local.bat'
    $apache = Join-Path $env:XAMPP_APACHE 'bin\httpd.exe'
    $apacheConf = Join-Path $env:XAMPP_APACHE 'conf\httpd.conf'
    foreach ($file in @($python, $waitress, $tunnelBat, $apache, $apacheConf)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('No existe: ' + $file) }
    }
    foreach ($directory in @($env:PROD_DIR, $env:TUNNEL_DIR)) {
        $views = Join-Path $directory 'procesamiento\views.py'
        if (Test-Path -LiteralPath $views) {
            $source = [IO.File]::ReadAllText($views)
            if ($source -match '(?m)^LANGUAGETOOL_URL\s*=\s*["'']http://127\.0\.0\.1:85/v2/check["'']\s*$' -and $env:LT_PORT -ne '85') {
                throw ('Actualiza LANGUAGETOOL_URL para leer la variable de entorno en ' + $views + '. Apache conserva el puerto 85.')
            }
        }
    }
    foreach ($port in @([int]$env:PROD_PORT, [int]$env:TUNNEL_PORT)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw ('Puerto ' + $port + ' ocupado. Deten la instancia anterior antes de iniciar TPSM.')
        }
    }
    $ltJar = Join-Path $env:LT_DIR 'languagetool-server.jar'
    $ltAvailable = Test-Path -LiteralPath $ltJar -PathType Leaf
    $ltOwners = @()
    if ($ltAvailable) {
        $java = (Get-Command java.exe -ErrorAction Stop).Source
        $ltOwners = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$env:LT_PORT) -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ownerId in $ltOwners) {
            $checked = Get-LanguageToolOwner $ownerId
            $checked.Dispose()
        }
    }
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    if (-not (Test-Port ([int]$env:APACHE_PORT))) {
        & $apache -d $env:XAMPP_APACHE -f $apacheConf -t
        if ($LASTEXITCODE -ne 0) { throw 'Apache tiene errores de configuracion.' }
        if (-not (Get-Process httpd -ErrorAction SilentlyContinue)) {
            # Apache es compartido: WMI lo inicia fuera del arbol administrado de TPSM.
            $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
            $command = '"' + $apache + '" -d "' + $env:XAMPP_APACHE + '" -f "' + $apacheConf + '"'
            $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
                CommandLine = $command
                CurrentDirectory = (Split-Path $apache)
                ProcessStartupInformation = $startup
            }
            if ($created.ReturnValue -ne 0) { throw ('No se pudo iniciar Apache: ' + $created.ReturnValue) }
        }
        $deadline = (Get-Date).AddSeconds(15)
        while (-not (Test-Port ([int]$env:APACHE_PORT)) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
        if (-not (Test-Port ([int]$env:APACHE_PORT))) {
            throw 'Apache no escucha en 8105. Revisa Listen y el VirtualHost del tunel; no se cambio su configuracion.'
        }
    }
    $client = New-Object Net.Sockets.TcpClient
    try {
        $url = [uri]$env:WORKSTATION_URL
        $connection = $client.ConnectAsync($url.Host, $url.Port)
        if (-not $connection.Wait(4000) -or -not $client.Connected) { throw 'Sin conexion' }
        Write-Output '[OK] Workstation accesible.'
    } catch { Write-Output '[WARN] Workstation no accesible. TPSM continuara sin confirmar disponibilidad de IA.' }
    finally { $client.Dispose() }

    $env:DJANGO_SETTINGS_MODULE = 'proyecto_suit.settings'
    Push-Location $env:PROD_DIR
    try {
        foreach ($arguments in @(@('check'), @('migrate', 'procesamiento', '--noinput'), @('collectstatic', '--noinput'))) {
            & $python manage.py @arguments
            if ($LASTEXITCODE -ne 0) { throw ('Fallo PROD: manage.py ' + ($arguments -join ' ')) }
        }
    } finally { Pop-Location }
    $workers += Start-Worker 'prod' $waitress @('--host=127.0.0.1', ('--port=' + $env:PROD_PORT), '--threads=16', '--max-request-body-size=104857600', '--channel-timeout=900', 'proyecto_suit.wsgi:application') $env:PROD_DIR
    $env:DJANGO_SETTINGS_MODULE = 'proyecto_suit.tunnel_settings'
    $workers += Start-Worker 'tunnel' $env:ComSpec @('/d', '/c', ('call "' + $tunnelBat + '"')) $env:TUNNEL_DIR
    if ($ltAvailable) {
        foreach ($ownerId in $ltOwners) {
            $previous = Get-LanguageToolOwner $ownerId
            Stop-Process -InputObject $previous -Force -ErrorAction Stop
            if (-not $previous.WaitForExit(5000)) { throw 'LanguageTool anterior no termino.' }
            $previous.Dispose()
            Write-Output ('[INFO] LanguageTool anterior cerrado: PID ' + $ownerId)
        }
        $workers += Start-Worker 'languagetool' $java @('-cp', 'languagetool-server.jar', 'org.languagetool.server.HTTPServer', '--port', $env:LT_PORT, '--allow-origin') $env:LT_DIR
    } else { Write-Output '[WARN] No se encontro LanguageTool. Revisa LT_DIR.' }
    Write-Output ('[LOGS] ' + $logs)
    $ready = $false
    while ($true) {
        if ($env:DEPLOYER_STOP_FILE -and (Test-Path -LiteralPath $env:DEPLOYER_STOP_FILE)) {
            Write-Output '[STOP] Cerrando los procesos propios de TPSM; Apache permanece activo.'
            $exitCode = 0
            break
        }
        foreach ($worker in $workers) {
            $worker.Process.Refresh()
            if ($worker.Process.HasExited) {
                $worker.Process.WaitForExit()
                throw ($worker.Name + ' termino con codigo ' + $worker.Process.ExitCode + '. Revisa ' + $logs)
            }
        }
        if (-not $ready -and (Test-Port ([int]$env:PROD_PORT)) -and (Test-Port ([int]$env:TUNNEL_PORT)) -and
            (-not $ltAvailable -or (Test-Port ([int]$env:LT_PORT)))) {
            $components = if ($ltAvailable) { 'PROD, tunel y LanguageTool' } else { 'PROD y tunel' }
            Write-Output ('[OK] TPSM responde en ' + $components + '. URL: ' + $env:SITE_ORIGIN)
            $ready = $true
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Output ('[ERROR] ' + $_.Exception.Message)
    $exitCode = 1
} finally {
    foreach ($worker in $workers) {
        if (-not $worker.Process.HasExited) {
            try {
                & taskkill.exe /PID $worker.Process.Id /T /F 2>&1 | Out-Null
                if (-not $worker.Process.WaitForExit(5000)) { throw 'El proceso no termino.' }
            } catch {
                Write-Output ('[ERROR] No se pudo confirmar el cierre de ' + $worker.Name)
                $exitCode = 1
            }
        }
        $worker.Process.Dispose()
    }
}
exit $exitCode
