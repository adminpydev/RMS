"""Mask secrets in process command lines. Over-redaction is fine; under-redaction is a bug.

Every pattern starts only at a token boundary (lookbehind) and avoids nested
unbounded quantifiers, so matching is linear; input is also capped at _MAX chars.
"""
from __future__ import annotations

import re

_MAX = 16384
_W = r"(?:pass|pwd|secret|token|api[-_]?key|auth|credential|session|private[-_]?key|access[-_]?key)"
# value: balanced quotes, or an unterminated quote that masks to end of string, or plain chars
_V = r"""(?:"[^"]*(?:"|$)|'[^']*(?:'|$)|[^\s"'])+"""

# well-known token shapes anywhere (left boundary so e.g. "disk-cache" is safe)
_TOK = re.compile(
    r"(?<![\w-])sk-[\w-]{20,}|gh[pousr]_\w{20,}|github_pat_\w{20,}|xox[abpr]-[\w-]+"
    r"|AKIA[0-9A-Z]{16}|eyJ[\w-]+\.[\w-]+\.[\w-]+"
)
# curl -u user:pass / --user[=]user:pass: keep user, mask the rest
_USER = re.compile(
    r"""(?P<p>(?<![\w.-])(?:-u|--user|--proxy-user)(?:=|\s+)(?P<q>["'])?[^\s:"']*:)(?(q)[^"']*|[^\s"']*)"""
)
# cookies: header and flags, whole value
_COOKIE_H = re.compile(r"(?P<p>(?<![\w-])(?:set-)?cookie:\s*)[^\"'\n]+", re.I)
_COOKIE_F = re.compile(
    r"""(?P<p>(?<![\w.-])(?:-b|--cookie)(?:=|\s+))(?:"[^"]*"|'[^']*'|\S+)"""
)
# JSON body: "password": "x"  (string or bare value)
_JSON = re.compile(
    rf"""(?P<p>"(?=[\w.-]*?{_W})[\w.-]+"\s*:\s*)(?:"(?:[^"\\]|\\.)*"|[^\s,}}\]]+)""", re.I
)
# --flag=value / -Dname=value / --flag value (space form never eats a following --flag)
_FLAG = re.compile(
    rf"(?<![\w.-])(?P<f>--?(?:(?=[\w.-]*?{_W})[\w.-]+|(?:[\w.-]*[-_.])?(?:key|pw)(?![\w.-])))"
    rf"(?:=(?P<v1>{_V})?|\s+(?P<v2>(?!--){_V}))",
    re.I,
)
# NAME=value env-style / bare assignments
_ENV = re.compile(
    rf"(?<![\w.-])(?:(?=[\w.]*?{_W})|(?=(?:[\w.]*[_.])?(?:key|pw)=))(?P<n>[\w.]+)=(?:{_V}|(?=\s|$))",
    re.I,
)
# Header: value (Authorization: Bearer x, X-Api-Key: x)
_HDR = re.compile(
    rf"""(?<![\w-])(?=[\w-]*?{_W})(?P<h>[\w-]+:\s*(?:(?:bearer|basic|digest|token)\s+)?)[^\s"']+""",
    re.I,
)
_SCHEME = re.compile(r"(?<![\w+.-])[A-Za-z][\w+.-]*://")


def _flag(m):
    return f"{m['f']}{'=' if m['v1'] is not None else ' '}***"


def _url(m):
    t = m[0]
    at = t.rfind("@")
    if at < 0:
        return t
    s = _SCHEME.search(t)
    if not s or s.end() >= at:
        return t
    u = t[s.end():at]
    return f"{t[:s.end()]}{'***:***' if ':' in u else '***'}{t[at:]}"


def redact(cmdline: str | None) -> str | None:
    if cmdline is None:
        return None
    s = _TOK.sub("***", cmdline[:_MAX])
    s = _USER.sub(lambda m: f"{m['p']}***", s)
    s = re.sub(r"\S+", _url, s)
    s = _COOKIE_H.sub(lambda m: f"{m['p']}***", s)
    s = _COOKIE_F.sub(lambda m: f"{m['p']}***", s)
    s = _JSON.sub(lambda m: f'{m["p"]}"***"', s)
    s = _FLAG.sub(_flag, s)
    s = _ENV.sub(lambda m: f"{m['n']}=***", s)
    return _HDR.sub(lambda m: f"{m['h']}***", s)
