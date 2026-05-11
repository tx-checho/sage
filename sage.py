"""
S.A.G.E. - Systemic Adaptive Guidance Engine
A desktop AI assistant with continuous screen awareness.

v1.3.0 — Added:
  • File system access — list dirs, read files, write files, delete, rename, copy
  • Installed app awareness — scans registry + Start Menu on startup
  • open action now resolves any installed app name to its real exe path
  • System context injected into every message (installed apps + current directory)
"""

import customtkinter as ctk
import threading
import time
import google.genai as genai
from google.genai import types
from PIL import Image, ImageGrab
import io
import json
import os
import sys
import re
import shutil
import subprocess
from datetime import datetime
import keyboard
import pystray
from pystray import MenuItem as TrayItem
import pyperclip
import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.3

# ── CONFIG ───────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "api_key": "",
        "screen_interval": 5,
        "model": "gemini-2.5-flash",
        "hotkey": "ctrl+space",
        "clipboard_awareness": True,
    }

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

config = load_config()

# ── THEME ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

BG        = "#0a0a0f"
BG_PANEL  = "#0f0f1a"
BG_INPUT  = "#12121f"
BG_CARD   = "#161625"
ACCENT    = "#00d4ff"
ACCENT2   = "#7b2fff"
ACCENT3   = "#ff4466"
TEXT      = "#e8e8f0"
TEXT_DIM  = "#5a5a7a"
USER_CLR  = "#00d4ff"
SAGE_CLR  = "#c084fc"
SYS_CLR   = "#3a3a5c"
ACT_CLR   = "#ff9944"

# ── APP SCANNER ───────────────────────────────────────────────────────────────
class AppScanner:
    """Scans Windows registry and Start Menu for installed applications."""

    def __init__(self):
        self.apps: dict = {}   # name.lower() -> exe path
        self._lock = threading.Lock()
        self._scan_done = False

    def scan(self):
        threading.Thread(target=self._full_scan, daemon=True).start()

    def _full_scan(self):
        found = {}
        found.update(self._scan_registry())
        found.update(self._scan_start_menu())
        with self._lock:
            self.apps = found
            self._scan_done = True

    def _scan_registry(self) -> dict:
        results = {}
        try:
            import winreg
            keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hive, path in keys:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                sub  = winreg.OpenKey(key, winreg.EnumKey(key, i))
                                name = self._reg_val(sub, "DisplayName")
                                exe  = self._reg_val(sub, "DisplayIcon") or \
                                       self._reg_val(sub, "InstallLocation")
                                if name and exe:
                                    exe = exe.split(",")[0].strip().strip('"')
                                    if exe.lower().endswith(".exe") and os.path.exists(exe):
                                        results[name.lower()] = exe
                                        short = name.split()[0].lower()
                                        if short not in results:
                                            results[short] = exe
                            except Exception:
                                pass
                except Exception:
                    pass
        except ImportError:
            pass
        return results

    def _reg_val(self, key, name: str) -> str:
        try:
            import winreg
            val, _ = winreg.QueryValueEx(key, name)
            return str(val)
        except Exception:
            return ""

    def _scan_start_menu(self) -> dict:
        results = {}
        dirs = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        ]
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        name   = os.path.splitext(f)[0].lower()
                        target = self._resolve_lnk(os.path.join(root, f))
                        if target and os.path.exists(target):
                            results[name] = target
                            short = name.split()[0]
                            if short not in results:
                                results[short] = target
        return results

    def _resolve_lnk(self, lnk_path: str) -> str:
        try:
            import win32com.client
            shell    = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(lnk_path)
            return shortcut.TargetPath
        except Exception:
            return ""

    def resolve(self, name: str) -> str:
        with self._lock:
            return self.apps.get(name.strip().lower(), "")

    def search(self, query: str) -> list:
        q = query.lower()
        with self._lock:
            return [(n, p) for n, p in self.apps.items() if q in n][:20]

    def summary(self) -> str:
        with self._lock:
            if not self._scan_done:
                return "App scan in progress..."
            count   = len(self.apps)
            notable = ["chrome","firefox","edge","steam","discord","spotify",
                       "vscode","code","notepad++","blender","obs","vlc","roblox","minecraft"]
            found   = [n for n in notable if n in self.apps]
            sample  = ", ".join(found[:12]) if found else "none detected"
            return f"{count} apps indexed. Notable: {sample}"

    def all_names(self) -> list:
        with self._lock:
            return sorted(self.apps.keys())


