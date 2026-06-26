#!/usr/bin/env python3
"""
JARVIS — user_registry.py  (versione completa)
==============================================
Identità del sistema. Regole stabilite nella progettazione:

  - UN solo admin, con il PIN che SCEGLIE lui. Impossibile averne due
    (init_admin rifiuta se esiste già).
  - I secondari ("gli intrusi che l'admin tollera") hanno PIN ALFANUMERICI
    GENERATI dal sistema (secrets) — nessuno li sceglie, quindi niente collisioni
    da gestire e niente messaggio "PIN già in uso" da cui dedurre qualcosa.
  - L'identità è una CHIAVE interna unica (marco, marco2…), NON il nome mostrato.
    Due "Marco" diversi hanno chiavi diverse ma possono mostrare la stessa etichetta.
  - Il PIN identifica e autorizza; il potere admin è legato alla CHIAVE admin,
    non al "primo PIN che combacia" -> una collisione non promuove nessuno.

Matrice dei PIN (decisa insieme):
  - crea persona nuova        -> PIN admin (poi il sistema genera il PIN della persona)
  - stessa persona, +lingua   -> PIN admin + PIN della persona (presente al microfono)
  - stessa persona, rifà lingua-> PIN admin + PIN della persona
  - admin rifà la SUA voce    -> solo PIN admin (è già lui)
  - elimina voce/account      -> solo PIN admin (il proprietario decide)
  - l'IDENTITÀ admin non si elimina MAI (non ti chiudi fuori)

Dipendenze: pin_auth.py. Testabile da solo (audio iniettato come dipendenza).
"""

import json
import os
import re
import secrets
import threading
from pathlib import Path

from pin_auth import hash_pin, verify_pin

_LOCK = threading.Lock()

