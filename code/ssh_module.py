#!/usr/bin/env python3
"""
JARVIS — ssh_module.py
======================
Gestione connessioni SSH multi-host per JARVIS v6.

Funzionalità:
  - Connessioni SSH persistenti (una per host, riutilizzate)
  - Rilevamento automatico OS remoto (Linux / Windows / macOS)
  - Esecuzione comandi adattata all'OS (bash vs cmd/powershell)
  - Inventario host su file JSON (~/.jarvis/ssh_hosts.json)
  - Info hardware remoto raccolte alla connessione (CPU, RAM, OS)
  - Comandi /host add | list | remove | connect | disconnect

Dipendenze:
  pip install paramiko
"""

import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import paramiko
    PARAMIKO_OK = True
except ImportError:
    PARAMIKO_OK = False


# ── Colori ANSI (riusa quelli di jarvis_banner se disponibili) ────────────────
CYAN  = "\033[96m"
GOLD  = "\033[93m"
GREEN = "\033[92m"
RED   = "\033[91m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RST   = "\033[0m"


# ── Tipi OS riconosciuti ──────────────────────────────────────────────────────
OS_LINUX   = "linux"
OS_WINDOWS = "windows"
OS_MACOS   = "macos"
OS_UNKNOWN = "unknown"


class RemoteHost:
    """Rappresenta un host SSH configurato."""

    def __init__(self, data: dict):
        self.name:     str = data["name"]          # nome breve es. "server-casa"
        self.host:     str = data["host"]          # IP o hostname
        self.port:     int = data.get("port", 22)
        self.user:     str = data["user"]
        self.auth:     str = data.get("auth", "password")   # "password" | "key"
        self.password: str = data.get("password", "")
        self.key_path: str = data.get("key_path", "")       # es. "~/.ssh/id_rsa"
        self.os_type:  str = data.get("os_type", OS_UNKNOWN)
        self.hw_info:  dict= data.get("hw_info", {})        # CPU, RAM, OS, hostname
        self.cwd:      str = data.get("cwd", "")            # directory corrente remota

    def to_dict(self) -> dict:
        return {
            "name":      self.name,
            "host":      self.host,
            "port":      self.port,
            "user":      self.user,
            "auth":      self.auth,
            "password":  self.password,
            "key_path":  self.key_path,
            "os_type":   self.os_type,
            "hw_info":   self.hw_info,
            "cwd":       self.cwd,
        }

    def display_line(self, active_name: str = "") -> str:
        mark  = f"{GREEN}● connesso{RST}" if self.name == active_name else f"{DIM}○ offline{RST}"
        os_ic = {"linux": "🐧", "windows": "🪟", "macos": "🍎"}.get(self.os_type, "?")
        hw    = ""
        if self.hw_info:
            hw = f" | {self.hw_info.get('os','?')} | {self.hw_info.get('cpu','?')[:30]}"
        return f"  {GOLD}{self.name}{RST} ({self.host}:{self.port}) {os_ic}{hw} {mark}"


class SSHSession:
    """Connessione SSH persistente a un singolo host."""

    KEEPALIVE = 30   # secondi tra keepalive

    def __init__(self, host: RemoteHost):
        self.host    = host
        self._client: Optional[paramiko.SSHClient] = None
        self._lock   = threading.Lock()
        self._alive  = False

    def connect(self) -> tuple[bool, str]:
        """Apre la connessione SSH. Ritorna (ok, messaggio)."""
        if not PARAMIKO_OK:
            return False, "paramiko non installato — esegui: pip install paramiko"
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = dict(
                hostname = self.host.host,
                port     = self.host.port,
                username = self.host.user,
                timeout  = 10,
            )
            if self.host.auth == "key" and self.host.key_path:
                kwargs["key_filename"] = str(Path(self.host.key_path).expanduser())
            else:
                kwargs["password"] = self.host.password

            client.connect(**kwargs)
            # Keepalive
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(self.KEEPALIVE)

            self._client = client
            self._alive  = True
            return True, f"Connesso a {self.host.name} ({self.host.host})"
        except paramiko.AuthenticationException:
            return False, f"Autenticazione fallita per {self.host.user}@{self.host.host}"
        except (socket.timeout, paramiko.ssh_exception.NoValidConnectionsError) as e:
            return False, f"Impossibile raggiungere {self.host.host}:{self.host.port} — {e}"
        except Exception as e:
            return False, f"Errore SSH: {e}"

    def disconnect(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._alive  = False

    def is_alive(self) -> bool:
        if not self._client or not self._alive:
            return False
        try:
            transport = self._client.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def run(self, command: str, timeout: int = 60) -> tuple[int, str]:
        """
        Esegue un comando sull'host remoto.
        Adatta automaticamente il comando all'OS (Linux bash / Windows cmd).
        Ritorna (returncode, output).
        """
        if not self.is_alive():
            ok, msg = self.connect()
            if not ok:
                return -1, f"❌ Riconnessione fallita: {msg}"

        # Adatta il comando all'OS
        cmd_to_run = self._adapt_command(command)

        with self._lock:
            try:
                stdin, stdout, stderr = self._client.exec_command(
                    cmd_to_run, timeout=timeout
                )
                rc  = stdout.channel.recv_exit_status()
                out = stdout.read().decode(errors="replace").strip()
                err = stderr.read().decode(errors="replace").strip()
                combined = out or err or ""
                return rc, combined
            except Exception as e:
                self._alive = False
                return -1, f"❌ Errore esecuzione remota: {e}"

    def _adapt_command(self, cmd: str) -> str:
        """
        Adatta il comando all'OS dell'host remoto.
        Su Windows wrappa in PowerShell se necessario.
        """
        os_type = self.host.os_type

        if os_type == OS_WINDOWS:
            # Converti comandi Linux comuni in equivalenti Windows
            _WIN_MAP = {
                r'^ls\b':    'dir',
                r'^ls -la':  'dir /a',
                r'^pwd\b':   'cd',
                r'^cat ':    'type ',
                r'^rm ':     'del ',
                r'^cp ':     'copy ',
                r'^mv ':     'move ',
                r'^mkdir ':  'mkdir ',
                r'^echo ':   'echo ',
                r'^which ':  'where ',
                r'^grep ':   'findstr ',
                r'^clear\b': 'cls',
                r'^df\b':    'wmic logicaldisk get size,freespace,caption',
                r'^free\b':  'wmic OS get TotalVisibleMemorySize,FreePhysicalMemory',
                r'^uname':   'ver',
                r'^ps\b':    'tasklist',
                r'^kill ':   'taskkill /PID ',
                r'^apt ':    'winget ',
                r'^pip3 ':   'pip ',
            }
            for pat, repl in _WIN_MAP.items():
                if re.match(pat, cmd, re.IGNORECASE):
                    cmd = re.sub(pat, repl, cmd, count=1, flags=re.IGNORECASE)
                    break
            # Wrappa in PowerShell per comandi non-cmd
            ps_cmds = ('Get-', 'Set-', 'New-', 'Remove-', 'Start-', 'Stop-',
                       'Invoke-', 'Test-', 'Write-', 'Read-', 'Out-')
            if any(cmd.startswith(p) for p in ps_cmds):
                cmd = f'powershell -Command "{cmd}"'

        elif os_type in (OS_LINUX, OS_MACOS):
            # Su Linux/macOS usa bash esplicito per sicurezza
            if not cmd.startswith(('bash', 'sh ', 'python', 'sudo')):
                pass   # bash è già il default della sessione SSH

        return cmd


# ── Rilevamento hardware remoto ───────────────────────────────────────────────
def _probe_linux(session: SSHSession) -> dict:
    info = {}
    cmds = {
        "os":       "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
        "kernel":   "uname -r",
        "cpu":      "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2",
        "ram":      "free -m | awk '/^Mem:/{print $2\" MiB total, \"$7\" MiB free\"}'",
        "hostname": "hostname",
        "arch":     "uname -m",
        "disk":     "df -h / | awk 'NR==2{print $2\" total, \"$4\" free\"}'",
    }
    for key, cmd in cmds.items():
        rc, out = session.run(cmd, timeout=10)
        info[key] = out.strip() if rc == 0 else "n/d"
    return info


def _probe_windows(session: SSHSession) -> dict:
    info = {}
    cmds = {
        "os":       "wmic os get Caption /value",
        "cpu":      "wmic cpu get Name /value",
        "ram":      "wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /value",
        "hostname": "hostname",
        "arch":     "wmic os get OSArchitecture /value",
        "disk":     "wmic logicaldisk where DeviceID='C:' get Size,FreeSpace /value",
    }
    for key, cmd in cmds.items():
        rc, out = session.run(cmd, timeout=10)
        # Estrai il valore dalla riga "Key=Value"
        if rc == 0:
            for line in out.splitlines():
                if "=" in line:
                    info[key] = line.split("=", 1)[1].strip()
                    break
            else:
                info[key] = out.strip()[:60]
        else:
            info[key] = "n/d"
    return info


def _detect_os(session: SSHSession) -> str:
    """Rileva l'OS dell'host remoto."""
    rc, out = session.run("uname -s", timeout=8)
    if rc == 0:
        out_l = out.lower()
        if "linux" in out_l:   return OS_LINUX
        if "darwin" in out_l:  return OS_MACOS
    # Prova Windows
    rc2, out2 = session.run("ver", timeout=8)
    if rc2 == 0 and "windows" in out2.lower():
        return OS_WINDOWS
    return OS_UNKNOWN


def probe_host(session: SSHSession) -> tuple[str, dict]:
    """
    Rileva OS e raccoglie info hardware dell'host remoto.
    Ritorna (os_type, hw_info).
    """
    os_type = _detect_os(session)
    if os_type == OS_LINUX:
        hw = _probe_linux(session)
    elif os_type == OS_WINDOWS:
        hw = _probe_windows(session)
    elif os_type == OS_MACOS:
        hw = _probe_linux(session)   # macOS comandi simili a Linux
    else:
        hw = {}
    return os_type, hw


# ══════════════════════════════════════════════════════════════════════════════
# SSHManager — gestisce tutti gli host e la connessione attiva
# ══════════════════════════════════════════════════════════════════════════════
class SSHManager:
    """
    Gestisce l'inventario host e la sessione SSH attiva.
    Una sola sessione attiva alla volta (multi-host parallelo = futuro).
    """

    def __init__(self, config_path: str = "~/.jarvis/ssh_hosts.json"):
        self._config_path = Path(config_path).expanduser().resolve()
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._hosts:   dict[str, RemoteHost] = {}    # name → RemoteHost
        self._session: Optional[SSHSession]  = None  # sessione attiva
        self._active_name: str = ""                  # nome host attivo
        self._load()

    # ── Inventario ────────────────────────────────────────────────────────────
    def _load(self):
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
                for h in data:
                    rh = RemoteHost(h)
                    self._hosts[rh.name] = rh
            except Exception:
                pass

    def _save(self):
        data = [h.to_dict() for h in self._hosts.values()]
        self._config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_host(self, host: RemoteHost):
        self._hosts[host.name] = host
        self._save()

    def remove_host(self, name: str) -> bool:
        if name not in self._hosts:
            return False
        if self._active_name == name:
            self.disconnect()
        del self._hosts[name]
        self._save()
        return True

    def list_hosts(self) -> list[RemoteHost]:
        return list(self._hosts.values())

    def get_host(self, name: str) -> Optional[RemoteHost]:
        return self._hosts.get(name)

    # ── Connessione ───────────────────────────────────────────────────────────
    def connect(self, name: str) -> tuple[bool, str]:
        """Connette all'host specificato, raccoglie info hardware."""
        if name not in self._hosts:
            return False, f"Host '{name}' non trovato. Usa /host list per vedere gli host."

        # Disconnette eventuale sessione precedente
        if self._session and self._session.is_alive():
            self._session.disconnect()

        host    = self._hosts[name]
        session = SSHSession(host)
        ok, msg = session.connect()
        if not ok:
            return False, msg

        # Rileva OS e hardware se non già fatto
        if host.os_type == OS_UNKNOWN or not host.hw_info:
            print(f"  Rilevamento OS e hardware di {name}...")
            os_type, hw = probe_host(session)
            host.os_type = os_type
            host.hw_info = hw
            # Imposta directory di default
            if not host.cwd:
                if os_type == OS_WINDOWS:
                    _, out = session.run("cd", timeout=5)
                    host.cwd = out.strip() or "C:\\Users\\" + host.user
                else:
                    _, out = session.run("echo $HOME", timeout=5)
                    host.cwd = out.strip() or f"/home/{host.user}"
            self._save()

        self._session     = session
        self._active_name = name
        return True, msg

    def disconnect(self):
        if self._session:
            self._session.disconnect()
        self._session     = None
        self._active_name = ""

    def is_connected(self) -> bool:
        return bool(self._session and self._session.is_alive())

    @property
    def active_host(self) -> Optional[RemoteHost]:
        if self._active_name:
            return self._hosts.get(self._active_name)
        return None

    # ── Esecuzione remota ─────────────────────────────────────────────────────
    def run_remote(self, command: str, timeout: int = 60) -> tuple[int, str]:
        """Esegue un comando sull'host attivo."""
        if not self.is_connected():
            return -1, "❌ Nessun host connesso"
        return self._session.run(command, timeout=timeout)

    # ── Context per JARVIS (system prompt) ────────────────────────────────────
    def system_context(self) -> str:
        """
        Ritorna una stringa da iniettare nel system prompt di JARVIS
        con la lista degli host e le info sull'host attivo.
        """
        if not self._hosts:
            return ""

        lines = ["Host SSH disponibili:"]
        for h in self._hosts.values():
            status = "CONNESSO" if h.name == self._active_name else "offline"
            os_name = {"linux": "Linux", "windows": "Windows",
                       "macos": "macOS"}.get(h.os_type, "?")
            hw = h.hw_info
            cpu = hw.get("cpu", "?")[:40].strip() if hw else "?"
            ram = hw.get("ram", "?")[:30].strip() if hw else "?"
            os_label = hw.get("os", os_name)[:40].strip() if hw else os_name
            cwd = h.cwd or "?"
            lines.append(
                f"  [{h.name}] {h.host} | {os_label} | CPU:{cpu} | RAM:{ram} | cwd:{cwd} | {status}"
            )

        if self._active_name:
            ah = self._hosts[self._active_name]
            lines.append(
                f"\nHost attivo: {ah.name} ({ah.os_type}) — "
                f"i comandi destinati a questo host vanno eseguiti in remoto via SSH. "
                f"Per i comandi destinati al PC locale usa target=locale."
            )

        lines.append(
            "\nRegola: per ogni comando decidi 'target': 'locale' o il nome dell'host remoto. "
            "Adatta la sintassi all'OS: Linux=bash, Windows=cmd/PowerShell."
        )
        return "\n".join(lines)

    # ── Gestione /host da terminale ───────────────────────────────────────────
    def cmd_add_interactive(self) -> Optional[RemoteHost]:
        """
        Guida interattiva per aggiungere un nuovo host.
        Ritorna il RemoteHost aggiunto, o None se annullato.
        """
        print()
        print("  Aggiungi nuovo host SSH")
        print("  ─────────────────────────")

        name = input("  Nome breve (es. server-casa): ").strip().lower()
        if not name:
            print("  Annullato.")
            return None
        if name in self._hosts:
            print(f"  Host '{name}' già esistente.")
            sovrascr = input("  Sovrascrivere? (s/N): ").strip().lower()
            if sovrascr != 's':
                return None

        host_addr = input("  Indirizzo IP o hostname: ").strip()
        if not host_addr:
            print("  Annullato.")
            return None

        port_in = input("  Porta SSH [22]: ").strip()
        port    = int(port_in) if port_in.isdigit() else 22

        user = input(f"  Utente [{os.getenv('USER','user')}]: ").strip()
        if not user:
            user = os.getenv("USER", "user")

        print("  Autenticazione:")
        print("    1. Password")
        print("    2. Chiave SSH (~/.ssh/id_rsa o altra)")
        auth_choice = input("  Scelta [1]: ").strip()

        password = ""
        key_path = ""
        if auth_choice == "2":
            auth = "key"
            key_in = input("  Percorso chiave [~/.ssh/id_rsa]: ").strip()
            key_path = key_in if key_in else "~/.ssh/id_rsa"
        else:
            auth = "password"
            import getpass
            password = getpass.getpass("  Password SSH: ")

        rh = RemoteHost({
            "name":     name,
            "host":     host_addr,
            "port":     port,
            "user":     user,
            "auth":     auth,
            "password": password,
            "key_path": key_path,
        })
        self.add_host(rh)
        print(f"\n  Host '{name}' salvato.")

        # Testa connessione
        test = input("  Testa connessione ora? (S/n): ").strip().lower()
        if test != 'n':
            print(f"  Connessione a {user}@{host_addr}:{port}...")
            ok, msg = self.connect(name)
            if ok:
                ah = self._hosts[name]
                print(f"  {msg}")
                print(f"  OS rilevato: {ah.os_type}")
                if ah.hw_info:
                    print(f"  OS:  {ah.hw_info.get('os','?')}")
                    print(f"  CPU: {ah.hw_info.get('cpu','?')}")
                    print(f"  RAM: {ah.hw_info.get('ram','?')}")
                disc = input("\n  Vuoi restare connesso? (S/n): ").strip().lower()
                if disc == 'n':
                    self.disconnect()
                    print("  Disconnesso.")
            else:
                print(f"  {msg}")
        return rh

    def cmd_list(self) -> str:
        """Ritorna la lista host formattata per il terminale."""
        if not self._hosts:
            return "  Nessun host configurato. Usa /host add per aggiungerne uno."
        lines = ["\n  Host SSH configurati:\n"]
        for h in self._hosts.values():
            lines.append(h.display_line(self._active_name))
        if self._active_name:
            ah = self._hosts[self._active_name]
            hw = ah.hw_info
            lines.append(f"\n  Host attivo: {ah.name}")
            lines.append(f"    OS:  {hw.get('os','?')}")
            lines.append(f"    CPU: {hw.get('cpu','?')}")
            lines.append(f"    RAM: {hw.get('ram','?')}")
            lines.append(f"    Dir: {ah.cwd}")
        return "\n".join(lines)

    def cmd_remove_interactive(self) -> str:
        """Guida interattiva per rimuovere un host."""
        hosts = self.list_hosts()
        if not hosts:
            return "  Nessun host da rimuovere."
        print()
        for i, h in enumerate(hosts, 1):
            print(f"    {i}. {h.name} ({h.host})")
        print()
        sel = input("  Numero da rimuovere (invio per annullare): ").strip()
        if not sel:
            return "  Annullato."
        try:
            idx = int(sel) - 1
            if not 0 <= idx < len(hosts):
                raise ValueError
        except ValueError:
            return "  Numero non valido."
        name = hosts[idx].name
        conf = input(f"  Rimuovere '{name}'? (s/N): ").strip().lower()
        if conf != 's':
            return "  Annullato."
        self.remove_host(name)
        return f"  Host '{name}' rimosso."
