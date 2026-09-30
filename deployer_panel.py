import json
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "services.json"
LOG_DIR = ROOT / "panel_logs"
LOG_DIR.mkdir(exist_ok=True)

BG = "#050807"
PANEL = "#0c1512"
GREEN = "#39ff88"
CYAN = "#42d9ff"
RED = "#ff4d6d"
YELLOW = "#ffd166"
TEXT = "#d8ffe8"


def run(args, timeout=25):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, shell=False)


def ps(script, timeout=25):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)


def load_config():
    if not CONFIG.exists():
        raise FileNotFoundError(f"No existe {CONFIG}")
    with CONFIG.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with CONFIG.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def slug(text):
    keep = []
    for char in text.upper():
        keep.append(char if char.isalnum() else "_")
    return "_".join(part for part in "".join(keep).split("_") if part)


def task_exists(name):
    return run(["schtasks", "/Query", "/TN", name]).returncode == 0


def task_state(name):
    if not task_exists(name):
        return "NO INSTALADO"
    out = run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"]).stdout
    for line in out.splitlines():
        if line.lower().startswith("status:"):
            return line.split(":", 1)[1].strip().upper()
    return "INSTALADO"


def port_pids(ports):
    if not ports:
        return []
    port_list = ",".join(str(p) for p in ports)
    script = (
        f"$ports=@({port_list});"
        "$ids=@();"
        "foreach($port in $ports){"
        "$ids += Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique"
        "};"
        "$ids | Where-Object {$_ -gt 0} | Sort-Object -Unique"
    )
    out = ps(script).stdout
    return [x.strip() for x in out.splitlines() if x.strip().isdigit()]


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
    return ps(script).stdout.strip() or "CPU -- | RAM --"


def ports_text(ports):
    if not ports:
        return "-"
    live = set()
    for pid in port_pids(ports):
        if pid:
            live.add(pid)
    status = []
    for port in ports:
        ok = ps(f"if(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue){{'OK'}}").stdout.strip()
        status.append(f"{port}:{'OK' if ok else '--'}")
    return "  ".join(status)


def latest_log(path):
    p = Path(path)
    if p.is_file():
        return p
    if not p.exists():
        return None
    files = [x for x in p.rglob("*.log") if x.is_file()]
    return max(files, key=lambda x: x.stat().st_mtime) if files else None


def create_task(app):
    task = app["task"]
    cwd = app["cwd"]
    bat = app["bat"]
    log = LOG_DIR / f"{task}.log"
    command = f'cd /d "{cwd}" && call "{bat}" >> "{log}" 2>&1'
    script = (
        f"$action=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/d /c {command}';"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 0);"
        f"Register-ScheduledTask -TaskName '{task}' -Action $action -Settings $settings -Force | Out-Null"
    )
    r = ps(script)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout or f"No se pudo instalar {task}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Deployer Panel")
        self.geometry("1180x720")
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.config_data = load_config()
        self.cards = {}
        self.build()
        self.refresh()
        self.after(500, self.drain)

    def build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=18, pady=14)
        tk.Label(top, text="DEPLOYER PANEL", fg=GREEN, bg=BG, font=("Consolas", 24, "bold")).pack(side="left")
        tk.Button(top, text="Instalar control automatico", command=self.install_all, bg=PANEL, fg=CYAN).pack(side="right", padx=6)
        tk.Button(top, text="Agregar servicio", command=self.add_service, bg=PANEL, fg=CYAN).pack(side="right", padx=6)
        tk.Button(top, text="Refrescar", command=self.refresh, bg=PANEL, fg=GREEN).pack(side="right", padx=6)

        self.grid = tk.Frame(self, bg=BG)
        self.grid.pack(fill="both", expand=True, padx=18)
        self.render_cards()

        tk.Label(self, text="EVENTOS", fg=CYAN, bg=BG, font=("Consolas", 11, "bold")).pack(anchor="w", padx=20)
        self.events = tk.Text(self, height=8, bg="#020403", fg=TEXT, insertbackground=GREEN, font=("Consolas", 10))
        self.events.pack(fill="x", padx=18, pady=(2, 14))

    def render_cards(self):
        for child in self.grid.winfo_children():
            child.destroy()
        self.cards = {}
        for i, app in enumerate(self.config_data["apps"]):
            card = ServiceCard(self.grid, self, app)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)
            self.cards[app["key"]] = card
        row = (len(self.config_data["apps"]) + 1) // 2
        cloud = CloudCard(self.grid, self, self.config_data.get("cloudflared_service", "cloudflared"))
        cloud.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
        self.cloud = cloud
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
            if msg:
                self.log(msg)
            self.refresh()
        self.after(500, self.drain)

    def refresh(self):
        for card in self.cards.values():
            card.refresh()
        self.cloud.refresh()

    def install_all(self):
        def work():
            for app in self.config_data["apps"]:
                if not Path(app["bat"]).exists():
                    return f"[ERROR] No existe: {app['bat']}"
                create_task(app)
            return "[OK] Tareas programadas instaladas/actualizadas."
        self.bg(work)

    def add_service(self):
        ServiceDialog(self)

    def save_new_service(self, app):
        self.config_data["apps"].append(app)
        save_config(self.config_data)
        self.render_cards()
        self.refresh()
        self.log(f"[OK] Servicio agregado: {app['name']}")


