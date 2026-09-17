// Quick Settings toggle for Display Tune. All the actual work (colord, ddcutil, the
// saturation extension) stays in the display-tune CLI/daemon; this extension only reads
// ~/.config/display-tune/settings.json and shells out to `display-tune`, so there is a
// single, already-tested implementation of every setting instead of a second one in JS.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

import {tr} from './i18n.js';

// Prefer the fixed install.sh location so the extension works even when gnome-shell's
// own process environment doesn't have ~/.local/bin on PATH (common: it is added by the
// shell profile, which gnome-shell itself does not source).
const CLI = (() => {
    const local = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'display-tune']);
    return GLib.file_test(local, GLib.FileTest.IS_EXECUTABLE) ? local : 'display-tune';
})();
const APP_ID = 'io.github.ahmad_alqattu.DisplayTune';
const SETTINGS_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'display-tune', 'settings.json']);
const BRIGHTNESS_DEBOUNCE_MS = 120;

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');

/** Run the CLI and return its stdout, or null if it failed or isn't installed. */
async function run(args) {
    let proc;
    try {
        proc = Gio.Subprocess.new([CLI, ...args], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    } catch (e) {
        console.error(`display-tune: ${e.message}`);
        return null;
    }
    try {
        const [stdout, stderr] = await proc.communicate_utf8_async(null, null);
        if (!proc.get_successful()) {
            console.error(`display-tune ${args.join(' ')}: ${stderr.trim()}`);
            return null;
        }
        return stdout;
    } catch (e) {
        console.error(`display-tune ${args.join(' ')}: ${e.message}`);
        return null;
    }
}

function readEnabled() {
    try {
        const [ok, data] = Gio.File.new_for_path(SETTINGS_FILE).load_contents(null);
        if (!ok)
            return true;
        const json = JSON.parse(new TextDecoder().decode(data));
        return json.enabled !== false;
    } catch {
        return true;                                // no settings file yet: nothing to bypass
    }
}

function monitorLabel(m) {
    return m.builtin ? tr('builtin') : m.name;
}

class SliderSection extends PopupMenu.PopupMenuSection {
    addSlider(name, initialPercent, onChange) {
        const label = new PopupMenu.PopupMenuItem(name, {reactive: false});
        this.addMenuItem(label);

        const slider = new Slider(initialPercent / 100);
        slider.accessible_name = name;
        slider.connect('notify::value', () => onChange(slider.value * 100));
        slider.connect('drag-end', () => onChange(slider.value * 100, true));

        const sliderBin = new St.Bin({
            style_class: 'slider-bin', child: slider, reactive: true, can_focus: true,
            x_expand: true, y_align: Clutter.ActorAlign.CENTER,
        });
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false});
        item.add_child(sliderBin);
        this.addMenuItem(item);
        return slider;
    }
}

const DisplayTuneToggle = GObject.registerClass(
class DisplayTuneToggle extends QuickMenuToggle {
    _init() {
        super._init({
            title: tr('title'),
            iconName: 'video-display-symbolic',
            toggleMode: true,
        });

        this.menu.setHeader('video-display-symbolic', tr('title'));

        this._presetsSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._presetsSection);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._slidersSection = new SliderSection();
        this.menu.addMenuItem(this._slidersSection);
        this._slidersSeparator = new PopupMenu.PopupSeparatorMenuItem();
        this.menu.addMenuItem(this._slidersSeparator);

        this.menu.addAction(tr('resetAll'), () => run(['--reset']));
        this.menu.addAction(tr('openApp'), () => this._openApp());

        this._brightnessTimers = new Map();

        this.connect('clicked', () => run([this.checked ? '--enable' : '--disable']));
        this.connect('destroy', () => this._onDestroy());
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refreshMenu();
        });

        const dir = Gio.File.new_for_path(GLib.path_get_dirname(SETTINGS_FILE));
        GLib.mkdir_with_parents(dir.get_path(), 0o755);
        this._fileMonitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null);
        this._fileMonitorId = this._fileMonitor.connect('changed', (_m, file) => {
            if (file.get_basename() === 'settings.json')
                this._refreshChecked();
        });

        this._refreshChecked();
    }

    _refreshChecked() {
        const checked = readEnabled();
        if (this.checked !== checked)
            this.set({checked});
    }

    async _refreshMenu() {
        const stdout = await run(['--list-json']);
        this._presetsSection.removeAll();
        this._slidersSection.removeAll();

        if (stdout === null) {
            this._presetsSection.addMenuItem(new PopupMenu.PopupMenuItem(tr('cliMissing'), {reactive: false}));
            this._slidersSeparator.visible = false;
            return;
        }

        let data;
        try {
            data = JSON.parse(stdout);
        } catch (e) {
            console.error(`display-tune --list-json: ${e.message}`);
            return;
        }

        if (this.checked !== data.enabled)
            this.set({checked: data.enabled});

        if (data.presets.length === 0) {
            this._presetsSection.addMenuItem(new PopupMenu.PopupMenuItem(tr('presetsNone'), {reactive: false}));
        } else {
            for (const name of data.presets)
                this._presetsSection.addAction(name, () => run(['--preset', name]));
        }

        this._slidersSeparator.visible = data.monitors.length > 0;
        for (const m of data.monitors) {
            this._slidersSection.addSlider(monitorLabel(m), m.brightness,
                (value, immediate) => this._setBrightness(m.connector, value, immediate));
        }
    }

    /** Debounced while dragging; sent right away on 'drag-end' so release feels instant. */
    _setBrightness(connector, percent, immediate = false) {
        const existing = this._brightnessTimers.get(connector);
        if (existing)
            GLib.source_remove(existing);
        this._brightnessTimers.delete(connector);

        const send = () => run(['--set-brightness', String(Math.round(percent)), '--monitor', connector]);
        if (immediate) {
            send();
            return;
        }
        this._brightnessTimers.set(connector, GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, BRIGHTNESS_DEBOUNCE_MS, () => {
                this._brightnessTimers.delete(connector);
                send();
                return GLib.SOURCE_REMOVE;
            }));
    }

    _openApp() {
        const info = Gio.DesktopAppInfo.new(`${APP_ID}.desktop`);
        if (info)
            info.launch([], global.create_app_launch_context(0, -1));
        else
            run([]);
        this.menu.close();
    }

    _onDestroy() {
        for (const id of this._brightnessTimers.values())
            GLib.source_remove(id);
        this._brightnessTimers.clear();
        this._fileMonitor.disconnect(this._fileMonitorId);
        this._fileMonitor.cancel();
    }
});

const Indicator = GObject.registerClass(
class Indicator extends SystemIndicator {
    _init() {
        super._init();
        this.quickSettingsItems.push(new DisplayTuneToggle());
    }
});

export default class DisplayTuneQuickSettingsExtension extends Extension {
    enable() {
        this._indicator = new Indicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;
    }
}
