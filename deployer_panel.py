import json
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "services.json"
LOG_DIR = ROOT / "panel_logs"
MANAGED_BATS = ROOT / "managed_bats"
ADDRESSES = ROOT / "direcciones.txt"
VERSION = "v2.4 auto-install"
LOG_DIR.mkdir(exist_ok=True)
MANAGED_BATS.mkdir(exist_ok=True)

BG = "#050807"
PANEL = "#0c1512"
PANEL_2 = "#08100e"
GREEN = "#39ff88"
CYAN = "#42d9ff"
RED = "#ff4d6d"
YELLOW = "#ffd166"
TEXT = "#d8ffe8"
MUTED = "#80a895"


def run(args, timeout=8):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, shell=False)


def ps(script, timeout=8):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)


def load_config():
    if not CONFIG.exists():
        return {"cloudflared_service": "cloudflared", "apps": []}
    with CONFIG.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with CONFIG.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def slug(text):
    value = "".join(char if char.isalnum() else "_" for char in text.upper())
    return "_".join(part for part in value.split("_") if part) or "SERVICIO"


def app_path(value):
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def task_exists(name):
    try:
        return run(["schtasks", "/Query", "/TN", name], timeout=4).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def task_state(name):
    if not task_exists(name):
        return "NO INSTALADO"
    try:
        out = run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"], timeout=4).stdout
    except subprocess.TimeoutExpired:
        return "LENTO"
    for line in out.splitlines():
        if line.lower().startswith("status:"):
            return line.split(":", 1)[1].strip().upper()
    return "INSTALADO"


def port_pids(ports):
    if not ports:
        return []
    script = (
        "$ports=@(" + ",".join(str(p) for p in ports) + ");"
        "$ids=@();"
        "foreach($port in $ports){"
        "$ids += Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique"
        "};"
        "$ids | Where-Object {$_ -gt 0} | Sort-Object -Unique"
    )
    try:
        out = ps(script, timeout=4).stdout
    except subprocess.TimeoutExpired:
        return []
    return [x.strip() for x in out.splitlines() if x.strip().isdigit()]


def port_statuses(ports):
    result = []
    for port in ports:
        try:
            ok = ps(f"if(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue){{'OK'}}", timeout=3).stdout.strip()
        except subprocess.TimeoutExpired:
            ok = ""
        result.append((port, bool(ok)))
    return result


def process_metrics(pids):
    if not pids:
        return "CPU -- | RAM --"
    script = (
        "$pids=@(" + ",".join(pids) + ");"
        "$ps=Get-Process -Id $pids -ErrorAction SilentlyContinue;"
        "$ram=($ps | Measure-Object WorkingSet64 -Sum).Sum;"
        "$cpu=($ps | Measure-Object CPU -Sum).Sum;"
        "'CPU {0:n1}s | RAM {1:n0} MB' -f $cpu, ($ram/1MB)"
    )
    try:
        return ps(script, timeout=4).stdout.strip() or "CPU -- | RAM --"
    except subprocess.TimeoutExpired:
        return "CPU -- | RAM --"


def latest_log(path):
    p = app_path(path)
    if p.drive and not Path(p.drive + "\\").exists():
        return None
    if p.is_file():
        return p
    if not p.exists():
        return None
    files = [x for x in p.rglob("*.log") if x.is_file()]
    return max(files, key=lambda x: x.stat().st_mtime) if files else None


def read_tail(paths, lines=120):
    if isinstance(paths, (str, Path)):
        paths = [paths]
    log = None
    for path in paths:
        log = latest_log(path)
        if log:
            break
    if not log:
        return None, "No encontre logs para este sistema ni en panel_logs."
    try:
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        return log, "\n".join(text)
    except OSError as exc:
        return log, f"No pude leer {log}: {exc}"


def create_task(app):
    task = app["task"]
    log = LOG_DIR / f"{task}.log"
    command = f'cd /d "{app_path(app["cwd"])}" && call "{app_path(app["bat"])}" >> "{log}" 2>&1'
    script = (
        f"$action=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/d /c {command}';"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 0);"
        f"Register-ScheduledTask -TaskName '{task}' -Action $action -Settings $settings -Force | Out-Null"
    )
    r = ps(script)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout or f"No se pudo instalar {task}")


