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
    ("curl -u bob:pw1 http://h", "curl -u bob:*** http://h"),
    ("curl --user bob:pw1 x", "curl --user bob:*** x"),
    ("curl --user=bob:pw1 x", "curl --user=bob:*** x"),
    ('curl -u "bob:pw1" x', 'curl -u "bob:***" x'),
    ("curl -u bob x", "curl -u bob x"),
    ("x --key abc", "x --key ***"),
    ("x --pw=abc", "x --pw=***"),
    ("x --pass abc", "x --pass ***"),
    ("x --db-pass abc", "x --db-pass ***"),
    ("x --ssh-key=/k", "x --ssh-key=***"),
    ("x --keyboard us", "x --keyboard us"),
    ("x --monkey 1", "x --monkey 1"),
    ("x ghp_" + "a" * 20, "x ***"),
    ("x github_pat_" + "a1_" * 8, "x ***"),
    ("x sk-ant-" + "a-" * 12, "x ***"),
    ("x xoxb-123-abc-def", "x ***"),
    ("x AKIAABCDEFGHIJKLMNOP", "x ***"),
    ("x eyJhbGc.eyJzdWI.sig_-1", "x ***"),
    ("mysql -psecret", "mysql -psecret"),  # attached short flag: ambiguous, left alone
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
