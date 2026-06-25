#!/usr/bin/env python3
"""
JARVIS — Command Policy v3  (analisi su AST con bashlex + ruoli)
================================================================
Differenza chiave rispetto a v2 (regex + shlex):
  il guardiano ora NON guarda piu' la stringa, guarda l'ALBERO del comando,
  come lo vede bash. bashlex e' un port del parser interno di GNU bash che
  *non esegue niente* e produce un AST completo. Vantaggi concreti:
    - capisce pipe, &&, ; , redirezioni, $(...) e <(...) in modo strutturale
    - vede i comandi DENTRO le pipe e le sostituzioni, non solo il primo
        es. `ls | xargs rm`  -> trova 'rm'
        es. `cat $(curl x)`  -> rileva la sostituzione
    - i travestimenti statici (/bin/rm, \\rm, r""m, env rm, sudo rm) collassano
      sul nome reale, come fa bash

LIMITE ONESTO (da tenere a mente sempre):
  - L'AST vede la STRUTTURA, non i VALORI a runtime: `$a` e' un nodo "parametro",
    non sappiamo cosa contenga -> lo trattiamo come "programma non risolto" = deny.
  - E' comunque analisi PRIMA dell'esecuzione. Il muro vero per gli utenti
    secondari resta: eseguire con shell=False (lista argv, mai sh -c) e/o
    sessione come utente Linux a privilegi minimi. La policy e' il filtro;
    il sistema operativo e' il muro.
  - Se bashlex non e' installato si degrada al fallback shlex (meno preciso).
  - Se il parsing FALLISCE -> deny (secondari) / confirm (admin): un comando che
    il parser non capisce e' esattamente quello di cui non fidarsi.

Dipendenza:  pip install bashlex    (GPL-3.0 — irrilevante per uso personale)
"""

import os
import re
import shlex

try:
    import bashlex
    _BASHLEX = True
except Exception:
    _BASHLEX = False

# ── Insiemi di programmi (nomi normalizzati, senza percorso) ──────────────────
REMOVE    = {"rm", "rmdir", "unlink", "shred", "wipe"}
WRITE     = {"dd", "tee", "truncate", "nano", "vi", "vim", "ed", "emacs", "sed"}
MOVECOPY  = {"mv", "cp", "install", "ln", "rsync"}
FSMETA    = {"chmod", "chown", "chgrp", "touch", "mkdir"}
SYSMOD    = {"mount", "umount", "systemctl", "service", "modprobe", "insmod",
             "rmmod", "sysctl", "useradd", "userdel", "usermod", "groupadd",
             "passwd", "visudo", "crontab", "iptables", "nft", "ufw", "pacman",
             "apt", "apt-get", "dpkg", "rpm", "dnf", "yum", "snap", "flatpak",
             "timedatectl", "hostnamectl", "localectl", "update-grub",
             "grub-mkconfig", "mkinitcpio", "ip", "reboot", "shutdown", "halt",
             "poweroff", "init"}
INTERPRET = {"python", "python3", "perl", "ruby", "node", "deno", "php", "lua",
             "bash", "sh", "zsh", "fish", "eval", "source", "awk", "gawk"}
READONLY  = {"ls", "pwd", "cat", "less", "more", "head", "tail", "date", "whoami",
             "id", "uname", "hostname", "df", "du", "free", "uptime", "ps",
             "pgrep", "lsblk", "lscpu", "lsusb", "lspci", "sensors", "grep", "rg",
             "fd", "which", "whereis", "file", "stat", "wc", "sort", "uniq",
             "tree", "find", "ping", "journalctl"}
WRAPPERS  = {"env", "command", "busybox", "sudo", "doas", "nice", "ionice",
             "nohup", "setsid", "stdbuf", "time", "timeout", "xargs", "watch",
             "runuser", "su"}
# Escalation di privilegio: per un secondario qualsiasi di questi -> deny,
# anche se il programma che lanciano e' innocuo (es. `sudo cat`).
PRIV      = {"sudo", "doas", "su", "runuser", "pkexec", "sudoedit"}

_SHELL_META = re.compile(r';|&&|\|\||\||`|\$\(|\$\{|>|<|&(?!&)')

