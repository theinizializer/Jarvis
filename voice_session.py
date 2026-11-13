"""User-bound microphone enrollment and speaker filtering for desktop voice mode."""
import time
from app_config import data_dir

READING_TEXT = (
    'Oggi preparo il mio assistente per ascoltare la mia voce. Parlo con calma e in modo naturale, '
    'come durante una conversazione. Posso chiedere informazioni, aprire una pagina, cercare un file '
    'oppure interrompere una risposta. Alla fine di questa frase continuo ancora per qualche secondo.'
)
READING_TRANSLATIONS = {
    'it': READING_TEXT,
    'en': 'Today I am preparing my assistant to listen to my voice. I speak calmly and naturally, just as I do in a conversation. I can ask a question, open a page, find a file or interrupt a reply. At the end I continue speaking for a few seconds.',
    'fr': 'Aujourd’hui je prépare mon assistant à écouter ma voix. Je parle calmement et naturellement, comme dans une conversation. Je peux poser une question, ouvrir une page, chercher un fichier ou interrompre une réponse. À la fin je continue à parler pendant quelques secondes.',
    'de': 'Heute bereite ich meinen Assistenten darauf vor, meine Stimme zu erkennen. Ich spreche ruhig und natürlich, wie in einem Gespräch. Ich kann eine Frage stellen, eine Seite öffnen, eine Datei suchen oder eine Antwort unterbrechen. Am Ende spreche ich noch ein paar Sekunden weiter.',
    'es': 'Hoy preparo mi asistente para escuchar mi voz. Hablo con calma y de forma natural, como en una conversación. Puedo hacer una pregunta, abrir una página, buscar un archivo o interrumpir una respuesta. Al terminar sigo hablando unos segundos más.',
    'pt': 'Hoje preparo o meu assistente para ouvir a minha voz. Falo com calma e de forma natural, como numa conversa. Posso fazer uma pergunta, abrir uma página, procurar um ficheiro ou interromper uma resposta. No final continuo a falar durante alguns segundos.',
    'nl': 'Vandaag bereid ik mijn assistent voor om naar mijn stem te luisteren. Ik spreek rustig en natuurlijk, zoals tijdens een gesprek. Ik kan een vraag stellen, een pagina openen, een bestand zoeken of een antwoord onderbreken. Aan het einde spreek ik nog een paar seconden door.',
    'ru': 'Сегодня я настраиваю своего помощника, чтобы он слушал мой голос. Я говорю спокойно и естественно, как во время обычного разговора. Я могу задать вопрос, открыть страницу, найти файл или прервать ответ. В конце этой фразы я продолжаю говорить ещё несколько секунд.',
    'bg': 'Днес подготвям своя асистент да слуша гласа ми. Говоря спокойно и естествено, както по време на обикновен разговор. Мога да задам въпрос, да отворя страница, да намеря файл или да прекъсна отговор. В края продължавам да говоря още няколко секунди.',
    'lb': 'Haut bereeden ech mäin Assistent drop vir, meng Stëmm ze lauschteren. Ech schwätze roueg an natierlech, wéi an engem Gespréich. Ech kann eng Fro stellen, eng Säit opmaachen, eng Datei sichen oder eng Äntwert ënnerbriechen. Zum Schluss schwätzen ech nach e puer Sekonne weider.',
}

FREE_SPEECH = 'Parla liberamente per 12 secondi. Racconta la tua giornata o descrivi ciò che hai davanti.'


class VoiceSession:
    def __init__(self, host):
        from voice_module import PROFILES_DIR, SpeakerVerifier, VoiceInput, WHISPER_OK, SD_OK
        if not WHISPER_OK or not SD_OK:
            raise RuntimeError('Componenti vocali mancanti. Installa requirements-voice.txt nel venv di JARVIS.')
        if not host.session_user or not host.registry.exists(host.session_user):
            raise PermissionError('Accedi con una password di sessione prima di creare il profilo vocale.')
        if not host._cfg.get('microphone'):
            raise ValueError('Scegli prima il microfono.')
        self.host, self.user, self.lang = host, host.session_user, host._cfg['lang']
        profiles_dir = data_dir() / 'voice_profiles'
        prefix = f'{self.user.lower()}_{self.lang.lower()}'
        legacy = [p for p in PROFILES_DIR.glob('*.np*') if p.stem in {prefix, prefix + '_ecapa'}]
        if legacy and not any(p.stem in {prefix, prefix + '_ecapa'} for p in profiles_dir.glob('*.np*')):
            profiles_dir = PROFILES_DIR
        self.speaker = SpeakerVerifier(profiles_dir, threshold=.50)
        if self.speaker._encoder is None:
            raise RuntimeError('Motore speaker non disponibile. Installa resemblyzer o SpeechBrain.')
        self.input = VoiceInput(host._cfg['microphone'])
        host._voice_input = self.input

    @property
    def enrolled(self):
        return self.speaker.has_user_profile(self.user, self.lang, verified=True)

    def verify_owner(self, password):
        if self.host.session_user != self.user or not self.host.registry.verify(self.user, password):
            raise PermissionError('Password della sessione errata.')

    def record_sample(self):
        engine = self.host._tts_engine
        if engine and engine.is_busy:
            raise ValueError('Attendi la fine della risposta vocale prima di registrare il tuo campione.')
        from audio_checks import recording_report
        audio = self.input.record_sample(12)
        quality = recording_report(audio)
        if quality['ok'] and self.input.is_ready():
            transcript = self.input._transcribe(audio, self.input._model, self.lang)
            if not transcript:
                quality.update(ok=False, message='La voce è presente ma le parole non sono riconoscibili. Parla più vicino al microfono e riprova.')
            else:
                quality['transcript'] = transcript
        return {'audio': audio, 'quality': quality}

    def enroll(self, samples):
        if self.host.session_user != self.user or self.host._cfg['lang'] != self.lang:
            raise PermissionError('La sessione è cambiata. Ripeti la registrazione.')
        self.speaker.enroll_samples(self.user, self.lang, *samples, cancel_event=self.input._stop_event)

    def listen(self):
        if self.host.session_user != self.user or self.host._cfg['lang'] != self.lang:
            raise PermissionError('La sessione o la lingua è cambiata. Riattiva la modalità vocale.')
        if not self.input._wait_whisper(60):
            raise RuntimeError(self.input.load_error_msg() or 'Whisper non disponibile.')
        engine = self.host._tts_engine
        # Discard any capture that overlaps playback, including delayed generation.
        if engine and engine.is_busy:
            return ''
        started = time.monotonic()
        not_before = max(started-.5, getattr(engine, 'last_playback', 0)+.05 if engine else 0)
        audio = self.input.capture_continuous(not_before)
        if engine and (engine.is_speaking or getattr(engine, 'last_playback', 0) >= started):
            return ''
        if audio is None:
            return ''
        self.host.events.put({'type': 'voice_stage', 'status': 'recognizing'})
        recognize_started = time.monotonic()
        matched, score = self.speaker.match_profile(audio, self.user, self.lang, .50)
        self.host.events.put({'type': 'voice_match', 'score': round(score, 3), 'matched': matched})
        if not matched:
            return ''
        text = self.input._transcribe(audio, self.input._model, self.lang) or ''
        self.host.events.put({'type': 'voice_stage', 'status': 'recognized',
                              'seconds': round(time.monotonic() - recognize_started, 2)})
        return text

    def stop(self):
        self.input.stop()
