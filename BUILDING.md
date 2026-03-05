## Building from Source
This project also provides GitHub Actions to let you compile the software yourself easily. Follow these steps:

1. [Fork the repo.](https://github.com/leapbtw/uxplay-windows/fork)
2. In the `Actions` tab of your fork, select `build uxplay-windows` and run it

The resulting uxplay-windows installer will be provided as an artifact from the GitHub Action.

## Local Portable Build (no installer)
You can build a combined local app bundle (tray app + UxPlay runtime) with:

```powershell
./build-portable.ps1 -Bootstrap
```

What this does:
- Installs required tools (`python`, `pyinstaller`, `cmake`, `ninja`, `MSYS2`) when `-Bootstrap` is used.
- Builds `UxPlay` from the local `UxPlay/` source directory.
- Prunes the runtime files using `dll-libs-list.txt` to keep the bundle compact.
- Builds `tray.py` into `dist/uxplay-windows/uxplay-windows.exe`.
- Copies UxPlay runtime into `dist/uxplay-windows/_internal`.

Result:
- Portable app folder at `dist/uxplay-windows`
- Start with `dist/uxplay-windows/uxplay-windows.exe`

Notes:
- First run can take a while because MSYS2 packages are installed.
- Firewall rule creation at app startup requires running the app with admin rights at least once.
