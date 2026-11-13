"""Observe a dedicated test window entering/leaving fullscreen through the real compositor."""
import sys
import time
import tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_visibility import DesktopVisibility

observer = DesktopVisibility()
root = tk.Tk()
root.title('JARVIS · verifica fullscreen')
root.geometry('500x280')
root.update()
root.focus_force()
def expect(value):
    deadline = time.monotonic()+4
    while time.monotonic()<deadline:
        root.update()
        if observer.fullscreen is value:
            return
        time.sleep(.03)
    raise AssertionError((value, observer.fullscreen, observer.error))
try:
    expect(False)
    root.attributes('-fullscreen', True)
    expect(True)
    root.attributes('-fullscreen', False)
    expect(False)
    print('Real compositor fullscreen changes: OK (' + observer.backend + ')')
finally:
    observer.close()
    root.destroy()
    time.sleep(.6)
