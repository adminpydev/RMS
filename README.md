# RMS

RAM Monitoring System. See where your RAM goes right now, in plain language. Everything runs on your own machine and nothing is written to disk.

## What it shows

One page, current state only:

- Memory panel: wired, active, inactive, compressed and free (used, cached and free on Linux), plus swap used and the pressure level.
- Insights: plain-language findings from six kinds: memory pressure, swap in use, one process using a big share of RAM, one app using a big share, an app running many processes, zombie processes.
- Apps table: top 50 apps by memory. Click one to expand its processes.
- Process table: refreshed every 2 s. Sort, search, pause.
- Process detail window for any process: memory (RSS, unique, virtual, page faults), CPU time and threads, open file count, listening ports, program path, working folder, command (copyable), parent and children. For processes macOS will not let RMS read (other users'), it shows limited details and says so.

Try it on example snapshots: https://adminpydev.github.io/RMS/

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
rms run [--port 8765] [--no-browser]
```

`rms run` runs in the foreground and serves the page at http://127.0.0.1:8765. Ctrl+C stops it. If the port is busy, pass `--port`.

## Privacy

- Runs only on your machine. The server binds to `127.0.0.1` and rejects other Host headers.
- Nothing is written to disk. No history is kept.
- Zero background work when no page is open: data is read only when the page asks.
- Command lines are redacted best-effort: passwords, tokens, API keys, URL credentials, auth headers and cookies are masked.
- Open files are shown only as a count, never paths.
- Read-only. RMS never kills or signals processes.
- Requests from other websites are refused.
- No telemetry, no accounts.
- Over SSH, forward the same port: `ssh -L 8765:127.0.0.1:8765 host`.

The demo site shows example snapshots from a demo machine, not your computer. Browsers cannot read system RAM.

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
