import sys
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
import winreg
import webbrowser
import ctypes
import tkinter as tk

from pathlib import Path
from typing import List, Optional

import pystray
from PIL import Image, ImageDraw, ImageOps

# ─── Constants ────────────────────────────────────────────────────────────────

APP_NAME = "uxplay-windows"
APPDATA_DIR = Path(os.environ["APPDATA"]) / "uxplay-windows"
LOG_FILE = APPDATA_DIR / f"{APP_NAME}.log"
DEFAULT_APP_VERSION = "0.0.0"
BONJOUR_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Bonjour Service"
BONJOUR_SERVICE_NAME = "Bonjour Service"
BONJOUR_DOWNLOAD_URL = (
    "https://download.info.apple.com/Mac_OS_X/061-8098.20100603.gthyu/"
    "BonjourPSSetup.exe"
)
UXPLAY_WINDOWS_RELEASES_URL = "https://github.com/koerby/uxplay-windows/releases"
UXPLAY_UPSTREAM_RELEASES_URL = "https://github.com/FDH2/UxPlay/releases"
UPDATE_REPO_API_URL = "https://api.github.com/repos/koerby/uxplay-windows/releases/latest"

# ensure the AppData folder exists up front:
APPDATA_DIR.mkdir(parents=True, exist_ok=True)

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ─── Path Discovery ───────────────────────────────────────────────────────────

class Paths:
    """
    Find where our bundled resources live:
      • if PyInstaller one-file: sys._MEIPASS
      • else if one-dir: same folder as the exe
      • else (running from .py): the script's folder
    Then, if there is an `_internal` subfolder, use that.
    """
    def __init__(self):
        if getattr(sys, "frozen", False):
            # one-file mode unpacks to _MEIPASS
            if hasattr(sys, "_MEIPASS"):
                cand = Path(sys._MEIPASS)
            else:
                # one-dir mode: resources sit beside the exe
                cand = Path(sys.executable).parent
        else:
            cand = Path(__file__).resolve().parent

        # if there's an _internal subfolder, that's where our .ico + bin live
        internal = cand / "_internal"
        self.resource_dir = internal if internal.is_dir() else cand

        # Try common icon locations used by source runs and PyInstaller bundles.
        icon_candidates = [
            self.resource_dir / "uxplay.ico",
            cand / "uxplay.ico",
            Path(__file__).resolve().parent / "uxplay.ico",
        ]
        self.icon_file = next((p for p in icon_candidates if p.exists()), icon_candidates[0])

        # first look for bin/uxplay.exe, else uxplay.exe at top level
        ux1 = self.resource_dir / "bin" / "uxplay.exe"
        ux2 = self.resource_dir / "uxplay.exe"
        self.uxplay_exe = ux1 if ux1.exists() else ux2

        # AppData paths
        self.appdata_dir = APPDATA_DIR
        self.arguments_file = self.appdata_dir / "arguments.txt"

        version_candidates = [
            self.resource_dir / "version.txt",
            cand / "version.txt",
            Path(__file__).resolve().parent / "version.txt",
        ]
        self.version_file = next((p for p in version_candidates if p.exists()), version_candidates[0])

# ─── Argument File Manager ────────────────────────────────────────────────────

