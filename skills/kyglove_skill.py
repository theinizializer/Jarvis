"""Skill KyGlove caricata dal pannello Jarvis."""
from skills.skill_base import JarvisSkill
from skills.kyglove_workshop import ROOT, BASE


class KyGloveSkill(JarvisSkill):
    name = "KyGlove"
    description = "Firmware additivo, test, diagnostica e aggiornamenti OTA del KyGlove ESP32-S3"
    version = "2.0"
    author = "Radostin"
    icon = "🦾"

    system_prompt_addon = f"""
Per KyGlove usa gli strumenti kyglove_*. La guida locale è {ROOT}/README.md.
A/B pollice/indice sono MPU-6050 a 0x69; C/D/E sono ADXL345. I cinque sensori
sono separati dal TCA9548A, canali 0,1,2,4,3; INT 41,38,16,18,17. I touch
39,40,15,7,6 sono A′–E′. Gli impatti formano accordi in 70 ms, cooldown
predefinito 700 ms per dito. Le prime 26 combinazioni in ordine per numero
di dita e poi alfabetico sono A–Z. I modificatori globali valgono nelle due
modalità. GloveBindings.h definisce le tabelle. ImpactBalance.h calibra su
cinque colpi per dito. GloveLogic.h confronta i picchi con il riferimento
personale, con denominatore limitato a 1. Il mouse integra il giroscopio del
pollice, Y orizzontale e X verticale. In test il mouse resta attivo in scrittura.
Leggi /status, /settings e /diagnostics con kyglove_device. Per regolare un
parametro usa configure e riporta revisione e valori riletti. Annotare la
versione prima di aggiungere una funzione. Il firmware stabile è {BASE}.
Il core, il suo .bin, le calibrazioni e le revisioni precedenti sono protetti.
Usa solo kyglove_add e kyglove_build per il codice. Parent = versione sul guanto
(per jarvis-<id> passa solo <id>). Ogni aggiunta eredita tutte le estensioni
del parent. Crea un header C++ con namespace uguale al nome, funzione
void tick(unsigned long now, bool writing) non bloccante. Per Arduino usa
#ifdef ARDUINO. Isola la logica verificabile in funzioni richiamate anche dai
test. Tre funzioni C++ bool nominale(), limite(), ripetizione() verificano
rispettivamente un caso d'uso reale, un bordo/errore e un evento ripetuto.
Sono eseguite singolarmente in WebAssembly senza import; usa C++ senza
dipendenze hardware per tali casi. Un test deve fallire se il comportamento
corrispondente si rompe. In OTA e nel test del pannello tick è sospeso.
Sorgenti in {ROOT}/jarvis-extensions/<id>/modules.json, rapporto e app.bin
in verified/ dopo tre casi per estensione, nove suite e compilazione ESP32-S3.
Se fallisce, leggi l'errore, correggi solo l'aggiunta creando una nuova revisione
dallo stesso parent e ripeti. Se non riesci, kyglove_consult chiede una revisione
con provider auto prima a NVIDIA e poi a Groq se necessario. Invia solo il frammento della nuova funzione e il test fallito.
Un parere non supera i test. Dopo due correzioni senza progressi spiega il
blocco. Per installare usa upload della revisione verificata. Il tool controlla
versione, spazio e conferma del boot. Per tornare indietro usa rollback.
Per ripristinare specificamente OTA5 usa restore_base dal binario originale verificato.
In caso di perdita Wi-Fi rileggi lo stato. L'AP si apre tenendo A′B′C′E′ 3 s.
Slot OTA da 1.966.080 byte su flash fisica N16. La prova hardware finale
riguarda gesto, direzione e comfort; chiedi l'esito solo quando necessario.
"""
