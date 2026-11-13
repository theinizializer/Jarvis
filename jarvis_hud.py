"""Segmented signal core for the native JARVIS Canvas.

Layout (outside in): tick ring, six module arcs, a slow middle ring and an inner
waveform ring.  Every visual change is driven by real application state: the
state name passed to tick(), the microphone level, and optionally the set of
module keys that are genuinely active.  Nothing here is random.
"""

import math
import re
from datetime import datetime
from vector_scene import vector_scene

BG = '#050911'
LINE = '#233F55'
CYAN = '#68DBFF'
BLUE = '#258FD7'
PULSE_BLUE = '#33ABFF'
PLATE = '#071827'
GRID = '#101E2C'
HUD_THEMES = {
    'Blu ionico': (CYAN, BLUE, PULSE_BLUE),
    'Ambra': ('#FFD67D', '#C58C3B', '#F5AC49'),
    'Verde radar': ('#8DF6C2', '#319C72', '#51E3A0'),
    'Viola orbitale': ('#D9BCFF', '#8861D0', '#B188FF'),
}
AMBER = '#FFC47D'
RED = '#FF8A8A'
MUTED = '#8EA1B4'

STATES = {
    'idle': 'In attesa',
    'sleeping': 'Standby · hey JARVIS',
    'listening': 'Acquisizione vocale',
    'working': 'Elaborazione',
    'provider': 'Modello in elaborazione',
    'executing': 'Strumento in esecuzione',
    'speaking': 'Riproduzione vocale',
    'approval': 'Autorizzazione richiesta',
    'error': 'Operazione non riuscita',
}

# Clockwise from the top.  key, label shown next to the arc.
MODULES = (
    ('voce', 'VOCE'),
    ('modello', 'MODELLO'),
    ('strumenti', 'STRUMENTI'),
    ('memoria', 'MEMORIA'),
    ('guardiano', 'GUARDIANO'),
    ('rete', 'MCP · SSH'),
)

# Which modules a state lights up when the caller gives no finer information.
ACTIVE = {
    'listening': frozenset({'voce'}),
    'speaking': frozenset({'voce'}),
    'working': frozenset({'modello', 'memoria'}),
    'provider': frozenset({'modello'}),
    'executing': frozenset({'strumenti'}),
    'approval': frozenset({'guardiano'}),
}

RING_POINTS = 96


