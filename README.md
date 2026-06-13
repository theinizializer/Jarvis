# 🤖 JARVIS v9.0

> Personal AI assistant — voice, web search, Discord, persistent memory with ChromaDB.
> Runs locally on your machine, with optional cloud fallback for speed.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 💬 **Text & Voice** | Keyboard or microphone — your choice |
| 🎙️ **Wake Word** | Say "Jarvis" to activate — fuzzy matching for accents |
| 😴 **Sleep Word** | "Jarvis sleep/dormi/dors" — closes the session |
| 🔒 **Speaker Verification** | JARVIS responds only to your voice (ECAPA-TDNN) |
| 🔊 **TTS** | Text-to-speech with automatic microphone mute |
| 🧠 **Memory Engine** | SQLite + ChromaDB — semantic search + persistent facts |
| 💻 **Terminal Commands** | Executes shell commands with confirmation for risky ones |
| 🌍 **Multilingual** | UI translated into IT, FR, EN, PT; voice/wake word for multiple languages |
| 🔍 **Web Search** | Tavily + Brave + SearXNG + DuckDuckGo + Wikipedia |
| 💙 **Discord Bot** | Use JARVIS directly from Discord (remote access) |
| 🤖 **Agent Mode** | Autonomous task execution with reasoning |
| 🖥️ **SSH Module** | Execute commands on remote servers (encrypted credentials) |
| 🔐 **Security** | Session PIN + encrypted sudo password (Fernet) |
| 🧩 **Multi-provider** | Groq → Cerebras → NVIDIA → Ollama, with automatic fallback |

---

## 📁 Project Structure

```
code/
  ├── jarvis_v9.py              ← main core: orchestrator, memory, terminal
  ├── api_module.py             ← LLM providers (Groq, Cerebras, NVIDIA, Ollama)
  ├── commands_module.py        ← slash commands (/tts, /setpin, /token, /sleep…)
  ├── discord_module.py         ← Discord integration
  ├── utils_module.py           ← constants, regex, run_cmd, cleanup management
  ├── jarvis_memory_engine.py   ← semantic memory SQLite + ChromaDB
  ├── voice_module.py           ← STT (Whisper), TTS, wake word, speaker verification
  ├── search_module.py          ← web search backend with fallback chain
  ├── language_module.py        ← localization and translated messages
  ├── agent_module.py           ← autonomous agent with reasoning
  ├── blender_module.py         ← Blender integration (experimental)
  ├── sepformer_server.py       ← voice separation server (Unix socket)
  ├── jarvis_banner.py          ← live UI (Textual TUI)
  ├── jarvis_secrets.py         ← encrypted secrets management (PIN, sudo)
  ├── ssh_module.py             ← remote SSH execution
  ├── persona_module.py         ← JARVIS personality
  ├── installer.py              ← automatic cross-platform setup
  └── Modelfile                 ← Ollama model configuration
docker-compose.yml             ← local SearXNG search engine
README.md                       ← this file
```

---

## 🚀 Installation

### Requirements