# Alfabeto PIN: maiuscole + cifre, senza caratteri ambigui (no O/0/I/1/L) —
# così si leggono ad alta voce senza confusione.
_PIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class UserRegistry:
    def __init__(self, path=None):
        self._path = Path(path) if path else Path(__file__).parent / "users.json"
        self._users = {}   # key -> {"display": str, "role": str, "pin_hash": str}
        self._load()

    # ── persistenza ───────────────────────────────────────────────────────────
    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._users = {}
            return
        # Migrazione dal vecchio formato {nome: {role, pin_hash}} -> aggiunge display.
        for k, u in raw.items():
            if "display" not in u:
                u["display"] = k
        self._users = raw

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._users, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)   # solo il proprietario legge gli hash
        except OSError:
            pass

    # ── helper ──────────────────────────────────────────────────────────────--
    @staticmethod
    def _slug(text):
        s = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
        return s or "utente"

    def _unique_key(self, display):
        base = self._slug(display)
        if base not in self._users:
            return base
        i = 2
        while f"{base}{i}" in self._users:
            i += 1
        return f"{base}{i}"

    def exists(self, key):
        return key in self._users

    def role_of(self, key):
        u = self._users.get(key)
        return u["role"] if u else None

    def display_of(self, key):
        u = self._users.get(key)
        return u["display"] if u else None

    def keys_with_display(self, display):
        d = (display or "").strip().lower()
        return [k for k, u in self._users.items() if u["display"].strip().lower() == d]

    def list_users(self):
        return [(k, u["display"], u["role"]) for k, u in sorted(self._users.items())]

    @property
    def admin_key(self):
        return next((k for k, u in self._users.items() if u["role"] == "admin"), None)

    @property
    def has_admin(self):
        return self.admin_key is not None

    # ── PIN ─────────────────────────────────────────────────────────────────--
    def pin_in_use(self, pin):
        """Uso INTERNO (no messaggi all'utente): il PIN combacia con qualcuno?"""
        return any(verify_pin(pin, u["pin_hash"]) for u in self._users.values())

    def generate_unique_pin(self, length=6):
        """PIN alfanumerico generato con secrets, garantito NON in collisione con
        nessun PIN esistente (incluso l'admin). Nessuna fuga: è generazione interna."""
        for _ in range(10000):
            pin = "".join(secrets.choice(_PIN_ALPHABET) for _ in range(length))
            if not self.pin_in_use(pin):
                return pin
        raise RuntimeError("impossibile generare un PIN unico")  # praticamente mai

    def verify(self, key, pin):
        u = self._users.get(key)
        return bool(u) and verify_pin(pin, u["pin_hash"])

    def verify_admin(self, pin):
        """Verifica il PIN CONTRO LA CHIAVE ADMIN (non 'primo che combacia').
        Così un secondario con lo stesso PIN non viene mai preso per admin."""
        k = self.admin_key
        return bool(k) and verify_pin(pin, self._users[k]["pin_hash"])

    def identify_by_pin(self, pin):
        """Login di sessione: (key, role) o (None, None). I PIN sono unici per
        costruzione (generati senza collisioni), quindi non c'è ambiguità."""
        if not pin:
            return None, None
        # admin prima, per sicurezza
        ak = self.admin_key
        if ak and verify_pin(pin, self._users[ak]["pin_hash"]):
            return ak, "admin"
        for k, u in self._users.items():
            if u["role"] != "admin" and verify_pin(pin, u["pin_hash"]):
                return k, u["role"]
        return None, None

    # ── creazione ───────────────────────────────────────────────────────────--
    def init_admin(self, display, pin):
        with _LOCK:
            if self.has_admin:
                return False, "admin già esistente"
            if not (display or "").strip() or not pin:
                return False, "nome o pin vuoto"
            key = self._unique_key(display)
            self._users[key] = {"display": display.strip(), "role": "admin",
                                "pin_hash": hash_pin(pin)}
            self._save()
            return True, key

    def create_secondary(self, display, pin):
        with _LOCK:
            if not (display or "").strip() or not pin:
                return False, "nome o pin vuoto"
            key = self._unique_key(display)
            self._users[key] = {"display": display.strip(), "role": "secondary",
                                "pin_hash": hash_pin(pin)}
            self._save()
            return True, key

    # ── modifica / eliminazione ────────────────────────────────────────────--
    def reset_pin(self, key, new_pin):
        """Reset PIN di un secondario: GENERA un nuovo PIN, non rivela il vecchio
        (gli hash non sono reversibili). L'admin non è una cassaforte di PIN altrui."""
        with _LOCK:
            u = self._users.get(key)
            if not u:
                return False, "inesistente"
            if u["role"] == "admin":
                return False, "il PIN admin lo cambia solo l'admin"
            u["pin_hash"] = hash_pin(new_pin)
            self._save()
            return True, key

    def delete_user(self, key):
        """Elimina un secondario per intero. L'admin NON è eliminabile."""
        with _LOCK:
            u = self._users.get(key)
            if not u:
                return False, "inesistente"
            if u["role"] == "admin":
                return False, "l'identità admin non si elimina"
            del self._users[key]
            self._save()
            return True, key


# ══════════════════════════════════════════════════════════════════════════════
# Helper interni dei flussi
# ══════════════════════════════════════════════════════════════════════════════
def _ask_admin_pin(registry, ask_pin, print_fn, attempts=2):
    """PIN admin 'sul momento' — rete contro la sessione lasciata aperta."""
    for left in range(attempts, 0, -1):
        pin = ask_pin("  PIN admin (per autorizzare): ")
        if not pin:
            return False
        if registry.verify_admin(pin):
            return True
        if left > 1:
            print_fn(f"  ❌ PIN admin errato — {left - 1} rimasto")
    return False


def _ask_person_pin(registry, key, ask_pin, print_fn, attempts=2):
    """PIN della PERSONA (presente al microfono) — acconsente a toccare la sua voce."""
    disp = registry.display_of(key) or key
    for left in range(attempts, 0, -1):
        pin = ask_pin(f"  PIN di {disp} (deve digitarlo {disp}): ")
        if not pin:
            return False
        if registry.verify(key, pin):
            return True
        if left > 1:
            print_fn(f"  ❌ PIN errato — {left - 1} rimasto")
    return False


