import json
import hashlib
import queue
import re
import subprocess
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "services.json"
LOG_DIR = ROOT / "panel_logs"
MANAGED_BATS = ROOT / "managed_bats"
ADDRESSES = ROOT / "direcciones.txt"
VERSION = "v3.1"
LOG_DIR.mkdir(exist_ok=True)
MANAGED_BATS.mkdir(exist_ok=True)

BG = "#f4f5f7"
PANEL = "#ffffff"
PANEL_2 = "#e9edf1"
GREEN = "#16704b"
CYAN = "#245fa6"
RED = "#b93d45"
YELLOW = "#916515"
TEXT = "#20252c"
MUTED = "#66717e"


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


def inspect_bat(path):
    data = app_path(path).read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    variables = {}
    commands = []
    for line in text.splitlines():
        line = line.strip()
        if not line or re.match(r"(?i)@?rem\b|::", line):
            continue
        line = re.sub(r"%([^%]+)%", lambda m: variables.get(m[1].upper(), m[0]), line)
        assignment = re.match(r'(?i)(?:@?set\s+|if\s+not\s+defined\s+\w+\s+set\s+)"?([\w]+)=([^\r\n]*)', line)
        if assignment:
            variables[assignment[1].upper()] = assignment[2].rstrip('"').strip()
        commands.append(line)
    # shortcut: static BAT inspection only; computed values and called scripts need manual ports.
    ports = {int(value) for key, value in variables.items()
             if re.search(r"(?:^|_)PORT(?:$|_)", key) and value.isdigit() and 1 <= int(value) <= 65535}
    for command in commands:
        if re.match(r"(?i)@?echo\b", command):
            continue
        for value in re.findall(r"(?i)--port(?:=|\s+)(\d+)\b|--(?:listen|bind)(?:=|\s+)[\w.\[\]:-]*:(\d+)\b", command):
            number = int(next(part for part in value if part))
            if 1 <= number <= 65535:
                ports.add(number)
    url = next((variables[key] for key in ("PUBLIC_URL", "SITE_URL", "APP_URL", "SITE_ORIGIN", "PUBLIC_ORIGIN")
                if variables.get(key, "").startswith(("https://", "http://"))), "")
    return {"ports": sorted(ports), "url": url, "script_hash": hashlib.sha256(data).hexdigest()}


def sync_bat(app):
    detected = inspect_bat(app["bat"])
    changed = app.get("script_hash") != detected["script_hash"]
    if app.get("port_mode", "manual") == "auto":
        app["ports"] = detected["ports"]
    if app.get("url_mode", "manual") == "auto":
        app["url"] = detected["url"]
    app["script_hash"] = detected["script_hash"]
    return changed


def task_exists(name):
    try:
        return run(["schtasks", "/Query", "/TN", name], timeout=4).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def task_state(name):
    try:
        task = name.replace("'", "''")
        result = ps(f"$t=Get-ScheduledTask -TaskName '{task}' -ErrorAction Stop; $t.State.ToString()", timeout=15)
    except subprocess.TimeoutExpired:
        return "LENTO"
    return result.stdout.strip().upper() if result.returncode == 0 else "ERROR CONSULTA"


def tcp_listeners():
    result = run(["netstat", "-ano", "-p", "tcp"], timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr or "No se pudieron consultar los puertos")
    listeners = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0] == "TCP" and fields[2].rsplit(":", 1)[-1] == "0":
            port = int(fields[1].rsplit(":", 1)[-1])
            listeners.setdefault(port, set()).add(fields[-1])
    return listeners


def port_pids(ports, listeners=None):
    listeners = tcp_listeners() if listeners is None else listeners
    return sorted({pid for port in ports for pid in listeners.get(port, ())})


def port_statuses(ports, listeners=None):
    listeners = tcp_listeners() if listeners is None else listeners
    return [(port, port in listeners) for port in ports]


