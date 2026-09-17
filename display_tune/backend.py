"""Everything that talks to the system: mutter (displays, backlight), colord (VCGT),
ddcutil (monitor hardware controls) and the saturation shell extension."""
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import threading
import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

APP_ID = 'io.github.ahmad_alqattu.DisplayTune'
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), 'display-tune')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')
ICC_DIR = os.path.join(GLib.get_user_data_dir(), 'icc')
PREFIX = 'display-tune-'
OLD_PREFIXES = (PREFIX, 'screen-tune-')

EXT_UUID = 'display-tune-saturation@ahmad-alqattu.github.io'
EXT_SCHEMA = 'org.gnome.shell.extensions.display-tune-saturation'
# Other saturation shaders that must not run on top of ours.
CONFLICTING_EXTENSIONS = ('saturation-extension@zb3.me',)

SOFT_DEFAULTS = {'brightness_sw': 100, 'contrast': 0, 'gamma': 1.0, 'warmth': 6500, 'saturation': 0}
LIMITS = {'brightness_sw': (30, 100), 'contrast': (-30, 30), 'gamma': (0.8, 1.2),
          'warmth': (3000, 8000), 'saturation': (-50, 100)}
B_MIN, _B_MAX = LIMITS['brightness_sw']


def default_language():
    from .i18n import LANGUAGES        # local import: avoid a hard dependency for CLI-only use
    for name in GLib.get_language_names():
        code = name.split('_')[0].split('.')[0]
        if code in LANGUAGES:
            return code
    return 'en'


def clean_values(values):
    """Keep only known settings, clamped to their limits."""
    out = {}
    for name, (lo, hi) in LIMITS.items():
        if name in values:
            try:
                v = min(hi, max(lo, float(values[name])))
            except (TypeError, ValueError):
                continue
            out[name] = round(v, 2) if name == 'gamma' else int(round(v))
    return out


# ───────────────────────────── saved settings ─────────────────────────────

class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = self._load()

    @staticmethod
    def _load():
        data = {'lang': default_language(), 'monitors': {}, 'presets': {}, 'enabled': True}
        try:
            with open(SETTINGS_FILE) as f:
                data.update(json.load(f))
        except (OSError, ValueError):
            pass
        data.setdefault('monitors', {})
        data.setdefault('presets', {})
        data.setdefault('enabled', True)
        return data

    def reload(self):
        with self.lock:
            self.data = self._load()

    def enabled(self):
        with self.lock:
            return bool(self.data.get('enabled', True))

    def set_enabled(self, value):
        with self.lock:
            self.data['enabled'] = bool(value)

    def save(self):
        with self.lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = SETTINGS_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_FILE)

    # per monitor
    def monitor(self, key):
        with self.lock:
            return {**SOFT_DEFAULTS, **self.data['monitors'].get(key, {})}

    def set(self, key, name, value):
        with self.lock:
            m = self.data['monitors'].setdefault(key, {})
            if value == SOFT_DEFAULTS[name]:
                m.pop(name, None)
            else:
                m[name] = value

    def set_all(self, key, values):
        values = clean_values(values)
        with self.lock:
            self.data['monitors'][key] = {k: v for k, v in values.items() if v != SOFT_DEFAULTS[k]}

    def reset(self, key):
        with self.lock:
            self.data['monitors'].pop(key, None)

    # presets
    def presets(self):
        with self.lock:
            return sorted(self.data['presets'], key=str.casefold)

    def preset(self, name):
        with self.lock:
            p = self.data['presets'].get(name)
            return {**SOFT_DEFAULTS, **p} if p is not None else None

    def save_preset(self, name, values):
        with self.lock:
            self.data['presets'][name] = clean_values(values)

    def delete_preset(self, name):
        with self.lock:
            self.data['presets'].pop(name, None)

    # export / import
    def export(self, path):
        with self.lock:
            payload = {'app': APP_ID, 'monitors': self.data['monitors'], 'presets': self.data['presets']}
        with open(path, 'w') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def import_file(self, path):
        with open(path) as f:
            payload = json.load(f)
        if not isinstance(payload, dict) or not isinstance(payload.get('monitors', {}), dict):
            raise ValueError('not a Display Tune settings file')
        monitors = {k: clean_values(v) for k, v in payload.get('monitors', {}).items() if isinstance(v, dict)}
        presets = {k: clean_values(v) for k, v in payload.get('presets', {}).items() if isinstance(v, dict)}
        with self.lock:
            self.data['monitors'].update(monitors)
            self.data['presets'].update(presets)
        return len(monitors), len(presets)


