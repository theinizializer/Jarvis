"""Configurable inactivity limits for typed sessions and conversational voice mode."""
import time
import tkinter as tk
from tkinter import ttk
from app_config import inactivity_timeout,validate_idle_minutes


class SessionIdleMixin:
    def _init_session_idle(self):
        self._last_user_input = time.monotonic()
        self._idle_work_active = False
        self.root.bind_all('<KeyPress>', self._typing_activity, add='+')
        self.root.after(250, self._idle_watch)

    def _typing_activity(self, event):
        if self.jarvis.role != 'locked' and not self.jarvis.voice_mode:
            kind=getattr(event.widget,'winfo_class',lambda:'')()
            if kind in {'Text','Entry','TEntry','TCombobox'}:
                self._last_user_input = time.monotonic()

    def _dismiss_session_dialogs(self):
        for child in list(self.root.winfo_children()):
            if isinstance(child,tk.Toplevel):child.destroy()
        self._voice_enrollment_window=None

    def _user_activity(self):
        self._last_user_input = time.monotonic()

    def _idle_watch(self):
        if self.closed:
            return
        self._check_session_idle(time.monotonic())
        self.root.after(250, self._idle_watch)

    def _update_idle_work(self, now):
        """Arm the full idle window only after the last operation has ended."""
        host=self.jarvis
        engine=host._tts_engine
        discord=getattr(host,'_discord',None)
        discord_busy=bool(discord and discord.is_busy)
        if discord:
            self._last_user_input=max(self._last_user_input,discord.last_work_at)
        working=(getattr(self,'_pending_work',0)>0
                 or bool(engine and engine.is_busy)
                 or discord_busy
                 or (getattr(host,'agent_task_active',False)
                     and not getattr(host,'tool_approval_pending',False)))
        if working:
            self._idle_work_active=True
            return True
        if self._idle_work_active:
            self._last_user_input=now
            self._idle_work_active=False
        return False

    def _check_session_idle(self, now):
        if self.jarvis.role == 'locked' or not self.jarvis.session_user:
            return
        # Passive microphone capture is not a running task. All queued operations,
        # model/tool work, Discord requests and speech must finish before counting.
        if self._update_idle_work(now):
            return
        timeout=inactivity_timeout(getattr(self.jarvis,'_cfg',{}),voice=self.jarvis.voice_mode)
        if timeout is None:
            return
        if self.jarvis.voice_mode:
            voice = self.jarvis._voice_input
            last_voice = getattr(voice, 'last_voice_at', 0) if voice else 0
            if not isinstance(last_voice, (int, float)):
                last_voice = 0
            last = max(self._last_user_input, last_voice)
            if not self.jarvis.voice_sleeping and now-last >= timeout:
                if getattr(self.jarvis,'tool_approval_pending',False):
                    self._decide_tool(False)
                self._handle_voice_text('dormi')
            return
        if now-self._last_user_input >= timeout:
            self._suspend_text_session()

    def _suspend_text_session(self):
        self.jarvis.lock_session()
        if self.jarvis._voice_input:
            self.jarvis._voice_input.stop()
            self.jarvis._voice_input=None
        self._interrupt_speech()
        self._hide_floating_core()
        self._dismiss_session_dialogs()
        self.approval_frame.pack_forget()
        self.show_login()
        self.status.set('Sessione sospesa per inattività. Inserisci la password per continuare.')

    def _security_page(self):
        self.title('Sicurezza','Scegli quando sospendere la sessione. Il conto parte dopo la fine di tutti i lavori di JARVIS.')
        cfg=self.jarvis._cfg
        controls=[]
        for prefix,title,label,description in (
            ('session_lock','Password della sessione','Blocco automatico per inattività',
             'In modalità scrittura sospende la sessione e richiede di nuovo la password. Puoi disattivarlo per usare JARVIS come server.'),
            ('voice_standby','Modalità vocale','Standby vocale automatico',
             'Dopo un periodo di silenzio attende il richiamo «hey JARVIS». Puoi scegliere un tempo diverso dal blocco della scrittura.')):
            body=self._settings_section(title,description)
            enabled=tk.BooleanVar(value=cfg.get(prefix+'_enabled',True) is not False)
            try:initial=validate_idle_minutes(cfg.get(prefix+'_minutes',2))
            except ValueError:initial=2
            minutes=tk.StringVar(value=str(initial))
            toggle=ttk.Checkbutton(body,text=label,variable=enabled,style='Card.TCheckbutton')
            toggle.pack(anchor='w',pady=(0,12))
            row=ttk.Frame(body,style='Card.TFrame');row.pack(fill='x')
            ttk.Label(row,text='Tempo di inattività · minuti',style='Card.TLabel').pack(side='left',padx=(0,12))
            duration=ttk.Spinbox(row,textvariable=minutes,from_=1,to=1440,width=7)
            duration.pack(side='left')
            def state(*_,enabled=enabled,duration=duration):
                duration.configure(state='normal' if enabled.get() else 'disabled')
            enabled.trace_add('write',state);state()
            controls.append((enabled,minutes,prefix,initial))
        ttk.Label(self.content,text='Le modifiche sono riservate all’amministratore e valgono per tutte le sessioni di questo JARVIS.',
                  style='Hint.TLabel',wraplength=620).pack(fill='x',pady=(0,12))
        feedback=tk.StringVar()
        ttk.Label(self.content,textvariable=feedback,wraplength=620).pack(fill='x',pady=(0,10))
        def save():
            values=[]
            for flag,minutes,prefix,initial in controls:
                try:value=validate_idle_minutes(minutes.get())
                except ValueError as exc:
                    if flag.get():feedback.set(str(exc));return
                    value=initial
                values.append((flag.get(),value))
            lock,voice=values
            def saved(_):
                feedback.set('Impostazioni salvate e già attive.')
                self._saved()
            self.submit(lambda:self.jarvis.set_session_security(lock_enabled=lock[0],lock_minutes=lock[1],
                voice_enabled=voice[0],voice_minutes=voice[1]),saved)
        ttk.Button(self.content,text='Salva sicurezza',style='Primary.TButton',command=save).pack(anchor='w',pady=(0,16))

    def _logout_session(self):
        self._leave_voice()
        self._interrupt_speech()
        self.jarvis.logout()
        self._hide_floating_core()
        self._dismiss_session_dialogs()
        self._view_chat_id = None
        self._reset_chat_view()
        self.show_login()
        self.status.set('Logout effettuato · puoi accedere con un altro account.')
