"""Desktop entry point: kn5port in its own window (pywebview), or the browser if pywebview is missing.

The packaged kn5port.exe also runs conversions by re-launching itself with command-line arguments
(``kn5port.exe convert ...``), so with arguments this behaves exactly like the ``kn5port`` command.
"""
import os
import sys


def _attach_std_handles():
    """A windowed exe starts with sys.stdout = None; reattach to the pipes the parent process passed in."""
    if sys.stdout is not None:
        return
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        out = []
        for std in (-11, -12):                                   # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            h = k32.GetStdHandle(std)
            if h in (0, -1):
                raise OSError("no handle")
            fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
            out.append(open(fd, "w", encoding="utf-8", errors="replace", buffering=1))
        sys.stdout, sys.stderr = out
    except Exception:
        sys.stdout = sys.stderr = open(os.devnull, "w")


def main():
    if len(sys.argv) > 1:                         # command-line use, including conversion subprocesses
        _attach_std_handles()
        from .cli import main as cli_main
        cli_main(sys.argv[1:])
        return

    from .ui.server import start_server
    app, httpd, url = start_server()
    try:
        import webview
    except ImportError:
        import time
        import webbrowser
        _attach_std_handles()
        print(f"kn5port UI: {url}  (install 'pywebview' for a desktop window; Ctrl+C to stop)", flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    window = webview.create_window("kn5port", url, width=1360, height=880, min_size=(900, 600),
                                   background_color="#111316")

    def pick_folder(start=""):
        r = window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or "")
        return (r[0] if isinstance(r, (list, tuple)) and r else r) or ""

    def pick_file(start=""):
        r = window.create_file_dialog(webview.OPEN_DIALOG, directory=start or "",
                                      file_types=("Disc images (*.iso;*.xiso)", "All files (*.*)"))
        return (r[0] if isinstance(r, (list, tuple)) and r else r) or ""

    app.pick_folder = pick_folder
    app.pick_file = pick_file
    app.updater.quit = window.destroy          # after an update: close, so this process exits and the new exe runs
    try:
        webview.start()
    finally:
        for j in app.jobs.values():
            j.cancel()
        if app.fh1.job:
            app.fh1.job.cancel()
        httpd.shutdown()


if __name__ == "__main__":
    main()
