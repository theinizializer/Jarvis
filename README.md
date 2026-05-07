# 🤖 JARVIS v6.0

> Local personal AI assistant — voice, web search, Discord, persistent memory.  
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
| 🧠 **Persistent Memory** | Remembers facts between sessions |
| 💻 **Terminal Commands** | Run shell commands with confirmation |
| 🌍 **16 Languages** | IT, FR, EN, DE, ES, PT, ZH, JA, KO, AR, RU, NL, PL, TR, SV, LB |
| 🔍 **Web Search** | SearXNG → Tavily → Brave → DDG → Wikipedia |
| 💙 **Discord Bot** | Use JARVIS directly from Discord |
| 🥽 **AR Glasses** | Raspberry Pi 5 support with auto-discovery |

---

## 📁 Project Structure

```
jarvis_v6.py        ← main core: Ollama, memory, terminal, Discord
voice_module.py     ← STT (Whisper), TTS (gTTS), wake word, speaker verification
search_module.py    ← web search backends with fallback chain
language_module.py  ← localization for 16 languages
jarvis_rasp.py      ← Raspberry Pi version (AR glasses)
installer.py        ← automatic setup
Modelfile           ← Ollama model configuration
docker-compose.yml  ← SearXNG local search engine
```

---

## 🚀 Installation

### Requirements

- Python 3.10+
- [Ollama](https://ollama.com) — local AI model
- Linux with PulseAudio/PipeWire (macOS/Windows: partial support)

```bash
sudo apt install pulseaudio mpg123 parecord  # Ubuntu/Debian
```

### Quick Start

```bash
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis
pip install -r requirements.txt
```

### Create the Ollama model

```bash
ollama pull qwen2.5:7b
ollama create jarvisQwen -f Modelfile
```

### Start JARVIS

```bash
python jarvis_v6.py
```

On first run, JARVIS will ask you to choose a language — this is saved and never asked again.

---

## 🔍 Web Search Setup

JARVIS uses a fallback chain — configure what you have, skip the rest.

| Backend | Type | Setup |
|---------|------|-------|
| **SearXNG** ⭐ | Self-hosted, unlimited | `docker compose up -d` |
| **Tavily** | API, 1000 req/month free | [tavily.com](https://tavily.com) → `TAVILY_API_KEY` in `.env` |
| **Brave** | API, 2000 req/month free | [brave search](https://api.search.brave.com) → `BRAVE_API_KEY` in `.env` |
| **DuckDuckGo** | Free, no key | Automatic fallback |
| **Wikipedia** | Free, no key | Always available |
| **Open-Meteo** | Free, no key | Weather forecasts |

```bash
# Start SearXNG locally (recommended)
docker compose up -d
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
| `/add language` | Add a new language |
| `/weather <city>` | Weather forecast |
| `/news` | Latest news |
| `/wiki <topic>` | Wikipedia search |
| `/tts` | Toggle voice on/off |
| `/mode` | Switch keyboard ↔ microphone |
| `/agent <goal>` | Autonomous agent mode |
| `/add_voice` | Record voice profile (8-10 seconds) |
| `/voice_profiles` | Manage voice profiles |
| `/stats` | Session statistics |
| `/log` | Show command history |
| `/help` | Show all available commands |
| `/exit` | Close JARVIS |

> **Note**: Commands are **fully localized** in all 16 languages — just use the English version or your language equivalent!

---

## 🔒 Speaker Verification (optional)

JARVIS can learn your voice and ignore everyone else — including itself.

```bash
# Set up voice profile (speak for 10 seconds)
HIP_VISIBLE_DEVICES=-1 python voice_module.py --setup-speaker
```

On AMD GPUs, always set `HIP_VISIBLE_DEVICES=-1` to force CPU mode for resemblyzer.

---

## 🥽 Raspberry Pi / AR Glasses

`jarvis_rasp.py` connects to your main PC over the local network — the AI model runs on the PC, commands execute on the Raspberry Pi.

```bash
# On your main PC — allow Ollama network connections
sudo systemctl edit ollama
# Add: Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama

# On Raspberry Pi — auto-discovers the PC
python jarvis_rasp.py
```

---

## 🔑 API Keys (.env)

```env
DISCORD_TOKEN=...      # Discord bot (optional)
BRAVE_API_KEY=...      # Brave Search (optional)
GNEWS_API_KEY=...      # GNews (optional)
TAVILY_API_KEY=...     # Tavily AI search (optional)
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
HIP_VISIBLE_DEVICES=-1 python jarvis_v6.py
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
