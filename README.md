# 🤖 JARVIS v8.0

> Local personal AI assistant — voice, web search, Discord, persistent memory with ChromaDB.  
> Runs **completely offline** on your machine. No data sent to external servers (except optional web searches).

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 💬 **Text & Voice** | Keyboard or microphone — your choice |
| 🎙️ **Wake Word** | Say "Jarvis" to activate — fuzzy matching for accents |
| 😴 **Sleep Word** | "Jarvis dormi/sleep/dors" — closes session |
| 🔒 **Speaker Verification** | JARVIS responds only to your voice |
| 🔊 **TTS** | Text-to-speech with automatic microphone mute |
| 🧠 **Memory Engine** | SQLite + ChromaDB — semantic search + persistent facts |
| 💻 **Terminal Commands** | Run shell commands with confirmation |
| 🌍 **16 Languages** | IT, FR, EN, DE, ES, PT, ZH, JA, KO, AR, RU, NL, PL, TR, SV, LB |
| 🔍 **Web Search** | Tavily + Brave + SearXNG + DDG + Wikipedia |
| 💙 **Discord Bot** | Use JARVIS directly from Discord |
| 🤖 **Agent Mode** | Autonomous task execution with reasoning |
| 🖥️ **SSH Module** | Execute commands on remote servers |

---

## 📁 Project Structure

```
code/
  ├── jarvis_v8.py              ← main core: Ollama, memory, terminal, Discord
  ├── jarvis_memory_engine.py   ← SQLite + ChromaDB semantic memory
  ├── voice_module.py           ← STT (Whisper), TTS (gTTS), wake word, speaker verification
  ├── search_module.py          ← web search backends with fallback chain
  ├── language_module.py        ← localization for 16 languages
  ├── agent_module.py           ← autonomous agent with reasoning
  ├── jarvis_banner.py          ← live UI banner
  ├── jarvis_secrets.py         ← PIN + encrypted secrets management
  ├── ssh_module.py             ← remote SSH execution
  ├── installer.py              ← automatic cross-platform setup
  └── Modelfile                 ← Ollama model configuration
docker-compose.yml             ← SearXNG local search engine
README.md                       ← this file
```

---

## 🚀 Installation

### Requirements