def _langs_of(speaker, key):
    try:
        return [l for (n, l) in speaker.list_profiles() if n == key]
    except Exception:
        return []


def _show_credential(ask_fn, print_fn, display, pin):
    """Mostra un PIN generato in modo VISIBILE e ferma il flusso finché l'admin
    non conferma di averlo annotato (gli hash non sono reversibili: se lo perde,
    l'unico rimedio è un reset, non un recupero)."""
    print_fn("")
    print_fn("  ┌────────────────────────────────────────────")
    print_fn(f"  │  PIN di {display}:  {pin}")
    print_fn(f"  │  Annotalo e consegnalo a {display}.")
    print_fn("  │  NON sarà più mostrato.")
    print_fn("  └────────────────────────────────────────────")
    ask_fn(f"  Premi invio dopo aver annotato il PIN di {display}… ")


# ══════════════════════════════════════════════════════════════════════════════
# /aggiungi_voce
# ══════════════════════════════════════════════════════════════════════════════
def enroll_voice_profile(registry, speaker, record_fn, ask_fn, print_fn,
                         session_user=None, lang="it", ask_secret_fn=None,
                         max_pin_attempts=2, admin_ok=False):
    """
    Flusso completo. session_user = chiave admin della sessione (per la rete PIN admin).
    admin_ok=True -> il PIN admin è già stato verificato dal chiamante (menu unico),
    quindi NON lo richiede di nuovo (evita di chiederlo due volte).
    Ritorna (ok, messaggio).
    """
    ask_pin = ask_secret_fn or ask_fn

    # 1) PIN admin (salta solo se il menu l'ha già verificato).
    if not admin_ok and not _ask_admin_pin(registry, ask_pin, print_fn, max_pin_attempts):
        print_fn("  🔒 PIN admin errato. Operazione annullata.")
        return False, "admin pin"

    # 2) Etichetta.
    display = (ask_fn("  Nome utente: ") or "").strip()
    if not display:
        return False, "nome vuoto"

    keys = registry.keys_with_display(display)
    mode = None        # "new" = identità nuova ; "same" = persona esistente
    target = None      # chiave esistente (solo per mode "same")

    if not keys:
        mode = "new"
    else:
        choice = (ask_fn(f"  '{display}' esiste già — [s]tessa persona o [n]uova? ")
                  or "").strip().lower()
        if choice.startswith("n"):
            display = (ask_fn("  Nome del NUOVO utente (sarà un account distinto): ")
                       or "").strip()
            if not display:
                print_fn("  Annullato.")
                return False, "nome vuoto"
            mode = "new"
        else:
            mode = "same"
            if len(keys) == 1:
                target = keys[0]
            else:
                print_fn("  Quale? " + ", ".join(keys))
                target = (ask_fn("  chiave: ") or "").strip().lower()
                if target not in keys:
                    print_fn("  ❌ chiave non valida.")
                    return False, "chiave"
            # PIN: se è l'admin che rifà la SUA voce, basta il PIN admin (già dato).
            if registry.role_of(target) != "admin":
                if not _ask_person_pin(registry, target, ask_pin, print_fn, max_pin_attempts):
                    print_fn("  🔒 PIN della persona errato. Annullato.")
                    return False, "person pin"
            # lingua già presente? -> rifare?
            if lang in _langs_of(speaker, target):
                if not (ask_fn(f"  {target}_{lang} esiste già — rifare? [s/n] ")
                        or "").strip().lower().startswith("s"):
                    print_fn("  Annullato.")
                    return False, "annullato"

    # 3) REGISTRAZIONE prima di creare l'account: se la registrazione fallisce,
    #    non resta nessun account orfano e nessun PIN appeso.
    print_fn("  🎙️  Leggi il testo a voce naturale (~1 min 30).")
    audio = record_fn()
    if audio is None or (hasattr(audio, "__len__") and len(audio) == 0):
        print_fn("  ❌ Registrazione vuota — riprova /aggiungi_voce.")
        return False, "registrazione vuota"

    # 4) Persona nuova: SOLO ORA si crea l'account e si genera il PIN.
    new_pin = None
    if mode == "new":
        new_pin = registry.generate_unique_pin()
        okc, target = registry.create_secondary(display, new_pin)
        if not okc:
            print_fn(f"  ❌ {target}")
            return False, target

    if not speaker.add_profile(audio, target, lang):
        print_fn("  ❌ Salvataggio profilo fallito.")
        return False, "salvataggio fallito"

    print_fn(f"  ✅ Profilo '{registry.display_of(target)}' [{lang.upper()}] salvato.")

    # 5) Il PIN generato si mostra ALLA FINE, visibile, e il flusso si ferma
    #    finché l'admin non conferma di averlo annotato.
    if new_pin is not None:
        _show_credential(ask_fn, print_fn, registry.display_of(target), new_pin)

    return True, target


