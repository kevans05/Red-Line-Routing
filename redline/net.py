"""HTTP / cookie / header helpers (no GUI dependencies)."""
import base64
import ctypes
import ctypes.wintypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def _parse_request_headers_raw(raw_headers: str) -> dict:
    """Parse a raw 'Header-Name: value' block into a dict."""
    headers = {}
    for line in raw_headers.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        headers[k.strip()] = v.strip()
    return headers


def _parse_cookies_from_headers(raw_headers: str) -> dict:
    """Extract Cookie key=value pairs from a raw headers text block.

    Looks for a ``Cookie:`` line; if absent, treats the whole string as a
    raw cookie string (``name=val; name2=val2``).
    """
    raw = ""
    for line in raw_headers.splitlines():
        if line.lower().startswith("cookie:"):
            raw = line.split(":", 1)[1].strip()
            break
    if not raw:
        raw = raw_headers.strip()
    cookies = {}
    for part in re.split(r";\s*", raw):
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _update_cookie_in_headers(raw_headers: str, new_cookies: dict) -> str:
    """Rebuild the Cookie: line in raw_headers with updated values; preserve other lines."""
    new_val = "; ".join(f"{k}={v}" for k, v in new_cookies.items())
    lines_out = []
    replaced = False
    for line in raw_headers.splitlines():
        if line.lower().startswith("cookie:"):
            if not replaced:
                lines_out.append(f"Cookie: {new_val}")
                replaced = True
        else:
            lines_out.append(line)
    if not replaced and new_cookies:
        lines_out.append(f"Cookie: {new_val}")
    return "\n".join(lines_out)


def _fmt_phone(raw: str) -> str:
    """Format a phone number string. Leaves unrecognized lengths (e.g. extensions) unchanged."""
    digits = re.sub(r"\D", "", raw)
    n = len(digits)
    if n == 7:
        return f"{digits[:3]}-{digits[3:]}"
    if n == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if n == 11 and digits[0] == "1":
        return f"1-{digits[1:4]}-{digits[4:7]}-{digits[7:]}"
    return raw


def _domain_from_url(url: str) -> str:
    """Extract just the hostname from a URL, or return the raw string."""
    import urllib.parse
    try:
        return urllib.parse.urlparse(url).hostname or url
    except Exception:
        return url