# ── FILE SYSTEM TOOLS ─────────────────────────────────────────────────────────
class FileSystem:

    @staticmethod
    def _expand(path: str) -> str:
        return os.path.expandvars(os.path.expanduser(path))

    @staticmethod
    def list_dir(path: str) -> str:
        try:
            p = FileSystem._expand(path)
            if not os.path.exists(p):
                return f"Path not found: {p}"
            entries = []
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if os.path.isdir(full):
                    entries.append(f"[DIR]  {name}")
                else:
                    entries.append(f"[FILE] {name}  ({FileSystem._fmt(os.path.getsize(full))})")
            return f"Contents of {p}:\n" + "\n".join(entries) if entries else f"{p} is empty."
        except Exception as e:
            return f"list_dir error: {e}"

    @staticmethod
    def read_file(path: str, max_chars: int = 8000) -> str:
        try:
            p = FileSystem._expand(path)
            if not os.path.exists(p):
                return f"File not found: {p}"
            sz = os.path.getsize(p)
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            trunc = " [truncated]" if sz > max_chars else ""
            return f"Contents of {p}{trunc}:\n\n{content}"
        except Exception as e:
            return f"read_file error: {e}"

    @staticmethod
    def write_file(path: str, content: str, mode: str = "w") -> str:
        try:
            p = FileSystem._expand(path)
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(p, mode, encoding="utf-8") as f:
                f.write(content)
            return f"Written: {p}"
        except Exception as e:
            return f"write_file error: {e}"

    @staticmethod
    def delete_file(path: str) -> str:
        try:
            p = FileSystem._expand(path)
            if os.path.isdir(p):
                shutil.rmtree(p); return f"Deleted directory: {p}"
            elif os.path.isfile(p):
                os.remove(p); return f"Deleted file: {p}"
            return f"Not found: {p}"
        except Exception as e:
            return f"delete error: {e}"

    @staticmethod
    def rename(src: str, dst: str) -> str:
        try:
            os.rename(FileSystem._expand(src), FileSystem._expand(dst))
            return f"Renamed: {src} -> {dst}"
        except Exception as e:
            return f"rename error: {e}"

    @staticmethod
    def copy_file(src: str, dst: str) -> str:
        try:
            s, d = FileSystem._expand(src), FileSystem._expand(dst)
            shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)
            return f"Copied: {src} -> {dst}"
        except Exception as e:
            return f"copy error: {e}"

    @staticmethod
    def make_dir(path: str) -> str:
        try:
            p = FileSystem._expand(path)
            os.makedirs(p, exist_ok=True)
            return f"Created: {p}"
        except Exception as e:
            return f"mkdir error: {e}"

    @staticmethod
    def search_files(directory: str, pattern: str) -> str:
        try:
            import fnmatch
            d = FileSystem._expand(directory)
            matches = []
            skip = {'node_modules', '__pycache__', '$Recycle.Bin', 'Windows', '.git'}
            for root, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x not in skip and not x.startswith('.')]
                for f in files:
                    if fnmatch.fnmatch(f.lower(), pattern.lower()):
                        matches.append(os.path.join(root, f))
                if len(matches) >= 50:
                    break
            if not matches:
                return f"No files matching '{pattern}' in {d}"
            return f"Found {len(matches)}:\n" + "\n".join(matches[:50])
        except Exception as e:
            return f"search error: {e}"

    @staticmethod
    def _fmt(b: int) -> str:
        for u in ("B","KB","MB","GB"):
            if b < 1024: return f"{b:.1f} {u}"
            b //= 1024
        return f"{b:.1f} TB"


