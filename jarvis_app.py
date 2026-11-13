"""Native JARVIS window; Tk is loaded only for desktop mode."""
from __future__ import annotations
import colorsys
import json
import math
import os
import re
import traceback
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path
from app_config import RESPONSE_TONES, SERVICE_FIELDS
from jarvis_hud import AMBER, BG, CYAN as ACCENT, HUD_THEMES, HudCore, LINE, MUTED, mix

PANEL, INK, WHITE = '#0B1624', '#ECF1F7', '#0D1927'

class ChatInput(tk.Text):
    """Multiline composer retaining the small Entry interface used by the UI."""
    def get(self, first='1.0', last='end-1c'):
        return super().get('1.0' if first == 0 else first, last)
    def delete(self, first, last=None):
        return super().delete('1.0' if first == 0 else first, last)
    def insert(self, index, text, *tags):
        return super().insert('1.0' if index == 0 else index, text, *tags)

class PrismWheel(tk.Canvas):
    """Hue and saturation picker. The separate brightness slider sets HSV value."""

    def __init__(self, parent, on_pick):
        self.radius = 63
        size = self.radius * 2 + 1
        super().__init__(parent, width=size, height=size, bg=PANEL, takefocus=True,
                         highlightbackground=LINE, highlightcolor=ACCENT, highlightthickness=2)
        self.on_pick = on_pick
        self.image = tk.PhotoImage(master=self, width=size, height=size)
        for y in range(-self.radius, self.radius + 1):
            limit = math.isqrt(self.radius ** 2 - y ** 2)
            row = [self.hex_color(math.atan2(-y, x) / math.tau % 1,
                                  math.hypot(x, y) / self.radius, 1)
                   for x in range(-limit, limit + 1)]
            self.image.put('{' + ' '.join(row) + '}', to=(self.radius - limit, self.radius + y))
        self.create_image(2, 2, image=self.image, anchor='nw')
        self.marker_border = self.create_oval(0, 0, 0, 0, outline=BG, width=4)
        self.marker = self.create_oval(0, 0, 0, 0, outline='#FFFFFF', width=2)
        self.bind('<Button-1>', self._pick_pointer)
        self.bind('<B1-Motion>', self._pick_pointer)
        self.bind('<KeyPress>', self._pick_key)

    @staticmethod
    def hex_color(hue, saturation, value):
        rgb = colorsys.hsv_to_rgb(hue, saturation, value)
        return '#%02X%02X%02X' % tuple(round(channel * 255) for channel in rgb)

    def show_position(self, hue, saturation):
        angle = hue * math.tau
        self.x = math.cos(angle) * saturation * self.radius
        self.y = -math.sin(angle) * saturation * self.radius
        x, y = self.radius + 2 + self.x, self.radius + 2 + self.y
        for item in (self.marker_border, self.marker):
            self.coords(item, x - 5, y - 5, x + 5, y + 5)

    def _pick(self, x, y):
        hue = math.atan2(-y, x) / math.tau % 1
        saturation = min(1, math.hypot(x, y) / self.radius)
        self.show_position(hue, saturation)
        self.on_pick(hue, saturation)
        return 'break'

    def _pick_pointer(self, event):
        self.focus_set()
        return self._pick(event.x - self.radius - 2, event.y - self.radius - 2)

    def _pick_key(self, event):
        movement = {'Left': (-4, 0), 'Right': (4, 0), 'Up': (0, -4), 'Down': (0, 4)}
        if event.keysym in movement:
            dx, dy = movement[event.keysym]
            return self._pick(self.x + dx, self.y + dy)


from voice_ui import VoiceUIMixin
from session_idle import SessionIdleMixin
from discord_ui import DiscordUIMixin
from vision_ui import VisionUIMixin