- Python 3.10+
- [Ollama](https://ollama.com) — local AI model (for offline fallback)
- Linux / macOS / Windows

### Quick Start (automatic)

```bash
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis-main/code
python installer.py
```

The installer handles OS detection, installs Ollama and system dependencies (audio, portaudio), creates a Python virtualenv, installs packages, copies files to the correct location, and sets up API keys in `.env`.

### Manual Setup

```bash
# Install Ollama from https://ollama.com
ollama pull qwen2.5:7b
ollama create jarvisQwen -f Modelfile

pip install -r requirements.txt
python jarvis_v9.py
```

---

## 🧩 AI Providers (automatic fallback)

JARVIS tries providers in cascade: if one is unavailable or hits quota limits, it moves to the next, until reaching the local Ollama model which always works offline.

1. **Groq** — primary, fast (`GROQ_API_KEY`) — https://console.groq.com
2. **Cerebras** — fast fallback (`CEREBRAS_API_KEY`) — https://cloud.cerebras.ai
3. **NVIDIA NIM** — heavy tasks and vision (`NVIDIA_API_KEY`) — https://build.nvidia.com
4. **Ollama** — local, full privacy, no key needed

You can force a provider at runtime with `/provider groq|nvidia|ollama`.

> Token consumption for each provider is visible in the banner and with the `/token` command.

---

## 🔍 Web Search

JARVIS uses a fallback chain — configure what you have, skip the rest.

| Backend | Type | Setup |
|---------|------|-------|
| **Tavily** | API, 1000 req/month free | `TAVILY_API_KEY` in `.env` |
| **SearXNG** ⭐ | Self-hosted, unlimited | `docker compose up -d` |
| **Brave** | API, 2000 req/month free | `BRAVE_API_KEY` in `.env` |
| **DuckDuckGo** | Free, no key | Automatic fallback |
| **Wikipedia** | Free, no key | Always available |
| **Open-Meteo** | Free, no key | Weather forecasts |

---

## 🔐 Security

JARVIS has system access (shell commands, sudo, remote SSH), so it includes protections:

- **Session PIN** — required at startup in keyboard mode and after `/sleep`. Set or change it on the fly with `/setpin`, no reinstall needed.
- **Encrypted sudo password** — saved in `.env.enc` with Fernet (AES), tied to the machine. Never shown on screen or stored in plaintext.
- **Encrypted SSH credentials** — remote host passwords are encrypted at rest.
- **Confirmation for risky commands** — the guardian blocks/requests confirmation for destructive commands.

Encrypted secrets (`.env.enc`) are separate from API keys (`.env`): the two files don't overwrite each other.

---

## 🧠 Memory

Semantic memory with ChromaDB, persistent across sessions:

```bash
/memorizza My name is Radostin
/memoria          # show memories (semantic search)
/dimentica        # clear memory
```

---

## 💬 Commands (slash)

All commands start with `/` — work from both keyboard and voice.

| Command | Description |
|---------|-------------|
| `/memorizza <fact>` | Save to persistent memory |
| `/memoria` | Show all stored memories |
| `/dimentica` | Clear memory |
| `/lingua` | Show current language |
| `/cambia_lingua` | Change language (hot-swap, no restart) |
| `/meteo <city>` | Weather forecast |
| `/notizie` | Latest news |
| `/wiki <topic>` | Wikipedia search |
| `/tts` | Enable/disable voice |
| `/provider groq\|nvidia\|ollama` | Switch AI provider |
| `/agente <goal>` | Autonomous agent mode |
| `/aggiungi_voce` | Record voice profile (10-15 seconds) |
| `/profili_voce` | Manage voice profiles |
| `/host` | SSH host management |
| `/stats` | Session statistics |
| `/token` | Token consumption per provider |
| `/setpin` | Set or change session PIN |
| `/dormi` | Standby (locked with PIN) |
| `/esci` | Close JARVIS |
| `/aiuto` | Show all available commands |

> Command descriptions and main messages are translated into IT/FR/EN/PT and change on the fly with `/cambia_lingua`.

---

## 🔒 Speaker Verification

JARVIS can learn your voice and ignore others. Uses **ECAPA-TDNN** (SpeechBrain) for accurate recognition.

```bash
# Record voice profile (speak 10-15 seconds, with various sentences)
# from within JARVIS:
/aggiungi_voce
```

For best results: record in the same environment and with the same microphone you'll use normally. Profiles are separate per engine (ECAPA/Resemblyzer) and don't mix.

---

## 🔑 `.env` Configuration

Format: `KEY=value` — **no spaces around `=` and no quotes**.

```env
GROQ_API_KEY=gsk_...           # Groq (optional — falls back to Ollama if absent)
CEREBRAS_API_KEY=csk-...       # Cerebras (optional — fallback from Groq)
NVIDIA_API_KEY=nvapi-...       # NVIDIA NIM (optional — vision + heavy tasks)
TAVILY_API_KEY=tvly-...        # Tavily Search (optional)
BRAVE_API_KEY=...              # Brave Search (optional)
GNEWS_API_KEY=...              # GNews (optional)
DISCORD_TOKEN=...              # Discord Bot (optional)

# Sources — PC = localhost, Raspberry = local network PC IP
OLLAMA_HOST=localhost
OLLAMA_PORT=11434
WHISPER_MODEL=medium           # medium (PC) | base | tiny (Raspberry, faster)
```

> Sudo password and PIN do **not** go in `.env`: they're managed encrypted in `.env.enc` (see Security section).

---

## 🛠️ Troubleshooting

**Microphone not hearing**
Often it's the wrong audio source (e.g., Bluetooth headphones stealing the default microphone). Check your devices and select the right one in JARVIS's microphone menu:
```bash
arecord -l                 # list capture devices
wpctl status               # (PipeWire) check default source
```

**Whisper not transcribing / hangs on startup in voice mode**
Likely the Whisper model is missing from cache. Download it:
```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu', compute_type='int8')"
```
Or use a smaller model by setting `WHISPER_MODEL=base` in `.env`.

**Cerebras error / falls back to Ollama**
Verify that `CEREBRAS_API_KEY` in `.env` is correct and **without quotes**. The model used is `gpt-oss-120b` (old Llama models are deprecated).

**JARVIS hears itself talking**
Use headphones/earbuds, or record a voice profile so JARVIS ignores its own voice.

**TTS not working**
```bash
sudo apt install mpg123    # or the equivalent for your distro
```

---

## 🤝 Contributing

PRs and issues welcome. If you add a language, search backend, or feature, open a PR.

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

*Inspired by Iron Man's JARVIS. Built by a student in Luxembourg 🇱🇺 as a personal project.*