def _dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt bytes using Windows CryptUnprotectData (stdlib ctypes only)."""
    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]
    inp = _BLOB(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    ok  = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError(f"CryptUnprotectData failed (error {ctypes.GetLastError()})")
    result = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return result


def _decrypt_cookie_aes_gcm(key: bytes, enc_val: bytes) -> "str | None":
    """Decrypt a Chrome/Edge v10/v20 AES-256-GCM cookie value via PowerShell."""
    if len(enc_val) < 3 + 12 + 16:
        return None
    nonce  = enc_val[3:15]
    ct_tag = enc_val[15:]
    k64 = base64.b64encode(key).decode()
    n64 = base64.b64encode(nonce).decode()
    d64 = base64.b64encode(ct_tag).decode()
    script = (
        f"$k=[Convert]::FromBase64String('{k64}');"
        f"$n=[Convert]::FromBase64String('{n64}');"
        f"$d=[Convert]::FromBase64String('{d64}');"
        "$t=$d[($d.Length-16)..($d.Length-1)];"
        "$c=$d[0..($d.Length-17)];"
        "$a=[System.Security.Cryptography.AesGcm]::new($k);"
        "$p=New-Object byte[] $c.Length;"
        "$a.Decrypt($n,$c,$t,$p);"
        "[Convert]::ToBase64String($p)"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15)
        out = r.stdout.strip()
        if r.returncode != 0 or not out:
            return None
        return base64.b64decode(out).decode("utf-8", errors="replace")
    except Exception:
        return None


def _grab_browser_cookies(domain: str) -> dict:
    """Extract cookies for *domain* from Edge (or Chrome) on Windows.

    Returns {name: value}.  Raises RuntimeError on any setup failure.
    """
    if sys.platform != "win32":
        raise RuntimeError("Browser cookie extraction is only supported on Windows.")

    local_app = os.environ.get("LOCALAPPDATA", "")
    browser_dir = None
    for candidate in (
        os.path.join(local_app, "Microsoft", "Edge", "User Data"),
        os.path.join(local_app, "Google",    "Chrome", "User Data"),
    ):
        if os.path.isdir(candidate):
            browser_dir = candidate
            break
    if browser_dir is None:
        raise RuntimeError("Could not find Edge or Chrome user data directory.")

    local_state_path = os.path.join(browser_dir, "Local State")
    with open(local_state_path, "r", encoding="utf-8") as fh:
        local_state = json.load(fh)
    enc_key_b64 = local_state["os_crypt"]["encrypted_key"]
    enc_key     = base64.b64decode(enc_key_b64)[5:]
    master_key  = _dpapi_decrypt(enc_key)

    cookies_path = None
    try:
        profiles = sorted(os.listdir(browser_dir))
    except OSError:
        profiles = []
    for profile in ["Default"] + [p for p in profiles if p.startswith("Profile")]:
        for p in (os.path.join(browser_dir, profile, "Network", "Cookies"),
                  os.path.join(browser_dir, profile, "Cookies")):
            if os.path.isfile(p):
                cookies_path = p
                break
        if cookies_path:
            break
    if cookies_path is None:
        raise RuntimeError(
            "Could not find the Edge/Chrome Cookies database.\n\n"
            f"Searched inside: {browser_dir}")

    domain_clean = domain.lstrip(".")

    def _query_cookies_db(path: str, uri: bool = False) -> list:
        conn = sqlite3.connect(path, uri=uri)
        try:
            return conn.execute(
                "SELECT name, encrypted_value FROM cookies"
                " WHERE host_key LIKE ? OR host_key LIKE ?",
                (f"%{domain_clean}%", f"%.{domain_clean}%"),
            ).fetchall()
        finally:
            conn.close()

    rows = None

    path_fwd = cookies_path.replace("\\", "/")
    prefix   = "file:///" if len(path_fwd) >= 2 and path_fwd[1] == ":" else "file://"
    try:
        rows = _query_cookies_db(prefix + path_fwd + "?immutable=1", uri=True)
    except Exception:
        pass

    if rows is None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_path = tf.name
        try:
            shutil.copy2(cookies_path, tmp_path)
            rows = _query_cookies_db(tmp_path)
        except Exception as exc:
            raise RuntimeError(
                f"Could not read the browser cookie database.\n\n"
                f"Close ALL Edge and Chrome windows (including background\n"
                f"apps in the system tray) and click Grab again.\n\n"
                f"Database: {cookies_path}\n"
                f"Detail: {exc}"
            ) from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    result = {}
    for name, enc_val in rows:
        if not enc_val:
            continue
        if enc_val[:3] in (b"v10", b"v20"):
            val = _decrypt_cookie_aes_gcm(master_key, enc_val)
        else:
            try:
                val = _dpapi_decrypt(enc_val).decode("utf-8", errors="replace")
            except Exception:
                val = None
        if val is not None:
            result[name] = val

    return result


def _ps_grab_windows_cookies(url: str) -> dict:
    """Fetch cookies via Windows Integrated Authentication (NTLM/Kerberos).

    Uses PowerShell Invoke-WebRequest with -UseDefaultCredentials so the current
    Windows domain account is used automatically — no password prompt required.
    Returns {name: value}.  Raises RuntimeError on failure.
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows authentication cookie grab requires Windows.")
    url_esc = url.replace("'", "''")
    ps = (
        f"$url = '{url_esc}'; "
        "$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession; "
        "$status = 0; "
        "try { "
        "  $r = Invoke-WebRequest -Uri $url -UseDefaultCredentials -UseBasicParsing -WebSession $session; "
        "  $status = $r.StatusCode "
        "} catch [System.Net.WebException] { "
        "  if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode } "
        "} catch { $status = -1 }; "
        "$obj = [PSCustomObject]@{ status = $status; cookies = $session.Cookies.GetCookies($url) }; "
        "Write-Host '__RLR_JSON_START__'; "
        "$obj | ConvertTo-Json -Depth 5 | Write-Host; "
        "Write-Host '__RLR_JSON_END__'"
    ).replace("\n", "")
    flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "BYPASS", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            creationflags=flags,
        )
    except FileNotFoundError:
        raise RuntimeError("PowerShell not found.")
    if r.returncode != 0 and "__RLR_JSON_START__" not in r.stdout:
        raise RuntimeError(f"PowerShell error:\n{(r.stderr or r.stdout).strip()}")
    stdout = r.stdout
    start = stdout.find("__RLR_JSON_START__")
    end   = stdout.find("__RLR_JSON_END__")
    if start == -1 or end == -1:
        raise RuntimeError(
            f"Could not locate JSON output in PowerShell response.\n\nOutput: {stdout[:300]}"
        )
    json_text = stdout[start + len("__RLR_JSON_START__"):end].strip()
    try:
        data = {k: v.replace("\r", "") if isinstance(v, str) else v
                for k, v in json.loads(json_text).items()}
    except (json.JSONDecodeError, AttributeError) as exc:
        raise RuntimeError(
            f"Could not parse PowerShell output:\n{exc}\n\nOutput: {json_text[:300]}"
        ) from exc
    raw = data.get("cookies") or []
    if isinstance(raw, dict):
        raw = [raw]
    result = {}
    for cookie in (raw if isinstance(raw, list) else []):
        name  = cookie.get("Name") or cookie.get("name", "")
        value = cookie.get("Value") if "Value" in cookie else cookie.get("value", "")
        if name:
            result[name] = (value or "").replace("\r", "")
    return result
