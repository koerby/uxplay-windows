# Release Notes - uxplay-windows v1.2.1

Release date: 2026-03-06

## Overview
`uxplay-windows` provides a tray-first AirPlay receiver setup for Windows based on UxPlay, including Control Center UI, diagnostics, snapshot support, and installer workflow.

## New in v1.2.1

- Improved snapshot capture reliability for shared iPad screen sessions.
- Snapshot capture now targets the UxPlay runtime window content directly (window-only mode), instead of a visible desktop crop.
- Snapshot overlays from other windows (for example Control Center on top) are avoided by direct window capture.
- Snapshot output path is standardized to `C:\Users\<User>\Pictures\UxPlay`.
- Saved snapshots are opened as `.png` via the Windows default image viewer association.
- Control/Status UX polishing from current branch state:
  - unified full-width status bar usage for runtime states,
  - improved tray state synchronization for checked menu items,
  - improved Control Center reopen lifecycle stability.

## Core Capabilities

- AirPlay receiver lifecycle from tray and Control Center:
  - Start
  - Stop
  - Restart
  - Pause/Resume UxPlay renderer
- Snapshot workflow:
  - Save screenshot of active UxPlay runtime window
  - Open saved PNG immediately via default viewer
- Health/diagnostics overview in Control Center:
  - Runtime status
  - Bonjour service status
  - Engine/process status
  - Autostart status
- Tray icon state feedback:
  - running/healthy
  - idle/ready
  - dependency or service issues
- Update check against fork releases (`koerby/uxplay-windows`).
- Autostart toggle.
- Global hotkey for snapshot (`Ctrl+9`, when available).
- Installer support including Bonjour handling.

## System Requirements (Runtime)

- Windows 10 or Windows 11
- Network conditions for AirPlay usage:
  - sender device (iPhone/iPad/macOS) and PC in same reachable network segment
- Bonjour availability (installer can handle missing Bonjour)
- Typical end-user install method:
  - `uxplay-win_setup_v1.2.1.exe`

## Requirements for Building from Source

- Python 3.x
- PyInstaller
- Inno Setup (for setup executable generation)
- For full local runtime build workflows:
  - MSYS2
  - CMake
  - Ninja

Reference: `BUILDING.md`

## External Components, Licenses, and Sources

The following third-party components are used/bundled or required by project workflows.

### UxPlay
- Purpose: Core AirPlay receiver engine
- License: GPL-3.0
- Source: https://github.com/FDH2/UxPlay
- License text: https://github.com/FDH2/UxPlay/blob/master/LICENSE

### mDNSResponder / Bonjour
- Purpose: DNS-SD / Bonjour service functionality
- Licenses: BSD-3-Clause and Apache-2.0 (component-specific)
- Sources:
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/LICENSE
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/mDNSShared/dns_sd.h
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/mDNSCore/mDNS.c
- Installer download endpoint used by app logic:
  - https://download.info.apple.com/Mac_OS_X/061-8098.20100603.gthyu/BonjourPSSetup.exe

### GStreamer (runtime media stack used by UxPlay packaging)
- Purpose: Media pipeline/runtime plugins and codecs
- License: LGPL (see upstream package terms)
- Sources:
  - https://gstreamer.freedesktop.org/
  - https://github.com/GStreamer/gst-plugins-base
  - https://github.com/GStreamer/gst-plugins-bad
  - https://github.com/GStreamer/gst-plugins-good
  - https://github.com/GStreamer/gst-libav
- Runtime file selection list in this repo:
  - `dll-libs-list.txt`

### Inno Setup
- Purpose: Windows installer creation
- License: Inno Setup License
- Source: https://jrsoftware.org/isinfo.php
- License info: https://jrsoftware.org/isinfo.php#license

### Python Packaging/UI Libraries
- PyInstaller
  - Purpose: Build `tray.py` into Windows executable
  - License: GPL-2.0 with exception
  - Source: https://github.com/pyinstaller/pyinstaller
- pystray
  - Purpose: Windows tray icon/menu integration
  - License: BSD-3-Clause
  - Source: https://github.com/moses-palmer/pystray
- Pillow
  - Purpose: Imaging/screenshot processing support
  - License: HPND-like (Pillow license)
  - Source: https://github.com/python-pillow/Pillow

### Icon Assets
- Source project: Material Design Icons / Pictogrammers
- License: Apache-2.0
- Source: https://pictogrammers.com/docs/general/license/

## Attribution and Compliance Notes

- Project/fork attribution:
  - Original fork base: https://github.com/leapbtw/uxplay-windows
  - Maintained fork: https://github.com/koerby/uxplay-windows
- Main project license and attribution summary:
  - `LICENSE.md`
- This release note summarizes known dependencies and sources from repository metadata; for redistribution/compliance, verify each upstream license text directly.
