"""Full-screen test pattern for judging black level, white level, gamma and color balance."""
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402


def _label(cr, text, x, y, size=13, color=(0.85, 0.85, 0.85), width=None):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(f'Sans {size}'))
    if width:
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD)
    layout.set_text(text, -1)
    cr.set_source_rgb(*color)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def _draw(_area, cr, w, h, hint):
    cr.set_source_rgb(0.5, 0.5, 0.5)
    cr.paint()
    m = w * 0.04
    row_h = (h - 2 * m) / 6

    # 1. near-black steps on black: 0..20 of 255
    cr.set_source_rgb(0, 0, 0)
    cr.rectangle(0, 0, w, row_h + m)
    cr.fill()
    steps = list(range(0, 22, 2))
    cw = (w - 2 * m) / len(steps)
    for i, v in enumerate(steps):
        cr.set_source_rgb(v / 255, v / 255, v / 255)
        cr.rectangle(m + i * cw + cw * 0.15, m * 0.5 + row_h * 0.1, cw * 0.7, row_h * 0.7)
        cr.fill()
        _label(cr, str(v), m + i * cw + cw * 0.4, m * 0.5 + row_h * 0.82, 10, (0.45, 0.45, 0.45))

    # 2. near-white steps on white: 235..255
    y = row_h + m
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, y, w, row_h)
    cr.fill()
    steps = list(range(235, 256, 2))
    cw = (w - 2 * m) / len(steps)
    for i, v in enumerate(steps):
        cr.set_source_rgb(v / 255, v / 255, v / 255)
        cr.rectangle(m + i * cw + cw * 0.15, y + row_h * 0.1, cw * 0.7, row_h * 0.7)
        cr.fill()
        _label(cr, str(v), m + i * cw + cw * 0.38, y + row_h * 0.8, 10, (0.55, 0.55, 0.55))

    # 3. 21 grey steps
    y += row_h
    n = 21
    cw = w / n
    for i in range(n):
        v = i / (n - 1)
        cr.set_source_rgb(v, v, v)
        cr.rectangle(i * cw, y, cw + 1, row_h)
        cr.fill()

    # 4. smooth grey ramp
    y += row_h
    grad = cairo.LinearGradient(0, 0, w, 0)
    grad.add_color_stop_rgb(0, 0, 0, 0)
    grad.add_color_stop_rgb(1, 1, 1, 1)
    cr.set_source(grad)
    cr.rectangle(0, y, w, row_h)
    cr.fill()

    # 5. primary / secondary color bars
    y += row_h
    colors = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (0, 1, 0), (1, 0, 1), (1, 0, 0), (0, 0, 1)]
    cw = w / len(colors)
    for i, c in enumerate(colors):
        cr.set_source_rgb(*c)
        cr.rectangle(i * cw, y, cw + 1, row_h)
        cr.fill()

    # 6. hue strip
    y += row_h
    grad = cairo.LinearGradient(0, 0, w, 0)
    for i in range(13):
        hue = i / 12
        r = abs(hue * 6 - 3) - 1
        g = 2 - abs(hue * 6 - 2)
        b = 2 - abs(hue * 6 - 4)
        grad.add_color_stop_rgb(i / 12, *(min(1, max(0, c)) for c in (r, g, b)))
    cr.set_source(grad)
    cr.rectangle(0, y, w, h - y)
    cr.fill()

    cr.set_source_rgba(0, 0, 0, 0.6)
    cr.rectangle(m, h - m * 1.6, w - 2 * m, m * 1.2)
    cr.fill()
    _label(cr, hint, m * 1.3, h - m * 1.45, 12, (1, 1, 1), width=w - 2.6 * m)


def show(connector, tr):
    win = Gtk.Window(title=tr('menu_test_pattern'), decorated=False)
    area = Gtk.DrawingArea(hexpand=True, vexpand=True)
    area.set_draw_func(_draw, tr('test_hint'))
    win.set_child(area)

    def on_key(_controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            win.close()
            return True
        return False

    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', on_key)
    win.add_controller(keys)
    click = Gtk.GestureClick()
    click.connect('released', lambda *_: win.close())
    win.add_controller(click)

    target = None
    monitors = Gdk.Display.get_default().get_monitors()
    for i in range(monitors.get_n_items()):
        mon = monitors.get_item(i)
        if mon.get_connector() == connector:
            target = mon
    if target:
        win.fullscreen_on_monitor(target)
    else:
        win.fullscreen()
    win.present()
    return win
