# RMS

RAM Monitoring System. See what is using RAM right now and what grew in the last 30 minutes, in plain language. Everything runs on your own machine and nothing is written to disk.

## What it shows

One page:

- Insights strip: up to 4 findings, from five kinds: memory pressure, swap growth, fast grower, steady growth, big newcomer (for example "Slack (PID 812) grew 340 MB in 5 min").
- Live process table, refreshed every 2 s. Sort, search, pause. Columns include Δ 5 min and Δ 30 min.
- Process detail window for any row.

Try it on a simulated replay of sample data: https://adminpydev.github.io/RMS/

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
rms run [--interval 10] [--port 8765] [--no-browser]
```

`rms run` runs in the foreground and serves the page at http://127.0.0.1:8765. Ctrl+C stops it. If the port is busy, pass `--port`.

## Privacy

- Runs only on your machine. The server binds to `127.0.0.1` and rejects other Host headers.
- Nothing is written to disk. RMS keeps the last 30 minutes in memory only.
- Command lines are redacted best-effort: passwords, tokens, API keys, URL credentials, auth headers and cookies are masked. It prefers over-redacting.
- Read-only. RMS never kills or signals processes.
- The 2 s sampling runs only while the page is open.
- Requests from other websites are refused.
- No telemetry, no accounts. The page only talks to its own origin.
- Over SSH, forward the same port: `ssh -L 8765:127.0.0.1:8765 host`.

The demo site is a simulated replay of sample data, not your computer. Browsers cannot read system RAM.

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
