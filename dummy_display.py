#!/usr/bin/env python3
"""
GUI tool to add/enable or turn off displays using xrandr.
Tracks which displays were created by this script (virtual displays).
Shows (physical) or (virtual) next to each output in the Manage tab.
Requires: Python 3, tkinter, and xrandr installed.
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass
from functools import lru_cache
import tkinter as tk
from tkinter import messagebox, ttk


# Constants
VIRTUAL_DISPLAYS_FILE = Path.home() / ".virtual_displays.json"
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
                    data = json.load(f)
                    return set(data.get('virtual_displays', []))
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load virtual displays file: {e}")
        return set()
    
    def save(self) -> None:
        """Save virtual displays to file."""
        try:
            with open(self.filepath, 'w') as f:
                json.dump({'virtual_displays': list(self._displays)}, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save virtual displays file: {e}")
    
    def add(self, display_name: str) -> None:
        """Add a display to tracking."""
        self._displays.add(display_name)
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


class XrandrInterface:
    """Interface for xrandr command execution and parsing."""
    
    @staticmethod
    def check_prerequisites() -> Tuple[bool, Optional[str]]:
        """Check if X11 and xrandr are available.
        Returns (success, error_message)
        """
        if 'DISPLAY' not in os.environ:
            return False, "DISPLAY environment variable not set. Is X11 running?"
        
        if shutil.which('xrandr') is None:
            return False, "xrandr not found. Please install it (e.g., 'sudo apt install x11-xserver-utils')."
        
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
                    connected = 'connected' in line
                    active = bool(GEOMETRY_PATTERN.search(line))
                    
                    # Check subsequent indented lines for active modes
                    if not active and connected:
                        j = i + 1
                        while j < len(lines) and lines[j] and lines[j][0] in (' ', '\t'):
                            if '*' in lines[j]:
                                active = True
                                break
                            j += 1
                        i = j - 1
                    
                    displays.append(DisplayOutput(name, connected, active))
            i += 1
        
        return displays
    
    @staticmethod
    def add_mode(output: str, resolution: str) -> Tuple[bool, Optional[str]]:
        """Add a mode to an output. Returns (success, error_message)."""
        try:
            subprocess.run(['xrandr', '--addmode', output, resolution],
                         check=True,
                         capture_output=True,
                         text=True,
                         timeout=10)
            return True, None
        except subprocess.CalledProcessError as e:
            return False, f"Failed to add mode: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
    
    @staticmethod
    def enable_output(output: str, resolution: str, placement: str, relative_to: str) -> Tuple[bool, Optional[str]]:
        """Enable an output with specified settings."""
        placement_arg = PLACEMENT_OPTIONS.get(placement)
        if not placement_arg:
            return False, f"Invalid placement: {placement}"
        
        cmd = ['xrandr', '--output', output, '--mode', resolution, placement_arg, relative_to]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)
            return True, None
        except subprocess.CalledProcessError as e:
            return False, f"Failed to enable output: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
    
    @staticmethod
    def turn_off_output(output: str) -> Tuple[bool, Optional[str]]:
        """Turn off an output."""
        try:
            subprocess.run(['xrandr', '--output', output, '--off'],
                         check=True,
                         capture_output=True,
                         text=True,
                         timeout=10)
            return True, None
        except subprocess.CalledProcessError as e:
            return False, f"Failed to turn off output: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"


class XrandrGUI(tk.Tk):
    """Main GUI application for display management."""
    
    def __init__(self):
        super().__init__()
        self.title("Display Manager")
        self.geometry("600x500")
        self.resizable(False, False)
        
        # Initialize managers
        self.tracker = VirtualDisplayTracker()
        self.xrandr = XrandrInterface()
        
        # Data storage
        self.all_outputs: List[DisplayOutput] = []
        
        # Perform system checks
        success, error_msg = self.xrandr.check_prerequisites()
        if not success:
            messagebox.showerror("Error", error_msg)
            self.destroy()
            return
        
        # Load initial data
        if not self._refresh_data():
            messagebox.showerror("Error", "Failed to load display information")
            self.destroy()
            return
        
        # Create UI
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the main UI structure."""
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.add_frame = ttk.Frame(self.notebook)
        self.manage_frame = ttk.Frame(self.notebook)
        
        self.notebook.add(self.add_frame, text="Add Virtual Display")
        self.notebook.add(self.manage_frame, text="Manage Displays")
        
        self._build_add_tab()
        self._build_manage_tab()
    
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
    
    def _build_add_tab(self) -> None:
        """Build the Add Virtual Display tab."""
        # Clear existing widgets
        for widget in self.add_frame.winfo_children():
            widget.destroy()
        
        main_frame = ttk.Frame(self.add_frame, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        row = 0
        
        # Info label
        ttk.Label(main_frame, 
                 text="Note: Displays created here will be marked as (virtual) in the Manage tab.",
                 foreground="blue").grid(row=row, column=0, sticky=tk.W, pady=(0, 10))
        row += 1
        
        # Unused outputs section
        ttk.Label(main_frame, text="Unused Outputs:", 
                 font=('', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=(0, 5))
        row += 1
        
        inactive = self.inactive_outputs
        if not inactive:
            ttk.Label(main_frame, 
                     text="No unused (connected but inactive) outputs found.").grid(
                         row=row, column=0, sticky=tk.W, padx=20)
            self.add_port_var = tk.StringVar()
        else:
            self.add_port_var = tk.StringVar(value=inactive[0].name)
            for output in inactive:
                virtual_note = " (virtual)" if self.tracker.is_virtual(output.name) else ""
                ttk.Radiobutton(main_frame, 
                              text=f"{output.name}{virtual_note}",
                              variable=self.add_port_var,
                              value=output.name).grid(row=row, column=0, sticky=tk.W, padx=20)
                row += 1
        
        # Resolution input
        ttk.Label(main_frame, text="Resolution (e.g., 1920x1080):",
                 font=('', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=(10, 5))
        row += 1
        
        self.resolution_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.resolution_var, width=15).grid(
            row=row, column=0, sticky=tk.W, padx=20)
        row += 1
        
        # Placement options
        ttk.Label(main_frame, text="Placement relative to main screen:",
                 font=('', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=(10, 5))
        row += 1
        
        placement_frame = ttk.Frame(main_frame)
        placement_frame.grid(row=row, column=0, sticky=tk.W, padx=20)
        row += 1
        
        self.placement_var = tk.StringVar(value="left")
        for text, value in [("Left", "left"), ("Right", "right"), 
                           ("Above", "above"), ("Below", "below")]:
            ttk.Radiobutton(placement_frame, text=text,
                          variable=self.placement_var,
                          value=value).pack(side=tk.LEFT, padx=5)
        
        # Main screen selection
        ttk.Label(main_frame, text="Main Screen:",
                 font=('', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=(10, 5))
        row += 1
        
        active = self.active_outputs
        if not active:
            ttk.Label(main_frame, 
                     text="No active outputs available.").grid(
                         row=row, column=0, sticky=tk.W, padx=20)
            self.main_port_var = tk.StringVar()
        else:
            self.main_port_var = tk.StringVar(value=active[0].name)
            for output in active:
                virtual_note = " (virtual)" if self.tracker.is_virtual(output.name) else ""
                ttk.Radiobutton(main_frame,
                              text=f"{output.name}{virtual_note}",
                              variable=self.main_port_var,
                              value=output.name).grid(row=row, column=0, sticky=tk.W, padx=20)
                row += 1
        
        # Action buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, pady=20)
        ttk.Button(btn_frame, text="Apply", command=self._apply_add).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_all).pack(side=tk.LEFT, padx=5)
    
    def _build_manage_tab(self) -> None:
        """Build the Manage Displays tab."""
        for widget in self.manage_frame.winfo_children():
            widget.destroy()
        
        main_frame = ttk.Frame(self.manage_frame, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Info label
        ttk.Label(main_frame,
                 text="Displays marked (virtual) were created by this script or detected as virtual.",
                 foreground="blue").pack(anchor=tk.W, pady=(0, 10))
        
        ttk.Label(main_frame, text="All Outputs:",
                 font=('', 10, 'bold')).pack(anchor=tk.W)
        
        # List frame with scrolling
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Headers
        header = ttk.Frame(list_frame)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Output", width=25, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        ttk.Label(header, text="Status", width=20, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        ttk.Label(header, text="Action", width=10, anchor=tk.W).pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(list_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=2)
        
        # Scrollable content
        canvas = tk.Canvas(list_frame, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind("<Configure>",
                             lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Populate outputs
        for output in self.all_outputs:
            row = ttk.Frame(scrollable_frame)
            row.pack(fill=tk.X, pady=2)
            
            display_type = "virtual" if self.tracker.is_virtual(output.name) else "physical"
            name_text = f"{output.name} ({display_type})"
            ttk.Label(row, text=name_text, width=25, anchor=tk.W).pack(side=tk.LEFT, padx=5)
            ttk.Label(row, text=output.status, width=20, anchor=tk.W).pack(side=tk.LEFT, padx=5)
            
            if output.connected and output.active:
                ttk.Button(row, text="Turn Off",
                          command=lambda name=output.name: self._turn_off(name)).pack(
                              side=tk.LEFT, padx=5)
            else:
                ttk.Label(row, text="—", width=10).pack(side=tk.LEFT, padx=5)
        
        # Refresh button
        ttk.Button(main_frame, text="Refresh List", command=self._refresh_all).pack(pady=10)
    
    def _apply_add(self) -> None:
        """Apply the add virtual display settings."""
        inactive = self.inactive_outputs
        active = self.active_outputs
        
        if not inactive:
            messagebox.showerror("Error", "No unused outputs available.")
            return
        if not active:
            messagebox.showerror("Error", "No active main screen available.")
            return
        
        port = self.add_port_var.get()
        main = self.main_port_var.get()
        placement = self.placement_var.get()
        resolution = self.resolution_var.get().strip()
        
        if not port:
            messagebox.showerror("Error", "No unused output selected.")
            return
        if not main:
            messagebox.showerror("Error", "No main screen selected.")
            return
        if not RESOLUTION_PATTERN.match(resolution):
            messagebox.showerror("Error", 
                               "Invalid resolution format. Use WIDTHxHEIGHT, e.g., 1920x1080.")
            return
        
        # Add mode
        success, error = self.xrandr.add_mode(port, resolution)
        if not success:
            messagebox.showerror("Add Mode Failed", 
                               f"Command: xrandr --addmode {port} {resolution}\nError: {error}")
            return
        
        # Enable output
        success, error = self.xrandr.enable_output(port, resolution, placement, main)
        if not success:
            messagebox.showerror("Enable Output Failed", error)
            return
        
        # Track as virtual
        self.tracker.add(port)
        
        messagebox.showinfo("Success",
                          f"Display {port} enabled with {resolution} {placement} of {main}.\n"
                          "This display is now marked as (virtual) in the Manage tab.")
        self._refresh_all()
    
    def _turn_off(self, output_name: str) -> None:
        """Turn off a display."""
        display_type = "virtual" if self.tracker.is_virtual(output_name) else "physical"
        
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
    
    def _refresh_all(self) -> None:
        """Refresh all data and rebuild UI."""
        if self._refresh_data():
            self._build_add_tab()
            self._build_manage_tab()
        else:
            messagebox.showerror("Error", "Failed to refresh xrandr data.")


def main():
    """Entry point for the application."""
    app = XrandrGUI()
    app.mainloop()


if __name__ == '__main__':
    main()