def diagnostic_report(config):
    sections = [f"Deployer {VERSION}"]
    for command in (["whoami"], ["netstat", "-ano", "-p", "tcp"]):
        result = run(command, timeout=20)
        sections.append(" ".join(command) + "\n" + result.stdout + result.stderr)
    for app in config["apps"]:
        sections.append(f"\n=== {app['name']} ===\nBAT: {app_path(app['bat'])}\nCarpeta: {app_path(app['cwd'])}")
        bat = app_path(app["bat"])
        if bat.is_file():
            sections.append(bat.read_text(encoding="utf-8", errors="replace"))
        task = app["task"].replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop';"
            f"$t=Get-ScheduledTask -TaskName '{task}';"
            "$t | Select-Object TaskName,State | Format-List | Out-String;"
            "$t.Principal | Select-Object UserId,LogonType,RunLevel | Format-List | Out-String;"
            "$t.Actions | Format-List | Out-String;"
            "$t | Get-ScheduledTaskInfo | Format-List | Out-String"
        )
        result = ps(script, timeout=20)
        sections.append(result.stdout + result.stderr)
        log, tail = read_tail(LOG_DIR / f"{app['task']}.log")
        sections.append(f"Log: {log}\n{tail}")
    if ADDRESSES.is_file():
        sections.append("\n=== Rutas originales ===")
        for line in ADDRESSES.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = [part.strip() for part in line.split("=")]
            if len(parts) == 3:
                target = Path(parts[1]) / parts[2]
                sections.append(f"{target}: {'EXISTE' if target.is_file() else 'NO EXISTE'}")
    report = ROOT / "diagnostico.txt"
    report.write_text("\n".join(sections), encoding="utf-8")
    return f"[OK] Diagnostico guardado en {report}"


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
    if not path:
        return None
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
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop';"
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "$user=New-Object Security.Principal.WindowsPrincipal($identity);"
        "if(-not $user.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
        "{throw 'Abre el panel como administrador para instalar las tareas.'};"
        "$principal=New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest;"
        f"$action=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument {quote('/d /c ' + command)} "
        f"-WorkingDirectory {quote(app_path(app['cwd']))};"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 0);"
        f"Register-ScheduledTask -TaskName {quote(task)} -Action $action -Principal $principal -Settings $settings -Force | Out-Null"
    )
    r = ps(script)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout or f"No se pudo instalar {task}")


def write_wrapper(name, target):
    wrapper = MANAGED_BATS / name
    wrapper.write_text(
        f'@echo off\nsetlocal DisableDelayedExpansion\n'
        f'if not exist "{target}" (\n'
        f'  echo [ERROR] No existe el BAT original: "{target}"\n'
        '  exit /b 2\n)\n'
        f'echo [%date% %time%] Ejecutando "{target}"\n'
        f'call "{target}"\nexit /b %ERRORLEVEL%\n', encoding="utf-8"
    )
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


