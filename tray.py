import sys
import csv
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
from datetime import datetime

from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pystray
from PIL import Image, ImageDraw, ImageGrab, ImageOps, ImageTk

try:
    from ctypes import wintypes
    from pystray._util import win32 as pystray_win32
except Exception:
    # Keep fallback behavior if backend internals are unavailable.
    wintypes = None
    pystray_win32 = None

# ─── Constants ────────────────────────────────────────────────────────────────

APP_NAME = "uxplay-windows"
APPDATA_DIR = Path(os.environ["APPDATA"]) / "uxplay-windows"
SNAPSHOT_DIR = Path.home() / "Pictures" / "UxPlay"
LOG_FILE = APPDATA_DIR / f"{APP_NAME}.log"
DEFAULT_APP_VERSION = "0.0.0"
BONJOUR_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Bonjour Service"
BONJOUR_SERVICE_NAME = "Bonjour Service"
BONJOUR_DOWNLOAD_URL = (
    "https://download.info.apple.com/Mac_OS_X/061-8098.20100603.gthyu/"
    "BonjourPSSetup.exe"
)
UXPLAY_WINDOWS_RELEASES_URL = "https://github.com/kaktools/uxplay-windows/releases"
UXPLAY_UPSTREAM_RELEASES_URL = "https://github.com/FDH2/UxPlay/releases"
UPDATE_REPO_API_URL = "https://api.github.com/repos/kaktools/uxplay-windows/releases/latest"
UXPLAY_EXE_NAME = "uxplay.exe"
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
VK_9 = 0x39
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

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


if pystray_win32 is not None:
    class TrayMenuIcon(pystray.Icon):
        """Windows-only override: show tray menu on left and right click."""

        def _on_notify(self, wparam, lparam):
            if self._menu_handle and lparam in (pystray_win32.WM_LBUTTONUP, pystray_win32.WM_RBUTTONUP):
                pystray_win32.SetForegroundWindow(self._hwnd)
                point = wintypes.POINT()
                pystray_win32.GetCursorPos(ctypes.byref(point))

                hmenu, descriptors = self._menu_handle
                index = pystray_win32.TrackPopupMenuEx(
                    hmenu,
                    pystray_win32.TPM_RIGHTALIGN | pystray_win32.TPM_BOTTOMALIGN | pystray_win32.TPM_RETURNCMD,
                    point.x,
                    point.y,
                    self._menu_hwnd,
                    None,
                )
                if index > 0:
                    descriptors[index - 1](self)
                return

            super()._on_notify(wparam, lparam)
else:
    TrayMenuIcon = pystray.Icon

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
        self._lock = threading.Lock()

    @staticmethod
    def _list_running_pids(image_name: str) -> List[int]:
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {image_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode != 0:
                return []

            rows = [r for r in csv.reader((result.stdout or "").splitlines()) if r]
            pids: List[int] = []
            for row in rows:
                if not row:
                    continue
                # "INFO: No tasks are running..."
                if row[0].startswith("INFO:"):
                    continue
                if len(row) >= 2:
                    try:
                        pids.append(int(row[1]))
                    except ValueError:
                        continue
            return pids
        except Exception:
            logging.exception("Failed listing running pids for %s", image_name)
            return []

    @staticmethod
    def _kill_pid(pid: int) -> bool:
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=8,
            )
            return result.returncode == 0
        except Exception:
            logging.exception("Failed killing pid %s", pid)
            return False

    def _cleanup_stale_processes(self) -> None:
        running = self._list_running_pids(UXPLAY_EXE_NAME)
        if not running:
            return
        logging.warning("Found stale uxplay.exe processes: %s", running)
        for pid in running:
            self._kill_pid(pid)

    def start(self) -> None:
        with self._lock:
            if self.process and self.process.poll() is None:
                logging.info("UxPlay server already running (PID %s)", self.process.pid)
                return

            # Prevent duplicate instances started outside current process handle.
            if self._list_running_pids(UXPLAY_EXE_NAME):
                logging.info("UxPlay already running (external instance detected)")
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
                time.sleep(0.6)
                if self.process.poll() is not None:
                    logging.error("UxPlay exited immediately after start")
                    self.process = None
                    return
                logging.info("Started UxPlay (PID %s)", self.process.pid)
            except Exception:
                logging.exception("Failed to launch UxPlay")
                self.process = None

    def stop(self) -> None:
        with self._lock:
            had_process = bool(self.process and self.process.poll() is None)
            if had_process:
                pid = self.process.pid
                logging.info("Stopping UxPlay (PID %s)...", pid)
                try:
                    self.process.terminate()
                    self.process.wait(timeout=4)
                    logging.info("UxPlay stopped cleanly.")
                except subprocess.TimeoutExpired:
                    logging.warning("Terminate timeout; force-killing UxPlay PID %s", pid)
                    self._kill_pid(pid)
                except Exception:
                    logging.exception("Error stopping UxPlay")
                finally:
                    self.process = None

            # Always clean up orphan/stuck uxplay.exe processes.
            self._cleanup_stale_processes()

            if not had_process and not self._list_running_pids(UXPLAY_EXE_NAME):
                logging.info("UxPlay server not running.")

    def is_running(self) -> bool:
        if self.process and self.process.poll() is None:
            return True
        return bool(self._list_running_pids(UXPLAY_EXE_NAME))


