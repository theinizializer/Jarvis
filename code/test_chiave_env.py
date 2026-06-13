#!/usr/bin/env python3
"""
test_chiave_env.py — Testa la chiave ESATTA caricata dal .env contro Cerebras.
Mettilo in ~/Documenti/modelli ed esegui: python test_chiave_env.py
"""
import os
from pathlib import Path
import requests

# Carica la chiave esattamente come fa JARVIS (dal .env, togliendo virgolette)
envp = Path(__file__).parent / ".env"
key = ""
for line in envp.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line.startswith("CEREBRAS_API_KEY") and "=" in line:
        _, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
            v = v[1:-1].strip()
        key = v
        break

print(f"Chiave dal .env: {repr(key)}")
print(f"Lunghezza: {len(key)} caratteri")
print(f"Inizia con csk-: {key.startswith('csk-')}")
# Mostra eventuali caratteri strani (spazi, a-capo nascosti)
print(f"Caratteri non-alfanumerici/dash: {[c for c in key if not (c.isalnum() or c == '-')]}")

print("\nTesto la chiave contro Cerebras (gpt-oss-120b)...")
r = requests.post(
    "https://api.cerebras.ai/v1/chat/completions",
    headers={"Authorization": f"Bearer {key}"},
    json={"model": "gpt-oss-120b",
          "messages": [{"role": "user", "content": "ciao"}],
          "max_tokens": 5},
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Risposta: {r.text[:200]}")
if r.status_code == 200:
    print("\n✅ La chiave del .env FUNZIONA — il problema era altrove")
elif r.status_code == 401:
    print("\n❌ La chiave del .env e' INVALIDA (401)")
    print("   → quella che hai testato a mano era diversa/corretta.")
    print("   → ricopia la chiave buona nel .env (occhio a caratteri mancanti).")
