"""Origin (host) matching between a target URL and a vault item URI.

Hosts must match exactly, with ONE deliberate relaxation: a leading `www.`
is ignored, so an apex domain and its `www` host count as the same site
(same registrable domain, same owner). No other subdomain leniency —
`sub.example.com` still never matches `example.com` — and ports must match.
An empty host (e.g. a scheme-less string, which urlparse puts in the path)
never matches anything.
"""
from urllib.parse import urlparse


def _canonical_host(url: str) -> str:
    """netloc (host[:port]), lowercased, with a single leading `www.` stripped."""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def hosts_match(target_url: str, vault_uri: str) -> bool:
    """True iff target_url and vault_uri refer to the same host, treating a
    leading `www.` as equivalent to its apex. Only the host is compared —
    scheme is ignored — but ports must match if present, and a non-www
    subdomain is never a match."""
    target = _canonical_host(target_url)
    return bool(target) and target == _canonical_host(vault_uri)
