# AGENTS.md — General working instructions

This file is the single source of truth for how to work on this repository. Read it
before editing code, running the app, or preparing a release. It applies to humans
and AI assistants alike. Keep it tool-agnostic.

## 1. What this project is

A small Windows desktop utility that controls RGB lighting on the RK-H87 three-mode
mechanical keyboard. It exposes 21 built-in light modes plus brightness, animation
speed, sleep timer and per-colour selection through a self-drawn Tkinter UI with a
system-tray presence. There is deliberately no vendor SDK dependency.

The device layer (`rk87core`) talks to the keyboard over HIDAPI. The protocol was
reverse-engineered from the open-source Rangoli project (GPLv3), which derived it by
sniffing the official RK Software; the RK-H87RGB profile is `258a:00ef`.

Design intent: **instrument-grade, precise, restrained.**

## 2. Non-negotiable rules

### Device writes are conservative (safety)
- Only the `0x0A` lighting Feature report may ever be written. SINO WEALTH keyboard
  MCUs of this family can enter their ISP (firmware-flash) bootloader through a magic
  Feature report on a different report ID. Blind writes to unknown reports risk
  soft-bricking the keyboard. Never fuzz reports.
- The app starts in **DEMO mode** by default. Writing to real hardware requires an
  explicit "连接真机" click. Never auto-connect to hardware on startup.
- Do not test write operations against a real keyboard without explicit user consent —
  it changes their hardware lighting configuration. Use the mock transport for
  UI/regression work.

### Privacy — never commit these
- Personal names or handles in docs/comments.
- Machine-specific absolute paths (user home directories, workspace paths).
- Runtime config files (`%APPDATA%\RK87 Light\config.json`).
- Virtual environments, `build/`, or `dist/` — PyInstaller `.toc` files embed absolute
  machine paths (user home, AppData, Windows directories). Only the compiled `.exe`
  is attached to a GitHub release, never committed to the repo tree.
- Credentials, tokens, API keys of any kind (a PAT must stay in memory during a
  release, never written to a file or printed).
- Debug screenshots of the desktop, taskbar, tray neighbourhood, or logs that reveal
  personal applications.

### Functionality over decoration
All core controls (mode, brightness, speed, colour, apply) must stay reachable in one
step on the main surface. Visual changes must not break: taskbar button,
minimize-to-taskbar, close-to-tray, tray context menu, autostart toggle, or keyboard
shortcuts. Re-run interaction checks after any UI edit.

## 3. Architecture notes (learned the hard way)

### HID transport
- The H87 exposes TWO vendor-defined collections with `usage_page == 0xFF00`: Col03
  (GET_FEATURE returns `read error`) and Col05 (works). Always filter by `Col05` in the
  device path — opening Col03 or the standard boot-keyboard interface will fail or,
  worse, capture keystrokes.
- The lighting channel is a **65-byte Feature report**, report ID `0x0A`. GET_FEATURE
  on other reports (0x01..0x0F) returns read errors; length must be exactly 65.
- Windows hidapi treats `read(timeout_ms=0)` as an infinite block. Use a non-zero
  timeout / nonblocking mode so a stray read can never hang the calling thread.
- Device I/O runs in a worker thread with a busy flag; never block the UI thread.

### Lighting protocol (65-byte buffer, from Rangoli::setStandardLightBufferBetter)
```
[0]=0x0A  [1]=0x01  [2]=0x01  [3]=0x02  [4]=0x29  [5]=mode bit
[7]=animation speed (1..5)  [8]=brightness (0..5)
[9..11]=R,G,B (only when random_colours is off)
[12]=random-colour flag  [13]=sleep timer (1..5)
```
Mode bits: `NeonStream=1 ... FlashAway=21` (exact enum in `rk87core.py`, from
Rangoli `src/modemodel.h`). Chinese mode labels mirror the upstream UI order.
Battery is NOT readable over HID on this keyboard — `Fn+Enter` shows it on the
keyboard itself (digits 1-0 = 10%-100%), a firmware-internal behaviour. Do not add a
fake battery percentage.

### Window / taskbar (Windows 10 LTSC 19044 verified)
- A borderless (`overrideredirect`) Tk window gets **no taskbar button**, and
  `WS_EX_APPWINDOW` hacks do not fix it.
- Working pattern: an offscreen 1x1 `Tk` "proxy" owns the real taskbar button; a
  borderless `Toplevel` surface draws the UI. Bind the proxy's `<Map>`/`<Unmap>` to
  show/hide the surface. Custom minimize → `proxy.iconify()`; close → withdraw both;
  tray restore → `proxy.deiconify()` then sync.
- Transparent key is `#000001`; the background PNG fills its corners with that key.
  Never reuse that colour for real UI pixels.
- Drag uses **absolute screen coordinates** (`x_root/y_root` delta from press origin).
  Accumulating `winfo_x()+dx` makes the window accelerate away from the cursor.
- Right-hand header controls are laid out right-to-left with a 16px inset from the
  header edge. Keep that inset or the status pill overlaps the window buttons.

### UI rendering
- Rounded surfaces are antialiased images: PIL 4x supersample → LANCZOS →
  `ImageTk.PhotoImage`, cached by `(w,h,radius,fill,outline,width)`.
- All recoloring goes through `_set_rect()` / `_round_photo()`. Do **not** call
  `canvas.itemconfigure(item, fill=...)` on a rounded surface — it bypasses the cache
  and breaks antialiasing.
- A rounded surface carries its own single border. Do not add a separate highlight
  line on top of it (that reads as a duplicated border).

## 4. Common operations

```bash
# Run in demo mode (no hardware needed)
.venv/Scripts/python.exe rk87light.py

# Core module self-test (read-only device probe, no writes)
.venv/Scripts/python.exe rk87core.py

# Build the one-file executable
.venv/Scripts/python.exe -m PyInstaller RK87Light.spec --noconfirm
# -> dist/RK87Light.exe

# Regenerate assets after changing their generators
.venv/Scripts/python.exe make_assets.py
```

Dependencies: `hidapi`, `pystray`, `Pillow`, `pyinstaller` (build only). Python 3.10+
with tkinter.

## 5. Verification before calling anything done

1. Run `python rk87core.py` — must find `258A:00EF` on the `FF00` Col05 channel and
   read back 65 bytes beginning `01 01 01 01 01 00 00 00`.
2. Run `python rk87light.py` and confirm: rounded corners clean, header border
   continuous, status pill does not overlap window buttons, no duplicated panel
   borders, spacing even.
3. Exercise: mode click, brightness/speed nudge, colour picker, random toggle, apply
   (demo → local only), window drag, minimize→taskbar restore, close→tray→restore,
   tray submenus, autostart toggle. Check the event log reflects each action.
4. When iterating on screenshots, **confirm the window handle changed** after
   restarting — a stale instance silently invalidates before/after comparisons.
5. Do not claim write operations verified on real hardware unless you actually
   performed them with consent.

## 6. Release checklist

- [ ] Version tag `vX.Y.Z` chosen; README.md and AGENTS.md current.
- [ ] `python -m py_compile` on all `.py` files passes.
- [ ] Rebuild `dist/RK87Light.exe` from current source.
- [ ] Attach the exe to the GitHub release; write release notes from the changelog.
- [ ] Confirm no personal data, absolute paths, tokens, venv, build dirs, or debug
      screenshots are in the tree.