# ───────────────────────────── mutter ─────────────────────────────

@dataclass
class Monitor:
    connector: str
    vendor: str
    product: str
    serial: str
    name: str
    builtin: bool
    active: bool

    @property
    def key(self):
        return f'{self.vendor}:{self.product}:{self.serial}'


class Mutter:
    NAME = 'org.gnome.Mutter.DisplayConfig'
    PATH = '/org/gnome/Mutter/DisplayConfig'

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def _call(self, method, args=None):
        return self.bus.call_sync(self.NAME, self.PATH, self.NAME, method, args,
                                  None, Gio.DBusCallFlags.NONE, -1, None).unpack()

    def monitors(self):
        _serial, monitors, logical, _props = self._call('GetCurrentState')
        active = {spec[0] for lm in logical for spec in lm[5]}
        out = []
        for spec, _modes, props in monitors:
            connector, vendor, product, serial = spec
            out.append(Monitor(connector, vendor, product, serial,
                               props.get('display-name', product),
                               bool(props.get('is-builtin', False)), connector in active))
        out.sort(key=lambda m: (not m.builtin, m.connector))
        return out

    def backlight(self, connector):
        """(serial, min, max, value) of the backlight mutter manages for this connector, or None."""
        try:
            serial, entries = self.bus.call_sync(
                self.NAME, self.PATH, 'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)', (self.NAME, 'Backlight')), None, 0, -1, None).unpack()[0]
        except GLib.Error:
            return None
        for e in entries:
            if e.get('connector') == connector and e.get('active', True):
                return serial, e['min'], e['max'], e['value']
        return None

    def set_backlight(self, connector, value):
        b = self.backlight(connector)
        if b:
            self._call('SetBacklight', GLib.Variant('(usi)', (b[0], connector, int(value))))

    def on_monitors_changed(self, cb):
        return self.bus.signal_subscribe(self.NAME, self.NAME, 'MonitorsChanged', self.PATH,
                                         None, Gio.DBusSignalFlags.NONE, lambda *a: cb())

    def on_backlight_changed(self, cb):
        def handler(_c, _s, _p, _i, _sig, params):
            iface, changed, _inv = params.unpack()
            if iface == self.NAME and 'Backlight' in changed:
                cb()
        return self.bus.signal_subscribe(self.NAME, 'org.freedesktop.DBus.Properties',
                                         'PropertiesChanged', self.PATH, None,
                                         Gio.DBusSignalFlags.NONE, handler)


# ───────────────────────────── VCGT curves ─────────────────────────────

def _kelvin_rgb(k):
    # Tanner Helland's black-body approximation
    t = k / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = (99.4708025861 * math.log(t) - 161.1195681661) if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return [min(255.0, max(0.0, v)) / 255.0 for v in (r, g, b)]


def warmth_gains(kelvin):
    """Per-channel gains relative to 6500K, normalised so the strongest channel is 1."""
    ref = _kelvin_rgb(6500)
    g = [c / r for c, r in zip(_kelvin_rgb(kelvin), ref)]
    top = max(g)
    return [v / top for v in g]


def is_neutral(s):
    return (s['contrast'] == 0 and abs(s['gamma'] - 1.0) < 1e-6 and
            s['warmth'] == 6500 and s['brightness_sw'] == 100)


def effective(store, key):
    """The settings actually applied to a display. When the global colors toggle is
    off, every 'look' adjustment is bypassed but software brightness is kept, since for
    a display with no hardware brightness control that IS its real brightness, not a
    stylistic tweak."""
    s = store.monitor(key)
    if store.enabled():
        return s
    return {**SOFT_DEFAULTS, 'brightness_sw': s['brightness_sw']}


def ramps(s, size=256):
    a = 2 * s['contrast'] / 100.0              # mid-tone slope = 1 + contrast%
    gains = warmth_gains(s['warmth'])
    scale = s['brightness_sw'] / 100.0
    out = []
    for ch in range(3):
        vals = []
        for i in range(size):
            x = i / (size - 1)
            y = (1 - a) * x + a * (3 * x * x - 2 * x ** 3)   # S-curve, keeps 0 and 1 fixed
            y = min(1.0, max(0.0, y)) ** (1.0 / s['gamma'])
            vals.append(round(min(1.0, max(0.0, y * gains[ch] * scale)) * 65535))
        out.append(vals)
    return out


