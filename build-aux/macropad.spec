%global app_id io.github.djshiye.Macropad

Name:           macropad
Version:        1.0.0
# CI appends .<commit count> so every build from main is a newer release.
Release:        1%{?release_suffix}%{?dist}
Summary:        Rebind the keys and wheel of the SDINNOVATION 6-key macro pad

License:        MIT
URL:            https://github.com/djshiye/macropad-fedora
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
# for the import check in %%check
BuildRequires:  python3-evdev
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  systemd-rpm-macros

Requires:       python3-gobject
Requires:       python3-evdev
Requires:       gtk4
Requires:       libadwaita >= 1.6
Requires:       hicolor-icon-theme
# Tools the daemon runs for notifications, apps, files and pasting text
Requires:       /usr/bin/notify-send
Requires:       xdg-utils
Requires:       wl-clipboard
Requires:       /usr/bin/gtk-launch
Recommends:     ptyxis

%description
A small per-user daemon grabs the SDINNOVATION SIDE-KEYBOARD (USB 0816:246f)
and runs the action bound to each key and wheel direction: shortcuts, text,
commands, apps, terminals, layer switches or chains of steps. The Macropad
app (GTK 4 / libadwaita) edits the bindings and can store shortcuts in the
pad's own memory.

%prep
%autosetup -n %{name}-%{version}

%build

%install
install -d %{buildroot}%{python3_sitelib}/macropad
install -pm 0644 macropad/*.py %{buildroot}%{python3_sitelib}/macropad/

install -d %{buildroot}%{_bindir} %{buildroot}%{_libexecdir}
printf '#!%{python3}\nfrom macropad.gui import main\nmain()\n' > %{buildroot}%{_bindir}/macropad-settings
printf '#!%{python3}\nfrom macropad.daemon import main\nmain()\n' > %{buildroot}%{_libexecdir}/macropad-daemon
chmod 0755 %{buildroot}%{_bindir}/macropad-settings %{buildroot}%{_libexecdir}/macropad-daemon

install -Dpm 0644 build-aux/macropad.service %{buildroot}%{_userunitdir}/macropad.service
install -Dpm 0644 build-aux/80-macropad.preset %{buildroot}%{_userpresetdir}/80-macropad.preset
install -Dpm 0644 build-aux/70-macropad.rules %{buildroot}%{_udevrulesdir}/70-macropad.rules
install -Dpm 0644 build-aux/%{app_id}.desktop %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 build-aux/%{app_id}.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
install -Dpm 0644 build-aux/%{app_id}.metainfo.xml %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
appstreamcli validate --no-net --explain %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -c "import macropad.config, macropad.keys, macropad.board"

%post
%systemd_user_post macropad.service
# Apply the udev rule now so the pad, uinput and hidraw are usable without a reboot.
udevadm control --reload >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=input --subsystem-match=misc --subsystem-match=hidraw >/dev/null 2>&1 || :
udevadm settle --timeout=5 >/dev/null 2>&1 || :
# First install: start the service for everyone already logged in.
# (A personal package: the preset above enables it for future logins.)
if [ $1 -eq 1 ]; then
    for unit in $(systemctl list-units 'user@*.service' --state=running --no-legend --plain 2>/dev/null | cut -d' ' -f1); do
        uid=${unit#user@}; uid=${uid%%.service}
        user=$(id -nu "$uid" 2>/dev/null) || continue
        systemctl --user -M "$user@" start macropad.service >/dev/null 2>&1 || :
    done
fi

%preun
%systemd_user_preun macropad.service

%postun
%systemd_user_postun_with_restart macropad.service

%files
%license LICENSE
%doc README.md
%{python3_sitelib}/macropad/
%{_bindir}/macropad-settings
%{_libexecdir}/macropad-daemon
%{_userunitdir}/macropad.service
%{_userpresetdir}/80-macropad.preset
%{_udevrulesdir}/70-macropad.rules
%{_datadir}/applications/%{app_id}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_metainfodir}/%{app_id}.metainfo.xml

%changelog
* Tue Oct 06 2026 djshiye <dreamfantom16@gmail.com> - 1.0.0-1
- First packaged release: per-user service, udev rule and app installed under /usr