class ArgumentManager:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def ensure_exists(self) -> None:
        logging.info("Ensuring arguments file at '%s'", self.file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("", encoding="utf-8")
            logging.info("Created empty arguments.txt")

    def read_args(self) -> List[str]:
        if not self.file_path.exists():
            logging.warning("arguments.txt missing → no custom args")
            return []
        text = self.file_path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        try:
            return shlex.split(text)
        except ValueError as e:
            logging.error("Could not parse arguments.txt: %s", e)
            return []

# ─── Server Process Manager ──────────────────────────────────────────────────

class ServerManager:
    def __init__(self, exe_path: Path, arg_mgr: ArgumentManager):
        self.exe_path = exe_path
        self.arg_mgr = arg_mgr
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            logging.info("UxPlay server already running (PID %s)", self.process.pid)
            return

        if not self.exe_path.exists():
            logging.error("uxplay.exe not found at %s", self.exe_path)
            return

        cmd = [str(self.exe_path)] + self.arg_mgr.read_args()
        logging.info("Starting UxPlay: %s", cmd)
        try:
            self.process = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            logging.info("Started UxPlay (PID %s)", self.process.pid)
        except Exception:
            logging.exception("Failed to launch UxPlay")

    def stop(self) -> None:
        if not (self.process and self.process.poll() is None):
            logging.info("UxPlay server not running.")
            return

        pid = self.process.pid
        logging.info("Stopping UxPlay (PID %s)...", pid)
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
            logging.info("UxPlay stopped cleanly.")
        except subprocess.TimeoutExpired:
            logging.warning("Did not terminate in time; killing it.")
            self.process.kill()
            self.process.wait()
        except Exception:
            logging.exception("Error stopping UxPlay")
        finally:
            self.process = None

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

# ─── Auto-Start Manager ───────────────────────────────────────────────────────

class AutoStartManager:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, app_name: str, exe_cmd: str):
        self.app_name = app_name
        self.exe_cmd = exe_cmd

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.RUN_KEY,
                0,
                winreg.KEY_READ
            ) as key:
                val, _ = winreg.QueryValueEx(key, self.app_name)
                return self.exe_cmd in val
        except FileNotFoundError:
            return False
        except Exception:
            logging.exception("Error checking Autostart")
            return False

    def enable(self) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.RUN_KEY,
                0,
                winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(
                    key,
                    self.app_name,
                    0,
                    winreg.REG_SZ,
                    self.exe_cmd
                )
            logging.info("Autostart enabled")
        except Exception:
            logging.exception("Failed to enable Autostart")

    def disable(self) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.RUN_KEY,
                0,
                winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, self.app_name)
            logging.info("Autostart disabled")
        except FileNotFoundError:
            logging.info("No Autostart entry to delete")
        except Exception:
            logging.exception("Failed to disable Autostart")

    def toggle(self) -> None:
        if self.is_enabled():
            self.disable()
        else:
            self.enable()


class DependencyManager:
    @staticmethod
    def is_bonjour_installed() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BONJOUR_SERVICE_KEY):
                return True
        except FileNotFoundError:
            return False
        except Exception:
            logging.exception("Failed checking Bonjour registry key")
            return False

    @staticmethod
    def get_missing_dependencies(paths: Paths) -> List[str]:
        missing = []
        if not paths.uxplay_exe.exists():
            missing.append("uxplay-runtime")
        if not DependencyManager.is_bonjour_installed():
            missing.append("bonjour")
        return missing

    @staticmethod
    def notify_if_missing(paths: Paths) -> bool:
        missing = DependencyManager.get_missing_dependencies(paths)
        if not missing:
            return True

        lines = [
            "Required components are missing:",
            "",
        ]
        if "uxplay-runtime" in missing:
            lines.append("- UxPlay runtime (uxplay.exe + DLLs)")
        if "bonjour" in missing:
            lines.append("- Bonjour Service")

        if "uxplay-runtime" in missing:
            lines += [
                "",
                "AirPlay cannot work without UxPlay runtime.",
                "",
                "Click Yes to open download/help pages now.",
                "Click No to exit the app.",
            ]
        else:
            lines += [
                "",
                "AirPlay may not work until dependencies are installed.",
                "",
                "Click Yes to open required download pages now.",
            ]
        message = "\n".join(lines)

        logging.warning("Missing dependencies detected: %s", ", ".join(missing))
        try:
            result = ctypes.windll.user32.MessageBoxW(
                0,
                message,
                f"{APP_NAME} - Missing Dependencies",
                0x00000004 | 0x00000010 | 0x00010000 | 0x00040000,
                # MB_YESNO | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
            )
            if result == 6:  # IDYES
                if "uxplay-runtime" in missing:
                    webbrowser.open(UXPLAY_WINDOWS_RELEASES_URL)
                    webbrowser.open(UXPLAY_UPSTREAM_RELEASES_URL)
                if "bonjour" in missing:
                    webbrowser.open(BONJOUR_DOWNLOAD_URL)
                if "uxplay-runtime" in missing:
                    return False
                return True
            if "uxplay-runtime" in missing:
                return False
        except Exception:
            logging.exception("Failed to display dependency warning dialog")
            if "uxplay-runtime" in missing:
                return False

        return True


