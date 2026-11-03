import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deployer_panel as panel


class TpsmLauncherCheck(unittest.TestCase):
    def test_detects_all_ports_and_cooperative_stop(self):
        launcher = Path(__file__).with_name("launchers") / "LevantarPROD-TPSM.bat"
        detected = panel.inspect_bat(launcher)
        self.assertEqual(detected["ports"], [85, 7003, 8015, 8105])
        self.assertEqual(detected["url"], "https://tpsm.grupo-asicce.com")
        self.assertTrue(detected["cooperative_stop"])

    def test_stop_closes_owned_workers_without_stopping_apache(self):
        supervisor = Path(__file__).with_name("launchers") / "Start-TPSM-Managed.ps1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "conf").mkdir()
            for relative in ("levantar_local.bat", "bin/httpd.exe", "conf/httpd.conf", "languagetool-server.jar"):
                (root / relative).touch()
            (root / "manage.py").write_text('import sys\nprint("FAKE-MANAGE", *sys.argv[1:])\n', encoding="utf-8")
            stop = root / "requested.stop"
            stop.touch()
            env = os.environ.copy()
            env.update(PROD_DIR=str(root), TUNNEL_DIR=str(root), XAMPP_APACHE=str(root), LT_DIR=str(root),
                       PROD_PYTHON=sys.executable, PROD_WAITRESS=sys.executable,
                       PROD_PORT="7003", TUNNEL_PORT="8015", APACHE_PORT="8105", LT_PORT="85",
                       WORKSTATION_URL="http://127.0.0.1:9090/infer", SITE_ORIGIN="https://tpsm.grupo-asicce.com",
                       TPSM_LOG_DIR=str(root / "logs"), DEPLOYER_STOP_FILE=str(stop))
            driver = root / "test.ps1"
            driver.write_text(r'''
$global:launched = 0
$global:closed = @()
function Get-NetTCPConnection { return @() }
function New-Object {
    param($TypeName)
    if ($TypeName -ne 'Net.Sockets.TcpClient') { throw 'Unexpected object' }
    $client = [pscustomobject]@{ Connected = $true }
    $client | Add-Member ScriptMethod Dispose {}
    $client | Add-Member ScriptMethod ConnectAsync {
        $task = [pscustomobject]@{}
        $task | Add-Member ScriptMethod Wait { return $true }
        return $task
    }
    return $client
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,
          [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($WindowStyle -ne 'Hidden' -or -not $RedirectStandardOutput -or -not $RedirectStandardError) { throw 'Worker is not hidden/logged' }
    if ($FilePath -like '*httpd*') { throw 'Apache must not be a managed worker' }
    if ($global:launched -eq 1 -and $env:SITE_ORIGIN -ne 'https://tpsm.grupo-asicce.com') { throw 'Wrong tunnel origin' }
    $global:launched++
    $process = [pscustomobject]@{ Id = 100 + $global:launched; HasExited = ($env:TPSM_TEST_FAIL -eq '1' -and $global:launched -eq 1); ExitCode = 9 }
    $process | Add-Member ScriptMethod Dispose {}
    $process | Add-Member ScriptMethod WaitForExit { return $true }
    $process | Add-Member ScriptMethod Refresh {}
    return $process
}
function taskkill.exe {
    $global:closed += $args[1]
    if (($args -join ' ') -notmatch '^/PID 10[123] /T /F$') { throw 'Wrong process tree' }
}
''' + "\n& '" + str(supervisor).replace("'", "''") + "'\n" + r'''
$code = $LASTEXITCODE
$expected = if ($env:TPSM_TEST_FAIL -eq '1') { 2 } else { 3 }
if ($global:launched -ne 3 -or $global:closed.Count -ne $expected) { throw 'Missing worker cleanup' }
exit $code
''', encoding="utf-8")
            result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                                    capture_output=True, text=True, timeout=20, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.count("FAKE-MANAGE"), 3, result.stdout)
            self.assertIn("Apache permanece activo", result.stdout)
            stop.unlink()
            env["TPSM_TEST_FAIL"] = "1"
            failed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                                    capture_output=True, text=True, timeout=20, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("prod termino con codigo 9", failed.stdout)

    def test_panel_stop_signals_supervisor(self):
        from types import SimpleNamespace
        app = {"task": "TPSM", "name": "TPSM", "cooperative_stop": True}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(panel, "LOG_DIR", Path(directory)), patch.object(panel, "task_state", side_effect=["RUNNING", "READY"]), patch.object(panel, "ps", return_value=SimpleNamespace(returncode=0, stdout="0")):
                result = panel.ServiceCard._stop(SimpleNamespace(app=app))
                self.assertTrue((Path(directory) / "TPSM.stop").exists())
                self.assertTrue(result.startswith("[OK]"))


if __name__ == "__main__":
    unittest.main()
