#!/usr/bin/env python3
"""
jarvis_secrets.py — Gestione segreti cifrati per JARVIS v6.0

Salva API key, password sudo e PIN di sessione in un file .env.enc
cifrato con Fernet (AES-128-CBC + HMAC-SHA256).

La chiave di cifratura viene derivata da informazioni univoche della
macchina (machine-id + username + hostname) tramite PBKDF2-SHA256,
quindi il file .env.enc è inutile su qualsiasi altro PC.

Uso:
    from jarvis_secrets import SecretsManager
    sm = SecretsManager()
    sm.load_into_env()          # carica tutto in os.environ
    pin = sm.get("JARVIS_PIN")  # legge un valore
"""

import os
import sys
import hashlib
import platform
import subprocess
from pathlib import Path

# ── Dipendenze opzionali ──────────────────────────────────────────────────────
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64
    CRYPTO_OK = True
except ImportError:
    CRYPTO_OK = False

# ── Percorsi ──────────────────────────────────────────────────────────────────
def _default_enc_path() -> Path:
    """Cerca .env.enc nella cartella dello script o in ~/Documenti/modelli."""
    candidates = [
        Path(__file__).parent / ".env.enc",
        Path.home() / "Documenti" / "modelli" / ".env.enc",
        Path.home() / "Documents" / "modelli" / ".env.enc",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Default: stessa cartella dello script
    return Path(__file__).parent / ".env.enc"


# ══════════════════════════════════════════════════════════════════════════════
class SecretsManager:
    """
    Gestisce la cifratura/decifratura del file .env.enc.

    La chiave Fernet è derivata con PBKDF2 da:
        machine_id + username + hostname
    Questo lega il file a questa specifica macchina.
    """

    SALT_SUFFIX = b"JARVIS_v6_SALT_2025"
    PBKDF2_ITER = 390_000  # OWASP 2023 raccomanda ≥310k per SHA-256

    def __init__(self, enc_path: Path = None):
        self._path = enc_path or _default_enc_path()
        self._data: dict = {}
        self._fernet = None
        if CRYPTO_OK:
            self._fernet = self._build_fernet()

    # ── Derivazione chiave ────────────────────────────────────────────────────

    def _machine_secret(self) -> bytes:
        """Genera un segreto univoco per questa macchina."""
        parts = []

        # 1. machine-id (Linux/Mac)
        for mid_path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            p = Path(mid_path)
            if p.exists():
                parts.append(p.read_text().strip())
                break

        # 2. Mac: IOPlatformSerialNumber
        if platform.system() == "Darwin":
            try:
                r = subprocess.run(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True, text=True
                )
                for line in r.stdout.splitlines():
                    if "IOPlatformSerialNumber" in line:
                        parts.append(line.split('"')[-2])
                        break
            except Exception:
                pass

        # 3. Windows: MachineGuid
        if platform.system() == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography"
                )
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(guid)
            except Exception:
                pass

        # 4. Username + hostname (sempre disponibili)
        parts.append(os.getenv("USER") or os.getenv("USERNAME") or "jarvis")
        parts.append(platform.node())

        combined = "|".join(parts).encode("utf-8")
        return hashlib.sha256(combined).digest()

    def _build_fernet(self) -> "Fernet":
        """Deriva la chiave Fernet dalla macchina e crea l'istanza."""
        machine_secret = self._machine_secret()
        salt = hashlib.sha256(machine_secret + self.SALT_SUFFIX).digest()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.PBKDF2_ITER,
        )
        key = base64.urlsafe_b64encode(kdf.derive(machine_secret))
        return Fernet(key)

    # ── Lettura / Scrittura ───────────────────────────────────────────────────

    def _serialize(self, data: dict) -> bytes:
        """Serializza il dizionario in bytes (formato KEY=VALUE\\n)."""
        lines = []
        for k, v in data.items():
            lines.append(f"{k}={v}")
        return "\n".join(lines).encode("utf-8")

    def _deserialize(self, raw: bytes) -> dict:
        """Deserializza bytes in dizionario."""
        result = {}
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
        return result

    def load(self) -> dict:
        """Carica e decifra il file .env.enc. Ritorna dict vuoto se non esiste."""
        if not self._path.exists():
            # Fallback: prova il .env in chiaro
            plain = self._path.with_suffix("")
            if plain.exists():
                self._data = self._load_plain(plain)
                return dict(self._data)
            return {}

        if not CRYPTO_OK:
            # Crypto non disponibile — fallback plain
            plain = self._path.with_suffix("")
            if plain.exists():
                self._data = self._load_plain(plain)
                return dict(self._data)
            return {}

        try:
            encrypted = self._path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            self._data = self._deserialize(decrypted)
            return dict(self._data)
        except InvalidToken:
            print(
                "⚠️  Impossibile decifrare .env.enc — "
                "file creato su un'altra macchina o corrotto.",
                file=sys.stderr
            )
            return {}
        except Exception as e:
            print(f"⚠️  Errore lettura .env.enc: {e}", file=sys.stderr)
            return {}

    def _load_plain(self, path: Path) -> dict:
        """Carica .env in chiaro (fallback)."""
        data = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                data[k.strip()] = v.strip()
        return data

    def save(self, data: dict):
        """Cifra e salva il dizionario in .env.enc."""
        self._data = dict(data)

        if not CRYPTO_OK:
            # Fallback: salva in chiaro con avviso
            plain = self._path.with_suffix("")
            lines = ["# JARVIS v6.0 secrets (NON cifrато — installa cryptography)\n"]
            for k, v in data.items():
                lines.append(f"{k}={v}")
            plain.write_text("\n".join(lines), encoding="utf-8")
            if platform.system() != "Windows":
                os.chmod(plain, 0o600)
            print("⚠️  cryptography non installato — segreti salvati in chiaro", file=sys.stderr)
            return

        encrypted = self._fernet.encrypt(self._serialize(data))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(encrypted)

        # Permessi restrittivi (solo proprietario)
        if platform.system() != "Windows":
            os.chmod(self._path, 0o600)

        # Rimuovi eventuale .env in chiaro se esiste
        plain = self._path.with_suffix("")
        if plain.exists():
            plain.unlink()

    def get(self, key: str, default: str = "") -> str:
        """Legge un valore (carica il file se non ancora caricato)."""
        if not self._data:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value: str):
        """Imposta un valore e salva."""
        if not self._data:
            self.load()
        self._data[key] = value
        self.save(self._data)

    def load_into_env(self):
        """
        Carica tutti i segreti in os.environ.
        Non sovrascrive variabili già presenti nell'ambiente.
        """
        data = self.load()
        for k, v in data.items():
            if k not in os.environ and v:
                os.environ[k] = v

    # ── PIN di sessione ───────────────────────────────────────────────────────

    def verify_pin(self, entered: str) -> bool:
        """Verifica il PIN/password inserito dall'utente."""
        stored = self.get("JARVIS_PIN", "")
        if not stored:
            return True  # nessun PIN configurato — accesso libero
        pin_type = self.get("JARVIS_PIN_TYPE", "pin")
        if pin_type == "pin":
            # Solo numeri
            return entered.strip() == stored.strip()
        else:
            # Password/frase — case sensitive
            return entered.strip() == stored.strip()

    @property
    def has_pin(self) -> bool:
        return bool(self.get("JARVIS_PIN", ""))

    @property
    def crypto_available(self) -> bool:
        return CRYPTO_OK


