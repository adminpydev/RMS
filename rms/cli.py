import argparse
import errno
import signal
import socket
import sys
import webbrowser
from pathlib import Path

import psutil

from rms import __version__
from rms.server import make_server
from rms.snapshot import Snapshotter


def _meta():
    return {"host": socket.gethostname(), "os": sys.platform,
            "total_ram": psutil.virtual_memory().total,
            "version": __version__}


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _run(a):
    try:
        server = make_server(a.port, _meta(), Path(__file__).parent / "web", Snapshotter())
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"Port {a.port} is in use. Try: rms run --port {a.port + 1}", file=sys.stderr)
        else:
            print(f"Cannot start server: {e}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{a.port}"
    print(f"RMS running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    signal.signal(signal.SIGTERM, _interrupt)  # stop like Ctrl+C (kill, launchd, systemd)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def positive_int(s):
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def port_int(s):
    n = positive_int(s)
    if n > 65535:
        raise argparse.ArgumentTypeError("must be <= 65535")
    return n


def main(argv=None):
    p = argparse.ArgumentParser(prog="rms")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--port", type=port_int, default=8765)
    r.add_argument("--no-browser", action="store_true")
    r.set_defaults(fn=_run)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
