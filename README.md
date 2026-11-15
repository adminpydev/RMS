# RMS

RAM Monitoring System. Built-in tools show RAM usage right now. RMS records it over time, on your own machine, and shows where it went: which app leaked, what spiked at 3pm, what a normal day looks like.

Try the dashboard on sample data: https://adminpydev.github.io/RMS/

## Install

```
pipx install git+https://github.com/adminpydev/RMS
```

or with uv:

```
uv tool install git+https://github.com/adminpydev/RMS
```

Python 3.9 or newer. No sudo needed.

## Usage

```
rms run     [--interval 10] [--port 8765] [--db PATH] [--retention-days 14] [--no-browser]
rms export  [--from ISO] [--to ISO] [-o FILE]
rms status
```

- `rms run` samples RAM in the foreground and serves the dashboard at http://127.0.0.1:8765. Ctrl+C stops it cleanly. If the port is busy, pass `--port`.
- `rms export` dumps recorded data as JSON (same format as the demo dataset).
- `rms status` prints the database path, size, row counts and time range.

Data lives in `~/.rms/rms.db` and is kept for 14 days by default.

## Privacy

- Runs only on your machine. The server binds to `127.0.0.1` and rejects other Host headers.
- No telemetry, no accounts. The dashboard makes no requests except to its own origin.
- The dashboard only answers on `127.0.0.1`/`localhost` at the same port. Over SSH, forward the same local port: `ssh -L 8765:127.0.0.1:8765 host`.
- Open source, Apache-2.0.

The demo site shows sample data from a demo machine, not your computer. Browsers cannot read system RAM.

## Development

```
uv venv .venv && uv pip install -e '.[dev]'
.venv/bin/pytest
node --test tests/js/
```

## Publishing the site

The workflow in `.github/workflows/pages.yml` tests, then force-pushes the assembled site to the `gh-pages` branch on every push to `master`. After the first push, enable it once: Settings → Pages → Deploy from a branch → `gh-pages` / root.

## License

Apache-2.0. See `LICENSE`.
