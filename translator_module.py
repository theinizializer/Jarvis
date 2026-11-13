"""
translator_module.py — JARVIS v10
Modalità traduttore — sessione di traduzione interattiva tra due parlanti.

Cambiamenti v10:
- Non più importato inline da jarvis_v9.py solo in _run_translation_mode()
- Ora importato in cima da commands_module.py e disponibile come comando normale
- TranslatorSession è una dataclass con factory method new()
- Aggiunto get_supported_pairs() per la UI impostazioni

Uso da commands_module.py:
    from translator_module import TranslatorSession
    session = TranslatorSession.new(lang_a="it", lang_b="en", tts=tts_engine)
    risposta = session.handle_utterance("Ciao, come stai?")
    session.close()
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # evita import circolare con voice_module


# ---------------------------------------------------------------------------
# Lingue supportate
# ---------------------------------------------------------------------------

SUPPORTED_LANGS: dict[str, str] = {
    "it": "italiano",
    "en": "inglese",
    "fr": "francese",
    "de": "tedesco",
    "es": "spagnolo",
    "pt": "portoghese",
    "nl": "olandese",
    "ru": "russo",
    "zh": "cinese",
    "ja": "giapponese",
    "bg": "bulgaro",
    "lb": "lussemburghese",
}

# Parole di controllo per terminare / scartare (per lingua)
_STOP_WORDS: dict[str, list[str]] = {
    "it": ["fine", "stop", "esci", "termina", "chiudi"],
    "en": ["stop", "quit", "exit", "end", "close"],
    "fr": ["arrête", "stop", "quitter", "fin"],
    "de": ["stop", "ende", "beenden", "schluss"],
}

_UNDO_WORDS: dict[str, list[str]] = {
    "it": ["annulla", "ignorala", "torna indietro", "cancella"],
    "en": ["undo", "ignore", "go back", "cancel"],
    "fr": ["annuler", "ignorer"],
    "de": ["rückgängig", "ignorieren"],
}


def get_supported_pairs() -> list[dict]:
    """
    Ritorna la lista delle coppie di lingue supportate per la UI.
    """
    pairs = []
    codes = list(SUPPORTED_LANGS.keys())
    for i, a in enumerate(codes):
        for b in codes[i+1:]:
            pairs.append({
                "code_a": a, "name_a": SUPPORTED_LANGS[a],
                "code_b": b, "name_b": SUPPORTED_LANGS[b],
                "label": f"{SUPPORTED_LANGS[a]} ↔ {SUPPORTED_LANGS[b]}",
            })
    return pairs


# ---------------------------------------------------------------------------
# TranslatorSession
# ---------------------------------------------------------------------------

@dataclass
class TranslatorSession:
    """
    Sessione di traduzione tra due parlanti (lang_a e lang_b).

    Il modulo rileva automaticamente chi sta parlando in base alla lingua
    e traduce verso l'altra. Supporta comandi vocali di controllo
    (stop, annulla, ecc.) in entrambe le lingue.
    """

    lang_a: str                          # es. "it"
    lang_b: str                          # es. "en"
    tts: Optional[object]                # istanza TTSEngine (opzionale)
    ollama_fn: Optional[object] = None   # callable(prompt, lang) → str
    history: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _active: bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        if self.lang_a not in SUPPORTED_LANGS:
            raise ValueError(f"Lingua non supportata: {self.lang_a}")
        if self.lang_b not in SUPPORTED_LANGS:
            raise ValueError(f"Lingua non supportata: {self.lang_b}")

    # ------------------------------------------------------------------
    @classmethod
    def new(
        cls,
        lang_a: str = "it",
        lang_b: str = "en",
        tts=None,
        ollama_fn=None,
    ) -> "TranslatorSession":
        """Factory method — crea una nuova sessione di traduzione."""
        return cls(lang_a=lang_a, lang_b=lang_b, tts=tts, ollama_fn=ollama_fn)

    # ------------------------------------------------------------------
    def _speaker_of(self, text: str) -> str:
        """
        Rileva in quale lingua è scritto il testo (lang_a o lang_b).
        Usa euristica semplice: parole frequenti per lingua.
        """
        # Parole comuni per lingua (heuristic leggera)
        _markers: dict[str, list[str]] = {
            "it": ["il", "la", "lo", "che", "non", "per", "con", "una", "sono", "hai"],
            "en": ["the", "is", "are", "you", "and", "for", "that", "not", "have", "with"],
            "fr": ["le", "la", "les", "est", "pas", "que", "avec", "vous", "nous", "je"],
            "de": ["der", "die", "das", "ist", "nicht", "und", "für", "mit", "sie", "ich"],
            "es": ["el", "la", "los", "que", "no", "con", "para", "una", "son", "está"],
        }
        words = re.findall(r"\b\w+\b", text.lower())
        scores = {self.lang_a: 0, self.lang_b: 0}
        for lang in (self.lang_a, self.lang_b):
            markers = _markers.get(lang, [])
            scores[lang] = sum(1 for w in words if w in markers)
        if scores[self.lang_a] >= scores[self.lang_b]:
            return self.lang_a
        return self.lang_b

    def _parse_control(self, text: str, lang: str) -> Optional[str]:
        """Controlla se il testo è un comando di controllo (stop, undo, ecc.)."""
        t = text.lower().strip()
        for stop in _STOP_WORDS.get(lang, []):
            if stop in t:
                return "stop"
        for undo in _UNDO_WORDS.get(lang, []):
            if undo in t:
                return "undo"
        return None

    def _decide(self, source_lang: str) -> str:
        """Ritorna la lingua di destinazione per la traduzione."""
        return self.lang_b if source_lang == self.lang_a else self.lang_a

    def _come_si_dice(self, text: str, from_lang: str, to_lang: str) -> str:
        """
        Traduce il testo usando Ollama se disponibile,
        altrimenti usa una traduzione inline minimale (solo per test).
        """
        if self.ollama_fn:
            from_name = SUPPORTED_LANGS.get(from_lang, from_lang)
            to_name   = SUPPORTED_LANGS.get(to_lang, to_lang)
            prompt = (
                f"Traduci questo testo da {from_name} a {to_name}. "
                f"Rispondi SOLO con la traduzione, senza spiegazioni:\n\n{text}"
            )
            try:
                return self.ollama_fn(prompt, to_lang)
            except Exception as e:
                return f"[Errore traduzione: {e}]"

        # Fallback minimale (placeholder)
        return f"[Traduzione {from_lang}→{to_lang}]: {text}"

    # ------------------------------------------------------------------
    def handle_utterance(self, text: str) -> Optional[str]:
        """
        Processa un'utterance dell'utente.

        Returns:
            str  → la traduzione da pronunciare/mostrare
            None → sessione terminata (stop ricevuto)
        """
        if not self._active:
            return None

        with self._lock:
            source_lang = self._speaker_of(text)
            control     = self._parse_control(text, source_lang)

            if control == "stop":
                self._active = False
                farewell = {
                    "it": "Traduzione terminata.",
                    "en": "Translation ended.",
                    "fr": "Traduction terminée.",
                    "de": "Übersetzung beendet.",
                }.get(source_lang, "Done.")
                return farewell

            if control == "undo":
                self.scarta_ultimo_turno()
                return {
                    "it": "Ultimo turno annullato.",
                    "en": "Last turn discarded.",
                }.get(source_lang, "Undone.")

            target_lang  = self._decide(source_lang)
            translation  = self._come_si_dice(text, source_lang, target_lang)

            self.history.append({
                "source_lang": source_lang,
                "target_lang": target_lang,
                "original":    text,
                "translation": translation,
            })

            # TTS opzionale
            if self.tts:
                try:
                    self.tts.say(translation, lang=target_lang)
                except Exception:
                    pass

            return translation

    def scarta_ultimo_turno(self) -> None:
        """Rimuove l'ultimo turno dalla cronologia (comando 'ignorala' / KyGlove)."""
        if self.history:
            self.history.pop()

    def close(self) -> None:
        """Termina la sessione e libera risorse."""
        self._active = False
        self.history.clear()

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def turn_count(self) -> int:
        return len(self.history)

    def __repr__(self) -> str:
        status = "attiva" if self._active else "chiusa"
        return (
            f"<TranslatorSession {self.lang_a}↔{self.lang_b} "
            f"turni={self.turn_count} {status}>"
        )
