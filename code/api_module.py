"""
api_module.py — Provider LLM di JARVIS
========================================
Contiene i metodi di chiamata ai modelli AI come mixin class (JarvisAPIMixin).

Provider supportati:
  - Groq          (cervello principale, veloce, 120B)
  - Cerebras      (fallback Groq, 70B gratuito)
  - NVIDIA NIM    (vision + task pesanti, Qwen 397B con thinking)
  - Ollama        (fallback locale)

Uso in jarvis_v9.py:
    from api_module import JarvisAPIMixin

    class Jarvis(JarvisAPIMixin, ...):
        ...

I metodi del mixin usano self.* già presenti in Jarvis:
    self._tts_say(), self.model, self.vmodel,
    self._groq_available, self._cerebras_available, self._nvidia_available,
    self._history, self._lock, self._stats, self._err(),
    self._sys_prompt(), self._tools(), self._clean_for_history(),
    self._capture_screen()
"""

import json
import subprocess
import time

import requests

from utils_module import (
    GROQ_URL, GROQ_MODEL, GROQ_API_KEY,
    CEREBRAS_URL, CEREBRAS_MODEL, CEREBRAS_API_KEY,
    NVIDIA_URL, NVIDIA_MODEL, NVIDIA_API_KEY,
    OLLAMA_URL,
    HEAVY_TASK_KEYWORDS,
    MAX_HISTORY, _CPU_THREADS,
    _RE_JSON_TOOL,
)

try:
    from PIL import ImageGrab
    PIL_OK = True
except Exception:
    PIL_OK = False


def _check_stream_error(data: dict, provider: str = "provider"):
    """
    Controlla se un chunk di streaming contiene un errore di quota/rate-limit.
    Alcuni provider (Groq incluso) non lanciano HTTP 429 ma mandano l'errore
    dentro lo stream con HTTP 200. Senza questo controllo il fallback non
    scatta mai e JARVIS si blocca senza risposta.

    Lancia HTTPError(429) per quota/rate-limit → triggera il fallback.
    Lancia Exception generica per altri errori dello stream.
    """
    if not isinstance(data, dict) or "error" not in data:
        return
    err = data["error"]
    if isinstance(err, str):
        err_type, err_msg = "", err
    else:
        err_type = (err.get("type") or err.get("code") or "").lower()
        err_msg  = (err.get("message") or "").lower()

    quota_markers = ("rate_limit", "rate-limit", "quota", "insufficient",
                     "exceeded", "too many", "tokens per")
    if any(m in err_type for m in quota_markers) or any(m in err_msg for m in quota_markers):
        fake_resp = type("R", (), {"status_code": 429})()
        raise requests.exceptions.HTTPError(
            f"{provider}: quota/rate-limit esaurito", response=fake_resp
        )
    raise Exception(f"{provider} stream error: {err_msg or err_type or 'sconosciuto'}")