def service_status(snap, ports):
    if not snap:
        return "Sin consultar", MUTED
    if snap.get("missing"):
        return "Falta lanzador", RED
    opened = sum(ok for _, ok in snap.get("statuses", []))
    if ports and opened == len(ports):
        return "Puertos activos", GREEN
    if opened:
        return "Parcial", YELLOW
    if snap.get("state") == "RUNNING":
        return "En ejecucion", YELLOW
    return "Sin puertos activos", MUTED


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Deployer Panel")
        self.geometry("1260x780")
        self.minsize(960, 640)
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.config_data = load_config()
        self.cards = {}
        self.selected_log = None
        self.selected_key = None
        self.snapshots = {}
        self.log_running = False
        self.auto_refresh = tk.BooleanVar(value=True)
        self.scan_running = False
        self.build()
        for app in self.config_data["apps"]:
            try:
                if sync_bat(app):
                    self.log(f"[BAT] {app['name']}: archivo revisado; puertos {app['ports'] or 'sin detectar'}.")
            except OSError as exc:
                self.log(f"[ERROR] {app['name']}: {exc}")
        save_config(self.config_data)
        self.update_detail()
        self.refresh()
        self.after(500, self.drain)
        self.after(5000, self.auto_tick)

    def build(self):
        self.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TButton", padding=(12, 7), background=PANEL, borderwidth=1)
        style.map("TButton", background=[("active", PANEL_2)])
        style.configure("Primary.TButton", background=GREEN, foreground="white")
        style.map("Primary.TButton", background=[("active", "#10563a")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, rowheight=52, borderwidth=0)
        style.configure("Treeview.Heading", background=BG, foreground=MUTED, padding=10)
        style.map("Treeview", background=[("selected", "#e4eef9")], foreground=[("selected", TEXT)])
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9))
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=24, pady=(20, 12))
        title_box = tk.Frame(top, bg=BG)
        title_box.pack(side="left")
        tk.Label(title_box, text="Deployer", fg=TEXT, bg=BG, font=("Segoe UI", 23, "bold")).pack(anchor="w")
        self.summary = tk.Label(title_box, text="Consultando sistemas...", fg=MUTED, bg=BG)
        self.summary.pack(anchor="w")
        ttk.Button(top, text="Agregar servicio", style="Primary.TButton", command=self.add_service).pack(side="right", padx=(8, 0))
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Instalar todas las tareas", command=self.install_all)
        menu.add_separator()
        menu.add_command(label="Exportar diagnostico", command=lambda: self.bg(diagnostic_report, self.config_data))
        options = ttk.Menubutton(top, text="Herramientas", menu=menu)
        options.pack(side="right", padx=8)
        ttk.Button(top, text="Actualizar", command=self.refresh).pack(side="right")

        content = ttk.Panedwindow(self, orient="horizontal")
        content.pack(fill="both", expand=True, padx=24, pady=(4, 12))
        left = tk.Frame(content, bg=PANEL)
        right = tk.Frame(content, bg=BG)
        content.add(left, weight=2)
        content.add(right, weight=5)
        self.service_list = ttk.Treeview(left, columns=("state",), show="tree headings", selectmode="browse")
        self.service_list.heading("#0", text="Sistema", anchor="w")
        self.service_list.heading("state", text="Estado", anchor="w")
        self.service_list.column("#0", width=195, minwidth=140)
        self.service_list.column("state", width=145, minwidth=135, stretch=False)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.service_list.yview)
        self.service_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.service_list.pack(fill="both", expand=True)
        self.service_list.bind("<<TreeviewSelect>>", self.select_service)
        for tag, color in (("active", GREEN), ("error", RED), ("unknown", MUTED), ("partial", YELLOW)):
            self.service_list.tag_configure(tag, foreground=color)

        self.detail_title = tk.Label(right, bg=BG, fg=TEXT, font=("Segoe UI", 20, "bold"), anchor="w")
        self.detail_title.pack(fill="x", padx=(20, 0), pady=(8, 6))
        self.detail_status = tk.Label(right, bg=BG, fg=MUTED, anchor="w")
        self.detail_status.pack(fill="x", padx=(20, 0))
        self.detail_paths = tk.Label(right, bg=BG, fg=MUTED, anchor="w", justify="left", wraplength=560)
        self.detail_paths.pack(fill="x", padx=(20, 0), pady=(10, 12))
        right.bind("<Configure>", lambda e: (self.detail_paths.configure(wraplength=max(200, e.width - 24)), self.detail_status.configure(wraplength=max(200, e.width - 24))))
        self.actions = tk.Frame(right, bg=BG)
        self.actions.pack(fill="x", padx=(20, 0), pady=(0, 12))
        self.secondary = tk.Frame(right, bg=BG)
        self.secondary.pack(fill="x", padx=(20, 0), pady=(0, 12))
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True, padx=(20, 0))
        logs = tk.Frame(self.notebook, bg=PANEL)
        events = tk.Frame(self.notebook, bg=PANEL)
        self.notebook.add(logs, text="Logs")
        self.notebook.add(events, text="Actividad")
        log_top = tk.Frame(logs, bg=PANEL)
        log_top.pack(fill="x")
        self.log_source = tk.StringVar(value="Salida del panel")
        source = ttk.Combobox(log_top, textvariable=self.log_source, values=("Salida del panel", "Logs de aplicacion"), state="readonly", width=22)
        source.pack(side="left", padx=8, pady=8)
        source.bind("<<ComboboxSelected>>", lambda e: self.select_log_source())
        ttk.Button(log_top, text="Actualizar log", command=self.refresh_log).pack(side="right", padx=8)
        self.log_view = self.text_area(logs)
        self.events = self.text_area(events)
        self.log_path = tk.Label(logs, text="", bg=PANEL, fg=MUTED, anchor="w", wraplength=550)
        self.log_path.pack(side="bottom", before=self.log_view.master, fill="x", padx=10, pady=6)
        footer = tk.Frame(self, bg=BG)
        footer.pack(side="bottom", before=content, fill="x", padx=24, pady=(0, 12))
        tk.Label(footer, text=VERSION, bg=BG, fg=MUTED).pack(side="left")
        ttk.Checkbutton(footer, text="Actualizacion automatica", variable=self.auto_refresh).pack(side="right")
        self.render_cards()

    def text_area(self, parent):
        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, bg=PANEL, fg=TEXT, font=("Consolas", 10), height=8, width=40, relief="flat", padx=12, pady=12, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        return text

    def render_cards(self):
        for card in self.cards.values():
            card.destroy()
        self.service_list.delete(*self.service_list.get_children())
        self.cards = {}
        for app in self.config_data["apps"]:
            card = ServiceCard(self, self, app)
            self.cards[app["key"]] = card
            self.service_list.insert("", "end", iid=app["key"], text=app["name"], values=("Sin consultar",), tags=("unknown",))
        if hasattr(self, "cloud"):
            self.cloud.destroy()
        self.cloud = CloudCard(self, self, self.config_data.get("cloudflared_service", "cloudflared"))
        self.service_list.insert("", "end", iid="__cloud__", text="Cloudflared", values=("Sin consultar",), tags=("unknown",))
        keys = self.service_list.get_children()
        self.service_list.selection_set(self.selected_key if self.selected_key in keys else keys[0])
        self.select_service()

    def select_service(self, event=None):
        selection = self.service_list.selection()
        if not selection:
            return
        self.selected_key = selection[0]
        for frame in (self.actions, self.secondary):
            for child in frame.winfo_children():
                child.destroy()
        cloud = self.selected_key == "__cloud__"
        controller = self.cloud if cloud else self.cards[self.selected_key]
        self.detail_title.configure(text="Cloudflared" if cloud else controller.app["name"])
        for i, (label, action) in enumerate((("Iniciar", "Start-Service"), ("Detener", "Stop-Service"), ("Reiniciar", "Restart-Service"))):
            command = (lambda a=action: self.cloud.control(a)) if cloud else (controller.start, controller.stop, controller.restart)[i]
            ttk.Button(self.actions, text=label, command=command, style="Primary.TButton" if i == 0 else "TButton").pack(side="left", padx=(0, 8))
        if cloud:
            ttk.Button(self.secondary, text="Configurar servicio", command=self.configure_cloud).pack(side="left")
            self.selected_log = None
            self.set_log("Cloudflared se controla mediante el servicio de Windows.")
        else:
            if controller.app.get("url"):
                ttk.Button(self.actions, text="Abrir URL", command=lambda: webbrowser.open(controller.app["url"])).pack(side="left")
            for label, command in (("Abrir CMD", controller.open_cmd), ("Instalar tarea", controller.install), ("Editar", lambda: self.edit_service(controller.app)), ("Quitar", lambda: self.delete_service(controller.app))):
                ttk.Button(self.secondary, text=label, command=command).pack(side="left", padx=(0, 6))
            self.select_log_source()
        self.update_detail()

    def update_detail(self):
        if self.selected_key == "__cloud__":
            self.detail_status.configure(text=getattr(self.cloud, "state", "Sin consultar"), fg=MUTED)
            self.detail_paths.configure(text=f"Servicio de Windows: {self.cloud.service}")
        elif self.selected_key in self.cards:
            app = self.cards[self.selected_key].app
            snap = self.snapshots.get(self.selected_key, {})
            status, color = service_status(snap, app.get("ports", []))
            self.detail_status.configure(text=f"{status}   |   {snap.get('metrics', 'CPU -- | RAM --')}   |   Tarea: {snap.get('state', 'Sin consultar')}", fg=color)
            ports = "   ".join(f"{p}: {'activo' if ok else 'cerrado'}" for p, ok in snap.get("statuses", [])) or ("Puertos sin consultar" if app.get("ports") else "Puerto no detectado")
            self.detail_paths.configure(text=f"{ports}\nURL: {app.get('url') or 'Sin configurar'}\n\nBAT: {app_path(app['bat'])}\nCarpeta: {app_path(app['cwd'])}")

    def configure_cloud(self):
        from tkinter.simpledialog import askstring
        name = askstring("Cloudflared", "Nombre del servicio de Windows", initialvalue=self.cloud.service, parent=self)
        if name and name.strip():
            self.config_data["cloudflared_service"] = name.strip()
            save_config(self.config_data)
            self.cloud.service = name.strip()
            self.update_detail()
            self.refresh()

    def log(self, msg):
        self.events.configure(state="normal")
        self.events.insert("end", datetime.now().strftime("%H:%M:%S  ") + msg + "\n")
        self.events.see("end")
        self.events.configure(state="disabled")

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
            if isinstance(msg, tuple) and msg[0] == "config":
                self.config_data = msg[1]
                self.render_cards()
                self.refresh()
                continue
            if isinstance(msg, tuple) and msg[0] == "metadata":
                self.update_detail()
                continue
            if isinstance(msg, tuple) and msg[0] == "log":
                self.log_running = False
                if msg[1] == self.selected_log:
                    self.log_path.configure(text=str(msg[2] or "No hay archivo de log"))
                    self.set_log(msg[3])
                else:
                    self.refresh_log()
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
            listeners = tcp_listeners()
            for app in self.config_data["apps"]:
                state = task_state(app["task"])
                statuses = port_statuses(app.get("ports", []), listeners)
                pids = port_pids(app.get("ports", []), listeners)
                data[app["key"]] = {
                    "state": state,
                    "statuses": statuses,
                    "pids": pids,
                    "metrics": process_metrics(pids),
                    "missing": not app_path(app["bat"]).exists(),
                }
            service = self.config_data.get("cloudflared_service", "cloudflared").replace("'", "''")
            try:
                cloud = ps(f"Get-Service -Name '{service}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status", timeout=4).stdout.strip()
            except subprocess.TimeoutExpired:
                cloud = "LENTO"
            self.q.put(("scan", {"apps": data, "cloud": cloud}))
        except Exception as exc:
            self.q.put(("scan", {"apps": {}, "cloud": "ERROR"}))
            self.q.put(f"[ERROR] Escaneo fallo: {exc}")

    def apply_scan(self, scan):
        self.snapshots = scan["apps"]
        active = ports_ok = total_ports = missing = 0
        for key, card in self.cards.items():
            state = card.apply_snapshot(scan["apps"].get(key, {}))
            active += int(state["active"])
            missing += int(state["missing"])
            ports_ok += state["ports_ok"]
            total_ports += state["ports_total"]
        self.cloud.apply_status(scan["cloud"])
        self.summary.configure(text=f"{active}/{len(self.cards)} activos  |  {ports_ok}/{total_ports} puertos OK  |  {missing} BAT faltantes" if self.cards else "Sin sistemas registrados")
        self.scan_running = False
        self.update_detail()

    def refresh_log(self):
        if not self.selected_log or self.log_running:
            return
        self.log_running = True
        paths = list(self.selected_log)
        def work():
            try:
                log, text = read_tail(paths)
            except Exception as exc:
                log, text = None, f"No se pudo leer el log: {exc}"
            self.q.put(("log", paths, log, text))
        threading.Thread(target=work, daemon=True).start()

    def set_log(self, text):
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.insert("end", text)
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def select_log_source(self):
        if self.selected_key not in self.cards:
            return
        app = self.cards[self.selected_key].app
        self.selected_log = [LOG_DIR / f"{app['task']}.log"] if self.log_source.get() == "Salida del panel" else [app.get("logs", "")]
        self.log_path.configure(text="Consultando log...")
        self.set_log("")
        self.refresh_log()

    def show_logs(self, app):
        self.service_list.selection_set(app["key"])
        self.select_service()
        self.notebook.select(0)

    def install_all(self):
        def work():
            for app in self.config_data["apps"]:
                if not app_path(app["bat"]).exists():
                    return f"[ERROR] No existe: {app['bat']}"
                self.sync_service(app)
                create_task(app)
                app["task_dirty"] = False
            save_config(self.config_data)
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
        self.q.put(("config", config))
        return "[OK] Rutas reparadas y tareas reinstaladas. Ya puedes presionar Iniciar."

    def install_one(self, app):
        self.bg(lambda: self._install_one(app))

    def _install_one(self, app):
        if not app_path(app["bat"]).exists():
            return f"[ERROR] No existe: {app['bat']}"
        self.sync_service(app)
        create_task(app)
        app["task_dirty"] = False
        save_config(self.config_data)
        return f"[OK] Tarea instalada: {app['task']}"

    def sync_service(self, app):
        changed = sync_bat(app)
        save_config(self.config_data)
        self.q.put(("metadata", app["key"]))
        if changed:
            self.q.put(f"[BAT] {app['name']}: cambio detectado; puertos {app['ports'] or 'sin detectar'}.")

    def add_service(self):
        ServiceDialog(self)

    def edit_service(self, app):
        ServiceDialog(self, app)

    def save_service(self, app, old_key=None):
        if old_key:
            old = next(x for x in self.config_data["apps"] if x["key"] == old_key)
            app["task_dirty"] = old.get("task_dirty", False) or (old["bat"], old["cwd"]) != (app["bat"], app["cwd"])
            self.config_data["apps"] = [app if x["key"] == old_key else x for x in self.config_data["apps"]]
            action = "editado"
        else:
            self.config_data["apps"].append(app)
            action = "agregado"
        save_config(self.config_data)
        self.selected_key = app["key"]
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


