# License And Attribution

`uxplay-windows` is distributed under the [GNU General Public License v3.0 (GPL-3.0)](https://www.gnu.org/licenses/gpl-3.0.html).

## Fork Attribution

This repository is a fork and continuation of:

- Original project: https://github.com/leapbtw/uxplay-windows

The current maintained fork is:

- Current repository: https://github.com/koerby/uxplay-windows

## Third-Party Components

`uxplay-windows` bundles and/or depends on the following projects, each under its own license terms.

### 1. UxPlay

- License: [GNU General Public License v3.0 (GPL-3.0)](https://github.com/FDH2/UxPlay/blob/master/LICENSE)
- Source: https://github.com/FDH2/UxPlay

### 2. mDNSResponder

- Purpose: DNS-SD / Bonjour-related functionality
- Licenses: [BSD-3-Clause](https://opensource.org/licenses/BSD-3-Clause) and [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)
- Sources:
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/LICENSE
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/mDNSShared/dns_sd.h
  - https://github.com/apple-oss-distributions/mDNSResponder/blob/rel/mDNSResponder-214/mDNSCore/mDNS.c

### 3. GStreamer

- Purpose: media pipeline/runtime components
- License: [GNU Lesser General Public License (LGPL)](https://www.gnu.org/licenses/lgpl-3.0.html)
- Sources:
  - https://gstreamer.freedesktop.org/
  - https://github.com/GStreamer/gst-plugins-base
  - https://github.com/GStreamer/gst-plugins-bad
  - https://github.com/GStreamer/gst-plugins-good
  - https://github.com/GStreamer/gst-libav

### 4. Inno Setup

- Purpose: Windows installer creation
- License: [Inno Setup License](https://jrsoftware.org/isinfo.php#license)
- Source: https://jrsoftware.org/isinfo.php

### 5. Icon Source

- Based on assets from Material Design Icons / Pictogrammers
- License: [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)
- Source: https://pictogrammers.com/docs/general/license/

## Build Tools (Not Redistributed)

This project uses tools such as `PyInstaller` and `MSYS2` during build, but does not redistribute those toolchains directly.

- `PyInstaller`: GPL-2.0 with exception (see upstream license terms)
- `MSYS2`: meta-distribution with package-specific licenses

## Notice

This document is an attribution and licensing overview for convenience. Always verify compliance requirements directly from each upstream license text when redistributing binaries or modified sources.