def write_wrapper(name, target):
    wrapper = MANAGED_BATS / name
    wrapper.write_text(f'@echo off\ncall "{target}"\nexit /b %ERRORLEVEL%\n', encoding="utf-8")
    return str(wrapper.relative_to(ROOT))


def restore_base_config():
    if not ADDRESSES.exists():
        raise FileNotFoundError(f"No existe {ADDRESSES}")
    apps = []
    specs = {
        "SiAuditaxes_Multisite": ("auditaxes", "AUDITAXES", "Deployer_AUDITAXES", "AUDITAXES.bat", [4321, 4322, 4323], "logs"),
        "SiAuditaxes_Auditoria": ("siaudtax_exposicion", "SiAudTax Exposicion", "Deployer_SiAudTax_Exposicion", "SiAudTax-Exposicion.bat", [7009, 7010], "..\\logs"),
        "LLM_Foda": ("foda", "LLM FODA", "Deployer_FODA", "FODA.bat", [7004], "logs"),
        "TPSM": ("tpsm", "TPSM", "Deployer_TPSM", "TPSM.bat", [7003, 8015, 8105, 85], "C:\\TSPM\\Sistema"),
    }
    for raw in ADDRESSES.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [x.strip() for x in raw.split("=")]
        if len(parts) != 3 or parts[0] not in specs:
            continue
        key, label, task, wrapper_name, ports, logs = specs[parts[0]]
        folder = Path(parts[1])
        target = folder / parts[2]
        write_wrapper(wrapper_name, target)
        apps.append({
            "key": key,
            "name": label,
            "task": task,
            "cwd": "managed_bats",
            "bat": f"managed_bats\\{wrapper_name}",
            "ports": ports,
            "logs": str((folder / logs).resolve()) if ":" not in logs else logs,
        })
    if not apps:
        raise RuntimeError("direcciones.txt no tiene servicios validos.")
    config = {"cloudflared_service": "cloudflared", "apps": apps}
    save_config(config)
    return config


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Deployer Panel")
        self.geometry("1260x780")
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.config_data = load_config()
        self.cards = {}
        self.selected_log = None
        self.auto_refresh = tk.BooleanVar(value=True)
        self.scan_running = False
        self.build()
        self.refresh()
        self.after(500, self.drain)
        self.after(5000, self.auto_tick)

    def build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=18, pady=14)
        Logo(top).pack(side="left", padx=(0, 12))
        title_box = tk.Frame(top, bg=BG)
        title_box.pack(side="left")
        tk.Label(title_box, text=f"DEPLOYER // OPS CONTROL  {VERSION}", fg=GREEN, bg=BG, font=("Consolas", 24, "bold")).pack(anchor="w")
        self.summary = tk.Label(title_box, text="escaneando sistemas...", fg=MUTED, bg=BG, font=("Consolas", 10))
        self.summary.pack(anchor="w")

        tk.Checkbutton(top, text="Auto", variable=self.auto_refresh, bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG).pack(side="right", padx=6)
        tk.Button(top, text="Instalar todo", command=self.install_all, bg=PANEL, fg=CYAN).pack(side="right", padx=6)
        tk.Button(top, text="Reparar rutas", command=self.repair_paths, bg=PANEL, fg=YELLOW).pack(side="right", padx=6)
        tk.Button(top, text="Agregar servicio", command=self.add_service, bg=PANEL, fg=CYAN).pack(side="right", padx=6)
        tk.Button(top, text="Refrescar", command=self.refresh, bg=PANEL, fg=GREEN).pack(side="right", padx=6)

        self.grid = tk.Frame(self, bg=BG)
        self.grid.pack(fill="both", expand=True, padx=18)
        self.render_cards()

        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=18, pady=(2, 14))
        left = tk.Frame(bottom, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = tk.Frame(bottom, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        tk.Label(left, text="EVENTOS", fg=CYAN, bg=BG, font=("Consolas", 11, "bold")).pack(anchor="w")
        self.events = tk.Text(left, height=8, bg="#020403", fg=TEXT, insertbackground=GREEN, font=("Consolas", 10))
        self.events.pack(fill="both", expand=True)

        log_top = tk.Frame(right, bg=BG)
        log_top.pack(fill="x")
        tk.Label(log_top, text="LOGS", fg=CYAN, bg=BG, font=("Consolas", 11, "bold")).pack(side="left")
        tk.Button(log_top, text="Actualizar log", command=self.refresh_log, bg=PANEL, fg=GREEN).pack(side="right")
        self.log_view = tk.Text(right, height=8, bg="#020403", fg=TEXT, insertbackground=GREEN, font=("Consolas", 10))
        self.log_view.pack(fill="both", expand=True)

    def render_cards(self):
        for child in self.grid.winfo_children():
            child.destroy()
        self.cards = {}
        for i, app in enumerate(self.config_data["apps"]):
            card = ServiceCard(self.grid, self, app)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)
            self.cards[app["key"]] = card
        row = (len(self.config_data["apps"]) + 1) // 2
        self.cloud = CloudCard(self.grid, self, self.config_data.get("cloudflared_service", "cloudflared"))
        self.cloud.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
        self.grid.columnconfigure(0, weight=1)
        self.grid.columnconfigure(1, weight=1)

    def log(self, msg):
        self.events.insert("end", msg + "\n")
        self.events.see("end")

    def bg(self, fn, *args):
        def work():
            try:
                self.q.put(fn(*args))
            except Exception as exc:
                self.q.put(f"[ERROR] {exc}")
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        while not self.q.empty():
            msg = self.q.get()
            if isinstance(msg, tuple) and msg[0] == "scan":
                self.apply_scan(msg[1])
                continue
            if msg:
                self.log(msg)
            self.refresh()
        self.after(500, self.drain)

    def auto_tick(self):
        if self.auto_refresh.get():
            self.refresh()
            self.refresh_log()
        self.after(5000, self.auto_tick)

    def refresh(self):
        if self.scan_running:
            return
        self.scan_running = True
        self.summary.configure(text="escaneando sistemas...")
        threading.Thread(target=self.scan, daemon=True).start()

    def scan(self):
        try:
            data = {}
            for app in self.config_data["apps"]:
                state = task_state(app["task"])
                statuses = port_statuses(app.get("ports", []))
                pids = port_pids(app.get("ports", []))
                data[app["key"]] = {
                    "state": state,
                    "statuses": statuses,
                    "pids": pids,
                    "metrics": process_metrics(pids),
                    "missing": not app_path(app["bat"]).exists(),
                }
            service = self.config_data.get("cloudflared_service", "cloudflared")
            try:
                cloud = ps(f"Get-Service -Name '{service}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status", timeout=4).stdout.strip()
            except subprocess.TimeoutExpired:
                cloud = "LENTO"
            self.q.put(("scan", {"apps": data, "cloud": cloud}))
        except Exception as exc:
            self.q.put(("scan", {"apps": {}, "cloud": "ERROR"}))
            self.q.put(f"[ERROR] Escaneo fallo: {exc}")

    def apply_scan(self, scan):
        active = ports_ok = total_ports = missing = 0
        for key, card in self.cards.items():
            state = card.apply_snapshot(scan["apps"].get(key, {}))
            active += int(state["active"])
            missing += int(state["missing"])
            ports_ok += state["ports_ok"]
            total_ports += state["ports_total"]
        self.cloud.apply_status(scan["cloud"])
        self.summary.configure(text=f"{active}/{len(self.cards)} activos  |  {ports_ok}/{total_ports} puertos OK  |  {missing} BAT faltantes")
        self.scan_running = False

    def refresh_log(self):
        if not self.selected_log:
            return
        log, text = read_tail(self.selected_log)
        self.log_view.delete("1.0", "end")
        self.log_view.insert("end", f"{log or self.selected_log}\n" + "-" * 70 + "\n" + text)
        self.log_view.see("end")

    def show_logs(self, app):
        self.selected_log = [app.get("logs", ""), LOG_DIR / f"{app['task']}.log"]
        self.refresh_log()

    def install_all(self):
        def work():
            for app in self.config_data["apps"]:
                if not app_path(app["bat"]).exists():
                    return f"[ERROR] No existe: {app['bat']}"
                create_task(app)
            return "[OK] Tareas programadas instaladas/actualizadas."
        self.bg(work)

    def repair_paths(self):
        if not messagebox.askyesno("Reparar rutas", "Restaurar servicios desde direcciones.txt?"):
            return
        self.bg(self._repair_paths)

    def _repair_paths(self):
        config = restore_base_config()
        for app in config["apps"]:
            if not app_path(app["bat"]).exists():
                return f"[ERROR] No existe wrapper: {app['bat']}"
            create_task(app)
        self.config_data = config
        self.after(0, self.render_cards)
        self.after(0, self.refresh)
        return "[OK] Rutas reparadas y tareas reinstaladas. Ya puedes presionar Iniciar."

    def install_one(self, app):
        self.bg(lambda: self._install_one(app))

    def _install_one(self, app):
        if not app_path(app["bat"]).exists():
            return f"[ERROR] No existe: {app['bat']}"
        create_task(app)
        return f"[OK] Tarea instalada: {app['task']}"

    def add_service(self):
        ServiceDialog(self)

    def edit_service(self, app):
        ServiceDialog(self, app)

    def save_service(self, app, old_key=None):
        if old_key:
            self.config_data["apps"] = [app if x["key"] == old_key else x for x in self.config_data["apps"]]
            action = "editado"
        else:
            self.config_data["apps"].append(app)
            action = "agregado"
        save_config(self.config_data)
        self.render_cards()
        self.refresh()
        self.log(f"[OK] Servicio {action}: {app['name']}")

    def delete_service(self, app):
        if not messagebox.askyesno("Eliminar servicio", f"Quitar {app['name']} del panel?"):
            return
        self.config_data["apps"] = [x for x in self.config_data["apps"] if x["key"] != app["key"]]
        save_config(self.config_data)
        self.render_cards()
        self.refresh()
        self.log(f"[OK] Servicio eliminado del panel: {app['name']}")


class Logo(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=72, height=72, bg=BG, highlightthickness=0)
        self.create_oval(5, 5, 67, 67, outline=GREEN, width=2)
        self.create_oval(14, 14, 58, 58, outline=CYAN, width=1)
        self.create_line(36, 10, 36, 62, fill=GREEN, width=2)
        self.create_line(10, 36, 62, 36, fill=CYAN, width=2)
        self.create_polygon(36, 18, 49, 49, 36, 42, 23, 49, fill="", outline=GREEN, width=2)
        self.create_text(36, 36, text="D", fill=TEXT, font=("Consolas", 20, "bold"))


class ServiceDialog(tk.Toplevel):
    def __init__(self, app_ui, app=None):
        super().__init__(app_ui)
        self.app_ui = app_ui
        self.old_key = app["key"] if app else None
        self.title("Editar servicio" if app else "Agregar servicio")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.fields = {}
        rows = [("Nombre", "name"), ("BAT", "bat"), ("Carpeta", "cwd"), ("Puertos", "ports"), ("Logs", "logs")]
        for i, (label, key) in enumerate(rows):
            tk.Label(self, text=label, fg=TEXT, bg=BG, font=("Consolas", 10)).grid(row=i, column=0, sticky="w", padx=12, pady=6)
            entry = tk.Entry(self, width=64, bg=PANEL, fg=TEXT, insertbackground=GREEN)
            entry.grid(row=i, column=1, padx=8, pady=6)
            self.fields[key] = entry
            if key in ("bat", "cwd", "logs"):
                tk.Button(self, text="...", command=lambda k=key: self.pick(k), bg=PANEL, fg=CYAN, width=4).grid(row=i, column=2, padx=8)
        if app:
            self.fields["name"].insert(0, app["name"])
            self.fields["bat"].insert(0, app["bat"])
            self.fields["cwd"].insert(0, app["cwd"])
            self.fields["ports"].insert(0, ",".join(str(p) for p in app.get("ports", [])))
            self.fields["logs"].insert(0, app.get("logs", ""))
        else:
            self.fields["ports"].insert(0, "7000")
        help_text = (
            "BAT: archivo .bat que se ejecuta. Carpeta: carpeta donde debe correr. "
            "Logs: carpeta o archivo .log opcional; si no existe se usa panel_logs. Puertos: separados por coma."
        )
        tk.Label(self, text=help_text, fg=MUTED, bg=BG, font=("Consolas", 9), wraplength=560, justify="left").grid(
            row=len(rows), column=0, columnspan=3, sticky="w", padx=12, pady=(4, 0)
        )
        buttons = tk.Frame(self, bg=BG)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=3, sticky="e", padx=12, pady=12)
        tk.Button(buttons, text="Cancelar", command=self.destroy, bg=PANEL, fg=TEXT, width=12).pack(side="right", padx=4)
        tk.Button(buttons, text="Guardar", command=self.save, bg=PANEL, fg=GREEN, width=12).pack(side="right", padx=4)
        self.grab_set()

    def pick(self, key):
        if key == "bat":
            value = filedialog.askopenfilename(filetypes=[("Batch", "*.bat"), ("Todos", "*.*")])
            if value:
                self.fields["bat"].delete(0, "end")
                self.fields["bat"].insert(0, value)
                self.fields["cwd"].delete(0, "end")
                self.fields["cwd"].insert(0, str(Path(value).parent))
            return
        value = filedialog.askdirectory()
        if value:
            self.fields[key].delete(0, "end")
            self.fields[key].insert(0, value)

    def save(self):
        name = self.fields["name"].get().strip()
        bat = self.fields["bat"].get().strip()
        cwd = self.fields["cwd"].get().strip() or str(Path(bat).parent)
        logs = self.fields["logs"].get().strip() or cwd
        try:
            ports = [int(x.strip()) for x in self.fields["ports"].get().split(",") if x.strip()]
        except ValueError:
            messagebox.showerror("Puertos invalidos", "Usa formato: 7000,7001")
            return
        if not name or not bat:
            messagebox.showerror("Faltan datos", "Nombre y BAT son obligatorios.")
            return
        if not app_path(bat).exists():
            messagebox.showerror("BAT no existe", str(app_path(bat)))
            return
        if not app_path(cwd).exists():
            messagebox.showerror("Carpeta no existe", str(app_path(cwd)))
            return
        app = {
            "key": slug(name).lower(),
            "name": name,
            "task": f"Deployer_{slug(name)}",
            "cwd": cwd,
            "bat": bat,
            "ports": ports,
            "logs": logs,
        }
        self.app_ui.save_service(app, self.old_key)
        self.destroy()


