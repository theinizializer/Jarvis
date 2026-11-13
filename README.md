# JARVIS desktop 10.10

[Installazione v10](INSTALLATION.md) · [Archivio v9](https://github.com/theinizializer/Jarvis/tree/old-v9) · [Firmware KyGlove](https://github.com/theinizializer/KyGlove)


App nativa Linux con conversazione, impostazioni e strumenti MCP stdio.
Avvio: `./avvia_jarvis.sh`. Il comando `jarvis` e l’icona nel menu applicazioni
sono registrati da `./install.sh`. La modalità comandi rimane disponibile con
`./avvia_jarvis.sh --text`.

**Gmail e Discord.** La v10 legge anche le credenziali dei servizi presenti
nell’archivio cifrato `.env.enc` della v9. La lettura avviene in memoria:
la password non viene copiata nei file di configurazione. I valori non vuoti
configurati nella v10 hanno precedenza. Serve `cryptography`, incluso nelle
dipendenze desktop. **Posta → Verifica Gmail** controlla accesso e cartella INBOX
in sola lettura; **Controlla posta** mantiene il filtro promozionale della v9.

In **Impostazioni → Discord**, usa **Avvia bot** per collegare il token salvato;
lo stato mostra errori di autenticazione, connessione e permessi. Le dipendenze
sono in `requirements-discord.txt`, installate insieme agli effetti desktop.
Il bot risponde ai messaggi diretti e alle menzioni, con conversazioni separate
per utente/canale. Di default è autorizzato solo il proprietario dell’app Discord;
si possono indicare esplicitamente gli ID utenti. Per ricevere tutti i messaggi
di un canale, indica il suo ID, seleziona l’opzione nella pagina e abilita
**Message Content Intent** nel Developer Portal. Il bot usa permessi ridotti:
conversazione e dati del sistema; file, posta e azioni che richiedono conferma
si gestiscono nell’app locale. **Avvia il bot al mio accesso** abilita l’avvio
automatico dopo l’accesso amministratore; da terminale è disponibile `--discord`.
Il logout ferma il bot. Questa integrazione riguarda la chat testuale Discord.

I nuclei fluttuanti di scrittura e voce occupano entrambi 157 × 157 px,
circa il 30% in meno rispetto ai precedenti 224 px. L’icona dell’app usa
`assets/jarvis-icon-v2.png`; l’originale SVG resta disponibile.

La **10.10** rende disponibile al modello anche **`curl_fetch`**, oltre a
`web_search` e `read_url` che già utilizzano curl. Lo strumento esegue un GET
HTTP/HTTPS e restituisce stato HTTP, URL finale, tipo di contenuto ed estratto
grezzo di HTML/JSON/JavaScript. `find` cerca una stringa letterale nel corpo;
`offset` e `max_chars` permettono di proseguire la lettura senza riversare tutta
la pagina nel contesto. Il download è limitato a 8 MiB; gli estratti sono separatamente
limitati a 10.000 caratteri. La cache di paginazione dura 60 secondi ed è dichiarata
nel risultato; `refresh=true` forza un nuovo download. Non esegue JavaScript.

La ricerca ignora redirect pubblicitari e pagine di aiuto dei motori di ricerca.
Su Amazon prova anche la ricerca diretta. Lo scraper elimina manifest di asset,
traduzioni e controlli di filtro e mantiene nome/link del prodotto insieme al
prezzo. Consegna, accessori compatibili, prezzi di riferimento e prodotti
raccomandati diversi dalla scheda richiesta vengono separati dall’offerta.
Un HTTP 200 non implica che il prezzo sia nel documento: se manca un importo
pertinente l’estrazione lo segnala. I blocchi anti-bot restano errori espliciti.
Il [rapporto curl](docs/design/10.10-curl-tools.md) contiene le verifiche e i limiti.

La **10.9** aggiunge **Servizi → API multiple** per Groq, Cerebras, NVIDIA e
OpenAI. Scegli il provider in alto, poi aggiungi nome e chiave nella sezione.
Il campo **Modelli** può restare vuoto oppure contenere identificatori separati
da virgole. **Salva chiave** salva quella voce; **Nuova** prepara un’altra voce.
**Sposta su/giù**, **Chiave abilitata** e **Rimuovi** gestiscono priorità e uso.
**Verifica** prova soltanto la chiave selezionata con un messaggio sintetico,
senza fallback e senza inviare chat o file. I valori salvati restano mascherati;
in modifica un campo API vuoto conserva il valore precedente.

La chiave principale di Servizi viene provata prima dell’elenco, se abilitata.
Se non risponde o riceve un errore di autenticazione, credito o quota, viene
provata la successiva abilitata per lo **stesso provider e modello**. Il risultato
di ogni strumento e le conferme del Guardiano restano quelli del compito corrente.
La chiave riuscita prosegue il compito; quella non disponibile viene saltata per
il suo cooldown. Esaurite le chiavi, prosegue la catena di modelli/provider già
configurata. Un input non valido non viene ripetuto su tutte le chiavi.

Nomi, ordine e filtri sono in `config.json`; i valori sono nell’archivio `.env`
delle credenziali con permessi 0600, senza comparire nel prompt o nei log.
La telemetria mostra il nome assegnato all’API in uso. La quota Groq è condivisa
dall’organizzazione: altre chiavi della stessa organizzazione possono ricevere
lo stesso limite. [Documentazione Groq](https://console.groq.com/docs/rate-limits).
Il [rapporto API multiple](docs/design/10.9-api-multiple.md) descrive le prove
locali con chiavi sintetiche; non sono stati usati altri account reali.

La versione 10.2 ripristina il ciclo agente della v9: il modello richiede strumenti,
JARVIS li esegue, restituisce i risultati e prosegue lo stesso compito. Non si
basa sul riconoscimento di singole frasi per operare sui file. Le chiamate
strutturate coprono directory, ricerca con nomi simili, lettura, dati PC, comandi,
creazione/copia di file, web, memoria, posta e server MCP già abilitati.
Le letture strutturate operano nella Home e i comandi usano i permessi dell’utente
Linux, con `command_policy` e revisione delle azioni non diagnostiche. Nessuna
credenziale sudo viene raccolta dalla UI. I risultati degli strumenti vengono
inviati al provider che risponde al compito, compreso il successivo della catena
quando il fallback è abilitato.

La richiesta di autorizzazione sospende il compito: **Consenti** riprende il ciclo,
**Annulla compito** lo interrompe. La creazione conserva i file esistenti;
una sostituzione richiede lettura precedente, hash, conferma e backup dell’originale.
Il registro **Attività** mostra gli strumenti reali;
**Interrompi** annulla la connessione HTTP al modello e ferma il gruppo dei
processi dei comandi locali avviati dal compito. Nessuna risposta tardiva avvia
altri strumenti. Le modifiche già completate rimangono conservate.

La versione 10.3 aggiorna le connessioni Cerebras/NVIDIA e aggiunge il nucleo
animato. **Aspetto** permette animazioni complete, movimento ridotto o nucleo
statico. Gli elementi Canvas vengono riutilizzati; l’animazione si sospende fuori
dalla conversazione o quando la finestra è minimizzata. Anelli, scansione e
pulsazione rappresentano gli stati reali di lavoro, autorizzazione ed errore.

La versione 10.4 introduce una chat a tutta larghezza, messaggi formattati,
composizione multilinea (**Invio** invia, **Maiusc+Invio** va a capo), **Nuova chat**
e impostazioni divise per categoria. Il nucleo animato è compatto e la J è
centrata nel triangolo; l’icona usa lo stesso simbolo.

La versione 10.5 sposta il nucleo animato dietro i messaggi, con intensità ridotta
per conservare il contrasto, e salva le conversazioni sul PC. **Nuova chat**
conserva la precedente; **Chat salvate** la riapre con messaggi e risultati degli
strumenti, anche dopo la chiusura dell’app. Le chat sono separate per utente e
profilo e archiviate in `$XDG_DATA_HOME/jarvis-data/conversations.sqlite3`, con
permessi 0600. Il modello usa gli ultimi otto turni del contesto operativo;
la conversazione visibile resta archiviata. Questo salvataggio parte dalla
10.5: le conversazioni delle versioni precedenti rimaste solo in RAM non vengono
ricostruite. Una conferma interrotta dalla chiusura non viene rieseguita quando
si riapre la chat; chiedendo di riprendere verranno richieste nuove autorizzazioni.

La 10.6 rende l’interruzione attiva anche durante la risposta di Groq/Ollama,
prima degli header o a metà corpo HTTP. Lo stop non causa fallback verso un
altro provider, non applica risposte tardive e permette una nuova richiesta.
Funziona anche prima dell’avvio di una richiesta accodata e durante una conferma.
Per i comandi locali termina anche i processi figli del gruppo avviato da JARVIS.

La 10.7 è stata verificata con modelli reali nella finestra dell’app: lettura,
scrittura, copia, ricerca con nomi simili, terminale, memoria, web e MCP locale.
Il [rapporto delle prove](docs/design/live-tool-audit-2026-11-02.md) conserva anche
gli insuccessi, con richieste, tool effettivi, risultati e schermate.

**Comandi → Controlla strumenti disponibili**, oppure `strumenti` nella chat,
mostra il registro attivo senza richiedere un modello. Posta e MCP vengono
proposti ai modelli solo quando configurati/abilitati; tutti i modelli della
stessa sessione ricevono lo stesso registro e rispettano lo stesso Guardiano.
Le conferme hanno nomi italiani e dettagli completi scorrevoli, con sintesi
compatta per lasciare visibili i pulsanti anche su finestre piccole.

`calculate_numbers` ordina fino a 1.000 numeri e calcola la somma, mantenendo
duplicati e valori negativi. Le ricevute `verified` dei file attestano i byte
salvati, mentre il risultato deve ancora rispettare il contenuto del compito.
La ricerca PyPI/npm estrae il nome del pacchetto dalla query e non presenta più
gli errori dei registri come risultati validi. Anche il processo curl è annullabile.

Il prompt mantiene le istruzioni statiche all’inizio e non cambia più a ogni
minuto, favorendo il riuso del contesto. Un completamento Groq vuoto viene
richiesto nuovamente una sola volta prima del fallback. Questi miglioramenti
non rendono equivalenti i modelli: nelle prove il locale 7B era più lento e
alcuni compiti composti producevano contenuti errati, pur avendo accesso ai tool.

Un 429 Groq con `Retry-After` breve viene riprovato fino a due volte sullo stesso modello
prima di passare alla riserva. **Servizi → Attesa massima quota Groq** imposta
il limite totale (120 secondi di default, 0 per saltare l’attesa, massimo 120). La barra
di stato mostra il conto alla rovescia, interrompibile con lo stesso pulsante.
L’attesa non modifica le quote dell’account né risolve un 402 Cerebras.

Il tempo massimo di Ollama passa da 60 a 300 secondi e si può impostare fra 60 e
900 in **Servizi**. Il contatore nella barra di stato mostra l’attesa. Un errore
conserva richiesta e risultati e libera il compito: si può scrivere **riprendi il
compito** o una nuova richiesta. Le scritture e copie verificano i byte pubblicati
tramite rilettura e SHA-256; `write_text_file` restituisce anche il testo riletto.
Le frasi naturali che iniziano con «ricordi», «aiuto» o «esci» non vengono più
intercettate come comandi brevi quando continuano con altre parole.

Groq usa ragionamento medio per GPT-OSS, un limite di risposta di 2048 token e
non reinvia il ragionamento privato nella cronologia. Un HTTP 400 con codice
`tool_use_failed` consente un solo nuovo completamento correttivo, prima di
qualsiasi esecuzione, mantenendo la validazione degli strumenti del servizio.
Per un 429 il cooldown rispetta `Retry-After` quando presente; non cambia il
limite dell’account. Riferimenti: [limiti Groq](https://console.groq.com/docs/rate-limits),
[API Groq](https://console.groq.com/docs/api-reference).
La riserva `openai/gpt-oss-20b` è facoltativa, disattivata di default e configurabile
in **Servizi → Consenti anche Groq 20B**. Se abilitata, viene usata solo durante
la quota del 120B e il preferito torna disponibile dopo il cooldown. Il passaggio
non modifica la preferenza salvata. La catena Cerebras → Ollama resta attiva.
Le verifiche manuali non attivano riserve.

La 10.8 distingue i limiti di token al minuto/giorno nei dettagli dell’errore.
I vecchi turni vengono ridotti per contenere l’invio a Groq; il compito attivo e
i suoi risultati non vengono tagliati, e l’archivio locale rimane integro.
La stima del contesto è euristica; i token effettivi e i numeri delle quote sono
registrati in `jarvis-data/provider-events.jsonl`, senza prompt, chiavi o file.
Una richiesta singola oltre la quota viene ridotta sui soli turni precedenti;
se il compito attivo è ancora troppo grande, l’errore è esplicito.

**Chat salvate** ora offre ricerca nei titoli e messaggi (**Ctrl+F**), **Fissa**
per tenere una chat in cima ed **Elimina**. **Annulla eliminazione** ripristina
l’ultima chat cancellata durante la sessione aperta. Pin e archivio persistono
al riavvio; l’annullamento dell’eliminazione è solo in memoria fino alla chiusura.

Il web usa **curl**, senza Tavily. `web_search` trova URL e visita le fonti;
`read_url` seleziona campi e passaggi pertinenti da HTML, JSON, JSON-LD,
stato JavaScript incorporato, oggetti/variabili statiche. Rimuove navigazione,
pubblicità e tracking, conserva URL e percorsi dei campi e riconosce CAPTCHA.
Se una pagina è vuota, può seguire fino a tre risorse statiche dello stesso sito
e URL JSON letterali individuati negli script. Non esegue JavaScript: dati che
richiedono esecuzione, login o verifiche interattive vengono segnalati come tali.
`site_analyzer.py` usa lo stesso estrattore per le schede diagnostiche.
Il [rapporto 10.8](docs/design/10.8-web-quota-chats.md) distingue prove riuscite,
prova interrotta per quota e limiti rimasti.

Il fallback automatico è attivo per impostazione predefinita: con Groq preferito,
la catena è **Groq → Cerebras → Ollama**. Se si sceglie NVIDIA, OpenAI o Cerebras,
viene provato prima quel provider, poi Groq, Cerebras e Ollama senza duplicati.
Con Ollama preferito le richieste restano locali. In **Servizi** si può disabilitare
il fallback, riprovare il preferito e verificare il modello locale. I provider
senza chiave o con errori vengono saltati temporaneamente (30 secondi per errori
transitori, 5 minuti per autenticazione, credito o modello non disponibile).
Il provider riuscito continua lo stesso compito; non si riparte dall’inizio e
non si rieseguono gli strumenti già completati. Il ruolo, i permessi Linux,
gli strumenti e le conferme del Guardiano appartengono al nucleo JARVIS e restano
gli stessi per ogni modello. Il provider effettivo e i fallimenti compaiono in chat
e nel registro **Attività**. La preferenza salvata non viene cambiata dal fallback.

Ollama viene caricato solo al primo utilizzo. Se è installato ma non avviato,
JARVIS può avviare `ollama serve` locale con il ruolo amministratore; non usa sudo
e non scarica modelli. Sceglie un modello già installato compatibile con gli
strumenti. Il servizio avviato resta disponibile anche dopo la chiusura dell’app;
il log è in `$XDG_DATA_HOME/jarvis-data/ollama-service.log`. Se mancano modelli
compatibili, la UI mostra un errore senza ridurre gli strumenti disponibili.

In **Servizi**, **Carica modelli** legge il catalogo dell’account; **Verifica chat**
invia soltanto `Reply only OK.`, senza profilo, cronologia o dati locali. Il catalogo
accessibile non garantisce che l’account abbia credito o accesso all’inferenza.
Ogni provider conserva il proprio modello, evitando di inviare a Cerebras o
NVIDIA il nome del modello Groq/Ollama. Il cambio manuale del provider azzera la
conversazione corrente in RAM, mantenendo quella precedente nell’archivio; il
fallback durante un compito ne conserva il contesto.
**Verifica chat** controlla soltanto il provider scelto e non attiva fallback,
così un altro provider funzionante non nasconde l’errore del servizio verificato.
Il 402 osservato su Cerebras il 2 novembre 2026 riguarda credito/fatturazione:
la selezione di un modello diverso non ha rimosso il blocco dell’account.

Predefiniti: Groq `openai/gpt-oss-120b`, Cerebras `gpt-oss-120b`, NVIDIA
`nvidia/nemotron-3.5-lightning-30b-a3b`. Il modello v9 NVIDIA
`qwen/qwen3.5-397b-a17b` non compare nel catalogo cloud verificato ed è indicato
come endpoint gratuito deprecato nel catalogo NVIDIA. Il ragionamento NVIDIA
approfondito è opzionale; il timeout di inferenza NVIDIA è 180 secondi.

## Installazione e ripristino

- `./install.sh`: installa il progetto locale, un ambiente Python pulito e l’icona.
- `./install_from_code.sh`: importa da `~/Scaricati/code` o `~/Downloads/code`
  nella cartella del progetto, normalmente `~/Documenti/modelli`.
- `./install_from_code.sh --source /percorso/code`: seleziona un’altra sorgente locale.
- `./reinstall.sh`: ripristina la copia del codice dell’ultima installazione riuscita
  e ricrea l’ambiente Python.
- `./reinstall.sh --voice`: aggiunge le dipendenze vocali; richiede rete.
- `python3 installer.py --dry-run --no-desktop`: mostra le operazioni senza scrivere.

Le stesse operazioni sono disponibili nel pannello Manutenzione.
La sorgente importata deve contenere la versione desktop completa; un pacchetto
incompleto viene rifiutato prima di modificare l’installazione.

Il codice precedente viene conservato in `backups/install-<data>/`, l’ambiente
nuovo in `.runtime/`, la sorgente di ripristino in `.install-source/`.
Gli installer non importano segreti dalla cartella scaricata e non cancellano
`.env`, `.env.enc`, utenti, memorie o skill personali. Le copie di backup restano
finché non vengono rimosse manualmente.

## Impostazioni

- **Servizi:** provider, modello, chiave API mascherata, lingua e fallback.
  **Configura integrazioni** raccoglie le altre chiavi e le variabili avanzate.
  Le nuove impostazioni dei servizi vengono usate dalle richieste successive.
  Scegliendo Groq viene proposto `openai/gpt-oss-120b`; **Verifica provider AI**
  controlla credenziali e modelli accessibili senza inviare la conversazione.
  I modelli remoti devono essere abilitati nell’account; non vengono scaricati.
- **Email:** controlla, cerca e leggi la posta Gmail. L’invio mostra destinatario,
  oggetto e corpo in una richiesta di conferma. Le credenziali accettano
  `GMAIL_ADDRESS` e `GMAIL_APP_PASSWORD` (oppure i nomi legacy `EMAIL_ADDRESS`
  e `EMAIL_PASSWORD`).
- **Profili:** nome e personalità, profilo attivo, gestione utenti e PIN.
  Il cambio profilo azzera la conversazione in RAM. I profili sono preferenze,
  non account separati; le memorie locali restano condivise.
- **Voce:** lettura risposte e pulsante microfono, selezione dispositivo.
  Il riconoscimento non ascolta in background. Whisper scarica il suo modello al
  primo utilizzo. TTS usa gTTS e richiede rete e un riproduttore audio di sistema.
- **Skill:** attivazione, configurazione, ricaricamento e importazione di `SKILL.md`.
  Le skill Python integrate vengono importate solo quando abilitate; le nuove
  skill Markdown sono istruzioni per il modello. Non vengono eseguiti installer
  di dipendenze durante l’importazione dei moduli.
- **MCP:** aggiunta, modifica, rimozione e abilitazione server locali stdio,
  elenco strumenti e chiamate con argomenti JSON. Il server parte al primo uso;
  ogni chiamata strumento viene mostrata prima dell’esecuzione.
- **Comandi:** pulsanti per informazioni PC, ricerca e lettura di file di testo sulla
  Scrivania, aiuto, memoria, statistiche, skill, MCP,
  standby, ricordi, traduttore e campo comandi libero.

Configurazione: `$XDG_CONFIG_HOME/jarvis/config.json` (default `~/.config/jarvis`).
Credenziali dei servizi: `.env` nella stessa directory, permessi 0600; il vecchio
`.env` del progetto viene letto come base e non modificato. I campi sono mascherati,
ma il file delle API non è cifrato. `.env.enc` legacy resta intatto.
Dati e skill personali: `$XDG_DATA_HOME/jarvis-data`.
Gli utenti esistenti vengono migrati dal `users.json` del progetto; il PIN è
richiesto se è già presente un amministratore. I secondari non possono aprire
le impostazioni o gestire skill/MCP; i comandi rispettano `command_policy`.

Comandi utili:

```
/aiuto
pc
trova test sulla scrivania
leggi file test per jarvis.txt
ricorda il progetto usa Python
memoria
lingua it
traduttore it en
skill
skill on Linux Sysadmin
skill off Linux Sysadmin
skill import /percorso/nome-skill/SKILL.md
skill reload
mcp
mcp add nome {"command":"python3","args":["/percorso/server.py"]}
mcp on nome
mcp tools nome
mcp call nome strumento {"argomento":"valore"}
mcp remove nome
run uname -a
impostazioni
```

## Verifiche e pulizia

`python3 -m unittest discover -s tests -v` verifica nucleo, PIN e ruoli, memoria,
configurazione, skill, protocollo MCP, timeout e rollback dell’installer.
I test del fallback controllano anche errori HTTP, conferme, risultati degli
strumenti, permessi dei secondari, annullamento e scelta dei modelli locali.
`python3 tests/gui_smoke.py` richiede un display e visita tutti i pannelli.
`python3 tests/gui_agent_smoke.py` verifica conferme, ripresa e layout con dati fittizi.
`python3 tests/gui_chat_actions.py` verifica ricerca, pin, eliminazione e annulla
nella vera finestra a 850×620, senza chiamate ai modelli.
`python3 tests/gui_api_keys.py` verifica gestione chiavi, file privati, trasporto
HTTP e rotazione dopo 429 durante un compito. Usa un server locale simulato e
chiavi sintetiche: nessuna quota cloud viene consumata.
`python3 tests/curl_fetch_smoke.py` verifica curl reale, HTTP, redirect, UTF-8,
lettura grezza, ricerca nel corpo, paginazione, cache/refresh, stop e presenza
di `curl_fetch` nel payload degli strumenti. Solo server locale; niente API cloud.
`python3 tests/gui_history_smoke.py` riavvia la UI, riapre una chat e verifica
che un errore del modello permetta una nuova risposta con il contesto conservato.
`python3 tests/network_cancel_smoke.py` usa soltanto localhost e processi
temporanei per verificare stop durante header/corpo/errori HTTP e comandi figli.
`python3 tests/gui_cancel_smoke.py` preme Interrompi nella vera finestra,
controlla la disconnessione e invia subito un’altra richiesta.
`python3 tests/gui_hud_preview.py` controlla animazioni, movimento ridotto/off,
scelta provider e produce una GIF dalla sola finestra di prova.
`python3 project_audit.py` controlla sintassi, duplicati identici e raggiungibilità
tramite import statici; i candidati non vengono cancellati automaticamente.

La versione precedente è disponibile nel ramo `old-v9`.
Il modulo email è collegato alla UI e agli strumenti del modello. I moduli
Discord e Blender e il vecchio pianificatore sono conservati come integrazioni
opzionali; non si avviano dal ciclo agente desktop. L’accesso visuale allo schermo
della v9 non è abilitato per il modello testuale Groq configurato.
I server MCP remoti HTTP/SSE non sono ancora supportati.

Specifica del trasporto MCP implementato:
https://modelcontextprotocol.io/specification/2024-11-05/basic/transports

## Aggiornamento HUD e accesso

Il PIN si inserisce nella schermata di accesso della finestra principale:
non si aprono finestre modali prima del ciclo eventi. Dopo tre tentativi errati
lo sblocco attende 30 secondi. Gli errori delle operazioni in background vengono
mostrati nella conversazione e nella barra di stato e non bloccano i controlli.
Il nucleo HUD ciano ruota durante l’elaborazione. Nelle finestre strette il pannello
decorativo lascia spazio alla conversazione.
Il test grafico usa il ciclo eventi reale, tasti e clic sintetici: verifica PIN,
invio con pulsante e Invio, navigazione durante una richiesta e dopo un errore.

## Conversazione vocale e nucleo fluttuante

Il pulsante **Zittisci** ferma la voce in riproduzione e scarta il resto della
risposta parlata. Il testo rimane nella conversazione. Il microfono resta aperto
fra i turni: un buffer volatile conserva fino a tre secondi senza salvare audio
su disco e scarta i campioni della riproduzione di JARVIS. Il limite del lettore
segue la durata decodificata dell’audio, per evitare attese nel rilascio del dispositivo.
Le risposte vengono sintetizzate per frasi; le richieste di sintesi bloccate
si possono interrompere e hanno un limite di 15 secondi per frase.

Quando si usa un’altra applicazione o una pagina delle impostazioni, il nucleo
compare in basso a destra: HUD nella modalità scrittura, sfera nella modalità
vocale. Il nucleo compatto usa una finestra Qt trasparente, senza pannello di
sfondo, e continua a muoversi in attesa. Su KDE è ancorato all’angolo dell’area
utile del monitor, sopra la barra di sistema. Un clic sull’HUD apre una chat
rapida con sfondo, gestita dal desktop per ricevere correttamente la tastiera e collegata alla conversazione
corrente. **Invio** manda il messaggio, **Maiusc+Invio** aggiunge una riga,
**Esc** richiude la chat rapida. Le autorizzazioni del Guardiano rimangono nella
finestra principale.
Un clic fuori dal riquadro lo riporta alle dimensioni del nucleo e conserva
la bozza non ancora inviata.

In modalità vocale **dormi** mette JARVIS in standby: continua a rilevare il
richiamo, ma ignora le altre richieste. **Hey JARVIS** lo riattiva; si può anche
dire «hey JARVIS, che ore sono?» in una sola frase. Le varianti della v9 sono
raccolte in `voice_commands.py`, comprese «arvis» e «jarvi», con l’aggiunta di
«jarv». Il filtro dello speaker e i permessi vocali restano attivi in standby.
Il pulsante **Dormi / Risveglia** e un clic sulla sfera fluttuante permettono
di cambiare stato anche senza parlare.

In **Impostazioni → Sicurezza** l’amministratore sceglie separatamente il tempo
del blocco con password e dello standby vocale, da 1 a 1440 minuti. Entrambi
si possono disattivare; le preferenze sono persistenti e si applicano subito.
Per lasciare JARVIS disponibile come server disattiva **Blocco automatico per
inattività**. L’accesso iniziale e il logout mantengono il normale flusso password.

In modalità scrittura, dopo 120 secondi senza scrivere (valore predefinito) la sessione viene
sospesa e richiede di nuovo la password dell’account corrente. Scrivere o
inviare un messaggio riavvia il conteggio. Il conto alla rovescia è disattivato
finché ci sono operazioni in esecuzione o in coda. Parte dalla durata impostata
solo quando l’ultimo lavoro termina e il risultato è disponibile, compresa
la fine della sintesi e della riproduzione vocale e delle richieste Discord.
Il semplice collegamento del bot e l’ascolto senza parole non sono lavori
attivi: il microfono continua a rilevare il silenzio per lo standby vocale.
La conversazione viene conservata;
la password di un altro account non può sbloccarla. **Logout**, disponibile
anche nella schermata sospesa, permette di cambiare utente. Le risposte e
le operazioni rimaste in coda prima della sospensione vengono annullate.
Se l’app è in secondo piano, il nucleo sospeso resta disponibile: un clic
apre la schermata della password, senza mostrare la conversazione.
In modalità vocale, il tempo di silenzio impostato (due minuti di default) porta allo standby:
si riparte con «hey JARVIS», con **Risveglia** o con un clic sul nucleo.

In standby gli strati della sfera si separano; al risveglio tornano al loro
posto. Durante le risposte il nucleo segue l’ampiezza del file audio riprodotto,
con transizioni graduali e rispetto dell’impostazione di movimento ridotto.

Su KDE/Wayland l’osservatore usa gli eventi fullscreen di KWin, inclusi quelli
delle applicazioni Wayland native. Il nucleo viene nascosto quando una finestra
in primo piano è a schermo intero e riappare all’uscita; su X11 è disponibile un
controllo EWMH. Se il desktop non fornisce uno stato affidabile, il nucleo
fluttuante rimane nascosto. Riferimento: [API KWin](https://develop.kde.org/docs/plasma/kwin/api/).

Entrambi i nuclei sono disegnati con tracciati vettoriali con antialias:
Cairo nella finestra principale e Qt nel nucleo trasparente.
La fase di rotazione prosegue tra gli stati e velocità e ampiezza cambiano
gradualmente. Restano disponibili movimento ridotto e animazioni disattivate.
Le dipendenze sono incluse nell’installazione con `--voice` o `--vision`;
`./reinstall.sh --desktop-effects` installa soltanto questi effetti desktop.
Senza Cairo rimane disponibile il renderer Canvas di Tk.

In **Visione → Prova il cursore JARVIS** si apre una pagina locale in una
scheda separata: la freccia con etichetta JARVIS si sposta, scrive un messaggio
e preme il pulsante di prova. Non usa servizi vision esterni. La stessa freccia
identifica le azioni nel browser operativo e viene esclusa dagli screenshot
inviati ai modelli. Il puntatore separato controlla il browser JARVIS; il
controllo indipendente delle app native richiede il supporto del compositor.
