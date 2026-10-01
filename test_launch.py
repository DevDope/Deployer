import tempfile
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import deployer_panel as panel


class LaunchCheck(unittest.TestCase):
    def test_live_listener_is_detected(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            listeners = panel.tcp_listeners()
            self.assertEqual(panel.port_statuses([port], listeners), [(port, True)])
            self.assertTrue(panel.port_pids([port], listeners))

    def test_listener_parsing_does_not_depend_on_windows_language(self):
        output = (
            "  TCP  127.0.0.1:7004  0.0.0.0:0  ESCUCHANDO  123\n"
            "  TCP  [::]:7010  [::]:0  LISTENING  456\n"
            "  TCP  127.0.0.1:9000  127.0.0.1:7004  ESTABLISHED  789\n"
        )
        with patch.object(panel, "run", return_value=SimpleNamespace(returncode=0, stdout=output)):
            self.assertEqual(panel.tcp_listeners(), {7004: {"123"}, 7010: {"456"}})

    def test_task_requests_elevated_interactive_user_and_quotes_paths(self):
        app = {
            "task": "Deployer_Check",
            "cwd": "managed_bats",
            "bat": "managed_bats/Check.bat",
        }
        with patch.object(panel, "ps", return_value=SimpleNamespace(returncode=0)) as execute:
            panel.create_task(app)
        script = execute.call_args.args[0]
        self.assertIn("-LogonType Interactive -RunLevel Highest", script)
        self.assertIn("-Principal $principal", script)
        self.assertIn("-WorkingDirectory", script)
        self.assertIn('call "', script)

    def test_wrapper_reports_missing_original_without_running_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(panel, "ROOT", root), patch.object(panel, "MANAGED_BATS", root):
                panel.write_wrapper("Check.bat", root / "missing original.bat")
            result = panel.run(["cmd.exe", "/d", "/c", "call", str(root / "Check.bat")])
        self.assertEqual(result.returncode, 2)
        self.assertIn("No existe el BAT original", result.stdout)


if __name__ == "__main__":
    unittest.main()
