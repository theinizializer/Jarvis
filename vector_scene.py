"""Small retained vector scene rendered with Cairo antialiasing into a Tk canvas."""
import math
import tkinter as tk

try:
    import cairo
except ImportError:
    cairo = None


def vector_scene(canvas):
    if getattr(canvas, '_jarvis_vector_scene', False):
        return canvas
    if cairo is None:
        return canvas
    if not hasattr(canvas, '_vector_scene'):
        canvas.delete('field')
        canvas._vector_scene = VectorScene(canvas)
    return canvas._vector_scene


class VectorScene:
    def __init__(self, canvas):
        self.widget = canvas
        self.items = {}
        self._next = 0
        self._surface = None
        self._image = None
        self._image_item = canvas.create_image(0, 0, anchor='nw')

    def configure(self, **options):
        self.widget.configure(**options)

    def _create(self, kind, coords, options):
        self._next += 1
        self.items[self._next] = [kind, list(coords), options]
        return self._next

    def create_line(self, *coords, **options):
        return self._create('line', coords, options)

    def create_oval(self, *coords, **options):
        return self._create('oval', coords, options)

    def create_arc(self, *coords, **options):
        return self._create('arc', coords, options)

    def create_polygon(self, *coords, **options):
        return self._create('polygon', coords, options)

    def create_rectangle(self, *coords, **options):
        return self._create('rectangle', coords, options)

    def create_text(self, *coords, **options):
        return self._create('text', coords, options)

    def delete(self, tag):
        if tag == 'all':
            self.items.clear()
        else:
            self.items = {key: item for key, item in self.items.items()
                          if tag != key and tag not in item[2].get('tags', ())}

    def coords(self, item, *coords):
        if coords:
            self.items[item][1] = list(coords)
        return self.items[item][1]

    def itemconfigure(self, item, **options):
        self.items[item][2].update(options)

    def itemcget(self, item, key):
        return self.items[item][2].get(key, '')

    @staticmethod
    def _color(context, color):
        context.set_source_rgb(*(int(color[i:i+2], 16) / 255 for i in (1, 3, 5)))

    def _draw(self, context, kind, coords, options):
        if options.get('state') == 'hidden':
            return
        context.save()
        context.new_path()
        context.set_line_width(options.get('width', 1))
        context.set_line_join(cairo.LINE_JOIN_ROUND)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        context.set_dash(options.get('dash', ()))
        if kind == 'text':
            x, y = coords
            text = options.get('text', '')
            font = options.get('font', ('Noto Sans', 10))
            context.translate(x, y)
            context.rotate(-math.radians(options.get('angle', 0)))
            context.select_font_face(font[0], cairo.FONT_SLANT_NORMAL,
                                     cairo.FONT_WEIGHT_BOLD if 'bold' in font else cairo.FONT_WEIGHT_NORMAL)
            context.set_font_size(abs(font[1]) * (96 / 72 if font[1] > 0 else 1))
            xb, yb, width, height, _, _ = context.text_extents(text)
            anchor = options.get('anchor', 'center')
            if anchor == 'center':
                anchor = ''
            x = -xb if 'w' in anchor else -xb-width if 'e' in anchor else -xb-width/2
            y = -yb if 'n' in anchor else -yb-height if 's' in anchor else -yb-height/2
            context.move_to(x, y)
            self._color(context, options.get('fill', '#FFFFFF'))
            context.show_text(text)
        else:
            if kind in {'line', 'polygon'}:
                context.move_to(*coords[:2])
                for i in range(2, len(coords), 2):
                    context.line_to(*coords[i:i+2])
                if kind == 'polygon':
                    context.close_path()
            elif kind == 'rectangle':
                x1, y1, x2, y2 = coords
                context.rectangle(x1, y1, x2-x1, y2-y1)
            else:
                x1, y1, x2, y2 = coords
                context.save()
                context.translate((x1+x2)/2, (y1+y2)/2)
                context.scale(max(.01, abs(x2-x1)/2), max(.01, abs(y2-y1)/2))
                if kind == 'oval':
                    context.arc(0, 0, 1, 0, math.tau)
                else:
                    start = -math.radians(options.get('start', 0))
                    extent = -math.radians(options.get('extent', 90))
                    draw_arc = context.arc_negative if extent < 0 else context.arc
                    draw_arc(0, 0, 1, start, start+extent)
                context.restore()
            fill = options.get('fill', '') if kind not in {'arc', 'line'} else ''
            if fill:
                self._color(context, fill)
                context.fill_preserve()
            stroke = options.get('fill', '#000000') if kind == 'line' else options.get('outline', '#000000')
            if stroke:
                self._color(context, stroke)
                context.stroke()
            else:
                context.new_path()
        context.restore()

    def present(self, width, height):
        width, height = int(width), int(height)
        if self._surface is None or (self._surface.get_width(), self._surface.get_height()) != (width, height):
            self._surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
        context = cairo.Context(self._surface)
        context.set_antialias(cairo.ANTIALIAS_BEST)
        self._color(context, self.widget.cget('background'))
        context.paint()
        for kind, coords, options in self.items.values():
            self._draw(context, kind, coords, options)
        self._surface.flush()
        pixels = self._surface.get_data()
        rgb = bytearray(width * height * 3)
        # Cairo RGB24 is native endian; Tk's binary PPM expects RGB byte order.
        import sys
        offsets = (2, 1, 0) if sys.byteorder == 'little' else (1, 2, 3)
        for channel, offset in enumerate(offsets):
            rgb[channel::3] = pixels[offset::4]
        data = f'P6\n{width} {height}\n255\n'.encode() + rgb
        if self._image is None:
            self._image = tk.PhotoImage(master=self.widget, data=data, format='PPM')
            self.widget.itemconfigure(self._image_item, image=self._image)
        else:
            self._image.configure(data=data, format='PPM')
