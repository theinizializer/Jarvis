#!/usr/bin/env python3
"""
JARVIS — PIN auth (riferimento). Solo stdlib, niente dipendenze.

Principi:
  - Il PIN si HASHA, non si cifra: serve solo verificarlo, mai rileggerlo.
    Se ti rubano il file, l'hash non torna indietro (la cifratura sì).
  - prompt_pin() risolve il tuo bug: input vuoto o EOF = ANNULLA (non "sbagliato"),
    tentativi massimi, nessun loop infinito, nessun crash.
  - getpass: il PIN non viene mostrato a schermo e non finisce nei log.

Upgrade opzionale: se installi `argon2-cffi`, sostituisci pbkdf2 con argon2id
(piu' robusto contro brute-force). Per un PIN locale, pbkdf2 a 200k iterazioni
e' gia' piu' che adeguato.
"""

import hashlib
import hmac
import secrets
import getpass
import time

_ITERATIONS = 200_000


def hash_pin(pin: str, salt: bytes = None) -> str:
    """Ritorna 'salt_hex$hash_hex' — da salvare AL POSTO del PIN in chiaro."""
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITERATIONS)
    return f"{salt.hex()}${h.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """Confronto a tempo costante contro l'hash salvato (no timing leak)."""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        h = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), _ITERATIONS
        )
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:
        return False


def prompt_pin(stored: str, prompt: str = "PIN: ",
               max_attempts: int = 3, delay: float = 0.5) -> bool:
    """
    Chiede il PIN fino a max_attempts. Ritorna True se corretto, False altrimenti.

    IL FIX AL TUO BUG:
      - input vuoto  -> annulla subito (NON conta come "PIN sbagliato")
      - EOF / Ctrl-C -> annulla subito (niente loop infinito)
      - tetto di tentativi -> dopo max_attempts esce con False, non cicla all'infinito
    """
    for left in range(max_attempts, 0, -1):
        try:
            pin = getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\n❌ Annullato")
            return False

        if pin == "":
            print("❌ Annullato")          # <-- vuoto = annulla, non "sbagliato"
            return False
        if verify_pin(pin, stored):
            return True
        if left > 1:
            print(f"❌ PIN errato — {left - 1} tentativi rimasti")
            time.sleep(delay)             # piccolo freno anti brute-force

    print("🔒 Troppi tentativi — annullato")
    return False


# Per la TUI/modalita' voce: stessa logica ma con la TUA funzione di input.
# Passa una callable che ritorna stringa (o None/"" per annullare).
def prompt_pin_via(ask_fn, stored: str, prompt: str = "PIN: ",
                   max_attempts: int = 3) -> bool:
    for left in range(max_attempts, 0, -1):
        pin = ask_fn(prompt)
        if not pin:                       # None o "" -> annulla, non ciclare
            return False
        if verify_pin(pin.strip(), stored):
            return True
        if left > 1:
            print(f"❌ PIN errato — {left - 1} tentativi rimasti")
    return False


if __name__ == "__main__":
    s = hash_pin("1234")
    print("salvato:        ", s)
    print("verify '1234':  ", verify_pin("1234", s))   # True
    print("verify '0000':  ", verify_pin("0000", s))   # False
    print("verify '' (bug):", verify_pin("", s))       # False, e prompt_pin annulla