class BonjourServiceManager:
    @staticmethod
    def restart() -> bool:
        if not DependencyManager.is_bonjour_installed():
            logging.warning("Bonjour service is not installed; skipping restart")
            return False

        # First choice: PowerShell Restart-Service for a clean stop/start.
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"Restart-Service -Name '{BONJOUR_SERVICE_NAME}' -Force",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=25,
            )
            if result.returncode == 0:
                logging.info("Bonjour service restarted successfully")
                return True
            logging.warning(
                "Restart-Service failed (code %s): %s",
                result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
        except Exception:
            logging.exception("Restart-Service execution failed")

        # Fallback: SCM stop/start sequence.
        try:
            stop_result = subprocess.run(
                ["sc", "stop", BONJOUR_SERVICE_NAME],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=25,
            )
            time.sleep(1)
            start_result = subprocess.run(
                ["sc", "start", BONJOUR_SERVICE_NAME],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=25,
            )
            if start_result.returncode == 0:
                logging.info("Bonjour service restarted successfully via sc")
                return True
            logging.warning(
                "SC restart failed. stop=%s start=%s",
                stop_result.returncode,
                start_result.returncode,
            )
        except Exception:
            logging.exception("SC restart fallback failed")

        # Final attempt: request elevation so Bonjour restart can succeed.
        try:
            elevated_cmd = (
                "Start-Process powershell -Verb RunAs -Wait "
                "-ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command "
                "\"Restart-Service -Name ''Bonjour Service'' -Force\"'"
            )
            elev = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    elevated_cmd,
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if elev.returncode == 0:
                logging.info("Bonjour service restarted successfully via elevation")
                return True
            logging.warning("Elevated Bonjour restart failed (code %s)", elev.returncode)
        except Exception:
            logging.exception("Elevated Bonjour restart failed")

        return False

    @staticmethod
    def is_running() -> bool:
        if not DependencyManager.is_bonjour_installed():
            return False

        try:
            result = subprocess.run(
                ["sc", "query", BONJOUR_SERVICE_NAME],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            output = (result.stdout or "").upper()
            return "STATE" in output and "RUNNING" in output
        except Exception:
            logging.exception("Could not query Bonjour service state")
            return False


class VersionManager:
    @staticmethod
    def read_current_version(paths: Paths) -> str:
        try:
            if paths.version_file.exists():
                value = paths.version_file.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except Exception:
            logging.exception("Failed reading version file: %s", paths.version_file)
        return DEFAULT_APP_VERSION


class UpdateChecker:
    def __init__(self, current_version: str):
        self.current_version = current_version
        self.notifier = None

    def set_notifier(self, notifier) -> None:
        self.notifier = notifier

    def _notify(self, title: str, message: str) -> None:
        if self.notifier:
            try:
                self.notifier(title, message)
                return
            except Exception:
                logging.exception("Update notifier failed")
        logging.info("%s: %s", title, message)

    @staticmethod
    def _normalize_version(value: str) -> List[int]:
        digits = re.findall(r"\d+", value or "")
        if not digits:
            return [0]
        return [int(x) for x in digits]

    @classmethod
    def _is_newer(cls, latest: str, current: str) -> bool:
        latest_parts = cls._normalize_version(latest)
        current_parts = cls._normalize_version(current)
        max_len = max(len(latest_parts), len(current_parts))
        latest_parts += [0] * (max_len - len(latest_parts))
        current_parts += [0] * (max_len - len(current_parts))
        return latest_parts > current_parts

    @staticmethod
    def _fetch_latest_release() -> Optional[dict]:
        req = urllib.request.Request(
            UPDATE_REPO_API_URL,
            headers={"User-Agent": f"{APP_NAME}-update-check"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload

    def check_for_updates(self, interactive: bool = True) -> None:
        try:
            payload = self._fetch_latest_release()
            if not payload:
                return
            latest = payload.get("tag_name", "")
            url = payload.get("html_url", UXPLAY_WINDOWS_RELEASES_URL)

            if self._is_newer(latest, self.current_version):
                self._notify(
                    "Update available",
                    f"Current: {self.current_version} | Latest: {latest}. Opening releases page...",
                )
                webbrowser.open(url)
            elif interactive:
                self._notify("No updates", f"You are up to date ({self.current_version}).")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._notify("Update check unavailable", "Release feed not found (404).")
                return
            logging.exception("Update check HTTP error")
            if interactive:
                self._notify("Update check failed", f"HTTP error: {e.code}")
        except Exception:
            logging.exception("Update check failed")
            if interactive:
                self._notify("Update check failed", "Could not check for updates right now.")


class ControlCenterWindow:
    def __init__(self, tray: "TrayIcon"):
        self.tray = tray
        self._thread: Optional[threading.Thread] = None
        self._root: Optional[tk.Tk] = None
        self._status_chip: Optional[tk.Label] = None
        self._status_title: Optional[tk.Label] = None
        self._status_details: Optional[tk.Label] = None
        self._runtime_value: Optional[tk.Label] = None
        self._bonjour_value: Optional[tk.Label] = None
        self._server_value: Optional[tk.Label] = None
        self._autostart_value: Optional[tk.Label] = None
        self._autostart_button: Optional[tk.Button] = None
        self._lock = threading.Lock()

    def show(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and self._root:
                self._root.after(0, self._focus_existing)
                return

            self._thread = threading.Thread(target=self._run_window, daemon=True)
            self._thread.start()

    def _focus_existing(self) -> None:
        if not self._root:
            return
        self._root.deiconify()
        self._root.lift()
        self._root.attributes("-topmost", True)
        self._root.after(250, lambda: self._root and self._root.attributes("-topmost", False))

    def _run_window(self) -> None:
        try:
            root = tk.Tk()
            self._root = root
            root.title("uxplay Control Center")
            root.geometry("440x400")
            root.resizable(False, False)
            root.overrideredirect(True)
            root.configure(bg="#DCE4EE")
            root.attributes("-alpha", 0.0)

            outer = tk.Frame(root, bg="#DCE4EE", bd=0)
            outer.pack(fill="both", expand=True, padx=1, pady=1)

            shell = tk.Frame(outer, bg="#F7F9FC", bd=0, highlightthickness=1, highlightbackground="#CDD7E3")
            shell.pack(fill="both", expand=True, padx=2, pady=2)

            title_bar = tk.Frame(shell, bg="#EEF3FA", height=38)
            title_bar.pack(fill="x")
            title_bar.pack_propagate(False)

            title_label = tk.Label(
                title_bar,
                text="uxplay Control Center",
                font=("Segoe UI Semibold", 10),
                bg="#EEF3FA",
                fg="#1D2733",
            )
            title_label.pack(side="left", padx=12)

            close_btn = tk.Label(
                title_bar,
                text="x",
                font=("Segoe UI Semibold", 10),
                bg="#EEF3FA",
                fg="#3B4D62",
                padx=12,
                pady=6,
                cursor="hand2",
            )
            close_btn.pack(side="right", padx=2)

            content = tk.Frame(shell, bg="#F7F9FC")
            content.pack(fill="both", expand=True, padx=12, pady=12)

            self._make_draggable(root, title_bar, title_label)
            close_btn.bind("<Enter>", lambda _: close_btn.config(bg="#F6DADF", fg="#8D1B2A"))
            close_btn.bind("<Leave>", lambda _: close_btn.config(bg="#EEF3FA", fg="#3B4D62"))
            close_btn.bind("<Button-1>", lambda _: self._close_animated())

            self._status_chip = tk.Label(content, text="", font=("Segoe UI Semibold", 9), padx=10, pady=4)
            self._status_chip.pack(anchor="w")

            self._status_title = tk.Label(content, text="", font=("Segoe UI Semibold", 14), bg="#F7F9FC", fg="#223041")
            self._status_title.pack(anchor="w", pady=(10, 0))

            self._status_details = tk.Label(content, text="", font=("Segoe UI", 9), bg="#F7F9FC", fg="#4D5E73", wraplength=396, justify="left")
            self._status_details.pack(anchor="w", pady=(2, 10))

            cards = tk.Frame(content, bg="#F7F9FC")
            cards.pack(fill="x", pady=(0, 10))

            card_specs = [
                ("Start AirPlay", "Launch receiver engine", self.tray.start_server, "#DFF4E6", "#1F7A46"),
                ("Stop AirPlay", "Stop receiver engine", self.tray.stop_server, "#E9EEF5", "#405266"),
                ("Restart Stack", "Restart AirPlay + Bonjour", self.tray._restart, "#E5EFFD", "#1E5FB8"),
                ("Check Updates", "Query latest release", lambda: self.tray.update_checker.check_for_updates(interactive=True), "#F4E9FD", "#6C2DA0"),
            ]
            for idx, (title, subtitle, fn, bg, fg) in enumerate(card_specs):
                card = self._create_action_card(cards, title, subtitle, fn, bg, fg)
                card.grid(row=idx // 2, column=idx % 2, padx=5, pady=5, sticky="nsew")
            cards.grid_columnconfigure(0, weight=1)
            cards.grid_columnconfigure(1, weight=1)

            details = tk.Frame(content, bg="#FFFFFF", highlightthickness=1, highlightbackground="#DFE7F0")
            details.pack(fill="x", pady=(0, 10))

            self._runtime_value = self._status_row(details, "UxPlay Runtime", 0)
            self._bonjour_value = self._status_row(details, "Bonjour Service", 1)
            self._server_value = self._status_row(details, "AirPlay Engine", 2)
            self._autostart_value = self._status_row(details, "Autostart", 3)

            footer = tk.Frame(content, bg="#F7F9FC")
            footer.pack(fill="x")

            self._autostart_button = tk.Button(
                footer,
                text="Toggle Autostart",
                command=lambda: self._run_async(self._toggle_autostart),
                relief="flat",
                bd=0,
                bg="#EAF0F7",
                activebackground="#DCE7F2",
                fg="#223041",
                font=("Segoe UI Semibold", 9),
                padx=12,
                pady=7,
                cursor="hand2",
            )
            self._autostart_button.pack(side="left")

            health_btn = tk.Button(
                footer,
                text="Health Check",
                command=lambda: self._run_async(self.tray._health_check_popup),
                relief="flat",
                bd=0,
                bg="#EAF0F7",
                activebackground="#DCE7F2",
                fg="#223041",
                font=("Segoe UI Semibold", 9),
                padx=12,
                pady=7,
                cursor="hand2",
            )
            health_btn.pack(side="left", padx=(8, 0))

            close_main = tk.Button(
                footer,
                text="Close",
                command=self._close_animated,
                relief="flat",
                bd=0,
                bg="#1F6FE5",
                activebackground="#165DC0",
                fg="#FFFFFF",
                font=("Segoe UI Semibold", 9),
                padx=12,
                pady=7,
                cursor="hand2",
            )
            close_main.pack(side="right")

            root.protocol("WM_DELETE_WINDOW", self._close_animated)
            self._refresh_loop()
            self._fade_in()
            root.mainloop()
        except Exception:
            logging.exception("Failed opening Control Center window")
            self.tray.notify_user("Control Center failed", "Could not open the popup window.")
        finally:
            with self._lock:
                self._root = None
                self._thread = None

    @staticmethod
    def _status_row(parent: tk.Widget, label: str, row: int) -> tk.Label:
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg="#FFFFFF", fg="#5C6E82").grid(row=row, column=0, sticky="w", padx=10, pady=6)
        value = tk.Label(parent, text="-", font=("Segoe UI Semibold", 9), bg="#FFFFFF", fg="#1F2C3A")
        value.grid(row=row, column=1, sticky="e", padx=10, pady=6)
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=0)
        return value

    def _create_action_card(
        self,
        parent: tk.Widget,
        title: str,
        subtitle: str,
        action,
        bg_color: str,
        accent_fg: str,
    ) -> tk.Frame:
        card = tk.Frame(parent, bg=bg_color, highlightthickness=1, highlightbackground="#D8E2EE", cursor="hand2")

        title_lbl = tk.Label(card, text=title, font=("Segoe UI Semibold", 10), bg=bg_color, fg=accent_fg)
        title_lbl.pack(anchor="w", padx=10, pady=(8, 2))

        sub_lbl = tk.Label(card, text=subtitle, font=("Segoe UI", 8), bg=bg_color, fg="#4C6075")
        sub_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        def on_enter(_):
            card.configure(highlightbackground=accent_fg)

        def on_leave(_):
            card.configure(highlightbackground="#D8E2EE")

        def on_click(_):
            self._run_async(action)

        for widget in (card, title_lbl, sub_lbl):
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)
            widget.bind("<Button-1>", on_click)

        return card

    def _make_draggable(self, root: tk.Tk, *widgets: tk.Widget) -> None:
        drag_state = {"x": 0, "y": 0}

        def on_press(event):
            drag_state["x"] = event.x_root - root.winfo_x()
            drag_state["y"] = event.y_root - root.winfo_y()

        def on_drag(event):
            nx = event.x_root - drag_state["x"]
            ny = event.y_root - drag_state["y"]
            root.geometry(f"+{nx}+{ny}")

        for widget in widgets:
            widget.bind("<ButtonPress-1>", on_press)
            widget.bind("<B1-Motion>", on_drag)

    def _fade_in(self) -> None:
        if not self._root:
            return
        try:
            alpha = float(self._root.attributes("-alpha"))
        except Exception:
            alpha = 0.0
        alpha = min(1.0, alpha + 0.12)
        self._root.attributes("-alpha", alpha)
        if alpha < 1.0:
            self._root.after(20, self._fade_in)

    def _close_animated(self) -> None:
        if not self._root:
            return
        try:
            alpha = float(self._root.attributes("-alpha"))
        except Exception:
            alpha = 1.0
        alpha = max(0.0, alpha - 0.16)
        self._root.attributes("-alpha", alpha)
        if alpha <= 0.0:
            self._root.destroy()
        else:
            self._root.after(16, self._close_animated)

    def _run_async(self, action) -> None:
        threading.Thread(target=action, daemon=True).start()

    def _toggle_autostart(self) -> None:
        self.tray.auto_mgr.toggle()
        enabled = self.tray.auto_mgr.is_enabled()
        self.tray.notify_user("Autostart", "Enabled" if enabled else "Disabled")

    def _refresh_loop(self) -> None:
        if not self._root:
            return

        snapshot = self.tray.get_health_snapshot()
        state = snapshot["state"]
        details = snapshot["details"]

        if self._status_chip:
            if state == "running":
                self._status_chip.config(text="RUNNING", bg="#DFF4E6", fg="#1F7A46")
            elif state == "error":
                self._status_chip.config(text="ERROR", bg="#FCE3E6", fg="#A11E2F")
            else:
                self._status_chip.config(text="IDLE", bg="#E7EDF5", fg="#41566D")

        if self._status_title:
            self._status_title.config(text="Live System Status")
        if self._status_details:
            self._status_details.config(text=details)

        if self._runtime_value:
            self._runtime_value.config(text="OK" if snapshot["runtime_ok"] else "Missing")
        if self._bonjour_value:
            self._bonjour_value.config(text=snapshot["bonjour_state"])
        if self._server_value:
            self._server_value.config(text="Running" if snapshot["server_running"] else "Stopped")
        if self._autostart_value:
            self._autostart_value.config(text="Enabled" if snapshot["autostart"] else "Disabled")
        if self._autostart_button:
            self._autostart_button.config(text="Disable Autostart" if snapshot["autostart"] else "Enable Autostart")

        self._root.after(1200, self._refresh_loop)

# ─── System Tray Icon UI ─────────────────────────────────────────────────────

class TrayIcon:
    def __init__(
        self,
        icon_path: Path,
        paths: Paths,
        server_mgr: ServerManager,
        bonjour_mgr: BonjourServiceManager,
        arg_mgr: ArgumentManager,
        auto_mgr: AutoStartManager,
        update_checker: UpdateChecker,
    ):
        self.paths = paths
        self.server_mgr = server_mgr
        self.bonjour_mgr = bonjour_mgr
        self.arg_mgr = arg_mgr
        self.auto_mgr = auto_mgr
        self.update_checker = update_checker
        self.stop_event = threading.Event()
        self.desired_running = False
        self._status_text = "Ready"
        self.control_center = ControlCenterWindow(self)

        self.normal_icon = self._load_icon(icon_path).convert("RGBA")
        self.running_icon = self._colorize_icon(self.normal_icon, (44, 173, 105))
        self.error_icon = self._colorize_icon(self.normal_icon, (220, 53, 69))
        self.idle_icon = self._colorize_icon(self.normal_icon, (80, 99, 118))
        self._last_state = ""

        status_item = pystray.MenuItem(
            lambda _: f"Status: {self._status_text}",
            None,
            enabled=False,
        )

        menu = pystray.Menu(
            status_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Control Center", lambda _: self.open_control_center()),
            pystray.MenuItem("Start AirPlay", lambda _: self.start_server()),
            pystray.MenuItem("Stop AirPlay",  lambda _: self.stop_server()),
            pystray.MenuItem("Restart AirPlay + Bonjour", lambda _: self._restart()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start With Windows",
                lambda _: auto_mgr.toggle(),
                checked=lambda _: auto_mgr.is_enabled()
            ),
            pystray.MenuItem(
                "Check For Updates",
                lambda _: self.update_checker.check_for_updates(interactive=True)
            ),
            pystray.MenuItem("Run Health Check", lambda _: self._health_check_popup()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "License",
                lambda _: webbrowser.open(
                    "https://github.com/koerby/uxplay-windows/blob/"
                    "main/LICENSE.md"
                )
            ),
            pystray.MenuItem("Exit", lambda _: self._exit()),
        )

        self.icon = pystray.Icon(
            name=f"{APP_NAME}\nRight-click to configure.",
            icon=self.normal_icon,
            title=APP_NAME,
            menu=menu
        )
        self.update_checker.set_notifier(self.notify_user)

    def notify_user(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            logging.info("%s: %s", title, message)

    @staticmethod
    def _load_icon(icon_path: Path) -> Image.Image:
        """Load tray icon from disk, or use a tiny fallback image if missing."""
        try:
            return Image.open(icon_path)
        except FileNotFoundError:
            logging.warning("Icon file not found at %s, using fallback icon", icon_path)
            return Image.new("RGBA", (16, 16), (60, 130, 200, 255))
        except Exception:
            logging.exception("Failed to load icon from %s, using fallback icon", icon_path)
            return Image.new("RGBA", (16, 16), (60, 130, 200, 255))

    @staticmethod
    def _colorize_icon(base_icon: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
        base = base_icon.copy().resize((16, 16), Image.LANCZOS).convert("RGBA")
        alpha = base.split()[3]
        gray = ImageOps.grayscale(base)
        colored = ImageOps.colorize(gray, black=(20, 20, 20), white=rgb)
        colored.putalpha(alpha)
        # Add a subtle ring for clearer visibility on light/dark taskbars.
        draw = ImageDraw.Draw(colored)
        draw.ellipse((0, 0, 15, 15), outline=(255, 255, 255, 110), width=1)
        return colored

    def _compute_health(self) -> tuple[str, str]:
        errors: List[str] = []
        missing = DependencyManager.get_missing_dependencies(self.paths)

        if "uxplay-runtime" in missing:
            errors.append("UxPlay runtime missing")
        if "bonjour" in missing:
            errors.append("Bonjour service missing")
        elif not BonjourServiceManager.is_running():
            errors.append("Bonjour service stopped or hanging")

        if self.desired_running and not self.server_mgr.is_running():
            errors.append("UxPlay process not running")

        if errors:
            return ("error", " | ".join(errors))
        if self.server_mgr.is_running():
            return ("running", "AirPlay active")
        return ("idle", "Ready")

    def get_health_snapshot(self) -> dict:
        missing = DependencyManager.get_missing_dependencies(self.paths)
        bonjour_state = "Missing"
        if "bonjour" not in missing:
            bonjour_state = "Running" if BonjourServiceManager.is_running() else "Stopped"

        state, details = self._compute_health()
        return {
            "state": state,
            "details": details,
            "runtime_ok": "uxplay-runtime" not in missing,
            "bonjour_state": bonjour_state,
            "server_running": self.server_mgr.is_running(),
            "autostart": self.auto_mgr.is_enabled(),
        }

    def _refresh_visual_state(self) -> None:
        state, details = self._compute_health()
        self._status_text = details

        if state == self._last_state and self.icon.title == f"{APP_NAME} Control Center - {details}":
            return

        if state == "running":
            self.icon.icon = self.running_icon
        elif state == "error":
            self.icon.icon = self.error_icon
        else:
            self.icon.icon = self.idle_icon

        self.icon.title = f"{APP_NAME} Control Center - {details}"
        self._last_state = state

    def _monitor_server_status(self) -> None:
        while not self.stop_event.wait(2):
            self._refresh_visual_state()

    def _health_check_popup(self) -> None:
        state, details = self._compute_health()
        title = "System Healthy" if state != "error" else "Action Required"
        self.notify_user(title, details)

    def open_control_center(self) -> None:
        self.control_center.show()

    def _restart(self):
        logging.info("Restarting UxPlay and Bonjour service")
        self.desired_running = True
        self.server_mgr.stop()
        if not self.bonjour_mgr.restart():
            self.notify_user(
                "Bonjour restart failed",
                "Could not restart Bonjour Service automatically. Check UAC/admin rights.",
            )
        self.server_mgr.start()
        self._refresh_visual_state()

    def start_server(self):
        self.desired_running = True
        self.server_mgr.start()
        self._refresh_visual_state()

    def stop_server(self):
        self.desired_running = False
        self.server_mgr.stop()
        self._refresh_visual_state()

    def _exit(self):
        logging.info("Exiting tray")
        self.stop_event.set()
        self.desired_running = False
        self.server_mgr.stop()
        self._refresh_visual_state()
        self.icon.stop()

    def run(self):
        threading.Thread(target=self._monitor_server_status, daemon=True).start()
        self._refresh_visual_state()
        self.icon.run()

# ─── Application Orchestration ───────────────────────────────────────────────

class Application:
    def __init__(self):
        self.paths = Paths()
        self.arg_mgr = ArgumentManager(self.paths.arguments_file)
        self.version = VersionManager.read_current_version(self.paths)

        # Build the exact command string for registry
        script = Path(__file__).resolve()
        if getattr(sys, "frozen", False):
            exe_cmd = f'"{sys.executable}"'
        else:
            exe_cmd = f'"{sys.executable}" "{script}"'

        self.auto_mgr = AutoStartManager(APP_NAME, exe_cmd)
        self.server_mgr = ServerManager(self.paths.uxplay_exe, self.arg_mgr)
        self.bonjour_mgr = BonjourServiceManager()
        self.update_checker = UpdateChecker(self.version)
        self.tray      = TrayIcon(
            self.paths.icon_file,
            self.paths,
            self.server_mgr,
            self.bonjour_mgr,
            self.arg_mgr,
            self.auto_mgr,
            self.update_checker,
        )

    def run(self):
        self.arg_mgr.ensure_exists()
        if not DependencyManager.notify_if_missing(self.paths):
            logging.warning("Critical dependency missing. Tray stays active for diagnostics.")

        # delay server start so the tray icon appears immediately
        threading.Thread(target=self._delayed_start, daemon=True).start()

        logging.info("Launching tray icon")
        self.tray.run()
        logging.info("Tray exited – shutting down")

    def _delayed_start(self):
        time.sleep(3)
        self.tray.start_server()

if __name__ == "__main__":
    Application().run()
