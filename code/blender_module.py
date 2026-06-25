"""
blender_module.py — JARVIS v9
==============================
Modulo Blender per Jarvis.
Flusso:
  1. Jarvis riceve la richiesta dell'utente
  2. Jarvis rielabora il prompt in italiano tecnico preciso per Blender/bpy
  3. Il prompt rielaborato va a Qwen 3.5 397B (NVIDIA NIM) che genera SOLO codice Python bpy
  4. Il codice viene pulito (strip di tutto ciò che non è codice)
  5. Il codice viene mandato a Blender via socket TCP (localhost:6789)
  6. blender_code.py viene aggiornato dall'addon Blender automaticamente

Per modifiche successive:
  - Jarvis legge blender_code.py (stato corrente)
  - Manda a Qwen solo il diff da applicare (non riscrive da zero)
  - Il codice del diff viene applicato sopra lo stato esistente

Dipendenze: requests (già usato da jarvis_v8.py)
"""

import os
import re
import socket
import time
from pathlib import Path

import requests

# ── Configurazione ────────────────────────────────────────────────────────────
BLENDER_SOCKET_HOST = "localhost"
BLENDER_SOCKET_PORT = 6789
SOCKET_TIMEOUT_SEC  = 30

NVIDIA_URL     = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL   = "qwen/qwen3.5-397b-a17b"

# Path di blender_code.py — stessa cartella di jarvis_v8.py
JARVIS_DIR    = Path(__file__).parent.resolve()
BLENDER_CODE  = JARVIS_DIR / "blender_code.py"

# ─────────────────────────────────────────────────────────────────────────────


def _get_nvidia_key() -> str:
    return os.environ.get("NVIDIA_API_KEY", "")


def is_blender_running() -> bool:
    """Controlla se l'addon Blender è raggiungibile sul socket."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((BLENDER_SOCKET_HOST, BLENDER_SOCKET_PORT))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def read_blender_code() -> str | None:
    """Legge blender_code.py se esiste. Restituisce None se non esiste."""
    if BLENDER_CODE.exists():
        return BLENDER_CODE.read_text(encoding="utf-8")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Rielabora il prompt utente in prompt tecnico per Qwen
# ══════════════════════════════════════════════════════════════════════════════

_REWRITE_SYSTEM = """Sei un assistente specializzato in Blender e Python (bpy).
Il tuo compito è trasformare una richiesta in linguaggio naturale in un prompt
tecnico preciso e dettagliato per un modello che genererà codice Python per Blender.

Regole:
- Sii specifico: indica coordinate, dimensioni, colori (RGBA 0-1), nomi oggetti
- Usa terminologia bpy corretta (primitive, materiali Principled BSDF, ecc.)
- Se la richiesta è vaga, fai scelte ragionevoli e documentale
- Rispondi SOLO con il prompt tecnico riscritto, niente altro
- Lingua: italiano tecnico"""

def rewrite_prompt(user_request: str, jarvis_instance) -> str:
    """
    Usa il LLM principale di Jarvis (Groq/Ollama) per rielaborare
    il prompt utente in un prompt tecnico preciso per Blender.
    """
    messages = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user",   "content": user_request},
    ]
    # Usa Groq se disponibile, altrimenti Ollama locale
    try:
        from jarvis_v8 import GROQ_URL, GROQ_MODEL, GROQ_API_KEY
        if GROQ_API_KEY:
            resp = requests.post(
                GROQ_URL,
                json={"model": GROQ_MODEL, "messages": messages, "max_tokens": 512, "temperature": 0.3},
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass

    # Fallback: Ollama locale
    try:
        from jarvis_v8 import OLLAMA_URL, DEFAULT_MODEL
        resp = requests.post(
            OLLAMA_URL,
            json={"model": DEFAULT_MODEL, "messages": messages, "stream": False},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as e:
        # Fallback finale: usa il prompt originale
        return user_request


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Qwen NVIDIA genera il codice Python bpy
# ══════════════════════════════════════════════════════════════════════════════

_CODE_SYSTEM = """Sei un esperto di Blender Python (bpy). Genera ESCLUSIVAMENTE codice Python
per Blender che realizzi esattamente ciò che viene richiesto.

