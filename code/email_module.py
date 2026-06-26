"""
email_module.py — Integrazione Gmail (IMAP/SMTP) per JARVIS
============================================================
Mixin JarvisEmailMixin con i metodi che diventano tool call:

  - email_check()              → quante email importanti non lette (mittente+oggetto)
  - email_read(uid)            → legge il corpo di una email specifica
  - email_search(query)        → cerca email per mittente/oggetto/testo
  - email_send(to, subj, body) → invia (la CONFERMA è gestita da jarvis_v9)

Filtro promo: le email pubblicitarie vengono riconosciute da header
(List-Unsubscribe), mittente (noreply/newsletter) e parole chiave nell'oggetto,
poi SPOSTATE nell'etichetta "JARVIS/Promo" (mai cancellate) e marcate lette.
Le email importanti restano NON lette finché l'utente non le apre davvero.

Credenziali: lette dal SecretsManager (cifrate) o da env:
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD

Per ottenere una App Password:
  Google Account → Sicurezza → Verifica in 2 passaggi → Password per le app
"""

import email as _email
import imaplib
import json
import re
import smtplib
import ssl
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
PROMO_LABEL = "JARVIS/Promo"
STATE_FILE = Path.home() / "jarvis_memory" / "email_state.json"

# ── Segnali di filtro ─────────────────────────────────────────────────────────
_PROMO_SUBJECT = re.compile(
    r"(\b(sconto|offerta|saldi|solde|promo|deal|newsletter|coupon|black\s*friday|"
    r"cyber\s*monday|sale|outlet|gratis|free|win|vinci|regalo|ultim[oi]\s+giorn|"
    r"non\s+perder|iscriviti|unsubscribe|réduction|remise|bon\s+plan)\b|%|🔥|🎉|🍔|☀️|🛍️|💸)",
    re.IGNORECASE,
)
_IMPORTANT_SUBJECT = re.compile(
    r"\b(ordine|commande|order|spedit|expédié|shipped|consegna|livraison|delivery|"
    r"tracking|fattura|facture|invoice|conferma|confirm|codice|code|verifica|verify|"
    r"sicurezza|security|password|accesso|login|appuntamento|rendez-vous|scadenz|"
    r"pagamento|payment|paiement|ricevuta|reçu|receipt|pacco|colis|package)\b",
    re.IGNORECASE,
)
_PROMO_SENDER = re.compile(
    r"(noreply|no-reply|newsletter|marketing|promo|notifications?|mailer|info@|"
    r"news@|offers?@|deals?@)",
    re.IGNORECASE,
)