# ══════════════════════════════════════════════════════════════════════════════
# /profili_voce  (lista + due eliminazioni: voce-sola / account-intero + reset)
# ══════════════════════════════════════════════════════════════════════════════
def manage_profiles(registry, speaker, ask_fn, print_fn,
                    session_user=None, ask_secret_fn=None, max_pin_attempts=2):
    ask_pin = ask_secret_fn or ask_fn

    if not _ask_admin_pin(registry, ask_pin, print_fn, max_pin_attempts):
        print_fn("  🔒 PIN admin errato. Operazione annullata.")
        return False, "admin pin"

    profiles = []
    try:
        profiles = speaker.list_profiles()
    except Exception:
        pass
    print_fn("  Utenti: " + ", ".join(f"{k}({r})" for k, _d, r in registry.list_users()))
    print_fn("  Profili vocali: " + (", ".join(f"{n}_{l}" for n, l in profiles) or "nessuno"))

    action = (ask_fn("  [v] togli una voce  [a] elimina account  [r] reset PIN: ")
              or "").strip().lower()

    # ── togli una voce (anche dell'admin: resta l'identità) ────────────────────
    if action == "v":
        key = (ask_fn("  chiave utente: ") or "").strip().lower()
        lng = (ask_fn("  lingua (es. it): ") or "").strip().lower()
        if not registry.exists(key):
            print_fn("  ❌ utente inesistente."); return False, "inesistente"
        if hasattr(speaker, "delete_profile") and speaker.delete_profile(key, lng):
            print_fn(f"  ✅ Voce {key}_{lng} rimossa "
                     + ("(identità admin intatta)" if registry.role_of(key) == "admin" else ""))
            return True, "voce rimossa"
        print_fn("  ❌ profilo non trovato."); return False, "no profilo"

    # ── elimina account intero (solo secondari) ────────────────────────────────
    if action == "a":
        key = (ask_fn("  chiave utente da eliminare: ") or "").strip().lower()
        if registry.role_of(key) == "admin":
            print_fn("  🔒 L'identità admin non si elimina.")
            return False, "admin protetto"
        # via tutte le sue voci, poi l'account
        for lng in _langs_of(speaker, key):
            if hasattr(speaker, "delete_profile"):
                speaker.delete_profile(key, lng)
        okd, info = registry.delete_user(key)
        print_fn(f"  ✅ Account '{key}' eliminato." if okd else f"  ❌ {info}")
        return okd, info

    # ── reset PIN di un secondario (genera nuovo, non rivela il vecchio) ────────
    if action == "r":
        key = (ask_fn("  chiave utente: ") or "").strip().lower()
        if registry.role_of(key) == "admin":
            print_fn("  🔒 Il PIN admin non si resetta da qui.")
            return False, "admin"
        if not registry.exists(key):
            print_fn("  ❌ inesistente."); return False, "inesistente"
        newpin = registry.generate_unique_pin()
        registry.reset_pin(key, newpin)
        _show_credential(ask_fn, print_fn, registry.display_of(key), newpin)
        return True, "reset"

    print_fn("  (niente)")
    return False, "annullato"


