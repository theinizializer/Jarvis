"""Discord settings and connection diagnostics for the v10 desktop."""
import tkinter as tk
from tkinter import ttk


class DiscordUIMixin:
    def _discord_page(self):
        self.title('Discord','Collega il bot e usa un messaggio diretto o una menzione @JARVIS.')
        settings=self.jarvis.settings
        token=self.field(self.content,'Token del bot',settings.secret('DISCORD_TOKEN'),secret=True)
        channel=self.field(self.content,'ID canale consentito · facoltativo',settings.secret('DISCORD_CHANNEL_ID'))
        users=self.field(self.content,'ID utenti autorizzati · separati da virgole',
                         ', '.join(str(value) for value in self.jarvis._cfg.get('discord_allowed_users',[])))
        ttk.Label(self.content,text='Se lasci vuoti gli utenti, risponde solo al proprietario dell’app Discord. Le chat Discord restano separate dalla conversazione sul PC.',
                  style='Hint.TLabel',wraplength=600).pack(fill='x',pady=8)
        all_messages=tk.BooleanVar(value=self.jarvis._cfg.get('discord_all_channel_messages',False))
        ttk.Checkbutton(self.content,text='Rispondi a tutti i miei messaggi nel canale indicato · richiede Message Content Intent',
                        variable=all_messages).pack(anchor='w',pady=8)
        auto=tk.BooleanVar(value=self.jarvis._cfg.get('discord_enabled',False))
        ttk.Checkbutton(self.content,text='Avvia il bot al mio accesso a JARVIS',variable=auto).pack(anchor='w',pady=8)
        runtime=self.jarvis.discord_service()
        if not hasattr(self,'_discord_status'):self._discord_status=tk.StringVar(value=runtime.message)
        ttk.Label(self.content,textvariable=self._discord_status,wraplength=600).pack(fill='x',pady=12)
        def save():
            self.jarvis.require_admin()
            identifiers=[value.strip() for value in users.get().split(',') if value.strip()]
            if any(not value.isdigit() for value in identifiers) or (channel.get().strip() and not channel.get().strip().isdigit()):
                self._discord_status.set('Gli ID Discord devono contenere solo cifre.');return False
            if all_messages.get() and not channel.get().strip():
                self._discord_status.set('Indica un canale per attivare le risposte a tutti i messaggi.');return False
            settings.save_secrets({'DISCORD_TOKEN':token.get().strip(),'DISCORD_CHANNEL_ID':channel.get().strip()})
            settings.save(updates={'discord_allowed_users':identifiers,'discord_all_channel_messages':all_messages.get(),
                                  'discord_enabled':auto.get()})
            self.jarvis._discord_requested=auto.get()
            self._discord_status.set('Configurazione salvata. Riavvia il bot per applicarla.')
            return True
        def start():
            if save():self._discord_status.set(runtime.start())
        row=ttk.Frame(self.content)
        row.pack(fill='x',pady=12)
        ttk.Button(row,text='Salva configurazione',command=save).pack(side='left')
        ttk.Button(row,text='Avvia bot',style='Primary.TButton',command=start).pack(side='left',padx=8)
        ttk.Button(row,text='Ferma bot',command=runtime.stop).pack(side='left')
        ttk.Label(self.content,text='Il bot usa permessi ridotti. Mail, file e operazioni che richiedono approvazione restano nell’app locale. Gli errori di token, connessione e intent compaiono qui.',
                  style='Hint.TLabel',wraplength=600).pack(fill='x',pady=8)
