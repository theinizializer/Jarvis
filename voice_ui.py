"""Native voice workspace, enrollment dialog and detachable core."""
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from jarvis_hud import OrbitalCore
from floating_ui import FloatingUIMixin
from voice_commands import VoiceDialogue
from app_config import data_dir


class VoiceUIMixin(FloatingUIMixin):
    def _init_voice_ui(self):
        self._voice_session = None
        self._voice_enrollment_window = None
        self._voice_force_enroll = False
        self._voice_enrollment_verified = None
        self._voice_generation = 0
        self._voice_dialogue = VoiceDialogue()
        self._init_floating_ui()
        self.voice_bar = ttk.Frame(self.chat_frame)
        controls = ttk.Frame(self.voice_bar)
        controls.pack(anchor='center', pady=8)
        self.voice_stop_button = ttk.Button(controls, text='Zittisci', command=self._interrupt_speech)
        self.voice_stop_button.pack(side='left', padx=5)
        self._voice_standby_label = tk.StringVar(value='Dormi')
        ttk.Button(controls, textvariable=self._voice_standby_label,
                   command=self._toggle_voice_standby).pack(side='left', padx=5)
        ttk.Button(controls, text='Torna alla scrittura', command=self._leave_voice,
                   style='Primary.TButton').pack(side='left', padx=5)

    def _ask_credential(self, admin=False, context=''):
        if time.monotonic() < self._login_retry_at:
            self.status.set('Attendi prima di riprovare la password.')
            return None
        prompt = 'Password amministratore per questa operazione:' if admin else 'Password della tua sessione:'
        if context:
            prompt = context[:700] + '\n\n' + prompt
        password = simpledialog.askstring('Autorizzazione', prompt, show='●', parent=self.root)
        if password is None:
            return None
        key = self.jarvis.session_user
        valid = self.jarvis.registry.verify_admin(password) if admin else self.jarvis.registry.verify(key, password)
        if not valid:
            self._login_failures += 1
            if self._login_failures >= 3:
                self._login_retry_at = time.monotonic() + 30
                self._login_failures = 0
            self.status.set('Password errata.')
            return None
        self._login_failures = 0
        return password

    def _toggle_voice(self, force_enroll=False):
        if self._voice_enrollment_window and self._voice_enrollment_window.winfo_exists():
            self._voice_enrollment_window.lift()
            return
        self._voice_force_enroll = force_enroll
        if self.jarvis.voice_mode:
            self._leave_voice()
            return
        if self.busy or self.jarvis.tool_approval_pending or self.jarvis.role == 'locked':
            self.status.set('Completa il compito attivo prima di cambiare modalità.')
            return
        if not self.jarvis.session_user:
            self.status.set('Configura l’account amministratore e accedi prima di attivare la voce.')
            return
        self._voice_enrollment_verified = None
        directory = data_dir() / 'voice_profiles'
        prefix = f'{self.jarvis.session_user.lower()}_{self.jarvis._cfg["lang"].lower()}'
        legacy = Path.home() / 'jarvis_memory' / 'voice_profiles'
        files = [p for root in (directory, legacy) for p in root.glob('*.np*')
                 if p.stem in {prefix, prefix + '_ecapa'}]
        if not files or force_enroll:
            if not messagebox.askyesno('Profilo vocale', 'Registrare il profilo vocale per il tuo account e questa lingua?', parent=self.root):
                return
            if self._ask_credential() is None:
                return
            self._voice_enrollment_verified = (self.jarvis.session_user, self.jarvis._cfg['lang'])
        self._microphones(on_chosen=self._prepare_voice_session)

    def _prepare_voice_session(self):
        from voice_session import VoiceSession
        self.submit(lambda: VoiceSession(self.jarvis), self._voice_session_ready)

    def _voice_session_ready(self, session):
        self._voice_session = session
        if session.enrolled and not self._voice_force_enroll:
            self._enter_voice()
            return
        if self._voice_enrollment_verified != (session.user, session.lang):
            if not messagebox.askyesno('Profilo vocale', 'Il profilo deve essere registrato con il controllo di qualità aggiornato, per questa lingua. Procedere?', parent=self.root):
                session.stop()
                return
            password = self._ask_credential()
            if password is None:
                session.stop()
                return
            session.verify_owner(password)
        self._voice_enrollment_verified = None
        self._enroll_voice_dialog(session)

    def _enroll_voice_dialog(self, session):
        from voice_session import READING_TRANSLATIONS, FREE_SPEECH
        reading = READING_TRANSLATIONS.get(session.lang, READING_TRANSLATIONS['en'])
        window = self._voice_enrollment_window = tk.Toplevel(self.root)
        window.title('Crea il tuo profilo vocale')
        window.geometry('640x470')
        window.configure(bg=self.bg)
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill='both', expand=True)
        title = tk.StringVar(value='1 di 2 · Lettura · 12 secondi')
        ttk.Label(frame, textvariable=title, font=('Noto Sans', 15, 'bold')).pack(anchor='w')
        text = tk.Text(frame, wrap='word', height=7, bg=self.surface, fg='#ECF1F7',
                       font=('Noto Sans', 12), padx=12, pady=12)
        text.pack(fill='both', expand=True, pady=14)
        text.insert('1.0', reading)
        text.configure(state='disabled')
        status = tk.StringVar(value='Leggi con voce naturale. Poi registreremo un campione spontaneo.')
        ttk.Label(frame, textvariable=status, wraplength=580).pack(fill='x', pady=6)
        samples, deadline = [], [0]
        closed = [False]
        def close():
            closed[0] = True
            session.stop()
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', close)
        def countdown():
            if closed[0] or not window.winfo_exists() or deadline[0] <= 0:
                return
            seconds = max(0, int(deadline[0] - time.monotonic()) + 1)
            status.set(f'Registrazione · {seconds} s' if seconds else 'Elaborazione del campione…')
            if seconds:
                self.root.after(200, countdown)
        def record():
            button.state(['disabled'])
            deadline[0] = time.monotonic() + 12
            countdown()
            def capture():
                try:
                    return session.record_sample(), None
                except Exception as exc:
                    return None, str(exc)
            self.submit(capture, collected)
        def collected(result):
            deadline[0] = 0
            recording, error = result
            if closed[0]:
                return
            quality = recording['quality'] if recording is not None else {'ok':False}
            if error or not quality['ok']:
                status.set(error or quality.get('message', 'Campione insufficiente. Ripeti la registrazione.'))
                button.state(['!disabled'])
                return
            samples.append(recording['audio'])
            if len(samples) == 1:
                title.set('2 di 2 · Conversazione spontanea · 12 secondi')
                text.configure(state='normal')
                text.delete('1.0', 'end')
                text.insert('1.0', FREE_SPEECH)
                text.configure(state='disabled')
                recognized = quality.get('transcript', '')
                status.set(quality['message'] + (f'\nHai detto: {recognized[:100]}' if recognized else '') + '\nOra premi Registra e parla liberamente.')
                button.state(['!disabled'])
            else:
                def publish():
                    try:
                        session.enroll(samples)
                        return None
                    except Exception as exc:
                        return str(exc)
                self.submit(publish, published)
        def published(error):
            if closed[0]:
                return
            if error:
                samples.clear()
                title.set('1 di 2 · Ripeti la lettura')
                text.configure(state='normal')
                text.delete('1.0', 'end')
                text.insert('1.0', reading)
                text.configure(state='disabled')
                status.set(error)
                button.state(['!disabled'])
                return
            samples.clear()
            closed[0] = True
            window.destroy()
            messagebox.showinfo('Profilo vocale verificato', 'Lettura e parlato spontaneo contengono voce riconoscibile. Il profilo è stato salvato e verificato. Ora puoi parlare con JARVIS.', parent=self.root)
            self._enter_voice()
        button = ttk.Button(frame, text='Registra', style='Primary.TButton', command=record)
        button.pack(anchor='e', pady=8)

    def _enter_voice(self):
        if self.closed or self.jarvis.tool_approval_pending:
            return
        self.jarvis.voice_mode = True
        self._user_activity()
        self.jarvis.voice_sleeping = False
        self._voice_dialogue = VoiceDialogue()
        self._voice_standby_label.set('Dormi')
        self._voice_generation += 1
        self.show('Conversazione')
        self.composer.pack_forget()
        self.workspace.pack_forget()
        self.voice_bar.pack(side='bottom', fill='x')
        self.voice_instruments.pack_forget()
        self.instrument_stage.pack_configure(fill='both', expand=True)
        self._text_hud = self.hud
        self.hud = OrbitalCore(self.core_canvas)
        self._apply_hud_theme()
        self.hud.resize(self.core_canvas.winfo_width(), self.core_canvas.winfo_height())
        self.session_text.set('Modalità vocale · permessi ridotti')
        self.root.after_idle(self.core_canvas.focus_set)
        self.root.after(400, self._voice_listen_next)

    def _leave_voice(self):
        if not self.jarvis.voice_mode:
            return
        self._voice_generation += 1
        self._interrupt_speech()
        self.jarvis.cancel_current()
        self.jarvis.voice_mode = False
        self._user_activity()
        self.jarvis.voice_sleeping = False
        if self.jarvis.tool_approval_pending:
            self.submit(lambda: self.jarvis.resume_pending_tool(False), self._answer)
        if self._voice_session:
            self._voice_session.stop()
        self._hide_floating_core()
        self.approval_frame.pack_forget()
        self.voice_bar.pack_forget()
        self.hud = self._text_hud
        self._apply_hud_theme()
        self.voice_instruments.pack(side='right', fill='y', padx=(6, 0))
        self.instrument_stage.pack_configure(fill='x', expand=False)
        self.workspace.pack(fill='both', expand=True)
        self.composer.pack(side='bottom', fill='x', pady=(16, 0), before=self.workspace)
        self.session_text.set('Sessione locale')
        self._listening = False
        self.root.after_idle(self.entry.focus_set)

    def _voice_listen_next(self):
        if self.closed or not self.jarvis.voice_mode:
            return
        engine = self.jarvis._tts_engine
        if self.busy or self.jarvis.tool_approval_pending or (engine and engine.is_busy):
            self.root.after(30, self._voice_listen_next)
            return
        generation = self._voice_generation
        def heard(text):
            if generation != self._voice_generation:
                return
            if text:
                self._handle_voice_text(text)
            self.root.after(30, self._voice_listen_next)
        def listen():
            try:
                return self._voice_session.listen()
            except Exception as exc:
                self.jarvis.events.put({'type':'voice_failure', 'message':str(exc)})
                return ''
        self.submit(listen, heard, listening=True)

    def _interrupt_speech(self):
        engine = self.jarvis._tts_engine
        if engine:
            engine.stop()
        self.status.set('Voce interrotta · puoi continuare a parlare' if self.jarvis.voice_mode else 'Voce interrotta')

    def _handle_voice_text(self, text):
        if text:
            self._user_activity()
        result = self._voice_dialogue.route(text)
        self.jarvis.voice_sleeping = self._voice_dialogue.sleeping
        self._voice_standby_label.set('Risveglia' if self.jarvis.voice_sleeping else 'Dormi')
        if result['action'] == 'sleep':
            if self.busy and not self._listening:
                self.jarvis.cancel_current()
            self._interrupt_speech()
            self.append('Tu', text)
            self.append('JARVIS', 'Standby. Di’ «hey JARVIS» per risvegliarmi.')
            self.session_text.set('Standby · in attesa di «hey JARVIS»')
            self.status.set('Standby · ascolto soltanto il richiamo')
        elif result['action'] == 'wake':
            self.session_text.set('Modalità vocale · permessi ridotti')
            if result['text']:
                self.send(result['text'])
            else:
                self.status.set('Sono qui · puoi parlare')
                self.append('JARVIS', 'Sono qui.')
                if not self.busy:
                    self.submit(lambda: self.jarvis._tts_say('Sono qui.'))
        elif result['action'] == 'command' and result['text']:
            self.send(result['text'])

    def _toggle_voice_standby(self):
        if not self.jarvis.voice_mode or self.jarvis.tool_approval_pending or (self.busy and not self._listening and not self.jarvis.voice_sleeping):
            return
        self._handle_voice_text('hey JARVIS' if self.jarvis.voice_sleeping else 'dormi')

