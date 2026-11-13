# JARVIS Desktop

Assistente AI per Linux con chat desktop, comandi vocali opzionali e strumenti per lavorare con file, web e servizi configurati. Puoi usare Ollama in locale oppure collegare provider AI online.

> **Privacy:** con Ollama le richieste possono restare sul tuo computer. Se scegli un provider online, il testo della conversazione e i risultati degli strumenti usati nel compito vengono inviati a quel provider. La navigazione web richiede una connessione a Internet.

## Funzionalità principali

- **Chat desktop** con cronologia salvata sul computer e impostazioni organizzate per categoria.
- **Modelli locali o online:** Ollama, Groq, Cerebras, NVIDIA e OpenAI, con fallback configurabile.
- **Strumenti per i compiti:** ricerca e lettura di file nella Home, informazioni sul sistema, comandi locali con controlli e conferme, ricerca web e memoria.
- **Integrazioni facoltative:** voce, Gmail, Discord e server MCP locali.
- **Controllo delle azioni:** le operazioni sensibili richiedono conferma; la creazione di file non sovrascrive quelli esistenti senza autorizzazione.

Le integrazioni opzionali potrebbero richiedere dipendenze, credenziali o configurazione aggiuntive. Consulta [Installazione](INSTALLATION.md) e [Changelog](CHANGELOG.md) per i dettagli.

## Installazione rapida

Requisiti: Linux, Git, Python 3 con `venv` e Tk; per la finestra desktop serve un ambiente grafico. Su Arch Linux, installa almeno `git`, `python` e `tk`.

```sh
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis
./install.sh --dry-run
./install.sh
jarvis
```

Per avviare senza installare il comando nel menu:

```sh
./avvia_jarvis.sh
```

Per usare la modalità testuale:

```sh
./avvia_jarvis.sh --text
```

La procedura dettagliata, inclusi i requisiti e le opzioni di ripristino, è in [INSTALLATION.md](INSTALLATION.md).

## Configurazione

Apri **Impostazioni** nell’app per configurare provider AI, lingua, profili e integrazioni. Per usare un modello online, inserisci la chiave API del provider scelto; per Ollama, installa e avvia Ollama con un modello compatibile.

JARVIS salva configurazione e dati nella directory utente:

- Configurazione: `~/.config/jarvis` (o `$XDG_CONFIG_HOME/jarvis`)
- Credenziali e dati: `~/.local/share/jarvis-data` (o `$XDG_DATA_HOME/jarvis-data`)

Le credenziali sono conservate in un file `.env` con permessi limitati, ma **non sono cifrate**. Proteggi l’accesso al tuo account Linux e non condividere quel file.

## Verifiche

Per eseguire i test:

```sh
python3 -m unittest discover -s tests -v
```

Altri controlli, compresi quelli che richiedono un ambiente grafico, sono descritti nei test del progetto.

## Documentazione e versioni

- [Guida all’installazione](INSTALLATION.md)
- [Novità e modifiche](CHANGELOG.md)
- [Versione precedente (v9)](https://github.com/theinizializer/Jarvis/tree/old-v9)
- [Firmware KyGlove](https://github.com/theinizializer/KyGlove)

JARVIS è distribuito con licenza [MIT](LICENSE).
