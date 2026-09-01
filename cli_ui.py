"""Terminal UI helpers: colors, spinner, and leveled logging."""

from __future__ import annotations

import sys
import threading
import time


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"


class Spinner:
    def __init__(self, message: str, verbose: bool = False):
        self.message = message
        self.verbose = verbose
        self._running = False
        self._thread = None
        self._chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def start(self):
        if self.verbose:
            print(f"{Colors.CYAN}{self.message}{Colors.RESET}")
            return
        self._running = True
        self._thread = threading.Thread(target=self._spin)
        self._thread.daemon = True
        self._thread.start()

    def _spin(self):
        i = 0
        while self._running:
            sys.stdout.write(f"\r{Colors.CYAN}{self._chars[i % len(self._chars)]} {self.message}{Colors.RESET}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    def stop(self, success: bool = True, message: str = ""):
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
        if self.verbose:
            return
        sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
        sys.stdout.flush()
        if message:
            color = Colors.GREEN if success else Colors.RED
            print(f"{color}{message}{Colors.RESET}")


def log_info(message: str, verbose: bool = False):
    if verbose:
        print(f"{Colors.BLUE}[INFO] {message}{Colors.RESET}")
    else:
        print(f"{Colors.BLUE}{message}{Colors.RESET}")


def log_success(message: str, verbose: bool = False):
    print(f"{Colors.GREEN}{message}{Colors.RESET}")


def log_warning(message: str, verbose: bool = False):
    print(f"{Colors.YELLOW}{message}{Colors.RESET}")


def log_error(message: str, verbose: bool = False):
    print(f"{Colors.RED}{message}{Colors.RESET}", file=sys.stderr)


def log_debug(message: str, verbose: bool = False):
    if verbose:
        print(f"{Colors.GRAY}[DEBUG] {message}{Colors.RESET}")
