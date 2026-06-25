#!/usr/bin/env python3
"""
SepFormer Server — processo persistente per speaker extraction.
Carica il modello una volta sola e resta in ascolto su socket Unix.
Protegge JARVIS da SIGSEGV isolando il codice GPU in un processo separato.

Protocollo:
  Client → Server: 4 byte (lunghezza payload) + payload (pickle di np.ndarray)
  Server → Client: 4 byte (lunghezza risposta) + risposta (pickle di np.ndarray)
"""
import os
import pickle
import signal
import socket
import struct
import sys
import threading
from pathlib import Path

SOCKET_PATH = Path.home() / "jarvis_memory" / "sepformer.sock"
SAMPLE_RATE  = 16000

def load_model():
    import torch
    from speechbrain.inference.separation import SepformerSeparation
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model  = SepformerSeparation.from_hparams(
        source="speechbrain/sepformer-wham16k-enhancement",
        run_opts={"device": device},
        savedir=str(Path.home() / "jarvis_memory" / "sepformer_cache"),
    )
    print(f"[SepFormer] Modello caricato su {device.upper()}", flush=True)
    return model, device

def separate(model, audio, device):
    import torch
    import numpy as np
    if audio.ndim > 1:
        audio = audio[:, 0]
    t = torch.tensor(audio, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        est = model.separate_batch(t)
    est_np   = est[0].cpu().numpy()
    energies = (est_np ** 2).mean(axis=0)
    best     = est_np[:, energies.argmax()].astype("float32")
    peak     = abs(best).max()
    if peak > 1e-6:
        best = best / peak * 0.95
    return best

def handle_client(conn, model, device):
    try:
        # Leggi lunghezza payload
        header = conn.recv(4)
        if not header:
            return
        length = struct.unpack(">I", header)[0]
        # Leggi payload
        data = b""
        while len(data) < length:
            chunk = conn.recv(min(65536, length - len(data)))
            if not chunk:
                break
            data += chunk
        audio = pickle.loads(data)
        result = separate(model, audio, device)
        payload = pickle.dumps(result)
        conn.sendall(struct.pack(">I", len(payload)) + payload)
    except Exception as e:
        print(f"[SepFormer] Errore client: {e}", flush=True)
        # Manda risposta vuota
        conn.sendall(struct.pack(">I", 0))
    finally:
        conn.close()

def main():
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    print("[SepFormer] Caricamento modello...", flush=True)
    try:
        model, device = load_model()
    except Exception as e:
        print(f"[SepFormer] ERRORE caricamento: {e}", flush=True)
        sys.exit(1)

    # Crea socket Unix
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCKET_PATH))
    srv.listen(4)
    SOCKET_PATH.chmod(0o600)
    print(f"[SepFormer] In ascolto su {SOCKET_PATH}", flush=True)

    # Gestione SIGTERM
    def shutdown(sig, frame):
        print("[SepFormer] Shutdown...", flush=True)
        srv.close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            conn, _ = srv.accept()
            t = threading.Thread(target=handle_client,
                                 args=(conn, model, device), daemon=True)
            t.start()
        except OSError:
            break

if __name__ == "__main__":
    main()
