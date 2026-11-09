from login_broker.matching import hosts_match

def test_exact_host_matches():
    assert hosts_match("https://example.com/login", "https://example.com") is True

def test_different_host_rejected():
    assert hosts_match("https://evil.com/login", "https://example.com") is False

def test_subdomain_rejected():
    # No wildcard/subdomain leniency — vault entry for example.com must not
    # match sub.example.com.
    assert hosts_match("https://sub.example.com/login", "https://example.com") is False

def test_port_mismatch_rejected():
    assert hosts_match("https://example.com:8443/login", "https://example.com") is False

def test_scheme_ignored_host_only():
    # Only the host is compared; scheme differences don't matter.
    assert hosts_match("http://example.com/login", "https://example.com/") is True

def test_www_and_apex_are_equivalent():
    # A leading www. is ignored: apex and its www host are the same site.
    assert hosts_match("https://example-shop.com/", "https://www.example-shop.com") is True
    assert hosts_match("https://www.example-shop.com/my-account/", "https://example-shop.com") is True

def test_www_equivalence_does_not_leak_to_other_subdomains():
    # Only www. is special; other subdomains stay rejected.
    assert hosts_match("https://shop.example.com/login", "https://www.example.com") is False

def test_www_equivalence_still_respects_ports():
    assert hosts_match("https://www.example.com:8443/login", "https://example.com") is False

def test_empty_host_never_matches():
    # A schemeless string parses to an empty netloc; it must not match.
    assert hosts_match("example.com", "example.com") is False
