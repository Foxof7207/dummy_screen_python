#!/usr/bin/env python3
"""
GUI tool to add/enable or turn off displays using xrandr, with VNC support for remote access.
Tracks which displays were created by this script (virtual displays).
Shows (physical) or (virtual) next to each output in the Manage tab.
Allows starting x11vnc on the dummy display for tablet connection.
Requires: Python 3, tkinter, xrandr, x11vnc installed.
"""

import json
import os
import re
import shutil
import subprocess
import signal
from pathlib import Path
from typing import List, Set, Optional, Tuple
from dataclasses import dataclass

import tkinter as tk
from tkinter import messagebox, ttk


# Constants
VIRTUAL_DISPLAYS_FILE = Path.home() / ".virtual_display"
SETTINGS_FILE = Path.home() / ".display_manager_settings"
RESOLUTION_PATTERN = re.compile(r'^\d+x\d+$')
GEOMETRY_PATTERN = re.compile(r'\d+x\d+\+\d+\+\d+')

# Compiled regex patterns for display detection
VIRTUAL_PATTERNS = [
    re.compile(r'^VIRTUAL', re.IGNORECASE),
    re.compile(r'^DUMMY', re.IGNORECASE),
    re.compile(r'HEADLESS-', re.IGNORECASE),
]

PHYSICAL_PATTERNS = [
    re.compile(r'^HDMI', re.IGNORECASE),
    re.compile(r'^DP', re.IGNORECASE),
    re.compile(r'^eDP', re.IGNORECASE),
    re.compile(r'^VGA', re.IGNORECASE),
    re.compile(r'^DVI', re.IGNORECASE),
    re.compile(r'^LVDS', re.IGNORECASE),
    re.compile(r'^DisplayPort', re.IGNORECASE),
]

PLACEMENT_OPTIONS = {
    'left': '--left-of',
    'right': '--right-of',
    'above': '--above',
    'below': '--below'
}


@dataclass
class DisplayOutput:
    """Represents a display output."""
    name: str
    connected: bool
    active: bool
    
    @property
    def status(self) -> str:
        """Return human-readable status."""
        if self.active:
            return "active"
        elif self.connected:
            return "inactive (connected)"
        return "disconnected"


class VirtualDisplayTracker:
    """Manages tracking of virtual displays."""
    
    def __init__(self, filepath: Path = VIRTUAL_DISPLAYS_FILE):
        self.filepath = filepath
        self._displays: Set[str] = self._load()
    
    def _load(self) -> Set[str]:
        """Load virtual displays from file."""
        try:
            if self.filepath.exists():
                with open(self.filepath, 'r') as f:
                    return set(line.strip() for line in f if line.strip())
        except IOError as e:
            print(f"Warning: Could not load virtual displays file: {e}")
        return set()
    
    def save(self) -> None:
        """Save virtual displays to file."""
        try:
            with open(self.filepath, 'w') as f:
                for display in sorted(self._displays):
                    f.write(f"{display}\n")
        except IOError as e:
            print(f"Warning: Could not save virtual displays file: {e}")
    
    def add(self, display_name: str) -> None:
        """Add a display to tracking."""
        self._displays.add(display_name)
        self.save()
    
    def remove(self, display_name: str) -> None:
        """Remove a display from tracking."""
        self._displays.discard(display_name)
        self.save()
    
    def is_virtual(self, output_name: str) -> bool:
        """Check if a display is virtual."""
        if output_name in self._displays:
            return True
        
        # Check against virtual patterns
        if any(pattern.match(output_name) for pattern in VIRTUAL_PATTERNS):
            self.add(output_name)
            return True
        
        # Check if it's a known physical pattern
        if any(pattern.match(output_name) for pattern in PHYSICAL_PATTERNS):
            return False
        
        return False
    
    def __contains__(self, item: str) -> bool:
        """Allow 'in' operator."""
        return item in self._displays


class VNCManager:
    """Manages VNC server processes."""
    
    def __init__(self):
        self.vnc_processes: dict[str, subprocess.Popen] = {}
        self.vnc_clips: dict[str, str] = {}  # Store clip regions
    
    def start_vnc(self, display: str = ":0", port: int = 5900, clip: Optional[str] = None,
                  forever: bool = True, shared: bool = True) -> Tuple[bool, Optional[str]]:
        """Start x11vnc on the specified display."""
        if display in self.vnc_processes and self.vnc_processes[display].poll() is None:
            return False, "VNC already running on this display"
        
        cmd = ['x11vnc', '-display', display, '-rfbport', str(port)]
        if forever:
            cmd.append('-forever')
        if shared:
            cmd.append('-shared')
        if clip:
            cmd.extend(['-clip', clip])
            self.vnc_clips[display] = clip
        
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.vnc_processes[display] = proc
            clip_msg = f" (clipped to {clip})" if clip else ""
            return True, f"VNC started on port {port}{clip_msg}"
        except FileNotFoundError:
            return False, "x11vnc not found. Please install it."
        except Exception as e:
            return False, f"Failed to start VNC: {str(e)}"
    
    def stop_vnc(self, display: str = ":0") -> Tuple[bool, Optional[str]]:
        """Stop x11vnc on the specified display."""
        if display not in self.vnc_processes:
            return False, "No VNC process found for this display"
        
        proc = self.vnc_processes[display]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        
        del self.vnc_processes[display]
        if display in self.vnc_clips:
            del self.vnc_clips[display]
        return True, "VNC stopped"
    
    def is_running(self, display: str = ":0") -> bool:
        """Check if VNC is running on the display."""
        if display in self.vnc_processes:
            return self.vnc_processes[display].poll() is None
        return False
    
    def get_clip(self, display: str = ":0") -> Optional[str]:
        """Get the clip region for the display."""
        return self.vnc_clips.get(display)


class SettingsManager:
    """Manages persistent application settings."""

    DEFAULTS = {
        # Wallpaper
        "reapply_wallpaper": True,
        # VNC
        "vnc_port": 5900,
        "vnc_display": ":0",
        "vnc_auto_start": True,
        "vnc_forever": True,
        "vnc_shared": True,
        # Display defaults
        "default_resolution": "1920x1080",
        "default_placement": "left",
        # Confirmations
        "confirm_turn_off": True,
        "confirm_forget": True,
    }

    def __init__(self, filepath: Path = SETTINGS_FILE):
        self.filepath = filepath
        self._settings: dict = self._load()

    def _load(self) -> dict:
        """Load settings from file."""
        settings = dict(self.DEFAULTS)
        try:
            if self.filepath.exists():
                with open(self.filepath, 'r') as f:
                    stored = json.load(f)
                    settings.update(stored)
        except Exception as e:
            print(f"Warning: Could not load settings: {e}")
        return settings

    def save(self) -> None:
        """Save settings to file."""
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save settings: {e}")

    def get(self, key: str):
        """Get a setting value."""
        return self._settings.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        """Set a setting value and persist."""
        self._settings[key] = value
        self.save()


