import pytest

from rms.redact import redact

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --type=renderer --lang=en-US"

CASES = [
    ("--password=hunter2", "--password=***"),
    ("--api-key abc123 run", "--api-key *** run"),
    ("-p", "-p"),
    ("--passwd=x", "--passwd=***"),
    ("--secret=x", "--secret=***"),
    ("--token=x", "--token=***"),
    ("--auth-token=x", "--auth-token=***"),
    ("--client_secret x", "--client_secret ***"),
    ("--access-key=x", "--access-key=***"),
    ("--private-key=/k.pem", "--private-key=***"),
    ("--PASSWORD=x", "--PASSWORD=***"),
    ('--password "a b" go', "--password *** go"),
    ("--password='a b' go", "--password=*** go"),
    ("--no-auth --port 3000", "--no-auth --port 3000"),
    ("java -Dpassword=x -jar a.jar", "java -Dpassword=*** -jar a.jar"),
    ("java -Ddb.password=x -jar a.jar", "java -Ddb.password=*** -jar a.jar"),
    ("AWS_SECRET_ACCESS_KEY=xyz node app.js", "AWS_SECRET_ACCESS_KEY=*** node app.js"),
    ("GITHUB_TOKEN=ghp_1 x", "GITHUB_TOKEN=*** x"),
    ('API_KEY="a b" x', "API_KEY=*** x"),
    ("password=x run", "password=*** run"),
    ("PATH=/usr/bin x", "PATH=/usr/bin x"),
    ("postgres://bob:pw@db:5432/x", "postgres://***:***@db:5432/x"),
    ("https://tok@host/x", "https://***@host/x"),
    ("https://example.com/a", "https://example.com/a"),
    ('curl -H "Authorization: Bearer eyJabc"', 'curl -H "Authorization: Bearer ***"'),
    ('curl -H "Authorization: Basic Zm9v"', 'curl -H "Authorization: Basic ***"'),
    ("Authorization: Basic Zm9v", "Authorization: Basic ***"),
    ('curl -H "X-Api-Key: abc123" u', 'curl -H "X-Api-Key: ***" u'),
    (CHROME, CHROME),
    ("python3 -m http.server 8000", "python3 -m http.server 8000"),
    ("node server.js --port 3000", "node server.js --port 3000"),
]


@pytest.mark.parametrize("src,want", CASES)
def test_redact_table(src, want):
    assert redact(src) == want


def test_none():
    assert redact(None) is None


def test_idempotent():
    s = redact("--password=x TOKEN=y postgres://a:b@h")
    assert redact(s) == s
