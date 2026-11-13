"""Vision settings, explicit image requests and bounding-box previews."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from vision_runtime import VISION_MODELS


class VisionUIMixin:
    def _vision_page(self):
        self.title('Visione', 'Il browser di JARVIS usa il proprio cursore e la propria tastiera. Ogni azione passa dal Guardiano.')
        ttk.Button(self.content, text='Prova il cursore JARVIS', style='Primary.TButton', command=lambda:
                   self.submit(lambda:self.jarvis.vision().cursor_demo(),
                               lambda result:self.status.set(result['description']))).pack(anchor='w', pady=(0,14))
        provider = self.field(self.content, 'Provider preferito', self.jarvis._cfg.get('vision_provider', 'nvidia'),
                              options=list(VISION_MODELS))
        fields = {key: self.field(self.content, f'Modello vision · {key}',
                                  self.jarvis._cfg.get('vision_models', {}).get(key, model))
                  for key, model in VISION_MODELS.items()}
        def save():
            updates = {'vision_provider': provider.get(), 'vision_models': {k:v.get().strip() for k,v in fields.items()}}
            self.submit(lambda:self.jarvis.settings.save(updates=updates), self._saved)
        ttk.Button(self.content, text='Salva modelli vision', style='Primary.TButton', command=save).pack(anchor='w', pady=12)
        ttk.Label(self.content, text='Le immagini vengono inviate al provider configurato quando chiedi un’analisi. Le API si gestiscono in Modelli e servizi.',
                  style='Hint.TLabel', wraplength=540).pack(fill='x', pady=(0, 12))
        url = self.field(self.content, 'Apri una pagina nel browser JARVIS', 'https://example.org')
        ttk.Button(self.content, text='Apri browser JARVIS', command=lambda:self._vision_action(
            lambda:self.jarvis.vision().open(url.get().strip()))).pack(anchor='w', pady=8)
        query = self.field(self.content, 'Elemento o oggetto da individuare', 'pulsante di ricerca')
        ttk.Button(self.content, text='Individua nel browser', command=lambda:self._vision_action(
            lambda:self.jarvis.vision().locate(query.get()))).pack(anchor='w', pady=8)
        ttk.Button(self.content, text='Individua sul desktop', command=lambda:self._vision_action(
            lambda:self.jarvis.vision().locate(query.get(), source='desktop'))).pack(anchor='w', pady=8)
        devices = sorted(str(p) for p in Path('/dev').glob('video[0-9]*'))
        device = self.field(self.content, 'Telecamera', devices[0] if devices else '/dev/video0', options=devices or None)
        ttk.Button(self.content, text='Acquisisci e individua nella telecamera', command=lambda:self._vision_action(
            lambda:self.jarvis.vision().locate(query.get(), source='camera', device=device.get()))).pack(anchor='w', pady=8)
        ttk.Label(self.content, text='Controllo separato delle app native su Wayland · richiede supporto del compositor. Il browser JARVIS è già indipendente dal puntatore fisico.',
                  style='Hint.TLabel', wraplength=540).pack(fill='x', pady=12)

    def _vision_action(self, function):
        self.jarvis.require_admin()
        if not messagebox.askyesno('Visione', 'Avviare questa operazione visiva?', parent=self.root):
            return
        self.submit(function, lambda result:self.status.set(result.get('description') or 'Operazione visiva completata.'))

    def _show_vision_result(self, result):
        window = tk.Toplevel(self.root)
        window.title('JARVIS · ' + result['source'])
        window.configure(bg=self.bg)
        image = tk.PhotoImage(master=window, file=result['image'])
        factor = max(1, (result['width'] + 899) // 900, (result['height'] + 599) // 600)
        image = image.subsample(factor)
        canvas = tk.Canvas(window, width=image.width(), height=image.height(), highlightthickness=0, bg=self.bg)
        canvas.pack(fill='both', expand=True)
        canvas._vision_image = image
        canvas.create_image(0, 0, image=image, anchor='nw')
        if result['found']:
            bbox = [v/factor for v in result['bbox']]
            canvas.create_rectangle(*bbox, outline=self.hud.accent, width=3)
            canvas.create_text(bbox[0], max(12, bbox[1]-6), anchor='sw', fill=self.hud.accent,
                               text=result.get('description',''), font=('Noto Sans', 11, 'bold'))
        ttk.Label(window, text=result.get('description','Elemento non individuato.'), wraplength=850).pack(fill='x', padx=12, pady=8)
        if result['source'] == 'browser' and result['found']:
            row = ttk.Frame(window, padding=10)
            row.pack(fill='x')
            def action(kind, **args):
                if self.jarvis.execution_role != 'admin':
                    self.status.set('Autorizza l’azione dal Guardiano nella conversazione.')
                    return
                if not messagebox.askyesno('Guardiano', 'Eseguire questa azione sul controllo individuato?', parent=window):
                    return
                self.submit(lambda:self.jarvis.vision().act(result['source_id'], kind, **args),
                            lambda _:self.status.set('Azione eseguita. Individua di nuovo il prossimo controllo.'))
                window.destroy()
            ttk.Button(row, text='Click sinistro', command=lambda:action('click')).pack(side='left')
            ttk.Button(row, text='Sposta cursore', command=lambda:action('move')).pack(side='left', padx=6)
            ttk.Button(row, text='Click destro', command=lambda:action('click', button='right')).pack(side='left', padx=6)
            ttk.Button(row, text='Scorri', command=lambda:action('scroll')).pack(side='left')
