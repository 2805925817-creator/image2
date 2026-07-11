# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-window desktop app for generating images via an OpenAI-compatible image API (`images.generate`, e.g. `gpt-image-1` / `dall-e-3` through a proxy/"中转站" endpoint). Built with `pywebview`: `main.py` hosts a native window and exposes a Python `Api` object to the page's JS as `window.pywebview.api`; `index.html` is the entire frontend (HTML/CSS/JS inlined in one file, no build step, no framework).

## Running

```bash
pip install -r requirements.txt
python main.py
```

There is no test suite, linter, or build/bundle step in this repo — it's run directly with the system Python.

## Architecture

- **`main.py`** — the `Api` class is the only backend logic, exposed to JS via `js_api=api` in `webview.create_window`. Key methods called from the frontend:
  - `get_config()` / `save_config()` — read/write `config.json` (base_url, api_key, model) next to the script (or next to the exe when frozen via `sys.frozen`).
  - `generate(size)` — runs in a background thread (`threading.Thread`) so the webview UI never blocks. It does **not** receive the prompt as a call argument; instead it reads it back out of the page via `webview.windows[0].evaluate_js('window.__currentPrompt')`, to avoid pywebview issues with very long strings passed as JS-call params. Results/errors are pushed back into the page by calling `__onGenOk(b64)` / `__onGenError(msg)` through `evaluate_js`.
  - `save_image(b64_data)` — opens a native save dialog (`webview.windows[0].create_file_dialog`) and writes the decoded PNG.
  - Path resolution (`_get_config_path`, `_get_html_path`) branches on `sys.frozen` to support both `python main.py` and a PyInstaller-frozen exe (frozen HTML is expected at `sys._MEIPASS/index.html`).

- **`index.html`** — single-page UI, no external assets/CDN. Notable pieces:
  - Config modal writes to `config.json` via `save_config`; API key is never echoed back in full (`get_config` returns a masked key).
  - Generated images are appended as `.card` elements in `#mainArea`; each holds the prompt, a spinner while pending, then the image + download/copy actions once `__onGenOk` fires.
  - A custom lightbox (`#lightbox`) implements scroll-to-zoom (zoom centered on cursor position) and drag-to-pan with plain mouse events — not a library.
  - A sidebar (`#sidebar`) keeps an in-memory (non-persisted) history of generated images for the session; nothing is written to disk except via explicit "下载图片" (download).

- **`config.json`** — local, gitignore-worthy: contains `base_url`, `api_key`, `model` in plaintext. Treat as a secret file; don't print its contents or commit it.

- **`resources/background_clothing_conf/`** — currently just an empty `icon` folder; not wired into `main.py` or `index.html` yet.

## Conventions to follow when editing

- Keep the frontend as a single self-contained `index.html` (no build tooling has been introduced) unless asked to change that.
- New Python↔JS calls should follow the existing pattern: expose a method on `Api`, call it from JS as `window.pywebview.api.<method>(...)`, and for anything long-running, spawn a `threading.Thread` and push results back via `evaluate_js` callbacks rather than blocking the call or returning large payloads synchronously.
- UI strings are in Chinese (zh-CN); match that when adding new UI text.
