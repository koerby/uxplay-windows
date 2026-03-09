# uxplay-windows

Free AirPlay receiver setup for Windows 10/11, based on [UxPlay](https://github.com/FDH2/UxPlay), with a tray-first control experience and installer workflow.

Current version: `1.2.2`

## Fork Attribution

This repository is a fork and continuation of:

- https://github.com/leapbtw/uxplay-windows

Current maintained fork:

- https://github.com/kaktools/uxplay-windows

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
  
<img width="1561" height="1114" alt="UxPlay - Receiver" src="https://github.com/user-attachments/assets/fcfef48b-7d25-440f-8af0-9b8928b5ce23" />

## Changelog (Fork Highlights)

### What is new in this fork

- Windows-11-style tray Control Center popup with live system status.
- Colored tray icon state engine (green running, red error, neutral idle).
- Dependency diagnostics for runtime and Bonjour availability.
- Integrated update check targeting `kaktools/uxplay-windows` releases.
- Improved installer behavior:
  - Prevent duplicate firewall rules.
  - Remove app firewall rules on uninstall.
  - Keep Bonjour installed on uninstall.
- Improved build flow with `build.bat`, `build-portable.ps1`, and `build-setup.ps1`.

## Download

Download the latest release here:

- https://github.com/kaktools/uxplay-windows/releases/latest

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

## Audio (AirPlay Sound)

Short answer: audio issues are often only partially fixable in this fork.

- `uxplay-windows` controls packaging, installer behavior, runtime layout, and process/service handling.
- Actual AirPlay decoding/stream behavior is handled by upstream `UxPlay` plus available media runtime components.

This means:

- If audio fails because of packaging/runtime files, we can fix it here.
- If audio fails due to an upstream UxPlay bug/regression/device compatibility issue, it must be fixed upstream.

### Audio troubleshooting checklist

1. Restart from tray/control center (`Restart` or `Restart Bonjour (Admin)`).
2. Verify `Bonjour Service` is running.
3. Ensure iPhone/iPad and PC are on the same network segment.
4. Try reconnecting AirPlay from iOS (disable/enable and reconnect target).
5. Test with latest release from this fork and, if possible, compare with latest upstream `UxPlay` runtime.
6. If video works but audio does not, report details (iOS version, device model, runtime version) in Issues.

### Reporting audio issues effectively

Include these details in bug reports:

- Windows version
- iOS/iPadOS/macOS version + sender device
- whether video works while audio fails
- whether issue appears after sleep/wake
- logs from `%APPDATA%\uxplay-windows\uxplay-windows.log`

## Control Center

The popup Control Center provides:

- Start / Stop / Restart AirPlay.
- Check for updates.
- Enable/disable autostart.
- One-click health check.
- Live status cards for runtime, Bonjour, engine, and autostart.

<img width="551" height="490" alt="UxPlay - Control Center" src="https://github.com/user-attachments/assets/762cbf82-b2e7-45eb-8306-7fa3957717e3" />


## Tasktray Menü 
<img width="229" height="314" alt="UxPlay - TaskTray Menü" src="https://github.com/user-attachments/assets/e2d254cf-83eb-492b-8a38-cc543e3f4bd5" />


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

If mirroring works but no sound is heard:

1. Run `Restart Bonjour (Admin)` once.
2. Use `Restart` from Control Center.
3. Reconnect AirPlay and test again.

Known edge case:

- Resume from sleep can leave AirPlay/Bonjour in a bad state; restart from the tray/control center.

## Reporting Issues

- Fork-specific packaging, installer, tray, and control center issues: report in this repository.
- Core AirPlay engine issues may come from upstream components (`UxPlay`, GStreamer, Bonjour stack).

## License

See `LICENSE.md` for license details and attribution.
