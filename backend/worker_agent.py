"""
Download Monitor — Worker Agent (Real mode, local screenshots)
Uses Playwright for browser downloads, subprocess for CLI.
Screenshots saved locally and served via FastAPI static route.
"""
import os, time, subprocess, re, shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from models import DownloadOutcome

# Screenshots stored here — served by FastAPI at /screenshots/
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)


def screenshot_url(task_id: str, filename: str) -> str:
    """Return the URL path for a screenshot."""
    return f"/screenshots/{task_id}/{filename}"


def capture_active_window(save_path: str):
    """Capture only the active/foreground window, not the full desktop."""
    try:
        import ctypes
        from ctypes import wintypes
        import pyautogui

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        left = max(rect.left, 0)
        top = max(rect.top, 0)
        width = rect.right - rect.left
        height = rect.bottom - rect.top

        if width > 100 and height > 100:
            img = pyautogui.screenshot(region=(left, top, width, height))
            img.save(save_path)
        else:
            pyautogui.screenshot(save_path)
    except Exception:
        try:
            import pyautogui
            pyautogui.screenshot(save_path)
        except Exception:
            pass


def capture_window_by_pid(save_path: str, pid: int):
    """Capture a specific window by its process ID."""
    try:
        import ctypes
        from ctypes import wintypes
        import pyautogui

        user32 = ctypes.windll.user32

        # Callback to find window belonging to PID
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )

        target_hwnd = None

        def enum_callback(hwnd, lparam):
            nonlocal target_hwnd
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid and user32.IsWindowVisible(hwnd):
                target_hwnd = hwnd
                return False  # stop enumerating
            return True

        user32.EnumWindows(EnumWindowsProc(enum_callback), 0)

        if target_hwnd:
            # Bring window to front first
            user32.SetForegroundWindow(target_hwnd)
            import time
            time.sleep(0.3)

            rect = wintypes.RECT()
            user32.GetWindowRect(target_hwnd, ctypes.byref(rect))

            left = max(rect.left, 0)
            top = max(rect.top, 0)
            width = rect.right - rect.left
            height = rect.bottom - rect.top

            if width > 50 and height > 50:
                img = pyautogui.screenshot(region=(left, top, width, height))
                img.save(save_path)
                return

        # Fallback to foreground window
        capture_active_window(save_path)
    except Exception:
        try:
            import pyautogui
            pyautogui.screenshot(save_path)
        except Exception:
            pass


# ── Outcome classification based on page text / download status ──
BLOCK_PATTERNS = [
    (r"blocked", DownloadOutcome.BROWSER_BLOCKED),
    (r"dangerous download", DownloadOutcome.BROWSER_BLOCKED),
    (r"malware|virus|threat", DownloadOutcome.DEFENDER_BLOCKED),
    (r"Windows protected your PC", DownloadOutcome.SUCCESS_SMARTSCREEN),
    (r"SmartScreen", DownloadOutcome.SUCCESS_SMARTSCREEN),
    (r"not commonly downloaded", DownloadOutcome.BROWSER_WARNED_UNCOMMON),
    (r"might be dangerous|could harm|may be dangerous", DownloadOutcome.BROWSER_WARNED_DANGEROUS),
    (r"couldn.t download|failed", DownloadOutcome.DOWNLOAD_FAILED),
]


def classify_from_text(text: str) -> Optional[DownloadOutcome]:
    """Try to classify outcome from visible page/dialog text."""
    for pattern, outcome in BLOCK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return outcome
    return None


@dataclass
class DownloadResult:
    outcome: DownloadOutcome = DownloadOutcome.PENDING
    file_name: Optional[str] = None
    browser_message: Optional[str] = None
    defender_message: Optional[str] = None
    screenshot_url: Optional[str] = None
    http_status: Optional[int] = None
    error_details: Optional[str] = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        # Convert enum to string for DB storage
        if hasattr(d.get("outcome"), "value"):
            d["outcome"] = d["outcome"].value
        return d