# ══════════════════════════════════════════════════════════════════════════════
# /profili_voce — COMANDO UNICO (sostituisce anche /aggiungi_voce)
# ══════════════════════════════════════════════════════════════════════════════
def manage_voice(registry, speaker, record_fn, ask_fn, print_fn,
                 session_user=None, lang="it", ask_secret_fn=None, max_pin_attempts=2):
    """PIN admin UNA volta, poi menu numerato. Niente lettere ambigue."""
    ask_pin = ask_secret_fn or ask_fn

    if not _ask_admin_pin(registry, ask_pin, print_fn, max_pin_attempts):
        print_fn("  🔒 PIN admin errato. Operazione annullata.")
        return False, "admin pin"

    try:
        profiles = speaker.list_profiles()
    except Exception:
        profiles = []
    print_fn("  Utenti: " + (", ".join(f"{k}({r})" for k, _d, r in registry.list_users()) or "nessuno"))
    print_fn("  Voci:   " + (", ".join(f"{n}_{l}" for n, l in profiles) or "nessuna"))
    action = (ask_fn("  [1] aggiungi voce  [2] togli una lingua  "
                     "[3] elimina account  [4] reset PIN: ") or "").strip()

    if action == "1":
        return enroll_voice_profile(registry, speaker, record_fn, ask_fn, print_fn,
                                    session_user=session_user, lang=lang,
                                    ask_secret_fn=ask_secret_fn,
                                    max_pin_attempts=max_pin_attempts, admin_ok=True)

    if action == "2":
        key = (ask_fn("  chiave utente: ") or "").strip().lower()
        lng = (ask_fn("  lingua (es. it): ") or "").strip().lower()
        if not registry.exists(key):
            print_fn("  ❌ utente inesistente."); return False, "inesistente"
        if hasattr(speaker, "delete_profile") and speaker.delete_profile(key, lng):
            print_fn(f"  ✅ Voce {key}_{lng} rimossa."
                     + (" (identità admin intatta)" if registry.role_of(key) == "admin" else ""))
            return True, "voce rimossa"
        print_fn("  ❌ profilo non trovato (o voice_module senza delete_profile).")
        return False, "no profilo"

    if action == "3":
        key = (ask_fn("  chiave utente da eliminare: ") or "").strip().lower()
        if registry.role_of(key) == "admin":
            print_fn("  🔒 L'identità admin non si elimina."); return False, "admin protetto"
        if not registry.exists(key):
            print_fn("  ❌ inesistente."); return False, "inesistente"
        removed = 0
        for lng in _langs_of(speaker, key):
            if hasattr(speaker, "delete_profile") and speaker.delete_profile(key, lng):
                removed += 1
        okd, info = registry.delete_user(key)
        print_fn(f"  ✅ Account '{key}' eliminato ({removed} voci rimosse)." if okd
                 else f"  ❌ {info}")
        return okd, info

    if action == "4":
        key = (ask_fn("  chiave utente: ") or "").strip().lower()
        if registry.role_of(key) == "admin":
            print_fn("  🔒 Il PIN admin non si resetta da qui."); return False, "admin"
        if not registry.exists(key):
            print_fn("  ❌ inesistente."); return False, "inesistente"
        newpin = registry.generate_unique_pin()
        registry.reset_pin(key, newpin)
        _show_credential(ask_fn, print_fn, registry.display_of(key), newpin)
        return True, "reset"

    print_fn("  (niente scelto)")
    return False, "annullato"


# Compat: la vecchia confirm_admin_pin usata altrove resta disponibile.
def confirm_admin_pin(registry, ask_fn, print_fn, session_user=None, attempts=2):
    return _ask_admin_pin(registry, ask_fn, print_fn, attempts)
