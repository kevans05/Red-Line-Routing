#!/usr/bin/env python3
"""auth_probe.py — confirm how a corporate web system authenticates.

Makes an *unauthenticated* request (no cookies) that does **not** follow
redirects, then reads the server's first response. Because we send no
credentials and never auto-negotiate, the server's real challenge is
surfaced — including the ``WWW-Authenticate: Negotiate`` / ``NTLM`` that a
browser answers silently and therefore hides from its Network tab.

Each URL is classified as one of:

  WINDOWS INTEGRATED  401 WWW-Authenticate: Negotiate / NTLM  -> Kerberos/NTLM
  ENTRA / CLOUD       redirect to login.microsoftonline.com, or Bearer challenge
  ADFS / FEDERATION   redirect to an ADFS endpoint
  BASIC AUTH          401 WWW-Authenticate: Basic
  FORMS / SSO GATEWAY redirect or 200 to an internal login page (cookie-based)
  OPEN / NO AUTH      200 with no authentication signals

…and each verdict is mapped to what it means for the future sync server's
document **relay** (see docs/remote-collaboration-design.md §4.4 / §9.4).

Pure Python standard library — no pip installs, nothing to download. It only
*reads* response headers: it sends no credentials, follows no redirects, and
uploads nothing.

Usage:
    python3 auth_probe.py https://drawings.corp.example.com/search
    python3 auth_probe.py --from-settings        # probe URLs saved in ~/.redlinerouting.db
    python3 auth_probe.py --insecure https://...  # skip TLS verify (internal CA) — diagnostic only
    python3 auth_probe.py --selftest             # validate the classifier, no network
"""

import argparse
import json
import os
import sqlite3
import ssl
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

_UA = "RedLine-auth-probe/1.0 (diagnostic; reads headers only)"
_DB = os.path.expanduser("~/.redlinerouting.db")

# Hostnames that mean "Microsoft cloud identity".
_ENTRA_HOSTS = ("login.microsoftonline.com", "login.microsoft.com",
                "login.windows.net", "login.microsoftonline.us")

