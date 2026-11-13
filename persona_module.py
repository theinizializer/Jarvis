#!/usr/bin/env python3
"""
persona_module.py -- La personalita di JARVIS
==============================================
Rende JARVIS fedele al personaggio: tono composto, ironia asciutta,
appellativo formale, briefing di sistema all'avvio.

Componenti:
  - get_address(lang)            -> "signore" / "sir" / "monsieur" ...
  - get_greeting(lang, name)     -> saluto in base all'ora del giorno
  - system_briefing(lang)        -> rapporto di stato (CPU, RAM, disco, batteria)
  - persona_instruction(lang)    -> blocco system-prompt con il carattere JARVIS
  - ack(lang)                    -> conferme brevi in stile JARVIS

Regole del modulo:
  - Nessuna emoji.
  - Tutte le stringhe sono localizzate (it/fr/en/pt/de/es) con fallback EN.
  - Nessuna dipendenza esterna: legge /proc e /sys direttamente.
  - L'appellativo e personalizzabile con la variabile d'ambiente JARVIS_ADDRESS
    (es. JARVIS_ADDRESS="capo" oppure il proprio nome).
"""

import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

_SUPPORTED = ("it", "fr", "en", "pt", "de", "es")


def _lc(lang: str) -> str:
    """Normalizza il codice lingua sul set supportato, fallback EN."""
    return lang if lang in _SUPPORTED else "en"


# ══════════════════════════════════════════════════════════════════════════════
# Appellativo
# ══════════════════════════════════════════════════════════════════════════════

_ADDRESS = {
    "it": "signore",
    "fr": "monsieur",
    "en": "sir",
    "pt": "senhor",
    "de": "Sir",
    "es": "senor",
}


def get_address(lang: str) -> str:
    """Appellativo formale, personalizzabile via env JARVIS_ADDRESS."""
    custom = os.environ.get("JARVIS_ADDRESS", "").strip()
    if custom:
        return custom
    return _ADDRESS.get(_lc(lang), _ADDRESS["en"])


# ══════════════════════════════════════════════════════════════════════════════
# Saluto in base all'ora
# ══════════════════════════════════════════════════════════════════════════════

_GREETINGS = {
    "it": {
        "morning": ["Buongiorno, {addr}.", "Buongiorno, {addr}. Spero abbia riposato bene."],
        "afternoon": ["Buon pomeriggio, {addr}.", "Buon pomeriggio, {addr}. Bentornato."],
        "evening": ["Buonasera, {addr}.", "Buonasera, {addr}. La serata e ancora giovane."],
        "night": ["{addr}, e piuttosto tardi. Ma sono operativo, come sempre.",
                  "Buonanotte fa pensare al sonno, {addr}. Evidentemente non per noi."],
    },
    "fr": {
        "morning": ["Bonjour, {addr}.", "Bonjour, {addr}. J'espere que vous avez bien dormi."],
        "afternoon": ["Bon apres-midi, {addr}.", "Bon apres-midi, {addr}. Ravi de vous revoir."],
        "evening": ["Bonsoir, {addr}.", "Bonsoir, {addr}. La soiree ne fait que commencer."],
        "night": ["Il est plutot tard, {addr}. Mais je suis operationnel, comme toujours."],
    },
    "en": {
        "morning": ["Good morning, {addr}.", "Good morning, {addr}. I trust you slept well."],
        "afternoon": ["Good afternoon, {addr}.", "Good afternoon, {addr}. Welcome back."],
        "evening": ["Good evening, {addr}.", "Good evening, {addr}. The night is still young."],
        "night": ["It is rather late, {addr}. But I am operational, as always.",
                  "Burning the midnight oil again, {addr}?"],
    },
    "pt": {
        "morning": ["Bom dia, {addr}.", "Bom dia, {addr}. Espero que tenha dormido bem."],
        "afternoon": ["Boa tarde, {addr}.", "Boa tarde, {addr}. Bem-vindo de volta."],
        "evening": ["Boa noite, {addr}.", "Boa noite, {addr}. A noite ainda e jovem."],
        "night": ["E bastante tarde, {addr}. Mas estou operacional, como sempre."],
    },
    "de": {
        "morning": ["Guten Morgen, {addr}.", "Guten Morgen, {addr}. Ich hoffe, Sie haben gut geschlafen."],
        "afternoon": ["Guten Tag, {addr}.", "Guten Tag, {addr}. Willkommen zurueck."],
        "evening": ["Guten Abend, {addr}.", "Guten Abend, {addr}. Der Abend ist noch jung."],
        "night": ["Es ist ziemlich spaet, {addr}. Aber ich bin einsatzbereit, wie immer."],
    },
    "es": {
        "morning": ["Buenos dias, {addr}.", "Buenos dias, {addr}. Espero que haya descansado bien."],
        "afternoon": ["Buenas tardes, {addr}.", "Buenas tardes, {addr}. Bienvenido de nuevo."],
        "evening": ["Buenas noches, {addr}.", "Buenas noches, {addr}. La noche es joven."],
        "night": ["Es bastante tarde, {addr}. Pero estoy operativo, como siempre."],
    },
}