class ServiceDialog(tk.Toplevel):
    def __init__(self, app_ui, app=None):
        super().__init__(app_ui)
        self.transient(app_ui)
        self.app_ui = app_ui
        self.old_key = app["key"] if app else None
        self.title("Editar servicio" if app else "Agregar servicio")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.fields = {}
        self.auto_ports = tk.BooleanVar(value=app.get("port_mode", "manual") == "auto" if app else True)
        rows = [("Nombre", "name"), ("Archivo BAT", "bat"), ("URL publica", "url"), ("Puertos", "ports"), ("Carpeta", "cwd"), ("Logs", "logs")]
        for i, (label, key) in enumerate(rows):
            tk.Label(self, text=label, fg=TEXT, bg=BG).grid(row=i, column=0, sticky="w", padx=12, pady=6)
            entry = ttk.Entry(self, width=60)
            entry.grid(row=i, column=1, padx=8, pady=6)
            self.fields[key] = entry
            if key in ("bat", "cwd", "logs"):
                ttk.Button(self, text="Buscar", command=lambda k=key: self.pick(k)).grid(row=i, column=2, padx=8)
            if key == "ports":
                ttk.Button(self, text="Detectar", command=self.detect).grid(row=i, column=2, padx=8)
        if app:
            self.fields["name"].insert(0, app["name"])
            self.fields["bat"].insert(0, app["bat"])
            self.fields["cwd"].insert(0, app["cwd"])
            self.fields["ports"].insert(0, ",".join(str(p) for p in app.get("ports", [])))
            self.fields["url"].insert(0, app.get("url", ""))
            self.fields["logs"].insert(0, app.get("logs", ""))
        self.fields["bat"].bind("<FocusOut>", lambda e: self.detect())
        ttk.Checkbutton(self, text="Actualizar puertos desde el BAT en cada arranque", variable=self.auto_ports, command=self.toggle_ports).grid(row=len(rows), column=1, sticky="w", padx=8, pady=8)
        self.detection = tk.Label(self, text="", fg=MUTED, bg=BG, wraplength=450, justify="left")
        self.detection.grid(row=len(rows) + 1, column=1, sticky="w", padx=8)
        buttons = tk.Frame(self, bg=BG)
        buttons.grid(row=len(rows) + 2, column=0, columnspan=3, sticky="e", padx=12, pady=12)
        ttk.Button(buttons, text="Guardar", command=self.save, style="Primary.TButton").pack(side="right", padx=4)
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=4)
        self.toggle_ports()
        self.grab_set()

    def toggle_ports(self):
        self.fields["ports"].configure(state="readonly" if self.auto_ports.get() else "normal")
        if self.auto_ports.get():
            self.detect()

    def detect(self):
        bat = self.fields["bat"].get().strip()
        if not bat:
            return
        try:
            found = inspect_bat(bat)
        except OSError as exc:
            self.detection.configure(text=str(exc), fg=RED)
            return
        if self.auto_ports.get():
            self.fields["ports"].configure(state="normal")
            self.fields["ports"].delete(0, "end")
            self.fields["ports"].insert(0, ",".join(map(str, found["ports"])))
            self.fields["ports"].configure(state="readonly")
        if not self.fields["url"].get() and found["url"]:
            self.fields["url"].insert(0, found["url"])
        self.detection.configure(text="Puertos detectados: " + ", ".join(map(str, found["ports"])) if found["ports"] else "Puerto no detectable. Desactiva la actualizacion automatica e indicalo manualmente.", fg=MUTED)

    def pick(self, key):
        if key == "bat":
            value = filedialog.askopenfilename(filetypes=[("Batch", "*.bat"), ("Todos", "*.*")])
            if value:
                self.fields["bat"].delete(0, "end")
                self.fields["bat"].insert(0, value)
                self.fields["cwd"].delete(0, "end")
                self.fields["cwd"].insert(0, str(Path(value).parent))
                self.detect()
            return
        value = filedialog.askdirectory()
        if value:
            self.fields[key].delete(0, "end")
            self.fields[key].insert(0, value)

    def save(self):
        self.detect()
        name = self.fields["name"].get().strip()
        bat = self.fields["bat"].get().strip()
        cwd = self.fields["cwd"].get().strip() or str(Path(bat).parent)
        logs = self.fields["logs"].get().strip() or cwd
        url = self.fields["url"].get().strip()
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or any(c.isspace() for c in url):
                raise ValueError
            parsed.port
        except ValueError:
            messagebox.showerror("URL invalida", "Indica una URL completa, por ejemplo https://mi-sistema.com")
            return
        try:
            ports = [int(x.strip()) for x in self.fields["ports"].get().split(",") if x.strip()]
            if not ports or any(not 1 <= port <= 65535 for port in ports):
                raise ValueError
        except ValueError:
            messagebox.showerror("Puertos invalidos", "Usa formato: 7000,7001")
            return
        if not name or not bat:
            messagebox.showerror("Faltan datos", "Nombre y BAT son obligatorios.")
            return
        if not app_path(bat).is_file() or app_path(bat).suffix.lower() != ".bat":
            messagebox.showerror("BAT no existe", str(app_path(bat)))
            return
        if not app_path(cwd).is_dir():
            messagebox.showerror("Carpeta no existe", str(app_path(cwd)))
            return
        app = {
            "key": self.old_key or slug(name).lower(),
            "name": name,
            "task": next((x["task"] for x in self.app_ui.config_data["apps"] if x["key"] == self.old_key), f"Deployer_{slug(name)}"),
            "cwd": cwd,
            "bat": bat,
            "ports": ports,
            "url": url,
            "port_mode": "auto" if self.auto_ports.get() else "manual",
            "logs": logs,
        }
        if not self.old_key and any(x["key"] == app["key"] for x in self.app_ui.config_data["apps"]):
            messagebox.showerror("Servicio existente", "Ya existe un servicio con ese nombre.")
            return
        try:
            app["url_mode"] = "auto" if inspect_bat(bat)["url"] == url else "manual"
            sync_bat(app)
        except OSError as exc:
            messagebox.showerror("No se pudo leer el BAT", str(exc))
            return
        self.app_ui.save_service(app, self.old_key)
        self.destroy()


