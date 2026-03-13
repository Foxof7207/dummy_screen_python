# Dummy Screen Python

A GUI tool for managing virtual and physical displays on Linux using `xrandr`, with integrated VNC support for remote access (e.g. connecting a tablet as a secondary screen).

---

## Features

- **Add virtual displays** – Create dummy/headless display outputs at custom resolutions and positions
- **Manage physical & virtual displays** – Enable, disable, or remove displays from a unified interface
- **VNC integration** – Start/stop `x11vnc` on any display for remote access, with optional clip regions
- **Display type detection** – Automatically identifies outputs as `(physical)` or `(virtual)` in the UI
- **Wallpaper re-application** – Fixes wallpaper artifacts after display changes across major desktop environments
- **Persistent settings** – Remembers your preferences across sessions (port, resolution, placement, confirmations)

---

## Requirements

- Python 3.8+
- `tkinter` (usually bundled with Python)
- `xrandr` and `cvt` — install via `sudo apt install x11-xserver-utils`
- `x11vnc` — install via `sudo apt install x11vnc`
- A running X11 session (`DISPLAY` environment variable must be set)

---

## Installation

No installation required. Just clone or download the script and run it directly.

```bash
git clone github.com/Foxof7207/dummy_screen_python
cd dummy_screen_python
python3 dummy_display.py
```

Or make it executable:

```bash
chmod +x dummy_display.py
./dummy_display.py
```

---

## Usage

Launch the application:

```bash
python3 dummy_display.py
```

The GUI has three tabs:

### Add Tab
Create a new virtual display by selecting:
- **Resolution** – Choose from common presets or enter a custom `WxH` value
- **Placement** – Position the new display relative to an existing one (left, right, above, below)

### Manage Tab
View all current display outputs with their status (`active`, `inactive`, `disconnected`) and type (`physical` / `virtual`). From here you can:
- Turn a display on or off
- Start or stop VNC on a specific display
- Forget (remove) a virtual display entirely

### Settings Tab
Configure persistent preferences:

| Setting | Description |
|---|---|
| VNC port | Port for `x11vnc` (default: `5900`) |
| X display | X server display string (default: `:0`) |
| Auto-start VNC | Automatically launch VNC when a display is added |
| Keep VNC alive (`-forever`) | Prevent VNC from exiting when clients disconnect |
| Shared VNC (`-shared`) | Allow multiple simultaneous VNC connections |
| Default resolution | Pre-selected resolution when adding a display |
| Default placement | Pre-selected position when adding a display |
| Confirmation dialogs | Toggle prompts for destructive actions |
| Re-apply wallpaper | Automatically fix wallpaper after display changes |

---

## Supported Desktop Environments (Wallpaper Re-application)

| Desktop Environment | Method Used |
|---|---|
| GNOME / Unity | `gsettings` |
| KDE Plasma | `qdbus` + PlasmaShell |
| XFCE | `xfdesktop --reload` |
| MATE | `gsettings` |
| Cinnamon | `gsettings` |
| Fallback | `nitrogen --restore` or `~/.fehbg` (feh) |

---

## Persistent Files

| File | Purpose |
|---|---|
| `~/.virtual_display` | Tracks which display outputs were created by this tool |
| `~/.display_manager_settings` | Stores your settings in JSON format |

---

## Notes

- Virtual displays are tracked across sessions. If a display is removed externally (e.g. system reboot), you can use the **Forget** button in the Manage tab to clean up the record.
- The VNC clip region feature allows you to share only the area of a display that matches a connected tablet or remote screen geometry.
- `x11vnc` must be installed even if you don't plan to use VNC — the prerequisite check will prevent the app from starting otherwise.

---

## License

GNU General Public License v3.0. See `LICENSE.txt` for details.

