"""Unified entry point: launches GUI by default or CLI when subcommands are passed."""

import sys


def _attach_windows_console() -> None:
    """Attach to parent console on Windows if invoked from terminal."""
    if sys.platform == "win32":
        try:
            import ctypes
            # ATTACH_PARENT_PROCESS = -1
            if ctypes.windll.kernel32.AttachConsole(-1):
                try:
                    sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                except Exception:
                    pass
                try:
                    sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                except Exception:
                    pass
        except Exception:
            pass


def entry_point() -> int:
    if len(sys.argv) > 1:
        _attach_windows_console()
        from upload_ready.cli import main as cli_main
        return cli_main()
    else:
        from upload_ready.gui.app import run_gui
        return run_gui()


if __name__ == "__main__":
    sys.exit(entry_point())
