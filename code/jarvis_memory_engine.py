#!/usr/bin/env python3
"""
jarvis_memory_engine.py — Sistema memoria avanzato per JARVIS v6.0

Architettura:
  - SQLite  : storico completo con timestamp, categoria, importanza
  - ChromaDB: ricerca semantica — trova memorie rilevanti per contesto
  - Fallback: JSON puro se ChromaDB non disponibile

Vantaggi vs JSON:
  - Ricerca semantica: "quali sono i miei progetti?" trova anche
    memorie che non contengono la parola "progetto"
  - Categorizzazione automatica: persone, luoghi, preferenze, fatti tecnici
  - Importanza: le memorie più usate vengono promosse nel contesto
  - Scalabile: funziona bene anche con centinaia di memorie
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── ChromaDB opzionale ────────────────────────────────────────────────────────
try:
    import chromadb
    from chromadb.utils import embedding_functions
    CHROMA_OK = True
except ImportError:
    CHROMA_OK = False

# ── Sentence transformers per embedding locale ────────────────────────────────
try:
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    EMBED_OK = True
except Exception:
    EMBED_OK = False


class MemoryEngine:
    """
    Sistema memoria ibrido SQLite + ChromaDB.

    Uso:
        mem = MemoryEngine(Path.home() / "jarvis_memory")
        mem.add("Radostin lavora su JARVIS")
        relevant = mem.search("cosa sta facendo Radostin?", n=5)
        all_facts = mem.all()
    """

    CATEGORIES = {
        "persona":     ["si chiama", "nome", "età", "lavora", "studia", "vive", "abita", "family"],
        "preferenza":  ["preferisce", "ama", "odia", "piace", "non piace", "favorite", "preferito"],
        "progetto":    ["progetto", "sviluppa", "sta facendo", "JARVIS", "codice", "github"],
        "luogo":       ["a ", "in ", "vive a", "abita a", "città", "paese", "indirizzo"],
        "tecnico":     ["python", "linux", "ubuntu", "raspberry", "arduino", "api", "modello"],
        "data":        ["oggi", "ieri", "domani", "alle", "del ", "gennaio", "febbraio"],
    }

    def __init__(self, mem_dir: Path):
        self._dir = Path(mem_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        # SQLite — storico completo
        self._db_path = self._dir / "memory.db"
        self._db = self._init_sqlite()

        # ChromaDB — ricerca semantica
        self._chroma = None
        self._collection = None
        if CHROMA_OK:
            self._init_chroma()

        # Migra da JSON se esiste
        self._migrate_from_json()

    # ── SQLite ────────────────────────────────────────────────────────────────

    def _init_sqlite(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                fact      TEXT NOT NULL,
                category  TEXT DEFAULT 'generale',
                source    TEXT DEFAULT 'manual',
                timestamp TEXT NOT NULL,
                importance INTEGER DEFAULT 1,
                access_count INTEGER DEFAULT 0
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_category  ON memories(category)")
        db.commit()
        return db

    def _categorize(self, fact: str) -> str:
        """Categorizza automaticamente un fatto."""
        fact_l = fact.lower()
        for cat, keywords in self.CATEGORIES.items():
            if any(kw in fact_l for kw in keywords):
                return cat
        return "generale"

    # ── ChromaDB ─────────────────────────────────────────────────────────────

    def _init_chroma(self):
        try:
            chroma_path = str(self._dir / "chroma")
            self._chroma = chromadb.PersistentClient(path=chroma_path)

            # Usa embedding locale (all-MiniLM-L6-v2, ~80MB, molto veloce)
            if EMBED_OK:
                ef = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
                self._collection = self._chroma.get_or_create_collection(
                    name="jarvis_memories",
                    embedding_function=ef,
                    metadata={"hnsw:space": "cosine"}
                )
            else:
                # Fallback: embedding di default ChromaDB
                self._collection = self._chroma.get_or_create_collection(
                    name="jarvis_memories",
                    metadata={"hnsw:space": "cosine"}
                )
            print(f"🧠 ChromaDB: ✅ ({self._collection.count()} memorie indicizzate)")
        except Exception as e:
            print(f"⚠️  ChromaDB: {e} — uso SQLite puro")
            self._chroma = None
            self._collection = None

    # ── Migrazione JSON → SQLite ──────────────────────────────────────────────

    def _migrate_from_json(self):
        """Migra le vecchie memorie da permanent.json se SQLite è vuoto."""
        json_path = self._dir / "permanent.json"
        if not json_path.exists():
            return

        with self._lock:
            count = self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if count > 0:
                return  # già migrato

        try:
            data = json.loads(json_path.read_text("utf-8"))
            if not data:
                return

            migrated = 0
            for entry in data:
                fact = entry.get("fact", "").strip()
                ts   = entry.get("timestamp", datetime.now().isoformat())
                if fact:
                    self._add_internal(fact, source="migrated", timestamp=ts)
                    migrated += 1

            if migrated:
                print(f"💾 Migrati {migrated} fatti da JSON → SQLite+ChromaDB")
        except Exception as e:
            print(f"⚠️  Migrazione JSON: {e}")

    # ── API pubblica ──────────────────────────────────────────────────────────

    def add(self, fact: str, source: str = "manual") -> int:
        """Aggiunge un fatto alla memoria. Ritorna l'ID."""
        fact = fact.strip()
        if not fact:
            return -1
        return self._add_internal(fact, source=source)

    def _add_internal(self, fact: str, source: str = "manual",
                      timestamp: str = None) -> int:
        ts = timestamp or datetime.now().isoformat()
        category = self._categorize(fact)

        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories (fact, category, source, timestamp) VALUES (?, ?, ?, ?)",
                (fact, category, source, ts)
            )
            row_id = cur.lastrowid
            self._db.commit()

        # Aggiunge anche a ChromaDB
        if self._collection is not None:
            try:
                self._collection.add(
                    documents=[fact],
                    ids=[str(row_id)],
                    metadatas=[{"category": category, "source": source, "ts": ts}]
                )
            except Exception:
                pass

        return row_id

    def search(self, query: str, n: int = 5, category: str = None) -> list[dict]:
        """
        Cerca memorie rilevanti per la query usando ChromaDB (semantico)
        oppure SQLite LIKE (fallback).
        Ritorna lista di {'id', 'fact', 'category', 'score'}.
        """
        if self._collection is not None:
            return self._search_chroma(query, n, category)
        return self._search_sqlite(query, n, category)

    def _search_chroma(self, query: str, n: int, category: str = None) -> list[dict]:
        try:
            where = {"category": category} if category else None
            results = self._collection.query(
                query_texts=[query],
                n_results=min(n, self._collection.count() or 1),
                where=where,
                include=["documents", "metadatas", "distances"]
            )
            out = []
            docs  = results["documents"][0]
            metas = results["metadatas"][0]
            dists = results["distances"][0]
            ids   = results["ids"][0]
            for doc, meta, dist, rid in zip(docs, metas, dists, ids):
                score = 1.0 - dist  # cosine distance → similarity
                if score > 0.3:  # filtra risultati non pertinenti
                    out.append({
                        "id":       int(rid),
                        "fact":     doc,
                        "category": meta.get("category", "generale"),
                        "score":    round(score, 3),
                    })
                    # Incrementa access_count
                    self._db.execute(
                        "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                        (int(rid),)
                    )
            self._db.commit()
            return out
        except Exception as e:
            return self._search_sqlite(query, n, category)

    def _search_sqlite(self, query: str, n: int, category: str = None) -> list[dict]:
        """Ricerca testuale semplice con LIKE."""
        words = [w for w in query.lower().split() if len(w) > 3]
        if not words:
            return self.recent(n)

        conditions = " OR ".join(["LOWER(fact) LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words]
        sql = f"SELECT id, fact, category FROM memories WHERE {conditions}"
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += f" ORDER BY importance DESC, access_count DESC LIMIT {n}"

        with self._lock:
            rows = self._db.execute(sql, params).fetchall()

        return [{"id": r[0], "fact": r[1], "category": r[2], "score": 0.5} for r in rows]

    def recent(self, n: int = 10) -> list[dict]:
        """Ritorna gli N fatti più recenti."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, fact, category, timestamp FROM memories ORDER BY id DESC LIMIT ?",
                (n,)
            ).fetchall()
        return [{"id": r[0], "fact": r[1], "category": r[2], "timestamp": r[3]} for r in rows]

    def all(self) -> list[dict]:
        """Ritorna tutti i fatti (compatibilità con il vecchio JSON)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, fact, category, timestamp FROM memories ORDER BY id"
            ).fetchall()
        return [{"id": r[0], "fact": r[1], "category": r[2], "timestamp": r[3]} for r in rows]

    def delete(self, fact_id: int) -> bool:
        """Elimina un fatto per ID."""
        with self._lock:
            self._db.execute("DELETE FROM memories WHERE id = ?", (fact_id,))
            self._db.commit()
        if self._collection is not None:
            try:
                self._collection.delete(ids=[str(fact_id)])
            except Exception:
                pass
        return True

    def clear(self):
        """Cancella tutta la memoria."""
        with self._lock:
            self._db.execute("DELETE FROM memories")
            self._db.commit()
        if self._collection is not None:
            try:
                self._chroma.delete_collection("jarvis_memories")
                self._init_chroma()
            except Exception:
                pass

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def format_for_prompt(self, query: str = "", n: int = 6) -> str:
        """
        Ritorna una stringa formattata da iniettare nel system prompt.
        Se c'è una query, usa la ricerca semantica per trovare le memorie
        più rilevanti. Altrimenti prende le più recenti.
        """
        if query and self.count() > 0:
            mems = self.search(query, n=n)
        else:
            mems = self.recent(n)

        if not mems:
            return ""

        facts = [m["fact"] for m in mems]
        return " | Memoria: " + " ; ".join(facts)

    def save_json_backup(self):
        """Salva backup JSON per compatibilità."""
        backup_path = self._dir / "permanent.json"
        data = [{"timestamp": m["timestamp"], "fact": m["fact"]}
                for m in self.all()]
        backup_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
        )

    @property
    def permanent(self) -> list:
        """Compatibilità con il vecchio self.permanent (lista di dict)."""
        return self.all()

    def __len__(self):
        return self.count()