class WindowCapture:
    @staticmethod
    def _find_uxplay_window(pids: List[int]) -> Optional[Tuple[int, Tuple[int, int, int, int]]]:
        user32 = ctypes.windll.user32
        pid_windows: List[Tuple[int, Tuple[int, int, int, int]]] = []
        title_windows: List[Tuple[int, Tuple[int, int, int, int]]] = []

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _window_text(hwnd: int) -> str:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""

        @EnumWindowsProc
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = _window_text(hwnd)
            title_l = title.lower()

            # Exclude local helper windows from capture.
            if "control center" in title_l or " - help" in title_l:
                return True

            pid_match = int(pid.value) in pids if pids else False
            title_match = ("uxplay windows - uxplay receiver" in title_l) or ("airplay" in title_l)
            if pids and not pid_match:
                return True
            if not pids and not title_match:
                return True

            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True

            w = r.right - r.left
            h = r.bottom - r.top
            if w < 120 or h < 90:
                return True

            rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            entry = (int(hwnd), rect)
            if pid_match:
                pid_windows.append(entry)
            elif title_match:
                title_windows.append(entry)
            return True

        user32.EnumWindows(callback, 0)
        candidates = pid_windows if pid_windows else title_windows
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[1][2] - item[1][0]) * (item[1][3] - item[1][1]))

    @staticmethod
    def _find_uxplay_window_rect(pids: List[int]) -> Optional[Tuple[int, int, int, int]]:
        found = WindowCapture._find_uxplay_window(pids)
        if not found:
            return None
        return found[1]

    @staticmethod
    def _capture_uxplay_window(hwnd: int, out_file: Path) -> bool:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Minimized windows often cannot provide valid frame content.
        if user32.IsIconic(hwnd):
            return False

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 3),
            ]

        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return False
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 2 or height < 2:
            return False

        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return False

        hdc_mem = None
        hbitmap = None
        old_obj = None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                return False

            hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hbitmap:
                return False

            old_obj = gdi32.SelectObject(hdc_mem, hbitmap)
            if not old_obj:
                return False

            # Capture client content directly from target window (independent of z-order overlays).
            pw_client_only = 0x00000001
            pw_render_full_content = 0x00000002
            flags_to_try = (
                pw_client_only | pw_render_full_content,
                pw_render_full_content,
                pw_client_only,
            )
            printed = 0
            for flags in flags_to_try:
                printed = user32.PrintWindow(hwnd, hdc_mem, flags)
                if printed:
                    break
            if not printed:
                return False

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # top-down for PIL compatibility
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            buf_len = width * height * 4
            pixels = (ctypes.c_ubyte * buf_len)()
            copied = gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, pixels, ctypes.byref(bmi), 0)
            if copied != height:
                return False

            image = Image.frombuffer("RGBA", (width, height), bytes(pixels), "raw", "BGRA", 0, 1)
            image.save(out_file, "PNG")
            return True
        except Exception:
            logging.exception("Window content capture via PrintWindow failed")
            return False
        finally:
            if hdc_mem and old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            if hbitmap:
                gdi32.DeleteObject(hbitmap)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

    @staticmethod
    def capture_best_effort(
        server_mgr: ServerManager,
        out_file: Path,
        prefer_window: bool = True,
        allow_desktop_fallback: bool = True,
    ) -> Tuple[bool, str]:
        try:
            if prefer_window:
                pids = server_mgr._list_running_pids(UXPLAY_EXE_NAME)
                window_info = WindowCapture._find_uxplay_window(pids)
                if window_info:
                    hwnd, _rect = window_info
                    if WindowCapture._capture_uxplay_window(hwnd, out_file):
                        return (True, "window")
                    return (False, "window-capture-failed")
                if not allow_desktop_fallback:
                    return (False, "window-missing")

            ImageGrab.grab(all_screens=True).save(out_file, "PNG")
            return (True, "desktop")
        except Exception:
            logging.exception("Screenshot capture failed")
            return (False, "none")


class ProcessFreezer:
    @staticmethod
    def _open_process(pid: int):
        return ctypes.windll.kernel32.OpenProcess(
            PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )

    @staticmethod
    def suspend_pid(pid: int) -> bool:
        handle = ProcessFreezer._open_process(pid)
        if not handle:
            return False
        try:
            status = ctypes.windll.ntdll.NtSuspendProcess(handle)
            return status == 0
        except Exception:
            logging.exception("Failed suspending pid %s", pid)
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    @staticmethod
    def resume_pid(pid: int) -> bool:
        handle = ProcessFreezer._open_process(pid)
        if not handle:
            return False
        try:
            status = ctypes.windll.ntdll.NtResumeProcess(handle)
            return status == 0
        except Exception:
            logging.exception("Failed resuming pid %s", pid)
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)


class WindowStyler:
    def __init__(self, paths: Paths):
        self.paths = paths
        self._icon_handle = None
        self._styled_hwnds: set[int] = set()
        self._original_styles: dict[int, int] = {}

    @staticmethod
    def _window_text(hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""

    def _load_icon_handle(self):
        if self._icon_handle:
            return self._icon_handle
        try:
            user32 = ctypes.windll.user32
            self._icon_handle = user32.LoadImageW(
                None,
                str(self.paths.icon_file),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
        except Exception:
            logging.exception("Could not load window icon from %s", self.paths.icon_file)
            self._icon_handle = None
        return self._icon_handle

    def apply_to_uxplay_windows(self, server_mgr: ServerManager) -> None:
        pids = server_mgr._list_running_pids(UXPLAY_EXE_NAME)
        if not pids:
            self._styled_hwnds.clear()
            return

        user32 = ctypes.windll.user32
        icon_handle = self._load_icon_handle()
        target_title = "UxPlay Windows - UxPlay Receiver"
        found_hwnds: set[int] = set()

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @EnumWindowsProc
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) not in pids:
                return True

            found_hwnds.add(int(hwnd))
            if int(hwnd) in self._styled_hwnds:
                return True

            try:
                # Override renderer title with user-facing app title.
                current = self._window_text(int(hwnd))
                if current != target_title:
                    user32.SetWindowTextW(hwnd, target_title)

                if icon_handle:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, icon_handle)
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, icon_handle)

                self._styled_hwnds.add(int(hwnd))
            except Exception:
                logging.exception("Failed applying window styling to hwnd=%s", int(hwnd))
            return True

        user32.EnumWindows(callback, 0)
        # Keep only still-existing handles to avoid unbounded growth.
        self._styled_hwnds.intersection_update(found_hwnds)

    def _get_uxplay_hwnds(self, server_mgr: ServerManager) -> List[int]:
        pids = set(server_mgr._list_running_pids(UXPLAY_EXE_NAME))
        if not pids:
            return []

        user32 = ctypes.windll.user32
        hwnds: List[int] = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @EnumWindowsProc
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) in pids:
                hwnds.append(int(hwnd))
            return True

        user32.EnumWindows(callback, 0)
        return hwnds

    def set_resizable(self, server_mgr: ServerManager, enabled: bool) -> None:
        user32 = ctypes.windll.user32
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
        for hwnd in self._get_uxplay_hwnds(server_mgr):
            try:
                style = int(user32.GetWindowLongW(hwnd, GWL_STYLE))
                if enabled:
                    original = self._original_styles.pop(hwnd, style)
                    new_style = original
                else:
                    self._original_styles.setdefault(hwnd, style)
                    new_style = style & ~(WS_THICKFRAME | WS_MAXIMIZEBOX)

                if new_style != style:
                    user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
                    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
            except Exception:
                logging.exception("Failed toggling resizable style for hwnd=%s", hwnd)


