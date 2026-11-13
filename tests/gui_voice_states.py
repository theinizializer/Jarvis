"""Check voice amplitude, exploded standby layers and reconstruction in the vector renderer."""
import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_hud import OrbitalCore

output = Path(os.environ.get('JARVIS_TEST_ARTIFACTS','/tmp/jarvis-voice-states'))
output.mkdir(parents=True,exist_ok=True)
root=tk.Tk()
root.title('JARVIS · stati del nucleo')
root.geometry('620x600')
canvas=tk.Canvas(root,highlightthickness=0)
canvas.pack(fill='both',expand=True)
root.update()
core=OrbitalCore(canvas)
core.resize(620,600)
now=time.monotonic()
def capture(name):
    root.update()
    subprocess.run(['import','-window',str(root.winfo_id()),str(output/name)],check=True,timeout=10)
try:
    core.tick(now,'speaking','reduced',.02)
    quiet=core.canvas.coords(core.layers[8])
    core.tick(now,'speaking','reduced',.9)
    assert quiet != core.canvas.coords(core.layers[8]), 'Speaking amplitude is not connected to the core'
    capture('jarvis-parla.png')
    for frame in range(100): core.tick(now+frame/60,'sleeping','full')
    assert core._separation>.99
    capture('jarvis-standby.png')
    separated=core.canvas.coords(core.layers[3])
    for frame in range(100): core.tick(now+2+frame/60,'idle','full')
    assert core._separation<.01
    assert separated != core.canvas.coords(core.layers[3])
    capture('jarvis-risveglio.png')
    root.geometry('216x178')
    root.update()
    core.resize(216,178)
    core.tick(now+5,'sleeping','reduced')
    capture('jarvis-standby-fluttuante.png')
    print('Speaking amplitude, standby separation, reconstruction, reduced motion and compact core: OK')
finally:
    root.destroy()
