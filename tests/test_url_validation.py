"""The satellite only accepts plain public https URLs for evidence."""

import random
import re

import pytest

import assay_dossier
from genlayer_stub import UserError

validate = assay_dossier._validate_url

VALID_URLS = [
    "https://example.org",
    "https://sub.example.co.uk/path?q=1#frag",
    "HTTPS://EXAMPLE.ORG/A",
    "https://example.org:443/x",
    "https://xn--bcher-kva.example.org/x",
    "https://example.xn--p1ai",
    "https://a-b.example.io",
    "https://example.org?x=1",
    "https://example.org#top",
    "https://1password.com/",
    "https://example.org/a%20b",
    "https://example.org/path@foo",
    "https://example.org/?mail=a@b.org",
    "https://" + "a" * 63 + ".org",
    # look-alikes of blocked wildcard-DNS services are ordinary domains
    "https://notnip.io",
    "https://nip.io.example.org",
    "https://mysslip.io",
]

INVALID_URLS = {
    "scheme": [
        "http://example.org",
        "ftp://example.org",
        "example.org",
        "//example.org",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https:/example.org",
        "https:example.org",
    ],
    "empty": ["", "   "],
    "host": [
        "https://",
        "https:///x",
        "https://?x",
        "https://#x",
        "https://localhost",
        "https://localhost/x",
        "https://x.localhost",
        "https://abc",
        "https://example.org.",
        "https://.example.org",
        "https://a..b.org",
        "https://-a.org",
        "https://a-.org",
        "https://ex_ample.org",
        "https://exämple.org",
        "https://a.b",
        "https://example.123",
        "https://example.o1",
    ],
    "ip-literal": [
        "https://127.0.0.1",
        "https://10.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://2130706433",
        "https://0x7f.0.0.1",
        "https://0177.0.0.1",
        "https://1.2.3.4.5",
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
    ],
    "credentials": [
        "https://user:pw@example.org",
        "https://example.org@evil.org",
        "https://example.org\\@evil.org",
        "https://example.org%2f@evil.org",
    ],
    "port": [
        "https://example.org:8443",
        "https://example.org:80",
        "https://example.org:",
        "https://example.org:443:443",
        "https://example.org:0443",
    ],
    "characters": [
        "https://exa mple.org",
        "https://example.org/a b",
        "https://example.org\t/x",
        "https://example.org/\x00",
        "https://example.org/\x7f",
        "https://example.org/\u2215evil",
    ],
}

INVALID_CASES = [
    pytest.param(url, id=f"{category}-{i}")
    for category, urls in INVALID_URLS.items()
    for i, url in enumerate(urls)
]


@pytest.mark.parametrize("url", VALID_URLS)
def test_accepts_public_https_urls(url):
    assert validate(url) == url


@pytest.mark.parametrize("url", INVALID_CASES)
def test_rejects_unsafe_or_malformed_urls(url):
    with pytest.raises(UserError):
        validate(url)


def test_surrounding_whitespace_is_stripped_not_rejected():
    assert validate("  https://example.org/x \n") == "https://example.org/x"


def test_scheme_error_message():
    with pytest.raises(UserError, match="must start with https://"):
        validate("http://example.org")


@pytest.mark.parametrize("tld", assay_dossier.BLOCKED_TLDS)
def test_blocked_top_level_domains(tld):
    with pytest.raises(UserError, match="not allowed"):
        validate(f"https://host.{tld}/x")
    with pytest.raises(UserError, match="not allowed"):
        validate(f"https://HOST.{tld.upper()}")


@pytest.mark.parametrize("domain", assay_dossier.BLOCKED_DOMAINS)
def test_blocked_wildcard_dns_domains_and_subdomains(domain):
    for url in [
        f"https://{domain}",
        f"https://sub.{domain}/x",
        f"https://10.0.0.1.{domain}",
        f"https://{domain.upper()}",
        f"https://sub.{domain}:443/",
    ]:
        with pytest.raises(UserError, match="not allowed"):
            validate(url)


def test_url_length_limit():
    limit = assay_dossier.MAX_URL
    prefix = "https://example.org/"
    assert validate(prefix + "a" * (limit - len(prefix))) is not None
    with pytest.raises(UserError, match="between 1 and"):
        validate(prefix + "a" * (limit - len(prefix) + 1))


def test_host_label_and_total_length_limits():
    with pytest.raises(UserError):
        validate("https://" + "a" * 64 + ".org")
    with pytest.raises(UserError):
        validate("https://" + ".".join(["a" * 63] * 4) + ".org")


def test_random_urls_never_crash_and_never_accept_credentials_or_brackets():
    # Deterministic fuzz: whatever the input, the validator either accepts a
    # clean public host or fails with a UserError, never with another error.
    rng = random.Random(7)
    pieces = [
        "a", "b", "example", "org", "com", "@", ":", ".", "-", "1", "443",
        "/", "?", "#", "[", "]", "xn--", "user", "pw", "%2f", "\\", " ",
    ]
    accepted = 0
    for _ in range(5000):
        url = "https://" + "".join(rng.choice(pieces) for _ in range(rng.randint(1, 10)))
        try:
            validate(url)
        except UserError:
            continue
        accepted += 1
        authority = re.split(r"[/?#]", url.strip()[len("https://"):], maxsplit=1)[0]
        assert "@" not in authority and "[" not in authority and "\\" not in url
        if ":" in authority:
            assert authority.endswith(":443")
    assert accepted > 0  # the generator does produce some valid URLs