class BrowserDownloader:
    """Download a file via a real browser using Playwright."""

    CHANNEL_MAP = {
        "edge": {"browser": "chromium", "channel": "msedge"},
        "chrome": {"browser": "chromium", "channel": "chrome"},
        "firefox": {"browser": "firefox", "channel": None},
    }

    def __init__(self, browser: str, task_id: str):
        self.browser_name = browser
        self.task_id = task_id
        self.result = DownloadResult()
        self.ss_dir = SCREENSHOT_DIR / task_id
        self.ss_dir.mkdir(parents=True, exist_ok=True)
        self.dl_dir = DOWNLOAD_DIR / task_id
        self.dl_dir.mkdir(parents=True, exist_ok=True)

    def _capture(self, page, step: str) -> str:
        """Capture the browser window only."""
        filename = f"{step}.png"
        path = str(self.ss_dir / filename)
        try:
            page.bring_to_front()
            time.sleep(0.3)
        except Exception:
            pass
        capture_active_window(path)
        return screenshot_url(self.task_id, filename)

    def _capture_desktop(self, step: str) -> str:
        """Full desktop capture for SmartScreen/Defender overlays."""
        filename = f"{step}_desktop.png"
        path = str(self.ss_dir / filename)
        try:
            import pyautogui
            pyautogui.screenshot(path)
        except Exception:
            pass
        return screenshot_url(self.task_id, filename)

    def execute(self, url: str) -> dict:
        cfg = self.CHANNEL_MAP.get(self.browser_name, self.CHANNEL_MAP["chrome"])

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            launcher = getattr(p, cfg["browser"])

            try:
                launch_args = {"headless": False}
                if cfg["channel"]:
                    launch_args["channel"] = cfg["channel"]

                browser = launcher.launch(**launch_args)
            except Exception as e:
                self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
                self.result.error_details = f"Browser launch failed: {e}"
                return self.result.to_dict()

            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            try:
                # Step 1: Trigger download
                # For direct file URLs, goto() throws because download starts
                # instead of page load — that's expected behavior
                self._capture(page, "01_before")

                download = None

                try:
                    with page.expect_download(timeout=60000) as dl_info:
                        try:
                            page.goto(url, wait_until="commit", timeout=30000)
                        except Exception:
                            pass  # Expected for direct download links
                    download = dl_info.value
                except Exception as e:
                    error_msg = str(e)
                    self.result.error_details = error_msg
                    ss_url = self._capture(page, "02_no_download")
                    self.result.screenshot_url = ss_url

                    try:
                        page_text = page.inner_text("body", timeout=3000)
                        classified = classify_from_text(page_text)
                        if classified:
                            self.result.outcome = classified
                            self.result.browser_message = page_text[:500]
                        else:
                            self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
                            self.result.browser_message = error_msg[:500]
                    except Exception:
                        self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED

                    browser.close()
                    return self.result.to_dict()

                # Step 2: Download triggered — capture multiple times
                time.sleep(2)
                self._capture(page, "02_download_bar")
                time.sleep(3)
                ss_url = self._capture(page, "03_downloading")

                self.result.file_name = download.suggested_filename

                # Check if download failed
                failure = download.failure()
                if failure:
                    self.result.error_details = failure
                    ss_url = self._capture(page, "04_download_failed")
                    self.result.screenshot_url = ss_url

                    # Try to classify from browser UI
                    try:
                        page_text = page.inner_text("body", timeout=3000)
                        classified = classify_from_text(page_text)
                        if classified:
                            self.result.outcome = classified
                            self.result.browser_message = page_text[:500]
                        else:
                            self.result.outcome = DownloadOutcome.BROWSER_BLOCKED
                            self.result.browser_message = failure
                    except Exception:
                        self.result.outcome = DownloadOutcome.BROWSER_BLOCKED
                        self.result.browser_message = failure

                    browser.close()
                    return self.result.to_dict()

                # Step 3: Download succeeded — save file
                save_path = str(self.dl_dir / download.suggested_filename)
                try:
                    download.save_as(save_path)
                except Exception as e:
                    self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
                    self.result.error_details = f"Save failed: {e}"
                    browser.close()
                    return self.result.to_dict()

                ss_url = self._capture(page, "04_downloaded")
                self.result.screenshot_url = ss_url

                # Step 4: Try to execute (for .exe / .msi)
                if save_path.endswith((".exe", ".msi")):
                    time.sleep(2)
                    exec_outcome = self._try_execute(page, save_path)
                    if exec_outcome:
                        self.result.outcome = exec_outcome
                    else:
                        self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED
                else:
                    self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED

                # Final screenshot
                self._capture(page, "05_final")

            except Exception as e:
                self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
                self.result.error_details = str(e)
                try:
                    self._capture(page, "99_error")
                except Exception:
                    pass

            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        return self.result.to_dict()

    def _try_execute(self, page, exe_path: str) -> Optional[DownloadOutcome]:
        """Run the downloaded file and check for SmartScreen/Defender."""
        try:
            proc = subprocess.Popen(
                [exe_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )

            # Wait for SmartScreen / Defender to appear
            time.sleep(6)

            # Capture desktop (SmartScreen is a system overlay, not in browser)
            desktop_url = self._capture_desktop("05_execution")

            # Check if desktop screenshot has SmartScreen/Defender text via OCR
            desktop_path = self.ss_dir / "05_execution_desktop.png"
            desktop_text = ""
            if desktop_path.exists():
                try:
                    import pytesseract
                    from PIL import Image
                    desktop_text = pytesseract.image_to_string(Image.open(str(desktop_path)))
                except ImportError:
                    # No OCR available — check if process is still alive
                    pass

            if desktop_text:
                classified = classify_from_text(desktop_text)
                if classified:
                    self.result.defender_message = desktop_text[:500]
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return classified

            # Check if file was deleted by Defender
            time.sleep(2)
            if not Path(exe_path).exists():
                self.result.defender_message = "File removed by Windows Defender"
                return DownloadOutcome.DEFENDER_BLOCKED

            # Process running = success (maybe with SmartScreen)
            try:
                proc.terminate()
            except Exception:
                pass

            return DownloadOutcome.SUCCESS_EXECUTED

        except Exception as e:
            self.result.error_details = f"Execution check: {e}"
            return None


class CLIDownloader:
    """Download via curl or PowerShell."""

    def __init__(self, method: str, task_id: str):
        self.method = method
        self.task_id = task_id
        self.result = DownloadResult()
        self.dl_dir = DOWNLOAD_DIR / task_id
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        self.ss_dir = SCREENSHOT_DIR / task_id
        self.ss_dir.mkdir(parents=True, exist_ok=True)

    def _cli_screenshot(self, step: str, pid: int = None) -> str:
        """Capture the CLI window by its process ID."""
        filename = f"{step}.png"
        path = str(self.ss_dir / filename)
        if pid:
            capture_window_by_pid(path, pid)
        else:
            capture_active_window(path)
        return screenshot_url(self.task_id, filename)

    def execute(self, url: str) -> dict:
        filename = url.split("/")[-1].split("?")[0] or "download.bin"
        save_path = str(self.dl_dir / filename)
        self.result.file_name = filename

        try:
            if self.method == "curl":
                # Write a batch script to avoid cmd /c flag parsing issues
                batch_path = str(self.dl_dir / "download.bat")
                with open(batch_path, "w") as f:
                    f.write(f'@echo off\n')
                    f.write(f'echo ==============================\n')
                    f.write(f'echo Downloading: {filename}\n')
                    f.write(f'echo URL: {url}\n')
                    f.write(f'echo ==============================\n')
                    f.write(f'curl.exe -L --insecure --ssl-no-revoke -o "{save_path}" --max-time 60 --progress-bar "{url}"\n')
                    f.write(f'echo.\n')
                    f.write(f'if exist "{save_path}" (\n')
                    f.write(f'  echo Download finished successfully.\n')
                    f.write(f') else (\n')
                    f.write(f'  echo Download FAILED.\n')
                    f.write(f')\n')
                    f.write(f'timeout /t 8 >nul\n')
                cmd = ["cmd", "/c", batch_path]
            else:  # powershell
                # Write a PS1 script to avoid escaping issues
                ps1_path = str(self.dl_dir / "download.ps1")
                with open(ps1_path, "w") as f:
                    f.write("Write-Host '==============================' -ForegroundColor Yellow\n")
                    f.write(f"Write-Host 'Downloading: {filename}' -ForegroundColor Cyan\n")
                    f.write(f"Write-Host 'URL: {url}' -ForegroundColor Cyan\n")
                    f.write("Write-Host '==============================' -ForegroundColor Yellow\n")
                    f.write("Write-Host ''\n")
                    f.write("[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12\n")
                    f.write("$ProgressPreference = 'Continue'\n")
                    f.write("try {\n")
                    f.write(f"  Invoke-WebRequest -Uri '{url}' -OutFile '{save_path}' -UseBasicParsing\n")
                    f.write("  Write-Host ''\n")
                    f.write("  Write-Host 'Download COMPLETE!' -ForegroundColor Green\n")
                    f.write(f"  Write-Host 'Saved to: {save_path}' -ForegroundColor Green\n")
                    f.write("} catch {\n")
                    f.write('  Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red\n')
                    f.write("}\n")
                    f.write("Write-Host ''\n")
                    f.write("Start-Sleep -Seconds 8\n")
                cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_path]

            # Launch in a VISIBLE console window
            proc = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

            # Take 3 screenshots: started, progress, completed
            time.sleep(1)
            self._cli_screenshot("01_started", proc.pid)

            # Wait for download, capture one mid-download screenshot
            elapsed = 0
            captured_mid = False
            while proc.poll() is None and elapsed < 90:
                time.sleep(2)
                elapsed += 2
                if not captured_mid and elapsed >= 4:
                    self._cli_screenshot("02_progress", proc.pid)
                    captured_mid = True

            if elapsed >= 90 and proc.poll() is None:
                proc.kill()
                self.result.outcome = DownloadOutcome.TIMEOUT
                self.result.error_details = f"{self.method} timed out"
                return self.result.to_dict()

            # Check if file exists and has content
            file_path = Path(save_path)

            # Take final screenshot before checking result
            self._cli_screenshot("03_completed", proc.pid)

            self.result.http_status = 200 if proc.returncode == 0 else 0

            if not file_path.exists() or file_path.stat().st_size == 0:
                self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
                if not file_path.exists():
                    self.result.error_details = f"{self.method}: file not found after download (exit code {proc.returncode})"
                else:
                    self.result.error_details = f"{self.method}: file is empty (0 bytes, exit code {proc.returncode})"
                return self.result.to_dict()

            self._cli_screenshot("02_downloaded")

            # Wait and check if Defender removes it
            time.sleep(3)
            if not file_path.exists():
                self.result.outcome = DownloadOutcome.DEFENDER_BLOCKED
                self.result.defender_message = "File removed by Defender after download"
                return self.result.to_dict()

            # For executables, try to run and check SmartScreen
            if save_path.endswith((".exe", ".msi")):
                try:
                    exec_proc = subprocess.Popen(
                        [save_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                    time.sleep(6)

                    # Check if file was deleted by Defender
                    if not file_path.exists():
                        self.result.outcome = DownloadOutcome.DEFENDER_BLOCKED
                        self.result.defender_message = "Defender removed after execution"
                    else:
                        self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED

                    try:
                        exec_proc.terminate()
                    except Exception:
                        pass

                    # Check if file still exists
                    if not file_path.exists():
                        self.result.outcome = DownloadOutcome.DEFENDER_BLOCKED
                        self.result.defender_message = "Defender removed after execution"
                    else:
                        self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED

                    try:
                        exec_proc.terminate()
                    except Exception:
                        pass

                except Exception as e:
                    self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED
                    self.result.error_details = f"Exec check: {e}"
            else:
                # Non-executable downloaded fine
                self.result.outcome = DownloadOutcome.SUCCESS_EXECUTED

        except subprocess.TimeoutExpired:
            self.result.outcome = DownloadOutcome.TIMEOUT
            self.result.error_details = f"{self.method} timed out"
            self._cli_screenshot("99_timeout")
        except Exception as e:
            self.result.outcome = DownloadOutcome.DOWNLOAD_FAILED
            self.result.error_details = str(e)

        return self.result.to_dict()