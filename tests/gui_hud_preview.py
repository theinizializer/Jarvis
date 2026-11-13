"""Render the real HUD with synthetic state; capture only this app's windows."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import traceback
from unittest.mock import patch
from tkinter import ttk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis, PROVIDERS
from jarvis_app import JarvisApp, PrismWheel
from jarvis_hud import HUD_THEMES

with tempfile.TemporaryDirectory(prefix='jarvis-hud-check-') as tmp:
    home=Path(tmp)
    with patch('pathlib.Path.home',return_value=home),patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}):
        settings=Settings(home/'config');settings.secrets={}
        settings.values.update(provider='groq',model=PROVIDERS['groq'][2])
        host=Jarvis(settings=settings);app=JarvisApp(host)
        app.root.geometry('1240x820')
        output=Path(os.environ.get('JARVIS_TEST_ARTIFACTS', ROOT/'artifacts'));output.mkdir(exist_ok=True)
        errors=[];frames=[]
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def capture(widget,path):
            app.root.update_idletasks()
            r=subprocess.run(['import','-window',str(widget.winfo_id()),str(path)],capture_output=True,text=True,timeout=10)
            assert r.returncode==0,r.stderr
        def guard(fn):
            try:fn()
            except Exception:
                errors.append(traceback.format_exc());app.close()
        def start():
            hud=app.hud;c=hud.canvas;now=time.monotonic()
            item_count = len(c.items) if hasattr(c, 'items') else len(c.find_all())
            first = c.coords(hud.wave)
            hud.tick(now + .1, 'working', 'full')
            assert c.coords(hud.wave) != first
            assert (len(c.items) if hasattr(c, 'items') else len(c.find_all())) == item_count, 'Canvas items grew during animation'
            hud.tick(now, 'listening', 'reduced', .1)
            quiet = c.coords(hud.wave)
            hud.tick(now, 'listening', 'reduced', .9)
            assert c.coords(hud.wave) != quiet
            hud.tick(now, 'approval', 'reduced')
            before = c.coords(hud.wave)
            hud.tick(now + 2, 'approval', 'reduced')
            assert c.coords(hud.wave) == before
            assert c.itemcget(hud.status, 'text') == 'Autorizzazione richiesta'
            hud.tick(now, 'listening', 'off', .1)
            before = c.coords(hud.wave)
            hud.tick(now + 4, 'listening', 'off', .9)
            assert c.coords(hud.wave) == before
            started = time.perf_counter()
            for frame in range(120):
                hud.tick(now + frame / 60, 'executing')
                app.root.update_idletasks()
            print(f'Canvas aggiornamento e flush: {(time.perf_counter()-started)*1000/120:.2f} ms/frame')
            host._cfg['motion']='full';app.busy=1
            app.activity_text.set('Anteprima del nucleo durante l’elaborazione')
            app.root.after(150,lambda:guard(lambda:record(0)))
        def record(index):
            path=home/f'frame-{index}.png'
            capture(app.core_canvas,path)
            frames.append(str(path))
            if index==0:capture(app.root,output/'jarvis-hud-working.png')
            if index<15:
                app.root.after(100,lambda:guard(lambda:record(index+1)))
            else:
                subprocess.run(['convert', '-delay', '10', '-loop', '0', *frames,
                                str(output/'jarvis-hud-animation.gif')], check=True, timeout=10)
                app.busy=0;app.status.set('Pronto');app.show('Servizi');app.root.after(150,lambda:guard(services))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def services():
            combos=[w for w in descendants(app.content) if isinstance(w,ttk.Combobox)]
            selector=next(w for w in combos if 'ollama' in w.cget('values'))
            model=next(w for w in combos if str(w.cget('state'))=='normal')
            selector.set('nvidia');assert model.get()==PROVIDERS['nvidia'][2]
            model.set('custom-nvidia');selector.set('cerebras')
            assert model.get()==PROVIDERS['cerebras'][2]
            selector.set('nvidia');assert model.get()=='custom-nvidia'
            model.set(PROVIDERS['nvidia'][2])
            app.root.after(200,lambda:guard(services_capture))
        def services_capture():
            capture(app.root,output/'jarvis-services-nvidia.png')
            app.show('Aspetto');app.root.geometry('850x620')
            app.root.after(180,lambda:guard(finish))
        def finish():
            assert app.nav['Aspetto'].winfo_ismapped() and app.nav['Aspetto'].instate(['selected'])
            theme = next(w for w in descendants(app.content)
                         if isinstance(w, ttk.Radiobutton) and w.cget('text') == 'Viola orbitale')
            theme.invoke()
            capture(app.root, output/'jarvis-appearance-small.png')
            save = next(w for w in descendants(app.content)
                        if isinstance(w, ttk.Button) and w.cget('text') == 'Salva aspetto')
            assert save.winfo_ismapped()
            save.invoke()
            app.root.after(250, lambda:guard(personalization))

        def personalization():
            assert Settings(settings.directory).values['hud_theme'] == 'Viola orbitale'
            assert app.hud.accent == HUD_THEMES['Viola orbitale'][0]
            wheel = next(w for w in descendants(app.content) if isinstance(w, PrismWheel))
            wheel.event_generate('<Button-1>', x=29, y=31)
            light, glow = (w for w in descendants(app.content) if isinstance(w, ttk.Scale))
            light.set(63)
            glow.set(84)
            target = next(w for w in descendants(app.content)
                          if isinstance(w, ttk.Radiobutton) and w.cget('text') == 'Sfondo app')
            target.invoke()
            wheel.event_generate('<Button-1>', x=50, y=104)
            light.set(24)
            app.root.after(120, lambda:guard(save_custom_color))

        def save_custom_color():
            capture(app.root, output/'jarvis-prism-small.png')
            save = next(w for w in descendants(app.content)
                        if isinstance(w, ttk.Button) and w.cget('text') == 'Salva aspetto')
            save.invoke()
            app.root.after(250, lambda:guard(custom_color_saved))

        def custom_color_saved():
            config = Settings(settings.directory).values
            assert config['hud_theme'] == 'Personalizzato'
            assert config['hud_color'] != '#68DBFF'
            assert max(int(config['hud_color'][i:i + 2], 16) for i in (1, 3, 5)) == round(.63 * 255)
            assert config['hud_glow'] == 84
            assert app.hud.glow_strength == .84
            assert config['ui_background'] != '#050911'
            assert max(int(config['ui_background'][i:i + 2], 16) for i in (1, 3, 5)) == round(.24 * 255)
            assert app.root.cget('background') == app.transcript.cget('background') == config['ui_background']
            assert app.core_canvas.cget('background') == config['ui_background']
            image = app._button_images['Primary.TButton'][0]
            assert image.transparency_get(0, 0) and not image.transparency_get(11, 11)
            app.nav['Profili'].invoke()
            app.root.after(180, lambda:guard(personalization_ready))

        def personalization_ready():
            tone = next(w for w in descendants(app.content)
                        if isinstance(w, ttk.Combobox) and 'Tecnico' in w.cget('values'))
            tone.set('Tecnico')
            app.root.after(120, lambda:guard(personalization_capture))

        def personalization_capture():
            capture(app.root, output/'jarvis-personalization.png')
            save = next(w for w in descendants(app.content)
                        if isinstance(w, ttk.Button) and w.cget('text') == 'Salva e usa profilo')
            save.invoke()
            app.root.after(250, lambda:guard(profile_saved))

        def profile_saved():
            assert Settings(settings.directory).profile()['tone'] == 'Tecnico'
            assert 'Usa termini tecnici precisi' in host._sys_prompt()
            app.show('Servizi')
            app.root.after(180,lambda:guard(services_small))
        def services_small():
            capture(app.root,output/'jarvis-services-small.png')
            assert app.content.winfo_width()<=app._settings_canvas.winfo_width()
            app._settings_canvas.yview_moveto(1)
            app.root.after(180,lambda:guard(fallback_capture))
        def fallback_capture():
            capture(app.root,output/'jarvis-fallback-small.png')
            app.close()
        app.root.after(250,lambda:guard(start))
        app.root.after(15000,lambda:(errors.append('Preview timeout'),app.close()) if not app.closed else None)
        app.run();host._shutdown()
        if errors:raise AssertionError(errors)
        print('HUD: movimento, geometria stabile, riduzione/off, provider e preview GIF OK')
