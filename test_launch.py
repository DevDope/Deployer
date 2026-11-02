import tempfile
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import deployer_panel as panel


class LaunchCheck(unittest.TestCase):
    def test_bat_changes_refresh_detected_values_but_keep_manual_values(self):
        with tempfile.TemporaryDirectory() as directory:
            bat = Path(directory) / "start.bat"
            bat.write_text('rem --port 9999\nset "FRONTEND_PORT=7004"\nset "PUBLIC_URL=https://demo.example"\nset "WORKSTATION_URL=http://192.168.0.103:9090"\ncall npm run preview -- --port %FRONTEND_PORT%\n', encoding="utf-8")
            app = {"bat": str(bat), "port_mode": "auto", "url_mode": "auto"}
            self.assertTrue(panel.sync_bat(app))
            self.assertEqual(app["ports"], [7004])
            self.assertEqual(app["url"], "https://demo.example")
            self.assertFalse(panel.sync_bat(app))
            bat.write_text('python -m waitress --listen=127.0.0.1:8015 app:wsgi\n', encoding="utf-8")
            self.assertTrue(panel.sync_bat(app))
            self.assertEqual(app["ports"], [8015])
            self.assertEqual(app["url"], "")
            app.update(port_mode="manual", url_mode="manual", ports=[8888], url="https://custom.example")
            bat.write_text('node app --port 9000\n', encoding="utf-8")
            panel.sync_bat(app)
            self.assertEqual(app["ports"], [8888])
            self.assertEqual(app["url"], "https://custom.example")

    def test_add_service_and_recheck_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            bat = Path(directory) / "start.bat"
            bat.write_text('set "PORT=7004"\nset "PUBLIC_URL=https://demo.example"\n', encoding="utf-8")
            config = {"cloudflared_service": "cloudflared", "apps": []}
            with patch.object(panel, "load_config", return_value=config), patch.object(panel, "save_config"), patch.object(panel.App, "refresh"), patch.object(panel.App, "refresh_log"), patch.object(panel.App, "after"):
                ui = panel.App()
                try:
                    self.assertEqual(list(ui.cards), [])
                    dialog = panel.ServiceDialog(ui)
                    dialog.fields["name"].insert(0, "Demo")
                    with patch.object(panel.filedialog, "askopenfilename", return_value=str(bat)):
                        dialog.pick("bat")
                    self.assertEqual(dialog.fields["ports"].get(), "7004")
                    self.assertEqual(dialog.fields["url"].get(), "https://demo.example")
                    dialog.save()
                    self.assertEqual(config["apps"][0]["cwd"], str(bat.parent))
                    self.assertEqual(config["apps"][0]["port_mode"], "auto")
                    bat.write_text('set "PORT=7010"\nset "PUBLIC_URL=https://new.example"\n', encoding="utf-8")
                    card = ui.cards["demo"]
                    with patch.object(panel, "task_exists", return_value=True), patch.object(card, "_run", return_value="requested"):
                        self.assertEqual(card._start(), "requested")
                    self.assertEqual(card.app["ports"], [7010])
                    self.assertEqual(card.app["url"], "https://new.example")
                    bat.write_text('set "PORT=7020"\n', encoding="utf-8")
                    reopened = panel.App()
                    try:
                        self.assertEqual(reopened.config_data["apps"][0]["ports"], [7020])
                    finally:
                        reopened.destroy()
                finally:
                    ui.destroy()

    def test_running_task_is_not_reported_as_ready_without_ports(self):
        self.assertEqual(panel.service_status({"state": "RUNNING", "statuses": [(7004, False)]}, [7004])[0], "En ejecucion")
        self.assertEqual(panel.service_status({"state": "RUNNING", "statuses": [(7004, True)]}, [7004])[0], "Puertos activos")

    def test_desktop_controls_and_cloud_selection(self):
        with patch.object(panel.App, "refresh"), patch.object(panel.App, "refresh_log"):
            ui = panel.App()
            try:
                for size in ("1260x780", "960x640"):
                    ui.geometry(size)
                    ui.update()
                    for frame in (ui.actions, ui.secondary):
                        for button in frame.winfo_children():
                            self.assertGreater(button.winfo_width(), 30)
                            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), ui.winfo_rootx() + ui.winfo_width())
                ui.service_list.selection_set("__cloud__")
                ui.select_service()
                self.assertEqual(ui.detail_title.cget("text"), "Cloudflared")
                self.assertEqual(len(ui.actions.winfo_children()), 3)
                self.assertEqual(len(ui.service_list.get_children()), len(ui.config_data["apps"]) + 1)
            finally:
                ui.destroy()

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
