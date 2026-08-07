"""Small cross-platform ANSI terminal layer for VIME."""

import os
import shutil
import sys
import time
from contextlib import contextmanager


ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
CLEAR_HOME = "\x1b[H\x1b[2J"

ESCAPE_KEYS = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[5~": "page_up",
    "\x1b[6~": "page_down",
    "\x1bOH": "home",
    "\x1bOF": "end",
    "\x1b[H": "home",
    "\x1b[F": "end",
}


def decode_escape(sequence):
    """Decode a complete ANSI escape sequence."""
    return ESCAPE_KEYS.get(sequence, "escape" if sequence == "\x1b" else None)


def cleanup_sequence():
    """Return the sequence that restores visible terminal state."""
    return CURSOR_SHOW + ALT_SCREEN_OFF


class TerminalSession:
    """Own raw input and ANSI screen state for one TUI run."""

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._fd = None
        self._termios_state = None
        self._windows_mode = None

    def __enter__(self):
        if os.name == "nt":
            self._enable_windows_vt()
        else:
            self._enable_posix_input()
        self.stdout.write(ALT_SCREEN_ON + CURSOR_HIDE + CLEAR_HOME)
        self.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self._restore_input()
        finally:
            self.stdout.write(cleanup_sequence())
            self.stdout.flush()
        return False

    def size(self):
        """Return current terminal dimensions as ``(columns, lines)``."""
        size = shutil.get_terminal_size(fallback=(80, 24))
        return size.columns, size.lines

    def write_frame(self, frame):
        """Replace the screen using one stdout write."""
        self.stdout.write(CLEAR_HOME + frame)
        self.stdout.flush()

    @contextmanager
    def suspended(self):
        """Temporarily restore the real terminal for an external program."""
        self._restore_input()
        self.stdout.write(CURSOR_SHOW + ALT_SCREEN_OFF)
        self.stdout.flush()
        try:
            yield
        finally:
            if os.name == "nt":
                self._enable_windows_vt()
            else:
                self._enable_posix_input()
            self.stdout.write(ALT_SCREEN_ON + CURSOR_HIDE + CLEAR_HOME)
            self.stdout.flush()

    def read_key(self, timeout=0.05):
        """Read one normalized key name, or return ``None`` on timeout."""
        if os.name == "nt":
            return self._read_windows_key(timeout)
        return self._read_posix_key(timeout)

    def _enable_windows_vt(self):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        self._windows_mode = (handle, mode.value)
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)

    def _enable_posix_input(self):
        import termios
        import tty

        self._fd = self.stdin.fileno()
        self._termios_state = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def _restore_input(self):
        if os.name == "nt":
            if self._windows_mode is not None:
                import ctypes
                handle, mode = self._windows_mode
                ctypes.windll.kernel32.SetConsoleMode(handle, mode)
            return
        if self._termios_state is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._termios_state)

    @staticmethod
    def _normalize_character(char):
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\x02":
            return "ctrl_b"
        if char == "\x05":
            return "ctrl_e"
        if char in ("\r", "\n"):
            return "enter"
        if char in ("\x7f", "\b"):
            return "backspace"
        return char

    def _read_windows_key(self, timeout):
        import msvcrt

        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.01, timeout))
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "I": "page_up", "Q": "page_down", "G": "home", "O": "end",
            }.get(msvcrt.getwch())
        return self._normalize_character(char)

    def _read_posix_key(self, timeout):
        import select

        ready, _, _ = select.select([self.stdin], [], [], timeout)
        if not ready:
            return None
        char = self.stdin.read(1)
        if char != "\x1b":
            return self._normalize_character(char)

        sequence = char
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.stdin], [], [], 0)
            if not ready:
                time.sleep(0.001)
                continue
            sequence += self.stdin.read(1)
            if sequence in ESCAPE_KEYS:
                return ESCAPE_KEYS[sequence]
        return decode_escape(sequence)