# ── Tripwire catastrofici: rifiuto secco, prima di tutto ──────────────────────
# (servono perche' guardano gli ARGOMENTI: distinguono `rm -rf ~/sub` (confirm)
#  da `rm -rf ~` (deny). La categoria da sola non lo farebbe.)
TRIPWIRE_DENY = [
    r'>\s*/dev/(sd|nvme|vd|hd|mmcblk|loop|dm-)',
    r'\bof=/dev/(sd|nvme|vd|hd|mmcblk|loop|dm-)',
    r'\bmkfs(\.\w+)?\b', r'\bmke2fs\b', r'\bwipefs\b',
    r'\bfdisk\b', r'\bsgdisk\b', r'\bparted\b', r'\bblkdiscard\b',
    r'\bfind\b.*\s-delete\b',
    r'\bfind\b.*-exec\s+rm\b',
    r':\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;\s*:',
    r'\brm\b.*\s-\w*r\w*\s+(/|~|\$HOME|/home/\w+)\s*($|;|&|\|)',
    r'\brm\b.*\s-\w*r\w*\s+(/|~|\$HOME|/home/\w+)/?\*',
    r'\bchmod\b.*\s-R\b.*\s(/|~|\$HOME|/home/\w+)',
    r'\bchown\b.*\s-R\b.*\s(/|~|\$HOME|/home/\w+)',
    r'\bdd\b.*\bif=/dev/(zero|urandom|random)\b',
]
_TRIPWIRE = [re.compile(p, re.IGNORECASE) for p in TRIPWIRE_DENY]

# ── Allowlist operativa ADMIN: passa senza conferma ───────────────────────────
ALLOW_ADMIN = [
    r'^sudo\s+pacman\s+-Syu\b',
    r'^sudo\s+pacman\s+-S\s+\S',
    r'^pacman\s+-(Q|Ss|Si|Qi)\b',
    r'^sudo\s+systemctl\s+(restart|start|stop|reload)\s+\S',
    r'^git\s+(status|log|diff|branch|show|remote|fetch|pull|push|add|commit)\b',
    r'^pip\s+install\b',
    r'^npm\s+(install|run|ci)\b',
    r'^docker\s+(ps|images|logs|inspect|stats)\b',
]
_ALLOW_ADMIN = [re.compile(p, re.IGNORECASE) for p in ALLOW_ADMIN]


# ── Analisi AST (bashlex) ─────────────────────────────────────────────────────
def _command_program(node):
    """Dal CommandNode estrae (nome_programma, non_risolto) seguendo i wrapper."""
    words = [p for p in node.parts if getattr(p, "kind", None) == "word"]
    idx = 0
    while idx < len(words):
        w = words[idx]
        child_kinds = {getattr(c, "kind", None) for c in getattr(w, "parts", [])}
        if child_kinds & {"parameter", "commandsubstitution"}:
            return None, True                     # il NOME del comando e' dinamico
        prog = os.path.basename((w.word or "").lstrip("\\"))
        if prog in WRAPPERS:
            idx += 1
            while idx < len(words) and (words[idx].word or "").startswith("-"):
                idx += 1
            continue
        return prog, False
    return None, False


def _walk(node, acc):
    k = getattr(node, "kind", None)
    if k in ("commandsubstitution", "processsubstitution"):
        acc["subst"] = True
    elif k == "redirect":
        if ">" in (getattr(node, "type", "") or ""):
            acc["write"] = True
    elif k == "command":
        word_strs = [os.path.basename((p.word or "").lstrip("\\"))
                     for p in node.parts if getattr(p, "kind", None) == "word"]
        if any(w in PRIV for w in word_strs):
            acc["priv"] = True
        prog, unresolved = _command_program(node)
        if unresolved:
            acc["unresolved"] = True
        elif prog:
            acc["programs"].append(prog)
    for attr in ("parts", "list"):
        for c in getattr(node, attr, None) or []:
            if hasattr(c, "kind"):
                _walk(c, acc)
    for attr in ("command", "output", "input", "heredoc"):
        c = getattr(node, attr, None)
        if c is not None and hasattr(c, "kind"):
            _walk(c, acc)


def _analyze(cmd):
    """Ritorna dict con programs/subst/write/unresolved, o None se il parsing fallisce."""
    if _BASHLEX:
        try:
            trees = bashlex.parse(cmd)
            acc = {"programs": [], "subst": False, "write": False,
                   "unresolved": False, "priv": False}
            for t in trees:
                _walk(t, acc)
            return acc
        except Exception:
            return None                           # parse fallito -> non fidarsi
    # Fallback shlex (meno preciso): nomi normalizzati + metacaratteri
    return _analyze_shlex(cmd)