# ── AGENT EXECUTOR ────────────────────────────────────────────────────────────
class AgentExecutor:

    ACTION_RE      = re.compile(r"```ACTIONS\s*([\s\S]*?)```", re.IGNORECASE)
    _app_scanner   = None   # injected at startup

    @staticmethod
    def extract_actions(response: str):
        match = AgentExecutor.ACTION_RE.search(response)
        if not match:
            return None, response
        raw   = match.group(1).strip()
        plain = AgentExecutor.ACTION_RE.sub("", response).strip()
        try:
            return json.loads(raw), plain
        except json.JSONDecodeError as e:
            return None, response + f"\n\nAction parse error: {e}"

    @staticmethod
    def execute_one(action: dict, scale: tuple = (1.0, 1.0)) -> str:
        act = action.get("action", "")
        sx, sy = scale

        if act == "click":
            x, y = int(action["x"]*sx), int(action["y"]*sy)
            pyautogui.click(x, y); return f"Clicked ({x},{y})"

        elif act == "move":
            x, y = int(action["x"]*sx), int(action["y"]*sy)
            pyautogui.moveTo(x, y, duration=0.2); return f"Moved ({x},{y})"

        elif act == "type":
            t = action.get("text","")
            pyautogui.typewrite(t, interval=0.04)
            return f"Typed: {t[:40]}{'...' if len(t)>40 else ''}"

        elif act == "hotkey":
            keys = action.get("keys","").split("+")
            pyautogui.hotkey(*keys); return f"Hotkey: {action.get('keys')}"

        elif act == "scroll":
            x = int(action.get("x",640)*sx); y = int(action.get("y",360)*sy)
            pyautogui.scroll(action.get("amount",-3), x=x, y=y)
            return f"Scrolled ({x},{y})"

        elif act == "open":
            return AgentExecutor.open_app(action.get("app",""))

        elif act == "list_dir":
            return FileSystem.list_dir(action.get("path","."))

        elif act == "read_file":
            return FileSystem.read_file(action.get("path",""))

        elif act == "write_file":
            return FileSystem.write_file(action.get("path",""), action.get("content",""), action.get("mode","w"))

        elif act == "delete_file":
            return FileSystem.delete_file(action.get("path",""))

        elif act == "rename":
            return FileSystem.rename(action.get("src",""), action.get("dst",""))

        elif act == "copy_file":
            return FileSystem.copy_file(action.get("src",""), action.get("dst",""))

        elif act == "make_dir":
            return FileSystem.make_dir(action.get("path",""))

        elif act == "search_files":
            return FileSystem.search_files(action.get("directory", os.path.expanduser("~")), action.get("pattern","*"))

        elif act == "find_app":
            q = action.get("query","")
            if AgentExecutor._app_scanner:
                res = AgentExecutor._app_scanner.search(q)
                return ("Apps found:\n" + "\n".join(f"{n}: {p}" for n,p in res)) if res else f"No apps matching '{q}'"
            return "App scanner unavailable"

        elif act == "wait":
            s = action.get("seconds",1); time.sleep(s); return f"Waited {s}s"

        elif act == "say":
            return f"✦ {action.get('text','')}"

        return f"Unknown action: {act}"

    @staticmethod
    def get_screen_scale() -> tuple:
        rw, rh = pyautogui.size()
        try:
            import ctypes
            u32 = ctypes.windll.user32
            u32.SetProcessDPIAware()
            return (u32.GetSystemMetrics(0)/1280, u32.GetSystemMetrics(1)/720)
        except Exception:
            return (rw/1280, rh/720)

    @staticmethod
    def open_app(name: str) -> str:
        # 1. App scanner
        if AgentExecutor._app_scanner:
            path = AgentExecutor._app_scanner.resolve(name)
            if path and os.path.exists(path):
                subprocess.Popen([path]); return f"Opened {name}: {path}"

        n = name.strip().lower()

        # 2. Browser hardcodes
        for browser, paths, cmd in [
            ("chrome",   [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                          r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                          os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")], "start chrome"),
            ("edge",     [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                          r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"], "start msedge"),
        ]:
            if n in (browser, f"google {browser}", f"microsoft {browser}"):
                for p in paths:
                    if os.path.exists(p):
                        subprocess.Popen([p]); return f"Opened {browser}"
                subprocess.Popen(cmd, shell=True); return f"Opened {browser} via shell"

        if n in ("firefox","mozilla firefox"):
            subprocess.Popen("start firefox", shell=True); return "Opened Firefox"

        # 3. System aliases
        aliases = {
            "notepad":"notepad.exe","calculator":"calc.exe","explorer":"explorer.exe",
            "file explorer":"explorer.exe","cmd":"cmd.exe","terminal":"cmd.exe",
            "powershell":"powershell.exe","task manager":"taskmgr.exe",
            "settings":"ms-settings:","paint":"mspaint.exe",
            "snipping tool":"snippingtool.exe","wordpad":"wordpad.exe",
        }
        if n in aliases:
            subprocess.Popen(aliases[n], shell=True); return f"Opened {name}"

        # 4. Shell fallback
        subprocess.Popen(name, shell=True); return f"Launched: {name}"


# ── PROMPTS ───────────────────────────────────────────────────────────────────
AGENT_PROMPT_ADDON = """

## Agentic capabilities

Emit a JSON ACTIONS block when the user wants you to DO something:

```ACTIONS
[
  {"action": "click",       "x": 452,  "y": 310,              "note": "Click target"},
  {"action": "type",        "text": "hello",                   "note": "Type text"},
  {"action": "hotkey",      "keys": "ctrl+c",                  "note": "Copy"},
  {"action": "scroll",      "x": 640,  "y": 400, "amount": -3, "note": "Scroll"},
  {"action": "move",        "x": 100,  "y": 200,              "note": "Move mouse"},
  {"action": "open",        "app": "chrome",                   "note": "Open app"},
  {"action": "wait",        "seconds": 1,                      "note": "Wait"},
  {"action": "list_dir",    "path": "C:/Users/ilove",          "note": "List folder"},
  {"action": "read_file",   "path": "C:/Dev/SAGE/sage.py",     "note": "Read file"},
  {"action": "write_file",  "path": "C:/note.txt", "content": "hi", "mode": "w", "note": "Write"},
  {"action": "delete_file", "path": "C:/trash.txt",            "note": "Delete"},
  {"action": "rename",      "src": "C:/old.txt", "dst": "C:/new.txt", "note": "Rename"},
  {"action": "copy_file",   "src": "C:/a.txt",   "dst": "C:/b.txt",   "note": "Copy"},
  {"action": "make_dir",    "path": "C:/NewFolder",            "note": "Make folder"},
  {"action": "search_files","directory": "C:/Users/ilove", "pattern": "*.py", "note": "Find files"},
  {"action": "find_app",    "query": "roblox",                 "note": "Find app path"},
  {"action": "say",         "text": "Done.",                   "note": "Report back"}
]
```

Rules:
- ACTIONS only when the user wants you to DO something. Questions/analysis = plain text.
- Always end with a "say" action.
- Coordinates are 1280x720 space, auto-scaled to real screen.
- For "open", use the app's common name — the system resolves to the real exe path from the app index.
- Paths support %USERPROFILE%, %APPDATA%, ~ etc.
- Actions run immediately. Be precise, don't do more than asked.
- Failsafe: mouse to top-left corner aborts execution."""

SYSTEM_PROMPT = """You are S.A.G.E. (Systemic Adaptive Guidance Engine) — a personal AI assistant.

You have continuous screen access via screenshots. Use what you see naturally.
Clipboard contents are included when relevant — only reference if clearly useful.

Personality: direct, intelligent, slightly dry wit. No fluff. Treat the user as capable.
Adapt tone to context. You're a tool with personality, not a therapist.

File system: you have full access. Use file actions to read, write, list, search files.
App index: all installed apps are indexed. Use "open" with common names — paths resolve automatically.
System context is injected with every message showing your current environment.

Keep responses concise. No markdown headers in casual chat.""" + AGENT_PROMPT_ADDON


# ── GEMINI CLIENT ─────────────────────────────────────────────────────────────
class GeminiClient:
    def __init__(self, app_scanner: AppScanner):
        self.client          = None
        self.history         = []
        self.last_screen     = None
        self.screen_lock     = threading.Lock()
        self._last_clipboard = ""
        self.app_scanner     = app_scanner
        self._init_model()

    def _init_model(self):
        if config.get("api_key"):
            try:
                self.client  = genai.Client(api_key=config["api_key"])
                self.history = []
            except Exception as e:
                self.client = None
                print(f"Gemini init error: {e}")

    def reinit(self): self._init_model()

    def capture_screen(self):
        try:
            shot = ImageGrab.grab().resize((1280, 720), Image.LANCZOS)
            with self.screen_lock:
                self.last_screen = shot
        except Exception as e:
            print(f"Screenshot error: {e}")

    def get_screen_image(self):
        with self.screen_lock:
            return self.last_screen

    def _image_to_bytes(self, img) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def get_clipboard(self) -> str:
        if not config.get("clipboard_awareness", True):
            return ""
        try:
            cur = pyperclip.paste()
            if cur and cur != self._last_clipboard and len(cur) < 4000:
                self._last_clipboard = cur
                return cur
        except Exception:
            pass
        return ""

    def _build_context(self) -> str:
        return "\n".join([
            f"[System — {datetime.now().strftime('%Y-%m-%d %H:%M')}]",
            f"CWD: {os.getcwd()}",
            f"Home: {os.path.expanduser('~')}",
            f"Apps: {self.app_scanner.summary()}",
        ])

    def send_message(self, message: str, include_clipboard: bool = True) -> str:
        if not self.client:
            return "No API key set. Go to Settings and enter your Gemini API key."
        try:
            parts = [types.Part(text=f"{self._build_context()}\n\n{message}")]

            if include_clipboard:
                clip = self.get_clipboard()
                if clip:
                    parts.append(types.Part(text=f"[Clipboard]:\n{clip}"))

            screen = self.get_screen_image()
            if screen:
                parts.append(types.Part(
                    inline_data=types.Blob(data=self._image_to_bytes(screen), mime_type="image/png")))

            self.history.append(types.Content(role="user", parts=parts))
            response = self.client.models.generate_content(
                model=config.get("model", "gemini-2.5-flash"),
                contents=self.history,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=4096,
                )
            )
            reply = response.text
            self.history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
            if len(self.history) > 40:
                self.history = self.history[-40:]
            return reply
        except Exception as e:
            return f"Error: {str(e)}"

    def send_action_result(self, summary: str) -> str:
        if not self.client:
            return ""
        self.capture_screen()
        return self.send_message(
            f"[Actions complete]\n{summary}\n\nCurrent screen shown. "
            "Did everything succeed? Fix anything that looks wrong with another ACTIONS block.",
            include_clipboard=False
        )

    def reset_chat(self): self.history = []


# ── SCREEN WATCHER ────────────────────────────────────────────────────────────
class ScreenWatcher:
    def __init__(self, client):
        self.client = client; self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self): self.running = False

    def _loop(self):
        while self.running:
            self.client.capture_screen()
            time.sleep(config.get("screen_interval", 5))


# ── FLOATING MINI-BAR ─────────────────────────────────────────────────────────
class FloatingBar(ctk.CTkToplevel):
    def __init__(self, master, on_submit):
        super().__init__(master)
        self.on_submit = on_submit
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)
        self.configure(fg_color=BG_PANEL)

        w, h = 620, 64
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{int(sh*0.38)}")

        border = ctk.CTkFrame(self, fg_color=ACCENT2, corner_radius=14)
        border.pack(fill="both", expand=True, padx=2, pady=2)
        inner  = ctk.CTkFrame(border, fg_color=BG_INPUT, corner_radius=12)
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        ctk.CTkLabel(inner, text="S·",
                     font=ctk.CTkFont(family="Courier New", size=15, weight="bold"),
                     text_color=ACCENT, width=28).pack(side="left", padx=(12,0))
        self.entry = ctk.CTkEntry(inner, placeholder_text="Ask SAGE...",
                                   fg_color="transparent", border_width=0,
                                   text_color=TEXT, placeholder_text_color=TEXT_DIM,
                                   font=ctk.CTkFont(size=14))
        self.entry.pack(side="left", fill="both", expand=True, padx=8)
        ctk.CTkLabel(inner, text="ESC to close",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM, width=72
                     ).pack(side="right", padx=12)

        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda e: self.close())
        self.bind("<Escape>",       lambda e: self.close())
        self.bind("<FocusOut>",     self._on_focus_out)
        self.after(100, self.entry.focus_force)

    def _on_focus_out(self, e):
        self.after(150, lambda: self.close() if not self.entry.focus_get() else None)

    def _submit(self, e=None):
        msg = self.entry.get().strip()
        if msg:
            self.close(); self.on_submit(msg)

    def close(self):
        try: self.destroy()
        except Exception: pass


