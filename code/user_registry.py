#!/usr/bin/env python3
"""
JARVIS — user_registry.py
=========================
La spina dorsale dell'identita'. Regola unica:
    UNA persona = un NOME unico + un RUOLO + UN solo PIN (hashato, non cifrato).

Principi che reggono tutto il resto della conversazione:
  - La VOCE dice CHI sei (personalizzazione), non cosa puoi fare.
  - Il PIN IDENTIFICA e AUTORIZZA: identify_by_pin() dato un PIN ritorna la
    persona e il suo ruolo. Non sei tu (ne' la voce, ne' il modello) a
    dichiarare "sono admin" — lo determina il PIN che possiedi.
  - Il guardiano applica; questo file e' solo il registro.

Dipendenze: pin_auth.py (hash_pin / verify_pin). Niente altro -> testabile da solo.
"""

import json
import os
import threading
from pathlib import Path

from pin_auth import hash_pin, verify_pin

_LOCK = threading.Lock()


class UserRegistry:
    def __init__(self, path=None):
        self._path = Path(path) if path else Path(__file__).parent / "users.json"
        self._users = {}   # nome -> {"role": "admin"|"secondary", "pin_hash": str}
        self._load()

    # ── persistenza ───────────────────────────────────────────────────────────
    def _load(self):
        if self._path.exists():
            try:
                self._users = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._users = {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._users, indent=2), encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)   # solo il proprietario legge gli hash
        except OSError:
            pass

    # ── helper ──────────────────────────────────────────────────────────────--
    @staticmethod
    def _norm(name):
        return (name or "").strip().lower()

    def exists(self, name):
        return self._norm(name) in self._users

    def role_of(self, name):
        u = self._users.get(self._norm(name))
        return u["role"] if u else None

    def list_users(self):
        return [(n, u["role"]) for n, u in sorted(self._users.items())]

    @property
    def has_admin(self):
        return any(u["role"] == "admin" for u in self._users.values())

    # ── creazione ───────────────────────────────────────────────────────────--
    def init_admin(self, name, pin):
        """Chiamato DALL'INSTALLER. Crea l'UNICO admin e rifiuta se esiste gia':
        e' questo che rende impossibile la duplicazione del PIN admin."""
        with _LOCK:
            if self.has_admin:
                return False, "admin gia' esistente"
            n = self._norm(name)
            if not n or not pin:
                return False, "nome o pin vuoto"
            self._users[n] = {"role": "admin", "pin_hash": hash_pin(pin)}
            self._save()
            return True, n

    def add_secondary(self, name, pin):
        """Nuovo utente secondario. Il controllo 'solo l'admin puo' crearlo' si fa
        A MONTE (gate di ruolo nell'handler/guardiano), non qui."""
        with _LOCK:
            n = self._norm(name)
            if not n or not pin:
                return False, "nome o pin vuoto"
            if n in self._users:
                return False, "nome gia' esistente"
            self._users[n] = {"role": "secondary", "pin_hash": hash_pin(pin)}
            self._save()
            return True, n

    # ── verifica / identificazione ──────────────────────────────────────────--
    def verify(self, name, pin):
        u = self._users.get(self._norm(name))
        return bool(u) and verify_pin(pin, u["pin_hash"])

    def identify_by_pin(self, pin):
        """Dato un PIN, ritorna (nome, ruolo) della persona a cui appartiene,
        o (None, None). E' QUESTO che usa il login di sessione: il PIN dice
        CHI sei. Cosi' un secondario non puo' 'dichiararsi admin'."""
        if not pin:
            return None, None
        for n, u in self._users.items():
            if verify_pin(pin, u["pin_hash"]):
                return n, u["role"]
        return None, None

    # ── eliminazione ──────────────────────────────────────────────────────────
    def delete_user(self, name):
        with _LOCK:
            n = self._norm(name)
            u = self._users.get(n)
            if not u:
                return False, "inesistente"
            if u["role"] == "admin":
                return False, "non si elimina l'admin"   # protezione
            del self._users[n]
            self._save()
            return True, n


