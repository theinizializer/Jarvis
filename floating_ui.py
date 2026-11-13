"""Detachable cores and a compact composer sharing the current JARVIS conversation."""
import time
import os
import tkinter as tk
from tkinter import ttk
from desktop_visibility import DesktopVisibility
from jarvis_hud import HudCore, OrbitalCore
from satellite_client import SatelliteClient


class FloatingUIMixin:
    def _init_floating_ui(self):
        self._floating_core = None
        self._satellite = None
        self._floating_saved_draft = None
        self._desktop_visibility = DesktopVisibility()
        self.root.bind('<FocusOut>', lambda _: self.root.after(180, self._sync_floating_core), add='+')
        self.root.bind('<FocusIn>', lambda _: self.root.after(80, self._sync_floating_core), add='+')
        self.root.after(250, self._floating_watch)

    def _floating_watch(self):
        if self.closed:
            return
        if self._satellite:
            while not self._satellite.events.empty():
                event=self._satellite.events.get()
                action=event.get('type')
                if action=='compose':
                    if self.jarvis.role=='locked':self._restore_main_window()
                    else:self._toggle_floating_chat(True)
                elif action=='open':self._restore_main_window()
                elif action=='stop':self._interrupt_speech()
                elif action=='standby':self._toggle_voice_standby()
        self._sync_floating_core()
        self.root.after(50, self._floating_watch)

    def _satellite_state(self, visible):
        if visible and self._satellite is None:
            self._satellite=SatelliteClient()
            self._desktop_visibility.satellite_pid=self._satellite.pid
        if self._satellite is None:return
        now=time.monotonic()
        state='approval' if self.jarvis.role=='locked' else self._hud_state(now)
        voice,engine=self.jarvis._voice_input,self.jarvis._tts_engine
        level=(voice.capture_level if voice and state=='listening' else
               engine.playback_level if engine and state=='speaking' else 0)
        cfg=self.jarvis._cfg
        self._satellite.update(visible=visible,mode='voice' if self.jarvis.voice_mode and self.jarvis.role!='locked' else 'text',
            state=state,level=round(level,2),theme=[cfg.get('hud_theme'),cfg.get('hud_color'),cfg.get('hud_glow',55),self.bg])

    def _sync_floating_core(self):
        if self.closed:
            return
        if self.jarvis.role == 'locked':
            away=self.root.focus_displayof() is None and self._desktop_visibility.active_pid!=os.getpid()
            self._satellite_state(bool(self.jarvis._reauth_user and away and self._desktop_visibility.fullscreen is False))
            return
        if self._desktop_visibility.fullscreen is not False:
            self._withdraw_floating_core()
            return
        focused = self.root.focus_displayof()
        top = focused.winfo_toplevel() if focused else None
        if (not (self._floating_core and self._floating_core._expanded) and self.page=='Conversazione'
                and self._desktop_visibility.active_pid==os.getpid()):
            self._withdraw_floating_core()
            return
        if self._floating_core and top is self._floating_core:
            self._floating_core._focus_deadline = 0
        if (self._floating_core and self._floating_core._expanded and top is not self._floating_core
                and time.monotonic() < getattr(self._floating_core, '_focus_deadline', 0)):
            return
        if self._floating_core and self._floating_core._expanded and top is not self._floating_core:
            self._toggle_floating_chat(False)
        if top and top is not self._floating_core:
            if top is not self.root or self.page == 'Conversazione':
                self._withdraw_floating_core()
                return
        mode = 'voice' if self.jarvis.voice_mode else 'text'
        if self._floating_core and self._floating_mode != mode:
            self._hide_floating_core()
        if self._floating_core is None:
            self._create_floating_core(mode)
        if self._floating_core._expanded:
            self._satellite_state(False)
        else:
            self._floating_core.withdraw()
            self._satellite_state(True)

    def _position_floating_core(self, expanded=False):
        width, height = (390, 540) if expanded else (216, 244)
        self._floating_core.geometry(f'{width}x{height}+{max(0,self.root.winfo_screenwidth()-width-24)}'
                                     f'+{max(0,self.root.winfo_screenheight()-height-58)}')

    def _create_floating_core(self, mode):
        self._floating_mode = mode
        window = self._floating_core = tk.Toplevel(self.root)
        window.withdraw()
        window.title('JARVIS · ' + ('voce' if mode == 'voice' else 'chat rapida'))
        # Keep the composer managed: unmanaged XWayland windows can lose keyboard input.
        window.overrideredirect(False)
        # Ordinary utility windows remain below compositor fullscreen windows.
        window.attributes('-type', 'utility')
        window.attributes('-topmost', True)
        window.configure(bg=self.bg, highlightbackground=self.hud.blue, highlightthickness=1)
        window._expanded = False
        self._position_floating_core()
        canvas = tk.Canvas(window, bg=self.bg, width=214, height=178, highlightthickness=0,
                           cursor='hand2')
        canvas.pack(fill='x')
        window._canvas = canvas
        core = (OrbitalCore if mode == 'voice' else HudCore)(canvas)
        window._core = core
        window._theme = None
        canvas.bind('<Configure>', lambda event: core.resize(event.width, event.height))
        if mode == 'text':
            canvas.bind('<Button-1>', lambda _: self._toggle_floating_chat())
        else:
            canvas.bind('<Button-1>', lambda _: self._toggle_voice_standby())
        self._floating_chat = ttk.Frame(window, padding=(10, 0, 10, 4))
        self._floating_transcript = tk.Text(self._floating_chat, height=8, wrap='word', state='disabled',
                    bg=self.panel, fg='#ECF1F7', font=('Noto Sans', 10), borderwidth=0,
                    highlightthickness=0, padx=10, pady=10)
        self._floating_transcript.pack(fill='both', expand=True)
        self._floating_entry = tk.Text(self._floating_chat, height=2, wrap='word', bg=self.surface,
                    fg='#ECF1F7', insertbackground=self.hud.accent, font=('Noto Sans', 11), padx=8, pady=8,
                    highlightthickness=1, highlightbackground=self.hud.blue, relief='flat')
        self._floating_entry.pack(fill='x', pady=(8, 4))
        if mode=='text' and self._floating_saved_draft and self._floating_saved_draft[0]==self.jarvis.session_user:
            self._floating_entry.insert('1.0',self._floating_saved_draft[1])
        self._floating_entry.bind('<Return>', self._send_floating_message)
        self._floating_entry.bind('<Shift-Return>', lambda _: None)
        self._floating_send = ttk.Button(self._floating_chat, text='Invia', style='Primary.TButton',
                                        command=self._send_floating_message)
        self._floating_send.pack(anchor='e')
        self._floating_hint = tk.StringVar(value='Clicca il nucleo per scrivere' if mode == 'text' else 'Conversazione vocale')
        ttk.Label(window, textvariable=self._floating_hint, anchor='center', font=('Noto Sans', 9)).pack(fill='x')
        controls = ttk.Frame(window, padding=(8, 4, 8, 8))
        controls.pack(side='bottom', fill='x')
        ttk.Button(controls, text='Apri JARVIS', command=self._restore_main_window).pack(side='left', expand=True)
        self._floating_stop = ttk.Button(controls, text='Zittisci', command=self._interrupt_speech)
        self._floating_stop.pack(side='right', expand=True)
        window.bind('<Escape>', lambda _: self._toggle_floating_chat(False))
        def animate():
            if self.closed or self._floating_core is not window:
                return
            started = time.monotonic()
            if window.winfo_viewable():
                if self._desktop_visibility.fullscreen is not False:
                    window.withdraw()
                else:
                    cfg = self.jarvis._cfg
                    theme = (cfg.get('hud_theme'), cfg.get('hud_color'), cfg.get('hud_glow', 55), self.bg)
                    if theme != window._theme:
                        core.set_theme(*theme)
                        window._theme = theme
                    voice = self.jarvis._voice_input
                    engine = self.jarvis._tts_engine
                    state = self._hud_state(started)
                    level = (voice.capture_level if voice and state == 'listening' else
                             engine.playback_level if engine and state == 'speaking' else 0)
                    core.tick(started, state, cfg.get('motion', 'full'), level)
                    if mode == 'voice':
                        self._floating_hint.set('Standby · clicca per risvegliare' if self.jarvis.voice_sleeping
                                                else 'Clicca il nucleo per dormire')
                    self._update_floating_chat()
            cost = (time.monotonic()-started)*1000
            self.root.after(max(4, round(20-cost)) if window.winfo_viewable() else 150, animate)
        animate()

    def _toggle_floating_chat(self, expanded=None):
        window = self._floating_core
        if not window or self._floating_mode != 'text':
            return
        expanded = not window._expanded if expanded is None else expanded
        window._expanded = expanded
        window._canvas.configure(height=138 if expanded else 178)
        self._position_floating_core(expanded)
        if expanded:
            self._satellite_state(False)
            window.deiconify()
            window.lift()
            window._focus_deadline = time.monotonic()+.4
            self._floating_chat.pack(fill='both', expand=True, after=window.winfo_children()[0])
            self._floating_hint.set('Chat corrente · Invio per inviare')
            def focus_composer():
                if self._floating_core is not window or not window._expanded:
                    return
                if window.winfo_viewable():
                    self._floating_entry.focus_force()
                elif time.monotonic() < window._focus_deadline:
                    self.root.after(25, focus_composer)
            self.root.after_idle(focus_composer)
            self._update_floating_chat()
        else:
            self._floating_chat.pack_forget()
            self._floating_hint.set('Clicca il nucleo per scrivere')
            window.withdraw()

    def _update_floating_chat(self):
        engine = self.jarvis._tts_engine
        self._floating_stop.state(['!disabled'] if engine and engine.is_busy else ['disabled'])
        if not self._floating_core._expanded:
            return
        self._floating_send.state(['disabled'] if self.busy or self.jarvis.tool_approval_pending else ['!disabled'])
        text = self.transcript.get('1.0', 'end-1c')[-12000:]
        if text != getattr(self, '_floating_last_text', None):
            self._floating_last_text = text
            self._floating_transcript.configure(state='normal')
            self._floating_transcript.delete('1.0', 'end')
            self._floating_transcript.insert('1.0', text)
            self._floating_transcript.configure(state='disabled')
            self._floating_transcript.see('end')
        if self.busy:
            self._floating_hint.set('JARVIS sta rispondendo…')
        elif self.jarvis.tool_approval_pending:
            self._floating_hint.set('Apri JARVIS per autorizzare l’operazione')
        else:
            self._floating_hint.set('Chat corrente · Invio per inviare')

    def _send_floating_message(self, _=None):
        text = self._floating_entry.get('1.0', 'end-1c').strip()
        if text and not self.busy and not self.jarvis.tool_approval_pending:
            self.send(text)
            self._floating_entry.delete('1.0', 'end')
        return 'break'

    def _restore_main_window(self):
        self.root.deiconify()
        self.show('Conversazione')
        self.root.lift()
        self.root.focus_force()
        self._withdraw_floating_core()

    def _withdraw_floating_core(self):
        self._satellite_state(False)
        if self._floating_core and self._floating_core.winfo_exists():
            if self._floating_core._expanded:
                self._toggle_floating_chat(False)
            self._floating_core.withdraw()

    def _hide_floating_core(self):
        self._satellite_state(False)
        if self._floating_core and self._floating_core.winfo_exists():
            if self._floating_mode=='text' and self.jarvis.session_user:
                self._floating_saved_draft=(self.jarvis.session_user,self._floating_entry.get('1.0','end-1c'))
            self._floating_core.destroy()
        if not self.jarvis.session_user:
            self._floating_saved_draft=None
        self._floating_core = None
        self._floating_last_text = None
