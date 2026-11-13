# Installare Jarvis v10 su Linux

Servono Git, Python 3 con venv e Tk, e un ambiente desktop Linux per la GUI. Su Arch i pacchetti di base sono git, python e tk.

```sh
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis
./install.sh --dry-run
./install.sh
jarvis
```

L'installer crea l'ambiente Python, registra il lanciatore nel menu e installa il comando in ~/.local/bin. La destinazione predefinita è ~/.local/share/jarvis-v10 (o XDG_DATA_HOME/jarvis-v10). Aggiungere ~/.local/bin al PATH se il comando non viene trovato. Per una postazione senza menu usare --no-desktop. Per installare dipendenze opzionali usare --desktop-effects, --voice e/o --vision. Per aggiornare, eseguire git pull --ff-only nel clone e rilanciare ./install.sh con gli stessi moduli opzionali.

La v9 si trova nel ramo old-v9. Il comando globale jarvis e l'icona del menu avviano la v10 dopo l'installazione. Conservare la directory locale della v9 e il suo archivio cifrato per i servizi precedenti.

## Collegare il guanto

Per installare il firmware separato nella posizione riconosciuta dalla skill:

```sh
git clone https://github.com/theinizializer/KyGlove.git "${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-v10/kyglove-firmware"
cd "${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-v10/kyglove-firmware"
python3 prepara.py
```

Seguire i prerequisiti Arduino del README KyGlove. Inserire la password già configurata se si usa un guanto esistente. Per usare un clone in un altro percorso impostare KYGLOVE_ROOT nell'ambiente di Jarvis. Attivare KyGlove nelle impostazioni delle skill. Il repository del guanto contiene cablaggio, suite di test e spazio per gli STL.
