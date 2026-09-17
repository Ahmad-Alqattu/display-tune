Name:           display-tune
Version:        1.0.0
Release:        1%{?dist}
Summary:        Per-display brightness, contrast, gamma, warmth and saturation for GNOME

License:        GPL-2.0-or-later
URL:            https://github.com/Ahmad-Alqattu/display-tune
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  glib2
BuildRequires:  libappstream-glib

Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       colord
Requires:       gnome-shell
Recommends:     ddcutil

%{?systemd_requires}
BuildRequires:  systemd-rpm-macros

%description
Display Tune gives every connected display its own brightness, contrast,
gamma, color warmth and saturation. Laptop brightness goes through mutter's
real backlight control, external monitors through DDC/CI (ddcutil) when
supported, contrast/gamma/warmth/software-brightness through a generated
VCGT color profile applied via colord, and saturation through a bundled,
flicker-free GNOME Shell extension. A second Quick Settings extension adds
a toggle next to Wi-Fi and Bluetooth. The interface is available in
English, Arabic, Russian and German.

%prep
%autosetup -n %{name}-%{version}

%build
python3 -m compileall -q display_tune
for f in extension/*/schemas; do
  [ -d "$f" ] && glib-compile-schemas "$f"
done

%install
# app
install -Dm644 -t %{buildroot}%{_datadir}/%{name}/display_tune display_tune/*.py
mkdir -p %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<EOF
#!/bin/sh
PYTHONPATH="%{_datadir}/%{name}\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m display_tune "\$@"
EOF
chmod 755 %{buildroot}%{_bindir}/%{name}

# desktop file, icon
sed "s#@BIN@#%{_bindir}#g" data/io.github.ahmad_alqattu.DisplayTune.desktop.in \
  > io.github.ahmad_alqattu.DisplayTune.desktop
desktop-file-install --dir=%{buildroot}%{_datadir}/applications \
  io.github.ahmad_alqattu.DisplayTune.desktop
install -Dm644 data/io.github.ahmad_alqattu.DisplayTune.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.ahmad_alqattu.DisplayTune.svg
install -Dm644 data/io.github.ahmad_alqattu.DisplayTune.metainfo.xml \
  %{buildroot}%{_datadir}/metainfo/io.github.ahmad_alqattu.DisplayTune.metainfo.xml

# GNOME Shell extensions (system-wide: available to every user)
for uuid in extension/*/; do
  uuid="$(basename "$uuid")"
  target=%{buildroot}%{_datadir}/gnome-shell/extensions/$uuid
  mkdir -p "$target"
  cp -r "extension/$uuid/." "$target/"
done

# systemd --user service, enabled by default for new sessions via a preset;
# existing sessions need one `systemctl --user enable --now display-tune.service`
sed "s#@BIN@#%{_bindir}#g" data/display-tune.service.in > %{name}.service
install -Dm644 %{name}.service %{buildroot}%{_userunitdir}/%{name}.service
echo "enable %{name}.service" > 50-%{name}.preset
install -Dm644 50-%{name}.preset %{buildroot}%{_userpresetdir}/50-%{name}.preset

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.ahmad_alqattu.DisplayTune.desktop
appstream-util validate-relax --nonet \
  %{buildroot}%{_datadir}/metainfo/io.github.ahmad_alqattu.DisplayTune.metainfo.xml
python3 -m py_compile display_tune/*.py

%post
%systemd_user_post %{name}.service
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :
update-desktop-database -q %{_datadir}/applications &>/dev/null || :

%postun
%systemd_user_postun %{name}.service
if [ $1 -eq 0 ]; then
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :
  update-desktop-database -q %{_datadir}/applications &>/dev/null || :
fi

%files
%license LICENSE
%doc README.md README.ar.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/io.github.ahmad_alqattu.DisplayTune.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.ahmad_alqattu.DisplayTune.svg
%{_datadir}/metainfo/io.github.ahmad_alqattu.DisplayTune.metainfo.xml
%{_datadir}/gnome-shell/extensions/display-tune-saturation@ahmad-alqattu.github.io/
%{_datadir}/gnome-shell/extensions/display-tune-quicksettings@ahmad-alqattu.github.io/
%{_userunitdir}/%{name}.service
%{_userpresetdir}/50-%{name}.preset

%changelog
* Thu Sep 17 2026 Ahmad Alqattu <19889053+Ahmad-Alqattu@users.noreply.github.com> - 1.0.0-1
- Initial package: per-display brightness/contrast/gamma/warmth/saturation,
  Quick Settings toggle, presets, export/import, English/Arabic/Russian/German