class ServiceCard(tk.Frame):
    def __init__(self, parent, app_ui, app):
        super().__init__(parent, bg=PANEL, bd=1, relief="solid")
        self.app_ui = app_ui
        self.app = app
        self.bar = tk.Frame(self, bg=RED, width=7)
        self.bar.pack(side="left", fill="y")
        body = tk.Frame(self, bg=PANEL)
        body.pack(side="left", fill="both", expand=True)
        header = tk.Frame(body, bg=PANEL)
        header.pack(fill="x", padx=12, pady=(10, 4))
        self.title = tk.Label(header, text=app["name"], fg=GREEN, bg=PANEL, font=("Consolas", 15, "bold"))
        self.title.pack(side="left")
        tk.Button(header, text="Editar", command=lambda: app_ui.edit_service(app), bg=PANEL_2, fg=CYAN, width=8).pack(side="right", padx=2)
        tk.Button(header, text="Quitar", command=lambda: app_ui.delete_service(app), bg=PANEL_2, fg=RED, width=8).pack(side="right", padx=2)
        self.status = tk.Label(body, fg=TEXT, bg=PANEL, font=("Consolas", 10), justify="left")
        self.status.pack(anchor="w", padx=12)
        self.ports = tk.Frame(body, bg=PANEL)
        self.ports.pack(anchor="w", padx=12, pady=4)
        buttons = tk.Frame(body, bg=PANEL)
        buttons.pack(anchor="w", padx=10, pady=10)
        for label, cmd in [
            ("Instalar", self.install), ("Iniciar", self.start), ("Detener", self.stop),
            ("Reiniciar", self.restart), ("Abrir CMD", self.open_cmd), ("Ver logs", self.open_logs),
        ]:
            tk.Button(buttons, text=label, command=cmd, bg=BG, fg=CYAN, width=10).pack(side="left", padx=3)

    def apply_snapshot(self, snap):
        state = snap.get("state", "ESCANEANDO")
        statuses = snap.get("statuses", [])
        pids = snap.get("pids", [])
        active = bool(pids) or "RUNNING" in state
        missing = snap.get("missing", False)
        color = RED if missing else GREEN if active else YELLOW if "READY" in state else RED
        self.bar.configure(bg=color)
        self.title.configure(fg=color)
        self.status.configure(text=f"Tarea: {state}  |  {snap.get('metrics', 'CPU -- | RAM --')}\nBAT: {'OK' if not missing else 'FALTA'}")
        for child in self.ports.winfo_children():
            child.destroy()
        for port, ok in statuses:
            tk.Label(self.ports, text=f" {port} {'OK' if ok else '--'} ", fg=BG, bg=GREEN if ok else RED, font=("Consolas", 9, "bold")).pack(side="left", padx=3)
        return {"active": active, "missing": missing, "ports_ok": sum(1 for _, ok in statuses if ok), "ports_total": len(statuses)}

    def install(self):
        self.app_ui.install_one(self.app)

    def start(self):
        self.app_ui.bg(self._start)

    def stop(self):
        if messagebox.askyesno("Detener", f"Detener {self.app['name']}?"):
            self.app_ui.bg(lambda: self._run(["schtasks", "/End", "/TN", self.app["task"]], "detenido"))

    def restart(self):
        if not messagebox.askyesno("Reiniciar", f"Reiniciar {self.app['name']}?"):
            return
        def work():
            if not task_exists(self.app["task"]):
                create_task(self.app)
            self._run(["schtasks", "/End", "/TN", self.app["task"]], "detenido")
            self._run(["schtasks", "/Run", "/TN", self.app["task"]], "iniciado")
            return f"[OK] {self.app['name']} reiniciado."
        self.app_ui.bg(work)

    def open_cmd(self):
        bat = app_path(self.app["bat"])
        if not bat.exists():
            messagebox.showerror("No existe", str(bat))
            return
        subprocess.Popen(["cmd", "/k", f'cd /d "{app_path(self.app["cwd"])}" && call "{bat}"'], creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.app_ui.log(f"[CMD] {self.app['name']} abierto en consola.")

    def open_logs(self):
        self.app_ui.show_logs(self.app)

    def _run(self, args, action):
        r = run(args)
        msg = r.stdout.strip() or r.stderr.strip()
        return f"[{'OK' if r.returncode == 0 else 'ERROR'}] {self.app['name']} {action}. {msg}"

    def _start(self):
        if not task_exists(self.app["task"]):
            create_task(self.app)
        return self._run(["schtasks", "/Run", "/TN", self.app["task"]], "iniciado")


class CloudCard(tk.Frame):
    def __init__(self, parent, app_ui, service):
        super().__init__(parent, bg=PANEL, bd=1, relief="solid")
        self.app_ui = app_ui
        self.service = service
        tk.Label(self, text="Cloudflared", fg=CYAN, bg=PANEL, font=("Consolas", 15, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        self.status = tk.Label(self, fg=TEXT, bg=PANEL, font=("Consolas", 10))
        self.status.pack(anchor="w", padx=12)
        buttons = tk.Frame(self, bg=PANEL)
        buttons.pack(anchor="w", padx=10, pady=10)
        for label, action in [("Iniciar", "Start-Service"), ("Detener", "Stop-Service"), ("Reiniciar", "Restart-Service")]:
            tk.Button(buttons, text=label, command=lambda a=action: self.control(a), bg=BG, fg=CYAN, width=11).pack(side="left", padx=3)

    def apply_status(self, status):
        self.status.configure(text=f"Servicio: {self.service} | Estado: {status or 'NO ENCONTRADO'}")

    def control(self, action):
        self.app_ui.bg(lambda: self._control(action))

    def _control(self, action):
        r = ps(f"{action} -Name '{self.service}' -ErrorAction Stop")
        return f"[{'OK' if r.returncode == 0 else 'ERROR'}] Cloudflared: {action}. {(r.stderr or r.stdout).strip()}"


if __name__ == "__main__":
    App().mainloop()
