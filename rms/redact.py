"""Mask secrets in process command lines. Over-redaction is fine; under-redaction is a bug."""
import re

_W = r"(?:pass|pwd|secret|token|api[-_]?key|auth|credential|session|private[-_]?key|access[-_]?key)"
_VAL = r"""(?:"[^"]*"|'[^']*'|\S+)"""

# --flag=value / -Dname=value / --flag value (space form never eats a following flag)
_FLAG = re.compile(
    rf"(?P<f>(?<![\w-])--?[\w.-]*{_W}[\w.-]*)(?:=(?P<v1>{_VAL}?)|\s+(?P<v2>(?!-){_VAL}))",
    re.I,
)
# NAME=value env-style / bare assignments
_ENV = re.compile(rf"(?<![\w.-])(?P<n>[\w.]*{_W}[\w.]*)=(?:{_VAL}|(?=\s|$))", re.I)
# scheme://userinfo@host
_URL = re.compile(r"(?P<s>[A-Za-z][\w+.-]*://)(?P<u>[^\s@]+)@")
# Header: value (Authorization: Bearer x, X-Api-Key: x)
_HDR = re.compile(
    rf"(?P<h>(?<![\w-])[\w-]*{_W}[\w-]*:\s*(?:(?:bearer|basic|digest|token)\s+)?)[^\s\"']+",
    re.I,
)


def _flag(m):
    return f"{m['f']}{'=' if m['v1'] is not None else ' '}***"


def _url(m):
    return f"{m['s']}{'***:***' if ':' in m['u'] else '***'}@"


def redact(cmdline):
    if cmdline is None:
        return None
    s = _URL.sub(_url, cmdline)
    s = _FLAG.sub(_flag, s)
    s = _ENV.sub(lambda m: f"{m['n']}=***", s)
    return _HDR.sub(lambda m: f"{m['h']}***", s)