- Python 3.10+
- [Ollama](https://ollama.com) — local AI model
- Linux/macOS/Windows supported

### Quick Start (Automatic)

```bash
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis-main/code
python installer.py
```

The installer will:
1. ✅ Detect your OS
2. ✅ Install Ollama automatically
3. ✅ Install system dependencies (audio, portaudio)
4. ✅ Create Python venv (Linux only)
5. ✅ Install all Python packages
6. ✅ Copy files to ~/Documents/modelli
7. ✅ Configure .env with API keys
8. ✅ Create startup scripts
9. ✅ Download Ollama models

### Manual Setup

```bash
# Install Ollama from https://ollama.com
# Pull base model
ollama pull qwen2.5:7b

# Create JARVIS model
ollama create jarvisQwen -f Modelfile

# Install dependencies
pip install -r requirements.txt

# Start JARVIS
python jarvis_v8.py
```

---

## 🔍 Web Search Setup

JARVIS uses a fallback chain — configure what you have, skip the rest.

| Backend | Type | Setup |
|---------|------|-------|
| **Tavily** | API, 1000 req/month free | [tavily.com](https://tavily.com) → `TAVILY_API_KEY` in `.env` |
| **SearXNG** ⭐ | Self-hosted, unlimited | `docker compose up -d` |
| **Brave** | API, 2000 req/month free | [brave search](https://api.search.brave.com) → `BRAVE_API_KEY` in `.env` |
| **DuckDuckGo** | Free, no key | Automatic fallback |
| **Wikipedia** | Free, no key | Always available |
| **Open-Meteo** | Free, no key | Weather forecasts |

```bash
# Start SearXNG locally (recommended)
docker compose up -d
```

---

## 🧠 Memory Engine (NEW in v8.0)

JARVIS now has **semantic memory** powered by ChromaDB:

```python
# Automatically saved between sessions
/memorizza Il mio nome è radostin
/memorizza Lavoro come programmatore a Lussemburgo
/memoria  # Shows all memories with semantic search
/dimentica tutto  # Clear all memories
```

---

## 🌍 Languages

Supports 16 languages — everything adapts: wake word, sleep word, commands, TTS, UI messages.

```
🇮🇹 Italiano  🇫🇷 Français  🇬🇧 English   🇩🇪 Deutsch
🇪🇸 Español   🇵🇹 Português  🇨🇳 中文       🇯🇵 日本語
🇰🇷 한국어     🇸🇦 العربية    🇷🇺 Русский    🇳🇱 Nederlands
🇵🇱 Polski    🇹🇷 Türkçe     🇸🇪 Svenska    🇱🇺 Lëtzebuergesch
```

---

## 💬 Commands (slash)

All commands use `/` — works in both keyboard and voice mode.

| Command | Description |
|---------|-------------|
| `/remember <fact>` | Save to persistent memory |
| `/memory` | Show everything remembered |
| `/forget all` | Clear all memory |
| `/language` | Show current language |
| `/change language` | Switch language |
| `/weather <city>` | Weather forecast |
| `/news` | Latest news |
| `/wiki <topic>` | Wikipedia search |
| `/tts` | Toggle voice on/off |
| `/provider groq\|nvidia\|ollama` | Switch AI provider |
| `/agent <goal>` | Autonomous agent mode |
| `/add_voice` | Record voice profile (8-10 seconds) |
| `/voice_profiles` | Manage voice profiles |
| `/host list` | Show SSH hosts |
| `/host add` | Add new SSH host |
| `/host <name>` | Connect to SSH host |
| `/stats` | Session statistics |
| `/log` | Show command history |
| `/help` | Show all available commands |
| `/exit` | Close JARVIS |

> **Note**: Commands are **fully localized** in all 16 languages — just use the English version or your language equivalent!

---

## 🤖 Multiple AI Providers (NEW in v8.0)

Choose your brain at runtime:

```bash
/provider groq      # Groq API (120B — fastest)
/provider nvidia    # NVIDIA NIM (Qwen 397B — vision + reasoning)
/provider ollama    # Local Ollama (privacy-first)
```

**Groq API** (recommended for speed):
1. Get free API key: https://console.groq.com
2. Set `GROQ_API_KEY` in `.env`
3. JARVIS auto-switches if internet available

**NVIDIA NIM** (recommended for vision + complex tasks):
1. Get free API key: https://build.nvidia.com
2. Set `NVIDIA_API_KEY` in `.env`
3. Use for vision, debugging, code review

**Local Ollama** (recommended for privacy):
- No API keys needed
- Runs 100% offline
- Slower but completely private

---

## 🔒 Speaker Verification (optional)

JARVIS can learn your voice and ignore everyone else — including itself.

```bash
# Set up voice profile (speak for 10 seconds)
cd ~/Documents/modelli
python voice_module.py --setup-speaker
```

On AMD GPUs, always set `HIP_VISIBLE_DEVICES=-1` to force CPU mode for resemblyzer.

---

## 🔑 API Keys (.env)

```env
GROQ_API_KEY=gsk_...           # Groq API (optional — use /provider ollama if not set)
NVIDIA_API_KEY=nvapi-...       # NVIDIA NIM (optional — vision + reasoning)
TAVILY_API_KEY=tvly-...        # Tavily Search (optional)
BRAVE_API_KEY=...              # Brave Search (optional)
GNEWS_API_KEY=...              # GNews (optional)
DISCORD_TOKEN=...              # Discord bot (optional)
SUDO_PASSWORD=...              # Sudo password (optional — ask on first run)
```

---

## 🛠️ Troubleshooting

**`PaErrorCode -9997` — Invalid sample rate**  
Your microphone uses a different sample rate. JARVIS auto-detects it, but if it fails:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices(kind='input'))"
```

**`HIP error` on AMD GPU**  
resemblyzer tries to use the AMD GPU. Force CPU:
```bash
HIP_VISIBLE_DEVICES=-1 python jarvis_v8.py
```

**Whisper not transcribing / hallucinating**  
Try a larger model — edit `WHISPER_MODEL_SIZE` in `voice_module.py`:
```python
WHISPER_MODEL_SIZE = "medium"  # or "large-v3" for best accuracy
```

**JARVIS hears itself speaking**  
Use headphones/earphones — the microphone won't pick up the speaker output.  
Or set up speaker verification so JARVIS ignores its own voice.

**`parecord: command not found`**  
```bash
sudo apt install pulseaudio-utils
```

**TTS not working**  
```bash
sudo apt install mpg123
which mpg123  # should print /usr/bin/mpg123
```

---

## 📊 Model Benchmarks

| Model | Type | Speed | Accuracy | Vision | Cost |
|-------|------|-------|----------|--------|------|
| Groq 120B | Cloud | ⚡⚡⚡ | ⭐⭐⭐⭐ | No | Free tier |
| NVIDIA Qwen 397B | Cloud | ⚡⚡ | ⭐⭐⭐⭐⭐ | Yes | Free tier |
| Ollama Qwen 7B | Local | ⚡ | ⭐⭐⭐ | No | Free |

---

## 🤝 Contributing

PRs and issues welcome! If you add a language, search backend, or feature — open a PR.

1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Commit and push
4. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

*Inspired by Iron Man's JARVIS. Built by a student from Luxembourg 🇱🇺 as a passion project.*