class ServiceDialog(tk.Toplevel):
    def __init__(self, app_ui):
        super().__init__(app_ui)
        self.app_ui = app_ui
        self.title("Agregar servicio")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.fields = {}
        rows = [
            ("Nombre", "name"),
            ("BAT", "bat"),
            ("Carpeta", "cwd"),
            ("Puertos", "ports"),
            ("Logs", "logs"),
        ]
        for i, (label, key) in enumerate(rows):
            tk.Label(self, text=label, fg=TEXT, bg=BG, font=("Consolas", 10)).grid(row=i, column=0, sticky="w", padx=12, pady=6)
            entry = tk.Entry(self, width=58, bg=PANEL, fg=TEXT, insertbackground=GREEN)
            entry.grid(row=i, column=1, padx=8, pady=6)
            self.fields[key] = entry
            if key in ("bat", "cwd", "logs"):
                tk.Button(self, text="...", command=lambda k=key: self.pick(k), bg=PANEL, fg=CYAN, width=4).grid(row=i, column=2, padx=8)
        self.fields["ports"].insert(0, "7000,7001")
        buttons = tk.Frame(self, bg=BG)
        buttons.grid(row=len(rows), column=0, columnspan=3, sticky="e", padx=12, pady=12)
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
        key = slug(name).lower()
        app = {
            "key": key,
            "name": name,
            "task": f"Deployer_{slug(name)}",
            "cwd": cwd,
            "bat": bat,
            "ports": ports,
            "logs": logs,
        }
        self.app_ui.save_new_service(app)
        self.destroy()


class ServiceCard(tk.Frame):
    def __init__(self, parent, app_ui, app):
        super().__init__(parent, bg=PANEL, bd=1, relief="solid")
        self.app_ui = app_ui
        self.app = app
        self.title = tk.Label(self, text=app["name"], fg=GREEN, bg=PANEL, font=("Consolas", 15, "bold"))
        self.title.pack(anchor="w", padx=12, pady=(10, 4))
        self.status = tk.Label(self, fg=TEXT, bg=PANEL, font=("Consolas", 10), justify="left")
        self.status.pack(anchor="w", padx=12)
        buttons = tk.Frame(self, bg=PANEL)
        buttons.pack(anchor="w", padx=10, pady=10)
        for label, cmd in [("Iniciar", self.start), ("Detener", self.stop), ("Reiniciar", self.restart), ("Abrir CMD", self.open_cmd), ("Logs", self.open_logs)]:
            tk.Button(buttons, text=label, command=cmd, bg=BG, fg=CYAN, width=11).pack(side="left", padx=3)

    def refresh(self):
        state = task_state(self.app["task"])
        pids = port_pids(self.app.get("ports", []))
        color = GREEN if pids or "RUNNING" in state else YELLOW if "READY" in state else RED
        self.title.configure(fg=color)
        self.status.configure(text=f"Tarea: {state}\nPuertos: {ports_text(self.app.get('ports', []))}\n{process_metrics(pids)}")

    def start(self):
        self.app_ui.bg(lambda: self._run(["schtasks", "/Run", "/TN", self.app["task"]], "iniciado"))

    def stop(self):
        self.app_ui.bg(lambda: self._run(["schtasks", "/End", "/TN", self.app["task"]], "detenido"))

    def restart(self):
        def work():
            self._run(["schtasks", "/End", "/TN", self.app["task"]], "detenido")
            self._run(["schtasks", "/Run", "/TN", self.app["task"]], "iniciado")
            return f"[OK] {self.app['name']} reiniciado."
        self.app_ui.bg(work)

    def open_cmd(self):
        bat = self.app["bat"]
        if not Path(bat).exists():
            messagebox.showerror("No existe", bat)
            return
        subprocess.Popen(["cmd", "/k", f'cd /d "{self.app["cwd"]}" && call "{bat}"'], creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.app_ui.log(f"[CMD] {self.app['name']} abierto en consola.")

    def open_logs(self):
        log = latest_log(self.app.get("logs", "")) or latest_log(LOG_DIR)
        if not log:
            messagebox.showinfo("Logs", "No encontre logs para este sistema.")
            return
        subprocess.Popen(["notepad.exe", str(log)])

    def _run(self, args, action):
        r = run(args)
        msg = r.stdout.strip() or r.stderr.strip()
        return f"[{'OK' if r.returncode == 0 else 'ERROR'}] {self.app['name']} {action}. {msg}"


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

    def refresh(self):
        out = ps(f"Get-Service -Name '{self.service}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status").stdout.strip()
        self.status.configure(text=f"Servicio: {self.service} | Estado: {out or 'NO ENCONTRADO'}")

    def control(self, action):
        self.app_ui.bg(lambda: self._control(action))

    def _control(self, action):
        r = ps(f"{action} -Name '{self.service}' -ErrorAction Stop")
        return f"[{'OK' if r.returncode == 0 else 'ERROR'}] Cloudflared: {action}. {(r.stderr or r.stdout).strip()}"


if __name__ == "__main__":
    App().mainloop()
