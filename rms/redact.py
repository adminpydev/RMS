"""Mask secrets in process command lines. Over-redaction is fine; under-redaction is a bug."""
import re

_W = r"(?:pass|pwd|secret|token|api[-_]?key|auth|credential|session|private[-_]?key|access[-_]?key)"
_K = r"(?:[\w.-]*[-_.])?(?:key|pw)"  # whole-word key/pw: --ssh-key yes, --keyboard no
_VAL = r"""(?:"[^"]*"|'[^']*'|\S+)"""

# --flag=value / -Dname=value / --flag value (space form never eats a following flag)
_FLAG = re.compile(
    rf"(?P<f>(?<![\w-])--?(?:[\w.-]*{_W}[\w.-]*|{_K}(?![\w.-])))(?:=(?P<v1>{_VAL}?)|\s+(?P<v2>(?!-){_VAL}))",
    re.I,
)
# NAME=value env-style / bare assignments
_ENV = re.compile(rf"(?<![\w.-])(?P<n>[\w.]*{_W}[\w.]*|(?:[\w.]*[-_.])?(?:key|pw))=(?:{_VAL}|(?=\s|$))", re.I)
# well-known token shapes anywhere in the line
_TOK = re.compile(
    r"gh[pousr]_\w{20,}|github_pat_\w{20,}|sk-[\w-]{20,}|xox[abpr]-[\w-]+|AKIA[0-9A-Z]{16}"
    r"|eyJ[\w-]+\.[\w-]+\.[\w-]+"
)
# curl -u user:pass / --user[=]user:pass: keep user, mask the rest
_USER = re.compile(r"(?P<p>(?<![\w-])(?:-u|--user|--proxy-user)(?:=|\s+)[\"']?[^\s:\"']*:)[^\s\"']*")
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
    s = _TOK.sub("***", cmdline)
    s = _USER.sub(lambda m: f"{m['p']}***", s)
    s = _URL.sub(_url, s)
    s = _FLAG.sub(_flag, s)
    s = _ENV.sub(lambda m: f"{m['n']}=***", s)
    return _HDR.sub(lambda m: f"{m['h']}***", s)