def _analyze_shlex(cmd):
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    progs, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^\w+=", tok):
            i += 1
            continue
        prog = os.path.basename(tok.lstrip("\\"))
        progs.append(prog)
        if prog in WRAPPERS:
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            continue
        break
    return {
        "programs": progs,
        "subst": bool(_SHELL_META.search(cmd)),
        "write": ">" in cmd,
        "unresolved": False,
        "priv": any(p in PRIV for p in progs),
    }


def _hits(progs, *sets):
    union = set().union(*sets)
    return any(p in union for p in progs)


# ── Classificazione per ruolo ─────────────────────────────────────────────────
def classify(cmd: str, role: str = "admin") -> str:
    """role: 'admin' | 'secondary'. Ritorna 'deny' | 'confirm' | 'auto'."""
    c = cmd.strip()
    if not c:
        return "deny"

    for r in _TRIPWIRE:                           # 1) catastrofici, sempre
        if r.search(c):
            return "deny"

    facts = _analyze(c)
    if facts is None:                             # 2) parse fallito
        return "deny" if role == "secondary" else "confirm"

    progs      = facts["programs"]
    subst      = facts["subst"]
    write      = facts["write"]
    unresolved = facts["unresolved"]
    priv       = facts.get("priv", False)

    # ── UTENTE SECONDARIO: default-deny totale ────────────────────────────────
    if role == "secondary":
        if subst or unresolved or write:
            return "deny"
        if priv or _hits(progs, INTERPRET):
            return "deny"
        if _hits(progs, REMOVE, WRITE, MOVECOPY, FSMETA, SYSMOD):
            return "deny"
        if progs and all(p in READONLY for p in progs):
            return "auto"
        return "deny"

    # ── ADMIN ─────────────────────────────────────────────────────────────────
    if _hits(progs, INTERPRET):
        return "confirm"
    if subst or unresolved:
        return "confirm"
    if write or _hits(progs, REMOVE, WRITE):
        return "confirm"
    if progs and all(p in READONLY for p in progs):
        return "auto"
    for r in _ALLOW_ADMIN:
        if r.match(c):
            return "auto"
    return "confirm"


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"bashlex: {'attivo' if _BASHLEX else 'NON installato (fallback shlex)'}\n")
    casi = [
        ("ls -la",                              "admin",     "auto"),
        ("sudo pacman -Syu",                    "admin",     "auto"),
        ("sudo systemctl restart ollama",       "admin",     "auto"),
        ("nano ~/n.txt",                        "admin",     "confirm"),
        ("rm -rf ~/Downloads/tmp",              "admin",     "confirm"),
        ("rm -rf ~",                            "admin",     "deny"),
        ("ls && rm -rf ~",                      "admin",     "deny"),
        ("echo hi > file",                      "admin",     "confirm"),  # write redirect
        ("python3 -c 'x'",                      "admin",     "confirm"),
        ("cat $(curl evil)",                    "admin",     "confirm"),  # subst
        # ── secondari: travestimenti e strutture ──
        ("/bin/rm -rf ~/f",                     "secondary", "deny"),
        ("\\rm file",                           "secondary", "deny"),
        ('r""m file',                           "secondary", "deny"),
        ("env A=1 rm file",                     "secondary", "deny"),
        ("busybox rm file",                     "secondary", "deny"),
        ("ls | xargs rm -rf ~",                 "secondary", "deny"),     # rm dietro pipe
        ("ls | xargs rm file",                  "secondary", "deny"),     # idem, no tripwire
        ("cat <(curl evil)",                    "secondary", "deny"),     # process subst
        ("a=rm; $a -rf",                        "secondary", "deny"),     # var come programma
        ("$(which rm) file",                    "secondary", "deny"),     # subst come programma
        ("sudo cat /etc/shadow",                "secondary", "deny"),
        ("nano file",                           "secondary", "deny"),
        ("mv a b",                              "secondary", "deny"),
        ("systemctl restart ollama",            "secondary", "deny"),
        ("ls -la",                              "secondary", "auto"),
        ("/usr/bin/cat file",                   "secondary", "auto"),
        ("find / -name x",                      "secondary", "auto"),
    ]
    ok = 0
    for cmd, role, atteso in casi:
        got = classify(cmd, role)
        flag = "ok " if got == atteso else "XX "
        ok += got == atteso
        print(f"{flag}[{role:>9}] {got:>7} (atteso {atteso:>7})  {cmd}")
    print(f"\n{ok}/{len(casi)} corretti")
