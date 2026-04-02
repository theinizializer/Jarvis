# Changelog

All notable changes to JARVIS are documented here.

## [6.0.0] — 2025-04-02

### Added
- `voice_module.py` — dedicated voice module with speaker verification, VAD, wake/sleep word
- `language_module.py` — full localization for 16 languages (wake word, commands, UI, TTS)
- `search_module.py` — modular search with Tavily, DDG, Wikipedia, Open-Meteo, ANSA
- Sleep word support — "Jarvis dormi/sleep/dors" closes session
- Slash commands — `/remember`, `/memory`, `/language`, `/change language` etc.
- First-run language setup — asks once, remembers forever
- Speaker verification — JARVIS responds only to your voice (resemblyzer)
- Microphone mute during TTS — prevents JARVIS from hearing itself
- `jarvis_rasp.py` — Raspberry Pi version with automatic ThinkPad discovery
- Tavily AI search integration

### Changed
- Wake word fuzzy matching now handles language-specific pronunciations
- TTS language follows selected language, not text detection
- System prompt language instruction now fully localized
- Whisper model size configurable (small/medium/large-v3)

### Fixed
- Sample rate auto-detection for microphone (no more PaErrorCode -9997)
- resemblyzer GPU error on AMD (HIP) — forced CPU mode
- Whisper hallucinations on silence filtered
- JARVIS no longer hears its own TTS output

## [5.x] — Previous versions

- JARVIS v5.x: voice, Discord, web search, memory
- JARVIS v4.x: terminal commands, Ollama integration
- JARVIS v3.x: initial voice support
- JARVIS v2.x: Discord bot
- JARVIS v1.x: initial release