def _mluc(text):
    raw = text.encode('utf-16-be')
    return b'mluc' + bytes(4) + struct.pack('>II', 1, 12) + b'enUS' + struct.pack('>II', len(raw), 28) + raw


def build_icc(edid_icc, s, title):
    """Copy of mutter's EDID profile (same colorimetry) plus a vcgt tag."""
    d = open(edid_icc, 'rb').read()
    n = struct.unpack('>I', d[128:132])[0]
    raw = {}
    for i in range(n):
        sig, off, size = struct.unpack('>4sII', d[132 + 12 * i:144 + 12 * i])
        raw[sig] = d[off:off + size]
    keep = [b'wtpt', b'chad', b'rXYZ', b'gXYZ', b'bXYZ', b'rTRC', b'gTRC', b'bTRC', b'chrm']
    vcgt = b'vcgt' + bytes(4) + struct.pack('>IHHH', 0, 3, 256, 2) + b''.join(
        struct.pack('>256H', *ch) for ch in ramps(s))
    tags = [(b'desc', _mluc(title)), (b'cprt', _mluc('No copyright, generated by Display Tune'))]
    tags += [(sig, raw[sig]) for sig in keep if sig in raw]
    tags.append((b'vcgt', vcgt))

    body, entries, off, seen = b'', [], 128 + 4 + 12 * len(tags), {}
    for sig, data in tags:
        if data in seen:                       # rTRC/gTRC/bTRC usually share one curve
            entries.append((sig, seen[data], len(data)))
            continue
        pad = (-len(data)) % 4
        entries.append((sig, off, len(data)))
        seen[data] = off
        body += data + bytes(pad)
        off += len(data) + pad
    header = bytearray(d[:128])
    header[0:4] = struct.pack('>I', off)
    header[84:100] = bytes(16)                 # profile ID 0 = not computed
    table = struct.pack('>I', len(tags)) + b''.join(struct.pack('>4sII', *e) for e in entries)
    return bytes(header) + table + body


# ───────────────────────────── colord ─────────────────────────────

class Colord:
    NAME = 'org.freedesktop.ColorManager'

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)

    def _call(self, path, iface, method, args=None):
        return self.bus.call_sync(self.NAME, path, iface, method, args, None, 0, -1, None).unpack()

    def _prop(self, path, iface, name):
        return self._call(path, 'org.freedesktop.DBus.Properties', 'Get',
                          GLib.Variant('(ss)', (iface, name)))[0]

    def device_for(self, connector):
        for dev in self._call('/org/freedesktop/ColorManager', self.NAME, 'GetDevicesByKind',
                              GLib.Variant('(s)', ('display',)))[0]:
            if self._prop(dev, self.NAME + '.Device', 'Metadata').get('XRANDR_name') == connector:
                return dev
        return None

    def profiles(self, dev):
        return list(self._prop(dev, self.NAME + '.Device', 'Profiles'))

    def filename(self, profile):
        return self._prop(profile, self.NAME + '.Profile', 'Filename')

    def edid_profile(self, dev):
        for p in self.profiles(dev):
            if self._prop(p, self.NAME + '.Profile', 'Metadata').get('DATA_source') == 'edid':
                return p
        return None

    def find(self, filename):
        try:
            return self._call('/org/freedesktop/ColorManager', self.NAME, 'FindProfileByFilename',
                              GLib.Variant('(s)', (filename,)))[0]
        except GLib.Error:
            return None

    def add(self, dev, profile):
        try:
            self._call(dev, self.NAME + '.Device', 'AddProfile', GLib.Variant('(so)', ('hard', profile)))
        except GLib.Error as e:
            if 'already' not in e.message.lower():
                raise

    def make_default(self, dev, profile):
        self._call(dev, self.NAME + '.Device', 'MakeProfileDefault', GLib.Variant('(o)', (profile,)))

    def remove(self, dev, profile):
        try:
            self._call(dev, self.NAME + '.Device', 'RemoveProfile', GLib.Variant('(o)', (profile,)))
        except GLib.Error:
            pass