# ── MAIN APP ──────────────────────────────────────────────────────────────────
class SAGEApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("S.A.G.E.")
        self.geometry("1000x700")
        self.minsize(800, 550)
        self.configure(fg_color=BG)

        self.app_scanner = AppScanner()
        self.app_scanner.scan()
        AgentExecutor._app_scanner = self.app_scanner

        self.client         = GeminiClient(self.app_scanner)
        self.watcher        = ScreenWatcher(self.client)
        self._agent_running = False
        self.watcher.start()

        self._build_ui()
        self._log_system("S.A.G.E. v1.3.0 online.")
        self._log_system("Screen | Agentic | File System | App Index — all active.")
        if not config.get("api_key"):
            self._log_system("No API key — open Settings to configure.")
        hk = config.get("hotkey", "ctrl+space")
        self._log_system(f"Hotkey: {hk.upper()}  |  Clipboard: {'ON' if config.get('clipboard_awareness',True) else 'OFF'}  |  Failsafe: top-left corner")
        self._log_system("Scanning installed apps in background...")

        self._register_hotkey()
        self._tray = None
        threading.Thread(target=self._start_tray, daemon=True).start()
        threading.Thread(target=self._wait_for_scan, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _wait_for_scan(self):
        while not self.app_scanner._scan_done:
            time.sleep(0.5)
        count = len(self.app_scanner.apps)
        self.after(0, lambda: self._log_system(f"App scan complete — {count} apps indexed."))

    # ── HOTKEY ───────────────────────────────────────────────────────────────
    def _register_hotkey(self):
        try:
            keyboard.add_hotkey(config.get("hotkey","ctrl+space"), self._invoke_bar, suppress=True)
        except Exception as e:
            self._log_system(f"Hotkey failed: {e}")

    def _unregister_hotkey(self):
        try: keyboard.clear_all_hotkeys()
        except Exception: pass

    def _invoke_bar(self):        self.after(0, self._show_floating_bar)
    def _show_floating_bar(self): FloatingBar(self, on_submit=self._bar_submit).focus_force()

    def _bar_submit(self, msg):
        self._show_window()
        self._log_user(msg)
        self.send_btn.configure(state="disabled", text="...")
        threading.Thread(target=self._get_response, args=(msg,), daemon=True).start()

    # ── TRAY ─────────────────────────────────────────────────────────────────
    def _start_tray(self):
        icon_img = Image.new("RGB", (64, 64), color="#7b2fff")
        self._tray = pystray.Icon("SAGE", icon_img, "S.A.G.E.", menu=pystray.Menu(
            TrayItem("Open SAGE",  self._tray_open, default=True),
            TrayItem("New Chat",   self._tray_new_chat),
            pystray.Menu.SEPARATOR,
            TrayItem("Quit",       self._tray_quit),
        ))
        self._tray.run()

    def _tray_open(self, *_):     self.after(0, self._show_window)
    def _tray_new_chat(self, *_): self.after(0, self._new_chat)
    def _tray_quit(self, *_):     self._quit_app()
    def _hide_to_tray(self):      self.withdraw()
    def _show_window(self):       self.deiconify(); self.lift(); self.focus_force()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.sidebar = ctk.CTkFrame(self, width=220, fg_color=BG_PANEL, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()
        self.main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_main()

    def _build_sidebar(self):
        lf = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        lf.pack(pady=(24,8), padx=16, fill="x")
        ctk.CTkLabel(lf, text="S.A.G.E.",
                     font=ctk.CTkFont(family="Courier New", size=22, weight="bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(lf, text="Systemic Adaptive\nGuidance Engine",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM, justify="left").pack(anchor="w")
        ctk.CTkFrame(self.sidebar, height=1, fg_color=SYS_CLR).pack(fill="x", padx=16, pady=12)

        def card(label, value, color):
            f = ctk.CTkFrame(self.sidebar, fg_color=BG_CARD, corner_radius=8)
            f.pack(padx=12, pady=3, fill="x")
            i = ctk.CTkFrame(f, fg_color="transparent")
            i.pack(padx=12, pady=8, fill="x")
            ctk.CTkLabel(i, text=label, font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_DIM).pack(anchor="w")
            ctk.CTkLabel(i, text=value, font=ctk.CTkFont(size=11, weight="bold"), text_color=color).pack(anchor="w")

        card("STATUS",       "● ACTIVE",      "#00ff88")
        card("SCREEN WATCH", "● CONTINUOUS",  ACCENT)
        card("FILE SYSTEM",  "● FULL ACCESS", ACCENT2)
        card("HOTKEY",       f"● {config.get('hotkey','ctrl+space').upper()}", "#ffcc00")

        # App index — updatable
        af = ctk.CTkFrame(self.sidebar, fg_color=BG_CARD, corner_radius=8)
        af.pack(padx=12, pady=3, fill="x")
        ai = ctk.CTkFrame(af, fg_color="transparent")
        ai.pack(padx=12, pady=8, fill="x")
        ctk.CTkLabel(ai, text="APP INDEX", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_DIM).pack(anchor="w")
        self.app_index_label = ctk.CTkLabel(ai, text="● SCANNING...",
                                             font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_DIM)
        self.app_index_label.pack(anchor="w")

        # Agent status — updatable
        ag = ctk.CTkFrame(self.sidebar, fg_color=BG_CARD, corner_radius=8)
        ag.pack(padx=12, pady=3, fill="x")
        agi = ctk.CTkFrame(ag, fg_color="transparent")
        agi.pack(padx=12, pady=8, fill="x")
        ctk.CTkLabel(agi, text="AGENT", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_DIM).pack(anchor="w")
        self.agent_status_label = ctk.CTkLabel(agi, text="● READY",
                                                font=ctk.CTkFont(size=11, weight="bold"), text_color=ACT_CLR)
        self.agent_status_label.pack(anchor="w")

        ctk.CTkFrame(self.sidebar, height=1, fg_color=SYS_CLR).pack(fill="x", padx=16, pady=12)

        bc = dict(corner_radius=8, height=36, font=ctk.CTkFont(size=12),
                  fg_color=BG_CARD, hover_color="#1e1e35", text_color=TEXT, anchor="w")
        ctk.CTkButton(self.sidebar, text="  ⟳  New Chat",  command=self._new_chat,      **bc).pack(padx=12, pady=2, fill="x")
        ctk.CTkButton(self.sidebar, text="  ⚙  Settings",  command=self._open_settings, **bc).pack(padx=12, pady=2, fill="x")
        ctk.CTkButton(self.sidebar, text="  📋  Clear Log", command=self._clear_chat,    **bc).pack(padx=12, pady=2, fill="x")
        ctk.CTkButton(self.sidebar, text="  🔍  App List",  command=self._show_app_list, **bc).pack(padx=12, pady=2, fill="x")
        ctk.CTkLabel(self.sidebar, text="v1.3.0", font=ctk.CTkFont(size=9),
                     text_color=TEXT_DIM).pack(side="bottom", pady=12)

    def _build_main(self):
        header = ctk.CTkFrame(self.main, height=52, fg_color=BG_PANEL, corner_radius=0)
        header.pack(fill="x"); header.pack_propagate(False)
        ctk.CTkLabel(header, text="CHAT INTERFACE",
                     font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(side="left", padx=20, pady=16)
        self.time_label = ctk.CTkLabel(header, text="",
                                        font=ctk.CTkFont(family="Courier New", size=11),
                                        text_color=TEXT_DIM)
        self.time_label.pack(side="right", padx=20)
        self._update_time()

        self.chat_box = ctk.CTkTextbox(
            self.main, fg_color=BG, text_color=TEXT,
            font=ctk.CTkFont(family="Courier New", size=13),
            wrap="word", corner_radius=0, border_width=0,
            scrollbar_button_color=BG_CARD, scrollbar_button_hover_color=SYS_CLR)
        self.chat_box.pack(fill="both", expand=True)
        self.chat_box.configure(state="disabled")
        for tag, color in [("user",USER_CLR),("sage",SAGE_CLR),("system",TEXT_DIM),
                            ("time",SYS_CLR),("body",TEXT),("action",ACT_CLR)]:
            self.chat_box.tag_config(tag, foreground=color)

        inp = ctk.CTkFrame(self.main, fg_color=BG_PANEL, corner_radius=0, height=80)
        inp.pack(fill="x"); inp.pack_propagate(False)
        ii = ctk.CTkFrame(inp, fg_color="transparent")
        ii.pack(fill="both", expand=True, padx=16, pady=12)

        self.input_box = ctk.CTkEntry(
            ii, placeholder_text="Ask SAGE anything...",
            fg_color=BG_INPUT, border_color=SYS_CLR, text_color=TEXT,
            placeholder_text_color=TEXT_DIM, font=ctk.CTkFont(size=13),
            corner_radius=8, height=44, border_width=1)
        self.input_box.pack(side="left", fill="both", expand=True, padx=(0,10))
        self.input_box.bind("<Return>", self._on_send)
        self.input_box.bind("<FocusIn>",  lambda e: self.input_box.configure(border_color=ACCENT))
        self.input_box.bind("<FocusOut>", lambda e: self.input_box.configure(border_color=SYS_CLR))

        self.send_btn = ctk.CTkButton(
            ii, text="SEND", command=self._on_send, width=80, height=44,
            fg_color=ACCENT2, hover_color="#6020df", text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8)
        self.send_btn.pack(side="right")

    # ── CHAT LOGIC ───────────────────────────────────────────────────────────
    def _on_send(self, event=None):
        msg = self.input_box.get().strip()
        if not msg: return
        self.input_box.delete(0, "end")
        self._log_user(msg)
        self.send_btn.configure(state="disabled", text="...")
        threading.Thread(target=self._get_response, args=(msg,), daemon=True).start()

    def _get_response(self, msg):
        response = self.client.send_message(msg)
        actions, plain = AgentExecutor.extract_actions(response)
        if actions:
            self.after(0, lambda: self._handle_action_response(actions, plain))
        else:
            self.after(0, lambda: self._log_sage(plain))
        self.after(0, lambda: self.send_btn.configure(state="normal", text="SEND"))

    def _handle_action_response(self, actions, plain_text):
        if plain_text:
            self._log_sage(plain_text)
        self._log_action(f"Executing {len(actions)} step(s)...")
        self._run_actions(actions)

    def _run_actions(self, actions):
        self._agent_running = True
        self._set_agent_status("● RUNNING", ACCENT3)
        threading.Thread(target=self._execute_actions, args=(actions,), daemon=True).start()

    def _execute_actions(self, actions):
        scale, results = AgentExecutor.get_screen_scale(), []
        try:
            for i, action in enumerate(actions, 1):
                desc = action.get("note", action.get("action","?"))
                self.after(0, lambda d=desc,n=i,t=len(actions): self._log_action(f"Step {n}/{t}: {d}"))
                result = AgentExecutor.execute_one(action, scale)
                results.append(result)
                # Echo file/app query results to chat
                if action.get("action") in ("list_dir","read_file","find_app","search_files"):
                    preview = result[:400] + ("..." if len(result)>400 else "")
                    self.after(0, lambda r=preview: self._log_action(r))
                self.client.capture_screen()
                time.sleep(0.2)

        except pyautogui.FailSafeException:
            self.after(0, lambda: self._log_action("Failsafe triggered — aborted."))
            self._agent_running = False
            self.after(0, lambda: self._set_agent_status("● ABORTED", ACCENT3))
            return
        except Exception as e:
            self.after(0, lambda: self._log_action(f"Execution error: {e}"))
            self._agent_running = False
            self.after(0, lambda: self._set_agent_status("● ERROR", ACCENT3))
            return

        self.after(0, lambda: self._log_action("Complete. Verifying..."))
        followup = self.client.send_action_result("\n".join(results))
        follow_actions, follow_plain = AgentExecutor.extract_actions(followup)
        self._agent_running = False
        self.after(0, lambda: self._set_agent_status("● READY", ACT_CLR))
        if follow_actions:
            self.after(0, lambda: self._handle_action_response(follow_actions, follow_plain))
        else:
            self.after(0, lambda: self._log_sage(follow_plain))

    def _set_agent_status(self, text, color):
        self.agent_status_label.configure(text=text, text_color=color)

    # ── APP LIST WINDOW ───────────────────────────────────────────────────────
    def _show_app_list(self):
        win = ctk.CTkToplevel(self)
        win.title("App Index"); win.geometry("600x500"); win.configure(fg_color=BG)
        ctk.CTkLabel(win, text="INDEXED APPS",
                     font=ctk.CTkFont(family="Courier New", size=14, weight="bold"),
                     text_color=ACCENT).pack(padx=20, pady=(20,4), anchor="w")
        sv = ctk.StringVar()
        ctk.CTkEntry(win, textvariable=sv, placeholder_text="Filter...",
                     fg_color=BG_INPUT, text_color=TEXT, border_color=SYS_CLR,
                     height=36).pack(padx=20, pady=(0,8), fill="x")
        box = ctk.CTkTextbox(win, fg_color=BG_CARD, text_color=TEXT,
                              font=ctk.CTkFont(family="Courier New", size=11), corner_radius=8)
        box.pack(padx=20, pady=(0,20), fill="both", expand=True)

        def refresh(*_):
            box.configure(state="normal"); box.delete("1.0","end")
            q = sv.get()
            if q:
                res = self.app_scanner.search(q)
                lines = [f"{n}\n  → {p}" for n,p in res]
            else:
                lines = self.app_scanner.all_names()[:300]
            box.insert("end", "\n".join(lines) or "Scan in progress...")
            box.configure(state="disabled")

        sv.trace_add("write", refresh); refresh()

    # ── LOGGING ──────────────────────────────────────────────────────────────
    def _log_user(self, msg):
        ts = datetime.now().strftime("%H:%M")
        self._append(f"\n[{ts}] ","time"); self._append("YOU   ","user"); self._append(f"{msg}\n","body")

    def _log_sage(self, msg):
        ts = datetime.now().strftime("%H:%M")
        self._append(f"\n[{ts}] ","time"); self._append("SAGE  ","sage"); self._append(f"{msg}\n","body")

    def _log_action(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"\n[{ts}] ","time"); self._append("ACT   ","action"); self._append(f"{msg}\n","body")

    def _log_system(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._append(f"\n[{ts}] SYS  {msg}\n","system")
        if "App scan complete" in msg:
            count = len(self.app_scanner.apps)
            self.app_index_label.configure(text=f"● {count} APPS", text_color="#00ff88")

    def _append(self, text, tag):
        self.chat_box.configure(state="normal")
        self.chat_box.insert("end", text, tag)
        self.chat_box.configure(state="disabled")
        self.chat_box.see("end")

    def _new_chat(self):
        self.client.reset_chat()
        self._log_system("Chat cleared. New session started.")

    def _clear_chat(self):
        self.chat_box.configure(state="normal")
        self.chat_box.delete("1.0","end")
        self.chat_box.configure(state="disabled")
        self._log_system("Display cleared.")

    # ── SETTINGS ─────────────────────────────────────────────────────────────
    def _open_settings(self):
        win = ctk.CTkToplevel(self)
        win.title("Settings"); win.geometry("480x520"); win.configure(fg_color=BG); win.grab_set()

        ctk.CTkLabel(win, text="SETTINGS",
                     font=ctk.CTkFont(family="Courier New", size=16, weight="bold"),
                     text_color=ACCENT).pack(padx=24, pady=(24,4), anchor="w")
        ctk.CTkFrame(win, height=1, fg_color=SYS_CLR).pack(fill="x", padx=24, pady=8)

        ctk.CTkLabel(win, text="Gemini API Key", font=ctk.CTkFont(size=12,weight="bold"),
                     text_color=TEXT_DIM).pack(padx=24, anchor="w")
        api_e = ctk.CTkEntry(win, placeholder_text="Enter API key...", fg_color=BG_INPUT,
                              text_color=TEXT, border_color=SYS_CLR, show="*", height=40,
                              font=ctk.CTkFont(size=12))
        api_e.pack(padx=24, pady=(4,16), fill="x")
        if config.get("api_key"): api_e.insert(0, config["api_key"])

        ctk.CTkLabel(win, text="Screen Capture Interval (seconds)",
                     font=ctk.CTkFont(size=12,weight="bold"), text_color=TEXT_DIM).pack(padx=24, anchor="w")
        iv = ctk.StringVar(value=str(config.get("screen_interval",5)))
        sl = ctk.CTkSlider(win, from_=2, to=30, number_of_steps=28,
                            button_color=ACCENT, progress_color=ACCENT2,
                            command=lambda v: iv.set(str(int(v))))
        sl.set(config.get("screen_interval",5)); sl.pack(padx=24, pady=(4,0), fill="x")
        ctk.CTkLabel(win, textvariable=iv, font=ctk.CTkFont(family="Courier New",size=11),
                     text_color=ACCENT).pack(padx=24, anchor="w", pady=(0,16))

        ctk.CTkLabel(win, text="Model", font=ctk.CTkFont(size=12,weight="bold"),
                     text_color=TEXT_DIM).pack(padx=24, anchor="w")
        mv = ctk.StringVar(value=config.get("model","gemini-2.5-flash"))
        ctk.CTkOptionMenu(win, values=["gemini-2.5-flash","gemini-2.5-pro","gemini-2.5-flash-lite"],
                          variable=mv, fg_color=BG_CARD, button_color=ACCENT2,
                          dropdown_fg_color=BG_PANEL).pack(padx=24, pady=(4,16), anchor="w")

        ctk.CTkLabel(win, text="Hotkey", font=ctk.CTkFont(size=12,weight="bold"),
                     text_color=TEXT_DIM).pack(padx=24, anchor="w")
        hke = ctk.CTkEntry(win, fg_color=BG_INPUT, text_color=TEXT, border_color=SYS_CLR,
                            height=40, font=ctk.CTkFont(family="Courier New",size=12))
        hke.insert(0, config.get("hotkey","ctrl+space"))
        hke.pack(padx=24, pady=(4,4), fill="x")
        ctk.CTkLabel(win, text="e.g.  ctrl+space  |  alt+g",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(padx=24, anchor="w", pady=(0,16))

        cv = ctk.BooleanVar(value=config.get("clipboard_awareness",True))
        ctk.CTkCheckBox(win, text="Clipboard awareness", variable=cv,
                        font=ctk.CTkFont(size=12), text_color=TEXT,
                        checkmark_color=BG, fg_color=ACCENT2, hover_color=ACCENT
                        ).pack(padx=24, pady=(0,20), anchor="w")

        def save():
            config["api_key"]             = api_e.get().strip()
            config["screen_interval"]     = int(float(iv.get()))
            config["model"]               = mv.get()
            config["hotkey"]              = hke.get().strip() or "ctrl+space"
            config["clipboard_awareness"] = cv.get()
            save_config(config)
            self._unregister_hotkey(); self._register_hotkey()
            self.client.reinit()
            self._log_system(f"Settings saved. Hotkey: {config['hotkey'].upper()}  |  Clipboard: {'ON' if config['clipboard_awareness'] else 'OFF'}")
            win.destroy()

        ctk.CTkButton(win, text="SAVE & APPLY", command=save, fg_color=ACCENT2,
                      hover_color="#6020df", font=ctk.CTkFont(size=13,weight="bold"),
                      height=40, corner_radius=8).pack(padx=24, fill="x")

    # ── UTILS ─────────────────────────────────────────────────────────────────
    def _update_time(self):
        self.time_label.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._update_time)

    def _quit_app(self):
        self.watcher.stop(); self._unregister_hotkey()
        if self._tray: self._tray.stop()
        self.destroy(); sys.exit(0)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = SAGEApp()
    app.mainloop()
