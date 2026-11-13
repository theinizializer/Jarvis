"""Transparent, compositor-managed vector core. Communicates only UI state over private pipes."""
import json
import math
import sys
import threading
import time
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion,QIcon
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget, QMenu
from vector_scene import VectorScene
from jarvis_hud import HudCore, OrbitalCore

CORE_BASE=224
CORE_SIZE=157


def emit(kind):
    print(json.dumps({'type':kind}),flush=True)


class Scene(VectorScene):
    _jarvis_vector_scene = True
    def __init__(self, widget):
        self.widget, self.items, self._next = widget, {}, 0
    def configure(self, **options):
        pass
    def present(self, *size):
        self.widget.update()


class Inbox(QObject):
    message = Signal(dict)


class CoreWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('JARVIS Satellite')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(CORE_SIZE,CORE_SIZE)
        self.setMask(QRegion(0,0,CORE_SIZE,CORE_SIZE,QRegion.Ellipse))
        self.setCursor(Qt.PointingHandCursor)
        self.scene = Scene(self)
        self.mode, self.state, self.level = '', 'idle', 0
        self.core = None
        self.theme = None
        self.inbox = Inbox()
        self.inbox.message.connect(self.receive)
        threading.Thread(target=self.read_pipe,daemon=True).start()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)
        emit('ready')

    def read_pipe(self):
        for line in sys.stdin:
            try:
                value=json.loads(line)
                if isinstance(value,dict): self.inbox.message.emit(value)
            except ValueError: pass
        self.inbox.message.emit({'quit':True})

    def receive(self, value):
        if value.get('quit'):
            QApplication.quit();return
        mode=value.get('mode','text')
        if mode != self.mode:
            self.mode=mode
            self.core=(OrbitalCore if mode=='voice' else HudCore)(self.scene)
            self.core.satellite=True
            self.theme=None
        theme=tuple(value.get('theme',['Blu ionico',None,55,'#050911']))
        if theme != self.theme:
            self.core.set_theme(*theme)
            self.core.resize(CORE_BASE,CORE_BASE)
            self.theme=theme
        self.state=value.get('state','idle')
        self.level=float(value.get('level',0))
        if value.get('visible',False):
            if not self.isVisible():
                self.show()
                area=QApplication.primaryScreen().availableGeometry()
                self.move(area.right()-self.width()-24,area.bottom()-self.height()-24)
        else:
            self.hide()

    def animate(self):
        if self.core and self.isVisible():
            self.core.tick(time.monotonic(),self.state,'full',self.level)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:
            emit('standby' if self.mode=='voice' else 'compose')
        elif event.button()==Qt.RightButton:
            menu=QMenu(self)
            menu.addAction('Apri JARVIS',lambda:emit('open'))
            menu.addAction('Zittisci',lambda:emit('stop'))
            if self.mode=='voice':menu.addAction('Dormi / Risveglia',lambda:emit('standby'))
            menu.exec(event.globalPosition().toPoint())

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(),Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.scale(CORE_SIZE/CORE_BASE,CORE_SIZE/CORE_BASE)
        for kind,coords,options in self.scene.items.values():
            if kind=='text' or options.get('state')=='hidden':continue
            painter.save()
            fill=options.get('fill','') if kind not in {'line','arc'} else ''
            stroke=options.get('fill','#000000') if kind=='line' else options.get('outline','#000000')
            if stroke:
                pen=QPen(QColor(stroke),options.get('width',1),Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)
                if options.get('dash'):pen.setDashPattern(list(options['dash']))
                painter.setPen(pen)
            else:painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(fill) if fill else Qt.NoBrush)
            if kind in {'line','polygon'}:
                path=QPainterPath(QPointF(*coords[:2]))
                for i in range(2,len(coords),2):path.lineTo(*coords[i:i+2])
                if kind=='polygon':path.closeSubpath()
                painter.drawPath(path)
            else:
                x1,y1,x2,y2=coords
                rect=QRectF(x1,y1,x2-x1,y2-y1)
                if kind=='oval':painter.drawEllipse(rect)
                elif kind=='rectangle':painter.drawRect(rect)
                elif kind=='arc':painter.drawArc(rect,round(options.get('start',0)*16),round(options.get('extent',90)*16))
            painter.restore()
        painter.end()


if __name__=='__main__':
    app=QApplication(sys.argv)
    app.setApplicationName('jarvis-satellite')
    app.setWindowIcon(QIcon(str(Path(__file__).with_name('assets')/'jarvis-icon-v2.png')))
    app.setDesktopFileName('jarvis-satellite')
    window=CoreWindow()
    sys.exit(app.exec())
