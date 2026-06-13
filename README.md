# 🤖 JARVIS v9.0

> Assistente AI personale — voce, ricerca web, Discord, memoria persistente con ChromaDB.
> Funziona in locale sulla tua macchina, con fallback cloud opzionali per velocità.

---

## ✨ Funzionalità

| Funzionalità | Descrizione |
|---------|-------------|
| 💬 **Testo & Voce** | Tastiera o microfono — a tua scelta |
| 🎙️ **Wake Word** | Di' "Jarvis" per attivare — fuzzy matching per accenti |
| 😴 **Sleep Word** | "Jarvis dormi/sleep/dors" — chiude la sessione |
| 🔒 **Speaker Verification** | JARVIS risponde solo alla tua voce (ECAPA-TDNN) |
| 🔊 **TTS** | Sintesi vocale con mute automatico del microfono |
| 🧠 **Memory Engine** | SQLite + ChromaDB — ricerca semantica + fatti persistenti |
| 💻 **Comandi Terminale** | Esegue comandi shell con conferma per quelli rischiosi |
| 🌍 **Multilingua** | UI tradotta in IT, FR, EN, PT; voce/wake word per più lingue |
| 🔍 **Ricerca Web** | Tavily + Brave + SearXNG + DuckDuckGo + Wikipedia |
| 💙 **Bot Discord** | Usa JARVIS direttamente da Discord (accesso remoto) |
| 🤖 **Modalità Agente** | Esecuzione autonoma di task con ragionamento |
| 🖥️ **Modulo SSH** | Esegue comandi su server remoti (credenziali cifrate) |
| 🔐 **Sicurezza** | PIN di sessione + password sudo cifrate (Fernet) |
| 🧩 **Multi-provider** | Groq → Cerebras → NVIDIA → Ollama, con fallback automatico |

---

## 📁 Struttura del progetto

```
code/
  ├── jarvis_v9.py              ← core principale: orchestratore, memoria, terminale
  ├── api_module.py             ← provider LLM (Groq, Cerebras, NVIDIA, Ollama)
  ├── commands_module.py        ← comandi slash (/tts, /setpin, /token, /dormi…)
  ├── discord_module.py         ← integrazione Discord
  ├── utils_module.py           ← costanti, regex, run_cmd, gestione cleanup
  ├── jarvis_memory_engine.py   ← memoria semantica SQLite + ChromaDB
  ├── voice_module.py           ← STT (Whisper), TTS, wake word, speaker verification
  ├── search_module.py          ← backend ricerca web con catena di fallback
  ├── language_module.py        ← localizzazione e messaggi tradotti
  ├── agent_module.py           ← agente autonomo con ragionamento
  ├── blender_module.py         ← integrazione Blender (sperimentale)
  ├── sepformer_server.py       ← server separazione voci (socket Unix)
  ├── jarvis_banner.py          ← UI live (TUI Textual)
  ├── jarvis_secrets.py         ← gestione segreti cifrati (PIN, sudo)
  ├── ssh_module.py             ← esecuzione SSH remota
  ├── persona_module.py         ← personalità di JARVIS
  ├── installer.py              ← setup automatico cross-platform
  └── Modelfile                 ← configurazione modello Ollama
docker-compose.yml             ← motore di ricerca SearXNG locale
README.md                       ← questo file
```

---

## 🚀 Installazione

### Requisiti