def _decode(s) -> str:
    """Decodifica header MIME (oggetti/mittenti con caratteri non-ASCII)."""
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                out.append(part.decode("utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def classify_email(subject: str, sender: str, has_list_unsub: bool) -> str:
    """
    Classifica una email come 'promo' | 'important' | 'unknown'.
    Logica a livelli — la maggior parte si risolve senza scomodare l'AI.
    """
    subj = subject or ""
    snd = sender or ""

    # Livello 1: header tecnico — la prova più forte di promo
    if has_list_unsub:
        # Ma se l'oggetto ha un forte segnale "importante", non scartare subito
        if _IMPORTANT_SUBJECT.search(subj):
            return "important"
        return "promo"

    # Livello 2: parole chiave oggetto
    if _IMPORTANT_SUBJECT.search(subj):
        return "important"
    if _PROMO_SUBJECT.search(subj):
        return "promo"

    # Livello 3: mittente
    if _PROMO_SENDER.search(snd):
        return "promo"

    # Indeciso → l'AI può decidere (raro)
    return "unknown"


class JarvisEmailMixin:
    """Mixin Gmail per JARVIS. Non istanziare direttamente."""

    # ── Credenziali ─────────────────────────────────────────────────────────

    def _email_creds(self):
        """Ritorna (address, app_password) da SecretsManager o env, o (None, None)."""
        import os
        addr = os.environ.get("GMAIL_ADDRESS", "")
        pwd = os.environ.get("GMAIL_APP_PASSWORD", "")
        sm = getattr(self, "_secrets_mgr", None)
        if sm:
            addr = addr or sm.get("GMAIL_ADDRESS", "")
            pwd = pwd or sm.get("GMAIL_APP_PASSWORD", "")
        return (addr or None, pwd or None)

    def _imap_connect(self):
        addr, pwd = self._email_creds()
        if not addr or not pwd:
            return None, "Credenziali Gmail non configurate (GMAIL_ADDRESS / GMAIL_APP_PASSWORD)"
        try:
            m = imaplib.IMAP4_SSL(IMAP_HOST)
            m.login(addr, pwd)
            return m, None
        except Exception as e:
            return None, f"Login IMAP fallito: {e}"

    # ── Stato (UID già processati) ──────────────────────────────────────────

    def _email_state(self) -> dict:
        try:
            s = json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:
            s = {}
        # Struttura completa con default
        s.setdefault("processed_uids", [])   # UID già visti (per non riprocessare)
        s.setdefault("last_check", None)      # ISO data ultimo controllo
        s.setdefault("important", [])         # ultime importanti segnalate (sessione)
        s.setdefault("history", [])           # STORICO: tutte le importanti viste
        s.setdefault("checks", [])            # storico controlli (per il bot)
        return s

    @staticmethod
    def _dedup_records(records):
        """Deduplica record-email per UID, preservando l'ordine di prima apparizione
        e conservando handled=True se una qualsiasi copia era già gestita."""
        best = {}
        for r in records:
            u = r.get("uid")
            if u is None:
                continue
            if u in best:
                # mantieni i dati più recenti ma non perdere un handled già True
                h = bool(best[u].get("handled")) or bool(r.get("handled"))
                best[u] = dict(r)
                best[u]["handled"] = h
            else:
                best[u] = dict(r)
        out, done = [], set()
        for r in records:
            u = r.get("uid")
            if u is None or u in done:
                continue
            done.add(u)
            out.append(best[u])
        return out

    @staticmethod
    def _dedup_uids(uids):
        out, done = [], set()
        for u in uids:
            if u in done:
                continue
            done.add(u)
            out.append(u)
        return out

    def _save_email_state(self, state: dict):
        try:
            # Deduplica in UN punto solo: qualunque percorso aggiunga un doppione,
            # lo stato salvato è sempre pulito (niente più mail ripetute).
            state["processed_uids"] = self._dedup_uids(state.get("processed_uids", []))[-500:]
            state["important"]      = self._dedup_records(state.get("important", []))
            state["history"]        = self._dedup_records(state.get("history", []))[-300:]
            if isinstance(state.get("checks"), list):
                state["checks"] = state["checks"][-20:]   # non far crescere all'infinito

            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Scrittura ATOMICA: scrivi su un temp e rinomina. Così non resti mai
            # con un file mezzo scritto se il processo muore durante il salvataggio.
            tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(STATE_FILE)
        except Exception:
            pass

    # ── Etichetta promo ───────────────────────────────────────────────────────

    def _ensure_promo_label(self, m):
        try:
            m.create(f'"{PROMO_LABEL}"')  # fallisce se esiste già — ok
        except Exception:
            pass

    # ── TOOL: controlla nuove email importanti ────────────────────────────────

    def email_check(self) -> str:
        """
        Controlla le email NON lette. Sposta le promo in JARVIS/Promo (marca lette),
        lascia le importanti non lette e le elenca. Non riprocessa le già viste.
        """
        m, err = self._imap_connect()
        if err:
            return f"❌ {err}"
        try:
            self._ensure_promo_label(m)
            m.select("INBOX")
            typ, data = m.search(None, "UNSEEN")
            if typ != "OK":
                return "❌ Errore ricerca email"
            uids = data[0].split()
            if not uids:
                return "📭 Nessuna nuova email."

            state = self._email_state()
            processed = set(state.get("processed_uids", []))
            important = []
            promo_count = 0

            from datetime import datetime
            for num in uids:
                # Header + UID in un'unica fetch (più affidabile)
                typ, msg_data = m.fetch(num, "(UID BODY.PEEK[HEADER])")
                if typ != "OK" or not msg_data:
                    continue
                # msg_data può contenere None o elementi non-tupla — filtra
                raw_header = None
                uid = None
                for item in msg_data:
                    if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                        raw_header = item[1]
                        # l'UID è nella parte testuale dell'elemento (item[0])
                        meta = item[0].decode(errors="replace") if isinstance(item[0], bytes) else str(item[0])
                        mu = re.search(r"UID (\d+)", meta)
                        if mu:
                            uid = mu.group(1)
                if raw_header is None:
                    continue
                if uid is None:
                    uid = num.decode() if isinstance(num, bytes) else str(num)
                if uid in processed:
                    continue
                msg = _email.message_from_bytes(raw_header)
                subject = _decode(msg.get("Subject", ""))
                sender_raw = _decode(msg.get("From", ""))
                sender_name, sender_addr = parseaddr(sender_raw)
                has_unsub = bool(msg.get("List-Unsubscribe"))

                kind = classify_email(subject, sender_addr or sender_name, has_unsub)

                if kind == "promo":
                    # Sposta in JARVIS/Promo e marca letta (mai cancellata)
                    try:
                        m.copy(num, f'"{PROMO_LABEL}"')
                        m.store(num, "+FLAGS", "\\Seen")
                        m.store(num, "+FLAGS", "\\Deleted")  # rimuove da INBOX (copia salva)
                    except Exception:
                        pass
                    promo_count += 1
                    processed.add(uid)
                else:
                    # Importante o incerto → lascia NON letta, segnala
                    rec = {
                        "uid": uid,
                        "from": sender_name or sender_addr,
                        "from_addr": sender_addr,
                        "subject": subject,
                        "kind": kind,
                        "seen_at": datetime.now().isoformat(),
                        "handled": False,   # diventa True quando la leggi/archivi
                    }
                    important.append(rec)
                    processed.add(uid)

            try:
                m.expunge()  # applica i \Deleted (le promo erano già copiate)
            except Exception:
                pass

            state["processed_uids"] = list(processed)[-500:]  # tieni le ultime 500
            state["last_check"] = datetime.now().isoformat()

            # STORICO persistente: aggiungi le nuove importanti (evita duplicati per UID)
            history = state.get("history", [])
            known_uids = {h["uid"] for h in history}
            for rec in important:
                if rec["uid"] not in known_uids:
                    history.append(rec)
            state["history"] = history[-300:]  # tieni le ultime 300

            prev_important = state.get("important", [])
            # Non perdere le segnalazioni precedenti se non ci sono email nuove
            state["important"] = important if important else prev_important
            self._save_email_state(state)

            # Risposta riassuntiva
            if not important:
                # Se non ci sono email NUOVE ma ne avevamo segnalate di recente,
                # ricordale invece di dire "nessuna" (evita la contraddizione
                # quando il modello richiama check dopo un "leggila").
                if prev_important:
                    lines = ["📭 Nessuna email NUOVA. Le ultime importanti che ti ho segnalato:"]
                    for e in prev_important:
                        lines.append(f"  • [{e['uid']}] da {e['from']} — {e['subject']}")
                    lines.append("\n(Dimmi 'leggila' o il numero per aprirne una)")
                    return "\n".join(lines)
                if promo_count:
                    return f"📭 Nessuna nuova email importante ({promo_count} promo archiviate in {PROMO_LABEL})."
                return "📭 Nessuna nuova email da controllare."
            lines = [f"📬 {len(important)} email importanti"
                     + (f" ({promo_count} promo archiviate):" if promo_count else ":")]
            for e in important:
                lines.append(f"  • [{e['uid']}] da {e['from']} — {e['subject']}")
            lines.append("\n(Chiedimi di leggerne una specifica con il suo numero)")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Errore controllo email: {e}"
        finally:
            try: m.logout()
            except Exception: pass

    # ── TOOL: leggi una email specifica ───────────────────────────────────────

    def email_read_last(self) -> str:
        """Legge l'ultima email importante segnalata da email_check.
        Usato quando l'utente dice 'leggila' senza ripetere l'UID."""
        state = self._email_state()
        important = state.get("important", [])
        if not important:
            return ("Non ho un'email importante recente da leggere. "
                    "Chiedimi prima di controllare le nuove email.")
        # L'ultima segnalata (la più recente della lista)
        uid = important[-1]["uid"]
        return self.email_read(uid)

    def email_read(self, uid: str) -> str:
        """Legge il corpo di una email per UID. NON la marca letta automaticamente."""
        m, err = self._imap_connect()
        if err:
            return f"❌ {err}"
        try:
            m.select("INBOX")
            typ, msg_data = m.uid("fetch", str(uid), "(BODY.PEEK[])")
            if (typ != "OK" or not msg_data or not msg_data[0]
                    or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2):
                return f"❌ Email {uid} non trovata"
            msg = _email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject", ""))
            sender = _decode(msg.get("From", ""))
            body = self._extract_body(msg)
            # Segna come "gestita" nello storico (l'hai letta)
            try:
                state = self._email_state()
                for h in state.get("history", []):
                    if h["uid"] == str(uid):
                        h["handled"] = True
                self._save_email_state(state)
            except Exception:
                pass
            return (f"📧 Da: {sender}\nOggetto: {subject}\n"
                    f"{'─'*40}\n{body[:3000]}")
        except Exception as e:
            return f"❌ Errore lettura: {e}"
        finally:
            try: m.logout()
            except Exception: pass

    def _extract_body(self, msg) -> str:
        """Estrae il testo dal messaggio (preferisce text/plain)."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        return part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace")
                    except Exception:
                        continue
            # fallback: primo testo disponibile
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        html = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace")
                        return re.sub(r"<[^>]+>", "", html)  # strip HTML grezzo
                    except Exception:
                        continue
            return "(nessun testo leggibile)"
        else:
            try:
                return msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                return "(nessun testo leggibile)"

    # ── TOOL: cerca email ──────────────────────────────────────────────────────

    def email_search(self, query: str, limit: int = 10) -> str:
        """Cerca email per mittente, oggetto o testo. Ritorna lista con UID."""
        m, err = self._imap_connect()
        if err:
            return f"❌ {err}"
        try:
            m.select("INBOX")
            # Cerca in OGGETTO e MITTENTE (Gmail supporta anche TEXT per il corpo)
            q = query.strip()
            typ, data = m.uid("search", None,
                              f'(OR (OR SUBJECT "{q}" FROM "{q}") TEXT "{q}")')
            if typ != "OK":
                return f"❌ Errore ricerca per '{q}'"
            uids = data[0].split()
            if not uids:
                return f"🔍 Nessuna email trovata per '{q}'"
            uids = uids[-limit:]  # le più recenti
            lines = [f"🔍 {len(uids)} risultati per '{q}':"]
            for u in reversed(uids):
                typ, md = m.uid("fetch", u, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if typ != "OK" or not md or not md[0] or not isinstance(md[0], tuple):
                    continue
                msg = _email.message_from_bytes(md[0][1])
                subj = _decode(msg.get("Subject", ""))
                frm = _decode(msg.get("From", ""))
                _, frm_addr = parseaddr(frm)
                uid_str = u.decode() if isinstance(u, bytes) else str(u)
                lines.append(f"  • [{uid_str}] {frm_addr or frm} — {subj}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Errore ricerca: {e}"
        finally:
            try: m.logout()
            except Exception: pass

    # ── TOOL: archivia una email (toglie dalla inbox) ─────────────────────────

    def email_archive(self, uid: str, label: str = None) -> str:
        """
        Archivia una email: la toglie dalla inbox. Se `label` è dato, la sposta
        in quell'etichetta (creandola se serve); altrimenti la archivia
        semplicemente (resta in 'Tutti i messaggi' di Gmail, fuori dalla inbox).
        """
        m, err = self._imap_connect()
        if err:
            return f"❌ {err}"
        try:
            m.select("INBOX")
            if label:
                self._make_label(m, label)
                m.uid("copy", str(uid), f'"{label}"')
            # In Gmail, rimuovere da INBOX = archiviare. Si fa con \Deleted+expunge
            # sulla cartella INBOX (la mail resta in "Tutti i messaggi").
            m.uid("store", str(uid), "+FLAGS", "\\Deleted")
            m.expunge()
            # Segna gestita nello storico
            try:
                state = self._email_state()
                for h in state.get("history", []):
                    if h["uid"] == str(uid):
                        h["handled"] = True
                        h["archived"] = True
                self._save_email_state(state)
            except Exception:
                pass
            where = f"in '{label}'" if label else "(fuori dalla inbox)"
            return f"📦 Email {uid} archiviata {where}."
        except Exception as e:
            return f"❌ Errore archiviazione: {e}"
        finally:
            try: m.logout()
            except Exception: pass

    # ── TOOL: crea una nuova etichetta ────────────────────────────────────────

    def _make_label(self, m, name: str):
        try:
            m.create(f'"{name}"')
        except Exception:
            pass

    def email_label(self, name: str) -> str:
        """Crea una nuova etichetta Gmail."""
        m, err = self._imap_connect()
        if err:
            return f"❌ {err}"
        try:
            self._make_label(m, name)
            return f"🏷️ Etichetta '{name}' creata (o già esistente)."
        except Exception as e:
            return f"❌ Errore creazione etichetta: {e}"
        finally:
            try: m.logout()
            except Exception: pass

    # ── TOOL: storico email importanti ────────────────────────────────────────

    def email_history(self, only_pending: bool = False) -> str:
        """Mostra lo storico delle email importanti viste (UID, mittente, oggetto).
        only_pending=True → solo quelle non ancora gestite (lette/archiviate)."""
        state = self._email_state()
        hist = state.get("history", [])
        if only_pending:
            hist = [h for h in hist if not h.get("handled")]
        if not hist:
            return "📭 Nessuna email importante nello storico."
        lines = [f"🗂️ Storico email importanti ({len(hist)}):"]
        for h in hist[-30:]:
            mark = "○" if not h.get("handled") else "●"
            lines.append(f"  {mark} [{h['uid']}] da {h['from']} — {h['subject']}")
        lines.append("\n(○ = da gestire, ● = già letta/archiviata)")
        return "\n".join(lines)

    # ── BOT: controllo giornaliero automatico ─────────────────────────────────

    def email_mark_check_state(self, done: bool):
        """Alla chiusura di JARVIS: salva data/ora e se il controllo è stato fatto.
        Usato dalla logica del bot per sapere se c'è un 'buco' da recuperare."""
        try:
            state = self._email_state()
            from datetime import datetime
            checks = state.get("checks", [])
            checks.append({"when": datetime.now().isoformat(), "done": bool(done)})
            # Pulizia: tieni solo i controlli degli ultimi 2 giorni (staccato dalle email)
            cutoff = datetime.now().timestamp() - 2 * 86400
            checks = [c for c in checks
                      if datetime.fromisoformat(c["when"]).timestamp() >= cutoff]
            state["checks"] = checks[-50:]
            self._save_email_state(state)
        except Exception:
            pass

    def email_daily_check_if_due(self, hour_pref: int = 6, max_gap_hours: int = 20) -> str:
        """
        Bot di controllo all'avvio. Logica (senza buchi):
        - Se l'ultimo controllo RIUSCITO è più vecchio di max_gap_hours → controlla
          (indipendentemente dall'ora: copre il 'buco' del giorno saltato).
        - Se è recente (< max_gap_hours) → non fa nulla.
        L'ora preferita (6 del mattino) è una preferenza: se è passato troppo
        tempo si controlla comunque, anche prima delle 6.
        Ritorna il riassunto se ha controllato, altrimenti stringa vuota.
        """
        if not getattr(self, "_email_enabled", False):
            return ""
        try:
            from datetime import datetime
            state = self._email_state()
            last = state.get("last_check")
            now = datetime.now()

            due = False
            if not last:
                due = True   # mai controllato → controlla
            else:
                gap_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600
                if gap_h >= max_gap_hours:
                    due = True

            if not due:
                return ""
            # Controlla (email_check aggiorna last_check e lo storico)
            result = self.email_check()
            self.email_mark_check_state(done=True)
            return result
        except Exception:
            return ""

    # ── TOOL: invia email (la conferma è in jarvis_v9) ────────────────────────

    def email_send(self, to: str, subject: str, body: str) -> str:
        """Invia una email. La conferma utente è gestita PRIMA, in jarvis_v9."""
        addr, pwd = self._email_creds()
        if not addr or not pwd:
            return "❌ Credenziali Gmail non configurate"
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = addr
            msg["To"] = to
            msg["Subject"] = subject
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
                s.login(addr, pwd)
                s.sendmail(addr, [to], msg.as_string())
            return f"✅ Email inviata a {to}"
        except Exception as e:
            return f"❌ Invio fallito: {e}"