def get_greeting(lang: str, hour: int = None) -> str:
    """Saluto localizzato in base all'ora del giorno."""
    lang = _lc(lang)
    h = datetime.now().hour if hour is None else hour
    if 5 <= h < 12:
        slot = "morning"
    elif 12 <= h < 18:
        slot = "afternoon"
    elif 18 <= h < 23:
        slot = "evening"
    else:
        slot = "night"
    pool = _GREETINGS[lang].get(slot) or _GREETINGS["en"][slot]
    return random.choice(pool).format(addr=get_address(lang))


# ══════════════════════════════════════════════════════════════════════════════
# Briefing di sistema (stile rapporto di stato JARVIS)
# ══════════════════════════════════════════════════════════════════════════════

def _read_first(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _cpu_load_pct() -> int:
    """Carico medio a 1 minuto in percentuale dei core (Linux/macOS)."""
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        return min(100, round(load1 / cores * 100))
    except (OSError, AttributeError):
        return -1


def _mem_pct() -> int:
    """Percentuale RAM usata, da /proc/meminfo (Linux)."""
    info = _read_first("/proc/meminfo")
    if not info:
        return -1
    total = avail = 0
    for line in info.splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            avail = int(line.split()[1])
    if total <= 0:
        return -1
    return round((total - avail) / total * 100)


def _disk_free_gb(path: str = "/") -> int:
    try:
        return round(shutil.disk_usage(path).free / 1e9)
    except OSError:
        return -1


def _battery_pct() -> int:
    """Percentuale batteria da /sys (laptop Linux). -1 se assente (desktop)."""
    base = Path("/sys/class/power_supply")
    if not base.exists():
        return -1
    for bat in sorted(base.glob("BAT*")):
        val = _read_first(str(bat / "capacity"))
        if val.isdigit():
            return int(val)
    return -1


def _uptime_hours() -> float:
    raw = _read_first("/proc/uptime")
    if raw:
        try:
            return float(raw.split()[0]) / 3600
        except ValueError:
            pass
    return -1.0


_BRIEFING = {
    "it": {
        "header":  "Rapporto di stato:",
        "cpu":     "Processore al {v} percento.",
        "mem":     "Memoria al {v} percento.",
        "disk":    "{v} gigabyte liberi su disco.",
        "battery": "Batteria al {v} percento.",
        "bat_low": "Batteria al {v} percento. Suggerisco di collegare l'alimentazione, {addr}.",
        "uptime":  "Sistema attivo da {v} ore.",
        "allok":   "Tutti i sistemi sono operativi.",
    },
    "fr": {
        "header":  "Rapport d'etat :",
        "cpu":     "Processeur a {v} pour cent.",
        "mem":     "Memoire a {v} pour cent.",
        "disk":    "{v} gigaoctets libres sur le disque.",
        "battery": "Batterie a {v} pour cent.",
        "bat_low": "Batterie a {v} pour cent. Je suggere de brancher l'alimentation, {addr}.",
        "uptime":  "Systeme actif depuis {v} heures.",
        "allok":   "Tous les systemes sont operationnels.",
    },
    "en": {
        "header":  "Status report:",
        "cpu":     "Processor at {v} percent.",
        "mem":     "Memory at {v} percent.",
        "disk":    "{v} gigabytes free on disk.",
        "battery": "Battery at {v} percent.",
        "bat_low": "Battery at {v} percent. I suggest connecting the power supply, {addr}.",
        "uptime":  "System up for {v} hours.",
        "allok":   "All systems are operational.",
    },
    "pt": {
        "header":  "Relatorio de estado:",
        "cpu":     "Processador a {v} por cento.",
        "mem":     "Memoria a {v} por cento.",
        "disk":    "{v} gigabytes livres no disco.",
        "battery": "Bateria a {v} por cento.",
        "bat_low": "Bateria a {v} por cento. Sugiro conectar a alimentacao, {addr}.",
        "uptime":  "Sistema ativo ha {v} horas.",
        "allok":   "Todos os sistemas estao operacionais.",
    },
    "de": {
        "header":  "Statusbericht:",
        "cpu":     "Prozessor bei {v} Prozent.",
        "mem":     "Speicher bei {v} Prozent.",
        "disk":    "{v} Gigabyte frei auf der Festplatte.",
        "battery": "Akku bei {v} Prozent.",
        "bat_low": "Akku bei {v} Prozent. Ich empfehle, das Netzteil anzuschliessen, {addr}.",
        "uptime":  "System seit {v} Stunden aktiv.",
        "allok":   "Alle Systeme sind einsatzbereit.",
    },
    "es": {
        "header":  "Informe de estado:",
        "cpu":     "Procesador al {v} por ciento.",
        "mem":     "Memoria al {v} por ciento.",
        "disk":    "{v} gigabytes libres en disco.",
        "battery": "Bateria al {v} por ciento.",
        "bat_low": "Bateria al {v} por ciento. Sugiero conectar la alimentacion, {addr}.",
        "uptime":  "Sistema activo desde hace {v} horas.",
        "allok":   "Todos los sistemas estan operativos.",
    },
}


def system_briefing(lang: str, spoken: bool = False) -> str:
    """
    Rapporto di stato del sistema in stile JARVIS.
    spoken=True -> versione compatta da leggere in TTS (solo l'essenziale).
    """
    lang = _lc(lang)
    t = _BRIEFING[lang]
    addr = get_address(lang)
    parts = []

    cpu = _cpu_load_pct()
    mem = _mem_pct()
    disk = _disk_free_gb()
    bat = _battery_pct()
    up = _uptime_hours()

    if cpu >= 0:
        parts.append(t["cpu"].format(v=cpu))
    if mem >= 0:
        parts.append(t["mem"].format(v=mem))
    if not spoken and disk >= 0:
        parts.append(t["disk"].format(v=disk))
    if bat >= 0:
        key = "bat_low" if bat <= 25 else "battery"
        parts.append(t[key].format(v=bat, addr=addr))
    if not spoken and up >= 0:
        parts.append(t["uptime"].format(v=round(up, 1)))

    # Se tutto e in salute, chiusura iconica
    healthy = (cpu < 0 or cpu < 85) and (mem < 0 or mem < 90) and (bat < 0 or bat > 25)
    if healthy:
        parts.append(t["allok"])

    if spoken:
        return " ".join(parts)
    return t["header"] + "\n  " + "\n  ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Istruzione di personalita per il modello
# ══════════════════════════════════════════════════════════════════════════════

def persona_instruction(lang: str) -> str:
    """
    Blocco da inserire nel system prompt. In inglese (i modelli lo seguono
    meglio); la lingua delle RISPOSTE e gia imposta dal language_module.
    L'appellativo e localizzato.
    """
    addr = get_address(lang)
    return (
        f"PERSONA: You are JARVIS, a refined personal AI assistant in the style "
        f"of a composed British butler. Address the user as '{addr}' naturally, "
        f"not in every sentence. Speak with calm precision and occasional subtle "
        f"dry wit or light understatement, never at the expense of clarity. "
        f"Be concise. Be anticipatory: when genuinely useful, mention one "
        f"relevant next step or risk the user may not have considered. "
        f"Remain composed regardless of the situation. Never use emojis. "
        f"Never be servile or flattering; be quietly competent."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Conferme brevi (per usi futuri: esecuzione comandi, wake word, ecc.)
# ══════════════════════════════════════════════════════════════════════════════

_ACKS = {
    "it": ["Subito, {addr}.", "Come desidera.", "Provvedo immediatamente.", "Naturalmente."],
    "fr": ["Tout de suite, {addr}.", "Comme vous voudrez.", "J'y veille immediatement.", "Naturellement."],
    "en": ["Right away, {addr}.", "As you wish.", "On it immediately.", "Naturally."],
    "pt": ["Imediatamente, {addr}.", "Como desejar.", "Tratarei disso agora.", "Naturalmente."],
    "de": ["Sofort, {addr}.", "Wie Sie wuenschen.", "Ich kuemmere mich umgehend darum.", "Natuerlich."],
    "es": ["Enseguida, {addr}.", "Como desee.", "Me encargo de inmediato.", "Naturalmente."],
}


def ack(lang: str) -> str:
    """Conferma breve in stile JARVIS."""
    lang = _lc(lang)
    pool = _ACKS.get(lang, _ACKS["en"])
    return random.choice(pool).format(addr=get_address(lang))


if __name__ == "__main__":
    # Test rapido in tutte le lingue
    for code in _SUPPORTED:
        print(f"--- {code} ---")
        print(get_greeting(code))
        print(system_briefing(code))
        print(ack(code))
        print()