class JarvisAPIMixin:
    """
    Mixin con tutti i metodi di chiamata ai provider LLM.
    Ereditato da Jarvis — non istanziare direttamente.
    """

    # ── Groq ──────────────────────────────────────────────────────────────────

    def _call_groq(self, messages: list, tools: list) -> tuple[str, list]:
        """Chiama Groq API (OpenAI-compatible). Ritorna (full_text, tool_calls)."""
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model":       GROQ_MODEL,
            "messages":    messages,
            "tools":       tools if tools else [],
            "tool_choice": "auto" if tools else "none",
            "temperature": 0.3,
            "max_tokens":  1024,
            "stream":      True,
            "stream_options": {"include_usage": True},
        }
        full_text  = ""
        tool_calls = []
        tts_buf    = ""
        _tc_buf: dict[int, dict] = {}

        with requests.post(GROQ_URL, json=payload, headers=headers,
                           timeout=60, stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line_s = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_s.startswith("data: "):
                    line_s = line_s[6:]
                if line_s.strip() in ("", "[DONE]"):
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                _check_stream_error(data, "Groq")
                if isinstance(data, dict) and data.get("usage"):
                    self._record_tokens("Groq", data["usage"])
                choice = data.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                chunk  = delta.get("content") or ""
                if chunk:
                    full_text += chunk
                    tts_buf   += chunk
                    print(chunk, end="", flush=True)
                    if any(c in chunk for c in ('.', '!', '?', '\n')):
                        self._tts_say(tts_buf.strip())
                        tts_buf = ""
                for tc_delta in delta.get("tool_calls", []):
                    idx2 = tc_delta.get("index", 0)
                    if idx2 not in _tc_buf:
                        _tc_buf[idx2] = {"id": tc_delta.get("id", ""), "type": "function",
                                         "function": {"name": "", "arguments": ""}}
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):      _tc_buf[idx2]["function"]["name"]      += fn["name"]
                    if fn.get("arguments"): _tc_buf[idx2]["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason") in ("stop", "tool_calls", "length"):
                    break

        if tts_buf.strip():
            self._tts_say(tts_buf.strip())
        print()

        for tc in sorted(_tc_buf.values(), key=lambda x: x.get("id", "")):
            try:
                args = json.loads(tc["function"].get("arguments", "{}") or "{}")
                tool_calls.append({"function": {"name": tc["function"]["name"], "arguments": args}})
            except Exception:
                pass
        return full_text, tool_calls

    # ── Cerebras ──────────────────────────────────────────────────────────────

    def _call_cerebras(self, messages: list, tools: list) -> tuple[str, list]:
        """Chiama Cerebras API (OpenAI-compatible). Fallback di Groq."""
        headers = {
            "Authorization": f"Bearer {CEREBRAS_API_KEY}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model":       CEREBRAS_MODEL,
            "messages":    messages,
            "tools":       tools if tools else [],
            "tool_choice": "auto" if tools else "none",
            "temperature": 0.3,
            "max_tokens":  1024,
            "stream":      True,
            "stream_options": {"include_usage": True},
        }
        full_text  = ""
        tool_calls = []
        tts_buf    = ""
        _tc_buf: dict[int, dict] = {}

        print("\n🟡 Cerebras (fallback Groq)...", flush=True)
        with requests.post(CEREBRAS_URL, json=payload, headers=headers,
                           timeout=60, stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line_s = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_s.startswith("data: "):
                    line_s = line_s[6:]
                if line_s.strip() in ("", "[DONE]"):
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                _check_stream_error(data, "Cerebras")
                if isinstance(data, dict) and data.get("usage"):
                    self._record_tokens("Cerebras", data["usage"])
                choice = data.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                chunk  = delta.get("content") or ""
                if chunk:
                    full_text += chunk
                    tts_buf   += chunk
                    print(chunk, end="", flush=True)
                    if any(c in chunk for c in ('.', '!', '?', '\n')):
                        self._tts_say(tts_buf.strip())
                        tts_buf = ""
                for tc_delta in delta.get("tool_calls", []):
                    idx2 = tc_delta.get("index", 0)
                    if idx2 not in _tc_buf:
                        _tc_buf[idx2] = {"id": tc_delta.get("id", ""), "type": "function",
                                         "function": {"name": "", "arguments": ""}}
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):      _tc_buf[idx2]["function"]["name"]      += fn["name"]
                    if fn.get("arguments"): _tc_buf[idx2]["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason") in ("stop", "tool_calls", "length"):
                    break

        if tts_buf.strip():
            self._tts_say(tts_buf.strip())
        print()

        for tc in sorted(_tc_buf.values(), key=lambda x: x.get("id", "")):
            try:
                args = json.loads(tc["function"].get("arguments", "{}") or "{}")
                tool_calls.append({"function": {"name": tc["function"]["name"], "arguments": args}})
            except Exception:
                pass
        return full_text, tool_calls

    # ── NVIDIA NIM ────────────────────────────────────────────────────────────

    def _call_nvidia(self, messages: list, tools: list,
                     image_b64: str = "") -> tuple[str, list]:
        """
        Chiama NVIDIA NIM (Qwen 397B) — vision + task pesanti.
        Usa il formato ufficiale NVIDIA con enable_thinking=True.
        Se image_b64 è fornito, lo inietta come contenuto multimodale.
        Ritorna (full_text, tool_calls).
        """
        headers = {
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Accept":        "text/event-stream",
            "Content-Type":  "application/json",
        }

        msgs = list(messages)
        if image_b64:
            last_text = msgs[-1].get("content", "Analizza questa immagine.") if msgs else "Analizza questa immagine."
            vision_content = [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": last_text}
            ]
            msgs = msgs[:-1] + [{"role": "user", "content": vision_content}]

        payload = {
            "model":              NVIDIA_MODEL,
            "messages":           msgs,
            "max_tokens":         4096,
            "temperature":        0.5,
            "top_p":              0.97,
            "top_k":              20,
            "presence_penalty":   0,
            "repetition_penalty": 1,
            "stream":             True,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        if tools and not image_b64:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"

        full_text  = ""
        think_text = ""
        tool_calls = []
        tts_buf    = ""
        in_think   = False
        _tc_buf: dict[int, dict] = {}

        print("\n🟣 NVIDIA Qwen 397B (thinking enabled)...", flush=True)
        try:
            resp_nvidia = requests.post(NVIDIA_URL, json=payload, headers=headers,
                                        timeout=180, stream=True)
            resp_nvidia.raise_for_status()
        except Exception as _nvidia_exc:
            self._err("NVIDIA.request", exc=_nvidia_exc)
            raise

        with resp_nvidia as resp:
            for line in resp.iter_lines():
                if not line:
                    continue
                line_s = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_s.startswith("data: "):
                    line_s = line_s[6:]
                if line_s.strip() in ("", "[DONE]"):
                    continue
                try:
                    data = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                _check_stream_error(data, "NVIDIA")
                if isinstance(data, dict) and data.get("usage"):
                    self._record_tokens("NVIDIA", data["usage"])
                choice = data.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                chunk  = delta.get("content") or ""
                if chunk:
                    if "<think>" in chunk:
                        in_think = True
                        print("\n🤔 [Qwen sta pensando...]", flush=True)
                    if "</think>" in chunk:
                        in_think = False
                        print("\n🟣 [Fine ragionamento]\n", flush=True)
                        continue
                    if in_think:
                        think_text += chunk
                        print(chunk, end="", flush=True)
                        continue
                    full_text += chunk
                    tts_buf   += chunk
                    print(chunk, end="", flush=True)
                    if any(c in chunk for c in ('.', '!', '?', '\n')):
                        self._tts_say(tts_buf.strip())
                        tts_buf = ""
                for tc_delta in delta.get("tool_calls", []):
                    idx2 = tc_delta.get("index", 0)
                    if idx2 not in _tc_buf:
                        _tc_buf[idx2] = {"id": tc_delta.get("id", ""), "type": "function",
                                         "function": {"name": "", "arguments": ""}}
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):      _tc_buf[idx2]["function"]["name"]      += fn["name"]
                    if fn.get("arguments"): _tc_buf[idx2]["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason") in ("stop", "tool_calls", "length"):
                    break

        if tts_buf.strip():
            self._tts_say(tts_buf.strip())
        print()

        for tc in sorted(_tc_buf.values(), key=lambda x: x.get("id", "")):
            try:
                args = json.loads(tc["function"].get("arguments", "{}") or "{}")
                tool_calls.append({"function": {"name": tc["function"]["name"], "arguments": args}})
            except Exception:
                pass
        return full_text, tool_calls

    # ── Router principale ─────────────────────────────────────────────────────

    def _call_model(self, user_msg, with_image=False, history=None,
                    web_context="", speaker_name: str = None):
        """
        Routing: Groq → Cerebras → NVIDIA → Ollama.
        Seleziona automaticamente il provider in base alla disponibilità
        e alla natura del task (heavy keywords → NVIDIA).
        """
        self._stats["calls"] += 1
        user_entry = {"role": "user", "content": user_msg}

        use_vision = with_image and PIL_OK and self.vmodel != self.model
        if use_vision:
            img = self._capture_screen()
            if img:
                user_entry["images"] = [img]
            else:
                use_vision = False

        hist        = history if history is not None else self._history
        sys_content = self._sys_prompt(query=user_msg, speaker_name=speaker_name)
        if web_context:
            sys_content += (
                "\n\n[RISULTATI RICERCA WEB — usa questi dati per rispondere:]\n"
                + web_context[:2500] +
                "\n[Fine risultati. Rispondi basandoti su questi dati reali. NON inventare prezzi o link.]"
            )
        messages = [
            {"role": "system", "content": sys_content},
            *hist,
            user_entry
        ]
        tools = self._tools()

        # ── Groq (cervello principale) ────────────────────────────────────────
        if self._groq_available and not use_vision:
            is_heavy = bool(HEAVY_TASK_KEYWORDS.search(user_msg)) if self._nvidia_available else False
            try:
                if is_heavy:
                    print(f"\n🟣 Task pesante — Qwen 397B", flush=True)
                    full_text, tool_calls = self._call_nvidia(messages, tools)
                else:
                    full_text, tool_calls = self._call_groq(messages, tools)
                if history is None:
                    with self._lock:
                        self._history.append({"role": "user", "content": user_msg})
                        clean = self._clean_for_history(full_text)
                        if clean:
                            self._history.append({"role": "assistant", "content": clean})
                        if len(self._history) > MAX_HISTORY * 2:
                            self._history = self._history[-(MAX_HISTORY * 2):]
                return full_text, tool_calls
            except requests.exceptions.ConnectionError as e:
                print("\n⚠️  Groq non raggiungibile — fallback Cerebras", flush=True)
                self._err("Groq.connection", exc=e)
                self._groq_available = False
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response else "?"
                if code == 429:
                    print(f"\n⚠️  Groq token esauriti (429) — fallback Cerebras", flush=True)
                else:
                    if code == 401:
                        self._groq_available = False
                    print(f"\n⚠️  Groq errore {code} — fallback Cerebras", flush=True)
                self._err("Groq.http", exc=e, msg=f"HTTP {code}")
            except Exception as e:
                print(f"\n⚠️  Groq errore ({e}) — fallback Cerebras", flush=True)
                self._err("Groq.generic", exc=e)

        # ── Fallback 1: Cerebras ──────────────────────────────────────────────
        if self._cerebras_available and not use_vision:
            try:
                full_text, tool_calls = self._call_cerebras(messages, tools)
                if history is None:
                    with self._lock:
                        self._history.append({"role": "user", "content": user_msg})
                        clean = self._clean_for_history(full_text)
                        if clean:
                            self._history.append({"role": "assistant", "content": clean})
                        if len(self._history) > MAX_HISTORY * 2:
                            self._history = self._history[-(MAX_HISTORY * 2):]
                return full_text, tool_calls
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response else "?"
                if code == 429:
                    print(f"\n⚠️  Cerebras token esauriti (429) — fallback Ollama", flush=True)
                elif code == 401:
                    self._cerebras_available = False
                    print(f"\n⚠️  Cerebras auth fallita — fallback Ollama", flush=True)
                else:
                    print(f"\n⚠️  Cerebras errore {code} — fallback Ollama", flush=True)
                self._err("Cerebras.http", exc=e, msg=f"HTTP {code}")
            except requests.exceptions.ConnectionError as e:
                print("\n⚠️  Cerebras non raggiungibile — fallback Ollama", flush=True)
                self._cerebras_available = False
                self._err("Cerebras.connection", exc=e)
            except Exception as e:
                print(f"\n⚠️  Cerebras errore ({e}) — fallback Ollama", flush=True)
                self._err("Cerebras.generic", exc=e)

        # ── Fallback 2: Ollama (locale) ───────────────────────────────────────
        payload = {
            "model":    self.vmodel if use_vision else self.model,
            "messages": messages,
            "stream":   True,
            "tools":    tools,
            "options":  {
                "num_ctx":     8192,
                "num_predict": 1024,
                "temperature": 0.3,
                "num_thread":  _CPU_THREADS,
            }
        }

        full_text, tool_calls = "", []
        tts_buf, json_buf, in_json = "", "", False

        try:
            with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = data.get('message', {})
                    if 'tool_calls' in msg:
                        tool_calls.extend(msg['tool_calls'])
                    chunk = msg.get('content', '')
                    if chunk:
                        full_text += chunk
                        if _RE_JSON_TOOL.search(chunk):
                            in_json = True
                        if in_json:
                            json_buf += chunk
                            if json_buf.count('{') > 0 and json_buf.count('{') <= json_buf.count('}'):
                                in_json = False
                        else:
                            print(chunk, end='', flush=True)
                            tts_buf += chunk
                            if any(c in chunk for c in ('.', '!', '?', '\n')):
                                self._tts_say(tts_buf.strip())
                                tts_buf = ""
                    if data.get('done'):
                        break

            if tts_buf.strip():
                self._tts_say(tts_buf.strip())
            print()

            if history is None:
                with self._lock:
                    self._history.append({"role": "user", "content": user_msg})
                    clean = self._clean_for_history(full_text)
                    if clean:
                        self._history.append({"role": "assistant", "content": clean})
                    if len(self._history) > MAX_HISTORY * 2:
                        self._history = self._history[-(MAX_HISTORY * 2):]

            return full_text, tool_calls

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            if code == 404:
                print(f"\n⚠️ Modello non in memoria (404) — ricarico '{self.model}'...", flush=True)
                try:
                    subprocess.Popen(
                        ["ollama", "run", self.model],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    print("⏳ Attendo che il modello si carichi (15s)...", flush=True)
                    time.sleep(15)
                    with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp2:
                        resp2.raise_for_status()
                        for line in resp2.iter_lines():
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                            except Exception:
                                continue
                            msg = data.get("message", {})
                            if "tool_calls" in msg:
                                tool_calls.extend(msg["tool_calls"])
                            chunk = msg.get("content", "")
                            if chunk:
                                full_text += chunk
                                print(chunk, end="", flush=True)
                            if data.get("done"):
                                break
                        print()
                    return full_text or "ok Modello ricaricato, riprova.", tool_calls
                except Exception as e2:
                    print(f"\n❌ Impossibile ricaricare il modello: {e2}")
                    return f"❌ Modello non disponibile. Esegui: ollama run {self.model}", []

            print(f"\n⚠️ Ollama HTTP {code} — riprovo tra 3s...", flush=True)
            time.sleep(3)
            try:
                with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp2:
                    resp2.raise_for_status()
                    for line in resp2.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            full_text += chunk
                            print(chunk, end="", flush=True)
                        if data.get("done"):
                            break
                    print()
            except Exception as e2:
                print(f"\n❌ Retry fallito: {e2}")
            return full_text or f"❌ {e}", tool_calls

        except requests.exceptions.Timeout as e:
            self._stats["errors"] += 1
            err = "⏱️ Timeout Ollama"
            print(f"\n{err}")
            self._err("Ollama.timeout", exc=e)
            if self._stats["errors"] >= 3:
                self._restart_ollama()
            return err, []

        except requests.exceptions.ConnectionError as e:
            self._stats["errors"] += 1
            err = "❌ Ollama non risponde"
            print(f"\n{err}")
            self._err("Ollama.connection", exc=e)
            if self._stats["errors"] >= 3:
                self._restart_ollama()
            return err, []

        except Exception as e:
            print(f"\n❌ {e}")
            self._err("Ollama.generic", exc=e)
            return f"❌ {e}", []