# ══════════════════════════════════════════════════════════════════════════════
# Flusso /aggiungi_voce  (da chiamare SOLO in sessione admin — gate a monte)
# ══════════════════════════════════════════════════════════════════════════════
def enroll_voice_profile(registry, speaker, record_fn, ask_fn, print_fn,
                         lang="it", max_pin_attempts=2, ask_secret_fn=None):
    """
    Implementa lo spec:
      comando -> nome -> esiste?
         SI':  conferma il PIN ESISTENTE di quella persona (max 2 tentativi).
               2 errati -> annulla, NESSUN profilo creato, NESSUN account nuovo.
         NO :  nuovo PIN (con conferma) -> nuovo utente secondario.
      La REGISTRAZIONE avviene SOLO DOPO che il PIN e' a posto -> mai profili orfani.

    Iniezione di dipendenze (cosi' e' testabile senza audio/torch):
      record_fn()        -> ritorna l'audio (o una lista di clip) gia' registrato.
      ask_fn(prompt)     -> stringa (per il NOME, visibile).
      ask_secret_fn(p)   -> stringa per i PIN, con input NASCOSTO. Se None, usa ask_fn.
      print_fn(msg)      -> output.
    Ritorna (ok: bool, messaggio: str).
    """
    ask_pin = ask_secret_fn or ask_fn
    name = (ask_fn("  Nome utente (es. radostin): ") or "").strip().lower()
    if not name:
        return False, "nome non valido"

    if registry.exists(name):
        # ── persona esistente: prova d'identita' col SUO pin ──────────────────
        ok = False
        for left in range(max_pin_attempts, 0, -1):
            pin = ask_pin(f"  PIN di {name} (conferma che sei tu): ")
            if not pin:
                print_fn("  Annullato.")
                return False, "annullato"
            if registry.verify(name, pin):
                ok = True
                break
            if left > 1:
                print_fn(f"  ❌ PIN errato — {left - 1} tentativo rimasto")
        if not ok:
            print_fn("  🔒 PIN errato. Annullato — nessun profilo, nessun account nuovo.")
            return False, "pin errato"
    else:
        # ── persona nuova: nuovo PIN -> nuovo utente secondario ───────────────
        pin1 = ask_pin(f"  Nuovo PIN per '{name}': ")
        if not pin1:
            print_fn("  Annullato.")
            return False, "annullato"
        pin2 = ask_pin("  Conferma PIN: ")
        if pin1 != pin2:
            print_fn("  ❌ I PIN non coincidono. Annullato.")
            return False, "pin non coincidono"
        created, info = registry.add_secondary(name, pin1)
        if not created:
            print_fn(f"  ❌ {info}")
            return False, info

    # ── REGISTRAZIONE: solo ora, identita' garantita ──────────────────────────
    print_fn("  🎙️  Leggi il testo a voce naturale (~1 min 30).")
    ask_fn("  (invio per iniziare la registrazione)")
    audio = record_fn()
    if audio is None or (hasattr(audio, "__len__") and len(audio) == 0):
        print_fn("  ❌ Registrazione vuota — riprova /aggiungi_voce.")
        return False, "registrazione vuota"

    if speaker.add_profile(audio, name, lang):
        print_fn(f"  ✅ Profilo '{name}' [{lang.upper()}] salvato.")
        return True, name
    print_fn("  ❌ Salvataggio profilo fallito.")
    return False, "salvataggio fallito"


def confirm_admin_pin(registry, ask_fn, print_fn, session_user, attempts=2):
    """Rete sotto azioni irreversibili (es. eliminare un profilo): richiede il PIN
    admin SUL MOMENTO, anche se la sessione e' gia' admin."""
    for left in range(attempts, 0, -1):
        pin = ask_fn("  Conferma il tuo PIN admin: ")
        if not pin:
            return False
        if registry.verify(session_user, pin):
            return True
        if left > 1:
            print_fn(f"  ❌ PIN errato — {left - 1} rimasto")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Self-test (mock, niente audio/torch)
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import tempfile

    class FakeSpeaker:
        def __init__(self): self.saved = []
        def add_profile(self, audio, name, lang):
            self.saved.append((name, lang)); return True

    def scripted_ask(answers):
        it = iter(answers)
        return lambda prompt="": next(it, "")

    tmp = Path(tempfile.mkdtemp()) / "users.json"
    passed = 0
    total = 0

    def check(label, cond):
        global passed, total
        total += 1
        passed += bool(cond)
        print(f"{'ok ' if cond else 'XX '}{label}")

    # init admin + no duplicazione
    reg = UserRegistry(tmp)
    check("init_admin crea admin", reg.init_admin("radostin", "1234")[0] is True)
    check("secondo admin rifiutato", reg.init_admin("altro", "9999")[0] is False)
    check("identify_by_pin trova admin", reg.identify_by_pin("1234") == ("radostin", "admin"))
    check("identify_by_pin pin ignoto -> None", reg.identify_by_pin("0000") == (None, None))

    # nuovo secondario via enroll
    sp = FakeSpeaker()
    ask = scripted_ask(["marco", "5678", "5678", ""])   # nome nuovo, pin, conferma, invio
    ok, msg = enroll_voice_profile(reg, sp, lambda: [b"audio"], ask, lambda m: None)
    check("nuovo utente: enroll ok", ok and reg.role_of("marco") == "secondary")
    check("nuovo utente: profilo salvato", ("marco", "it") in sp.saved)

    # utente esistente, pin corretto
    sp2 = FakeSpeaker()
    ask = scripted_ask(["marco", "5678", ""])
    ok, _ = enroll_voice_profile(reg, sp2, lambda: [b"x"], ask, lambda m: None)
    check("esistente pin giusto: profilo aggiunto", ok and ("marco", "it") in sp2.saved)

    # utente esistente, pin sbagliato 2 volte -> annulla, niente salvataggio
    sp3 = FakeSpeaker()
    ask = scripted_ask(["marco", "0000", "1111"])
    ok, _ = enroll_voice_profile(reg, sp3, lambda: [b"x"], ask, lambda m: None)
    check("esistente 2 pin errati: annullato", ok is False and sp3.saved == [])

    # pin sbagliato NON crea account nuovo di ripiego
    check("nessun account 'ripiego' creato", [n for n, _ in reg.list_users()] == ["marco", "radostin"])

    # conferma del nuovo pin non coincide -> annulla, niente utente
    sp4 = FakeSpeaker()
    ask = scripted_ask(["luca", "1111", "2222"])
    ok, _ = enroll_voice_profile(reg, sp4, lambda: [b"x"], ask, lambda m: None)
    check("nuovo: pin non coincide -> annullato", ok is False and not reg.exists("luca"))

    # delete secondario ok, delete admin rifiutato
    check("delete secondario ok", reg.delete_user("marco")[0] is True)
    check("delete admin rifiutato", reg.delete_user("radostin")[0] is False)

    # confirm_admin_pin
    check("confirm_admin_pin giusto", confirm_admin_pin(reg, scripted_ask(["1234"]), lambda m: None, "radostin") is True)
    check("confirm_admin_pin sbagliato", confirm_admin_pin(reg, scripted_ask(["9", "9"]), lambda m: None, "radostin") is False)

    print(f"\n{passed}/{total} test passati")