class ServiceCard(tk.Frame):
    def __init__(self, parent, app_ui, app):
        super().__init__(parent)
        self.app_ui = app_ui
        self.app = app

    def apply_snapshot(self, snap):
        statuses = snap.get("statuses", [])
        opened = sum(ok for _, ok in statuses)
        active = bool(statuses) and opened == len(statuses)
        missing = snap.get("missing", False)
        status, color = service_status(snap, self.app.get("ports", []))
        tag = {GREEN: "active", RED: "error", YELLOW: "partial"}.get(color, "unknown")
        self.app_ui.service_list.item(self.app["key"], values=(status,), tags=(tag,))
        return {"active": active, "missing": missing, "ports_ok": opened, "ports_total": len(self.app.get("ports", []))}

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
            self.app_ui.sync_service(self.app)
            self._run(["schtasks", "/End", "/TN", self.app["task"]], "detenido")
            return self._start()
        self.app_ui.bg(work)

    def open_cmd(self):
        bat = app_path(self.app["bat"])
        if not bat.exists():
            messagebox.showerror("No existe", str(bat))
            return
        try:
            self.app_ui.sync_service(self.app)
        except OSError as exc:
            messagebox.showerror("No se pudo leer el BAT", str(exc))
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
        self.app_ui.sync_service(self.app)
        if self.app.get("task_dirty") or not task_exists(self.app["task"]):
            create_task(self.app)
            self.app["task_dirty"] = False
            save_config(self.app_ui.config_data)
        return self._run(["schtasks", "/Run", "/TN", self.app["task"]], "solicitud de inicio")


class CloudCard(tk.Frame):
    def __init__(self, parent, app_ui, service):
        super().__init__(parent)
        self.app_ui = app_ui
        self.service = service

    def apply_status(self, status):
        self.state = {"Running": "Activo", "Stopped": "Detenido"}.get(status, status or "No encontrado")
        self.app_ui.service_list.item("__cloud__", values=(self.state,), tags=("active" if status == "Running" else "unknown",))

    def control(self, action):
        self.app_ui.bg(lambda: self._control(action))

    def _control(self, action):
        service = self.service.replace("'", "''")
        r = ps(f"{action} -Name '{service}' -ErrorAction Stop")
        return f"[{'OK' if r.returncode == 0 else 'ERROR'}] Cloudflared: {action}. {(r.stderr or r.stdout).strip()}"


if __name__ == "__main__":
    App().mainloop()
