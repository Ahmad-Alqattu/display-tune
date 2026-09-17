import argparse
import json
import sys

from . import __version__


def main():
    ap = argparse.ArgumentParser(prog='display-tune',
                                 description='Per-display brightness, contrast, gamma, warmth and saturation for GNOME.')
    ap.add_argument('--daemon', action='store_true', help='run the background service')
    ap.add_argument('--apply', action='store_true', help='re-apply saved settings once and exit')
    ap.add_argument('--list', action='store_true', help='list displays and saved presets')
    ap.add_argument('--list-json', action='store_true', help='same, as JSON (used by the Quick Settings extension)')
    ap.add_argument('--preset', metavar='NAME', help='apply a saved preset')
    ap.add_argument('--reset', action='store_true', help='reset colors to the defaults')
    ap.add_argument('--monitor', metavar='CONNECTOR', default='all',
                    help='display for --preset/--reset/--set-brightness, e.g. eDP-1 '
                         '(--preset/--reset default to every active display)')
    ap.add_argument('--set-brightness', metavar='PERCENT', type=int,
                    help='set brightness (0-100) through whatever channel --monitor has; requires --monitor')
    enable = ap.add_mutually_exclusive_group()
    enable.add_argument('--enable', dest='enabled', action='store_true', default=None,
                        help='turn color adjustments on')
    enable.add_argument('--disable', dest='enabled', action='store_false',
                        help='turn color adjustments off, temporarily reverting to the original colors')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = ap.parse_args()

    if args.daemon:
        from . import daemon
        return daemon.run()
    busy = (args.apply or args.list or args.list_json or args.preset or args.reset
            or args.set_brightness is not None or args.enabled is not None)
    if not busy:
        from . import ui
        return ui.run()

    from . import backend as B
    store, mutter, colord, ddc, saturation = B.Store(), B.Mutter(), B.Colord(), B.DDC(), B.Saturation()
    ddc.detect()
    monitors = mutter.monitors()

    if args.list or args.list_json:
        rows = []
        for m in monitors:
            s = store.monitor(m.key)
            kind, percent = B.brightness_channel(mutter, ddc, store, m)
            rows.append({'connector': m.connector, 'name': m.name, 'builtin': m.builtin, 'active': m.active,
                        'brightness_kind': kind, 'brightness': percent, **s})
        if args.list_json:
            print(json.dumps({'enabled': store.enabled(), 'monitors': rows, 'presets': store.presets()}))
        else:
            for r in rows:
                print(f'{r["connector"]:8} {r["name"]}{"" if r["active"] else " (off)"}: '
                      f'brightness {r["brightness"]}% ({r["brightness_kind"]}), '
                      f'contrast {r["contrast"]:+d}%, gamma {r["gamma"]:.2f}, {r["warmth"]}K, '
                      f'saturation {r["saturation"]:+d}%')
            print('color adjustments:', 'on' if store.enabled() else 'off (comparing with the original)')
            print('presets:', ', '.join(store.presets()) or '(none)')
        return 0

    if args.set_brightness is not None:
        if args.monitor == 'all':
            print('--set-brightness needs --monitor CONNECTOR; see --list', file=sys.stderr)
            return 1
        target = next((m for m in monitors if m.connector == args.monitor), None)
        if not target:
            print(f'no display named {args.monitor}; see --list', file=sys.stderr)
            return 1
        if B.set_brightness_channel(mutter, ddc, store, target, args.set_brightness):
            store.save()
        else:
            return 0                                # hardware channel: nothing left to apply

    if args.enabled is not None:
        store.set_enabled(args.enabled)
        store.save()

    if args.preset or args.reset:
        if args.monitor == 'all':
            targets = [m for m in monitors if m.active]
        else:
            targets = [m for m in monitors if m.connector == args.monitor]
            if not targets:
                print(f'no display named {args.monitor}; see --list', file=sys.stderr)
                return 1
        if args.preset:
            values = store.preset(args.preset)
            if values is None:
                print(f'no preset named {args.preset}; see --list', file=sys.stderr)
                return 1
        for m in targets:
            if args.reset:
                store.reset(m.key)
            else:
                store.set_all(m.key, values)
        store.save()

    _, ok = B.apply_all(store, mutter, colord, saturation)
    return 0 if ok else 2


sys.exit(main())
