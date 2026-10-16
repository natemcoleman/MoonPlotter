"""HTTP downloads that also work behind antivirus / proxy HTTPS inspection.

Python's built-in TLS (OpenSSL) can reject a certificate chain that Windows itself trusts - typically when
security software re-signs HTTPS traffic with its own root certificate ("self-signed certificate in
certificate chain"). We try, in order:
  1. urllib with Python's default trust store,
  2. urllib with the 'truststore' package (Windows / macOS certificate store), if installed,
  3. Windows' own curl.exe (uses the Windows certificate store via Schannel),
  4. PowerShell Invoke-WebRequest (same).
"""
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

UA = "TopoPlotterStudio/1.0 (personal pen-plotter maps)"


class HTTPStatusError(IOError):
    def __init__(self, code, url):
        super().__init__(f"HTTP {code} for {url}")
        self.code = code


def _urllib(url, timeout, context=None):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise HTTPStatusError(e.code, url)


def _is_cert_error(e):
    txt = f"{type(e).__name__} {e} {getattr(e, 'reason', '')}"
    return isinstance(e, ssl.SSLError) or 'CERTIFICATE_VERIFY_FAILED' in txt or 'certificate' in txt.lower()


def _curl_exe(url, timeout):
    exe = shutil.which('curl.exe') or (os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32',
                                                    'curl.exe') if sys.platform == 'win32' else None)
    if not exe or not os.path.exists(exe):
        return None
    flags = 0x08000000 if sys.platform == 'win32' else 0        # CREATE_NO_WINDOW
    p = subprocess.run([exe, '-sS', '-L', '--max-time', str(int(timeout)), '-A', UA, '-w', '\n%{http_code}', url],
                       capture_output=True, timeout=timeout + 15, creationflags=flags)
    if p.returncode != 0:
        raise IOError(f"curl.exe failed: {p.stderr.decode(errors='replace').strip()[:300]}")
    body, _, code = p.stdout.rpartition(b'\n')
    code = int(code.strip() or 0)
    if code >= 400:
        raise HTTPStatusError(code, url)
    return body


def _powershell(url, timeout):
    if sys.platform != 'win32':
        return None
    cmd = ("$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = 'Tls12,Tls13'; "
           f"$r = Invoke-WebRequest -UseBasicParsing -TimeoutSec {int(timeout)} -UserAgent '{UA}' -Uri '{url}'; "
           "[Console]::OpenStandardOutput().Write($r.RawContentStream.ToArray(), 0, $r.RawContentLength)")
    p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd], capture_output=True,
                       timeout=timeout + 30, creationflags=0x08000000)
    if p.returncode != 0:
        raise IOError(f"PowerShell download failed: {p.stderr.decode(errors='replace').strip()[:300]}")
    return p.stdout


_PREFERRED = None          # remembers which method worked, so later downloads skip the failing ones


def fetch_bytes(url, timeout=60):
    """Downloads url and returns the body bytes. Raises HTTPStatusError for 4xx/5xx, IOError otherwise."""
    global _PREFERRED
    methods = [('urllib', lambda: _urllib(url, timeout))]
    try:
        import truststore                                         # optional: pip install truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        methods.append(('truststore', lambda: _urllib(url, timeout, ctx)))
    except Exception:
        pass
    methods += [('curl.exe', lambda: _curl_exe(url, timeout)), ('powershell', lambda: _powershell(url, timeout))]
    if _PREFERRED:
        methods.sort(key=lambda m: m[0] != _PREFERRED)
    errors = []
    for name, fn in methods:
        try:
            data = fn()
        except HTTPStatusError:
            raise                                                 # the server answered: don't retry elsewhere
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            if name == 'urllib' and not _is_cert_error(e):
                break                                             # real network problem, not certificates
            continue
        if data is None:
            continue
        _PREFERRED = name
        return data
    raise IOError("Download failed.\n" + "\n".join(errors[:4]))