def mix(foreground, background, amount):
    """Blend two #RRGGBB colours; amount=1 gives the foreground."""
    a = [int(foreground[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02X%02X%02X' % tuple(round(y + (x - y) * amount) for x, y in zip(a, b))


class HudCore:
    """Segmented core projected onto the instrument plane."""

    def __init__(self, canvas, theme='Blu ionico'):
        self.canvas = vector_scene(canvas)
        self.geometry = None
        self.last_frame = None
        self.memory_percent = None
        self._last_tick = None
        self._phase = 0
        self._rate = 0
        self._gain = 0
        self._separation = 0
        self._frame_dt = 1/60

        self.set_theme(theme)

    def _motion_frame(self, now, rate, gain, motion):
        dt = min(.05, max(0, now - self._last_tick)) if self._last_tick is not None else 1/60
        self._last_tick = now
        self._frame_dt = dt
        ease = 1 - math.exp(-dt * 9)
        self._rate += (rate - self._rate) * ease
        self._gain += (gain - self._gain) * ease
        if motion == 'full':
            self._phase = (self._phase + self._rate * dt) % (math.tau * 20)
            return self._phase, self._gain
        return 0, gain

    def _present(self):
        if hasattr(self.canvas, 'present'):
            self.canvas.present(*self.geometry)

    def set_theme(self, name, color=None, glow=55, background=BG):
        self.background = background
        self.canvas.configure(background=background)
        self.accent, self.blue, self.pulse = HUD_THEMES.get(name, HUD_THEMES['Blu ionico'])
        if name == 'Personalizzato' and isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            self.accent = mix('#FFFFFF', color, .29)
            self.blue = mix(color, self.background, .78)
            self.pulse = mix('#FFFFFF', color, .12)
        self.glow_strength = max(0, min(100, glow)) / 100 if isinstance(glow, (int, float)) else .55
        self.line, self.plate, self.grid = LINE, PLATE, GRID
        if self.accent != CYAN or background != BG:
            self.line = mix(self.blue, self.background, .38)
            self.plate = mix(self.blue, self.background, .12)
            self.grid = mix(self.blue, self.background, .16)
        if self.geometry:
            self.resize(*self.geometry)

    # ------------------------------------------------------------------ layout
    def resize(self, width, height):
        if width < 10 or height < 10:
            return
        c = self.canvas
        c.delete('field')
        self.geometry = (width, height)
        self.last_frame = None
        self.labels_on = width >= 530 and height >= 164
        self.cx = width * (.48 if self.labels_on else .50)
        self.cy = height * (.53 if self.labels_on else .50)
        max_radius = width * .21 if self.labels_on else width / 2 - 12
        self.radius = R = max(18.0, min(max_radius, height * .37 if self.labels_on else height / 2 - 14))
        cx, cy = self.cx, self.cy
        self._cos = [math.cos(2 * math.pi * k / RING_POINTS) for k in range(RING_POINTS + 1)]
        self._sin = [math.sin(2 * math.pi * k / RING_POINTS) for k in range(RING_POINTS + 1)]

        self.sectors = {}
        if self.labels_on:
            self._draw_console(width, height)
        c.create_line(cx - R, cy, cx + R, cy, fill=self.line, dash=(2, 6), tags='field')
        c.create_line(cx, cy - R, cx, cy + R, fill=self.line, dash=(2, 6), tags='field')
        for scale in (.17, .23, .29, .45, .49, .54, .62, .70, .81, .97, 1.04):
            r = R * scale
            c.create_oval(cx-r, cy-r, cx+r, cy+r, outline=self.blue if scale in (.29, .49, .62) else self.line, tags='field')
        for start, extent, scale, stroke in ((12, 72, 1.04, 3), (91, 43, 1.04, 2),
                                             (151, 46, .96, 3), (218, 43, 1.10, 3),
                                             (287, 53, .96, 4), (66, 78, .66, 2),
                                             (244, 78, .66, 2), (112, 60, .52, 4),
                                             (295, 65, .52, 4)):
            r = R * scale
            c.create_arc(cx-r, cy-r, cx+r, cy+r, start=start, extent=extent, style='arc',
                         outline=self.pulse, width=stroke, tags='field')

        # Static tick ring.
        for i in range(100):
            angle = 2 * math.pi * i / 100
            inner = .82 if i % 5 == 0 else .88
            c.create_line(cx + R * inner * math.cos(angle), cy - R * inner * math.sin(angle),
                          cx + R * .91 * math.cos(angle), cy - R * .91 * math.sin(angle),
                          fill=self.blue if i % 5 == 0 else self.line, tags='field')

        # Module arcs, nodes and labels.
        arc_r = R * .77
        self.arcs, self.nodes, self.names = [], [], []
        for index, (key, label) in enumerate(MODULES):
            centre = 90 - 60 * index
            self.arcs.append(c.create_arc(cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r,
                                          start=centre - 27, extent=54, style='arc',
                                          outline=self.line, width=2, tags='field'))
            theta = math.radians(centre)
            nx, ny = cx + arc_r * math.cos(theta), cy - arc_r * math.sin(theta)
            self.nodes.append(c.create_oval(nx - 2, ny - 2, nx + 2, ny + 2, fill=self.line,
                                            outline='', tags='field'))
            lx, ly = width * .78, height * .34 + index * min(22, height * .08)
            if self.labels_on:
                c.create_line(lx, ly + 9, width - 15, ly + 9, fill=self.line, tags='field')
                c.create_line(lx - 9, ly - 5, lx - 9, ly + 5, fill=self.blue, tags='field')
            self.names.append(c.create_text(lx, ly, text=label, anchor='w', fill=MUTED,
                                            font=('Noto Sans Mono', 9),
                                            state='normal' if self.labels_on else 'hidden',
                                            tags='field'))
        self._module_style = [None] * len(MODULES)

        # Rings, glow underneath the crisp line.
        self.mid = c.create_line(0, 0, 1, 1, fill=self.line, width=1, tags='field')
        halo_state = 'normal' if self.glow_strength else 'hidden'
        self.halo = c.create_line(0, 0, 1, 1, width=4 + 14 * self.glow_strength,
                                  state=halo_state, tags='field')
        self.glow = c.create_line(0, 0, 1, 1, width=3 + 7 * self.glow_strength,
                                  state=halo_state, tags='field')
        self.wave = c.create_line(0, 0, 1, 1, fill=self.accent, width=2, tags='field')
        self.sweep = c.create_arc(cx - R * .91, cy - R * .91, cx + R * .91, cy + R * .91,
                                  start=0, extent=34, style='arc', outline=self.accent, width=2,
                                  state='hidden', tags='field')
        self._sweep_shown = False
        self.centre = c.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=self.accent, outline='', tags='field')
        self.orbit = c.create_arc(cx-R*1.08,cy-R*1.08,cx+R*1.08,cy+R*1.08,
                                  start=0,extent=32,style='arc',outline=self.accent,width=2,tags='field')
        c.create_text(16, 13, text='JARVIS / STAZIONE OPERATIVA' if self.labels_on else 'JARVIS / HUD',
                      anchor='nw', fill=self.accent, font=('Noto Sans Mono', 9, 'bold'), tags='field')
        self.status = c.create_text(16, height - 13, anchor='sw',
                                    font=('Noto Sans', 11, 'bold'), tags='field')
        self.tick(0, motion='off')

    def _draw_console(self, width, height):
        c = self.canvas
        for x in range(0, width, 24):
            c.create_line(x, 0, x, height, fill=self.grid, tags='field')
        for y in range(0, height, 24):
            c.create_line(0, y, width, y, fill=self.grid, tags='field')
        c.create_line(14, 35, width - 14, 35, fill=self.blue, tags='field')
        for x in range(16, width - 12, 14):
            c.create_line(x, 35, x, 39 if x % 3 else 42, fill=self.blue, tags='field')

        x = width * .12
        radius = min(height * .135, width * .080)
        clock_y, ram_y = height * .36, height * .70
        for y, title in ((clock_y, 'ORA LOCALE'), (ram_y, 'RAM IN USO')):
            box = (x-radius, y-radius, x+radius, y+radius)
            c.create_oval(*box, outline=self.line, fill=self.plate, width=2, tags='field')
            c.create_arc(*box, start=218, extent=-256, outline=self.blue,
                         width=2, style='arc', tags='field')
            for index in range(28):
                angle = math.radians(218 - index * 256 / 27)
                short = .77 if index % 4 == 0 else .87
                c.create_line(x + radius * short * math.cos(angle), y - radius * short * math.sin(angle),
                              x + radius * .95 * math.cos(angle), y - radius * .95 * math.sin(angle),
                              fill=self.blue if index % 4 == 0 else self.line, tags='field')
            c.create_text(x, y + radius * .44, text=title, fill=MUTED,
                          font=('Noto Sans Mono', 7), tags='field')
        self.clock_text = c.create_text(x, clock_y - radius * .18, fill=self.accent,
                                        font=('Noto Sans Mono', 11, 'bold'), tags='field')
        self.ram_text = c.create_text(x, ram_y - radius * .18, fill=self.accent,
                                      font=('Noto Sans Mono', 11, 'bold'), tags='field')
        self.ram_arc = c.create_arc(x-radius, ram_y-radius, x+radius, ram_y+radius,
                                    start=218, outline=self.accent, width=3, style='arc', tags='field')
        self.update_telemetry(self.memory_percent)

        cx, cy, radius = self.cx, self.cy, self.radius
        if height >= 220:
            for start, end, key in ((138, 174, 'modello'), (201, 239, 'strumenti'),
                                    (317, 350, 'guardiano')):
                points = []
                for ring, angles in ((.77, range(start, end + 1, 3)),
                                     (1.32, range(end, start - 1, -3))):
                    for angle in angles:
                        theta = math.radians(angle)
                        points.extend((cx + radius * ring * math.cos(theta),
                                       cy - radius * ring * math.sin(theta)))
                self.sectors[key] = c.create_polygon(*points, fill=self.plate, outline=self.blue, tags='field')
                theta = math.radians((start + end) / 2)
                c.create_text(cx + radius * 1.11 * math.cos(theta),
                              cy - radius * 1.11 * math.sin(theta),
                              text=key.upper(), angle=(start + end) / 2 % 180 - 90,
                              fill=self.accent, font=('Noto Sans Mono', 8), tags='field')

        edge = width * .76
        c.create_line(cx + radius * .62, cy - radius * .54, edge - 20, height * .27,
                      edge, height * .27, fill=self.blue, tags='field')
        c.create_rectangle(edge, height * .19, width - 12, height * .80,
                           outline=self.line, tags='field')
        c.create_text(width * .78, height * .24, text='MODULI / STATO', anchor='w',
                      font=('Noto Sans Mono', 9, 'bold'), fill=self.accent, tags='field')

    def update_telemetry(self, memory_percent):
        self.memory_percent = memory_percent
        if self.geometry is None or not self.labels_on:
            return
        c = self.canvas
        c.itemconfigure(self.clock_text, text=datetime.now().strftime('%H:%M'))
        c.itemconfigure(self.ram_text, text='—' if memory_percent is None else f'{memory_percent:.0f}%')
        c.itemconfigure(self.ram_arc, extent=-2.56 * (memory_percent or 0))
        self._present()

    # ------------------------------------------------------------------- frame
    def tick(self, now, state='idle', motion='full', level=0, modules=None):
        if self.geometry is None:
            return
        animated = motion == 'full'
        level = min(1.0, max(0.0, level)) if motion != 'off' and state == 'listening' else 0
        active = frozenset(modules) if modules is not None else ACTIVE.get(state, frozenset())
        frame = (state, motion, round(level, 2), active)
        if not animated and frame == self.last_frame:
            return
        c = self.canvas
        cx, cy, R = self.cx, self.cy, self.radius
        color = AMBER if state == 'approval' else RED if state == 'error' else self.accent
        dim = mix(color, self.background, .45)
        rate = {'idle': .65, 'working': 1.5, 'provider': 2.4, 'executing': 3.4,
                'speaking': .9, 'listening': .7, 'approval': .15, 'error': .15}.get(state, .4)
        gain = {'idle': .10, 'working': .45, 'provider': .70, 'executing': .55,
                'speaking': .50, 'approval': .08, 'error': .06}.get(state, .10 + .90 * level)
        phase, gain = self._motion_frame(now, rate, gain, motion)
        breathe = 1 + .02 * math.sin(now * .6) if animated and state == 'idle' else 1
        if state == 'approval':
            breathe = .94  # the core contracts while the Guardian waits for a decision

        inner, middle = R * .40 * breathe, R * .58
        wave_points, mid_points = [], []
        sin = math.sin
        for k in range(RING_POINTS + 1):
            theta = 2 * math.pi * k / RING_POINTS
            ripple = .5 * sin(3 * theta + phase) + .5 * sin(7 * theta - phase * 1.7)
            r1 = inner * (1 + .30 * gain * ripple)
            wave_points += (cx + r1 * self._cos[k], cy - r1 * self._sin[k])
            r2 = middle * (1 + .06 * gain * sin(5 * theta - phase * .6))
            mid_points += (cx + r2 * self._cos[k], cy - r2 * self._sin[k])
        c.coords(self.wave, *wave_points)
        c.coords(self.halo, *wave_points)
        c.coords(self.glow, *wave_points)
        c.coords(self.mid, *mid_points)
        c.itemconfigure(self.wave, fill=color)
        c.itemconfigure(self.halo, fill=mix(color, self.background, .24 * self.glow_strength))
        c.itemconfigure(self.glow, fill=mix(color, self.background, .60 * self.glow_strength))
        c.itemconfigure(self.mid, fill=dim if active else self.line)
        c.itemconfigure(self.centre, fill=color)
        c.itemconfigure(self.orbit,start=phase*57.3,outline=color)

        if self.last_frame is None or (state, active) != (self.last_frame[0], self.last_frame[3]):
            for key, item in self.sectors.items():
                c.itemconfigure(item, fill=mix(color, self.background, .19) if key in active else self.plate,
                                outline=color if key in active else self.blue)
        pulse = 1 + .5 * sin(phase * 2) if animated else 1
        for index, (key, _) in enumerate(MODULES):
            lit = key in active
            style = (lit, color, round(pulse, 1) if lit else 0)
            if style == self._module_style[index]:
                continue
            self._module_style[index] = style
            c.itemconfigure(self.arcs[index], outline=color if lit else self.line, width=3 * style[2] if lit else 2)
            c.itemconfigure(self.nodes[index], fill=color if lit else self.line)
            if self.labels_on:
                c.itemconfigure(self.names[index], fill=color if lit else MUTED)

        sweep = animated and state in {'working', 'provider', 'executing'}
        if sweep:
            c.itemconfigure(self.sweep, start=(-phase * 57.3) % 360, outline=color,
                            state='normal')
        elif self._sweep_shown:
            c.itemconfigure(self.sweep, state='hidden')
        self._sweep_shown = sweep
        c.itemconfigure(self.status, text=STATES[state], fill=color)
        self.last_frame = frame
        self._present()


class OrbitalCore(HudCore):
    """Projected orbital layers for voice activity, sharing the persisted theme."""

    def resize(self, width, height):
        if width < 10 or height < 10:
            return
        self.geometry = (width, height)
        self.last_frame = None
        c = self.canvas
        c.delete('field')
        self.layers = [c.create_line(0, 0, 1, 1, width=1, tags='field') for _ in range(17)]
        self.inner_layers = [c.create_line(0, 0, 1, 1, width=1, tags='field') for _ in range(9)]
        self.halos = [c.create_oval(0, 0, 1, 1, outline='', tags='field') for _ in range(4)]
        self.brackets = [c.create_arc(0, 0, 1, 1, extent=104, style='arc', width=2, tags='field') for _ in range(3)]
        self.precision_ticks = [c.create_line(0, 0, 1, 1, width=1, tags='field') for _ in range(48)]
        self.inner_arcs = [c.create_arc(0, 0, 1, 1, extent=68, style='arc', width=1, tags='field') for _ in range(4)]
        self.sparks = [c.create_oval(0, 0, 1, 1, outline='', tags='field') for _ in range(25)]
        compact = height < 250
        self.status = c.create_text(width / 2, height - (16 if compact else 34), fill=self.accent,
                                    font=('Noto Sans', 10 if compact else 12, 'bold'), tags='field')
        self.tick(0, motion='off')

    def update_telemetry(self, memory_percent):
        self.memory_percent = memory_percent

    def tick(self, now, state='idle', motion='full', level=0, modules=None):
        if self.geometry is None:
            return
        frame = (state, motion, round(level, 2))
        if motion != 'full' and self.last_frame == frame:
            return
        c = self.canvas
        width, height = self.geometry
        radius = max(14, min(width * .32, height * (.28 if height < 250 else .34)))
        cx, cy = width / 2, height * (.42 if height < 250 else .46)
        if getattr(self,'satellite',False):
            outer=1.10*(min(width,height)/2-14)
            radius=outer/(1.08+.20*self._separation+.06*self._gain)
            cx,cy=width/2,height/2
        gain = min(1, max(0, level)) if state == 'listening' and motion != 'off' else 0
        if state == 'speaking' and motion != 'off':
            gain = .12 + .88 * min(1, max(0, level))
        rate = .04 if state == 'sleeping' else .4 if state in {'working', 'provider', 'executing', 'speaking'} else .10
        phase, gain = self._motion_frame(now, rate, gain, motion)
        target = 1 if state == 'sleeping' else 0
        if motion == 'full':
            self._separation += (target-self._separation) * (1-math.exp(-self._frame_dt*4))
        else:
            self._separation = target
        separated = self._separation
        color = AMBER if state == 'approval' else RED if state == 'error' else self.accent
        if state == 'sleeping':
            color = mix(color, self.background, .65)
        points3d = []
        for index, item in enumerate(self.layers):
            tilt = (index - 7) * math.pi / 10
            offset_x = math.sin(index*2.4) * radius*.12*separated
            offset_y = (index-8)/8 * radius*.58*separated
            line = []
            for step in range(65):
                angle = step * math.tau / 64
                ripple = 1 + gain * .10 * math.sin(angle * 5 + index)
                if index < 7:
                    latitude = (index - 3) * math.pi / 9
                    x = math.cos(latitude) * math.cos(angle) * ripple
                    z = math.cos(latitude) * math.sin(angle)
                    y = math.sin(latitude)
                else:
                    x = math.cos(angle) * math.cos(tilt) * ripple
                    z = math.cos(angle) * math.sin(tilt)
                    y = math.sin(angle)
                rx = x * math.cos(phase) + z * math.sin(phase)
                rz = -x * math.sin(phase) + z * math.cos(phase)
                scale = radius * (1 + .09 * rz)
                px = cx + offset_x + rx * scale * (1-separated*.08)
                py = cy + offset_y + (y * (.86-separated*.42) + rz * .32) * scale
                line.extend((px, py))
                if step == (index * 5) % 64:
                    points3d.append((px, py, rz))
            c.coords(item, *line)
            c.itemconfigure(item, fill=mix(color, self.background, .35 + .5 * (index % 3) / 2))
        for index, item in enumerate(self.inner_layers):
            line = []
            shell = (.39, .62, .81)[index // 3]
            tilt = index * .74
            for step in range(65):
                angle = step * math.tau / 64
                radius_mod = shell * radius * (1 + gain * .12 * math.sin(angle * 6 + index))
                x = math.cos(angle)
                y = math.sin(angle) * math.cos(tilt + phase * .7)
                z = math.sin(angle) * math.sin(tilt + phase * .7)
                px = cx + (index//3-1)*radius*.40*separated + (x * math.cos(phase * .5) + z * math.sin(phase * .5)) * radius_mod
                py = cy + (index%3-1)*radius*.54*separated + (y * (.86-separated*.40) + z * .32) * radius_mod
                line.extend((px, py))
                if step == index * 7 % 64:
                    points3d.append((px, py, z))
            c.coords(item, *line)
            c.itemconfigure(item, fill=mix(color, self.background, .45 + .15 * (index % 3)))
        for index, item in enumerate(self.halos):
            size = radius * (.23 - index * .055) * (1 + gain * .65) * (1-separated*.40)
            c.coords(item, cx-size, cy-size, cx+size, cy+size)
            c.itemconfigure(item, fill=mix(color, self.background, (.06 + index * .16) * self.glow_strength))
        for index, item in enumerate(self.brackets):
            size = radius * (1.08 + gain * .06)
            shift = (index-1)*radius*.20*separated
            c.coords(item, cx-size, cy-size+shift, cx+size, cy+size+shift)
            c.itemconfigure(item, start=index * 120 + phase * 16 + separated*25, outline=mix(color, self.background, .75))
        for index, item in enumerate(self.precision_ticks):
            angle = index * math.tau / 48 + phase * .12
            inner = radius * (1.01 if index % 4 == 0 else 1.035)
            outer = radius * 1.065
            c.coords(item, cx+inner*math.cos(angle), cy+inner*math.sin(angle),
                     cx+outer*math.cos(angle), cy+outer*math.sin(angle))
            c.itemconfigure(item, fill=mix(color, self.background, .65 if index % 4 == 0 else .27))
        for index, item in enumerate(self.inner_arcs):
            size = radius * (.30 + index * .035)
            c.coords(item, cx-size, cy-size, cx+size, cy+size)
            c.itemconfigure(item, start=index*90 - phase*24, outline=mix(color, self.background, .45))
        for index, item in enumerate(self.sparks):
            px, py, depth = points3d[index % len(points3d)]
            size = 1.5 + (depth + 1) * .7
            c.coords(item, px-size, py-size, px+size, py+size)
            c.itemconfigure(item, fill=color)
        c.itemconfigure(self.status, text=STATES[state], fill=color)
        self.last_frame = frame
        self._present()
