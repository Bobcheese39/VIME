"""Small cross-platform ANSI terminal layer for VIME."""

import os
import re
import shutil
import sys
import time
from contextlib import contextmanager


ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
CLEAR_HOME = "\x1b[H\x1b[2J"

# Portable ECMA-48 SGR: PuTTY/xterm/VTE honor these; no 256/truecolor.
SGR_BOLD = "1"
SGR_UNDERLINE = "4"
SGR_REVERSE = "7"
SGR_RED = "31"
SGR_RESET = "\x1b[0m"

# ponytail: CSI SGR only, not OSC/UTF-8 combining widths. Upgrade to wcwidth
# if CJK/emoji columns start wrapping. Do not probe TERM for 256-color.
_CSI_SGR = re.compile(r"\x1b\[[0-9;]*m")
_SGR_RESETS = frozenset(("\x1b[0m", "\x1b[m"))

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


def style_enabled():
    """Off when NO_COLOR is non-empty or TERM is dumb. No COLORTERM/256 probing."""
    if os.environ.get("NO_COLOR"):
        return False
    return os.environ.get("TERM") != "dumb"


def strip_style(text):
    """Remove SGR sequences so a frame can be written as plain text."""
    return _CSI_SGR.sub("", text)


def visible_len(text):
    """Display width of *text*, ignoring SGR. One Unicode code point = one cell."""
    return len(strip_style(text))


def crop(text, width):
    """Crop to *width* visible cells without splitting an SGR sequence."""
    width = max(0, width)
    if width == 0:
        return ""
    out = []
    vis = 0
    i = 0
    n = len(text)
    styled = False
    while i < n and vis < width:
        match = _CSI_SGR.match(text, i)
        if match:
            seq = match.group()
            out.append(seq)
            styled = seq not in _SGR_RESETS
            i = match.end()
            continue
        out.append(text[i])
        vis += 1
        i += 1
    while i < n:
        match = _CSI_SGR.match(text, i)
        if not match:
            break
        seq = match.group()
        out.append(seq)
        styled = seq not in _SGR_RESETS
        i = match.end()
    result = "".join(out)
    if i < n and styled:
        result += SGR_RESET
    return result


def pad(text, width):
    """Crop to *width* visible cells and right-pad with spaces."""
    width = max(0, width)
    text = crop(text, width)
    return text + " " * (width - visible_len(text))


def paint(text, *codes):
    """Wrap already-padded *text* in SGR *codes*, or return it unchanged."""
    if not codes or not style_enabled():
        return text
    return "\x1b[" + ";".join(codes) + "m" + text + SGR_RESET


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
        if char == "\x1b":
            return "escape"
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
