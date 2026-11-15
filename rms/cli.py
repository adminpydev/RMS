import argparse
import errno
import json
import os
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import psutil

from rms import __version__
from rms.collector import Collector
from rms.server import make_server
from rms.store import Store

DEFAULT_DB = "~/.rms/rms.db"


def _iso(x):
    try:
        return int(datetime.fromisoformat(x).timestamp())
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid ISO datetime: {x!r}")


def _local(ts):
    return "-" if ts is None else datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")


def _meta(interval):
    return {"host": socket.gethostname(), "os": sys.platform,
            "total_ram": psutil.virtual_memory().total,
            "interval": interval, "version": __version__}


def _run(a):
    store = Store(os.path.expanduser(a.db))
    try:
        server = make_server(store, a.port, _meta(a.interval), Path(__file__).parent / "web")
    except OSError as e:
        store.close()
        if e.errno == errno.EADDRINUSE:
            print(f"Port {a.port} is in use. Try: rms run --port {a.port + 1}", file=sys.stderr)
        else:
            print(f"Cannot start server: {e}", file=sys.stderr)
        return 2
    stop = threading.Event()
    t = threading.Thread(target=Collector(store, a.interval, a.retention_days).run,
                         args=(stop,), daemon=True)
    t.start()
    url = f"http://127.0.0.1:{a.port}"
    print(f"RMS running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        t.join(timeout=a.interval + 2)
        server.server_close()
        store.close()
    return 0


def _export(a):
    store = Store(os.path.expanduser(a.db))
    try:
        d = store.export(a.frm, a.to)
    finally:
        store.close()
    d["meta"].update(_meta(10))  # ponytail: interval not stored in db; default assumed
    text = json.dumps(d)
    if a.output:
        Path(a.output).write_text(text)
    else:
        print(text)
    return 0


def _status(a):
    store = Store(os.path.expanduser(a.db))
    try:
        s = store.stats()
    finally:
        store.close()
    print(f"db: {s['path']}\nsize: {s['size_bytes'] / 2**20:.2f} MB\n"
          f"system: {s['system_rows']} rows\napps: {s['app_rows']} rows\n"
          f"range: {_local(s['first_ts'])} .. {_local(s['last_ts'])}")
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
    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", default=DEFAULT_DB)
    r = sub.add_parser("run", parents=[db])
    r.add_argument("--interval", type=positive_int, default=10)
    r.add_argument("--port", type=port_int, default=8765)
    r.add_argument("--retention-days", type=positive_int, default=14)
    r.add_argument("--no-browser", action="store_true")
    r.set_defaults(fn=_run)
    e = sub.add_parser("export", parents=[db])
    e.add_argument("--from", dest="frm", type=_iso)
    e.add_argument("--to", type=_iso)
    e.add_argument("-o", "--output")
    e.set_defaults(fn=_export)
    sub.add_parser("status", parents=[db]).set_defaults(fn=_status)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
