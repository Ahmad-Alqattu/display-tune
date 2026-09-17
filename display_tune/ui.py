import locale
import os
import re
import sys
import threading
import time
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, testpattern  # noqa: E402
from . import backend as B  # noqa: E402
from .i18n import LANGUAGES, Tr  # noqa: E402

REPO = 'https://github.com/Ahmad-Alqattu/display-tune'
SEP = '\x1f'   # separates the parts of a string action target


def use_language(lang):
    """Make GTK's own strings (context menus, tooltips) follow the app language."""
    os.environ['LANGUAGE'] = lang
    try:
        locale.setlocale(locale.LC_MESSAGES, '')   # also drops gettext's cached catalogs
    except locale.Error:
        pass


def signed_pct(v):
    return f'{v:+.0f}%' if round(v) else '0%'


def menu_item(label, action, target):
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, GLib.Variant('s', target))
    return item


class Worker:
    """One background thread; for each key only the latest request runs (sliders send many)."""

    def __init__(self):
        self.cv = threading.Condition()
        self.jobs = {}
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, key, fn):
        with self.cv:
            self.jobs.pop(key, None)
            self.jobs[key] = fn
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while not self.jobs:
                    self.cv.wait()
                key = next(iter(self.jobs))
                fn = self.jobs.pop(key)
            try:
                fn()
            except Exception:
                traceback.print_exc()


class SliderRow(Adw.ActionRow):
    def __init__(self, title, subtitle, lo, hi, step, value, fmt, on_change, default=None, reset_tooltip=None):
        super().__init__(title=title, subtitle=subtitle)
        self.step, self.fmt, self.on_change, self.default = step, fmt, on_change, default
        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step, page_increment=step * 5)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj,
                               draw_value=False, valign=Gtk.Align.CENTER, width_request=240)
        if default is not None:
            self.scale.add_mark(default, Gtk.PositionType.BOTTOM, None)
        self.label = Gtk.Label(width_chars=6, css_classes=['numeric', 'dim-label'])
        self.reset_button = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER,
                                       css_classes=['flat', 'circular'], tooltip_text=reset_tooltip,
                                       visible=False)
        self.reset_button.connect('clicked', lambda _b: self.scale.set_value(self.default))
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(self.reset_button)
        box.append(self.scale)
        box.append(self.label)
        self.add_suffix(box)
        self.handler = None
        self.set_value(value)
        self.handler = self.scale.connect('value-changed', self._changed)

    def _show(self, v):
        self.label.set_label('\u2066' + self.fmt(v) + '\u2069')   # keep "+10%" from flipping in RTL
        self.reset_button.set_visible(self.default is not None and abs(v - self.default) > self.step / 2)

    def set_value(self, v):
        if self.handler:
            self.scale.handler_block(self.handler)
        self.scale.set_value(v)
        self._show(v)
        if self.handler:
            self.scale.handler_unblock(self.handler)

    def _changed(self, scale):
        v = round(round(scale.get_value() / self.step) * self.step, 4)
        self._show(v)
        self.on_change(v)


