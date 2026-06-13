#!/usr/bin/env python3
"""
diag_cerebras.py — Diagnostica: perché CEREBRAS_API_KEY non viene caricata.
Mettilo in ~/Documenti/modelli ed esegui:  python diag_cerebras.py
"""
import os
import sys
from pathlib import Path

print("=" * 60)
print("DIAGNOSTICA CARICAMENTO CEREBRAS_API_KEY")
print("=" * 60)

# ── 1. La chiave è già nell'ambiente PRIMA di caricare qualcosa? ──────────────
pre = os.environ.get("CEREBRAS_API_KEY")
print(f"\n1. CEREBRAS_API_KEY gia in os.environ all'avvio:")
print(f"   {repr(pre[:12] + '...') if pre else 'ASSENTE (giusto cosi)'}")

# ── 2. Cosa contiene il .env.enc (SecretsManager)? ────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.resolve()))
print(f"\n2. Contenuto di .env.enc (segreti cifrati):")
try:
    from jarvis_secrets import SecretsManager
    sm = SecretsManager()
    data = sm.load()
    if not data:
        print("   .env.enc vuoto o assente")
    else:
        print(f"   chiavi presenti: {list(data.keys())}")
        ck = data.get("CEREBRAS_API_KEY")
        if ck is not None:
            print(f"   >>> CEREBRAS_API_KEY in .env.enc: {repr(ck[:12] + '...') if ck else 'PRESENTE MA VUOTA'}")
            print("   >>> QUESTA e' probabilmente la causa: blocca quella del .env")
        else:
            print("   CEREBRAS_API_KEY NON presente in .env.enc (ok)")
except Exception as e:
    print(f"   Errore SecretsManager: {e}")

# ── 3. Cosa c'è nel .env normale? ─────────────────────────────────────────────
print(f"\n3. Riga CEREBRAS nel .env normale:")
envp = Path(__file__).parent / ".env"
if envp.exists():
    found = False
    for line in envp.read_text(encoding="utf-8").splitlines():
        if "CEREBRAS" in line:
            v = line.split("=", 1)[1] if "=" in line else ""
            print(f"   {repr(line[:30])}... → valore lungo {len(v)} caratteri")
            found = True
    if not found:
        print("   Nessuna riga CEREBRAS nel .env!")
else:
    print("   .env non trovato qui")

# ── 4. Simula il caricamento completo come fa JARVIS ──────────────────────────
print(f"\n4. Simulo il caricamento JARVIS (SecretsManager + .env):")
try:
    from jarvis_secrets import SecretsManager
    SecretsManager().load_into_env()
except Exception:
    pass
# Poi il .env (come fa utils_module._load_dotenv)
for line in (envp.read_text(encoding="utf-8").splitlines() if envp.exists() else []):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, val = line.partition("=")
    k = k.strip(); val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("\"", "'"):
        val = val[1:-1].strip()
    if k and val and k not in os.environ:   # <-- la regola che NON sovrascrive
        os.environ[k] = val

final = os.environ.get("CEREBRAS_API_KEY", "")
print(f"   CEREBRAS_API_KEY finale: {repr(final[:12] + '...') if final else 'VUOTA'}")
print(f"   Lunghezza: {len(final)} caratteri")
if final.startswith("csk-"):
    print("   ✅ CARICATA CORRETTAMENTE")
else:
    print("   ❌ NON caricata o sbagliata")

print("\n" + "=" * 60)