class GlobalHotkeyManager:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._registered = False
        self._running = threading.Event()
        self._started_event = threading.Event()
        self._on_hotkey: Optional[Callable[[], None]] = None

    def start(self, on_hotkey: Callable[[], None]) -> bool:
        self._on_hotkey = on_hotkey
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started_event.wait(timeout=2.5)
        return self._registered

    def stop(self) -> None:
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                logging.exception("Failed posting WM_QUIT to hotkey thread")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        user32 = ctypes.windll.user32

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_size_t),
                ("time", ctypes.c_uint),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
            ]

        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        hotkey_id = 1
        self._registered = bool(user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL, VK_9))
        self._running.set()
        self._started_event.set()

        if not self._registered:
            logging.warning("Global hotkey Ctrl+9 could not be registered (likely already in use)")
            return

        try:
            msg = MSG()
            while self._running.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == hotkey_id:
                    if self._on_hotkey:
                        try:
                            self._on_hotkey()
                        except Exception:
                            logging.exception("Global hotkey callback failed")
        finally:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                logging.exception("Failed to unregister global hotkey")
            self._registered = False
            self._running.clear()

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
                "UxPlay cannot work without its runtime.",
                "",
                "Click Yes to open download/help pages now.",
                "Click No to exit the app.",
            ]
        else:
            lines += [
                "",
                "UxPlay may not work until dependencies are installed.",
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
    def _run_restart_command(command: str, timeout: int = 25) -> bool:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return True
            logging.warning("Bonjour restart command failed (%s): %s", result.returncode, (result.stderr or result.stdout or "").strip())
        except Exception:
            logging.exception("Bonjour restart command execution failed")
        return False

    @staticmethod
    def restart() -> bool:
        if not DependencyManager.is_bonjour_installed():
            logging.warning("Bonjour service is not installed; skipping restart")
            return False

        if BonjourServiceManager._run_restart_command(
            f"Restart-Service -Name '{BONJOUR_SERVICE_NAME}' -Force"
        ):
            logging.info("Bonjour service restarted successfully")
            return True

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

        # Final attempt: explicit UAC elevation for one-time admin restart.
        if BonjourServiceManager.restart_elevated_once():
            return True

        return False

    @staticmethod
    def restart_elevated_once() -> bool:
        # Launch elevated cmd for restart, then verify service state.
        try:
            params = (
                '/C sc stop "Bonjour Service" >nul 2>&1 '
                '& timeout /t 1 /nobreak >nul '
                '& sc start "Bonjour Service" >nul 2>&1'
            )
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, None, 0)
            if rc <= 32:
                logging.warning("User denied UAC or elevation failed (ShellExecute=%s)", rc)
                return False

            for _ in range(12):
                time.sleep(1)
                if BonjourServiceManager.is_running():
                    logging.info("Bonjour service restarted successfully via elevation")
                    return True
            logging.warning("Elevated Bonjour restart launched but service did not report RUNNING in time")
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
        self._hotkey_status_label: Optional[tk.Label] = None
        self._paused_overlay: Optional[tk.Label] = None
        self._autostart_var: Optional[tk.IntVar] = None
        self._autostart_toggle_canvas: Optional[tk.Canvas] = None
        self._autostart_toggle_knob: Optional[int] = None
        self._autostart_toggle_track: Optional[int] = None
        self._version_label: Optional[tk.Label] = None
        self._logo_img: Optional[ImageTk.PhotoImage] = None
        self._help_window: Optional[tk.Toplevel] = None
        self._pending_help_popup = False
        self._lock = threading.Lock()

    def _root_alive(self) -> bool:
        if not self._root:
            return False
        try:
            return bool(self._root.winfo_exists())
        except tk.TclError:
            return False

    def _safe_after(self, delay_ms: int, callback) -> None:
        if not self._root_alive():
            return
        try:
            self._root.after(delay_ms, callback)
        except tk.TclError:
            return

    def shutdown(self) -> None:
        with self._lock:
            if not self._root_alive():
                return
            self._safe_after(0, self._destroy_root)

    def _destroy_root(self) -> None:
        try:
            if self._help_window and self._help_window.winfo_exists():
                self._help_window.destroy()
        except Exception:
            pass
        try:
            if self._root and self._root.winfo_exists():
                self._root.destroy()
        except Exception:
            pass

    def show(self) -> None:
        with self._lock:
            if self._root_alive():
                self._safe_after(0, self._focus_existing)
                return

            # If Tk thread is still tearing down, retry shortly instead of dropping the request.
            if self._thread and self._thread.is_alive():
                retry = threading.Timer(0.15, self.show)
                retry.daemon = True
                retry.start()
                return

            self._thread = threading.Thread(target=self._run_window, daemon=True)
            self._thread.start()

    def show_help(self) -> None:
        with self._lock:
            if self._root_alive():
                self._safe_after(0, self._open_help_popup)
                return
            self._pending_help_popup = True
        self.show()

    def _focus_existing(self) -> None:
        if not self._root_alive():
            return
        try:
            try:
                is_hidden = str(self._root.state()) == "withdrawn"
            except Exception:
                is_hidden = False
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)
            if is_hidden:
                self._root.attributes("-alpha", 0.0)
                self._fade_in()
            self._safe_after(250, lambda: self._root_alive() and self._root.attributes("-topmost", False))
        except tk.TclError:
            return

    def _run_window(self) -> None:
        try:
            root = tk.Tk()
            self._root = root
            root.title("UxPlay - Control Center")
            width = 560
            height = 490
            sx = root.winfo_screenwidth()
            sy = root.winfo_screenheight()
            px = max(40, (sx - width) // 2)
            py = max(40, (sy - height) // 2)
            root.geometry(f"{width}x{height}+{px}+{py}")
            root.resizable(False, False)
            root.overrideredirect(True)
            root.configure(bg="#0A1118")
            root.attributes("-alpha", 0.0)

            outer = tk.Frame(root, bg="#0A1118", bd=0)
            outer.pack(fill="both", expand=True, padx=1, pady=1)

            shell = tk.Frame(outer, bg="#0F1923", bd=0, highlightthickness=1, highlightbackground="#2F4A5F")
            shell.pack(fill="both", expand=True, padx=10, pady=10)

            title_bar = tk.Frame(shell, bg="#0D2332", height=48)
            title_bar.pack(fill="x")
            title_bar.pack_propagate(False)

            logo_img = self.tray.normal_icon.copy().resize((18, 18), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(logo_img)
            logo_label = tk.Label(title_bar, image=self._logo_img, bg="#0D2332")
            logo_label.pack(side="left", padx=(14, 8))

            title_label = tk.Label(
                title_bar,
                text="UxPlay - Control Center",
                font=("Segoe UI Semibold", 12),
                bg="#0D2332",
                fg="#EFF7FF",
            )
            title_label.pack(side="left", padx=(0, 8))

            close_btn = tk.Label(
                title_bar,
                text="x",
                font=("Segoe UI Semibold", 11),
                bg="#0D2332",
                fg="#D3E7FA",
                padx=12,
                pady=8,
                cursor="hand2",
            )
            close_btn.pack(side="right", padx=(0, 6))

            help_icon = tk.Label(
                title_bar,
                text="?",
                font=("Segoe UI Semibold", 11),
                bg="#0D2332",
                fg="#D3E7FA",
                padx=10,
                pady=8,
                cursor="hand2",
            )
            help_icon.pack(side="right", padx=(2, 4))

            content = tk.Frame(shell, bg="#0F1923")
            content.pack(fill="both", expand=True, padx=14, pady=12)

            self._make_draggable(root, title_bar, logo_label, title_label)
            help_icon.bind("<Enter>", lambda _: help_icon.config(bg="#17435F", fg="#FFFFFF"))
            help_icon.bind("<Leave>", lambda _: help_icon.config(bg="#0D2332", fg="#D3E7FA"))
            help_icon.bind("<Button-1>", lambda _: self._open_help_popup())
            close_btn.bind("<Enter>", lambda _: close_btn.config(bg="#4A1F2B", fg="#FFDCE2"))
            close_btn.bind("<Leave>", lambda _: close_btn.config(bg="#0D2332", fg="#D3E7FA"))
            close_btn.bind("<Button-1>", lambda _: self._close_animated())

            status_panel = tk.Frame(content, bg="#122331", highlightthickness=1, highlightbackground="#2D556E")
            status_panel.pack(fill="x")

            status_body = tk.Frame(status_panel, bg="#122331")
            status_body.pack(fill="x", padx=12, pady=10)

            status_left = tk.Frame(status_body, bg="#122331")
            status_left.pack(side="left", fill="both", expand=True)

            status_right = tk.Frame(status_body, bg="#122331")
            status_right.pack(side="right", fill="y", padx=(14, 0))

            self._status_chip = tk.Label(status_left, text="", font=("Segoe UI Semibold", 9), padx=12, pady=4)
            # Keep widget allocated for compatibility, but hide it.
            self._status_chip.pack_forget()

            self._status_title = tk.Label(status_left, text="", font=("Segoe UI Semibold", 15), bg="#122331", fg="#F4FAFF")
            self._status_title.pack(anchor="w", pady=(6, 0))

            self._status_details = tk.Label(status_left, text="", font=("Segoe UI", 10), bg="#122331", fg="#D3E5F7", wraplength=410, justify="left")
            self._status_details.pack(anchor="w", pady=(4, 1))

            self._hotkey_status_label = tk.Label(
                status_left,
                text="",
                font=("Segoe UI", 10),
                bg="#122331",
                fg="#AFCBE4",
            )
            self._hotkey_status_label.pack(anchor="w", pady=(0, 8))

            self._paused_overlay = tk.Label(
                status_panel,
                text="",
                font=("Segoe UI Semibold", 10),
                bg="#122331",
                fg="#FFABC0",
                padx=12,
                pady=4,
            )
            self._paused_overlay.pack(fill="x", padx=12, pady=(0, 8))

            self._autostart_var = tk.IntVar(value=1 if self.tray.auto_mgr.is_enabled() else 0)
            auto_row = tk.Frame(status_right, bg="#122331")
            auto_row.pack(fill="x", pady=(0, 8))
            tk.Label(auto_row, text="Autostart", font=("Segoe UI", 10), bg="#122331", fg="#AFC5DA").pack(side="left", padx=(0, 8))
            self._autostart_toggle_canvas = tk.Canvas(
                auto_row,
                width=58,
                height=30,
                bg="#122331",
                bd=0,
                highlightthickness=0,
                relief="flat",
                cursor="hand2",
            )
            self._autostart_toggle_canvas.pack(side="left")
            self._autostart_toggle_track = self._autostart_toggle_canvas.create_oval(1, 3, 57, 27, fill="#547FBC", outline="#4A73AD", width=1)
            self._autostart_toggle_knob = self._autostart_toggle_canvas.create_oval(31, 4, 53, 26, fill="#F3F8FF", outline="#E2ECF8", width=1)
            self._autostart_toggle_canvas.bind("<Button-1>", lambda _: self._toggle_autostart())
            self._autostart_toggle_canvas.bind("<Enter>", lambda _: self._autostart_toggle_canvas and self._autostart_toggle_canvas.config(bg="#153046"))
            self._autostart_toggle_canvas.bind("<Leave>", lambda _: self._autostart_toggle_canvas and self._autostart_toggle_canvas.config(bg="#122331"))
            self._sync_autostart_toggle()

            snapshot_quick = tk.Button(
                status_right,
                text="📷  Snapshot",
                command=lambda: self._run_async(self.tray.capture_screenshot),
                relief="flat",
                bd=0,
                bg="#1E3244",
                activebackground="#28465F",
                fg="#E6EEF8",
                font=("Segoe UI Semibold", 10),
                anchor="w",
                justify="left",
                padx=9,
                pady=6,
                cursor="hand2",
            )
            snapshot_quick.pack(fill="x", pady=(0, 6))

            update_quick = tk.Button(
                status_right,
                text="⟳  Update",
                command=lambda: self._run_async(lambda: self.tray.update_checker.check_for_updates(interactive=True)),
                relief="flat",
                bd=0,
                bg="#1E3244",
                activebackground="#28465F",
                fg="#E6EEF8",
                font=("Segoe UI Semibold", 10),
                anchor="w",
                justify="left",
                padx=9,
                pady=6,
                cursor="hand2",
            )
            update_quick.pack(fill="x", pady=(0, 8))

            divider = tk.Frame(content, bg="#3A657D", height=1)
            divider.pack(fill="x", pady=(8, 8))

            cards = tk.Frame(content, bg="#0F1923")
            cards.pack(fill="x", pady=(0, 8))

            card_specs = [
                ("▶ Start", "Launch UxPlay receiver", self.tray.start_server, "#1A3A33", "#7EDAB4"),
                ("■ Stop", "Stop UxPlay receiver", self.tray.stop_server, "#3D242C", "#F3A9B8"),
                ("↻ Restart", "Restart UxPlay + Bonjour", self.tray._restart, "#20354D", "#9AC5F2"),
                ("⏸ Pause/Play", "Freze or resume.", self.tray.toggle_pause, "#3A3920", "#E7D27C"),
            ]
            for idx, (title, subtitle, fn, bg, fg) in enumerate(card_specs):
                card = self._create_action_card(cards, title, subtitle, fn, bg, fg)
                card.grid(row=idx // 2, column=idx % 2, padx=6, pady=6, sticky="nsew")
            cards.grid_columnconfigure(0, weight=1)
            cards.grid_columnconfigure(1, weight=1)
            cards.grid_rowconfigure(0, weight=1)
            cards.grid_rowconfigure(1, weight=1)

            meta = tk.Frame(content, bg="#0F1923")
            meta.pack(fill="x", pady=(0, 0))
            self._copyright_label = tk.Label(
                meta,
                text="\u00a9 KaKTools",
                bg="#0F1923",
                fg="#AFCBE4",
                font=("Segoe UI", 9),
            )
            self._copyright_label.pack(side="left", padx=(4, 0))
            self._version_label = tk.Label(
                meta,
                text=f"Version {self.tray.update_checker.current_version}",
                bg="#0F1923",
                fg="#D6E8FA",
                font=("Segoe UI Semibold", 9),
            )
            self._version_label.pack(side="right", padx=(0, 4))

            root.protocol("WM_DELETE_WINDOW", self._close_animated)
            self._refresh_loop()
            self._fade_in()
            if self._pending_help_popup:
                self._pending_help_popup = False
                root.after(250, self._open_help_popup)
            root.mainloop()
        except Exception:
            logging.exception("Failed opening Control Center window")
            self.tray.notify_user("Control Center failed", "Could not open the popup window.")
        finally:
            with self._lock:
                self._root = None
                self._thread = None
                self._help_window = None

    @staticmethod
    def _status_row(parent: tk.Widget, label: str, row: int) -> tk.Label:
        tk.Label(parent, text=label, font=("Segoe UI", 10), bg="#122231", fg="#AFC5DA").grid(row=row, column=0, sticky="w", padx=12, pady=7)
        value = tk.Label(parent, text="-", font=("Segoe UI Semibold", 10), bg="#122231", fg="#EEF6FF")
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
        card = tk.Frame(parent, bg=bg_color, highlightthickness=1, highlightbackground="#3A566D", cursor="hand2")
        card.configure(height=94)
        card.pack_propagate(False)

        title_lbl = tk.Label(card, text=title, font=("Segoe UI Semibold", 12), bg=bg_color, fg="#EEF6FF")
        title_lbl.pack(anchor="w", padx=10, pady=(10, 3))

        sub_lbl = tk.Label(card, text=subtitle, font=("Segoe UI", 9), bg=bg_color, fg="#B3C7DA", wraplength=210, justify="left")
        sub_lbl.pack(anchor="w", padx=10, pady=(0, 10))

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
        if not self._root_alive():
            return
        try:
            alpha = float(self._root.attributes("-alpha"))
        except Exception:
            alpha = 0.0
        alpha = min(1.0, alpha + 0.12)
        try:
            self._root.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha < 1.0:
            self._safe_after(20, self._fade_in)

    def _close_animated(self) -> None:
        if not self._root_alive():
            return
        try:
            alpha = float(self._root.attributes("-alpha"))
        except Exception:
            alpha = 1.0
        alpha = max(0.0, alpha - 0.16)
        try:
            self._root.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha <= 0.0:
            try:
                if self._help_window and self._help_window.winfo_exists():
                    self._help_window.withdraw()
            except Exception:
                pass
            try:
                if self._root and self._root.winfo_exists():
                    self._root.withdraw()
                    self._root.attributes("-alpha", 0.0)
            except Exception:
                pass
        else:
            self._safe_after(16, self._close_animated)

    def _run_async(self, action) -> None:
        threading.Thread(target=action, daemon=True).start()

    def _toggle_autostart(self) -> None:
        self.tray.auto_mgr.toggle()
        enabled = self.tray.auto_mgr.is_enabled()
        if self._autostart_var is not None:
            self._autostart_var.set(1 if enabled else 0)
        self._sync_autostart_toggle()
        self.tray._refresh_menu_state()
        self.tray.notify_user("Autostart", "Enabled" if enabled else "Disabled")

    def _sync_autostart_toggle(self) -> None:
        if not self._autostart_toggle_canvas or self._autostart_toggle_track is None or self._autostart_toggle_knob is None:
            return
        enabled = bool(self._autostart_var and self._autostart_var.get())
        if enabled:
            self._autostart_toggle_canvas.itemconfigure(self._autostart_toggle_track, fill="#5E8CCB", outline="#4A73AD")
            self._autostart_toggle_canvas.coords(self._autostart_toggle_knob, 31, 4, 53, 26)
        else:
            self._autostart_toggle_canvas.itemconfigure(self._autostart_toggle_track, fill="#395B84", outline="#2F4D70")
            self._autostart_toggle_canvas.coords(self._autostart_toggle_knob, 5, 4, 27, 26)

    def _refresh_loop(self) -> None:
        if not self._root_alive():
            return

        snapshot = self.tray.get_health_snapshot()
        state = snapshot["state"]
        details = snapshot["details"]

        if self._status_title:
            self._status_title.config(text="Live Status")
        if self._status_details:
            self._status_details.config(text=details)
        if self._hotkey_status_label:
            self._hotkey_status_label.config(text=f"Ctrl+9 saves a snapshot ({snapshot['hotkey_status']})")
        if self._paused_overlay:
            if snapshot["paused"]:
                self._paused_overlay.config(text="PAUSED", bg="#3D1822", fg="#FFC3CF")
            elif state == "running":
                self._paused_overlay.config(text="RUNNING", bg="#1E3B33", fg="#96F2C8")
            elif state == "error":
                self._paused_overlay.config(text="ERROR", bg="#46232B", fg="#FFB5C3")
            else:
                self._paused_overlay.config(text="IDLE", bg="#1E3444", fg="#B8D7F3")

        if self._autostart_var is not None:
            self._autostart_var.set(1 if snapshot["autostart"] else 0)
        self._sync_autostart_toggle()

        self._safe_after(900, self._refresh_loop)

    def _open_help_popup(self) -> None:
        if not self._root:
            return
        if self._help_window and self._help_window.winfo_exists():
            self._help_window.deiconify()
            self._help_window.lift()
            return

        win = tk.Toplevel(self._root)
        self._help_window = win
        win.title("Ux Play - Help")
        win.geometry("760x620")
        win.overrideredirect(True)
        win.configure(bg="#0A1118")
        win.resizable(False, False)

        # Center near Control Center window
        try:
            bx = self._root.winfo_x()
            by = self._root.winfo_y()
            win.geometry(f"760x620+{bx + 22}+{by + 22}")
        except Exception:
            pass

        outer = tk.Frame(win, bg="#0A1118", bd=0)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        shell = tk.Frame(outer, bg="#0F1923", highlightthickness=1, highlightbackground="#2F4A5F")
        shell.pack(fill="both", expand=True, padx=10, pady=10)

        header = tk.Frame(shell, bg="#0D2332", height=48)
        header.pack(fill="x")
        header.pack_propagate(False)

        logo = self.tray.normal_icon.copy().resize((22, 22), Image.LANCZOS)
        logo_tk = ImageTk.PhotoImage(logo)
        logo_lbl = tk.Label(header, image=logo_tk, bg="#0D2332")
        logo_lbl.image = logo_tk
        logo_lbl.pack(side="left", padx=(14, 8), pady=8)

        tk.Label(
            header,
            text="Ux Play - Help",
            font=("Segoe UI Semibold", 12),
            bg="#0D2332",
            fg="#EFF7FF",
        ).pack(side="left", pady=8)

        close_help = tk.Label(
            header,
            text="x",
            font=("Segoe UI Semibold", 11),
            bg="#0D2332",
            fg="#D3E7FA",
            padx=10,
            pady=8,
            cursor="hand2",
        )
        close_help.pack(side="right", padx=(2, 6))
        close_help.bind("<Enter>", lambda _: close_help.config(bg="#4A1F2B", fg="#FFDCE2"))
        close_help.bind("<Leave>", lambda _: close_help.config(bg="#0D2332", fg="#D3E7FA"))
        close_help.bind("<Button-1>", lambda _: win.destroy())

        self._make_draggable(win, header, logo_lbl)

        body = tk.Frame(shell, bg="#0F1923")
        body.pack(fill="both", expand=True, padx=18, pady=16)

        help_panel = tk.Frame(body, bg="#122231", highlightthickness=1, highlightbackground="#2C5168")
        help_panel.pack(fill="both", expand=True)

        scroll = tk.Scrollbar(help_panel)
        scroll.pack(side="right", fill="y")

        help_text = tk.Text(
            help_panel,
            wrap="word",
            relief="flat",
            bd=0,
            bg="#122231",
            fg="#DCEBFA",
            font=("Segoe UI", 10),
            padx=14,
            pady=12,
            yscrollcommand=scroll.set,
        )
        help_text.pack(side="left", fill="both", expand=True)
        scroll.config(command=help_text.yview)

        doc = (
            "UxPlay - Help\n"
            "================\n\n"
            "Was ist dieses Tool?\n"
            "Das UxPlay - Control Center steuert den UxPlay Receiver, den Bonjour-Dienst und Snapshot-Funktionen in einer Oberfläche.\n\n"
            "Was machen die Buttons?\n"
            "- Start: startet den UxPlay Receiver.\n"
            "- Stop: beendet den Receiver.\n"
            "- Restart: startet UxPlay und Bonjour neu (inkl. Admin-Fallback falls nötig).\n"
            "- Pause/Play: friert das aktive geteilte iPad-Bild ein oder setzt es fort.\n\n"
            "Wie funktionieren Snapshots?\n"
            "- Capture ist immer window-only.\n"
            "- Es wird nur das aktive UxPlay Fenster erfasst.\n"
            "- Kein Desktop-Fallback.\n"
            "- Hotkey: Ctrl+9 speichert ebenfalls einen Snapshot.\n\n"
            "Statusanzeige verstehen\n"
            "- RUNNING: Receiver aktiv und bereit für Streaming.\n"
            "- IDLE: Tool läuft, aber kein aktiver Stream.\n"
            "- ERROR: Eine Abhängigkeit oder ein Dienst fehlt/hängt.\n\n"
            "Autostart\n"
            "- Der Schalter 'Autostart' aktiviert/deaktiviert den Start mit Windows.\n\n"
            "Update prüfen\n"
            "- 'Update prüfen' fragt die neueste GitHub Release Version ab.\n"
            "- Bei neuer Version wird die Release-Seite geöffnet.\n\n"
            "Troubleshooting\n"
            "1. Kein Bild? Einmal Restart ausführen.\n"
            "2. Kein Bonjour? Tool mit Admin-Rechten starten und erneut Restart drücken.\n"
            "3. Snapshot ohne Inhalt? Sicherstellen, dass ein geteiltes iPad-Fenster aktiv ist.\n"
        )
        help_text.insert("1.0", doc)
        help_text.config(state="disabled")

        footer = tk.Frame(shell, bg="#0F1923")
        footer.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(
            footer,
            text="Close",
            command=win.destroy,
            relief="flat",
            bd=0,
            bg="#24597A",
            activebackground="#2E6D94",
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            padx=12,
            pady=6,
            cursor="hand2",
        ).pack(side="right")
        tk.Button(
            footer,
            text="README",
            command=lambda: webbrowser.open("https://github.com/kaktools/uxplay-windows/blob/main/README.md"),
            relief="flat",
            bd=0,
            bg="#1E4E69",
            activebackground="#2C6A8C",
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            padx=12,
            pady=6,
            cursor="hand2",
        ).pack(side="right", padx=(0, 10))

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
        self.receiver_paused = False
        self.capture_window_preferred = True
        self.capture_window_only = True
        self.restart_in_progress = False
        self._status_text = "Ready"
        self.control_center = ControlCenterWindow(self)
        self.window_styler = WindowStyler(paths)
        self.hotkeys = GlobalHotkeyManager()
        self.hotkey_active = False
        self._last_recover_attempt = 0.0
        self._dep_cache: List[str] = []
        self._dep_cache_ts = 0.0

        self.normal_icon = self._load_icon(icon_path).convert("RGBA").resize((16, 16), Image.LANCZOS)
        self.running_icon = self._with_indicator(self.normal_icon, (45, 182, 79, 255))
        self.error_icon = self._with_indicator(self.normal_icon, (224, 59, 75, 255))
        self.idle_icon = self.normal_icon
        self._last_state = ""

        status_item = pystray.MenuItem(
            lambda _: f"Status: {self._status_text}",
            None,
            enabled=False,
        )

        menu = pystray.Menu(
            status_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Control Center", lambda _: self.open_control_center(), default=True),
            pystray.MenuItem("Start", lambda _: self.start_server(), checked=lambda _: self.server_mgr.is_running() and not self.receiver_paused),
            pystray.MenuItem("Stop",  lambda _: self.stop_server(), checked=lambda _: not self.server_mgr.is_running()),
            pystray.MenuItem("Restart", lambda _: self._restart(), checked=lambda _: self.restart_in_progress),
            pystray.MenuItem("Pause UxPlay", lambda _: self.toggle_pause(), checked=lambda _: self.receiver_paused),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Autostart",
                lambda _: self.toggle_autostart(),
                checked=lambda _: auto_mgr.is_enabled()
            ),
            pystray.MenuItem(
                "Check Updates",
                lambda _: self.update_checker.check_for_updates(interactive=True)
            ),
            pystray.MenuItem("Save Snapshot", lambda _: self.capture_screenshot()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Help", lambda _: self.open_help()),
            pystray.MenuItem(
                "License",
                lambda _: webbrowser.open(
                    "https://github.com/kaktools/uxplay-windows/blob/"
                    "main/LICENSE.md"
                )
            ),
            pystray.MenuItem("Exit", lambda _: self._exit()),
        )

        self.icon = TrayMenuIcon(
            name="UxPlay Windows\nRight-click to configure.",
            icon=self.normal_icon,
            title="UxPlay Windows",
            menu=menu
        )
        self.update_checker.set_notifier(self.notify_user)

    def _refresh_menu_state(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            logging.exception("Could not refresh tray menu state")

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
    def _with_indicator(base_icon: Image.Image, rgba: tuple[int, int, int, int]) -> Image.Image:
        img = base_icon.copy().convert("RGBA")
        draw = ImageDraw.Draw(img)
        # Keep original logo and only add a tiny status dot.
        draw.ellipse((9, 9, 15, 15), fill=rgba, outline=(255, 255, 255, 235), width=1)
        return img

    def _get_cached_missing_dependencies(self) -> List[str]:
        now = time.time()
        if (now - self._dep_cache_ts) > 2.5:
            self._dep_cache = DependencyManager.get_missing_dependencies(self.paths)
            self._dep_cache_ts = now
        return self._dep_cache

    def _compute_health(self, missing: Optional[List[str]] = None) -> tuple[str, str]:
        errors: List[str] = []
        if missing is None:
            missing = self._get_cached_missing_dependencies()

        if self.receiver_paused:
            return ("idle", "Paused")

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
            return ("running", "UxPlay active")
        return ("idle", "Ready")

    def get_health_snapshot(self) -> dict:
        missing = self._get_cached_missing_dependencies()
        bonjour_state = "Missing"
        if "bonjour" not in missing:
            bonjour_state = "Running" if BonjourServiceManager.is_running() else "Stopped"

        state, details = self._compute_health(missing)
        return {
            "state": state,
            "details": details,
            "paused": self.receiver_paused,
            "runtime_ok": "uxplay-runtime" not in missing,
            "bonjour_state": bonjour_state,
            "server_running": self.server_mgr.is_running(),
            "autostart": self.auto_mgr.is_enabled(),
            "capture_mode": "Window only (fixed)",
            "hotkey_status": "active" if self.hotkey_active else "unavailable",
        }

    def _refresh_visual_state(self) -> None:
        missing = self._get_cached_missing_dependencies()
        state, details = self._compute_health(missing)
        self._status_text = details

        if state == self._last_state and self.icon.title == f"UxPlay Windows Control Center - {details}":
            self._refresh_menu_state()
            return

        if state == "running":
            self.icon.icon = self.running_icon
        elif state == "error":
            self.icon.icon = self.error_icon
        else:
            self.icon.icon = self.idle_icon

        self.icon.title = f"UxPlay Windows Control Center - {details}"
        self._last_state = state
        self._refresh_menu_state()

    def _monitor_server_status(self) -> None:
        while not self.stop_event.wait(2):
            if self.desired_running and not self.server_mgr.is_running():
                now = time.time()
                if now - self._last_recover_attempt > 12:
                    self._last_recover_attempt = now
                    logging.warning("Auto-recovery: UxPlay not running while desired_running=True")
                    self._restart()
            self.window_styler.apply_to_uxplay_windows(self.server_mgr)
            self._refresh_visual_state()

    def _health_check_popup(self) -> None:
        state, details = self._compute_health()
        title = "System Healthy" if state != "error" else "Action Required"
        self.notify_user(title, details)

    def open_control_center(self) -> None:
        self.control_center.show()

    def open_help(self) -> None:
        self.control_center.show_help()

    def _restart(self):
        logging.info("Restarting UxPlay and Bonjour service")
        self.restart_in_progress = True
        self._refresh_menu_state()
        try:
            self.receiver_paused = False
            self.desired_running = True
            self.window_styler.set_resizable(self.server_mgr, enabled=True)
            self.server_mgr.stop()
            if not self.bonjour_mgr.restart():
                logging.warning("Normal Bonjour restart failed, trying elevated restart fallback")
                if not self.bonjour_mgr.restart_elevated_once():
                    self.notify_user(
                        "Restart warning",
                        "UxPlay restarted, but Bonjour restart failed even with admin fallback.",
                    )
            self.server_mgr.start()
        finally:
            self.restart_in_progress = False
            self._refresh_visual_state()
            self._refresh_menu_state()

    def _restart_bonjour_admin(self):
        if self.bonjour_mgr.restart_elevated_once():
            self.notify_user("Bonjour restarted", "Bonjour Service restarted with elevated permissions.")
        else:
            self.notify_user("Bonjour restart failed", "Could not restart Bonjour Service with admin rights.")
        self._refresh_visual_state()

    def toggle_autostart(self):
        self.auto_mgr.toggle()
        self._refresh_menu_state()
        self._refresh_visual_state()
        self.notify_user("Autostart", "Enabled" if self.auto_mgr.is_enabled() else "Disabled")

    def toggle_pause(self):
        if self.receiver_paused:
            self.resume_receiver()
        else:
            self.pause_receiver()

    def pause_receiver(self):
        pids = self.server_mgr._list_running_pids(UXPLAY_EXE_NAME)
        if not pids:
            self.notify_user("Pause unavailable", "UxPlay is not running.")
            return

        if WindowCapture._find_uxplay_window_rect(pids) is None:
            self.notify_user("Pause unavailable", "No active shared iPad screen found.")
            return

        self.window_styler.set_resizable(self.server_mgr, enabled=False)
        suspended = 0
        for pid in pids:
            if ProcessFreezer.suspend_pid(pid):
                suspended += 1

        if suspended == 0:
            self.notify_user("Pause failed", "Could not freeze the UxPlay renderer process.")
            return

        self.receiver_paused = True
        self._refresh_visual_state()
        self._refresh_menu_state()
        self.notify_user("Paused", "UxPlay renderer is frozen. Press Play to resume updates.")

    def resume_receiver(self):
        pids = self.server_mgr._list_running_pids(UXPLAY_EXE_NAME)
        resumed = 0
        for pid in pids:
            if ProcessFreezer.resume_pid(pid):
                resumed += 1
        self.window_styler.set_resizable(self.server_mgr, enabled=True)

        self.receiver_paused = False
        if resumed == 0 and not self.server_mgr.is_running():
            # Fallback: restart receiver if process exited while paused.
            self.desired_running = True
            self.server_mgr.start()
        self._refresh_visual_state()
        self._refresh_menu_state()
        self.notify_user("Resumed", "UxPlay renderer is updating again.")

    def capture_screenshot(self, silent: bool = False):
        try:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_file = SNAPSHOT_DIR / f"airplay_capture_{ts}.png"
            ok, mode = WindowCapture.capture_best_effort(
                self.server_mgr,
                out_file,
                prefer_window=True,
                allow_desktop_fallback=False,
            )
            if not ok:
                if not silent:
                    if mode == "window-missing":
                        self.notify_user("Snapshot failed", "No UxPlay window found (strict window-only mode).")
                    elif mode == "window-capture-failed":
                        self.notify_user("Snapshot failed", "UxPlay window found, but direct window capture failed.")
                    else:
                        self.notify_user("Snapshot failed", "Could not capture screen/window.")
                return None

            if not silent:
                label = "UxPlay window" if mode == "window" else "desktop"
                self.notify_user("Snapshot saved", f"Saved {label} capture: {out_file}")
                self._open_snapshot_in_editor(out_file)
            return out_file
        except Exception:
            logging.exception("Screenshot capture failed")
            if not silent:
                self.notify_user("Snapshot failed", "Could not capture the current screen.")
            return None

    @staticmethod
    def _open_snapshot_in_editor(file_path: Path) -> None:
        # Wait briefly until the PNG exists and its size is stable.
        # This avoids launching the editor before the file is fully visible to Windows.
        deadline = time.time() + 2.0
        last_size = -1
        stable_count = 0
        while time.time() < deadline:
            try:
                if file_path.exists():
                    current_size = file_path.stat().st_size
                    if current_size > 0 and current_size == last_size:
                        stable_count += 1
                        if stable_count >= 2:
                            break
                    else:
                        stable_count = 0
                    last_size = current_size
            except Exception:
                stable_count = 0
            time.sleep(0.08)

        # Open with Windows default .png viewer (typically Photos or Photo Viewer).
        try:
            os.startfile(str(file_path))
        except Exception:
            logging.exception("Could not open snapshot viewer for %s", file_path)

    def start_server(self):
        self.receiver_paused = False
        self.desired_running = True
        self.server_mgr.start()
        self.window_styler.apply_to_uxplay_windows(self.server_mgr)
        self._refresh_visual_state()
        self._refresh_menu_state()

    def stop_server(self):
        self.receiver_paused = False
        self.desired_running = False
        self.server_mgr.stop()
        self.window_styler.set_resizable(self.server_mgr, enabled=True)
        self._refresh_visual_state()
        self._refresh_menu_state()

    def _exit(self):
        logging.info("Exiting tray")
        self.stop_event.set()
        self.desired_running = False
        self.server_mgr.stop()
        self.hotkeys.stop()
        self.control_center.shutdown()
        self._refresh_visual_state()
        self.icon.stop()

    def run(self):
        self.hotkey_active = self.hotkeys.start(lambda: self.capture_screenshot())
        if not self.hotkey_active:
            self.notify_user("Hotkey unavailable", "Ctrl+9 is already in use by another app.")
        else:
            self.notify_user("Hotkey active", "Use Ctrl+9 to save a snapshot.")

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