def apply_vcgt(colord, monitor, s):
    """Apply contrast/gamma/warmth/software brightness. Idempotent. Returns False if the
    colord device or its EDID profile does not exist yet (e.g. right after login)."""
    dev = colord.device_for(monitor.connector)
    if not dev:
        return False
    edid = colord.edid_profile(dev)
    if not edid:
        return False
    current = colord.profiles(dev)
    ours = [(p, colord.filename(p)) for p in current]
    ours = [(p, f) for p, f in ours if os.path.basename(f).startswith(OLD_PREFIXES)]

    if is_neutral(s):
        if current and current[0] != edid:
            colord.make_default(dev, edid)
        target = None
    else:
        tag = hashlib.md5(json.dumps([s[k] for k in sorted(SOFT_DEFAULTS)]).encode()).hexdigest()[:10]
        safe = re.sub(r'[^A-Za-z0-9]+', '_', monitor.key)
        target = os.path.join(ICC_DIR, f'{PREFIX}{safe}-{tag}.icc')
        profile = colord.find(target) if os.path.exists(target) else None
        if not profile:
            os.makedirs(ICC_DIR, exist_ok=True)
            title = (f'Display Tune {monitor.connector}: contrast {s["contrast"]:+g}, gamma {s["gamma"]:g}, '
                     f'{s["warmth"]}K, brightness {s["brightness_sw"]}%')
            with open(target + '.part', 'wb') as f:
                f.write(build_icc(colord.filename(edid), s, title))
            os.replace(target + '.part', target)
            for _ in range(40):                # colord watches the directory and picks the file up
                profile = colord.find(target)
                if profile:
                    break
                time.sleep(0.1)
            if not profile:
                return False
        if not current or current[0] != profile:
            colord.add(dev, profile)
            colord.make_default(dev, profile)

    for p, f in ours:                          # clean up profiles we no longer use
        if f != target:
            colord.remove(dev, p)
            try:
                os.remove(f)
            except OSError:
                pass
    return True


# ───────────────────────────── ddcutil ─────────────────────────────

class DDC:
    BRIGHTNESS, CONTRAST = 0x10, 0x12

    def __init__(self):
        self.lock = threading.Lock()
        self.buses = {}

    @staticmethod
    def available():
        return bool(GLib.find_program_in_path('ddcutil'))

    def detect(self):
        """Map connector -> I2C bus. Bus numbers can change between boots."""
        buses = {}
        if self.available():
            with self.lock:
                out = subprocess.run(['ddcutil', 'detect', '--terse'], capture_output=True,
                                     text=True, timeout=60).stdout
            for block in re.split(r'\n\s*\n', out):
                if not block.lstrip().startswith('Display'):
                    continue
                bus = re.search(r'/dev/i2c-(\d+)', block)
                conn = re.search(r'DRM[_ ]connector:\s+card\d+-(\S+)', block)
                if bus and conn:
                    buses[conn.group(1)] = int(bus.group(1))
        self.buses = buses
        return buses

    def get(self, connector):
        bus = self.buses.get(connector)
        if bus is None:
            return {}
        with self.lock:
            out = subprocess.run(['ddcutil', '--bus', str(bus), '--brief', 'getvcp', '10', '12'],
                                 capture_output=True, text=True, timeout=20).stdout
        return {int(m.group(1), 16): (int(m.group(2)), int(m.group(3)))
                for m in re.finditer(r'VCP ([0-9A-Fa-f]{2}) C (\d+) (\d+)', out)}

    def set(self, connector, code, value):
        bus = self.buses.get(connector)
        if bus is None:
            return False
        with self.lock:
            r = subprocess.run(['ddcutil', '--bus', str(bus), '--noverify', 'setvcp',
                                f'{code:02x}', str(int(value))], capture_output=True, timeout=20)
        return r.returncode == 0


# ───────────────────────────── saturation (shell extension) ─────────────────────────────

