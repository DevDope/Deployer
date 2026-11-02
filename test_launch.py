import tempfile
import json
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import deployer_panel as panel


class LaunchCheck(unittest.TestCase):
    def test_cmd_linked_scripts_are_detected_and_rechecked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            launcher = root / "iniciar-todo.cmd"
            launcher.write_text('powershell.exe -File "%~dp0scripts\\start.ps1"\n', encoding="utf-8")
            (root / "scripts/start.ps1").write_text("$env:INFERENCE_URL='http://server:4110'\n& pnpm.cmd dev\n", encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"scripts": {"dev": "node scripts/start-all.mjs"}}), encoding="utf-8")
            script = root / "scripts/start-all.mjs"
            script.write_text('const services=[{command:"pnpm start --port 4321",url:"http://localhost:4100"},{url:"http://localhost:5173"}];', encoding="utf-8")
            app = {"bat": str(launcher), "port_mode": "auto"}
            self.assertTrue(panel.sync_bat(app))
            self.assertEqual(app["ports"], [4100, 4321, 5173])
            script.write_text('const services=[{url:"http://localhost:5180"}];', encoding="utf-8")
            self.assertTrue(panel.sync_bat(app))
            self.assertEqual(app["ports"], [5180])

    def test_open_console_only_follows_logs_and_never_launches_bat(self):
        with tempfile.TemporaryDirectory() as directory:
            card = SimpleNamespace(app={"task": "Deployer_FODA", "name": "FODA", "bat": "start_frontend.bat"}, app_ui=SimpleNamespace(log=Mock()))
            with patch.object(panel, "LOG_DIR", Path(directory)), patch.object(panel.subprocess, "Popen") as launch:
                panel.ServiceCard.open_cmd(card)
            command = launch.call_args.args[0]
            self.assertEqual(command[0], "powershell")
            self.assertIn("Get-Content -LiteralPath", command[-1])
            self.assertIn("-Wait", command[-1])
            self.assertNotIn("start_frontend.bat", " ".join(command))
            self.assertTrue((Path(directory) / "Deployer_FODA.log").is_file())

    def test_project_local_bat_requires_its_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            bat = Path(directory) / "iniciar.bat"
            bat.write_text('@echo off\ncd /d "%~dp0"\ncall pnpm install\n', encoding="utf-8")
            app = {"bat": str(bat), "port_mode": "auto"}
            with self.assertRaisesRegex(FileNotFoundError, "Selecciona el BAT original"):
                panel.sync_bat(app)
            (bat.parent / "package.json").write_text('{}', encoding="utf-8")
            self.assertTrue(panel.sync_bat(app))
            bat.write_text('@echo off\ncd /d "%~dp0"\npushd "D:\\other-project"\ncall pnpm install\n', encoding="utf-8")
            self.assertFalse(panel.inspect_bat(bat)["requires_manifest"])

    def test_bat_changes_refresh_detected_values_but_keep_manual_values(self):
        with tempfile.TemporaryDirectory() as directory:
            bat = Path(directory) / "start.cmd"
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
            bat = Path(directory) / "start.cmd"
            bat.write_text('set "PORT=7004"\nset "PUBLIC_URL=https://demo.example"\n', encoding="utf-8")
            config = {"cloudflared_service": "cloudflared", "apps": []}
            with patch.object(panel, "load_config", return_value=config), patch.object(panel, "save_config"), patch.object(panel.App, "refresh"), patch.object(panel.App, "refresh_log"), patch.object(panel.App, "after"):
                ui = panel.App()
                try:
                    self.assertEqual(list(ui.cards), [])
                    dialog = panel.ServiceDialog(ui)
                    dialog.fields["name"].insert(0, "Demo")
                    secret = "test-only-inference-key-123456"
                    dialog.fields["inference_key"].insert(0, secret)
                    with patch.object(panel.filedialog, "askopenfilename", return_value=str(bat)):
                        dialog.pick("bat")
                    self.assertEqual(dialog.fields["ports"].get(), "7004")
                    self.assertEqual(dialog.fields["url"].get(), "https://demo.example")
                    dialog.save()
                    self.assertEqual(config["apps"][0]["cwd"], str(bat.parent))
                    self.assertEqual(config["apps"][0]["port_mode"], "auto")
                    protected = config["apps"][0]["inference_key_protected"]
                    self.assertNotIn(secret, json.dumps(config))
                    self.assertEqual(panel.crypt_secret(panel.base64.b64decode(protected), decrypt=True).decode(), secret)
                    editing = panel.ServiceDialog(ui, config["apps"][0])
                    self.assertEqual(editing.fields["inference_key"].get(), "")
                    editing.save()
                    self.assertEqual(config["apps"][0]["inference_key_protected"], protected)
                    bat.write_text('set "PORT=7010"\nset "PUBLIC_URL=https://new.example"\n', encoding="utf-8")
                    card = ui.cards["demo"]
                    with patch.object(panel, "create_task"), patch.object(card, "_run", return_value="requested"):
                        self.assertEqual(card._start(), "requested")
                    self.assertEqual(card.app["ports"], [7010])
                    self.assertEqual(card.app["url"], "https://new.example")
                    ui.update()
                    ui.select_service()
                    with patch.object(panel.webbrowser, "open") as open_url:
                        ui.site_link.event_generate("<Button-1>")
                        self.assertEqual(open_url.call_args.args[0], "https://new.example")
                    ui.chart.record("demo", {"cpu_percent": 4, "ram_mb": 50})
                    ui.chart.record("demo", {"cpu_percent": 8, "ram_mb": 56})
                    ui.chart.draw()
                    self.assertGreater(len(ui.chart.find_all()), 6)
                    for size in ("1260x780", "960x640"):
                        ui.geometry(size)
                        ui.update()
                        self.assertLess(ui.chart.winfo_rooty() + ui.chart.winfo_height(), ui.notebook.winfo_rooty())
                        self.assertGreater(ui.log_view.winfo_height(), 30)
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
                            if button.winfo_manager() != "pack":
                                continue
                            self.assertGreater(button.winfo_width(), 30)
                            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), ui.winfo_rootx() + ui.winfo_width())
                ui.service_list.selection_set("__cloud__")
                ui.select_service()
                self.assertEqual(ui.detail_title.cget("text"), "Cloudflared")
                self.assertEqual(sum(w.winfo_manager() == "pack" for w in ui.actions.winfo_children()), 4)
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
        self.assertIn("pythonw.exe", script)
        self.assertIn("--run-task Deployer_Check", script)
        self.assertIn("$registered=Get-ScheduledTask", script)
        self.assertIn("$registered.Actions[0].Arguments -ne $action.Arguments", script)

    def test_start_replaces_stale_task_even_when_config_claims_current_runner(self):
        app = {"task": "Deployer_FODA", "runner_version": 1, "task_dirty": False}
        events = []
        controller = SimpleNamespace(app=app, app_ui=SimpleNamespace(sync_service=Mock(), config_data={"apps": [app]}),
                                     _run=Mock(side_effect=lambda *args: events.append("run") or "requested"))
        with patch.object(panel, "save_config"), patch.object(panel, "create_task", side_effect=lambda app: events.append("register")):
            self.assertEqual(panel.ServiceCard._start(controller), "requested")
        self.assertEqual(events, ["register", "run"])
        controller._run.reset_mock()
        with patch.object(panel, "create_task", side_effect=RuntimeError("Access denied")):
            with self.assertRaisesRegex(RuntimeError, "Access denied"):
                panel.ServiceCard._start(controller)
        controller._run.assert_not_called()

    def test_hidden_runner_keeps_log_and_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat = root / "test runner.bat"
            bat.write_text('@echo off\necho runner-output\nexit /b 7\n', encoding="utf-8")
            config = {"apps": [{"bat": str(bat), "cwd": str(root), "task": "Test"}]}
            secret = "test-only-inference-key-123456"
            config["apps"][0]["inference_key_protected"] = panel.base64.b64encode(panel.crypt_secret(secret.encode())).decode()
            original_run = panel.subprocess.run
            with patch.object(panel, "load_config", return_value=config), patch.object(panel, "LOG_DIR", root), patch.object(panel.subprocess, "run", wraps=original_run) as execute:
                self.assertEqual(panel.run_task("Test"), 7)
                self.assertEqual(execute.call_args.kwargs["creationflags"], panel.subprocess.CREATE_NO_WINDOW)
                self.assertEqual(execute.call_args.kwargs["env"]["INFERENCE_API_KEY"], secret)
                self.assertNotIn(secret, str(execute.call_args.args[0]))
            output = (root / "Test.log").read_text(encoding="utf-8")
            self.assertIn("runner-output", output)
            self.assertIn("Fin: codigo 7", output)
            self.assertNotIn(secret, output)

    def test_cpu_percentage_uses_elapsed_time_and_process_identity(self):
        first = SimpleNamespace(returncode=0, stdout='{"Id":123,"Started":1,"CPU":100,"WorkingSet64":104857600}')
        second = SimpleNamespace(returncode=0, stdout='{"Id":123,"Started":1,"CPU":104,"WorkingSet64":209715200}')
        with patch.object(panel, "ps", side_effect=[first, second]), patch.object(panel.time, "monotonic", side_effect=[10, 12]), patch.object(panel.os, "cpu_count", return_value=4):
            initial = panel.process_metrics(["123"])
            latest = panel.process_metrics(["123"], initial)
        self.assertIsNone(initial["cpu_percent"])
        self.assertEqual(latest["cpu_percent"], 50)
        self.assertEqual(latest["ram_mb"], 200)

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
