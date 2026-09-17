"""Background service: re-applies saved settings at login and whenever displays change."""
from gi.repository import GLib

from . import backend as B


def run():
    store, mutter = B.Store(), B.Mutter()
    colord, saturation = B.Colord(), B.Saturation()
    state = {'timer': 0, 'retries': 0}

    def apply():
        state['timer'] = 0
        store.reload()
        try:
            monitors, ok = B.apply_all(store, mutter, colord, saturation)
            print('applied:', ', '.join(m.connector + ('' if m.active else ' (off)') for m in monitors), flush=True)
        except GLib.Error as e:
            print('error:', e.message, flush=True)
            ok = False
        if ok:
            state['retries'] = 0
        elif state['retries'] < 6:                 # colord creates devices a bit after login
            state['retries'] += 1
            schedule(5000)
        return GLib.SOURCE_REMOVE

    def schedule(ms):
        if state['timer']:
            GLib.source_remove(state['timer'])
        state['timer'] = GLib.timeout_add(ms, apply)

    mutter.on_monitors_changed(lambda: schedule(2000))   # hotplug emits several signals in a row
    schedule(1000)
    GLib.MainLoop().run()
