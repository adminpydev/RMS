import argparse
import errno
import signal
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import psutil

from rms import __version__
from rms.collector import Collector
from rms.live import LiveSampler, Recent
from rms.server import make_server


def _meta(interval):
    return {"host": socket.gethostname(), "os": sys.platform,
            "total_ram": psutil.virtual_memory().total,
            "interval": interval, "version": __version__}


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _run(a):
    recent = Recent()
    collector = Collector(recent, a.interval)
    sampler = LiveSampler(recent, collector)
    try:
        server = make_server(a.port, _meta(a.interval), Path(__file__).parent / "web",
                             recent, sampler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"Port {a.port} is in use. Try: rms run --port {a.port + 1}", file=sys.stderr)
        else:
            print(f"Cannot start server: {e}", file=sys.stderr)
        return 2
    stop = threading.Event()
    t = threading.Thread(target=collector.run, args=(stop,), daemon=True)
    t.start()
    lt = threading.Thread(target=sampler.run, args=(stop,), daemon=True)
    lt.start()
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
        stop.set()
        t.join(timeout=a.interval + 2)
        lt.join(timeout=3)
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
    r.add_argument("--interval", type=positive_int, default=10)
    r.add_argument("--port", type=port_int, default=8765)
    r.add_argument("--no-browser", action="store_true")
    r.set_defaults(fn=_run)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
