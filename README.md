# uxplay-windows

Free AirPlay receiver setup for Windows 10/11, based on [UxPlay](https://github.com/FDH2/UxPlay), with a tray-first control experience and installer workflow.

## Fork Attribution

This repository is a fork and continuation of:

- https://github.com/leapbtw/uxplay-windows

Current maintained fork:

- https://github.com/koerby/uxplay-windows

## What This Project Does

`uxplay-windows` packages `UxPlay` plus required runtime files into a practical Windows app with:

- Tray-based controls for start/stop/restart.
- Windows-11-style Control Center popup with quick toggles and live status.
- Health diagnostics (runtime, Bonjour service, process status).
- Colored tray icon states:
  - Green: AirPlay running.
  - Red: missing dependency or service/process issue.
  - Neutral: idle/ready.
- Built-in update check against this fork releases.
- Installer with automatic Bonjour handling when missing.

## Download

Download the latest release here:

- https://github.com/koerby/uxplay-windows/releases/latest

## Installation Notes

1. Install using the provided setup executable.
2. If Bonjour Service is not installed, setup can download/install it.
3. After install, use the tray icon or `Open Control Center` from tray menu.

If Windows SmartScreen warns about unsigned binaries, click `More info` and proceed only if you trust the build source.

## Runtime And Firewall Behavior

- Installer/update ensures firewall rule for `uxplay.exe` without creating duplicates.
- Existing stale firewall rule entries are replaced automatically.
- Uninstall removes app-related firewall rules (`uxplay-windows` and legacy `uxplay`).
- Uninstall terminates running `uxplay-windows.exe` and `uxplay.exe` processes before removal.
- Bonjour Service is not removed during app uninstall.

## Control Center

The popup Control Center provides:

- Start / Stop / Restart AirPlay.
- Check for updates.
- Enable/disable autostart.
- One-click health check.
- Live status cards for runtime, Bonjour, engine, and autostart.

## Build

Quick local options:

- Portable build: `build-portable.ps1`
- Installer build: `build-setup.ps1`
- Interactive helper: `build.bat`

Detailed notes:

- See `BUILDING.md`

## Troubleshooting

If your Apple device cannot connect:

1. Open tray menu and use `Restart AirPlay + Bonjour`.
2. Ensure Bonjour Service is running.
3. Confirm firewall is enabled for `uxplay.exe`.
4. Toggle Wi-Fi off/on on the Apple device.

Known edge case:

- Resume from sleep can leave AirPlay/Bonjour in a bad state; restart from the tray/control center.

## Reporting Issues

- Fork-specific packaging, installer, tray, and control center issues: report in this repository.
- Core AirPlay engine issues may come from upstream components (`UxPlay`, GStreamer, Bonjour stack).

## License

See `LICENSE.md` for license details and attribution.
