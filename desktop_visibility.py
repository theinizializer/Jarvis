"""Observe fullscreen windows without reading their contents or changing their state."""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def kwin_observer_script(service):
    return '''
function report() {
    const w = workspace.activeWindow;
    const fullscreen = Boolean(w && w.fullScreen && !w.minimized);
    callDBus(SERVICE, "/Visibility", "org.jarvis.Visibility", "Update", fullscreen, w ? Math.round(w.pid) : 0);
}
function anchor(w) {
    const a=workspace.clientArea(KWin.WorkArea,w), g=w.frameGeometry;
    const x=Math.max(a.x,a.x+a.width-g.width-24), y=Math.max(a.y,a.y+a.height-g.height-24);
    if (Math.abs(g.x-x)>.5 || Math.abs(g.y-y)>.5)
        w.frameGeometry={x:x,y:y,width:g.width,height:g.height};
    const current=w.frameGeometry;
    callDBus(SERVICE,"/Visibility","org.jarvis.Visibility","Docked",
        Math.round(current.x),Math.round(current.y),Math.round(current.width),Math.round(current.height),
        Math.round(a.x),Math.round(a.y),Math.round(a.width),Math.round(a.height));
}
function candidate(w) {
    callDBus(SERVICE,"/Visibility","org.jarvis.Visibility","SatellitePid",function(pid) {
        const chat=w.pid===PARENT_PID && w.caption==="JARVIS · chat rapida";
        if ((pid>0 && w.pid===pid) || chat) {
            w.keepAbove=true;w.skipTaskbar=true;w.skipPager=true;w.noBorder=true;
            if(!chat)w.onAllDesktops=true;
            anchor(w);
            w.frameGeometryChanged.connect(function(){anchor(w);});
            if(w.outputChanged)w.outputChanged.connect(function(){anchor(w);});
            if(chat)workspace.activeWindow=w;
        }
    });
}
function watch(w) {
    ["fullScreenChanged", "minimizedChanged", "desktopsChanged", "activitiesChanged"].forEach(function(name) {
        if (w[name]) w[name].connect(report);
    });
    report();
    candidate(w);
}
workspace.windowList().forEach(watch);
workspace.windowAdded.connect(watch);
workspace.windowRemoved.connect(report);
workspace.windowActivated.connect(report);
workspace.currentDesktopChanged.connect(report);
workspace.currentActivityChanged.connect(report);
report();
'''.replace('SERVICE', json.dumps(service)).replace('PARENT_PID',str(os.getpid()))


class DesktopVisibility:
    def __init__(self):
        self._fullscreen = None
        self.active_pid = 0
        self._healthy_at = 0
        self._stop = threading.Event()
        self.backend = 'unavailable'
        self.error = ''
        self.satellite_pid = 0
        self.dock_geometry = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name='fullscreen-observer')
        self._thread.start()

    @property
    def fullscreen(self):
        # Unknown compositor state keeps the overlay hidden.
        return self._fullscreen if time.monotonic() - self._healthy_at < 6 else None

    def close(self):
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(.8)

    def _worker(self):
        try:
            if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper() and shutil.which('qdbus6'):
                asyncio.run(self._kwin())
            elif os.environ.get('XDG_SESSION_TYPE') != 'wayland' and shutil.which('xprop'):
                self._x11()
        except Exception as exc:
            self.error = str(exc)
            self._fullscreen = None

    async def _kwin(self):
        from dbus_next.aio import MessageBus
        from dbus_next import Message, MessageType
        from dbus_next.service import ServiceInterface, method
        observer = self

        class Receiver(ServiceInterface):
            def __init__(self):
                super().__init__('org.jarvis.Visibility')

            @method()
            def Update(self, fullscreen: 'b', active_pid:'i'):
                observer._fullscreen = fullscreen
                observer.active_pid = active_pid
                observer._healthy_at = time.monotonic()

            @method()
            def SatellitePid(self) -> 'u':
                return observer.satellite_pid

            @method()
            def Docked(self,x:'i',y:'i',width:'i',height:'i',ax:'i',ay:'i',aw:'i',ah:'i'):
                observer.dock_geometry=(x,y,width,height,ax,ay,aw,ah)

        bus = await MessageBus().connect()
        service = f'org.jarvis.Visibility.p{os.getpid()}.n{id(self)}'
        plugin = f'jarvis-visibility-{os.getpid()}-{id(self)}'
        bus.export('/Visibility', Receiver())
        await bus.request_name(service)
        async def call(path, member, signature='', body=None, interface='org.kde.kwin.Scripting'):
            reply = await asyncio.wait_for(bus.call(Message(destination='org.kde.KWin', path=path,
                interface=interface, member=member, signature=signature, body=body or [])), 2)
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(str(reply.body))
            return reply.body
        try:
            with tempfile.TemporaryDirectory(prefix='jarvis-fullscreen-') as directory:
                script = Path(directory) / 'observer.js'
                script.write_text(kwin_observer_script(service))
                script_id = (await call('/Scripting', 'loadScript', 'ss', [str(script), plugin]))[0]
                if script_id < 0:
                    raise RuntimeError('KWin non ha caricato l’osservatore fullscreen.')
                await call(f'/Scripting/Script{script_id}', 'run', interface='org.kde.kwin.Script')
                self.backend = 'KWin'
                while not self._stop.is_set():
                    loaded = await call('/Scripting', 'isScriptLoaded', 's', [plugin])
                    if not loaded[0]:
                        raise RuntimeError('Osservatore KWin scollegato.')
                    if self._fullscreen is not None:
                        self._healthy_at = time.monotonic()
                    await asyncio.sleep(.5)
        finally:
            self._fullscreen = None
            try:
                await call('/Scripting', 'unloadScript', 's', [plugin])
            except Exception:
                pass
            bus.disconnect()

    def _x11(self):
        import re
        self.backend = 'X11'
        while not self._stop.is_set():
            result = subprocess.run(['xprop', '-root', '_NET_ACTIVE_WINDOW'], capture_output=True, text=True, timeout=1)
            windows = re.findall(r'0x[0-9a-fA-F]+', result.stdout)
            fullscreen = False
            for window in windows:
                state = subprocess.run(['xprop', '-id', window, '_NET_WM_STATE'],
                                       capture_output=True, text=True, timeout=1).stdout
                fullscreen |= '_NET_WM_STATE_FULLSCREEN' in state and '_NET_WM_STATE_HIDDEN' not in state
            self._fullscreen = fullscreen if result.returncode == 0 else None
            self._healthy_at = time.monotonic()
            self._stop.wait(.4)
