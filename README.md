# S.A.G.E. — Systemic Adaptive Guidance Engine

> A desktop AI assistant that sees your screen, controls your computer, and stays out of the way until you need it.

Powered by Google Gemini. Built with Python.

---

## What it does

S.A.G.E. runs silently in your system tray, continuously watching your screen. Hit a hotkey from anywhere, type a prompt, and get a response that already knows what you're looking at — no copy-pasting context, no explaining your situation.

In agentic mode, it can go further: click, type, scroll, open apps, and chain actions together — all with your approval before anything runs.

---

## Features

- **Screen awareness** — captures your screen on an interval so every response has visual context
- **Global hotkey** — invoke a floating prompt bar from any app (`Ctrl+Space` by default)
- **Agentic mode** — SAGE can control your mouse, keyboard, and applications
- **Confirmation loop** — every action plan is shown to you before execution, step by step
- **Action verification** — after running, SAGE takes a fresh screenshot and checks its own work
- **Clipboard awareness** — optionally includes your clipboard contents as context
- **System tray** — runs in the background, closing the window doesn't kill it
- **Emergency abort** — move your mouse to the top-left corner of the screen at any time to stop an action sequence instantly

---

## Setup

### 1. Install Python
Download from [python.org](https://python.org) — check **"Add Python to PATH"** during install.

### 2. Get a Gemini API Key
Go to [aistudio.google.com](https://aistudio.google.com) → click **Get API key** → copy it.

### 3. Run SAGE
Double-click `START_SAGE.bat` — it installs dependencies automatically and launches the app.

On first launch, click **Settings** in the sidebar and paste your API key. Config is saved locally and never committed to the repo.

---

## Controls

| Action | Default |
| :--- | :--- |
| Invoke prompt bar | `Ctrl + Space` |
| Close prompt bar | `Esc` |
| Abort agent actions | Move mouse to top-left corner |

The hotkey is fully customizable in Settings.

---

## Models

| Model | Best for |
| :--- | :--- |
| `gemini-2.5-flash-lite` | Fast, lightweight, great for most tasks |
| `gemini-2.5-flash` | Balanced speed and reasoning |
| `gemini-2.5-pro` | Complex tasks, detailed visual analysis |

---

## File structure

```
sage/
├── sage.py            — core app
├── requirements.txt   — dependencies
├── START_SAGE.bat     — launcher
└── README.md
```

`config.json` is auto-created on first settings save and is gitignored — never committed.

---

## Dependencies

Installed automatically by the launcher:

```
customtkinter, Pillow, google-genai, keyboard, pystray, pyperclip, pyautogui
```

> **Note:** The `keyboard` library requires running as administrator on Windows for the global hotkey to work. Right-click `START_SAGE.bat` → Run as administrator if the hotkey doesn't fire.