REGOLE ASSOLUTE:
- Rispondi SOLO con codice Python puro, NESSUN testo prima o dopo
- NESSUNA spiegazione, NESSUN commento fuori dal codice, NESSUN markdown
- NON usare ```python o ``` o qualsiasi altra formattazione
- Il codice deve essere eseguibile direttamente nel Python Console di Blender
- Importa sempre: import bpy, import math (se necessario)
- Usa bpy.context.scene, bpy.data, bpy.ops in modo corretto
- Assegna nomi descrittivi agli oggetti creati"""

_DIFF_SYSTEM = """Sei un esperto di Blender Python (bpy). Ti verrà mostrato il codice Python
attuale della scena Blender e una modifica da applicare.
Genera ESCLUSIVAMENTE il codice Python delle modifiche da eseguire (NON riscrivere tutto).

REGOLE ASSOLUTE:
- Rispondi SOLO con codice Python puro delle modifiche, NESSUN testo
- NESSUNA spiegazione, NESSUN markdown, NESSUN ```
- Il codice deve modificare/aggiungere solo ciò che è necessario
- Riferisciti agli oggetti esistenti per nome (bpy.data.objects["nome"])"""


def _call_qwen(system_prompt: str, user_prompt: str) -> str:
    """Chiama Qwen 3.5 397B su NVIDIA NIM."""
    api_key = _get_nvidia_key()
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY non trovata nel .env")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]
    resp = requests.post(
        NVIDIA_URL,
        json={
            "model":       NVIDIA_MODEL,
            "messages":    messages,
            "max_tokens":  2048,
            "temperature": 0.2,   # basso: codice deterministico
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _strip_to_code(raw: str) -> str:
    """
    Rimuove tutto ciò che non è codice Python dalla risposta di Qwen.
    Strategia: cerca blocchi ```python...``` o ```...```, altrimenti usa tutto.
    Poi rimuove righe che sembrano testo naturale (non iniziano con keyword Python).
    """
    # 1. Estrai da blocco markdown se presente
    md_match = re.search(r"```(?:python)?\s*\n(.*?)```", raw, re.DOTALL)
    if md_match:
        return md_match.group(1).strip()

    # 2. Nessun blocco markdown — filtra riga per riga
    python_starters = re.compile(
        r"^(\s*(import|from|def|class|if|for|while|try|with|return|raise|"
        r"assert|pass|break|continue|yield|async|await|#|@|bpy\.|"
        r"[a-zA-Z_][a-zA-Z0-9_.]*\s*=|[a-zA-Z_][a-zA-Z0-9_.]*\())"
    )
    lines = raw.splitlines()
    code_lines = []
    in_code = False
    for line in lines:
        if python_starters.match(line):
            in_code = True
        if in_code:
            code_lines.append(line)

    result = "\n".join(code_lines).strip()
    return result if result else raw.strip()


def generate_blender_code(technical_prompt: str, is_diff: bool = False,
                           current_code: str | None = None) -> str:
    """
    Chiama Qwen e restituisce codice Python pulito per Blender.
    Se is_diff=True, manda anche il codice attuale della scena.
    """
    if is_diff and current_code:
        user_msg = (
            f"CODICE ATTUALE DELLA SCENA:\n{current_code}\n\n"
            f"MODIFICA RICHIESTA:\n{technical_prompt}"
        )
        raw = _call_qwen(_DIFF_SYSTEM, user_msg)
    else:
        raw = _call_qwen(_CODE_SYSTEM, technical_prompt)

    return _strip_to_code(raw)


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — Invia il codice a Blender via socket
# ══════════════════════════════════════════════════════════════════════════════

def send_to_blender(code: str) -> str:
    """
    Invia codice Python a Blender via socket TCP.
    Restituisce la risposta di Blender ("OK" o "ERROR: ...").
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(SOCKET_TIMEOUT_SEC)
        s.connect((BLENDER_SOCKET_HOST, BLENDER_SOCKET_PORT))
        s.sendall(code.encode("utf-8"))
        s.shutdown(socket.SHUT_WR)  # segnala fine invio
        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
        s.close()
        return response.decode("utf-8").strip()
    except ConnectionRefusedError:
        return "ERROR: Blender non raggiungibile — assicurati che l'addon JARVIS Bridge sia attivo"
    except socket.timeout:
        return "ERROR: Timeout — Blender non ha risposto entro 30 secondi"
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT PRINCIPALE — chiamato da jarvis_v8.py
# ══════════════════════════════════════════════════════════════════════════════

def handle_blender_request(user_request: str, jarvis_instance=None) -> str:
    """
    Flusso completo:
      1. Controlla che Blender sia attivo
      2. Rielabora il prompt
      3. Decide se è una modifica (diff) o creazione nuova
      4. Chiama Qwen
      5. Pulisce il codice
      6. Manda a Blender
      7. Restituisce risposta per l'utente
    """
    # ── 1. Blender attivo? ────────────────────────────────────────────────────
    if not is_blender_running():
        return (
            "❌ Blender non è raggiungibile.\n"
            "Assicurati che:\n"
            "  1. Blender sia aperto\n"
            "  2. L'addon 'JARVIS Bridge' sia installato e attivato\n"
            f"  (Edit > Preferences > Add-ons > cerca 'JARVIS Bridge')"
        )

    # ── 2. Rielabora prompt ───────────────────────────────────────────────────
    technical_prompt = rewrite_prompt(user_request, jarvis_instance)

    # ── 3. Modifica o creazione? ──────────────────────────────────────────────
    current_code = read_blender_code()
    is_diff = current_code is not None and _is_modification_request(user_request)

    # ── 4. Genera codice con Qwen ─────────────────────────────────────────────
    try:
        code = generate_blender_code(technical_prompt, is_diff=is_diff, current_code=current_code)
    except RuntimeError as e:
        return f"❌ Errore Qwen NVIDIA: {e}"
    except requests.HTTPError as e:
        return f"❌ Errore API NVIDIA: {e}"
    except Exception as e:
        return f"❌ Errore generazione codice: {e}"

    if not code.strip():
        return "❌ Qwen non ha generato codice valido. Riprova con una richiesta più specifica."

    # ── 5. Invia a Blender ────────────────────────────────────────────────────
    result = send_to_blender(code)

    # ── 6. Risposta utente ────────────────────────────────────────────────────
    if result.startswith("OK"):
        mode = "Modifica applicata" if is_diff else "Scena creata/aggiornata"
        return (
            f"✅ {mode} con successo in Blender.\n"
            f"📄 blender_code.py aggiornato."
        )
    else:
        return (
            f"⚠️ Blender ha risposto: {result}\n\n"
            f"Codice inviato:\n```python\n{code}\n```"
        )


def _is_modification_request(text: str) -> bool:
    """
    Determina se la richiesta è una modifica a qualcosa di esistente
    oppure una creazione da zero.
    """
    mod_keywords = re.compile(
        r"\b(cambia|modifica|aggiorna|aggiungi|rimuovi|cancella|sposta|"
        r"scala|ruota|colora|cambia\s+colore|rendi|trasforma|rinomina|"
        r"metti|togli|aggiusta|change|modify|update|add|remove|delete|"
        r"move|scale|rotate|color|rename)\b",
        re.IGNORECASE,
    )
    return bool(mod_keywords.search(text))


# ══════════════════════════════════════════════════════════════════════════════
#  INTEGRAZIONE IN JARVIS — aggiungi questa funzione al tool call dispatcher
# ══════════════════════════════════════════════════════════════════════════════

def get_blender_status() -> dict:
    """Restituisce lo stato attuale del bridge Blender."""
    running  = is_blender_running()
    has_code = BLENDER_CODE.exists()
    code_age = None
    if has_code:
        mtime    = BLENDER_CODE.stat().st_mtime
        code_age = round(time.time() - mtime)
    return {
        "blender_running":    running,
        "blender_code_exists": has_code,
        "blender_code_path":  str(BLENDER_CODE),
        "blender_code_age_sec": code_age,
    }