class WallpaperManager:
    """Detects and re-applies the desktop wallpaper to fix artifacts after display changes."""

    @staticmethod
    def detect_desktop_environment() -> Optional[str]:
        """Detect the current desktop environment."""
        de = os.environ.get('XDG_CURRENT_DESKTOP', '').upper()
        if de:
            if 'GNOME' in de or 'UNITY' in de:
                return 'gnome'
            if 'KDE' in de:
                return 'kde'
            if 'XFCE' in de:
                return 'xfce'
            if 'LXDE' in de or 'LXQT' in de:
                return 'lxde'
            if 'MATE' in de:
                return 'mate'
            if 'CINNAMON' in de:
                return 'cinnamon'

        session = os.environ.get('DESKTOP_SESSION', '').lower()
        if 'gnome' in session:
            return 'gnome'
        if 'kde' in session or 'plasma' in session:
            return 'kde'
        if 'xfce' in session:
            return 'xfce'
        if 'lxde' in session:
            return 'lxde'
        if 'mate' in session:
            return 'mate'
        if 'cinnamon' in session:
            return 'cinnamon'

        return None

    @staticmethod
    def reapply_wallpaper() -> Tuple[bool, str]:
        """
        Attempt to re-apply the current wallpaper.
        Tries several desktop environment methods, then common wallpaper tools.
        Returns (success, message).
        """
        de = WallpaperManager.detect_desktop_environment()

        # --- GNOME / Unity ---
        if de == 'gnome':
            try:
                uri_result = subprocess.run(
                    ['gsettings', 'get', 'org.gnome.desktop.background', 'picture-uri'],
                    capture_output=True, text=True, timeout=5
                )
                if uri_result.returncode == 0:
                    uri = uri_result.stdout.strip().strip("'")
                    subprocess.run(
                        ['gsettings', 'set', 'org.gnome.desktop.background', 'picture-uri', uri],
                        timeout=5
                    )
                    return True, "Wallpaper re-applied via gsettings (GNOME)."
            except Exception as e:
                pass  # Fall through to other methods

        # --- KDE Plasma ---
        if de == 'kde':
            try:
                script = (
                    "var allDesktops = desktops();"
                    "for (var i = 0; i < allDesktops.length; i++) {"
                    "  var d = allDesktops[i];"
                    "  d.wallpaperPlugin = d.wallpaperPlugin;"
                    "  var cfg = d.currentConfigGroup;"
                    "  d.currentConfigGroup = cfg;"
                    "}"
                )
                result = subprocess.run(
                    ['qdbus', 'org.kde.plasmashell', '/PlasmaShell',
                     'org.kde.PlasmaShell.evaluateScript', script],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return True, "Wallpaper re-applied via KDE PlasmaShell."
            except Exception:
                pass

        # --- XFCE ---
        if de == 'xfce':
            try:
                result = subprocess.run(['xfdesktop', '--reload'],
                                        capture_output=True, timeout=5)
                if result.returncode == 0:
                    return True, "Wallpaper re-applied via xfdesktop --reload."
            except Exception:
                pass

        # --- MATE ---
        if de == 'mate':
            try:
                uri_result = subprocess.run(
                    ['gsettings', 'get', 'org.mate.background', 'picture-filename'],
                    capture_output=True, text=True, timeout=5
                )
                if uri_result.returncode == 0:
                    uri = uri_result.stdout.strip().strip("'")
                    subprocess.run(
                        ['gsettings', 'set', 'org.mate.background', 'picture-filename', uri],
                        timeout=5
                    )
                    return True, "Wallpaper re-applied via gsettings (MATE)."
            except Exception:
                pass

        # --- Cinnamon ---
        if de == 'cinnamon':
            try:
                uri_result = subprocess.run(
                    ['gsettings', 'get', 'org.cinnamon.desktop.background', 'picture-uri'],
                    capture_output=True, text=True, timeout=5
                )
                if uri_result.returncode == 0:
                    uri = uri_result.stdout.strip().strip("'")
                    subprocess.run(
                        ['gsettings', 'set', 'org.cinnamon.desktop.background', 'picture-uri', uri],
                        timeout=5
                    )
                    return True, "Wallpaper re-applied via gsettings (Cinnamon)."
            except Exception:
                pass

        # --- Fallback: nitrogen ---
        if shutil.which('nitrogen'):
            try:
                result = subprocess.run(['nitrogen', '--restore'],
                                        capture_output=True, timeout=5)
                if result.returncode == 0:
                    return True, "Wallpaper re-applied via nitrogen --restore."
            except Exception:
                pass

        # --- Fallback: feh ---
        feh_bg = Path.home() / '.fehbg'
        if feh_bg.exists() and shutil.which('feh'):
            try:
                result = subprocess.run(['bash', str(feh_bg)],
                                        capture_output=True, timeout=5)
                if result.returncode == 0:
                    return True, "Wallpaper re-applied via ~/.fehbg (feh)."
            except Exception:
                pass

        return False, (
            "Could not detect a supported method to re-apply the wallpaper.\n"
            f"Detected DE: {de or 'unknown'}.\n"
            "Supported: GNOME, KDE, XFCE, MATE, Cinnamon, nitrogen, feh."
        )


class XrandrInterface:
    """Interface for xrandr command execution and parsing."""
    
    @staticmethod
    def check_prerequisites() -> Tuple[bool, Optional[str]]:
        """Check if X11, xrandr, and x11vnc are available.
        Returns (success, error_message)
        """
        if 'DISPLAY' not in os.environ:
            return False, "DISPLAY environment variable not set. Is X11 running?"
        
        if shutil.which('xrandr') is None:
            return False, "xrandr not found. Please install it (e.g., 'sudo apt install x11-xserver-utils')."
        
        if shutil.which('cvt') is None:
            return False, "cvt not found. Please install it (e.g., 'sudo apt install x11-xserver-utils')."
        
        if shutil.which('x11vnc') is None:
            return False, "x11vnc not found. Please install it (e.g., 'sudo apt install x11vnc')."
        
        try:
            subprocess.run(['xrandr', '--version'], 
                         capture_output=True, 
                         check=True, 
                         timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False, "xrandr command failed. Is X11 running?"
        
        return True, None
    
    @staticmethod
    def get_outputs() -> Optional[List[DisplayOutput]]:
        """Parse xrandr output and return list of DisplayOutput objects."""
        try:
            result = subprocess.run(['xrandr'], 
                                  capture_output=True, 
                                  text=True, 
                                  check=True,
                                  timeout=10)
            return XrandrInterface._parse_xrandr_output(result.stdout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"Error running xrandr: {e}")
            return None
    
    @staticmethod
    def _parse_xrandr_output(output: str) -> List[DisplayOutput]:
        """Parse xrandr output text."""
        displays = []
        lines = output.splitlines()
        i = 0
        
        while i < len(lines):
            line = lines[i]
            # Detect output line (starts with non-space, not "Screen")
            if line and line[0] not in (' ', '\t') and not line.startswith('Screen'):
                parts = line.split()
                if parts:
                    name = parts[0]
                    connected = ' connected' in line
                    active = bool(GEOMETRY_PATTERN.search(line))
                    
                    # Check subsequent indented lines for active modes
                    if not active and connected:
                        j = i + 1
                        while j < len(lines) and lines[j] and lines[j][0] in (' ', '\t'):
                            if '*' in lines[j]:
                                active = True
                                break
                            j += 1
                    
                    displays.append(DisplayOutput(name, connected, active))
            i += 1
        
        return displays
    
    @staticmethod
    def get_existing_modes(output: str) -> List[str]:
        """Return list of existing mode names for a given output (if connected)."""
        modes = []
        try:
            result = subprocess.run(['xrandr'], capture_output=True, text=True, check=True)
            lines = result.stdout.splitlines()
            in_output = False
            for line in lines:
                if line.startswith(output):
                    in_output = True
                elif in_output and line.startswith(' '):
                    # Look for mode names (first word after spaces)
                    parts = line.strip().split()
                    if parts:
                        mode_name = parts[0]
                        # Remove any trailing flags like '+'
                        mode_name = re.sub(r'[^a-zA-Z0-9_.]', '', mode_name)
                        modes.append(mode_name)
                elif in_output and not line.startswith(' '):
                    break
        except:
            pass
        return modes
    
    @staticmethod
    def _create_mode_with_name(resolution: str, mode_name: str) -> Tuple[bool, Optional[str]]:
        """
        Create a new mode with the given name using cvt-generated timings.
        Tries without sync-polarity flags first (avoids BadName on Intel/modesetting
        drivers), then retries with them as a fallback.
        Returns (success, error_message).
        """
        try:
            res_parts = resolution.split('x')
            if len(res_parts) != 2:
                return False, "Invalid resolution format"

            # Generate modeline with cvt
            cvt_result = subprocess.run(['cvt', res_parts[0], res_parts[1]],
                                       capture_output=True, text=True, check=True)

            # Parse the modeline to extract the timing parameters
            for line in cvt_result.stdout.splitlines():
                if "Modeline" in line:
                    tokens = line.split()
                    # tokens[0]="Modeline", tokens[1]=quoted name, tokens[2:]=params
                    all_params = tokens[2:]

                    # Separate numeric timing params from sync-polarity flags
                    # (-hsync, +hsync, -vsync, +vsync).  Some drivers (Intel
                    # modesetting) return BadName when sync flags are present.
                    SYNC_FLAGS = {'-hsync', '+hsync', '-vsync', '+vsync'}
                    numeric_params = [p for p in all_params if p not in SYNC_FLAGS]
                    sync_params    = [p for p in all_params if p in SYNC_FLAGS]

                    # Attempt 1: without sync flags (most compatible)
                    # Attempt 2: with sync flags (fallback for older servers)
                    attempts = [numeric_params]
                    if sync_params:
                        attempts.append(all_params)

                    last_err = None
                    for params in attempts:
                        cmd = ['xrandr', '--newmode', mode_name] + params
                        print(f"Running: {' '.join(cmd)}")
                        try:
                            subprocess.run(cmd, check=True, capture_output=True, text=True)
                            return True, None
                        except subprocess.CalledProcessError as e:
                            stderr = e.stderr or ''
                            if 'already exists' in stderr:
                                return True, None
                            last_err = f"Error creating mode: {stderr}"

                    return False, last_err or "Unknown error creating mode"

            return False, "Could not generate modeline from cvt"
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            return False, f"Error creating mode: {getattr(e, 'stderr', str(e))}"

    @staticmethod
    def _get_global_mode_for_resolution(resolution: str) -> Optional[str]:
        """
        Search xrandr's global mode pool (listed under 'xrandr --verbose') for any
        mode whose name starts with the requested resolution string.
        Returns the mode name if found, else None.
        """
        try:
            result = subprocess.run(['xrandr', '--verbose'],
                                    capture_output=True, text=True, check=True, timeout=10)
            for line in result.stdout.splitlines():
                # Global modes appear as lines not indented that start with resolution
                stripped = line.strip()
                if stripped.startswith(resolution):
                    name = stripped.split()[0]
                    return name
        except Exception:
            pass
        return None

    @staticmethod
    def create_mode(resolution: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Try multiple strategies to ensure a usable mode exists:
        1. dummy_<resolution>  (e.g., dummy_1920x1080)
        2. cvt-generated name  (e.g., 1920x1080_60.00)
        3. plain resolution    (e.g., 1920x1080)
        4. Reuse any existing global mode matching the resolution (no --newmode needed)

        Each name is tried without sync flags first, then with them, to work around
        the BadName / RRCreateMode error seen on Intel modesetting drivers.

        Returns (success, error_message, used_mode_name).
        """
        # --- Strategy 0: reuse an already-known global mode (costs nothing) ---
        existing = XrandrInterface._get_global_mode_for_resolution(resolution)
        if existing:
            print(f"Reusing existing global mode: {existing}")
            return True, None, existing

        # --- Strategies 1-3: create a new mode under various names ---
        candidates = [f"dummy_{resolution}"]

        # Add cvt-generated name if we can extract it
        try:
            res_parts = resolution.split('x')
            cvt_result = subprocess.run(['cvt', res_parts[0], res_parts[1]],
                                       capture_output=True, text=True, check=True)
            for line in cvt_result.stdout.splitlines():
                if "Modeline" in line:
                    tokens = line.split()
                    cvt_name = tokens[1].strip('"')
                    candidates.append(cvt_name)
                    break
        except Exception:
            pass

        candidates.append(resolution)  # last resort plain name

        last_error = None
        for name in candidates:
            success, err = XrandrInterface._create_mode_with_name(resolution, name)
            if success:
                return True, None, name
            else:
                last_error = err
                print(f"Failed to create mode with name '{name}': {err}")

        # --- Strategy 4: after creation attempts, re-check global pool ---
        # (The --newmode call may have succeeded without raising an error even if
        # the CalledProcessError path was taken on some drivers.)
        existing = XrandrInterface._get_global_mode_for_resolution(resolution)
        if existing:
            print(f"Found mode in global pool after creation attempts: {existing}")
            return True, None, existing

        return False, (
            f"All mode-creation attempts failed. Last error: {last_error}\n\n"
            "Your graphics driver may not support custom mode creation via xrandr.\n"
            "Possible fixes:\n"
            "  • Use the 'xrandr-output-manager' / xorg dummy driver instead.\n"
            "  • Add 'Option \"VirtualSize\" \"3840x2160\"' to xorg.conf.\n"
            "  • Load the dummy kernel module: sudo modprobe dummy_hcd (or xf86-video-dummy).\n"
            "  • Use an existing mode shown in 'xrandr' output for this output."
        ), None

    @staticmethod
    def add_mode(output: str, resolution: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Ensure a mode exists (creating it if necessary) and add it to the output.
        Returns (success, error_message, mode_name_used).
        """
        # First, create the mode (or ensure it exists)
        success, err, mode_name = XrandrInterface.create_mode(resolution)
        if not success:
            return False, err, None
        if mode_name is None:
            return False, "No mode name available", None

        try:
            cmd = ['xrandr', '--addmode', output, mode_name]
            print(f"Running: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)
            return True, None, mode_name
        except subprocess.CalledProcessError as e:
            return False, f"Failed to add mode: {e.stderr}", mode_name
        except subprocess.TimeoutExpired:
            return False, "Command timed out", mode_name
    
    @staticmethod
    def enable_output(output: str, mode_name: str, placement: str, relative_to: str) -> Tuple[bool, Optional[str]]:
        """Enable an output with specified settings."""
        placement_arg = PLACEMENT_OPTIONS.get(placement)
        if not placement_arg:
            return False, f"Invalid placement: {placement}"
        
        cmd = ['xrandr', '--output', output, '--mode', mode_name, placement_arg, relative_to]
        print(f"Running: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)
            return True, None
        except subprocess.CalledProcessError as e:
            return False, f"Failed to enable output: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
    
    @staticmethod
    def turn_off_output(output: str) -> Tuple[bool, Optional[str]]:
        """Turn off a display output."""
        cmd = ['xrandr', '--output', output, '--off']
        print(f"Running: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)
            return True, None
        except subprocess.CalledProcessError as e:
            return False, f"Failed to turn off output: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
    
    @staticmethod
    def get_output_geometry(output: str) -> Optional[Tuple[int, int, int, int]]:
        """Get the geometry (x, y, width, height) of an output."""
        try:
            result = subprocess.run(['xrandr'], 
                                  capture_output=True, 
                                  text=True, 
                                  check=True,
                                  timeout=10)
            lines = result.stdout.splitlines()
            for i, line in enumerate(lines):
                if line.startswith(output):
                    # Find the geometry in the line or next lines
                    if '+' in line:
                        # Parse geometry from the line
                        parts = line.split()
                        for part in parts:
                            if '+' in part and 'x' in part:
                                # e.g., 1920x1080+0+0
                                geom = part
                                break
                        else:
                            continue
                        width, rest = geom.split('x')
                        height, x, y = rest.split('+')
                        return int(x), int(y), int(width), int(height)
                    # Check next lines for active mode
                    for j in range(i+1, len(lines)):
                        next_line = lines[j]
                        if next_line.startswith(' '):
                            if '*' in next_line:
                                # Parse geometry
                                parts = next_line.split()
                                for part in parts:
                                    if '+' in part:
                                        geom = part
                                        width, rest = geom.split('x')
                                        height, x, y = rest.split('+')
                                        return int(x), int(y), int(width), int(height)
                        else:
                            break
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None


class XrandrGUI(tk.Tk):
    """Main GUI application for display management with VNC support."""
    
    def __init__(self):
        super().__init__()
        self.title("Display Manager with VNC")
        self.geometry("700x650")
        self.resizable(False, False)
        
        # Initialize managers
        self.tracker = VirtualDisplayTracker()
        self.xrandr = XrandrInterface()
        self.vnc = VNCManager()
        self.settings = SettingsManager()
        
        # Data storage
        self.all_outputs: List[DisplayOutput] = []
        
        # Load initial data
        if not self._refresh_data():
            messagebox.showerror("Error", "Failed to load display information")
            self.destroy()
            raise SystemExit(1)
        
        # Create UI
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the main UI structure."""
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.add_frame = ttk.Frame(self.notebook)
        self.manage_frame = ttk.Frame(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.add_frame, text="➕  Add Display")
        self.notebook.add(self.manage_frame, text="🖥  Manage Displays")
        self.notebook.add(self.settings_frame, text="⚙  Settings")

        self._build_add_tab()
        self._build_manage_tab()
        self._build_settings_tab()

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _make_scrollable_frame(self, parent: ttk.Frame) -> ttk.Frame:
        """Create a vertically-scrollable frame inside *parent* and return the inner frame."""
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return inner

    def _activate_output_with_vnc(self, port: str, mode_name: str,
                                   placement: str, main: str) -> None:
        """Enable *port* with *mode_name* then optionally start VNC.  Shows result dialogs."""
        success, error = self.xrandr.enable_output(port, mode_name, placement, main)
        if not success:
            messagebox.showerror("Enable Output Failed", error)
            return

        geom = self.xrandr.get_output_geometry(port)
        clip = f"{geom[2]}x{geom[3]}+{geom[0]}+{geom[1]}" if geom else None

        self.tracker.add(port)

        if self.settings.get("vnc_auto_start"):
            vnc_ok, vnc_msg = self.vnc.start_vnc(
                display=self.settings.get("vnc_display"),
                port=self.settings.get("vnc_port"),
                clip=clip,
                forever=self.settings.get("vnc_forever"),
                shared=self.settings.get("vnc_shared"),
            )
            if not vnc_ok:
                messagebox.showwarning(
                    "VNC Warning",
                    f"Display enabled, but VNC failed to start:\n{vnc_msg}"
                )
            else:
                messagebox.showinfo(
                    "Display Enabled",
                    f"✓ {port} is now active at {mode_name}, {placement} of {main}.\n"
                    f"VNC: {vnc_msg}\n"
                    f"Connect via VNC on port {self.settings.get('vnc_port')}."
                )
        else:
            messagebox.showinfo(
                "Display Enabled",
                f"✓ {port} is now active at {mode_name}, {placement} of {main}.\n"
                "VNC auto-start is off — use the Manage tab to start VNC manually."
            )

        self._refresh_all()
        self._maybe_reapply_wallpaper()
    
    def _refresh_data(self) -> bool:
        """Refresh display data from xrandr."""
        outputs = self.xrandr.get_outputs()
        if outputs is None:
            return False
        
        self.all_outputs = outputs
        
        # Update tracker with any detected virtual displays
        for output in self.all_outputs:
            if output.connected:
                self.tracker.is_virtual(output.name)
        
        return True
    
    @property
    def active_outputs(self) -> List[DisplayOutput]:
        """Get list of active outputs."""
        return [o for o in self.all_outputs if o.connected and o.active]
    
    @property
    def inactive_outputs(self) -> List[DisplayOutput]:
        """Get list of inactive but connected outputs."""
        return [o for o in self.all_outputs if o.connected and not o.active]
    
    @property
    def disconnected_outputs(self) -> List[DisplayOutput]:
        """Get list of disconnected outputs (can be used as dummy displays)."""
        return [o for o in self.all_outputs if not o.connected]
    
    @property
    def available_outputs(self) -> List[DisplayOutput]:
        """Get list of outputs that can be enabled (inactive + disconnected)."""
        return self.inactive_outputs + self.disconnected_outputs
    
    @property
    def virtual_outputs(self) -> List[DisplayOutput]:
        """Get list of all outputs marked as virtual (any state)."""
        return [o for o in self.all_outputs if self.tracker.is_virtual(o.name)]
    
    def _build_add_tab(self) -> None:
        """Build the Add Virtual Display tab."""
        for widget in self.add_frame.winfo_children():
            widget.destroy()

        main_frame = ttk.Frame(self.add_frame, padding="12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Output ────────────────────────────────────────────────────────────
        out_card = ttk.LabelFrame(main_frame, text="  Output to enable  ", padding="8")
        out_card.pack(fill=tk.X, pady=(0, 8))

        available = self.available_outputs
        if not available:
            ttk.Label(out_card, text="No available outputs (all are already active).").pack(anchor=tk.W)
            self.add_port_var = tk.StringVar()
        else:
            self.add_port_var = tk.StringVar(value=available[0].name)
            for output in available:
                if self.tracker.is_virtual(output.name):
                    tag = "virtual"
                elif output.connected:
                    tag = "inactive"
                else:
                    tag = "disconnected / dummy"
                ttk.Radiobutton(
                    out_card,
                    text=f"{output.name}  [{tag}]",
                    variable=self.add_port_var,
                    value=output.name,
                ).pack(anchor=tk.W, padx=4, pady=1)

        # ── Resolution ────────────────────────────────────────────────────────
        res_card = ttk.LabelFrame(main_frame, text="  Resolution  ", padding="8")
        res_card.pack(fill=tk.X, pady=(0, 8))

        COMMON_RESOLUTIONS = [
            "1920x1080", "2560x1440", "3840x2160",
            "1280x720",  "1280x800",  "1366x768",
            "1600x900",  "1440x900",  "1024x768",
        ]
        res_row = ttk.Frame(res_card)
        res_row.pack(fill=tk.X)
        self.resolution_var = tk.StringVar(value=self.settings.get("default_resolution"))
        ttk.Combobox(res_row, textvariable=self.resolution_var,
                     values=COMMON_RESOLUTIONS, width=14).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(res_row, text="Test Mode", command=self._test_mode).pack(side=tk.LEFT)
        ttk.Label(res_card, text="Custom values are accepted (e.g. 1920x1080).",
                  foreground="#666", font=('', 8)).pack(anchor=tk.W, pady=(4, 0))

        # ── Placement ─────────────────────────────────────────────────────────
        place_card = ttk.LabelFrame(main_frame, text="  Placement relative to main screen  ", padding="8")
        place_card.pack(fill=tk.X, pady=(0, 8))

        self.placement_var = tk.StringVar(value=self.settings.get("default_placement"))
        place_row = ttk.Frame(place_card)
        place_row.pack(anchor=tk.W)
        for text, value in [("Left", "left"), ("Right", "right"),
                             ("Above", "above"), ("Below", "below")]:
            ttk.Radiobutton(place_row, text=text, variable=self.placement_var,
                            value=value).pack(side=tk.LEFT, padx=6)

        # ── Main screen ───────────────────────────────────────────────────────
        main_card = ttk.LabelFrame(main_frame, text="  Main screen  ", padding="8")
        main_card.pack(fill=tk.X, pady=(0, 8))

        active = self.active_outputs
        if not active:
            ttk.Label(main_card, text="No active outputs available.").pack(anchor=tk.W)
            self.main_port_var = tk.StringVar()
        else:
            self.main_port_var = tk.StringVar(value=active[0].name)
            for output in active:
                tag = " [virtual]" if self.tracker.is_virtual(output.name) else ""
                ttk.Radiobutton(main_card,
                                text=f"{output.name}{tag}",
                                variable=self.main_port_var,
                                value=output.name).pack(anchor=tk.W, padx=4, pady=1)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(4, 0))
        ttk.Button(btn_frame, text="Apply", command=self._apply_add).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_all).pack(side=tk.LEFT, padx=5)
    
    def _build_manage_tab(self) -> None:
        """Build the Manage Displays tab (all outputs + VNC controls)."""
        for widget in self.manage_frame.winfo_children():
            widget.destroy()

        main_frame = ttk.Frame(self.manage_frame, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Output list ───────────────────────────────────────────────────────
        list_card = ttk.LabelFrame(main_frame, text="  All Outputs  ", padding="6")
        list_card.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Column headers
        header = ttk.Frame(list_card)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Output",  width=22, anchor=tk.W, font=('', 9, 'bold')).pack(side=tk.LEFT, padx=4)
        ttk.Label(header, text="Type",    width=10, anchor=tk.W, font=('', 9, 'bold')).pack(side=tk.LEFT, padx=4)
        ttk.Label(header, text="Status",  width=22, anchor=tk.W, font=('', 9, 'bold')).pack(side=tk.LEFT, padx=4)
        ttk.Label(header, text="Actions", width=28, anchor=tk.W, font=('', 9, 'bold')).pack(side=tk.LEFT, padx=4)
        ttk.Separator(list_card, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=3)

        inner = self._make_scrollable_frame(list_card)

        for output in self.all_outputs:
            is_virtual = self.tracker.is_virtual(output.name)
            row = ttk.Frame(inner)
            row.pack(fill=tk.X, pady=2)

            ttk.Label(row, text=output.name,
                      width=22, anchor=tk.W).pack(side=tk.LEFT, padx=4)
            ttk.Label(row, text="virtual" if is_virtual else "physical",
                      width=10, anchor=tk.W,
                      foreground="#0066cc" if is_virtual else "#444").pack(side=tk.LEFT, padx=4)
            ttk.Label(row, text=output.status,
                      width=22, anchor=tk.W).pack(side=tk.LEFT, padx=4)

            btn_cell = ttk.Frame(row)
            btn_cell.pack(side=tk.LEFT, padx=4)
            if output.active:
                ttk.Button(btn_cell, text="Turn Off",
                           command=lambda n=output.name: self._turn_off(n)).pack(side=tk.LEFT, padx=2)
            if is_virtual:
                ttk.Button(btn_cell, text="Forget",
                           command=lambda n=output.name: self._forget_virtual(n)).pack(side=tk.LEFT, padx=2)
            if not output.active and not is_virtual:
                ttk.Label(btn_cell, text="—").pack(side=tk.LEFT, padx=2)

        # ── VNC controls ──────────────────────────────────────────────────────
        vnc_card = ttk.LabelFrame(main_frame, text="  VNC  ", padding="8")
        vnc_card.pack(fill=tk.X, pady=(0, 6))

        display = self.settings.get("vnc_display")
        vnc_running = self.vnc.is_running(display)
        status_text = f"Running on {display}:{self.settings.get('vnc_port')}"
        if vnc_running and self.vnc.get_clip(display):
            status_text += f"  (clip: {self.vnc.get_clip(display)})"
        elif not vnc_running:
            status_text = "Not running"

        ttk.Label(vnc_card, text=f"Status: {status_text}",
                  foreground="green" if vnc_running else "#666").pack(anchor=tk.W, pady=(0, 6))

        btn_row = ttk.Frame(vnc_card)
        btn_row.pack(anchor=tk.W)
        ttk.Button(btn_row, text="Start VNC", command=self._start_vnc).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Stop VNC",  command=self._stop_vnc).pack(side=tk.LEFT)

        ttk.Button(main_frame, text="Refresh", command=self._refresh_all).pack(pady=(4, 0))
    
    def _test_mode(self) -> None:
        """Test if a mode can be created for the given resolution."""
        resolution = self.resolution_var.get().strip()
        if not RESOLUTION_PATTERN.match(resolution):
            messagebox.showerror("Error", "Invalid resolution format. Use WIDTHxHEIGHT, e.g., 1920x1080.")
            return
        
        success, err, mode_name = self.xrandr.create_mode(resolution)
        if success:
            messagebox.showinfo("Success", f"Mode created successfully.\nUsed name: {mode_name}")
        else:
            messagebox.showerror("Mode Creation Failed", 
                               f"Could not create mode.\n\nError: {err}\n\n"
                               "Your X server may not support creating custom modes.\n"
                               "Possible solutions:\n"
                               "• Use an existing mode from the output's mode list (if any).\n"
                               "• Try a different resolution.\n"
                               "• Check if your graphics driver supports custom modes.\n"
                               "• Run 'xrandr --newmode test 100 100 200 300 400 100 200 300 400' in a terminal to test.")
    
    def _apply_add(self) -> None:
        """Apply the add virtual display settings."""
        available = self.available_outputs
        active = self.active_outputs

        if not available:
            messagebox.showerror("Error", "No available outputs. All outputs are currently active.")
            return
        if not active:
            messagebox.showerror("Error", "No active main screen available.")
            return

        port = self.add_port_var.get()
        main = self.main_port_var.get()
        placement = self.placement_var.get()
        resolution = self.resolution_var.get().strip()

        if not port:
            messagebox.showerror("Error", "No output selected.")
            return
        if not main:
            messagebox.showerror("Error", "No main screen selected.")
            return
        if not RESOLUTION_PATTERN.match(resolution):
            messagebox.showerror("Error", "Invalid resolution — use WIDTHxHEIGHT, e.g. 1920x1080.")
            return

        # Offer to reuse an already-present mode for this resolution
        existing_modes = self.xrandr.get_existing_modes(port)
        matching = [m for m in existing_modes if resolution in m]
        if matching:
            if messagebox.askyesno(
                "Mode already exists",
                f"{port} already has a mode matching {resolution}:\n  {matching[0]}\n\n"
                "Use it instead of creating a new one?"
            ):
                self._activate_output_with_vnc(port, matching[0], placement, main)
                return

        # Create a new mode and enable the output
        success, error, mode_name = self.xrandr.add_mode(port, resolution)
        if not success:
            messagebox.showerror(
                "Add Mode Failed",
                f"xrandr --addmode {port} {resolution}\n\n{error}\n\n"
                "Your X server may not support custom modes.\n"
                "Try an existing mode shown in 'xrandr' output."
            )
            return

        self._activate_output_with_vnc(port, mode_name, placement, main)
    
    def _turn_off(self, output_name: str) -> None:
        """Turn off a display."""
        display_type = "virtual" if self.tracker.is_virtual(output_name) else "physical"
        
        if self.settings.get("confirm_turn_off"):
            if not messagebox.askyesno("Confirm Turn Off",
                                       f"Are you sure you want to turn off {output_name} ({display_type})?\n"
                                       "This may cause the display to go blank."):
                return
        
        success, error = self.xrandr.turn_off_output(output_name)
        if not success:
            messagebox.showerror("Turn Off Failed", error)
            return
        
        messagebox.showinfo("Success", f"Display {output_name} turned off.")
        self._refresh_all()
        self._maybe_reapply_wallpaper()
    
    def _forget_virtual(self, output_name: str) -> None:
        """Remove a display from the virtual tracker."""
        if self.settings.get("confirm_forget"):
            if not messagebox.askyesno("Confirm Forget",
                                       f"Remove {output_name} from the virtual display list?\n"
                                       "It will no longer be shown as (virtual) in the tabs."):
                return
        self.tracker.remove(output_name)
        self._refresh_all()
    
    def _start_vnc(self) -> None:
        """Start VNC server."""
        success, msg = self.vnc.start_vnc(
            display=self.settings.get("vnc_display"),
            port=self.settings.get("vnc_port"),
            forever=self.settings.get("vnc_forever"),
            shared=self.settings.get("vnc_shared"),
        )
        if success:
            messagebox.showinfo("VNC Started", msg)
        else:
            messagebox.showerror("VNC Failed", msg)
        self._refresh_all()
    
    def _stop_vnc(self) -> None:
        """Stop VNC server."""
        success, msg = self.vnc.stop_vnc(display=self.settings.get("vnc_display"))
        if success:
            messagebox.showinfo("VNC Stopped", msg)
        else:
            messagebox.showerror("VNC Failed", msg)
        self._refresh_all()
    
    def _maybe_reapply_wallpaper(self) -> None:
        """Re-apply wallpaper if the setting is enabled."""
        if not self.settings.get("reapply_wallpaper"):
            return
        success, msg = WallpaperManager.reapply_wallpaper()
        if not success:
            messagebox.showwarning(
                "Wallpaper Re-apply Failed",
                f"Could not re-apply wallpaper automatically.\n\n{msg}\n\n"
                "You can disable this feature in the Settings tab."
            )

    def _build_settings_tab(self) -> None:
        """Build the Settings tab."""
        for widget in self.settings_frame.winfo_children():
            widget.destroy()

        # Scrollable container
        outer = ttk.Frame(self.settings_frame)
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(canvas, padding="15")
        canvas_window = canvas.create_window((0, 0), window=main_frame, anchor=tk.NW)

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=canvas.winfo_width())

        main_frame.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── Title ────────────────────────────────────────────────────────────
        ttk.Label(main_frame, text="Settings", font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 4))
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

        # ════════════════════════════════════════════════════════════════════
        # Section 1 – Wallpaper
        # ════════════════════════════════════════════════════════════════════
        wp_card = ttk.LabelFrame(main_frame, text="  Wallpaper  ", padding="12")
        wp_card.pack(fill=tk.X, pady=(0, 10))

        warning_frame = tk.Frame(wp_card, bg="#fff3cd", bd=1, relief=tk.SOLID)
        warning_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(warning_frame, text="⚠  Warning", font=('', 9, 'bold'),
                 bg="#fff3cd", fg="#856404", anchor=tk.W).pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(warning_frame,
                 text=("When a second display is added or removed, some desktop environments\n"
                       "leave visual artifacts or a blank background on the remaining screen.\n"
                       "Enabling this option automatically re-applies your current wallpaper\n"
                       "after every display change to work around this issue."),
                 bg="#fff3cd", fg="#533f03", justify=tk.LEFT, anchor=tk.W,
                 wraplength=560).pack(fill=tk.X, padx=8, pady=(2, 8))

        reapply_var = tk.BooleanVar(value=self.settings.get("reapply_wallpaper"))

        def _wp_status_text(v): return (
            "Enabled — wallpaper will be re-applied after display changes." if v
            else "Disabled — wallpaper will NOT be re-applied after display changes."
        )

        wp_status = ttk.Label(wp_card, text=_wp_status_text(reapply_var.get()),
                              foreground="green" if reapply_var.get() else "gray",
                              font=('', 8))

        def on_wp_toggle():
            self.settings.set("reapply_wallpaper", reapply_var.get())
            wp_status.config(text=_wp_status_text(reapply_var.get()),
                             foreground="green" if reapply_var.get() else "gray")

        ttk.Checkbutton(wp_card, text="Re-apply wallpaper after display changes",
                        variable=reapply_var, command=on_wp_toggle).pack(anchor=tk.W)
        wp_status.pack(anchor=tk.W, padx=4, pady=(2, 4))

        de = WallpaperManager.detect_desktop_environment()
        de_text = de.upper() if de else "Unknown (will try nitrogen / feh as fallback)"
        ttk.Label(wp_card, text=f"Detected desktop environment: {de_text}",
                  foreground="#555", font=('', 8, 'italic')).pack(anchor=tk.W, padx=4)

        def test_wallpaper():
            ok, msg = WallpaperManager.reapply_wallpaper()
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "Wallpaper Re-applied" if ok else "Re-apply Failed", msg)

        ttk.Button(wp_card, text="Test: Re-apply Now", command=test_wallpaper).pack(
            anchor=tk.W, pady=(8, 0))

        # ════════════════════════════════════════════════════════════════════
        # Section 2 – VNC
        # ════════════════════════════════════════════════════════════════════
        vnc_card = ttk.LabelFrame(main_frame, text="  VNC  ", padding="12")
        vnc_card.pack(fill=tk.X, pady=(0, 10))

        # Port
        port_row = ttk.Frame(vnc_card)
        port_row.pack(fill=tk.X, pady=3)
        ttk.Label(port_row, text="VNC port:", width=32, anchor=tk.W).pack(side=tk.LEFT)
        port_var = tk.IntVar(value=self.settings.get("vnc_port"))
        port_spin = ttk.Spinbox(port_row, from_=1, to=65535, textvariable=port_var, width=8)
        port_spin.pack(side=tk.LEFT)
        ttk.Label(port_row, text="  (default: 5900)", foreground="#777",
                  font=('', 8)).pack(side=tk.LEFT)

        def on_port_change(*_):
            try:
                v = int(port_var.get())
                if 1 <= v <= 65535:
                    self.settings.set("vnc_port", v)
            except (ValueError, tk.TclError):
                pass
        port_var.trace_add("write", on_port_change)

        # X display
        disp_row = ttk.Frame(vnc_card)
        disp_row.pack(fill=tk.X, pady=3)
        ttk.Label(disp_row, text="X display:", width=32, anchor=tk.W).pack(side=tk.LEFT)
        disp_var = tk.StringVar(value=self.settings.get("vnc_display"))
        disp_entry = ttk.Entry(disp_row, textvariable=disp_var, width=8)
        disp_entry.pack(side=tk.LEFT)
        ttk.Label(disp_row, text="  (default: :0)", foreground="#777",
                  font=('', 8)).pack(side=tk.LEFT)

        def on_disp_change(*_):
            v = disp_var.get().strip()
            if v:
                self.settings.set("vnc_display", v)
        disp_var.trace_add("write", on_disp_change)

        ttk.Separator(vnc_card, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # Auto-start
        auto_var = tk.BooleanVar(value=self.settings.get("vnc_auto_start"))
        auto_status = ttk.Label(vnc_card,
            text="VNC will start automatically after adding a display." if auto_var.get()
                 else "VNC must be started manually from the Manage tab.",
            foreground="green" if auto_var.get() else "gray", font=('', 8))

        def on_auto_toggle():
            self.settings.set("vnc_auto_start", auto_var.get())
            auto_status.config(
                text="VNC will start automatically after adding a display." if auto_var.get()
                     else "VNC must be started manually from the Manage tab.",
                foreground="green" if auto_var.get() else "gray")

        ttk.Checkbutton(vnc_card, text="Auto-start VNC when a display is added",
                        variable=auto_var, command=on_auto_toggle).pack(anchor=tk.W)
        auto_status.pack(anchor=tk.W, padx=4, pady=(2, 6))

        # -forever flag
        forever_var = tk.BooleanVar(value=self.settings.get("vnc_forever"))

        def on_forever_toggle():
            self.settings.set("vnc_forever", forever_var.get())

        ttk.Checkbutton(vnc_card,
                        text="Keep VNC running after client disconnects  (-forever)",
                        variable=forever_var, command=on_forever_toggle).pack(anchor=tk.W)
        ttk.Label(vnc_card,
                  text="    When off, VNC exits as soon as the last client disconnects.",
                  foreground="#555", font=('', 8)).pack(anchor=tk.W, pady=(0, 4))

        # -shared flag
        shared_var = tk.BooleanVar(value=self.settings.get("vnc_shared"))

        def on_shared_toggle():
            self.settings.set("vnc_shared", shared_var.get())

        ttk.Checkbutton(vnc_card,
                        text="Allow multiple simultaneous VNC connections  (-shared)",
                        variable=shared_var, command=on_shared_toggle).pack(anchor=tk.W)
        ttk.Label(vnc_card,
                  text="    When off, a new connection will disconnect the existing one.",
                  foreground="#555", font=('', 8)).pack(anchor=tk.W)

        # ════════════════════════════════════════════════════════════════════
        # Section 3 – Display Defaults
        # ════════════════════════════════════════════════════════════════════
        disp_card = ttk.LabelFrame(main_frame, text="  Display Defaults  ", padding="12")
        disp_card.pack(fill=tk.X, pady=(0, 10))

        # Default resolution
        res_row = ttk.Frame(disp_card)
        res_row.pack(fill=tk.X, pady=3)
        ttk.Label(res_row, text="Default resolution:", width=22, anchor=tk.W).pack(side=tk.LEFT)
        COMMON_RESOLUTIONS = [
            "1920x1080", "2560x1440", "3840x2160",
            "1280x720",  "1280x800",  "1366x768",
            "1600x900",  "1440x900",  "1024x768",
        ]
        def_res_var = tk.StringVar(value=self.settings.get("default_resolution"))
        res_combo = ttk.Combobox(res_row, textvariable=def_res_var,
                                 values=COMMON_RESOLUTIONS, width=14)
        res_combo.pack(side=tk.LEFT)
        ttk.Label(res_row, text="  (custom values are accepted)",
                  foreground="#777", font=('', 8)).pack(side=tk.LEFT)

        def on_res_change(*_):
            v = def_res_var.get().strip()
            if RESOLUTION_PATTERN.match(v):
                self.settings.set("default_resolution", v)
        def_res_var.trace_add("write", on_res_change)
        res_combo.bind("<<ComboboxSelected>>", lambda _: on_res_change())

        # Default placement
        ttk.Label(disp_card, text="Default placement relative to main screen:",
                  anchor=tk.W).pack(anchor=tk.W, pady=(8, 3))
        placement_row = ttk.Frame(disp_card)
        placement_row.pack(anchor=tk.W, padx=10)
        def_place_var = tk.StringVar(value=self.settings.get("default_placement"))

        def on_place_change():
            self.settings.set("default_placement", def_place_var.get())

        for label, value in [("Left", "left"), ("Right", "right"),
                              ("Above", "above"), ("Below", "below")]:
            ttk.Radiobutton(placement_row, text=label, variable=def_place_var,
                            value=value, command=on_place_change).pack(side=tk.LEFT, padx=6)

        # ════════════════════════════════════════════════════════════════════
        # Section 4 – Confirmations
        # ════════════════════════════════════════════════════════════════════
        conf_card = ttk.LabelFrame(main_frame, text="  Confirmation Dialogs  ", padding="12")
        conf_card.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(conf_card,
                  text="Disable these to skip confirmation prompts for faster workflows.",
                  foreground="#555", font=('', 8)).pack(anchor=tk.W, pady=(0, 6))

        turn_off_var = tk.BooleanVar(value=self.settings.get("confirm_turn_off"))

        def on_turn_off_conf():
            self.settings.set("confirm_turn_off", turn_off_var.get())

        ttk.Checkbutton(conf_card, text='Confirm before turning off a display',
                        variable=turn_off_var, command=on_turn_off_conf).pack(anchor=tk.W)

        forget_var = tk.BooleanVar(value=self.settings.get("confirm_forget"))

        def on_forget_conf():
            self.settings.set("confirm_forget", forget_var.get())

        ttk.Checkbutton(conf_card, text='Confirm before forgetting a virtual display',
                        variable=forget_var, command=on_forget_conf).pack(anchor=tk.W, pady=(4, 0))

    def _refresh_all(self) -> None:
        """Refresh all data and rebuild UI."""
        if self._refresh_data():
            self._build_add_tab()
            self._build_manage_tab()
            self._build_settings_tab()
        else:
            messagebox.showerror("Error", "Failed to refresh xrandr data.")


def main():
    """Entry point for the application."""
    # Perform system checks before starting GUI
    success, error_msg = XrandrInterface.check_prerequisites()
    if not success:
        print(f"Error: {error_msg}")
        return

    app = XrandrGUI()
    app.mainloop()


if __name__ == '__main__':
    main()