class MonitorPage(Adw.PreferencesPage):
    def __init__(self, win, monitor, others):
        super().__init__()
        self.win, self.m, self.tr = win, monitor, win.tr
        tr, st = self.tr, win.store.monitor(monitor.key)
        self.rows = {}

        # ── light ──
        self.light = Adw.PreferencesGroup(title=tr('group_light'), description=self._describe())
        self.add(self.light)
        if monitor.builtin:
            bl = win.mutter.backlight(monitor.connector)
            if bl:
                _serial, lo, hi, val = bl
                self.bl_range = (lo, hi)
                row = SliderRow(tr('brightness'), tr('brightness_backlight'), 1, 100, 1,
                                self._bl_pct(val), lambda v: f'{v:.0f}%', self._set_backlight)
                self.light.add(row)
                self.rows['backlight'] = row
        else:
            self.checking = Adw.ActionRow(title=tr('brightness'), subtitle=tr('checking'), sensitive=False)
            self.checking.add_suffix(Adw.Spinner())
            self.light.add(self.checking)

        # ── color ──
        self.color = Adw.PreferencesGroup(title=tr('group_color'))
        self.add(self.color)
        self._soft_row('contrast', tr('contrast_sw'), tr('contrast_sw_sub'), 1, signed_pct, st)
        self._soft_row('gamma', tr('gamma'), tr('gamma_sub'), 0.01, lambda v: f'{v:.2f}', st)
        self._soft_row('warmth', tr('warmth'), tr('warmth_sub'), 100, lambda v: f'{v:.0f}' + tr('kelvin_suffix'), st)
        if not win.saturation.available():
            sub = tr('saturation_missing')
        elif not win.saturation_loaded:
            sub = tr('saturation_relogin')
        else:
            sub = tr('saturation_sub')
        self._soft_row('saturation', tr('saturation'), sub, 5, signed_pct, st).set_sensitive(
            win.saturation.available())

        # ── presets and copy ──
        group = Adw.PreferencesGroup(title=tr('group_presets'))
        self.add(group)
        row = Adw.ActionRow(title=tr('presets'), subtitle=tr('presets_sub'))
        self.presets_button = Gtk.MenuButton(icon_name='view-more-symbolic', valign=Gtk.Align.CENTER,
                                             css_classes=['flat'])
        row.add_suffix(self.presets_button)
        row.set_activatable_widget(self.presets_button)
        group.add(row)
        if others:
            copy = Gio.Menu()
            for o in others:
                copy.append_item(menu_item(win.screen_name(o), 'win.copy-to', monitor.connector + SEP + o.connector))
            if len(others) > 1:
                copy.append_item(menu_item(tr('copy_all'), 'win.copy-to', monitor.connector + SEP + '*'))
            row = Adw.ActionRow(title=tr('copy_to'), subtitle=tr('copy_to_sub'))
            button = Gtk.MenuButton(icon_name='edit-copy-symbolic', valign=Gtk.Align.CENTER,
                                    css_classes=['flat'], menu_model=copy)
            row.add_suffix(button)
            row.set_activatable_widget(button)
            group.add(row)
        self.refresh_presets()

        # ── reset ──
        actions = Adw.PreferencesGroup(description=tr('reset_sub'))
        self.add(actions)
        reset = Adw.ButtonRow(title=tr('reset'))
        reset.connect('activated', lambda _r: win.apply_values(monitor, {}))
        actions.add(reset)

    def _describe(self):
        m = self.m
        text = f'{self.win.screen_name(m)} · \u2066{m.connector}\u2069'
        return text if m.active else f'{text} — {self.tr("inactive")}'

    def refresh_presets(self):
        tr, names = self.tr, self.win.store.presets()
        menu, apply = Gio.Menu(), Gio.Menu()
        for name in names:
            apply.append_item(menu_item(name, 'win.preset-apply', self.m.connector + SEP + name))
        if not names:
            apply.append(tr('presets_none'), 'win.nothing')
        menu.append_section(None, apply)
        manage = Gio.Menu()
        manage.append_item(menu_item(tr('preset_save'), 'win.preset-save', self.m.connector))
        if names:
            delete = Gio.Menu()
            for name in names:
                delete.append_item(menu_item(name, 'win.preset-delete', name))
            manage.append_submenu(tr('preset_delete'), delete)
        menu.append_section(None, manage)
        self.presets_button.set_menu_model(menu)

    def sync_rows(self):
        st = self.win.store.monitor(self.m.key)
        for name in B.SOFT_DEFAULTS:
            if name in self.rows:
                self.rows[name].set_value(st[name])

    # backlight (mutter)
    def _bl_pct(self, val):
        lo, hi = self.bl_range
        return max(1, min(100, round((val - lo) * 100 / (hi - lo))))

    def _set_backlight(self, pct):
        lo, hi = self.bl_range
        value = lo + (hi - lo) * pct / 100
        self.bl_touched = time.monotonic()
        self.win.worker.submit(('bl', self.m.connector),
                               lambda: self.win.mutter.set_backlight(self.m.connector, value))

    def backlight_changed(self):
        row = self.rows.get('backlight')
        if not row or time.monotonic() - getattr(self, 'bl_touched', 0) < 1.0:
            return                                  # our own change; don't fight the drag
        bl = self.win.mutter.backlight(self.m.connector)
        if bl:
            row.set_value(self._bl_pct(bl[3]))

    # DDC
    def ddc_ready(self, values):
        if not hasattr(self, 'checking'):
            return
        tr = self.tr
        self.light.remove(self.checking)
        del self.checking
        if 0x10 in values:
            cur, top = values[0x10]
            self.light.add(SliderRow(tr('brightness'), tr('brightness_ddc'), 0, top, 1, cur,
                                     lambda v: f'{v:.0f}', lambda v: self._set_ddc(0x10, v)))
            if 0x12 in values:
                cur, top = values[0x12]
                self.light.add(SliderRow(tr('contrast_hw'), tr('contrast_hw_sub'), 0, top, 1, cur,
                                         lambda v: f'{v:.0f}', lambda v: self._set_ddc(0x12, v)))
                self.rows['contrast'].set_title(tr('contrast_sw_extra'))
        else:
            st = self.win.store.monitor(self.m.key)
            self.rows['brightness_sw'] = SliderRow(
                tr('brightness'), tr('brightness_sw'), *B.LIMITS['brightness_sw'], 1, st['brightness_sw'],
                lambda v: f'{v:.0f}%', lambda v: self._set_soft('brightness_sw', v),
                default=B.SOFT_DEFAULTS['brightness_sw'], reset_tooltip=tr('reset_value'))
            self.light.add(self.rows['brightness_sw'])

    def _set_ddc(self, code, v):
        self.win.worker.submit(('ddc', self.m.connector, code),
                               lambda: self.win.ddc.set(self.m.connector, code, v))

    # software settings (VCGT + saturation)
    def _soft_row(self, name, title, subtitle, step, fmt, st):
        lo, hi = B.LIMITS[name]
        row = SliderRow(title, subtitle, lo, hi, step, st[name], fmt, lambda v: self._set_soft(name, v),
                        default=B.SOFT_DEFAULTS[name], reset_tooltip=self.tr('reset_value'))
        self.color.add(row)
        self.rows[name] = row
        return row

    def _set_soft(self, name, v):
        self.win.store.set(self.m.key, name, round(v, 2) if name == 'gamma' else int(round(v)))
        if name == 'saturation':
            self.win.schedule_saturation()
        else:
            self.win.schedule_vcgt(self.m)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=780, default_height=860, icon_name=B.APP_ID)
        self.store = B.Store()
        self.mutter, self.colord = B.Mutter(), B.Colord()
        self.ddc, self.saturation = B.DDC(), B.Saturation()
        self.worker = Worker()
        self.pages, self.monitors, self.timers = {}, [], {}
        self.ddc_values = None
        self.tr = Tr(self.store.data.get('lang', B.default_language()))

        self._action('language', self._set_language, 's', self.tr.lang)
        self._action('rescan', lambda *_: self.rebuild(rescan_ddc=True))
        self._action('test-pattern', self._test_pattern)
        self._action('export', self._export)
        self._action('import', self._import)
        self._action('about', self._about)
        self._action('preset-apply', self._preset_apply, 's')
        self._action('preset-save', self._preset_save, 's')
        self._action('preset-delete', self._preset_delete, 's')
        self._action('copy-to', self._copy_to, 's')

        self.mutter.on_monitors_changed(lambda: self._debounce('monitors', 1200, lambda: self.rebuild(True)))
        self.mutter.on_backlight_changed(self._backlight_changed)
        self.rebuild(rescan_ddc=True)

    def _action(self, name, callback, param=None, state=None):
        ptype = GLib.VariantType.new(param) if param else None
        if state is not None:
            action = Gio.SimpleAction.new_stateful(name, ptype, GLib.Variant(param, state))
        else:
            action = Gio.SimpleAction.new(name, ptype)
        action.connect('activate', callback)
        self.add_action(action)

    def screen_name(self, m):
        if m.builtin:
            return self.tr('builtin')
        # mutter names monitors like 'Dell Inc. 22"'; localize the size and isolate the
        # vendor so its punctuation doesn't jump sides in right-to-left text
        match = re.fullmatch(r'(.*?)\s+(\d+(?:\.\d+)?)"', m.name)
        if not match:
            return '\u2068' + m.name + '\u2069'
        name = self.tr('screen_size', vendor='\u2068' + match.group(1) + '\u2069', size=match.group(2))
        return ('\u200f' if self.tr.rtl else '') + name        # RLM: keep right-to-left order

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # ── building ──
    def rebuild(self, rescan_ddc=False):
        tr = self.tr
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if tr.rtl else Gtk.TextDirection.LTR)
        self.set_title(tr('app_title'))
        self.saturation_loaded = self.saturation.loaded()
        visible = self.stack.get_visible_child_name() if getattr(self, 'stack', None) else None
        try:
            self.monitors = self.mutter.monitors()
        except GLib.Error:
            self.monitors = []

        self.stack = Adw.ViewStack()
        self.pages = {}
        for m in self.monitors:
            page = MonitorPage(self, m, [o for o in self.monitors if o is not m])
            icon = 'computer-symbolic' if m.builtin else 'video-display-symbolic'
            self.stack.add_titled_with_icon(page, m.connector, self.screen_name(m), icon)
            self.pages[m.connector] = page
        if not self.monitors:
            self.stack.add_titled(Adw.StatusPage(title=tr('no_monitors'), icon_name='video-display-symbolic'),
                                  'none', tr('no_monitors'))
        if visible in self.pages:
            self.stack.set_visible_child_name(visible)

        languages = Gio.Menu()
        for code, name in LANGUAGES.items():
            languages.append_item(menu_item(name, 'win.language', code))
        menu = Gio.Menu()
        menu.append_submenu(tr('menu_language'), languages)
        section = Gio.Menu()
        section.append(tr('menu_test_pattern'), 'win.test-pattern')
        section.append(tr('menu_rescan'), 'win.rescan')
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(tr('menu_export'), 'win.export')
        section.append(tr('menu_import'), 'win.import')
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(tr('menu_about'), 'win.about')
        menu.append_section(None, section)

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True))
        self.colors_toggle = Gtk.ToggleButton(icon_name='video-display-symbolic',
                                              tooltip_text=tr('colors_toggle'), active=self.store.enabled())
        self.colors_toggle.connect('toggled', lambda b: self._set_enabled(b.get_active()))
        header.pack_start(self.colors_toggle)
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

        if rescan_ddc or self.ddc_values is None:
            self.ddc_values = None
            threading.Thread(target=self._detect_ddc, args=(self.monitors,), daemon=True).start()
        else:
            self._ddc_done(self.ddc_values)

    def _detect_ddc(self, monitors):
        values = {}
        try:
            self.ddc.detect()
            for m in monitors:
                if not m.builtin:
                    values[m.connector] = self.ddc.get(m.connector)
        except Exception:
            traceback.print_exc()
        GLib.idle_add(self._ddc_done, values)

    def _ddc_done(self, values):
        self.ddc_values = values
        for conn, page in self.pages.items():
            if not page.m.builtin:
                page.ddc_ready(values.get(conn, {}))
        return GLib.SOURCE_REMOVE

    def _backlight_changed(self):
        for page in self.pages.values():
            if page.m.builtin:
                page.backlight_changed()

    # ── applying in the background ──
    def _debounce(self, key, ms, fn):
        if key in self.timers:
            GLib.source_remove(self.timers[key])

        def fire():
            self.timers.pop(key, None)
            fn()
            return GLib.SOURCE_REMOVE
        self.timers[key] = GLib.timeout_add(ms, fire)

    def save_later(self):
        self.worker.submit('save', self.store.save)

    def schedule_vcgt(self, monitor):
        def job():
            B.apply_vcgt(self.colord, monitor, B.effective(self.store, monitor.key))
            self.store.save()
        self._debounce(('vcgt', monitor.connector), 250,
                       lambda: self.worker.submit(('vcgt', monitor.connector), job))

    def schedule_saturation(self):
        def run():
            self.saturation.sync(self.mutter.monitors(), self.store)
            self.save_later()
        self._debounce('saturation', 250, run)

    def _set_enabled(self, value):
        if self.store.enabled() == value:
            return
        self.store.set_enabled(value)
        self.save_later()
        for m in self.monitors:
            self.schedule_vcgt(m)
        self.schedule_saturation()
        self.toast(self.tr('colors_on' if value else 'colors_off'))

    def apply_values(self, monitor, values):
        self.store.set_all(monitor.key, values)
        page = self.pages.get(monitor.connector)
        if page:
            page.sync_rows()
        self.schedule_vcgt(monitor)
        self.schedule_saturation()

    def _monitor(self, connector):
        return next((m for m in self.monitors if m.connector == connector), None)

    # ── actions ──
    def _set_language(self, action, param):
        action.set_state(param)
        self.tr = Tr(param.unpack())
        use_language(self.tr.lang)
        self.store.data['lang'] = self.tr.lang
        self.save_later()
        self.rebuild()

    def _preset_apply(self, _action, param):
        connector, name = param.unpack().split(SEP, 1)
        monitor, values = self._monitor(connector), self.store.preset(name)
        if monitor and values is not None:
            self.apply_values(monitor, values)
            self.toast(self.tr('preset_applied', name='\u2068' + name + '\u2069'))

    def _preset_save(self, _action, param):
        monitor = self._monitor(param.unpack())
        if not monitor:
            return
        tr = self.tr
        dialog = Adw.AlertDialog(heading=tr('preset_dialog_title'),
                                 body=tr('preset_dialog_body', screen=self.screen_name(monitor)))
        entry = Gtk.Entry(placeholder_text=tr('preset_name'), activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', tr('cancel'))
        dialog.add_response('save', tr('save'))
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        dialog.set_response_enabled('save', False)
        entry.connect('changed', lambda e: dialog.set_response_enabled('save', bool(e.get_text().strip())))

        def on_response(_d, response):
            name = entry.get_text().strip()
            if response == 'save' and name:
                self.store.save_preset(name, self.store.monitor(monitor.key))
                self.save_later()
                for page in self.pages.values():
                    page.refresh_presets()
                self.toast(tr('preset_saved', name='\u2068' + name + '\u2069'))
        dialog.connect('response', on_response)
        dialog.present(self)
        entry.grab_focus()

    def _preset_delete(self, _action, param):
        name = param.unpack()
        self.store.delete_preset(name)
        self.save_later()
        for page in self.pages.values():
            page.refresh_presets()
        self.toast(self.tr('preset_deleted', name='\u2068' + name + '\u2069'))

    def _copy_to(self, _action, param):
        src, dst = param.unpack().split(SEP, 1)
        source = self._monitor(src)
        if not source:
            return
        values = self.store.monitor(source.key)
        targets = [m for m in self.monitors if m is not source] if dst == '*' else [self._monitor(dst)]
        for m in filter(None, targets):
            self.apply_values(m, values)
        label = self.tr('copy_all') if dst == '*' else self.screen_name(targets[0])
        self.toast(self.tr('copied', screen=label))

    def _test_pattern(self, *_):
        page = self.stack.get_visible_child()
        connector = page.m.connector if isinstance(page, MonitorPage) else None
        testpattern.show(connector, self.tr)

    def _export(self, *_):
        dialog = Gtk.FileDialog(title=self.tr('menu_export'), initial_name='display-tune-settings.json')

        def done(d, result):
            try:
                path = d.save_finish(result).get_path()
            except GLib.Error:
                return                                   # cancelled
            self.store.export(path)
            self.toast(self.tr('exported'))
        dialog.save(self, None, done)

    def _import(self, *_):
        dialog = Gtk.FileDialog(title=self.tr('menu_import'))

        def done(d, result):
            try:
                path = d.open_finish(result).get_path()
            except GLib.Error:
                return
            try:
                monitors, presets = self.store.import_file(path)
            except (OSError, ValueError) as e:
                print('import failed:', e, file=sys.stderr)
                self.toast(self.tr('import_failed'))
                return
            self.store.save()
            for m in self.monitors:
                self.apply_values(m, self.store.monitor(m.key))
            for page in self.pages.values():
                page.refresh_presets()
            self.toast(self.tr('imported', monitors=monitors, presets=presets))
        dialog.open(self, None, done)

    def _about(self, *_):
        # Built with our own strings: libadwaita's About dialog uses the system language
        # and has no translation for every language we support.
        tr = self.tr
        dialog = Adw.AlertDialog(heading=tr('app_title'))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Image(icon_name=B.APP_ID, pixel_size=96))
        for text, classes in ((tr('about_version', version=__version__), ['dim-label']),
                              (tr('app_summary'), []),
                              ('Ahmad Alqattu', ['caption-heading']),
                              (tr('credits_saturation'), ['caption', 'dim-label']),
                              (tr('about_license'), ['caption', 'dim-label'])):
            box.append(Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER, css_classes=classes))
        dialog.set_extra_child(box)
        dialog.add_response('website', tr('about_website'))
        dialog.add_response('close', tr('close'))
        dialog.set_default_response('close')
        dialog.set_close_response('close')

        def on_response(_d, response):
            if response == 'website':
                Gtk.UriLauncher.new(REPO).launch(self, None, None)
        dialog.connect('response', on_response)
        dialog.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=B.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        use_language(B.Store().data.get('lang', B.default_language()))
        win = self.get_active_window() or Window(self)
        win.present()


def run():
    return App().run(sys.argv[:1])