class Saturation:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.settings = None
        dirs = [os.path.join(GLib.get_user_data_dir(), 'gnome-shell', 'extensions', EXT_UUID, 'schemas')]
        dirs += [os.path.join(d, 'gnome-shell', 'extensions', EXT_UUID, 'schemas') for d in GLib.get_system_data_dirs()]
        for d in dirs:
            if os.path.isdir(d):
                src = Gio.SettingsSchemaSource.new_from_directory(d, Gio.SettingsSchemaSource.get_default(), False)
                schema = src.lookup(EXT_SCHEMA, False)
                if schema:
                    self.settings = Gio.Settings.new_full(schema, None, None)
                    break

    def available(self):
        return self.settings is not None

    def _shell(self, method, uuid):
        return self.bus.call_sync('org.gnome.Shell', '/org/gnome/Shell', 'org.gnome.Shell.Extensions',
                                  method, GLib.Variant('(s)', (uuid,)), None, 0, -1, None).unpack()

    def _info(self, uuid):
        try:
            return self._shell('GetExtensionInfo', uuid)[0]
        except GLib.Error:
            return {}

    def loaded(self):
        """GNOME Shell only discovers newly installed extensions at login."""
        return bool(self._info(EXT_UUID))

    def sync(self, monitors, store):
        if not self.available():
            return
        for uuid in CONFLICTING_EXTENSIONS:
            if self._info(uuid).get('enabled'):
                try:
                    self._shell('DisableExtension', uuid)
                except GLib.Error:
                    pass
        active = [m for m in monitors if m.active]
        pcts = [effective(store, m.key)['saturation'] for m in active]
        want = any(pcts)
        if want:
            # The shader amplifies: stored s gives saturation 1 + (s - 1) * 5.
            # Index 0 is the global value, the rest follow monitor-ids.
            factors = [1.0] + [1 + p / 500.0 for p in pcts]
            ids = [m.connector for m in active]
            if self.settings.get_strv('monitor-ids') != ids:
                self.settings.set_strv('monitor-ids', ids)
            cur = self.settings.get_value('saturation-factors').unpack()
            if len(cur) != len(factors) or any(abs(a - b) > 1e-6 for a, b in zip(cur, factors)):
                self.settings.set_value('saturation-factors', GLib.Variant('ad', factors))
            if not self.settings.get_boolean('use-per-monitor-settings'):
                self.settings.set_boolean('use-per-monitor-settings', True)
        try:
            if want != bool(self._info(EXT_UUID).get('enabled')):
                self._shell('EnableExtension' if want else 'DisableExtension', EXT_UUID)
        except GLib.Error:
            pass


# ───────────────────────────── apply everything ─────────────────────────────

def apply_all(store, mutter, colord, saturation):
    """Returns (monitors, ok). ok is False when some colord device was not ready."""
    monitors = mutter.monitors()
    ok = True
    for m in monitors:
        try:
            ok = apply_vcgt(colord, m, effective(store, m.key)) and ok
        except (GLib.Error, OSError) as e:
            print(f'vcgt {m.connector}: {e}', flush=True)
    saturation.sync(monitors, store)
    return monitors, ok


# ───────────────────────────── brightness (any channel) ─────────────────────────────

def brightness_channel(mutter, ddc, store, monitor):
    """The brightness control this monitor actually has, as (kind, percent 0-100).
    kind is 'mutter' (real backlight), 'ddc' (monitor hardware) or 'sw' (software dimming)."""
    if monitor.builtin:
        bl = mutter.backlight(monitor.connector)
        if bl:
            _serial, lo, hi, val = bl
            return 'mutter', round((val - lo) * 100 / (hi - lo)) if hi > lo else 100
    elif monitor.connector in ddc.buses:
        values = ddc.get(monitor.connector)
        if DDC.BRIGHTNESS in values:
            cur, top = values[DDC.BRIGHTNESS]
            return 'ddc', round(cur * 100 / top) if top else 0
    return 'sw', store.monitor(monitor.key)['brightness_sw']


def set_brightness_channel(mutter, ddc, store, monitor, percent):
    """Set brightness through whichever channel this monitor has. Returns True when it
    changed a saved software setting, meaning the caller must re-apply the VCGT and save."""
    percent = max(0, min(100, int(round(percent))))
    if monitor.builtin:
        bl = mutter.backlight(monitor.connector)
        if bl:
            _serial, lo, hi, _val = bl
            mutter.set_backlight(monitor.connector, lo + (hi - lo) * percent / 100)
            return False
    elif monitor.connector in ddc.buses:
        ddc.set(monitor.connector, DDC.BRIGHTNESS, percent)
        return False
    store.set(monitor.key, 'brightness_sw', max(B_MIN, percent))
    return True