# Set-Cookie name fingerprints -> a human label.
_COOKIE_FINGERPRINTS = {
    "ESTSAUTH": "Entra", "ESTSAUTHPERSISTENT": "Entra", "AADSSO": "Entra",
    "SignInStateCookie": "Entra", "FedAuth": "SharePoint Online", "rtFa": "SharePoint Online",
    "MSISAuth": "ADFS", "MSISAuthenticated": "ADFS",
    "SMSESSION": "SiteMinder gateway", "OAMAuthnCookie": "Oracle Access Manager",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects so the 3xx itself (and its Location) is visible."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ──────────────────────────────────────────────────────────────────────────
# Classification — a pure function so it can be unit-tested without a network.
# ──────────────────────────────────────────────────────────────────────────
def classify(status, www_authenticate, location, set_cookie_names, body_snippet):
    """Return a dict: {label, mechanism, relay} from the observed signals."""
    wa = (www_authenticate or "").lower()
    loc = location or ""
    loc_host = urllib.parse.urlsplit(loc).netloc.lower()
    body = (body_snippet or "").lower()

    # 1) An explicit WWW-Authenticate challenge is the strongest signal.
    if wa:
        if "negotiate" in wa:
            return {"label": "WINDOWS INTEGRATED (Kerberos/NTLM)",
                    "mechanism": "WWW-Authenticate: Negotiate" + (" + NTLM" if "ntlm" in wa else ""),
                    "relay": "KCD + gMSA feasible — server can fetch AS the user, no stored creds."}
        if "ntlm" in wa:
            return {"label": "WINDOWS INTEGRATED (NTLM only)",
                    "mechanism": "WWW-Authenticate: NTLM",
                    "relay": "NTLM can't be delegated -> no KCD. Use cookie-forwarding or a service account."}
        if "bearer" in wa:
            entra = any(h in wa for h in ("login.microsoftonline", "login.microsoft", "login.windows.net"))
            return {"label": "ENTRA / OAUTH (Bearer)" if entra else "OAUTH / BEARER",
                    "mechanism": "WWW-Authenticate: Bearer",
                    "relay": "OAuth On-Behalf-Of / Graph — server calls the API as the user."}
        if "basic" in wa:
            return {"label": "BASIC AUTH",
                    "mechanism": "WWW-Authenticate: Basic",
                    "relay": "Service account with stored credentials (Basic sends them every call)."}
        return {"label": "AUTH CHALLENGE (other)", "mechanism": "WWW-Authenticate: " + www_authenticate,
                "relay": "Inspect the scheme to decide."}

    # 2) A redirect for an unauthenticated user — where does it send them?
    if status in (301, 302, 303, 307, 308) and loc:
        if any(h in loc_host for h in _ENTRA_HOSTS) or "login.microsoftonline" in loc.lower():
            return {"label": "ENTRA / CLOUD", "mechanism": f"redirect -> {loc_host or loc}",
                    "relay": "Cloud identity -> OAuth On-Behalf-Of / Microsoft Graph."}
        if "/adfs/" in loc.lower() or loc_host.startswith(("adfs.", "sts.", "fs.")):
            return {"label": "ADFS / FEDERATION", "mechanism": f"redirect -> {loc_host or loc}",
                    "relay": "On-prem federation, usually Kerberos-backed -> KCD/gMSA likely."}
        where = loc_host or "(same host) " + loc
        return {"label": "FORMS / SSO GATEWAY", "mechanism": f"redirect -> {where}",
                "relay": "Cookie/session login -> relay by forwarding the user's session cookie (or a service login)."}

    # 3) A 200 — real content, or a login form served inline?
    if status == 200:
        if 'type="password"' in body or "name=\"password\"" in body or "type=password" in body:
            return {"label": "FORMS LOGIN (inline)", "mechanism": "200 serving a username/password form",
                    "relay": "Cookie/session login -> forward the user's session cookie (or a service login)."}
        if set_cookie_names:
            return {"label": "SESSION / COOKIE (already authed or open)",
                    "mechanism": "200 + Set-Cookie, no challenge",
                    "relay": "Cookie/session based -> forward the session cookie. (Point at a PROTECTED path to be sure.)"}
        return {"label": "OPEN / NO AUTH", "mechanism": "200, no auth signals",
                "relay": "No auth seen — likely a public/landing page. Point at a protected path."}

    # 4) Anything else is inconclusive.
    return {"label": f"INCONCLUSIVE (HTTP {status})", "mechanism": f"status {status}, no challenge/redirect",
            "relay": "Point at the actual protected resource (e.g. the drawing-search endpoint)."}


# ──────────────────────────────────────────────────────────────────────────
# Network probe
# ──────────────────────────────────────────────────────────────────────────
def _extract(headers):
    wa = ", ".join(headers.get_all("WWW-Authenticate") or []) or None
    loc = headers.get("Location")
    names = []
    for sc in headers.get_all("Set-Cookie") or []:
        name = sc.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return wa, loc, names, headers.get("Server")


def probe(url, insecure=False, timeout=10):
    """Return (status, www_auth, location, cookie_names, server, body, error)."""
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        resp = opener.open(req, timeout=timeout)
        body = resp.read(4096).decode("utf-8", "replace")
        wa, loc, names, server = _extract(resp.headers)
        return resp.status, wa, loc, names, server, body, None
    except urllib.error.HTTPError as e:                       # 3xx/401/4xx/5xx surface here
        try:
            body = e.read(4096).decode("utf-8", "replace")
        except Exception:
            body = ""
        wa, loc, names, server = _extract(e.headers)
        return e.code, wa, loc, names, server, body, None
    except urllib.error.URLError as e:
        reason = e.reason
        hint = ""
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
            hint = "  (TLS verify failed — internal CA? re-run with --insecure to read headers anyway)"
        return None, None, None, None, None, None, f"{reason}{hint}"
    except (socket.timeout, TimeoutError):
        return None, None, None, None, None, None, f"timed out after {timeout}s"
    except Exception as e:                                    # noqa: BLE001 — diagnostic, report anything
        return None, None, None, None, None, None, str(e)


# ──────────────────────────────────────────────────────────────────────────
# Settings loader (optional convenience)
# ──────────────────────────────────────────────────────────────────────────
def urls_from_settings(db_path=_DB):
    """Pull URL-ish values out of the app's global config DB."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    out = []
    for key, raw in rows:
        try:
            val = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(val, str) and val.lower().startswith(("http://", "https://")):
            out.append((key, val))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────────────────
def report(label_for, url, status, wa, loc, names, server, body, error):
    print(f"\n  {label_for + ': ' if label_for else ''}{url}")
    if error:
        print(f"    [ERROR] {error}")
        return
    verdict = classify(status, wa, loc, names, body)
    print(f"    ==> {verdict['label']}")
    print(f"        why : {verdict['mechanism']}")
    print(f"        relay: {verdict['relay']}")
    print(f"        seen : HTTP {status}"
          + (f" | WWW-Authenticate: {wa}" if wa else "")
          + (f" | Location: {loc}" if loc else ""))
    if names:
        tagged = [f"{n} [{_COOKIE_FINGERPRINTS[n]}]" if n in _COOKIE_FINGERPRINTS else n for n in names]
        print(f"        cookies: {', '.join(tagged)}")
    if server:
        print(f"        server : {server}")


# ──────────────────────────────────────────────────────────────────────────
# Self-test (no network) — validates the classifier
# ──────────────────────────────────────────────────────────────────────────
_CASES = [
    ("Kerberos/NTLM", (401, "Negotiate, NTLM", None, [], ""), "WINDOWS INTEGRATED (Kerberos/NTLM)"),
    ("NTLM only",     (401, "NTLM", None, [], ""), "WINDOWS INTEGRATED (NTLM only)"),
    ("Basic",         (401, "Basic realm=x", None, [], ""), "BASIC AUTH"),
    ("Entra redirect",(302, None, "https://login.microsoftonline.com/x", ["ESTSAUTH"], ""), "ENTRA / CLOUD"),
    ("ADFS redirect", (302, None, "https://adfs.corp.example.com/adfs/ls/?x", ["MSISAuth"], ""), "ADFS / FEDERATION"),
    ("Forms redirect",(302, None, "/login?returnUrl=/x", ["APPSESSION"], ""), "FORMS / SSO GATEWAY"),
    ("Inline form",   (200, None, None, ["APPSESSION"], '<input type="password">'), "FORMS LOGIN (inline)"),
    ("Open page",     (200, None, None, [], "<h1>welcome</h1>"), "OPEN / NO AUTH"),
]


def selftest():
    ok = True
    for name, args, expected in _CASES:
        got = classify(*args)["label"]
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{status}] {name:<16} -> {got}")
    print("\n  all classifier cases passed." if ok else "\n  SOME CASES FAILED.")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Confirm how a corporate web system authenticates.")
    ap.add_argument("urls", nargs="*", help="URL(s) to probe — ideally a protected path, not a landing page")
    ap.add_argument("--from-settings", action="store_true", help="also probe URLs saved in ~/.redlinerouting.db")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (internal CA) — diagnostic only")
    ap.add_argument("--timeout", type=float, default=10, help="per-request timeout in seconds (default 10)")
    ap.add_argument("--selftest", action="store_true", help="validate the classifier offline and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    targets = [("", u) for u in args.urls]
    if args.from_settings:
        targets += urls_from_settings()
    if not targets:
        ap.error("give one or more URLs, or use --from-settings")

    print("auth_probe — unauthenticated, no-redirect header read (sends no credentials, uploads nothing)")
    if args.insecure:
        print("  ! TLS verification DISABLED (--insecure) — diagnostic only")
    for label, url in targets:
        s, wa, loc, names, server, body, err = probe(url, insecure=args.insecure, timeout=args.timeout)
        report(label, url, s, wa, loc, names, server, body, err)
    print("\nReminder: point at a PROTECTED resource (e.g. the drawing-search endpoint), not a public landing page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
