import time

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
    ("x --pwd=a", "x --pwd=***"),
    ("x --credential a", "x --credential ***"),
    ("x --session=a", "x --session=***"),
    ("x --password -hunter2", "x --password ***"),
    ("x --token -abc123 y", "x --token *** y"),
    ("--no-auth --port 3000", "--no-auth --port 3000"),
    ("curl -d '{\"password\":\"x\"}' u", "curl -d '{\"password\":\"***\"}' u"),
    ("curl -d '{\"password\": \"x y\", \"a\": 1}' u", "curl -d '{\"password\": \"***\", \"a\": 1}' u"),
    ('curl -H "Cookie: sid=abc; b=c" u', 'curl -H "Cookie: ***" u'),
    ("curl --cookie 'sid=abc' u", "curl --cookie *** u"),
    ("curl -b sid=abc u", "curl -b *** u"),
    ('curl --data-urlencode "password=x" u', 'curl --data-urlencode "password=***'),  # closing quote looks unterminated: masks to end (over-redaction accepted),
    ('--password="x"y z', "--password=*** z"),
    ('--api-key="a b"c z', "--api-key=*** z"),
    ("--disk-cache-size-limit-bytes=5", "--disk-cache-size-limit-bytes=5"),
    ("x --disk-cache-size-limit-bytes 5", "x --disk-cache-size-limit-bytes 5"),
    ('PASSWORD="abc def', "PASSWORD=***"),
    ('TOKEN="abc', "TOKEN=***"),
    ('x -e PASSWORD="x', "x -e PASSWORD=***"),
    ('X SECRET="a b', "X SECRET=***"),
    ("API_KEY='k", "API_KEY=***"),
    ('--password=ab"cd ef', "--password=***"),
    ('DB_PASS=ab"cd', "DB_PASS=***"),
    ("x _ghp_" + "a" * 20, "x _***"),
    ("MY_ghp_" + "a" * 20, "MY_***"),
    ("x -ghp_" + "a" * 20, "x -***"),
    ("-Pghp_" + "a" * 20, "-P***"),
    ("curl -u 'a:b c' u", "curl -u 'a:***' u"),
    ('curl -u "a:b c" u', 'curl -u "a:***" u'),
    ("sshpass -p hunter2 ssh h", "sshpass -p *** ssh h"),
    ("/usr/bin/sshpass -p 'a b' ssh h", "/usr/bin/sshpass -p *** ssh h"),
    ("redis-cli -h db -a hunter2 ping", "redis-cli -h db -a *** ping"),
    ("ssh -p 2222 h", "ssh -p 2222 h"),
    ("ls -a /tmp", "ls -a /tmp"),
    ("x glpat-" + "a" * 20, "x ***"),
    ("x npm_" + "A1" * 18, "x ***"),
    ("x xapp-1-A0-123-abc", "x ***"),
    ("x hvs." + "a" * 24, "x ***"),
    ("curl https://hooks.slack.com/services/T0/B0/xyz -d x",
     "curl https://hooks.slack.com/services/*** -d x"),
    ("aws configure set aws_secret_access_key abc/123", "aws configure set aws_secret_access_key ***"),
    ("aws configure set aws_session_token tok", "aws configure set aws_session_token ***"),
    ("aws configure set region us-east-1", "aws configure set region us-east-1"),
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


@pytest.mark.parametrize("src,want", CASES)
def test_idempotent_table(src, want):
    assert redact(want) == want


def test_perf_adversarial():
    inputs = [
        "a" * 100000 + "pass",
        "x://" * 25000,
        "eyJ" + "a." * 50000,
        "--a" * 30000,
        "=" * 50000,
        "a:" * 30000 + "@",
        ".-" * 50000 + "x",
        "@" + "x://" * 25000,
        "pass" * 25000,
        'PASSWORD="' * 1600,
        "PASSWORD='" * 1600,
        "--password=a\"" * 3000,
        "-u '" + "a:" * 8000,
        "-u " * 5000 + "'",
        "_ghp_" * 3000,
        "sshpass -p " * 1600,
        "redis-cli -a " * 1300,
        "glpat-" * 2800,
        "npm_" * 4200,
        "xapp-" * 3300,
        "hvs." * 4200,
        "https://hooks.slack.com/services/" * 500,
        "aws_session_token " * 950,
        "aws configure set aws_secret_access_key '" * 400,
    ]
    total = 0.0
    for s in inputs:
        t = time.perf_counter()
        redact(s)
        dt = time.perf_counter() - t
        assert dt < 0.1, (s[:12], dt)
        total += dt
    assert total < 1.0


def test_input_capped():
    assert len(redact("a " * 50000)) <= 16384