# ══════════════════════════════════════════════════════════════════════════════
# Funzione di utilità per l'installer
# ══════════════════════════════════════════════════════════════════════════════

def setup_secrets_interactive(dest_dir: Path) -> dict:
    """
    Raccoglie interattivamente tutti i segreti e li salva cifrati.
    Usato dall'installer.
    Ritorna il dizionario dei segreti configurati.
    """
    enc_path = dest_dir / ".env.enc"
    sm = SecretsManager(enc_path)

    # Carica eventuali valori esistenti
    existing = sm.load()

    print()
    if sm.crypto_available:
        print(f"  🔒 Cifratura attiva (Fernet/AES — chiave legata a questa macchina)")
    else:
        print(f"  ⚠️  cryptography non installato — segreti salvati in chiaro")
    print()

    keys_meta = [
        ("TAVILY_API_KEY",  "Tavily Search (consigliato)",          "https://app.tavily.com", False),
        ("BRAVE_API_KEY",   "Brave Search (opzionale)",              "https://api.search.brave.com", True),
        ("GNEWS_API_KEY",   "GNews (opzionale)",                     "https://gnews.io", True),
        ("DISCORD_TOKEN",   "Discord Bot Token (opzionale)",         "https://discord.com/developers/applications", True),
        ("SUDO_PASSWORD",   "Password sudo (per comandi di sistema)", "la tua password di login", False),
    ]

    new_values = dict(existing)

    for key, desc, hint, optional in keys_meta:
        current = existing.get(key, "")
        tag = "(opzionale)" if optional else "(consigliato)"
        print(f"  {key} {tag}")
        print(f"  {desc}")
        print(f"  → {hint}")
        if current:
            masked = current[:4] + "***" if len(current) > 4 else "***"
            print(f"  Valore attuale: {masked} (invio = mantieni)")
        else:
            print(f"  (invio per saltare)")

        # Password/token: nascondi input
        if key in ("SUDO_PASSWORD", "DISCORD_TOKEN") or "KEY" in key or "TOKEN" in key:
            try:
                import getpass
                val = getpass.getpass("  > ")
            except Exception:
                val = input("  > ").strip()
        else:
            val = input("  > ").strip()

        if val:
            new_values[key] = val
        elif current:
            pass  # mantieni esistente
        print()

    # ── Configurazione PIN di sessione ────────────────────────────────────────
    print("  ─── PIN di sessione JARVIS ───────────────────────────")
    print("  Protegge JARVIS da accessi non autorizzati al terminale.")
    print("  Verrà chiesto dopo il banner all'avvio e con /dormi.\n")

    pin_type_choice = input("  Tipo: [1] PIN numerico  [2] Password  [3] Frase  (invio=salta): ").strip()

    if pin_type_choice in ("1", "2", "3"):
        type_map = {"1": "pin", "2": "password", "3": "frase"}
        pin_type = type_map[pin_type_choice]
        new_values["JARVIS_PIN_TYPE"] = pin_type

        hints = {
            "pin":      "Solo numeri (es. 1234)",
            "password": "Lettere e numeri (es. Jarvis2025)",
            "frase":    "Frase intera (es. apri sesamo jarvis)",
        }
        print(f"  {hints[pin_type]}")

        try:
            import getpass
            pin1 = getpass.getpass("  PIN: ")
            pin2 = getpass.getpass("  Conferma: ")
        except Exception:
            pin1 = input("  PIN: ").strip()
            pin2 = input("  Conferma: ").strip()

        if pin1 == pin2 and pin1:
            if pin_type == "pin" and not pin1.isdigit():
                print("  ⚠️  PIN deve contenere solo numeri — saltato")
            else:
                new_values["JARVIS_PIN"] = pin1
                print(f"  ✅ PIN configurato ({pin_type})")
        else:
            print("  ⚠️  PIN non corrispondente — saltato")
    else:
        print("  ⚠️  Nessun PIN configurato — accesso libero")

    print()

    # Salva tutto cifrato
    sm.save(new_values)
    print(f"  ✅ Segreti salvati in {enc_path}")
    if sm.crypto_available:
        print(f"  🔒 File cifrato — inutilizzabile su altre macchine")

    return new_values


if __name__ == "__main__":
    # Test rapido
    sm = SecretsManager()
    print(f"Crypto disponibile: {sm.crypto_available}")
    print(f"Path: {sm._path}")
    data = sm.load()
    print(f"Chiavi caricate: {list(data.keys())}")
