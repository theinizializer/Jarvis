# 🤖 JARVIS v6.0

Local personal AI assistant based on Ollama
, with voice support, web search, Discord bot, and persistent memory. Runs completely offline on your machine — no data is sent to external servers (except optional web searches).
---

## ✨ Features

💬 Text and voice chat — interact with JARVIS via keyboard or microphone
🎙️ Wake word "Jarvis" — voice activation with fuzzy matching (works with different accents and pronunciations)
🔒 Speaker verification — JARVIS responds only to your voice (optional, requires resemblyzer)
🔊 TTS — text-to-speech using gTTS
🧠 Persistent memory — remembers facts about you between sessions
💻 Terminal command execution — JARVIS can run shell commands with confirmation
🌍 Multilingual — Italian, French, English, German, Spanish, and more (16 languages)
🔍 Web search — weather, news, Wikipedia, general search (via SearXNG, Brave, DDG, Tavily)
💙 Discord bot — use JARVIS directly from Discord
---

## 📁 Project Structure

```
jarvis_v6.py        ← main core: loop, Ollama, Discord, memory, terminal
voice_module.py     ← STT (Whisper), TTS (gTTS), wake word, speaker verification
search_module.py    ← web search (SearXNG, Brave, DDG, Wikipedia, weather, news)
language_module.py  ← multi-language localization (wake word, commands, UI messages)
installer.py        ← installs everything and configures API keys
```

---

## 🚀 Quick Installation

### 1. Requisiti di sistema

Python 3.10+
Ollama
 — for the local AI model
PulseAudio — for microphone (Linux): sudo apt install pulseaudio
mpg123 — for TTS: sudo apt install mpg123
Docker (optional) — for local SearXNG

### 2. Clone the project

```bash
git clone https://github.com/tuousername/jarvis.git
cd jarvis
```

### 3. Run the installer

```bash
python installer.py
```
The installer will automatically create:

jarvisenv/ — Python virtual environment with all dependencies
.env — file with your API keys (not committed to git)
avvia_jarvis.sh — quick start script

### 4. Create the Ollama model

Before starting JARVIS, you need a configured Ollama model. You can use any installed model, for example:

```bash
ollama pull qwen2.5:7b

ollama create jarvisQwen -f Modelfile
```

### 5. Start Jarvis

```bash
./avvia_jarvis.sh
```

---

## 🔍 Web Search — Configuration

JARVIS supports multiple search backends, in priority order:

| Backend        | Type                           | How to configure                                                                 |
| -------------- | ------------------------------ | -------------------------------------------------------------------------------- |
| **SearXNG**    | Self-hosted, free, recommended | `docker run -d -p 8080:8080 searxng/searxng`                                     |
| **Tavily**     | API, free tier                 | [tavily.com](https://tavily.com) → `TAVILY_API_KEY` in `.env`                    |
| **Brave**      | API, free tier                 | [api.search.brave.com](https://api.search.brave.com) → `BRAVE_API_KEY` in `.env` |
| **DuckDuckGo** | Free, no key                   | Automatic fallback                                                               |
| **Wikipedia**  | Free, no key                   | Always available                                                                 |
| **Open-Meteo** | Free, no key                   | Always available (weather)                                                       |
| **ANSA RSS**   | Free, no key                   | Automatic fallback (news)                                                        |


If you don’t configure anything, JARVIS will use DuckDuckGo and Wikipedia for free.

---

## 🌍 Supported Languages

Italiano, Français, English, Deutsch, Español, Português, 中文, 日本語, 한국어, العربية, Русский, Nederlands, Polski, Türkçe, Svenska, Lëtzebuergesch

Change language with the command: /change language (or equivalent in the active language)

---

## 💬 Main Commands

| Command            | Description                            |
| ------------------ | -------------------------------------- |
| `/remember <fact>` | Save a fact in persistent memory       |
| `/memory`          | Show everything JARVIS remembers       |
| `/forget all`      | Clear all memory                       |
| `/search <query>`  | Explicit web search                    |
| `/weather <city>`  | Current weather and forecast           |
| `/news`            | Latest news                            |
| `/wiki <topic>`    | Wikipedia search                       |
| `/language`        | Show current language                  |
| `/change language` | Change language                        |
| `/tts`             | Enable/disable voice                   |
| `/mode`            | Switch between keyboard and microphone |
| `/stats`           | Session statistics                     |
| `/exit`            | Close JARVIS                           |


---

## 🔑 API Key

API keys are configured in the .env file (created by the installer). Never commit this file to git.

```env
DISCORD_TOKEN=...      # Discord bot token (optional)
BRAVE_API_KEY=...      # Brave Search (optional, free tier)
GNEWS_API_KEY=...      # GNews (optional, free tier)
TAVILY_API_KEY=...     # Tavily AI search (optional, free tier)
```

---

## 🤝 Contributing

The project is open source — pull requests and issues are welcome! If you add a language, a search backend, or a feature, open a PR.

---

## 📄 License
MIT License — see [LICENSE](LICENSE)

## Voice Recognition

HIP_VISIBLE_DEVICES=-1 python3 voice_module.py --setup-speaker
