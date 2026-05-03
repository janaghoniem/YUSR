from __future__ import annotations

import glob
import os
import time
import pyautogui
import pyperclip
import subprocess
from datetime import datetime
from typing import Optional


def _get_agent_folder(subfolder: str) -> str:
    """Get or create agent folder for storing text files."""
    possible_paths = [
        os.path.expanduser("~/OneDrive/Desktop/agent"),
        os.path.expanduser("~/Desktop/agent"),
        os.path.expanduser("~/Documents/agent"),
    ]

    for base_path in possible_paths:
        if os.path.exists(os.path.dirname(base_path)) or os.path.exists(base_path):
            folder = os.path.join(base_path, subfolder)
            os.makedirs(folder, exist_ok=True)
            return folder

    fallback_path = os.path.join(os.path.expanduser("~"), "agent", subfolder)
    os.makedirs(fallback_path, exist_ok=True)
    return fallback_path


def _ensure_txt_extension(name: str) -> str:
    """Ensure filename has .txt extension."""
    if name.lower().endswith(".txt"):
        return name
    return f"{name}.txt"


def _timestamp() -> str:
    """Get current timestamp."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _print_success(path: str) -> None:
    """Print success message with file path."""
    print(f"[FILE]: {path}")
    print("EXECUTION_SUCCESS")


def _resolve_text_path(filename: str) -> str:
    """Resolve text file path from various locations."""
    if os.path.isabs(filename) and os.path.exists(filename):
        return filename

    folder = _get_agent_folder("notes")
    search_roots = [
        os.path.expanduser("~\\Desktop"),
        os.path.expanduser("~\\OneDrive\\Desktop"),
        os.path.expanduser("~\\Documents"),
        os.path.expanduser("~\\Downloads"),
        folder,
    ]

    for root in search_roots:
        if not os.path.exists(root):
            continue
        direct = os.path.join(root, filename)
        if os.path.isfile(direct):
            return direct
        # Try with .txt extension if not provided
        if not filename.endswith(".txt"):
            direct_txt = os.path.join(root, f"{filename}.txt")
            if os.path.isfile(direct_txt):
                return direct_txt
        matches = glob.glob(os.path.join(root, "**", filename), recursive=True)
        if matches:
            return matches[0]

    raise FileNotFoundError(f"Could not find file: {filename}")


def notepad_launch() -> None:
    """Launch Notepad application (non-blocking)."""
    try:
        # Use subprocess.Popen for non-blocking launch
        # This prevents the subprocess from hanging waiting for Notepad to close
        subprocess.Popen("notepad.exe")
        time.sleep(0.5)  # Brief delay for Notepad to initialize
        print("EXECUTION_SUCCESS")
    except Exception as e:
        print(f"Exception: {e}")


def notepad_create(name: Optional[str] = None) -> str:
    """Create a new text file for editing."""
    folder = _get_agent_folder("notes")
    filename = _ensure_txt_extension(name) if name else f"note_{_timestamp()}.txt"
    save_path = filename if os.path.isabs(filename) else os.path.join(folder, filename)

    # Create empty file
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write("")

    _print_success(save_path)
    return save_path


def notepad_open(path: str) -> None:
    """Open a text file in Notepad."""
    target = _resolve_text_path(path)
    os.startfile(target, "open")
    time.sleep(1.5)  # Wait for Notepad to open
    _print_success(target)


def notepad_find(filename: str) -> str:
    """Find a text file and return its path."""
    found = _resolve_text_path(filename)
    _print_success(found)
    return found


def notepad_load(path: str) -> str:
    """Load and return the contents of a text file."""
    resolved = _resolve_text_path(path)
    with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return content


def notepad_save(path: str, content: str) -> str:
    """Save content to a text file."""
    save_path = _ensure_txt_extension(path)
    if not os.path.isabs(save_path):
        save_path = os.path.join(_get_agent_folder("notes"), save_path)

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(content)

    _print_success(save_path)
    return save_path


def notepad_write(path: str, content: str, mode: str = 'w') -> str:
    """
    Write or append content to a text file.

    Args:
        path: File path
        content: Text content to write
        mode: 'w' for overwrite (default), 'a' for append
    """
    save_path = _ensure_txt_extension(path)
    if not os.path.isabs(save_path):
        save_path = os.path.join(_get_agent_folder("notes"), save_path)

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, mode, encoding='utf-8') as f:
        f.write(content)

    _print_success(save_path)
    return save_path


def notepad_type(text: str, delay: float = 0.05) -> None:
    """
    Type text into the currently focused Notepad window.
    Uses clipboard for better handling of special characters and multi-line text.

    Args:
        text: Text to type
        delay: Delay between characters (for slow typing simulation)
    """
    try:
        # Use clipboard for reliable text insertion (especially for non-ASCII)
        pyperclip.copy(text)
        time.sleep(0.1)

        # Paste with Ctrl+V
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.2)

        print("EXECUTION_SUCCESS")
    except Exception as e:
        print(f"Exception: {e}")


def notepad_append(path: str, text: str) -> str:
    """Append text to an existing file."""
    return notepad_write(path, text, mode='a')


def notepad_clear(path: str) -> str:
    """Clear all content from a file."""
    return notepad_save(path, "")


def notepad_read(path: str) -> None:
    """Read and print file contents (for extraction tasks)."""
    try:
        content = notepad_load(path)
        print(content)
        print("EXECUTION_SUCCESS")
    except Exception as e:
        print(f"Exception: {e}")


def notepad_focus() -> None:
    """Focus on the currently open Notepad window."""
    time.sleep(0.5)
    pyautogui.hotkey('alt', 'tab')  # Switch to Notepad if needed
    time.sleep(0.3)
    print("EXECUTION_SUCCESS")


__all__ = [
    "notepad_launch",
    "notepad_create",
    "notepad_open",
    "notepad_find",
    "notepad_load",
    "notepad_save",
    "notepad_write",
    "notepad_type",
    "notepad_append",
    "notepad_clear",
    "notepad_read",
    "notepad_focus",
]