- Python 3.10+
- [Ollama](https://ollama.com) — modello AI locale (per il fallback offline)
- Linux / macOS / Windows

### Avvio rapido (automatico)

```bash
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis-main/code
python installer.py
```

L'installer si occupa di rilevare il sistema operativo, installare Ollama e le dipendenze di sistema (audio, portaudio), creare il virtualenv Python, installare i pacchetti, copiare i file nella cartella di destinazione, configurare il `.env` con le API key, impostare PIN e password sudo cifrati, creare gli script di avvio e scaricare il modello Ollama.

### Setup manuale

```bash
# Installa Ollama da https://ollama.com
ollama pull qwen2.5:7b
ollama create jarvisQwen -f Modelfile

pip install -r requirements.txt
python jarvis_v9.py
```

---

## 🧩 Provider AI (fallback automatico)

JARVIS prova i provider in cascata: se uno non è disponibile o esaurisce la quota, passa al successivo, fino al modello locale Ollama che funziona sempre offline.

1. **Groq** — principale, veloce (`GROQ_API_KEY`) — https://console.groq.com
2. **Cerebras** — fallback veloce (`CEREBRAS_API_KEY`) — https://cloud.cerebras.ai
3. **NVIDIA NIM** — task pesanti e vision (`NVIDIA_API_KEY`) — https://build.nvidia.com
4. **Ollama** — locale, privacy totale, nessuna chiave necessaria

Puoi forzare un provider a runtime con `/provider groq|nvidia|ollama`.

> Il consumo di token per ogni provider è visibile nel banner e con il comando `/token`.

---

## 🔍 Ricerca web

JARVIS usa una catena di fallback — configura quello che hai, salta il resto.

| Backend | Tipo | Setup |
|---------|------|-------|
| **Tavily** | API, 1000 req/mese gratis | `TAVILY_API_KEY` nel `.env` |
| **SearXNG** ⭐ | Self-hosted, illimitato | `docker compose up -d` |
| **Brave** | API, 2000 req/mese gratis | `BRAVE_API_KEY` nel `.env` |
| **DuckDuckGo** | Gratis, nessuna chiave | Fallback automatico |
| **Wikipedia** | Gratis, nessuna chiave | Sempre disponibile |
| **Open-Meteo** | Gratis, nessuna chiave | Previsioni meteo |

---

## 🔐 Sicurezza

JARVIS ha accesso al sistema (comandi shell, sudo, SSH remoto), quindi include protezioni:

- **PIN di sessione** — richiesto all'avvio in modalità tastiera e dopo `/dormi`. Impostalo o cambialo a caldo con `/setpin`, senza reinstallare.
- **Password sudo cifrata** — salvata in `.env.enc` con Fernet (AES), legata alla macchina. Non viene mai mostrata a schermo né salvata in chiaro.
- **Credenziali SSH cifrate** — le password degli host remoti sono cifrate a riposo.
- **Conferma comandi rischiosi** — il guardiano blocca/chiede conferma per comandi distruttivi.

I segreti cifrati (`.env.enc`) sono separati dalle API key (`.env`): i due file non si sovrascrivono.

---

## 🧠 Memoria

Memoria semantica con ChromaDB, persistente tra le sessioni:

```bash
/memorizza Il mio nome è Radostin
/memoria          # mostra le memorie (ricerca semantica)
/dimentica        # cancella la memoria
```

---

## 💬 Comandi (slash)

Tutti i comandi iniziano con `/` — funzionano sia da tastiera sia a voce.

| Comando | Descrizione |
|---------|-------------|
| `/memorizza <fatto>` | Salva nella memoria persistente |
| `/memoria` | Mostra tutto ciò che è memorizzato |
| `/dimentica` | Cancella la memoria |
| `/lingua` | Mostra la lingua corrente |
| `/cambia_lingua` | Cambia lingua (a caldo, senza riavviare) |
| `/meteo <città>` | Previsioni meteo |
| `/notizie` | Ultime notizie |
| `/wiki <argomento>` | Ricerca Wikipedia |
| `/tts` | Attiva/disattiva la voce |
| `/provider groq\|nvidia\|ollama` | Cambia provider AI |
| `/agente <obiettivo>` | Modalità agente autonomo |
| `/aggiungi_voce` | Registra profilo vocale (10-15 secondi) |
| `/profili_voce` | Gestisci profili vocali |
| `/host` | Gestione host SSH |
| `/stats` | Statistiche sessione |
| `/token` | Consumo token per provider |
| `/setpin` | Imposta o cambia il PIN di sessione |
| `/dormi` | Standby (blocca con PIN) |
| `/esci` | Chiudi JARVIS |
| `/aiuto` | Mostra tutti i comandi disponibili |

> Le descrizioni dei comandi e i messaggi principali sono tradotti in IT/FR/EN/PT e cambiano a caldo con `/cambia_lingua`.

---

## 🔒 Speaker Verification

JARVIS può imparare la tua voce e ignorare gli altri. Usa **ECAPA-TDNN** (SpeechBrain) per un riconoscimento accurato.

```bash
# Registra il profilo vocale (parla 10-15 secondi, con frasi varie)
# dall'interno di JARVIS:
/aggiungi_voce
```

Per buoni risultati: registra nello stesso ambiente e con lo stesso microfono che userai normalmente. I profili sono separati per motore (ECAPA/Resemblyzer) e non si mischiano.

---

## 🔑 Configurazione `.env`

Formato: `CHIAVE=valore` — **senza spazi attorno all'`=` e senza virgolette**.

```env
GROQ_API_KEY=gsk_...           # Groq (opzionale — fallback su Ollama se assente)
CEREBRAS_API_KEY=csk-...       # Cerebras (opzionale — fallback di Groq)
NVIDIA_API_KEY=nvapi-...       # NVIDIA NIM (opzionale — vision + task pesanti)
TAVILY_API_KEY=tvly-...        # Tavily Search (opzionale)
BRAVE_API_KEY=...              # Brave Search (opzionale)
GNEWS_API_KEY=...              # GNews (opzionale)
DISCORD_TOKEN=...              # Bot Discord (opzionale)

# Sorgenti — PC = localhost, Raspberry = IP del PC sulla rete locale
OLLAMA_HOST=localhost
OLLAMA_PORT=11434
WHISPER_MODEL=medium           # medium (PC) | base | tiny (Raspberry, più veloce)
```

> La password sudo e il PIN **non** vanno nel `.env`: sono gestiti cifrati nel `.env.enc` (vedi sezione Sicurezza).

---

## 🛠️ Troubleshooting

**Il microfono non sente**
Spesso è la sorgente audio sbagliata (es. auricolari Bluetooth che rubano il microfono di default). Verifica i dispositivi e scegli quello giusto nel menu microfoni di JARVIS:
```bash
arecord -l                 # elenca i dispositivi di cattura
wpctl status               # (PipeWire) controlla la sorgente di default
```

**Whisper non trascrive / si blocca all'avvio in modalità vocale**
Probabilmente il modello Whisper manca dalla cache. Scaricalo:
```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu', compute_type='int8')"
```
Oppure usa un modello più piccolo impostando `WHISPER_MODEL=base` nel `.env`.

**Cerebras risponde con errore / cade su Ollama**
Verifica che `CEREBRAS_API_KEY` nel `.env` sia corretta e **senza virgolette**. Il modello usato è `gpt-oss-120b` (i vecchi modelli Llama sono deprecati).

**JARVIS sente sé stesso parlare**
Usa cuffie/auricolari, oppure registra un profilo vocale così JARVIS ignora la propria voce.

**TTS non funziona**
```bash
sudo apt install mpg123    # o l'equivalente per la tua distro
```

---

## 🤝 Contribuire

PR e issue benvenute. Se aggiungi una lingua, un backend di ricerca o una funzionalità, apri una PR.

---

## 📄 Licenza

MIT License — vedi [LICENSE](LICENSE)

---

*Ispirato al JARVIS di Iron Man. Costruito da uno studente in Lussemburgo 🇱🇺 come progetto personale.*
