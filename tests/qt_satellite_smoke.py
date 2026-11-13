"""Native Wayland alpha, continuous vectors and compositor placement for the compact core."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
# The host has Qt; D-Bus is also available in the app's isolated environment.
sys.path.append(str(root/'venv/lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'))
from PySide6.QtWidgets import QApplication
from satellite_core import CoreWindow,CORE_SIZE
from desktop_visibility import DesktopVisibility

app=QApplication([])
app.setApplicationName('jarvis-satellite')
observer=DesktopVisibility()
observer.satellite_pid=os.getpid()
with patch.object(CoreWindow,'read_pipe',lambda self:None):
    window=CoreWindow()
try:
    window.receive({'mode':'voice','visible':True,'state':'idle'})
    window.activateWindow()
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
        if observer.dock_geometry:
            x,y,w,h,ax,ay,aw,ah=observer.dock_geometry
            if (x,y)==(ax+aw-w-24,ay+ah-h-24):break
    assert observer.dock_geometry is not None,(observer.backend,observer.error)
    x,y,w,h,ax,ay,aw,ah=observer.dock_geometry
    assert (x,y)==(ax+aw-w-24,ay+ah-h-24),observer.dock_geometry
    image=window.grab().toImage()
    assert (image.width(),image.height())==(157,157)==(CORE_SIZE,CORE_SIZE)
    assert image.pixelColor(0,0).alpha()==0,'The core background is opaque'
    before=window.scene.coords(window.core.layers[8])[:]
    for _ in range(40):app.processEvents();time.sleep(.016)
    assert before!=window.scene.coords(window.core.layers[8]),'The idle core is static'
    target=root/'artifacts/jarvis-nucleo-trasparente.png'
    target.parent.mkdir(exist_ok=True)
    assert window.grab().save(str(target))
    sizes={}
    for mode in ('voice','text'):
        window.receive({'mode':mode,'visible':True,'state':'idle'})
        for _ in range(6):app.processEvents();time.sleep(.02)
        frame=window.grab().toImage()
        pixels=[(x,y) for y in range(frame.height()) for x in range(frame.width()) if frame.pixelColor(x,y).alpha()>25]
        sizes[mode]=(max(x for x,y in pixels)-min(x for x,y in pixels),max(y for x,y in pixels)-min(y for x,y in pixels))
        assert window.grab().save(str(root/f'artifacts/jarvis-nucleo-{mode}-157.png'))
    assert all(abs(a-b)<=8 for a,b in zip(sizes['voice'],sizes['text'])),sizes
    window.resize(270,270)
    assert (window.width(),window.height())==(157,157)
    print('Native transparency, moving vectors, equal 157px cores and bottom-right geometry: OK',sizes,observer.dock_geometry)
finally:
    window.hide()
    observer.close()
