import GLib from 'gi://GLib';

// Kept in sync by hand with display_tune/i18n.py's matching keys.
const STRINGS = {
    en: {
        title: 'Display Tune',
        builtin: 'Laptop screen',
        openApp: 'Open Display Tune',
        resetAll: 'Reset All Colors',
        presetsNone: 'No saved presets',
        cliMissing: 'Display Tune is not installed correctly',
    },
    ar: {
        title: 'ضبط الشاشات',
        builtin: 'شاشة اللابتوب',
        openApp: 'افتح ضبط الشاشات',
        resetAll: 'رجّع كل الألوان',
        presetsNone: 'ما في إعدادات محفوظة',
        cliMissing: 'ضبط الشاشات مش مركّب صح',
    },
    ru: {
        title: 'Настройка экранов',
        builtin: 'Экран ноутбука',
        openApp: 'Открыть «Настройку экранов»',
        resetAll: 'Сбросить все цвета',
        presetsNone: 'Нет сохранённых профилей',
        cliMissing: 'Display Tune установлена некорректно',
    },
    de: {
        title: 'Bildschirmanpassung',
        builtin: 'Laptop-Bildschirm',
        openApp: 'Bildschirmanpassung öffnen',
        resetAll: 'Alle Farben zurücksetzen',
        presetsNone: 'Noch keine gespeicherten Profile',
        cliMissing: 'Display Tune ist nicht korrekt installiert',
    },
};

function pickLanguage() {
    for (const name of GLib.get_language_names()) {
        const lang = name.split(/[_.]/)[0];
        if (lang in STRINGS)
            return lang;
    }
    return 'en';
}

const LANG = pickLanguage();

export function tr(key) {
    return STRINGS[LANG][key] ?? STRINGS.en[key] ?? key;
}