class JarvisApp(SessionIdleMixin, VoiceUIMixin, VisionUIMixin, DiscordUIMixin):
    def __init__(self, jarvis):
        self.jarvis = jarvis
        self.bg, self.panel, self.surface = self._background_palette()
        self.root = tk.Tk(className='jarvis')
        self.root.title(f'JARVIS {jarvis.version} — Assistente personale')
        self.root.geometry('1240x820')
        self.root.minsize(850, 620)
        self.root.configure(bg=self.bg)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Control-s>', lambda _: self.show('Servizi'))
        self.root.bind('<Control-l>', lambda _: self.show('Conversazione'))
        self.root.bind('<Control-n>', lambda _: self._new_chat())
        self.root.bind('<Escape>', self._close_settings)
        self.root.bind('<MouseWheel>', self._scroll_settings)
        self.root.bind('<Button-4>', self._scroll_settings)
        self.root.bind('<Button-5>', self._scroll_settings)
        self.jobs, self.results = queue.Queue(), queue.Queue()
        self.closed = False
        self.busy = 0
        self._pending_work = 0
        self._view_chat_id=None
        self.page = ''
        self._hud_error_until = 0
        self._login_failures = 0
        self._login_retry_at = 0
        self._style()
        self._icon()
        self._layout()
        self._init_voice_ui()
        self._init_session_idle()
        self._apply_hud_theme()
        self.root.report_callback_exception = self._callback_error
        threading.Thread(target=self._worker, daemon=True, name='jarvis-worker').start()
        if self.jarvis.role == 'locked':
            self.show_login()
        else:
            self.show('Conversazione')
        self._refresh_chats()
        self.root.after(80, self._poll)
        self.root.after(40, self._animate_hud)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Noto Sans', 11), background=self.bg, foreground=INK)
        style.configure('TFrame', background=self.bg)
        style.configure('Card.TFrame', background=self.panel)
        style.configure('TLabel', background=self.bg, foreground=INK)
        style.configure('Card.TLabel', background=self.panel, foreground=INK)
        style.configure('Title.TLabel', font=('Noto Sans', 22, 'bold'), foreground=INK)
        style.configure('Hint.TLabel', foreground=MUTED, font=('Noto Sans', 10))
        style.configure('TButton', padding=(16, 10), background=self.bg, foreground=INK,
                        borderwidth=0, relief='flat', focusthickness=1, focuscolor=ACCENT)
        style.map('TButton', foreground=[('disabled', MUTED)],
                  background=[('disabled', self.bg), ('!disabled', self.bg)])
        style.configure('Settings.TButton', anchor='w', padding=(8, 5), background=self.panel)
        style.map('Settings.TButton', foreground=[('selected', INK), ('!selected', MUTED)],
                  background=[('disabled', self.panel), ('!disabled', self.panel)])
        style.configure('Archive.TButton', padding=(6, 7), font=('Noto Sans', 9))
        style.configure('Compact.TButton',padding=(10,8))
        style.map('Compact.TButton', foreground=[('disabled', MUTED), ('selected', ACCENT)])
        style.configure('Primary.TButton', background=self.bg, foreground=BG)
        style.map('Primary.TButton', foreground=[('disabled', MUTED), ('active', BG)])
        style.configure('TEntry', padding=11, fieldbackground=self.surface, foreground=INK,
                        insertcolor=INK, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, borderwidth=1)
        style.map('TEntry', bordercolor=[('focus', ACCENT)])
        style.configure('TSpinbox',padding=(10,6),fieldbackground=self.surface,foreground=INK,
                        background=self.panel,arrowcolor=MUTED,insertcolor=INK,
                        bordercolor=LINE,lightcolor=LINE,darkcolor=LINE,borderwidth=1)
        style.map('TSpinbox',fieldbackground=[('disabled',self.panel)],foreground=[('disabled',MUTED)],
                  bordercolor=[('focus',ACCENT)])
        style.configure('TCombobox', padding=9, fieldbackground=self.surface, foreground=INK,
                        arrowcolor=MUTED, background=self.panel, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, borderwidth=1)
        style.map('TCombobox', fieldbackground=[('readonly', self.surface)], foreground=[('readonly', INK)])
        for name in ('TCheckbutton', 'TRadiobutton'):
            style.configure(name, background=self.bg, foreground=INK, indicatorbackground=self.surface)
            style.map(name, background=[('active', self.bg)], indicatorbackground=[('selected', ACCENT)])
        style.configure('Prism.Horizontal.TScale', background=ACCENT, troughcolor=self.surface,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, sliderlength=18)
        style.configure('Card.TRadiobutton', background=self.panel)
        style.map('Card.TRadiobutton', background=[('active', self.panel)])
        style.configure('Card.TCheckbutton', background=self.panel, foreground=INK, indicatorbackground=self.surface)
        style.map('Card.TCheckbutton', background=[('active', self.panel)], indicatorbackground=[('selected', ACCENT)])
        style.configure('Treeview', rowheight=38, fieldbackground=self.panel, background=self.panel, foreground=INK, borderwidth=0)
        style.configure('Treeview.Heading', font=('Noto Sans', 10, 'bold'), background=self.surface, foreground=MUTED, relief='flat')
        style.map('Treeview', background=[('selected', '#243C48')], foreground=[('selected', INK)])
        style.configure('Vertical.TScrollbar', background=LINE, troughcolor=self.bg, arrowcolor=MUTED, borderwidth=0, arrowsize=10)
        style.configure('TScrollbar', background=LINE, troughcolor=self.bg, bordercolor=self.bg,
                        lightcolor=self.bg, darkcolor=self.bg, arrowcolor=MUTED, relief='flat')
        style.map('TScrollbar', background=[('disabled',self.bg),('active','#415068'),('!active',LINE)],
                  arrowcolor=[('disabled',self.bg),('!disabled',MUTED)])
        self.root.option_add('*TCombobox*Listbox.background', self.surface)
        self.root.option_add('*TCombobox*Listbox.foreground', INK)
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#243C48')


    def _icon(self):
        generated=Path(__file__).with_name('assets')/'jarvis-icon-v2.png'
        if generated.is_file():
            image=tk.PhotoImage(master=self.root,file=str(generated))
            self.icon=image.subsample(max(1,image.width()//128))
            self.root.iconphoto(True,self.icon)
            return
        # Code-native raster of the same triangle/J mark as the installed SVG.
        icon = tk.PhotoImage(width=64, height=64)
        pixels=[[self.bg]*64 for _ in range(64)]
        def stroke(points,width=1):
            for (x1,y1),(x2,y2) in zip(points,points[1:]):
                count=max(1,int(math.hypot(x2-x1,y2-y1)*2))
                for step in range(count+1):
                    x=round(x1+(x2-x1)*step/count);y=round(y1+(y2-y1)*step/count)
                    for dy in range(-width,width+1):
                        for dx in range(-width,width+1):
                            if 0<=x+dx<64 and 0<=y+dy<64:pixels[y+dy][x+dx]=ACCENT
        stroke([(32+25*math.cos(a*math.pi/180),32+25*math.sin(a*math.pi/180)) for a in range(361)],0)
        stroke([(32,18.5),(44,39.5),(20,39.5),(32,18.5)],0)
        stroke([(29,27),(35,27),(35,33.5),(34.5,36.5),(31.5,38),(28,36.5),(28,34)],0)
        icon.put(' '.join('{'+' '.join(row)+'}' for row in pixels))
        self.icon = icon
        self.root.iconphoto(True, icon)

    def _layout(self):
        sidebar = tk.Frame(self.root, bg=self.surface, width=210)
        sidebar.pack(side='left', fill='y'); sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=self.surface); brand.pack(fill='x', padx=22, pady=(22, 18))
        tk.Label(brand, text='JARVIS', bg=self.surface, fg=INK, font=('Noto Sans', 18, 'bold')).pack(anchor='w')
        tk.Label(brand, text=f'Archivio locale  /  {self.jarvis.version}', bg=self.surface, fg=MUTED,
                 font=('Noto Sans', 9)).pack(anchor='w', pady=(4, 0))
        self.new_chat_button = ttk.Button(sidebar, text='+  Nuova chat', command=self._new_chat)
        self.new_chat_button.pack(fill='x', padx=16, pady=(0, 14))
        self.nav = {}
        tk.Label(sidebar, text='Chat salvate', bg=self.surface, fg=MUTED, font=('Noto Sans',9)).pack(anchor='w', padx=28, pady=(18,8))
        self.chat_search=tk.StringVar();self._chat_search_after=None
        self.chat_search_entry=ttk.Entry(sidebar,textvariable=self.chat_search,width=18)
        self.chat_search_entry.pack(fill='x',padx=16,pady=(0,8))
        self.chat_search_entry.bind('<Escape>',lambda _:(self.chat_search.set(''),'break')[1])
        self.root.bind('<Control-f>', self._open_chat_search)
        self.chat_search.trace_add('write',lambda *_:self._queue_chat_search())
        tk.Label(sidebar,text='Cerca nel titolo e nei messaggi',bg=self.surface,fg=MUTED,font=('Noto Sans',8)).pack(anchor='w',padx=18,pady=(0,8))
        actions=tk.Frame(sidebar,bg=self.surface);actions.pack(fill='x',padx=16,pady=(0,6))
        def chat_action(text, command):
            return ttk.Button(actions, text=text, command=command, style='Archive.TButton', state='disabled')
        self.pin_chat_button=chat_action('☆ Fissa',self._pin_chat);self.pin_chat_button.pack(side='left',fill='x',expand=True,padx=(0,4))
        self.delete_chat_button=chat_action('Elimina',self._delete_chat);self.delete_chat_button.pack(side='left',fill='x',expand=True)
        self.chat_ids=[]
        self.chat_list=tk.Listbox(sidebar,bg=self.surface,fg=MUTED,selectbackground=self.panel,selectforeground=INK,
            relief='flat',highlightthickness=0,bd=0,font=('Noto Sans',10),height=3,activestyle='none',exportselection=False)
        self.chat_list.pack(fill='both',expand=True,padx=20,pady=(0,12))
        self.chat_list.bind('<<ListboxSelect>>',self._select_chat)
        bottom = tk.Frame(sidebar, bg=self.surface); bottom.pack(side='bottom', fill='x', padx=16, pady=12,before=self.chat_list)
        self.undo_chat_button = ttk.Button(bottom, text='Annulla eliminazione', command=self._undo_delete_chat,
                                           style='Archive.TButton')
        self.session_text = tk.StringVar(value='Sessione bloccata' if self.jarvis.role=='locked' else 'Sessione locale')
        tk.Label(bottom, textvariable=self.session_text, bg=self.surface, fg=MUTED, font=('Noto Sans',9), anchor='w').pack(fill='x', padx=12, pady=(12,0))
        self.sidebar = sidebar
        self.sidebar.pack_forget()
        self.outer = outer = ttk.Frame(self.root)
        outer.pack(side='left', fill='both', expand=True)
        toolbar = ttk.Frame(outer, padding=(24, 8))
        toolbar.pack(fill='x')
        self.archive_button = ttk.Button(toolbar, text='Archivio', style='Compact.TButton',
                                         command=self._toggle_archive)
        self.archive_button.pack(side='left')
        ttk.Button(toolbar, text='Logout', style='Compact.TButton', command=self._logout_session).pack(side='left', padx=6)
        self.toolbar_nav = {}
        for title, page in (('Impostazioni', 'Servizi'), ('Posta', 'Email'), ('Conversazione', 'Conversazione')):
            button = ttk.Button(toolbar, text=title, style='Compact.TButton',
                                command=lambda target=page: self.show(target))
            button.pack(side='right', padx=(4, 0))
            self.toolbar_nav[page] = button
        self.status = tk.StringVar(value='Inserisci il PIN' if self.jarvis.role=='locked' else 'Pronto')
        footer = tk.Frame(outer,bg=self.bg);footer.pack(side='bottom',fill='x')
        tk.Label(footer,textvariable=self.status,bg=self.bg,fg=MUTED,font=('Noto Sans',9),anchor='w',padx=30,pady=8,wraplength=900).pack(fill='x')
        self.body=ttk.Frame(outer,padding=(24,8));self.body.pack(fill='both',expand=True)
        self.chat_frame=ttk.Frame(self.body)
        self.settings_nav = ttk.Frame(self.body, style='Card.TFrame', padding=(10, 14))
        ttk.Label(self.settings_nav, text='Impostazioni', style='Card.TLabel',
                  font=('Noto Sans', 14, 'bold')).pack(anchor='w', padx=8, pady=(0, 10))
        for group, items in (
            ('Preferenze', (('Aspetto', 'Aspetto'), ('Profili', 'Personalizzazione'), ('Voce', 'Voce'))),
            ('Connessioni', (('Servizi', 'Modelli e servizi'), ('Discord','Discord'), ('MCP', 'Server MCP'), ('Visione', 'Visione e browser'), ('Skill', 'Competenze'))),
            ('Sistema', (('Sicurezza','Sicurezza'),('Comandi', 'Comandi'), ('Manutenzione', 'Manutenzione'))),
        ):
            ttk.Label(self.settings_nav, text=group, style='Card.TLabel',
                      font=('Noto Sans', 9, 'bold')).pack(anchor='w', padx=8, pady=(8, 2))
            for page, label in items:
                button = ttk.Button(self.settings_nav, text=label, style='Settings.TButton',
                                    command=lambda target=page: self.show(target))
                button.pack(fill='x')
                self.nav[page] = button
        ttk.Button(self.settings_nav, text='← Torna alla chat', style='Settings.TButton',
                   command=lambda: self.show('Conversazione')).pack(fill='x', pady=(12, 0))
        self._chat()


    def _close_settings(self, _):
        if self.page in self.nav:
            self.show('Conversazione')
            return 'break'

    def _toggle_archive(self):
        if self.jarvis.role == 'locked':
            return
        if self.sidebar.winfo_manager():
            self.sidebar.pack_forget()
            self.archive_button.configure(text='Archivio')
        else:
            self.sidebar.pack(side='left', fill='y', before=self.outer)
            self.archive_button.configure(text='Chiudi archivio')

    def _open_chat_search(self, _):
        if self.jarvis.role != 'locked':
            if not self.sidebar.winfo_manager():
                self._toggle_archive()
            self.chat_search_entry.focus_set()
        return 'break'

    def _callback_error(self, exc_type, exc, tb):
        from app_config import data_dir
        try:
            path = data_dir() / 'desktop.log'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                traceback.print_exception(exc_type, exc, tb, file=stream)
        finally:
            self.status.set('Errore interfaccia: ' + str(exc))

    def _clear_page(self):
        for child in self.body.winfo_children():
            child.pack_forget()
            if child not in (self.chat_frame, self.settings_nav):
                child.destroy()

    def show_login(self):
        self.page = 'Accesso'
        self.sidebar.pack_forget()
        self.archive_button.configure(text='Archivio', state='disabled')
        for button in self.toolbar_nav.values():
            button.state(['disabled'])
        self._clear_page()
        for button in self.nav.values():
            button.state(['disabled'])
        self.new_chat_button.configure(state='disabled')
        frame = ttk.Frame(self.body)
        frame.pack(fill='both', expand=True)
        core = tk.Canvas(frame, height=160, bg=self.bg, highlightthickness=0)
        core.pack(fill='x', pady=(12, 18))
        core.bind('<Configure>', lambda e: self._draw_core(core, e.width, e.height, locked=True))
        card = ttk.Frame(frame)
        card.pack(anchor='center', fill='x', padx=55)
        suspended = bool(self.jarvis._reauth_user)
        name = self.jarvis.registry.display_of(self.jarvis._reauth_user) if suspended else ''
        ttk.Label(card, text=f'Bentornato, {name}' if suspended else 'Accesso a JARVIS', style='Title.TLabel').pack(anchor='w')
        ttk.Label(card, text='Inserisci la password di questa sessione. Per cambiare account, fai logout.' if suspended
                  else 'Inserisci la password per accedere a chat e impostazioni.',
                  style='Hint.TLabel', wraplength=600).pack(anchor='w', pady=(10, 22))
        self.pin_value = tk.StringVar()
        self.pin_entry = ttk.Entry(card, textvariable=self.pin_value, show='●', width=30)
        self.pin_entry.pack(anchor='w', fill='x')
        self.pin_entry.bind('<Return>', lambda _: self._unlock())
        self.login_error = tk.StringVar()
        tk.Label(card, textvariable=self.login_error, bg=self.bg, fg=AMBER,
                 font=('Noto Sans', 11)).pack(anchor='w', pady=10)
        self.unlock_button = ttk.Button(card, text='Sblocca JARVIS', style='Primary.TButton', command=self._unlock)
        self.unlock_button.pack(anchor='w', pady=(0, 12))
        if suspended:
            ttk.Button(card, text='Logout e cambia account', command=self._logout_session).pack(anchor='w')
        self.root.after_idle(self.pin_entry.focus_set)

    def _unlock(self):
        if self.busy:
            self.login_error.set('Chiusura dell’operazione precedente in corso. Riprova fra poco.')
            return
        if time.monotonic() < self._login_retry_at:
            self.login_error.set('Troppi tentativi. Attendi 30 secondi prima di riprovare.')
            return
        pin = self.pin_value.get()
        self.pin_value.set('')
        if not pin:
            self.login_error.set('Inserisci il PIN personale.')
            self.pin_entry.focus_set()
            return
        if not self.jarvis.authenticate(pin):
            self._login_failures += 1
            if self._login_failures >= 3:
                self._login_failures = 0
                self._login_retry_at = time.monotonic() + 30
                self.login_error.set('Troppi tentativi. Attendi 30 secondi prima di riprovare.')
            else:
                self.login_error.set('PIN errato. Riprova.')
            self.pin_entry.focus_set()
            return
        self._login_failures=0
        self._login_retry_at=0
        for button in self.nav.values():
            button.state(['!disabled'])
        self.new_chat_button.configure(state='normal')
        self.archive_button.configure(state='normal')
        for page, button in self.toolbar_nav.items():
            button.state(['!disabled'] if page == 'Conversazione' or self.jarvis.role == 'admin' else ['disabled'])
        self.session_text.set('Amministratore' if self.jarvis.role == 'admin' else 'Utente secondario')
        self.status.set('Accesso effettuato. Pronto.')
        self._user_activity()
        self.show('Conversazione')
        self._refresh_chats()

    def title(self, text, hint=''):
        ttk.Label(self.content, text=text, style='Title.TLabel').pack(anchor='w', pady=(0, 8))
        if hint:
            label=ttk.Label(self.content, text=hint, style='Hint.TLabel', wraplength=620)
            label.pack(fill='x', pady=(0, 20))
            label.bind('<Configure>', lambda e:label.configure(wraplength=max(150,e.width)))

    def show(self, page):
        if page != 'Conversazione' and (self.jarvis.agent_task_active or self.jarvis.voice_mode):
            self.status.set('Completa o annulla il compito attivo prima di cambiare impostazioni.')
            return
        if self.jarvis.role == 'locked':
            self.status.set('Sessione bloccata: inserisci il PIN per accedere.')
            if self.page != 'Accesso':
                self.show_login()
            return
        if page != 'Conversazione' and self.jarvis.role != 'admin':
            self.status.set('Le impostazioni sono riservate all’amministratore.')
            return
        self.page = page
        for target, button in self.toolbar_nav.items():
            selected = target == page or (target == 'Servizi' and page not in {'Email', 'Conversazione'})
            button.state(['selected'] if selected else ['!selected'])
        for name, button in self.nav.items():
            button.state(['selected'] if name == page else ['!selected'])
        self._clear_page()
        if page == 'Conversazione':
            self.jarvis._conversation_scope()
            if self._view_chat_id!=self.jarvis._chat_id:
                self._reset_chat_view()
                for message in self.jarvis._transcript:self.append(message['speaker'],message['text'],persist=False)
                self._view_chat_id=self.jarvis._chat_id
            self._refresh_chats()
            self.chat_frame.pack(fill='both', expand=True)
            self.entry.focus_set()
            return
        if page != 'Email':
            if self.sidebar.winfo_manager():
                self._toggle_archive()
            self.settings_nav.pack(side='left', fill='y', padx=(0, 22))
        canvas_frame = ttk.Frame(self.body)
        canvas_frame.pack(fill='both', expand=True)
        canvas = tk.Canvas(canvas_frame, bg=self.bg, highlightthickness=0)
        self._settings_canvas = canvas
        scrollbar = ttk.Scrollbar(canvas_frame, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        self.content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        def resize_content(event):
            width=min(event.width,920)
            canvas.itemconfigure(window,width=width)
            canvas.coords(window,max(0,(event.width-width)//2),0)
        canvas.bind('<Configure>',resize_content)
        {'Email': self._email_page, 'Servizi': self._services, 'Aspetto': self._appearance, 'Profili': self._profiles, 'Voce': self._voice,
         'Skill': self._skills, 'MCP': self._mcp, 'Comandi': self._commands,
         'Manutenzione': self._maintenance, 'Visione': self._vision_page,'Discord':self._discord_page,
         'Sicurezza':self._security_page}[page]()

    def _chat(self):
        header = ttk.Frame(self.chat_frame)
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, text='JARVIS', font=('Noto Sans', 17, 'bold')).pack(side='left')
        self.route_text = tk.StringVar()
        route_label = ttk.Label(header, textvariable=self.route_text, style='Hint.TLabel', justify='right')
        route_label.pack(side='right')
        header.bind('<Configure>', lambda e: route_label.configure(wraplength=max(160, e.width - 160)))

        stage = self.instrument_stage = ttk.Frame(self.chat_frame)
        stage.pack(fill='x', pady=(0, 8))
        self.chat_frame.bind('<Configure>', self._fit_instruments)
        self.core_canvas = tk.Canvas(stage, height=132, width=290, bg=self.bg,
                                     highlightbackground=LINE, highlightthickness=1)
        self.core_canvas.pack(side='left', fill='both', expand=True)
        self.hud = HudCore(self.core_canvas)
        self.core_canvas.bind('<Configure>', lambda e: self.hud.resize(e.width, e.height))
        instruments = self.voice_instruments = tk.Frame(stage, bg=self.panel, width=204)
        instruments.pack(side='right', fill='y', padx=(6, 0))
        instruments.pack_propagate(False)
        tk.Label(instruments, text='HOST LOCALE', fg=MUTED, bg=self.panel,
                 font=('Noto Sans Mono', 9)).pack(anchor='w', padx=12, pady=(9, 0))
        self.host_readout = tk.StringVar()
        tk.Label(instruments, textvariable=self.host_readout, fg=INK, bg=self.panel, justify='left',
                 font=('Noto Sans Mono', 9)).pack(anchor='w', padx=12, pady=(6, 0))
        self.ram_meter = tk.Canvas(instruments, width=176, height=5, bg=LINE, highlightthickness=0)
        self.ram_fill = self.ram_meter.create_rectangle(0, 0, 0, 5, fill=ACCENT, width=0)
        self.ram_meter.pack(anchor='w', padx=12, pady=(5, 0))
        self.telemetry = tk.StringVar()
        self._telemetry_at = 0.0
        self._work_state = 'working'
        self._listening = False
        links = ttk.Frame(instruments, style='Card.TFrame')
        links.pack(side='bottom', anchor='w', padx=8, pady=5)
        ttk.Button(links, text='Servizi', style='Compact.TButton',
                   command=lambda: self.show('Servizi')).pack(side='left', padx=(0, 4))
        ttk.Button(links, text='MCP', style='Compact.TButton',
                   command=lambda: self.show('MCP')).pack(side='left')

        activity = ttk.Frame(self.chat_frame)
        activity.pack(fill='x', pady=(0, 6))
        self.activity_text = tk.StringVar(value='Registro conversazione')
        self.activity_entries = []
        ttk.Button(activity, text='Attività', style='Compact.TButton',
                   command=self._show_activity).pack(side='right')
        activity_label = ttk.Label(activity, textvariable=self.activity_text, style='Hint.TLabel')
        activity_label.pack(side='left', fill='x', expand=True)
        activity.bind('<Configure>', lambda e: activity_label.configure(wraplength=max(180, e.width - 120)))
        self.workspace=ttk.Frame(self.chat_frame);self.workspace.pack(fill='both',expand=True)
        from chat_view import ChatCanvas
        self.transcript=ChatCanvas(self.workspace,height=12,width=30,wrap='word',bg=self.bg,fg=INK,
            font=('Noto Sans',13),relief='flat',borderwidth=0,highlightthickness=0,
            padx=10,pady=8,state='disabled',cursor='arrow',selectbackground='#30405A',spacing1=3,spacing3=7)
        self.transcript.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(self.workspace,command=self.transcript.yview);scroll.pack(side='right',fill='y')
        self.transcript.configure(yscrollcommand=scroll.set)
        self.transcript.tag_configure('assistant_name',foreground=ACCENT,font=('Noto Sans',10,'bold'),spacing1=22,spacing3=7)
        self.transcript.tag_configure('user_name',foreground=MUTED,font=('Noto Sans',10,'bold'),justify='right',spacing1=22,spacing3=7)
        self.transcript.tag_configure('assistant',foreground=INK,spacing3=14)
        self.transcript.tag_configure('user',foreground=INK,justify='right',spacing1=8,spacing3=16)
        self.transcript.tag_configure('bold',font=('Noto Sans',13,'bold'))
        self.transcript.tag_configure('inline',font=('Noto Sans Mono',10),background=self.panel)
        self.transcript.tag_configure('code',font=('Noto Sans Mono',10),background=self.surface,spacing1=8,spacing3=12)
        self.transcript.tag_configure('welcome',font=('Noto Sans',21,'bold'),foreground=INK,justify='center',spacing3=18)
        self.transcript.tag_configure('welcome_hint',font=('Noto Sans',11),foreground=MUTED,justify='center',spacing3=12)
        def margins(event):
            pad=max(12,(event.width-800)//2)
            for tag in ('assistant_name','assistant','code'):
                self.transcript.tag_configure(tag,lmargin1=pad,lmargin2=pad,rmargin=pad)
            self.transcript.tag_configure('user_name',rmargin=pad)
            self.transcript.tag_configure('user',lmargin1=pad+60,lmargin2=pad+60,rmargin=pad)
            self.root.after_idle(lambda:self.transcript.see('end'))
        self.transcript.bind('<Configure>',margins,add='+')
        self._reset_chat_view()
        self.composer=ttk.Frame(self.chat_frame)
        self.composer.pack(side='bottom',fill='x',pady=(16,0),before=self.workspace)
        input_box=tk.Frame(self.composer,bg=self.panel,highlightbackground=LINE,highlightthickness=1)
        input_box.pack(fill='x')
        self.entry = ChatInput(input_box, height=2, width=20, wrap='word', bg=self.panel, fg=INK,
                               insertbackground=INK, font=('Noto Sans', 13), relief='flat',
                               highlightthickness=0, padx=14, pady=8)
        self.entry.pack(fill='x')
        self.entry.bind('<FocusIn>', lambda _: input_box.configure(highlightbackground=self.hud.accent))
        self.entry.bind('<FocusOut>', lambda _: input_box.configure(highlightbackground=LINE))
        def enter(event):
            if event.state & 0x1:
                return
            self.send()
            return 'break'
        self.entry.bind('<Return>', enter)
        controls = ttk.Frame(self.composer)
        controls.pack(fill='x', pady=(6, 0))
        self.send_button = ttk.Button(controls, text='Invia', style='Primary.TButton', command=self.send)
        self.send_button.pack(side='right')
        self.stop_button = ttk.Button(controls, text='Interrompi', style='Compact.TButton', command=self._stop_task)
        self.stop_button.pack(side='right', padx=6)
        self.mic_button = ttk.Button(controls, text='Modalità vocale', style='Compact.TButton', command=self._toggle_voice)
        self.mic_button.pack(side='right')
        ttk.Label(controls, text='Maiusc+Invio · nuova riga', style='Hint.TLabel').pack(side='left')
        self.approval_frame = tk.Frame(self.chat_frame, bg=self.panel, highlightbackground=AMBER, highlightthickness=2)
        self.approval_text = tk.StringVar()
        tk.Label(self.approval_frame, text='Il Guardiano richiede una decisione', bg=self.panel, fg=AMBER,
                 font=('Noto Sans', 12, 'bold'), anchor='w').pack(fill='x', padx=16, pady=(12, 4))
        approval_summary = tk.Label(self.approval_frame, textvariable=self.approval_text, bg=self.panel, fg=INK,
                                   justify='left', anchor='w', wraplength=800)
        approval_summary.pack(fill='x', padx=16, pady=6)
        self.approval_frame.bind('<Configure>', lambda event: approval_summary.configure(wraplength=max(180,event.width-36)))
        detail_frame = ttk.Frame(self.approval_frame)
        detail_frame.pack(fill='x', padx=16)
        self.approval_details = tk.Text(detail_frame, height=3, wrap='word', bg=self.surface, fg=INK,
                                        font=('Noto Sans', 10), state='disabled')
        self.approval_details.pack(side='left', fill='x', expand=True)
        detail_scroll = ttk.Scrollbar(detail_frame, command=self.approval_details.yview)
        detail_scroll.pack(side='right', fill='y')
        self.approval_details.configure(yscrollcommand=detail_scroll.set)
        decisions = ttk.Frame(self.approval_frame)
        decisions.pack(fill='x', padx=16, pady=(4, 12))
        ttk.Button(decisions, text='Consenti', style='Primary.TButton', command=lambda: self._decide_tool(True)).pack(side='left', padx=(0, 8))
        ttk.Button(decisions, text='Annulla compito', command=lambda: self._decide_tool(False)).pack(side='left')
        self._update_telemetry()


    def _fit_instruments(self, _=None):
        # The Guardian gets the vertical space first, including on compact windows.
        height = self.chat_frame.winfo_height()
        target = max(160, height - 165) if self.jarvis.voice_mode and not self.jarvis.tool_approval_pending else 132 if self.jarvis.tool_approval_pending else max(132, min(318, height - 336))
        if int(self.core_canvas.cget('height')) != target:
            self.core_canvas.configure(height=target)

    def _draw_core(self, canvas, width, height, locked=False):
        renderer = getattr(canvas, '_static_hud', None) or HudCore(canvas)
        if renderer.background != self.bg:
            renderer.set_theme('Blu ionico', background=self.bg)
        renderer.resize(width, height)
        renderer.tick(time.monotonic(),'approval' if locked else 'idle','off')
        canvas._static_hud=renderer


    def _update_telemetry(self):
        preferred=self.jarvis._cfg.get('provider','ollama')
        used=self.jarvis._effective_provider
        model=self.jarvis._effective_model
        suffix=(' · riserva di ' + preferred.upper() if used!=preferred else ' · fallback attivo') if self.jarvis._cfg.get('auto_fallback',True) and preferred!='ollama' else ''
        notice=getattr(self,'_route_notice',None)
        self.route_text.set(notice or f'{used.upper()}  ·  {model}{suffix}')
        key_name=getattr(self.jarvis,'_effective_api_key_name',None)
        api='\nAPI: '+key_name if key_name else ''
        self.telemetry.set(f'Preferito: {preferred}\nRisponde: {used}\nModello: {model}{api}\nRuolo: {self.jarvis.role}\nGuardiano attivo')


    def _hud_state(self, now):
        if self.jarvis.tool_approval_pending:
            return 'approval'
        if now < self._hud_error_until:
            return 'error'
        if self.jarvis.voice_mode and self.jarvis.voice_sleeping:
            return 'sleeping'
        if self._listening:
            if self.jarvis.voice_mode and self.jarvis._voice_input and self.jarvis._voice_input.capture_level < .03:
                return 'idle'
            return 'listening'
        if self.busy:
            return self._work_state
        engine = self.jarvis._tts_engine
        return 'speaking' if engine and engine.is_speaking else 'idle'

    def _update_host_readout(self, now):
        if now < self._telemetry_at:
            return
        self._telemetry_at = now + 2
        load, memory = '—', '—'
        memory_percent = None
        try:
            load = f'{os.getloadavg()[0]:.2f}'
        except OSError:
            pass
        try:
            values = {}
            for line in Path('/proc/meminfo').read_text().splitlines():
                name, _, value = line.partition(':')
                if name in {'MemTotal', 'MemAvailable'}:
                    values[name] = int(value.split()[0])
            if values['MemTotal'] > 0:
                memory_percent = 100 * (1 - values['MemAvailable'] / values['MemTotal'])
                memory = f"{memory_percent:.0f}%"
        except (OSError, KeyError, ValueError):
            pass
        self.host_readout.set(f'Carico 1 min  {load}\nRAM in uso    {memory}')
        self.hud.update_telemetry(memory_percent)
        if memory_percent is None:
            self.ram_meter.pack_forget()
        else:
            self.ram_meter.coords(self.ram_fill, 0, 0, 176 * memory_percent / 100, 5)
            if not self.ram_meter.winfo_manager():
                self.ram_meter.pack(anchor='w', padx=12, pady=(5, 0))

    def _animate_hud(self):
        if self.closed:
            return
        now = time.monotonic()
        mode = self.jarvis._cfg.get('motion', 'full')
        detached = (self._floating_core and self._floating_core.winfo_viewable()) or (self._satellite and self._satellite.visible)
        foreground = self.root.focus_displayof() is not None or self._desktop_visibility.active_pid==os.getpid()
        visible = self.page == 'Conversazione' and self.core_canvas.winfo_viewable() and self.root.state() != 'iconic' and not detached and foreground
        delay = 160
        if visible:
            state = self._hud_state(now)
            voice = self.jarvis._voice_input
            engine = self.jarvis._tts_engine
            level = (voice.capture_level if voice and state == 'listening' else
                     engine.playback_level if engine and state == 'speaking' else 0)
            self.hud.tick(now, state, mode, level)
            self._update_host_readout(now)
            if mode == 'full':
                budget = 17
                cost = (time.monotonic() - now) * 1000
                delay = max(4, round(budget - cost))
            elif mode == 'reduced' and state == 'listening':
                delay = 100
        self.root.after(delay, self._animate_hud)

    def _background_palette(self):
        color = self.jarvis._cfg.get('ui_background', BG)
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = BG
        channels = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
        peak = max(channels)
        if peak > 82:
            color = '#%02X%02X%02X' % tuple(round(channel * 82 / peak) for channel in channels)
        return (BG, PANEL, WHITE) if color.upper() == BG else (
            color.upper(), mix('#839DB8', color, .09), mix('#839DB8', color, .14))

    def _recolor_surfaces(self):
        previous = (self.bg, self.panel, self.surface)
        palette = self._background_palette()
        if palette == previous:
            return
        self.bg, self.panel, self.surface = palette
        replacements = dict(zip(previous, palette))
        pending = [self.root]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            options = widget.keys()
            for option in ('background', 'activebackground', 'highlightbackground'):
                if option in options:
                    replacement = replacements.get(str(widget.cget(option)))
                    if replacement:
                        widget.configure(**{option: replacement})
        self.transcript.tag_configure('inline', background=self.panel)
        self.transcript.tag_configure('code', background=self.surface)
        self.transcript.configure(state='disabled')
        self._style()

    def _rounded_buttons(self, style, accent):
        shades = {
            'TButton': ('#253043', '#30405A', '#182638', self.panel),
            'Primary.TButton': (accent, mix('#FFFFFF', accent, .16), mix(accent, BG, .82), self.panel),
            'Settings.TButton': (self.panel, mix(INK, self.panel, .08), mix(INK, self.panel, .15), self.panel),
        }
        initialize = not hasattr(self, '_button_images')
        if initialize:
            self._button_images = {}
        for name, colors in shades.items():
            if initialize:
                images = [tk.PhotoImage(master=self.root, width=23, height=23) for _ in colors]
                self._button_images[name] = images
                border = 'Jarvis.' + name + '.border'
                style.element_create(border, 'image', images[0], ('disabled', images[3]),
                                     ('pressed', images[2]), ('selected', images[2]),
                                     ('active', images[1]), border=10, padding=0, sticky='nsew')
                style.layout(name, [(border, {'sticky': 'nsew', 'children': [
                    ('Button.focus', {'sticky': 'nsew', 'children': [
                        ('Button.padding', {'sticky': 'nsew', 'children': [
                            ('Button.label', {'sticky': 'nsew'})]})]})]})])
            for image, color in zip(self._button_images[name], colors):
                for row in range(23):
                    dy = max(0, 9 - row, row - 13)
                    inset = 9 - math.isqrt(81 - dy * dy)
                    image.put(color, to=(inset, row, 23 - inset, row + 1))

    def _apply_hud_theme(self):
        self._recolor_surfaces()
        config = self.jarvis._cfg
        self.hud.set_theme(config.get('hud_theme', 'Blu ionico'), config.get('hud_color'),
                           config.get('hud_glow', 55), self.bg)
        color = self.hud.accent
        style = ttk.Style(self.root)
        style.configure('Primary.TButton', focuscolor=color)
        style.map('Compact.TButton', foreground=[('disabled', MUTED), ('selected', color)])
        self._rounded_buttons(style, color)
        self.ram_meter.itemconfigure(self.ram_fill, fill=color)

    def _appearance(self):
        self.title('Aspetto', 'Scegli i colori del nucleo, gli accenti dell’interfaccia e la quantità di movimento.')
        current = self.jarvis._cfg.get('hud_theme', 'Blu ionico')
        theme = tk.StringVar(value=current if current in HUD_THEMES or current == 'Personalizzato' else 'Blu ionico')
        custom_color = self.jarvis._cfg.get('hud_color', '#68DBFF')
        background = self.bg
        target = tk.StringVar(value='Nucleo')
        if not isinstance(custom_color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', custom_color):
            custom_color = '#68DBFF'
        glow_value = self.jarvis._cfg.get('hud_glow', 55)
        glow = tk.DoubleVar(value=max(0, min(100, glow_value)) if isinstance(glow_value, (int, float)) else 55)
        colors = self._settings_section('Tema colore', 'Le richieste del Guardiano restano ambra e gli errori restano rossi.')
        choices = ttk.Frame(colors, style='Card.TFrame')
        choices.pack(fill='x', pady=(0, 12))
        for index, (name, (accent, _, _)) in enumerate(HUD_THEMES.items()):
            row = ttk.Frame(choices, style='Card.TFrame')
            row.grid(row=index // 3, column=index % 3, sticky='w', padx=(0, 12))
            tk.Frame(row, width=12, height=12, bg=accent).pack(side='left', padx=(0, 5))
            ttk.Radiobutton(row, text=name, variable=theme, value=name,
                            style='Card.TRadiobutton').pack(side='left', pady=4)
        ttk.Radiobutton(choices, text='Personalizzato', variable=theme, value='Personalizzato',
                        style='Card.TRadiobutton').grid(row=1, column=1, sticky='w')

        ttk.Radiobutton(choices, text='Nucleo', variable=target, value='Nucleo',
                        style='Card.TRadiobutton').grid(row=0, column=3, sticky='w')
        ttk.Radiobutton(choices, text='Sfondo app', variable=target, value='Sfondo app',
                        style='Card.TRadiobutton').grid(row=1, column=3, sticky='w')
        studio = ttk.Frame(colors, style='Card.TFrame')
        studio.pack(fill='x')
        spectrum = ttk.Frame(studio, style='Card.TFrame')
        spectrum.pack(side='left', anchor='n', padx=(0, 14))
        controls = ttk.Frame(studio, style='Card.TFrame')
        controls.pack(side='left', fill='both', expand=True)
        preview_canvas = tk.Canvas(controls, bg=self.bg, height=82, width=150,
                                   highlightthickness=1, highlightbackground=LINE)
        preview_canvas.pack(fill='x')
        preview = HudCore(preview_canvas)
        preview_canvas.bind('<Configure>', lambda e: preview.resize(e.width, e.height))
        brightness = tk.DoubleVar(value=100)
        swatch = tk.Label(spectrum, bg=self.panel, fg=INK, font=('Noto Sans Mono', 9))
        hue, saturation = 0.0, 0.0

        def refresh():
            preview.set_theme(theme.get(), custom_color, glow.get(), background)
            selected = background if target.get() == 'Sfondo app' else HUD_THEMES.get(theme.get(), (custom_color,))[0]
            swatch.configure(text=selected, fg=INK)
            light_label.configure(text=f'Luminosità  {round(brightness.get())}%')
            glow_label.configure(text=f'Alone e ombre  {round(glow.get())}%')

        def pick_color():
            nonlocal custom_color, background
            selected = wheel.hex_color(hue, saturation, brightness.get() / 100)
            if target.get() == 'Sfondo app':
                background = selected
                refresh()
                return
            custom_color = selected
            if theme.get() != 'Personalizzato':
                theme.set('Personalizzato')
            else:
                refresh()

        def choose(h, s):
            nonlocal hue, saturation
            hue, saturation = h, s
            pick_color()

        wheel = PrismWheel(spectrum, choose)
        wheel.configure(background=self.panel)
        wheel.pack()
        swatch.pack(pady=(4, 0))
        light_label = ttk.Label(controls, style='Card.TLabel')
        light_label.pack(anchor='w', pady=(5, 0))
        light_scale = ttk.Scale(controls, from_=25, to=100, variable=brightness, style='Prism.Horizontal.TScale',
                                command=lambda _: pick_color())
        light_scale.pack(fill='x')
        glow_label = ttk.Label(controls, style='Card.TLabel')
        glow_label.pack(anchor='w', pady=(4, 0))
        ttk.Scale(controls, from_=0, to=100, variable=glow, style='Prism.Horizontal.TScale',
                  command=lambda _: refresh()).pack(fill='x')

        def theme_changed(*_):
            nonlocal hue, saturation
            for_background = target.get() == 'Sfondo app'
            selected = background if for_background else HUD_THEMES.get(theme.get(), (custom_color,))[0]
            light_scale.configure(from_=2 if for_background else 25, to=32 if for_background else 100)
            channels = (int(selected[i:i + 2], 16) / 255 for i in (1, 3, 5))
            hue, saturation, value = colorsys.rgb_to_hsv(*channels)
            brightness.set(round(value * 100))
            wheel.show_position(hue, saturation)
            refresh()

        theme.trace_add('write', theme_changed)
        target.trace_add('write', theme_changed)
        theme_changed()
        mode = tk.StringVar(value=self.jarvis._cfg.get('motion', 'full'))
        motion=self._settings_section('Movimento del nucleo','Le animazioni si sospendono quando cambi pagina o minimizzi la finestra.')
        for value, label, description in [
            ('full', 'Animazioni JARVIS', 'Il campo e i nodi si muovono in base all’attività corrente.'),
            ('reduced', 'Movimento ridotto', 'Mantiene gli stati e il segnale del microfono, con geometria altrimenti ferma.'),
            ('off', 'Animazioni disattivate', 'Nucleo statico con etichette e colori di stato.')]:
            ttk.Radiobutton(motion, text=label, variable=mode, value=value,style='Card.TRadiobutton').pack(anchor='w', pady=(12, 4))
            hint=tk.Label(motion,text=description,bg=self.panel,fg=MUTED,font=('Noto Sans',10),justify='left',anchor='w',wraplength=650)
            hint.pack(fill='x',padx=(24,0),pady=(0,12))
            hint.bind('<Configure>',lambda e,w=hint:w.configure(wraplength=max(150,e.width)))
        def save():
            updates = {'motion': mode.get(), 'hud_theme': theme.get(),
                       'hud_color': custom_color, 'hud_glow': round(glow.get()), 'ui_background': background}
            def write():
                self.jarvis.settings.save(updates=updates)
            def applied(_):
                self._apply_hud_theme()
                self._saved()
            self.submit(write, applied)
        ttk.Button(self.content, text='Salva aspetto', style='Primary.TButton', command=save).pack(
            before=colors.master, anchor='e', pady=(0, 10))

    def _show_activity(self):
        window = tk.Toplevel(self.root)
        window.title('Attività JARVIS')
        window.geometry('760x440')
        output = tk.Text(window, bg=self.surface, fg=INK, wrap='word', font=('Noto Sans', 11), padx=18, pady=16)
        output.pack(fill='both', expand=True)
        output.insert('end', self.telemetry.get() + '\n\n' + ('\n'.join(self.activity_entries) or 'Nessuno strumento eseguito in questa sessione.'))
        output.configure(state='disabled')

    def _decide_tool(self, approved):
        if self.busy or not self.jarvis.tool_approval_pending:
            return
        self.approval_frame.pack_forget()
        pin = None
        if approved and self.jarvis.voice_mode:
            pin = self._ask_credential(admin=True)
            if pin is None:
                self.approval_frame.pack(side='bottom', fill='x', pady=(10, 0))
                return
        if not self.jarvis.voice_mode:
            self.composer.pack(side='bottom', fill='x', pady=(20, 0), before=self.workspace)
        self.submit(lambda: self.jarvis.resume_pending_tool(approved, admin_pin=pin), self._answer)

    def _scroll_settings(self, event):
        canvas = getattr(self, '_settings_canvas', None)
        if self.page in {'Conversazione', 'Accesso'} or not canvas or not canvas.winfo_exists():
            return
        if isinstance(event.widget, (tk.Text, ttk.Treeview)):
            return
        direction = -1 if getattr(event, 'num', None) == 4 or getattr(event, 'delta', 0) > 0 else 1
        canvas.yview_scroll(direction * 3, 'units')

    def _stop_task(self):
        self._interrupt_speech()
        self.jarvis.cancel_current()
        if self.jarvis.tool_approval_pending:
            self._decide_tool(False)
        elif self.busy:
            self.status.set('Interruzione richiesta · annullamento della richiesta in corso…')

    def append(self, speaker, message, persist=True):
        if persist and self.jarvis.role!='locked':self.jarvis.record_chat_message(speaker,message)
        self._view_chat_id=self.jarvis._chat_id
        self.transcript.configure(state='normal')
        user=speaker=='Tu'
        if user and self._welcome:
            self.transcript.delete('1.0','end');self._welcome=False
        self.transcript.insert('end',speaker+'\n','user_name' if user else 'assistant_name')
        body='user' if user else 'assistant'
        for part in re.split(r'(```[\s\S]*?```)',str(message)):
            if part.startswith('```'):
                lines=part[3:-3].split('\n',1)
                self.transcript.insert('end',lines[1] if len(lines)>1 else lines[0],('code',))
            else:
                for token in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)',part):
                    if token.startswith('**') and token.endswith('**'):
                        self.transcript.insert('end',token[2:-2],(body,'bold'))
                    elif token.startswith('`') and token.endswith('`'):
                        self.transcript.insert('end',token[1:-1],(body,'inline'))
                    else:self.transcript.insert('end',token,(body,))
        self.transcript.insert('end','\n\n');self.transcript.configure(state='disabled');self.transcript.see('end')


    def submit(self, function, callback=None, *, listening=False):
        if not self.busy:
            self._operation_started=time.monotonic()
            if self.jarvis._agent_cancel.is_set() and not self.jarvis.tool_approval_pending:self.jarvis.prepare_operation()
        self._work_state = 'working'
        self._listening = listening
        self.busy += 1
        if not listening:
            self._pending_work+=1
            self._idle_work_active=True
        self.stop_button.configure(state='normal')
        self.send_button.configure(state='disabled')
        self.mic_button.configure(state='disabled')
        self.status.set('Operazione in corso…')
        self.jobs.put((function, callback, self.jarvis._session_epoch,listening))

    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            function, callback = job[:2]
            epoch = job[2] if len(job)>2 else None
            listening=job[3] if len(job)>3 else False
            try:
                value, error = (function() if epoch is None or epoch == self.jarvis._session_epoch else None), None
            except Exception as exc:
                value, error = None, str(exc)
            self.results.put((value, error, callback, epoch,listening))

    def _poll(self):
        if self.closed:
            return
        while not self.results.empty():
            value, error, callback, epoch,listening = self.results.get()
            self.busy -= 1
            if not listening:self._pending_work-=1
            self._listening = False
            if epoch is not None and epoch != self.jarvis._session_epoch:
                continue
            self.status.set('Pronto' if self.busy == 0 else 'Operazione in corso…')
            if error:
                self._hud_error_until = time.monotonic() + 3
                self.status.set('Operazione non completata: ' + error)
                self.append('Errore', error)
            elif callback:
                try:
                    callback(value)
                except tk.TclError:
                    pass  # Page was closed while its background job completed.
                except Exception as exc:
                    self._callback_error(type(exc), exc, exc.__traceback__)
        chats_changed=False
        while not self.jarvis.events.empty():
            event = self.jarvis.events.get()
            if isinstance(event, dict) and event.get('type') == 'tool_activity':
                self._work_state = 'executing' if event.get('status') == 'running' else 'working'
                if event.get('status') == 'error':
                    self._hud_error_until = time.monotonic() + 3
                states = {'checking':'Verifica autorizzazione','running': 'In esecuzione', 'done': 'Completato', 'error': 'Errore', 'approval': 'In attesa di autorizzazione'}
                detail = f'{states.get(event["status"], event["status"])}: {event["tool"]}'
                if event.get('detail'):
                    detail += ' — ' + event['detail']
                names = {'read_file':'Lettura file','find_files':'Ricerca file','system_info':'Dati del PC',
                         'curl_fetch':'curl · lettura risposta HTTP','read_url':'Web · estrazione dati','web_search':'Ricerca web con curl',
                         'execute_terminal_command':'Comando di sistema','create_directory':'Creazione cartella',
                         'write_text_file':'Scrittura file','copy_file':'Copia file','list_directory':'Elenco cartella'}
                self.activity_text.set(states.get(event['status'],event['status']) + ': ' + names.get(event['tool'],event['tool']))
                self.activity_entries.append(detail)
                self.activity_entries = self.activity_entries[-200:]
            elif isinstance(event, dict) and event.get('type') in {'provider_attempt','provider_used','provider_unavailable','provider_failed','local_service'}:
                kind=event['type']
                if kind == 'provider_attempt':
                    self._work_state = 'provider'
                    self._route_notice = 'Connessione a ' + event['provider'].upper() + '…'
                elif kind=='provider_used':
                    self._work_state = 'working'
                    self._route_notice=None
                    self._provider_wait=None
                    if event.get('fallback'):self.activity_entries.append('Fallback: risponde ' + event['provider'] + ' con ' + event['model'])
                elif kind=='provider_unavailable':self.activity_entries.append(event['reason'])
                elif kind=='provider_failed':self._route_notice='Nessun provider disponibile · controlla le impostazioni'
                else:self._route_notice=event['message']
            elif isinstance(event, dict) and event.get('type') == 'vision_result':
                self._show_vision_result(event['result'])
            elif isinstance(event, dict) and event.get('type') == 'voice_failure':
                self._leave_voice()
                self.status.set(event['message'])
            elif isinstance(event, dict) and event.get('type') == 'voice_match':
                self.activity_text.set(f"Voce {'riconosciuta' if event['matched'] else 'ignorata'} · somiglianza {event['score']:.2f}")
            elif isinstance(event,dict) and event.get('type')=='discord_state':
                if hasattr(self,'_discord_status'):self._discord_status.set(event['message'])
                if event['state']=='error':self.status.set(event['message'])
                self.activity_entries.append(event['message'])
            elif event=='discord_start':
                if self.jarvis.execution_role=='admin':self.jarvis.discord_service().start()
            elif isinstance(event, dict) and event.get('type') == 'voice_stage':
                if event['status'] == 'recognizing':
                    self._listening = False
                    self.activity_text.set('Riconoscimento delle parole…')
                else:
                    self.activity_entries.append(f"Riconoscimento vocale: {event['seconds']:.2f} s")
            elif isinstance(event, dict) and event.get('type') == 'tts_state':
                if event['status'] == 'error':
                    self.status.set(event['message'])
                    self.activity_entries.append(event['message'])
                elif event['status'] in {'ready', 'stopped'} and self.jarvis.voice_mode:
                    self.status.set('Pronto ad ascoltare')
            elif event == 'settings':
                self.show('Servizi')
            elif event == 'exit':
                self.close()
                return
            elif event == 'voice_unavailable':
                self.status.set('Sintesi vocale disattivata: l’installazione non include gTTS.')
            elif event == 'chats_changed':chats_changed=True
            elif isinstance(event,dict) and event.get('type')=='archive_error':
                self.status.set('Salvataggio chat non riuscito: '+event['message'])
            elif isinstance(event,dict) and event.get('type')=='task_error':
                self.activity_entries.append('Compito interrotto: '+event['message'])
                self.activity_text.set('Compito interrotto · puoi correggere la richiesta e riprendere')
            elif isinstance(event,dict) and event.get('type')=='task_cancelled':
                self.activity_text.set('Compito interrotto su richiesta')
                self.activity_entries.append('Interruzione confermata · nessun altro strumento verrà eseguito')
            elif isinstance(event,dict) and event.get('type')=='provider_retry':
                self.activity_text.set('Correzione della chiamata al modello in corso')
                self.activity_entries.append(event['message'])
            elif isinstance(event,dict) and event.get('type')=='context_packed':
                self.activity_entries.append(f"Contesto: {event['omitted_messages']} messaggi precedenti conservati nell’archivio e omessi dall’invio; compito attivo integro.")
            elif isinstance(event,dict) and event.get('type')=='api_key_attempt':
                self._route_notice=f"{event['provider']} · connessione con API {event['key_name']}…"
            elif isinstance(event,dict) and event.get('type')=='api_key_unavailable':
                self.activity_entries.append(f"API {event['key_name']} ({event['provider']}): {event['reason']}")
            elif isinstance(event,dict) and event.get('type')=='api_key_used':
                marker=(event['provider'],event['key_name'])
                if marker!=getattr(self,'_last_api_marker',None):
                    self.activity_entries.append(f"API attiva: {event['key_name']} · {event['provider']} · {event['model']}")
                    self._last_api_marker=marker
            elif isinstance(event,dict) and event.get('type')=='provider_wait':
                self._provider_wait=event
                self.activity_entries.append(f"{event['provider']} / {event['model']}: quota temporanea, attesa {int(event['seconds'])} s prima di riprovare.")
        if chats_changed:self._refresh_chats()
        pending = self.jarvis.tool_approval_pending
        if self.page=='Conversazione':
            self._fit_instruments()
        self._update_chat_actions()
        if not self.busy and not pending and self.jarvis._agent_cancel.is_set():
            self._route_notice=None
            self.activity_text.set('Compito interrotto su richiesta')
        state = 'disabled' if self.busy or pending else 'normal'
        self.send_button.configure(state=state)
        self.entry.configure(state='disabled' if pending else 'normal')
        self.mic_button.configure(state=state)
        speech_busy = bool(self.jarvis._tts_engine and self.jarvis._tts_engine.is_busy)
        self.voice_stop_button.state(['!disabled'] if speech_busy else ['disabled'])
        self.stop_button.configure(state='normal' if self.busy or pending or speech_busy else 'disabled')
        self._update_telemetry()
        if self.busy:
            elapsed=int(time.monotonic()-getattr(self,'_operation_started',time.monotonic()))
            self.status.set(f"{'Ascolto' if self._listening else 'Elaborazione in corso'} · {elapsed} s")
            if self.jarvis.voice_mode and self.jarvis.voice_sleeping:
                self.status.set('Standby · di’ «hey JARVIS» per risvegliarmi')
            if self.jarvis._agent_cancel.is_set():self.status.set('Annullamento della richiesta in corso…')
            wait=getattr(self,'_provider_wait',None)
            if wait and wait['until']>time.monotonic() and not self.jarvis._agent_cancel.is_set():
                seconds=max(1,int(wait['until']-time.monotonic())+1)
                self.status.set(f"Quota Groq temporanea · riprovo {wait['model']} tra {seconds} s · Interrompi per annullare")
        self._update_idle_work(time.monotonic())
        self.root.after(80, self._poll)

    def send(self, text=None):
        if self.busy or self.jarvis.tool_approval_pending:
            self.status.set('Un compito è già attivo. Completa o annulla l’autorizzazione richiesta.')
            return
        if self.jarvis.role == 'locked':
            self.status.set('Sblocca la sessione con il PIN.')
            return
        text = text if text is not None else self.entry.get().strip()
        if not text:
            return
        self._user_activity()
        self.entry.delete(0, 'end')
        self.append('Tu', text)
        cancel_event=self.jarvis.prepare_operation()
        self._provider_wait=None
        self.submit(lambda: self.jarvis.process(text,cancel_event=cancel_event), self._answer)

    def _answer(self, answer):
        if answer.startswith(('Risposta non disponibile:', 'Errore provider')):
            self._hud_error_until = time.monotonic() + 3
        if answer.startswith('__AGENT_CONFIRM__'):
            call = json.loads(answer.removeprefix('__AGENT_CONFIRM__'))
            arguments = call['arguments']
            target = arguments.get('path') or arguments.get('destination') or arguments.get('server') or ''
            labels={'write_text_file':'Creare un nuovo file di testo','create_directory':'Creare una cartella',
                    'copy_file':'Copiare un file','execute_terminal_command':'Eseguire un comando sul PC',
                    'email_send':'Inviare un’email','mcp_call':'Eseguire uno strumento MCP','email_read':'Controllare e organizzare la posta'}
            summary=labels.get(call['tool'],call['tool'])
            if call['tool']=='write_text_file' and arguments.get('overwrite'):
                summary='Sostituire il file esistente · originale conservato in backup'
            def compact(value,limit=100):
                value=str(value).replace('\n',' ')
                return value if len(value)<=limit else value[:limit//2]+' … '+value[-limit//2:]
            if arguments.get('source'):summary+='\nOrigine: '+compact(arguments['source'])
            if target:summary+='\nDestinazione: '+compact(target)
            if arguments.get('command'):
                summary+='\nComando: '+compact(arguments['command'],130)+'\nDirectory: '+compact(call['cwd'])
            self.approval_text.set(summary)
            self.approval_details.configure(state='normal')
            self.approval_details.delete('1.0', 'end')
            self.approval_details.insert('1.0', json.dumps({'directory_di_lavoro':call['cwd'],'argomenti':arguments}, ensure_ascii=False, indent=2))
            self.approval_details.configure(state='disabled')
            self.composer.pack_forget()
            placement = {} if self.jarvis.voice_mode else {'before': self.workspace}
            self.approval_frame.pack(side='bottom', fill='x', pady=(10, 0), **placement)
            self.status.set('Compito sospeso: scegli Consenti oppure Annulla compito.')
            self.show('Conversazione')
            return
        if answer.startswith('__VOICE_CONFIRM__'):
            request = json.loads(answer.removeprefix('__VOICE_CONFIRM__'))
            pin = self._ask_credential(admin=True, context=json.dumps(request, ensure_ascii=False))
            if pin is None:
                self.append('JARVIS', 'Operazione annullata.')
                return
            def execute_once():
                with self.jarvis.voice_admin_operation(pin):
                    if request.get('host'):
                        return self.jarvis._execute_remote(**request, confirmed=True)
                    return self.jarvis._execute(request['command'], confirmed=True)
            self.submit(execute_once, self._answer)
            return
        if answer.startswith('__NEEDS_CONFIRM__'):
            command = answer.removeprefix('__NEEDS_CONFIRM__')
            if messagebox.askyesno('Esecuzione comando', 'Eseguire questo comando?\n\n' + command, parent=self.root):
                self.submit(lambda: self.jarvis._execute(command, confirmed=True), self._answer)
            else:
                self.append('JARVIS', 'Comando annullato.')
            return
        if answer.startswith('__REMOTE_CONFIRM__'):
            call = json.loads(answer.removeprefix('__REMOTE_CONFIRM__'))
            prompt = f"Host SSH: {call['host']}\n\nComando:\n{call['command']}"
            if messagebox.askyesno('Esecuzione via SSH', prompt, parent=self.root):
                self.submit(lambda: self.jarvis._execute_remote(**call, confirmed=True), self._answer)
            else:
                self.append('JARVIS', 'Comando remoto annullato.')
            return
        if answer.startswith('__MCP_CONFIRM__'):
            call = json.loads(answer.removeprefix('__MCP_CONFIRM__'))
            if messagebox.askyesno('Strumento MCP', 'Eseguire questa chiamata?\n\n' + json.dumps(call, ensure_ascii=False, indent=2), parent=self.root):
                self.submit(lambda: self.jarvis.mcp_client(call['server']).call(call['tool'], call['arguments']),
                            lambda result: self.append('MCP', json.dumps(result, ensure_ascii=False, indent=2)))
            return
        if answer:
            self.append('JARVIS', answer)
            if (self.jarvis._cfg['tts'] or self.jarvis.voice_mode) and not self.jarvis.voice_sleeping:
                self.submit(lambda: self.jarvis._tts_say(answer))

    def listen(self):
        if self.busy or self.jarvis.tool_approval_pending:
            self.status.set('Completa o interrompi il compito attivo prima di usare il microfono.')
            return
        if not self.jarvis._cfg['stt']:
            messagebox.showinfo('Microfono', 'Abilita il riconoscimento nelle impostazioni Voce.', parent=self.root)
            return
        self.submit(self.jarvis.listen_once, lambda text: self.send(text) if text else self.status.set('Nessuna voce riconosciuta'),
                    listening=True)

    def field(self, parent, label, value='', secret=False, options=None):
        ttk.Label(parent, text=label).pack(anchor='w', pady=(10, 4))
        variable = tk.StringVar(value=value)
        if options:
            widget = ttk.Combobox(parent, textvariable=variable, values=options, state='readonly')
        else:
            widget = ttk.Entry(parent, textvariable=variable, show='●' if secret else '')
        widget.pack(fill='x', padx=(0, 12))
        return variable

    def _saved(self, _=None):
        self.status.set('Impostazioni salvate')

    def _services(self):
        self.title('Modelli e connessioni','Scegli il cervello preferito. Il fallback mantiene il compito e i permessi della sessione.')
        cfg=self.jarvis._cfg
        body=self._settings_section('Il tuo assistente','Un provider preferito, con continuità automatica quando il servizio non è disponibile.')
        body.columnconfigure(0,weight=1);body.columnconfigure(1,weight=1)
        provider=tk.StringVar(value=cfg['provider']);model=tk.StringVar(value=cfg['model'])
        drafts=dict(cfg.get('provider_models',{}));previous=[cfg['provider']]
        from api_key_pool import is_pool_secret
        names=sorted(n for n in set(SERVICE_FIELDS)|set(self.jarvis.settings.secrets) if not is_pool_secret(n))
        original_services={name:self.jarvis.settings.secret(name) for name in names}
        fields={name:tk.StringVar(value=original_services[name]) for name in names}
        pf,pw=self._settings_field(body,'Provider preferito',provider,['groq','cerebras','ollama','nvidia','openai'])
        pf.grid(row=0,column=0,sticky='ew',padx=(0,10),pady=(0,18))
        mf,model_box=self._settings_field(body,'Modello',model,[model.get()],editable=True)
        mf.grid(row=0,column=1,sticky='ew',padx=(10,0),pady=(0,18))
        key_placeholder=tk.StringVar()
        key_frame,key_widget=self._settings_field(body,'Chiave API',key_placeholder,secret=True)
        key_frame.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,18))
        host=tk.StringVar(value=cfg['ollama_host'])
        hf,hw=self._settings_field(body,'Server Ollama',host)
        hf.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,18))
        language=tk.StringVar(value=cfg['lang'])
        lf,lw=self._settings_field(body,'Lingua delle risposte',language,['it','en','fr','de','es','pt','nl','ru','bg','lb'])
        lf.grid(row=2,column=0,sticky='ew',padx=(0,10),pady=(0,18))
        thinking=tk.BooleanVar(value=cfg.get('nvidia_thinking',False))
        thinking_widget=ttk.Checkbutton(body,text='Ragionamento approfondito',variable=thinking,style='Card.TCheckbutton')
        thinking_widget.grid(row=2,column=1,sticky='w',padx=(10,0))
        buttons=tk.Frame(body,bg=self.panel);buttons.grid(row=3,column=0,columnspan=2,sticky='ew',pady=(0,12))
        ttk.Button(buttons,text='Salva modifiche',style='Primary.TButton',command=lambda:save()).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='Verifica chat',command=lambda:verify()).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='Carica modelli',command=lambda:load_models()).pack(side='left')
        result_var=tk.StringVar(value='La verifica usa un messaggio di prova, senza file o dati del PC.')
        result_label=tk.Label(body,textvariable=result_var,bg=self.panel,fg=MUTED,font=('Noto Sans',10),wraplength=760,justify='left',anchor='w')
        result_label.grid(row=4,column=0,columnspan=2,sticky='ew',pady=(4,0))
        result_label.bind('<Configure>',lambda e:result_label.configure(wraplength=max(150,e.width)))
        self._api_keys_panel(provider,model)
        fallback_body=self._settings_section('Continuità del servizio','Prima le API disponibili dello stesso modello, poi Groq → Cerebras → Ollama. Il passaggio al 20B è facoltativo. Strumenti, ruolo e Guardiano restano gli stessi.')
        fallback=tk.BooleanVar(value=cfg.get('auto_fallback',True))
        small_fallback=tk.BooleanVar(value=cfg.get('groq_small_fallback',False))
        autostart=tk.BooleanVar(value=cfg.get('ollama_autostart',True))
        ttk.Checkbutton(fallback_body,text='Fallback automatico',variable=fallback,style='Card.TCheckbutton').pack(anchor='w',pady=(0,10))
        ttk.Checkbutton(fallback_body,text='Consenti anche Groq 20B come riserva del 120B',variable=small_fallback,style='Card.TCheckbutton').pack(anchor='w',pady=(0,10))
        ttk.Checkbutton(fallback_body,text='Avvia Ollama locale se necessario',variable=autostart,style='Card.TCheckbutton').pack(anchor='w',pady=(0,16))
        local_model=tk.StringVar(value=cfg.get('provider_models',{}).get('ollama',''))
        local_frame,local_widget=self._settings_field(fallback_body,'Modello locale di riserva · vuoto = scoperta automatica',local_model)
        local_frame.pack(fill='x')
        explanation=tk.Label(fallback_body,text='Il provider successivo riceve il contesto necessario a continuare la richiesta. I modelli locali già installati vengono verificati; nessun download automatico.',bg=self.panel,fg=MUTED,font=('Noto Sans',10),wraplength=760,justify='left',anchor='w')
        explanation.pack(fill='x',pady=(14,12))
        explanation.bind('<Configure>',lambda e:explanation.configure(wraplength=max(150,e.width)))
        row=tk.Frame(fallback_body,bg=self.panel);row.pack(fill='x')
        ttk.Button(row,text='Riprova il provider preferito',command=lambda:reset_health()).pack(side='left',padx=(0,8))
        ttk.Button(row,text='Verifica Ollama',command=lambda:verify_local()).pack(side='left')
        timeout=tk.StringVar(value=str(cfg.get('ollama_timeout',300)))
        timeout_frame,timeout_widget=self._settings_field(fallback_body,'Tempo massimo Ollama in secondi · 60–900',timeout)
        timeout_frame.pack(fill='x',pady=(16,0))
        groq_wait=tk.StringVar(value=str(cfg.get('groq_retry_wait',120)))
        groq_frame,groq_widget=self._settings_field(fallback_body,'Attesa massima quota Groq · 0–120 secondi; 0 passa subito alla riserva',groq_wait)
        groq_frame.pack(fill='x',pady=(16,0))
        integration=self._settings_section('Altri servizi','Gmail si configura nell’area avanzata. Per collegare il bot apri Discord nel menu Connessioni.')
        ttk.Button(integration,text='Configura integrazioni',command=self._advanced_services).pack(anchor='w')
        def render_provider(initial=False):
            from jarvis_v10 import PROVIDERS
            selected=provider.get()
            if not initial:
                drafts[previous[0]]=model.get();model.set(drafts.get(selected) or self.jarvis.model_for(selected));previous[0]=selected
                model_box.configure(values=[model.get()])
            if selected=='ollama':hf.grid();key_frame.grid_remove()
            else:
                hf.grid_remove();key_frame.grid()
                key_widget.configure(textvariable=fields[PROVIDERS[selected][1]])
            if selected=='nvidia':thinking_widget.grid()
            else:thinking_widget.grid_remove()
        render_provider(True);provider.trace_add('write',lambda *_:render_provider())
        def captured():
            from jarvis_v10 import PROVIDERS
            p=provider.get();key=PROVIDERS[p][1] if p in PROVIDERS else None
            return p,model.get().strip(),fields[key].get() if key else None,host.get().strip(),thinking.get()
        def show_result(value):
            text=str(value);result_var.set(text)
            result_label.configure(fg=AMBER if any(x in text.lower() for x in ('http ','non disponibile','non riuscita','errore')) else ACCENT)
        def save():
            p,m,key,address,think=captured()
            if not m or (p=='ollama' and not address.startswith(('http://','https://'))):
                show_result('Scegli un modello e un indirizzo locale valido.');return
            values={name:var.get() for name,var in fields.items() if var.get()!=original_services[name]}
            lang=language.get();fall=fallback.get();small=small_fallback.get();start=autostart.get();local=local_model.get().strip()
            try:seconds=int(timeout.get());assert 60<=seconds<=900
            except (ValueError,AssertionError):show_result('Scegli un tempo Ollama tra 60 e 900 secondi.');return
            try:quota_wait=int(groq_wait.get());assert 0<=quota_wait<=120
            except (ValueError,AssertionError):show_result('Scegli un’attesa Groq tra 0 e 120 secondi.');return
            def write():
                self.jarvis.settings.save_secrets(values);self.jarvis.select_provider(p,m)
                cfg.update(ollama_host=address,lang=lang,nvidia_thinking=think,auto_fallback=fall,groq_small_fallback=small,ollama_autostart=start,ollama_timeout=seconds,groq_retry_wait=quota_wait)
                if local:cfg.setdefault('provider_models',{})['ollama']=local
                elif p!='ollama':cfg.setdefault('provider_models',{}).pop('ollama',None)
                self.jarvis.settings.save()
            self.submit(write,lambda _:(show_result('Impostazioni salvate. Il fallback è pronto per la prossima richiesta.'),self._saved()))
        def verify():
            p,m,key,address,think=captured()
            self.submit(lambda:self.jarvis.probe_provider(p,m,key,address,think),show_result)
        def load_models():
            p,m,key,address,think=captured()
            def fetch():
                try:return {'ids':self.jarvis.available_models(p,key,address)}
                except Exception as exc:return {'error':self.jarvis._provider_problem(p,exc)}
            def loaded(value):
                if 'error' in value:show_result(value['error']);return
                model_box.configure(values=sorted(value['ids']));show_result(f"{len(value['ids'])} modelli caricati. Scegli un modello e verifica la chat.")
            self.submit(fetch,loaded)
        def reset_health():
            self.jarvis.reset_provider_health();show_result('Il provider preferito verrà riprovato alla prossima richiesta.')
        def verify_local():
            self.submit(self.jarvis.local_status,show_result)


    def _api_keys_panel(self,provider,active_model):
        body=self._settings_section('API multiple','Aggiungi chiavi di altri account per lo stesso provider. Il cambio API conserva modello, strumenti e compito.')
        primary=tk.BooleanVar(value=True);name=tk.StringVar();value=tk.StringVar();models=tk.StringVar();enabled=tk.BooleanVar(value=True)
        message=tk.StringVar();selected=[None];loading=[False]
        primary_button=ttk.Checkbutton(body,text='Usa la chiave principale prima di questo elenco',variable=primary,style='Card.TCheckbutton')
        primary_button.pack(anchor='w',pady=(0,12))
        table_frame=tk.Frame(body,bg=self.panel);table_frame.pack(fill='x',pady=(0,14))
        table=ttk.Treeview(table_frame,columns=('name','models','state'),show='headings',height=4,selectmode='browse')
        for key,title,width in (('name','Nome',160),('models','Modelli',210),('state','Stato',95)):
            table.heading(key,text=title);table.column(key,width=width,minwidth=50,stretch=True)
        table_bar=ttk.Scrollbar(table_frame,command=table.yview);table_bar.pack(side='right',fill='y')
        table.configure(yscrollcommand=table_bar.set);table.pack(side='left',fill='both',expand=True)
        form=tk.Frame(body,bg=self.panel);form.pack(fill='x');form.columnconfigure(0,weight=1);form.columnconfigure(1,weight=1)
        nf,nw=self._settings_field(form,'Nome account o chiave',name);nf.grid(row=0,column=0,sticky='ew',padx=(0,8),pady=(0,12))
        mf,mw=self._settings_field(form,'Modelli · vuoto = tutti; separa con virgole',models);mf.grid(row=0,column=1,sticky='ew',padx=(8,0),pady=(0,12))
        kf,kw=self._settings_field(form,'Nuova chiave API · vuoto conserva quella salvata',value,secret=True);kf.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,12))
        toggle=ttk.Checkbutton(form,text='Chiave abilitata',variable=enabled,style='Card.TCheckbutton');toggle.grid(row=2,column=0,columnspan=2,sticky='w',pady=(0,14))
        actions=tk.Frame(body,bg=self.panel);actions.pack(fill='x')
        for i in range(3):actions.columnconfigure(i,weight=1)
        buttons={}
        status=tk.Label(body,textvariable=message,bg=self.panel,fg=MUTED,font=('Noto Sans',10),justify='left',anchor='w',wraplength=700)
        status.pack(fill='x',pady=(12,0));status.bind('<Configure>',lambda e:status.configure(wraplength=max(140,e.width)))
        tip=tk.Label(body,text='Groq condivide le quote fra chiavi della stessa organizzazione. Per ogni chiave viene rispettata l’attesa comunicata dal servizio.',bg=self.panel,fg=MUTED,font=('Noto Sans',9),justify='left',anchor='w',wraplength=700)
        tip.pack(fill='x',pady=(10,0));tip.bind('<Configure>',lambda e:tip.configure(wraplength=max(140,e.width)))
        def set_message(text):
            if status.winfo_exists():message.set(str(text))
        def new():
            selected[0]=None;table.selection_remove(*table.selection());name.set('');value.set('');models.set('');enabled.set(True)
            set_message('Inserisci nome e chiave, poi premi Salva chiave.')
        def render(*_):
            if not table.winfo_exists():return
            p=provider.get();loading[0]=True
            table.delete(*table.get_children());selected[0]=None;name.set('');value.set('');models.set('');enabled.set(True)
            remote=p in {'groq','cerebras','nvidia','openai'}
            primary.set(self.jarvis._cfg.get('provider_primary_key_enabled',{}).get(p,True))
            for widget in (primary_button,nw,mw,kw,toggle,*buttons.values()):widget.configure(state='normal' if remote else 'disabled')
            if remote:
                for item in self.jarvis.api_key_entries(p):
                    state='Abilitata' if item['enabled'] and item['configured'] else ('Disabilitata' if not item['enabled'] else 'Senza chiave')
                    table.insert('', 'end',iid=item['id'],values=(item['name'],', '.join(item['models']) or 'Tutti',state))
                message.set('Chiavi provate in ordine dall’alto. Le modifiche qui si salvano con i pulsanti della sezione.')
            else:message.set('Ollama usa il server locale e non richiede chiavi API.')
            loading[0]=False
        def choose(_=None):
            ids=table.selection()
            if loading[0] or not ids:return
            item=next((e for e in self.jarvis.api_key_entries(provider.get()) if e['id']==ids[0]),None)
            if item:
                selected[0]=item['id'];name.set(item['name']);models.set(', '.join(item['models']));enabled.set(item['enabled']);value.set('')
                set_message('Chiave salvata e mascherata. Lascia vuoto il campo API per conservarla.')
        table.bind('<<TreeviewSelect>>',choose)
        def write(action,**args):
            if self.busy or self.jarvis.agent_task_active:
                if action=='primary':primary.set(self.jarvis._cfg.get('provider_primary_key_enabled',{}).get(provider.get(),True))
                set_message('Completa o interrompi il compito prima di modificare le API.');return
            p=provider.get()
            focus_key=args.get('key_id')
            def finished(result):
                if table.winfo_exists() and provider.get()==p:
                    render();key_id=result if action=='save' else focus_key
                    if key_id and table.exists(key_id):table.selection_set(key_id);choose()
                    set_message('Modifica salvata. La prossima richiesta userà questo ordine di API.')
            self.submit(lambda:self.jarvis.edit_api_keys(action,p,**args),finished)
        def save():write('save',name=name.get(),value=value.get(),models=models.get().split(','),enabled=enabled.get(),key_id=selected[0])
        def remove():
            if selected[0]:write('remove',key_id=selected[0])
            else:set_message('Seleziona la chiave da rimuovere.')
        def move(direction):
            if selected[0]:write('move',key_id=selected[0],direction=direction)
            else:set_message('Seleziona la chiave da spostare.')
        def verify():
            p=provider.get();restriction=[m.strip() for m in models.get().split(',') if m.strip()]
            m=restriction[0] if restriction else active_model.get().strip() or self.jarvis.model_for(p)
            raw=value.get().strip();key_id=selected[0]
            if not raw and not key_id:set_message('Inserisci una chiave o selezionane una salvata.');return
            set_message(f'Verifica singola su {p} / {m}; nessun fallback.')
            self.submit(lambda:self.jarvis.probe_provider(p,m,api_key=raw) if raw else self.jarvis.probe_api_key(p,key_id,m),set_message)
        operations=(('new','Nuova',new),('save','Salva chiave',save),('verify','Verifica',verify),('up','Sposta su',lambda:move(-1)),('down','Sposta giù',lambda:move(1)),('remove','Rimuovi',remove))
        for i,(key,label,command) in enumerate(operations):
            button=ttk.Button(actions,text=label,command=command,style='Compact.TButton');button.grid(row=i//3,column=i%3,sticky='ew',padx=(0,6),pady=(0,6));buttons[key]=button
        primary_button.configure(command=lambda:write('primary',enabled=primary.get()))
        provider.trace_add('write',render);render()
        self._api_key_controls={'table':table,'name':name,'key':value,'models':models,'enabled':enabled,'primary':primary,'primary_button':primary_button,
                                'provider':provider,'buttons':buttons,'message':message,'key_entry':kw,'body':body,'refresh':render}

    def _email_page(self):
        self.title('Email', 'Controlla e cerca la posta. L’invio richiede conferma prima di partire.')
        output = tk.Text(self.content, height=10, wrap='word', bg=self.surface, fg=INK,
                         insertbackground=ACCENT, relief='flat', padx=12, pady=10)
        output.pack(fill='both', expand=True, pady=(0, 14))
        output.configure(state='disabled')
        def show_result(result):
            output.configure(state='normal')
            output.delete('1.0', 'end')
            output.insert('1.0', str(result))
            output.configure(state='disabled')
        row = ttk.Frame(self.content)
        row.pack(fill='x', pady=8)
        ttk.Button(row,text='Verifica Gmail',command=lambda:self.submit(
            lambda:self.jarvis.email_action('email_status'),show_result)).pack(side='left',padx=(0,8))
        ttk.Button(row, text='Controlla nuove email', command=lambda: self.submit(
            lambda: self.jarvis.email_action('email_check'), show_result)).pack(side='left', padx=(0, 8))
        ttk.Button(row, text='Leggi ultima email', command=lambda: self.submit(
            lambda: self.jarvis.email_action('email_read_last'), show_result)).pack(side='left')
        query = self.field(self.content, 'Cerca nella posta')
        def search_mail():
            value = query.get()
            self.submit(lambda: self.jarvis.email_action('email_search', value), show_result)
        ttk.Button(self.content, text='Cerca email', command=search_mail).pack(anchor='w', pady=8)
        recipient = self.field(self.content, 'Destinatario')
        subject = self.field(self.content, 'Oggetto')
        body = self.field(self.content, 'Messaggio')
        def send():
            to, title, text = recipient.get().strip(), subject.get().strip(), body.get().strip()
            if not to or not title or not text:
                messagebox.showerror('Email', 'Completa destinatario, oggetto e messaggio.', parent=self.root)
                return
            prompt = f'Inviare questa email?\n\nA: {to}\nOggetto: {title}\n\n{text}'
            if messagebox.askyesno('Conferma invio', prompt, parent=self.root):
                self.submit(lambda: self.jarvis.email_action('email_send', to, title, text), show_result)
        ttk.Button(self.content, text='Invia email…', command=send).pack(anchor='w', pady=12)

    def _add_variable(self):
        name = simpledialog.askstring('Servizio', 'Nome della variabile, per esempio SERVICE_API_KEY:', parent=self.root)
        if not name:
            return
        value = simpledialog.askstring('Servizio', 'Valore:', show='●', parent=self.root)
        if value is not None:
            self.submit(lambda: self.jarvis.settings.save_secrets({name: value}), lambda _: self.show('Servizi'))

    def _profiles(self):
        self.title('Personalizzazione', 'Scegli uno stile di risposta e aggiungi le tue istruzioni al profilo.')
        cfg = self.jarvis._cfg
        actions = ttk.Frame(self.content)
        actions.pack(fill='x', pady=(0, 4))
        selected = self.field(self.content, 'Profilo', cfg['active_profile'], options=list(cfg['profiles']))
        name = self.field(self.content, 'Nome', self.jarvis.settings.profile()['name'])
        initial_tone = self.jarvis.settings.profile().get('tone', 'Bilanciato')
        tone = self.field(self.content, 'Stile delle risposte',
                          initial_tone if initial_tone in RESPONSE_TONES else 'Bilanciato',
                          options=list(RESPONSE_TONES))
        ttk.Label(self.content, text='Istruzioni personalizzate').pack(anchor='w', pady=(14, 6))
        persona = tk.Text(self.content, height=5, wrap='word', bg=self.surface, fg=INK, insertbackground=ACCENT, relief='flat', font=('Noto Sans', 11), padx=12, pady=10)
        persona.pack(fill='x')
        persona.insert('1.0', self.jarvis.settings.profile().get('personality', ''))
        def load(*_):
            profile = cfg['profiles'][selected.get()]
            name.set(profile['name'])
            style = profile.get('tone', 'Bilanciato')
            tone.set(style if style in RESPONSE_TONES else 'Bilanciato')
            persona.delete('1.0', 'end')
            persona.insert('1.0', profile.get('personality', ''))
        selected.trace_add('write', load)
        def save():
            key, display = selected.get(), name.get().strip()
            text, style = persona.get('1.0', 'end').strip(), tone.get()
            if not display:
                self.status.set('Inserisci il nome da usare nel profilo.')
                return
            def write():
                profile = {**cfg['profiles'][key], 'name': display, 'personality': text, 'tone': style}
                profiles = {**cfg['profiles'], key: profile}
                self.jarvis.settings.save(updates={'profiles': profiles, 'active_profile': key})
            self.submit(write, self._saved)
        def add():
            key = simpledialog.askstring('Nuovo profilo', 'Nome del profilo:', parent=self.root)
            if key and key not in cfg['profiles']:
                def write():
                    cfg['profiles'][key] = {'name': key, 'personality': ''}
                    self.jarvis.settings.save()
                self.submit(write, lambda _: self.show('Profili'))
        def delete():
            key = selected.get()
            if len(cfg['profiles']) == 1 or not messagebox.askyesno('Elimina profilo', 'Eliminare ' + key + '?', parent=self.root):
                return
            def write():
                del cfg['profiles'][key]
                if cfg['active_profile'] == key:
                    cfg['active_profile'] = next(iter(cfg['profiles']))
                self.jarvis.settings.save()
            self.submit(write, lambda _: self.show('Profili'))
        ttk.Button(actions, text='Salva e usa profilo', style='Primary.TButton',
                   command=save).pack(side='right')
        for label, fn in [('Nuovo profilo', add), ('Elimina', delete)]:
            ttk.Button(actions, text=label, style='Compact.TButton',
                       command=fn).pack(side='left', padx=(0, 6))
        ttk.Label(self.content, text='I profili personalizzano le risposte. L’accesso all’app usa gli utenti e i PIN esistenti.',
                  style='Hint.TLabel', wraplength=620).pack(anchor='w')
        ttk.Button(self.content, text='Gestisci utenti e PIN', command=self._users).pack(anchor='w', pady=16)

    def _users(self):
        self.jarvis.require_admin()
        window = tk.Toplevel(self.root)
        window.title('Utenti e PIN')
        window.geometry('540x430')
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        users = tk.Listbox(frame, font=('Noto Sans', 11), height=7, bg=self.surface, fg=INK, selectbackground=LINE, selectforeground=INK)
        users.pack(fill='both', expand=True)
        registry = self.jarvis.registry
        records = registry.list_users()
        for key, display, role in records:
            languages = registry.languages_of(key)
            label = f'{display} [{", ".join(languages)}]' if languages else display
            users.insert('end', f'{label} — {role}')
        def add():
            if not registry.has_admin:
                name = simpledialog.askstring('Amministratore', 'Nome amministratore:', parent=window)
                pin = simpledialog.askstring('Password', 'Scegli la password amministratore (almeno 6 caratteri):', show='●', parent=window)
                if name and pin and len(pin) >= 6:
                    ok, key = registry.init_admin(name, pin)
                    if ok:
                        self.jarvis.authenticate(pin)
                        window.destroy()
                        self._users()
                return
            pin = self._ask_credential(admin=True)
            if pin is None:
                return
            name = simpledialog.askstring('Nuovo account', f"Nome dell’utente · lingua {self.jarvis._cfg['lang']}:", parent=window)
            if not name:
                return
            try:
                key, credential = registry.create_generated_secondary(name, self.jarvis._cfg['lang'], pin)
            except (PermissionError, ValueError) as exc:
                messagebox.showerror('Account', str(exc), parent=window)
                return
            messagebox.showinfo('Account creato', f'Utente {name}\nPassword generata: {credential}\n\nAnnotala e consegnala all’utente. È salvato soltanto il suo hash.', parent=window)
            window.destroy()
            self._users()
        def delete():
            if users.curselection():
                key = records[users.curselection()[0]][0]
                if self._ask_credential(admin=True) is not None and messagebox.askyesno('Elimina utente', 'Eliminare l’utente selezionato?', parent=window):
                    registry.delete_user(key)
                    window.destroy()
                    self._users()
        def change_pin():
            if not users.curselection():
                return
            key = records[users.curselection()[0]][0]
            old = simpledialog.askstring('PIN', 'PIN attuale dell’utente:', show='●', parent=window)
            new = simpledialog.askstring('PIN', 'Nuovo PIN (almeno 6 caratteri):', show='●', parent=window)
            if old and new:
                ok, value = registry.change_pin(key, old, new)
                if ok:
                    messagebox.showinfo('PIN', 'PIN aggiornato.', parent=window)
                else:
                    messagebox.showerror('PIN', value, parent=window)
        ttk.Button(frame, text='Aggiungi utente', command=add).pack(side='left', pady=15)
        ttk.Button(frame, text='Elimina secondario', command=delete).pack(side='left', padx=8)
        ttk.Button(frame, text='Cambia PIN', command=change_pin).pack(side='left')

    def _voice(self):
        self.title('Voce', 'La modalità vocale crea il profilo del tuo account e ascolta automaticamente. La somiglianza minima è 0,50.')
        tts, stt = tk.BooleanVar(value=self.jarvis._cfg['tts']), tk.BooleanVar(value=self.jarvis._cfg['stt'])
        ttk.Checkbutton(self.content, text='Leggi le risposte ad alta voce', variable=tts).pack(anchor='w', pady=8)
        ttk.Checkbutton(self.content, text='Abilita il pulsante microfono', variable=stt).pack(anchor='w', pady=8)
        def save():
            values = {'tts': tts.get(), 'stt': stt.get()}
            def write():
                self.jarvis._cfg.update(values)
                if not values['tts'] and self.jarvis._tts_engine:
                    self.jarvis._tts_engine.stop()
                self.jarvis.settings.save()
            self.submit(write, self._saved)
        ttk.Button(self.content, text='Salva voce', command=save).pack(anchor='w', pady=18)
        ttk.Button(self.content, text='Verifica microfono', command=self._microphones).pack(anchor='w')
        ttk.Button(self.content, text='Registra di nuovo il profilo vocale', command=lambda:self._toggle_voice(force_enroll=True)).pack(anchor='w', pady=10)
        ttk.Label(self.content, text='Le dipendenze vocali sono opzionali. Installale con l’installer usando --voice.', style='Hint.TLabel', wraplength=620).pack(anchor='w', pady=18)

    def _save_microphone_selection(self, mic, on_chosen=None, monitor=None):
        def write():
            if monitor:
                monitor.wait()
            self.jarvis.settings.save(updates={'microphone': mic})
            if self.jarvis._voice_input:
                self.jarvis._voice_input.stop()
                self.jarvis._voice_input = None
        def saved(_):
            self.status.set('Microfono selezionato: ' + (mic.get('description') or mic.get('name', '')))
            if on_chosen:
                on_chosen()
        self.submit(write, saved)

    def _verify_microphones(self, mics, on_chosen):
        from voice_module import probe_microphones
        from audio_checks import confirmed_microphones
        window = tk.Toplevel(self.root)
        window.title('Verifica microfono')
        window.geometry('610x290')
        window.configure(bg=self.bg)
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Troviamo il microfono che ti sente',
                  font=('Noto Sans', 16, 'bold')).pack(anchor='w')
        message = tk.StringVar(value='Premi Avvia prova e parla normalmente per 5 secondi. Gli ingressi duplicati sono esclusi.')
        ttk.Label(frame, textvariable=message, wraplength=560).pack(fill='x', pady=16)
        progress = ttk.Progressbar(frame, mode='determinate', maximum=5)
        progress.pack(fill='x', pady=8)
        stopped = threading.Event()
        active = [False]
        def close():
            stopped.set()
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', close)
        window.bind('<Destroy>', lambda e:stopped.set() if e.widget is window else None)
        def start():
            nonlocal stopped
            stopped = threading.Event()
            active[0] = True
            button.state(['disabled'])
            started = time.monotonic()
            message.set('Parla ora, per esempio: «JARVIS, questa è la mia voce e sto provando il microfono».')
            def tick():
                if not window.winfo_exists() or stopped.is_set() or not active[0]:
                    return
                progress.configure(value=min(5, time.monotonic()-started))
                self.root.after(100, tick)
            tick()
            self.submit(lambda:probe_microphones(mics, stopped), checked)
        def checked(results):
            active[0] = False
            if not window.winfo_exists():
                return
            working = confirmed_microphones(results)
            if len(working) == 1:
                close()
                self._save_microphone_selection(working[0], on_chosen)
            elif len(working) > 1:
                close()
                self._microphones(on_chosen, verified=working)
            else:
                progress.configure(value=0)
                message.set('Non ho rilevato voce da nessun microfono durante la prova. '
                            'Controlla che il microfono sia attivo e parla mentre riprovi. '
                            'Nessun dispositivo è stato selezionato.')
                button.configure(text='Riprova parlando')
                button.state(['!disabled'])
        button = ttk.Button(frame, text='Avvia prova', style='Primary.TButton', command=start)
        button.pack(anchor='e', pady=10)
        window.bind('<Escape>', lambda _:close())

    def _microphones(self, on_chosen=None, verified=None):
        def scan():
            from voice_module import scan_microphones
            from audio_checks import physical_microphones
            return physical_microphones(scan_microphones())
        def pick(mics):
            if not mics:
                self.status.set('Nessun microfono disponibile. Controlla connessione e permessi audio.')
                return
            if verified is None:
                self._verify_microphones(mics, on_chosen)
                return
            from voice_module import MicrophoneMonitor
            window = tk.Toplevel(self.root)
            window.title('Scegli microfono')
            window.geometry('740x470')
            window.minsize(580, 310)
            window.configure(bg=self.bg)
            header = ttk.Frame(window, padding=(18, 16, 18, 10))
            header.pack(fill='x')
            ttk.Label(header, text='Quale microfono ti sente?', font=('Noto Sans', 16, 'bold')).pack(anchor='w')
            ttk.Label(header, text='Parla e osserva il livello accanto a ogni dispositivo.',
                      style='Hint.TLabel').pack(anchor='w', pady=(5, 0))
            container = ttk.Frame(window)
            container.pack(fill='both', expand=True, padx=18)
            canvas = tk.Canvas(container, bg=self.bg, highlightthickness=0)
            scroll = ttk.Scrollbar(container, command=canvas.yview)
            canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            canvas.pack(side='left', fill='both', expand=True)
            rows = ttk.Frame(canvas)
            content = canvas.create_window(0, 0, window=rows, anchor='nw')
            rows.bind('<Configure>', lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda event:canvas.itemconfigure(content, width=event.width))
            def wheel(event):
                direction = -1 if getattr(event, 'num', None) == 4 or getattr(event, 'delta', 0) > 0 else 1
                canvas.yview_scroll(direction * 3, 'units')
            for binding in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                window.bind(binding, wheel)
            current = self.jarvis._cfg.get('microphone')
            initial = next((i for i, mic in enumerate(mics) if mic == current), 0)
            selected = tk.IntVar(value=initial)
            meters = []
            for index, mic in enumerate(mics):
                row = tk.Frame(rows, bg=self.panel, highlightthickness=1, highlightbackground=LINE)
                row.pack(fill='x', pady=(0, 8))
                row.columnconfigure(0, weight=1)
                label = mic.get('description') or mic.get('name', 'Microfono')
                choice = tk.Radiobutton(row, text=label, variable=selected, value=index,
                                        bg=self.panel, fg=INK, activebackground=self.panel, activeforeground=INK,
                                        selectcolor=self.surface, font=('Noto Sans', 11),
                                        anchor='w', justify='left', wraplength=360, highlightthickness=0)
                choice.grid(row=0, column=0, sticky='w', padx=12, pady=14)
                choice.bind('<Configure>', lambda event, item=choice:item.configure(
                    wraplength=max(170, event.widget.master.winfo_width()-205)))
                instrument = tk.Frame(row, bg=self.panel)
                instrument.grid(row=0, column=1, padx=12, pady=8)
                level = tk.Canvas(instrument, width=132, height=16, bg=self.panel, highlightthickness=0)
                level.pack(anchor='e')
                segments = [level.create_rectangle(i*11, 2, i*11+8, 14, fill=LINE, outline='') for i in range(12)]
                signal = tk.StringVar(value='Connessione…')
                tk.Label(instrument, textvariable=signal, bg=self.panel, fg=MUTED,
                         font=('Noto Sans', 9)).pack(anchor='e', pady=(3, 0))
                meters.append((row, level, segments, signal))
            monitor = MicrophoneMonitor(mics)
            closed = False
            def close():
                nonlocal closed
                if closed:
                    return
                closed = True
                monitor.stop()
                if window.winfo_exists():
                    window.destroy()
            window.protocol('WM_DELETE_WINDOW', close)
            window.bind('<Destroy>', lambda event:monitor.stop() if event.widget is window else None)
            def update():
                if self.closed or closed or not window.winfo_exists():
                    monitor.stop()
                    return
                values = monitor.snapshot()
                for index, (row, meter, segments, signal) in enumerate(meters):
                    row.configure(highlightbackground=self.hud.accent if selected.get() == index else LINE)
                    value = values.get(index, 'waiting')
                    lit = 0
                    color = self.hud.accent
                    if value is None:
                        signal.set('Non disponibile')
                    elif value == 'waiting':
                        signal.set('Connessione…')
                    else:
                        dbfs, clipped, _ = value
                        lit = max(0, min(12, round((dbfs + 60) / 60 * 12)))
                        if clipped:
                            signal.set('Saturazione')
                            color = '#FF8A8A'
                        elif dbfs > -45:
                            signal.set('Segnale presente')
                        elif dbfs > -60:
                            signal.set('Segnale basso')
                        else:
                            signal.set('Quasi silenzioso')
                    for position, segment in enumerate(segments):
                        meter.itemconfigure(segment, fill=color if position < lit else LINE)
                self.root.after(100, update)
            def select():
                mic = mics[selected.get()]
                close()
                self._save_microphone_selection(mic, on_chosen, monitor)
            footer = ttk.Frame(window, padding=(18, 12))
            footer.pack(fill='x')
            ttk.Label(footer, text='Il livello viene misurato senza salvare l’audio.', style='Hint.TLabel').pack(side='left')
            ttk.Button(footer, text='Usa microfono', style='Primary.TButton', command=select).pack(side='right')
            window.bind('<Escape>', lambda _:close())
            monitor.start()
            update()
        if verified is None:
            self.submit(scan, pick)
        else:
            pick(verified)

    def _skills(self):
        self.title('Skill', 'Abilita le competenze che ti servono. Puoi importare istruzioni da un file SKILL.md.')
        self.skill_table = ttk.Treeview(self.content, columns=('name', 'state', 'description'), show='headings', height=7)
        for col, label, width in [('name', 'Skill', 160), ('state', 'Stato', 100), ('description', 'Descrizione', 330)]:
            self.skill_table.heading(col, text=label)
            self.skill_table.column(col, width=width, minwidth=80)
        self.skill_table.pack(fill='x', pady=12)
        def fill(skills):
            if self.page != 'Skill':
                return
            self.skill_table.delete(*self.skill_table.get_children())
            for item in skills:
                self.skill_table.insert('', 'end', iid=item['name'], values=(item['name'], 'Attiva' if item['enabled'] else 'Disattiva', item.get('description', '')))
            if self.jarvis.skills.errors:
                self.status.set('Errori skill: ' + str(self.jarvis.skills.errors))
        self.submit(self.jarvis.list_skills, fill)
        def toggle(enabled):
            names = self.skill_table.selection()
            if names:
                method = self.jarvis.enable_skill if enabled else self.jarvis.disable_skill
                self.submit(lambda: method(names[0]), lambda _: self.show('Skill'))
        def install():
            path = filedialog.askopenfilename(parent=self.root, title='Importa SKILL.md', filetypes=[('Skill', 'SKILL.md')])
            if path:
                self.submit(lambda: self.jarvis.skills.install_markdown(path), lambda _: self.show('Skill'))
        row = ttk.Frame(self.content)
        row.pack(fill='x', pady=12)
        for label, fn in [('Abilita', lambda: toggle(True)), ('Disabilita', lambda: toggle(False)),
                          ('Configura', self._skill_config), ('Importa skill', install),
                          ('Ricarica', lambda: self.submit(self.jarvis.skills.reload, lambda _: self.show('Skill')))]:
            ttk.Button(row, text=label, command=fn).pack(side='left', padx=(0, 6))

    def _skill_config(self):
        names = self.skill_table.selection()
        if not names:
            return
        name = names[0]
        schema = self.jarvis.skills.config_schema(name)
        window = tk.Toplevel(self.root)
        window.title(name)
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill='both', expand=True)
        saved = self.jarvis._cfg['skills'].get(name, {}).get('config', {})
        fields = {key: self.field(frame, info.get('label', key), saved.get(key, info.get('default', '')),
            secret=info.get('type') == 'password', options=info.get('options')) for key, info in schema.items()}
        if not fields:
            ttk.Label(frame, text='Questa skill non richiede configurazione.').pack()
        def save():
            values = {k: v.get() for k, v in fields.items()}
            for key, info in schema.items():
                if info.get('required') and not values[key]:
                    messagebox.showerror('Skill', 'Completa: ' + info.get('label', key), parent=window)
                    return
            self.submit(lambda: self.jarvis.enable_skill(name, values), lambda _: self.show('Skill'))
            window.destroy()
        ttk.Button(frame, text='Salva e abilita', command=save).pack(pady=18)

    def _mcp(self):
        self.title('Server MCP', 'Collega server locali tramite stdio. Si avviano soltanto quando richiedi gli strumenti.')
        table = ttk.Treeview(self.content, columns=('server', 'state', 'command'), show='headings', height=7)
        for col, label in [('server', 'Server'), ('state', 'Stato'), ('command', 'Eseguibile')]:
            table.heading(col, text=label)
            table.column(col, width=180)
        table.pack(fill='x', pady=12)
        for name, spec in self.jarvis._cfg['mcp_servers'].items():
            table.insert('', 'end', iid=name, values=(name, 'Abilitato' if spec.get('enabled') else 'Disabilitato', spec.get('command')))
        def selected():
            return table.selection()[0] if table.selection() else None
        row = ttk.Frame(self.content)
        row.pack(fill='x', pady=12)
        for label, fn in [('Aggiungi', lambda: self._mcp_edit()), ('Modifica', lambda: self._mcp_edit(selected()) if selected() else None),
            ('Strumenti', lambda: self._mcp_tools(selected()) if selected() else None),
            ('Rimuovi', lambda: self._mcp_remove(selected()) if selected() else None)]:
            ttk.Button(row, text=label, command=fn).pack(side='left', padx=(0, 8))

    def _mcp_edit(self, name=None):
        spec = self.jarvis._cfg['mcp_servers'].get(name, {})
        window = tk.Toplevel(self.root)
        window.title('Configurazione MCP stdio')
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill='both', expand=True)
        server = self.field(frame, 'Nome server (senza spazi)', name or '')
        command = self.field(frame, 'Eseguibile', spec.get('command', ''))
        args = self.field(frame, 'Argomenti (array JSON)', json.dumps(spec.get('args', [])))
        env = self.field(frame, 'Variabili ambiente (oggetto JSON)', json.dumps(spec.get('env', {})), secret=True)
        enabled = tk.BooleanVar(value=spec.get('enabled', False))
        ttk.Checkbutton(frame, text='Abilitato', variable=enabled).pack(anchor='w', pady=12)
        def save():
            try:
                key = server.get().strip()
                arguments, environment = json.loads(args.get()), json.loads(env.get())
                if not key or any(c.isspace() for c in key) or not command.get().strip():
                    raise ValueError('Indica nome senza spazi ed eseguibile')
                if key != name and key in self.jarvis._cfg['mcp_servers']:
                    raise ValueError('Nome server già utilizzato')
                if not isinstance(arguments, list) or not all(isinstance(v, str) for v in arguments):
                    raise ValueError('Gli argomenti devono essere un array di stringhe')
                if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in environment.items()):
                    raise ValueError('Le variabili devono essere un oggetto di stringhe')
                value = {'command': command.get().strip(), 'args': arguments, 'env': environment, 'enabled': enabled.get()}
            except (ValueError, TypeError) as exc:
                messagebox.showerror('Configurazione MCP', str(exc), parent=window)
                return
            def write():
                if name:
                    self.jarvis.disconnect_mcp(name)
                    self.jarvis._cfg['mcp_servers'].pop(name)
                self.jarvis._cfg['mcp_servers'][key] = value
                self.jarvis.settings.save()
            self.submit(write, lambda _: self.show('MCP'))
            window.destroy()
        ttk.Button(frame, text='Salva server', command=save).pack(anchor='w', pady=12)

    def _mcp_remove(self, name):
        if messagebox.askyesno('Rimuovi MCP', 'Rimuovere ' + name + '?', parent=self.root):
            def remove():
                self.jarvis.disconnect_mcp(name)
                del self.jarvis._cfg['mcp_servers'][name]
                self.jarvis.settings.save()
            self.submit(remove, lambda _: self.show('MCP'))

    def _mcp_tools(self, name):
        def show(tools):
            window = tk.Toplevel(self.root)
            window.title('Strumenti — ' + name)
            window.geometry('680x620')
            frame = ttk.Frame(window, padding=20)
            frame.pack(fill='both', expand=True)
            selected = self.field(frame, 'Strumento', tools[0]['name'] if tools else '', options=[t['name'] for t in tools])
            schema = tk.Text(frame, height=12, wrap='word', bg=self.surface, fg=INK, insertbackground=ACCENT, font=('Noto Sans', 10))
            schema.pack(fill='both', expand=True, pady=12)
            def describe(*_):
                tool = next((t for t in tools if t['name'] == selected.get()), {})
                schema.configure(state='normal')
                schema.delete('1.0', 'end')
                schema.insert('1.0', tool.get('description', '') + '\n\n' + json.dumps(tool.get('inputSchema', {}), indent=2, ensure_ascii=False))
                schema.configure(state='disabled')
            selected.trace_add('write', describe)
            describe()
            arguments = self.field(frame, 'Argomenti (oggetto JSON)', '{}')
            def call():
                try:
                    payload = json.loads(arguments.get())
                    if not isinstance(payload, dict):
                        raise ValueError('Gli argomenti devono essere un oggetto')
                except ValueError as exc:
                    messagebox.showerror('Argomenti MCP', str(exc), parent=window)
                    return
                self._answer('__MCP_CONFIRM__' + json.dumps({'server': name, 'tool': selected.get(), 'arguments': payload}))
            ttk.Button(frame, text='Esegui strumento', command=call).pack(anchor='w', pady=12)
        self.submit(lambda: self.jarvis.mcp_client(name).connect(), show)

    def _commands(self):
        self.title('Comandi', 'I pulsanti usano gli stessi comandi disponibili nella conversazione.')
        capabilities=self._settings_section('Chiedi un compito in linguaggio naturale',
            'Esempio: «Esegui il test nel file teast per jarvis.txt sulla Scrivania». JARVIS legge il file, usa gli strumenti e chiede conferma prima delle modifiche.')
        tk.Label(capabilities,text='Strumenti: dati del PC; ricerca, lettura e creazione di file; calcoli; comandi Linux; web; memoria; posta e MCP quando configurati. Il ruolo e il Guardiano valgono per ogni modello.',
            bg=self.panel,fg=MUTED,font=('Noto Sans',10),wraplength=650,justify='left',anchor='w').pack(fill='x')
        for label, command in [('Informazioni PC', 'pc'), ('Trova test sulla Scrivania', 'trova test sulla scrivania'),
            ('Controlla strumenti disponibili','strumenti'),
            ('Aiuto e comandi', 'aiuto'), ('Mostra memoria', 'memoria'), ('Statistiche', 'token'),
            ('Lista file', 'ls'), ('Elenca skill', 'skill'), ('Elenca server MCP', 'mcp'), ('Pausa ascolto', 'standby')]:
            ttk.Button(self.content, text=label, command=lambda c=command: self._send_from_settings(c)).pack(anchor='w', pady=4)
        command = self.field(self.content, 'Comando interno o run <comando di sistema>')
        ttk.Button(self.content, text='Esegui comando', command=lambda: self._send_from_settings(command.get())).pack(anchor='w', pady=12)
        text = self.field(self.content, 'Nuovo ricordo')
        ttk.Button(self.content, text='Salva ricordo', command=lambda: self._send_from_settings('ricorda ' + text.get())).pack(anchor='w', pady=12)
        search = self.field(self.content, 'Ricerca web')
        ttk.Button(self.content, text='Cerca sul web', command=lambda: self._send_from_settings('cerca online ' + search.get())).pack(anchor='w', pady=12)
        def change_directory():
            path = filedialog.askdirectory(parent=self.root, title='Directory di lavoro')
            if path:
                self._send_from_settings('cd ' + path)
        ttk.Button(self.content, text='Cambia directory', command=change_directory).pack(anchor='w', pady=12)
        remote_host = self.field(self.content, 'Host SSH configurato')
        remote_cmd = self.field(self.content, 'Comando remoto')
        ttk.Button(self.content, text='Esegui su host SSH', command=lambda: self._send_from_settings('ssh ' + remote_host.get() + ' ' + remote_cmd.get())).pack(anchor='w', pady=12)
        pair = self.field(self.content, 'Lingue traduttore', 'it en')
        ttk.Button(self.content, text='Avvia traduttore', command=lambda: self._send_from_settings('traduttore ' + pair.get())).pack(anchor='w', pady=12)
        ttk.Button(self.content, text='Esci da JARVIS', command=self.close).pack(anchor='w', pady=12)

    def _send_from_settings(self, command):
        self.show('Conversazione')
        self.send(command)

    def _maintenance(self):
        self.title('Manutenzione', 'Aggiorna dal progetto scaricato o ricrea l’ambiente Python conservando chiavi, utenti e impostazioni.')
        ttk.Button(self.content, text='Aggiorna da Scaricati/code', command=lambda: self._install('import')).pack(anchor='w', pady=8)
        ttk.Button(self.content, text='Reinstalla da zero', command=lambda: self._install('reinstall')).pack(anchor='w', pady=8)
        ttk.Button(self.content, text='Installa voce e visione', command=lambda: self._install('reinstall', voice=True, vision=True)).pack(anchor='w', pady=8)
        ttk.Button(self.content, text='Controlla codice e duplicati', command=self._audit).pack(anchor='w', pady=8)
        ttk.Label(self.content, text='L’installer crea un backup prima di sostituire il codice. Riavvia l’app dopo l’installazione.',
            style='Hint.TLabel', wraplength=620).pack(anchor='w', pady=18)

    def _install(self, mode, voice=False, vision=False):
        source = None
        if mode == 'import':
            source = filedialog.askdirectory(parent=self.root, title='Progetto in Scaricati/code')
            if not source:
                return
        if not messagebox.askyesno('Installazione JARVIS', 'Procedere? Il codice o l’ambiente Python verranno ricreati. Credenziali e dati saranno conservati.', parent=self.root):
            return
        def install():
            from installer import install_project
            return str(install_project(mode=mode, source=Path(source) if source else None, destination=Path(__file__).parent, voice=voice, vision=vision))
        self.submit(install, lambda result: messagebox.showinfo('Installazione', result + '\nRiavvia JARVIS.', parent=self.root))

    def _audit(self):
        def audit():
            from project_audit import audit_project
            return audit_project(Path(__file__).parent)
        self.submit(audit, lambda report: self.append('Verifica progetto', json.dumps(report, ensure_ascii=False, indent=2)))

    def run(self):
        # No modal dialogs or grabs before the event loop. Authentication is an
        # ordinary page, so it can never hide behind and disable the main window.
        self.root.mainloop()

    def close(self):
        if self.closed:
            return
        self._leave_voice()
        self._hide_floating_core()
        self._desktop_visibility.close()
        if self._satellite:
            self._satellite.close()
        if self.jarvis._tts_engine:
            self.jarvis._tts_engine.stop()
        self.jarvis.cancel_current()
        self.closed = True
        # Shutdown waits behind current work; no Tk calls from worker threads.
        self.jobs.put((self.jarvis._shutdown, None))
        self.jobs.put(None)
        for token in self.root.tk.splitlist(self.root.tk.call('after','info')):
            # Let each widget delete its own Python callbacks during destruction.
            self.root.tk.call('after','cancel',token)
        self.root.destroy()

    def _reset_chat_view(self):
        self.transcript.configure(state='normal');self.transcript.delete('1.0','end')
        self.transcript.insert('end','\n\nCosa facciamo oggi?\n','welcome')
        self.transcript.insert('end','Scrivi un comando o attiva il microfono.\n','welcome_hint')
        self.transcript.configure(state='disabled');self._welcome=True

    def _new_chat(self):
        if self.busy or self.jarvis.agent_task_active:
            self.status.set('Completa o interrompi il compito prima di aprire una nuova chat.');return
        if self.jarvis.role=='locked':self.show_login();return
        self.jarvis.new_conversation();self._reset_chat_view();self.show('Conversazione')
        self._route_notice=None;self.activity_text.set('Strumenti locali pronti');self.activity_entries=[]
        self.entry.delete(0,'end')
        self.status.set('Nuova chat pronta')

    def _refresh_chats(self):
        if self.jarvis.role=='locked':
            self.chat_list.delete(0,'end');self.chat_ids=[];return
        try:chats=self.jarvis.conversations(self.chat_search.get())
        except Exception as exc:self.status.set('Archivio chat non disponibile: '+str(exc));return
        self.chat_list.delete(0,'end');self.chat_ids=[]
        self._chat_records={chat['id']:chat for chat in chats}
        for chat in chats:
            self.chat_ids.append(chat['id']);self.chat_list.insert('end',('★ ' if chat['pinned'] else '')+chat['title'])
            if chat['id']==self.jarvis._chat_id:self.chat_list.selection_set(len(self.chat_ids)-1)
        self._update_chat_actions()

    def _queue_chat_search(self):
        if self._chat_search_after:self.root.after_cancel(self._chat_search_after)
        self._chat_search_after=self.root.after(180,self._apply_chat_search)

    def _apply_chat_search(self):
        self._chat_search_after=None;self._refresh_chats()

    def _selected_chat_id(self):
        selected=self.chat_list.curselection()
        return self.chat_ids[selected[0]] if selected and selected[0]<len(self.chat_ids) else None

    def _update_chat_actions(self):
        chat_id=self._selected_chat_id()
        allowed=bool(chat_id and self.jarvis.role!='locked' and not self.busy and not self.jarvis.agent_task_active)
        pinned=getattr(self,'_chat_records',{}).get(chat_id,{}).get('pinned',False)
        self.pin_chat_button.configure(state='normal' if allowed else 'disabled',text='★ Rimuovi pin' if pinned else '☆ Fissa')
        self.delete_chat_button.configure(state='normal' if allowed else 'disabled')

    def _pin_chat(self):
        chat_id=self._selected_chat_id()
        if not chat_id or self.busy:return
        try:self.jarvis.pin_conversation(chat_id,not self._chat_records[chat_id]['pinned'])
        except Exception as exc:self.status.set(str(exc));return
        self._refresh_chats()

    def _delete_chat(self):
        chat_id=self._selected_chat_id()
        if not chat_id or self.busy:return
        try:self.jarvis.delete_conversation(chat_id)
        except Exception as exc:self.status.set(str(exc));return
        if self._view_chat_id==chat_id:
            self._reset_chat_view();self.entry.delete(0,'end');self._view_chat_id=None
            self.activity_entries=[];self.activity_text.set('Strumenti locali pronti')
        if not self.undo_chat_button.winfo_manager():self.undo_chat_button.pack(fill='x',pady=(0,6))
        self._refresh_chats();self.status.set('Chat eliminata · puoi annullare l’ultima eliminazione durante questa sessione')

    def _undo_delete_chat(self):
        if self.busy:return
        try:self.jarvis.undo_delete_conversation()
        except Exception as exc:self.status.set(str(exc));return
        self.undo_chat_button.pack_forget();self._refresh_chats();self.status.set('Chat ripristinata')

    def _select_chat(self,event=None):
        selected=self.chat_list.curselection()
        if not selected:return
        if self.busy or self.jarvis.agent_task_active:
            self.status.set('Completa o interrompi il compito prima di cambiare chat.');return
        chat_id=self.chat_ids[selected[0]]
        if chat_id==self.jarvis._chat_id:return
        try:messages=self.jarvis.open_conversation(chat_id)
        except Exception as exc:self.status.set('Impossibile aprire la chat: '+str(exc));return
        self._reset_chat_view()
        for message in messages:self.append(message['speaker'],message['text'],persist=False)
        self.activity_entries=['Risultato conservato: '+m.get('name','strumento') for m in self.jarvis._history if m['role']=='tool'][-200:]
        self.activity_text.set('Chat ripresa · i risultati precedenti sono conservati')
        self.entry.delete(0,'end');self.show('Conversazione')
        self.status.set('Chat ripresa · scrivi per continuare')

    def _settings_section(self, title, description=''):
        card=tk.Frame(self.content,bg=self.panel);card.pack(fill='x',pady=(0,18))
        tk.Label(card,text=title,bg=self.panel,fg=INK,font=('Noto Sans',14,'bold'),anchor='w').pack(fill='x',padx=24,pady=(20,5))
        if description:
            hint=tk.Label(card,text=description,bg=self.panel,fg=MUTED,font=('Noto Sans',10),wraplength=760,justify='left',anchor='w')
            hint.pack(fill='x',padx=24,pady=(0,12))
            hint.bind('<Configure>',lambda e:hint.configure(wraplength=max(150,e.width)))
        body=tk.Frame(card,bg=self.panel);body.pack(fill='x',padx=24,pady=(8,24))
        return body

    def _settings_field(self,parent,label,variable,options=None,secret=False,editable=False):
        frame=tk.Frame(parent,bg=self.panel)
        caption=tk.Label(frame,text=label,bg=self.panel,fg=MUTED,font=('Noto Sans',10),anchor='w',justify='left',wraplength=650)
        caption.pack(fill='x',pady=(0,7))
        caption.bind('<Configure>',lambda e:caption.configure(wraplength=max(150,e.width)))
        if options is not None:
            widget=ttk.Combobox(frame,textvariable=variable,values=options,state='normal' if editable else 'readonly')
        else:widget=ttk.Entry(frame,textvariable=variable,show='●' if secret else '')
        widget.configure(width=16)
        widget.pack(fill='x');return frame,widget

    def _advanced_services(self):
        window=tk.Toplevel(self.root);window.title('Integrazioni e variabili');window.geometry('680x620');window.configure(bg=self.bg)
        canvas=tk.Canvas(window,bg=self.bg,highlightthickness=0);canvas.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(window,command=canvas.yview);bar.pack(side='right',fill='y');canvas.configure(yscrollcommand=bar.set)
        frame=ttk.Frame(canvas,padding=24);item=canvas.create_window(0,0,window=frame,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        frame.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        def scroll(event):
            direction=-1 if getattr(event,'num',None)==4 or getattr(event,'delta',0)>0 else 1
            canvas.yview_scroll(direction*3,'units')
        for binding in ('<MouseWheel>','<Button-4>','<Button-5>'):window.bind(binding,scroll)
        ttk.Label(frame,text='Integrazioni',style='Title.TLabel').pack(anchor='w',pady=(0,12))
        from api_key_pool import is_pool_secret
        fields={name:self.field(frame,name,self.jarvis.settings.secrets.get(name,''),secret=True) for name in sorted(set(SERVICE_FIELDS)|set(self.jarvis.settings.secrets)) if not is_pool_secret(name)}
        def save():
            values={name:var.get() for name,var in fields.items()}
            def write():self.jarvis.settings.save_secrets(values);self.jarvis.reset_provider_health()
            self.submit(write,lambda _:(self._saved(),self.show('Servizi')));window.destroy()
        ttk.Button(frame,text='Salva integrazioni',style='Primary.TButton',command=save).pack(anchor='w',pady=24)
        ttk.Button(frame,text='Aggiungi variabile',command=self._add_variable).pack(anchor='w')

