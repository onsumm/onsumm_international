"""
Onsumm Tally Connector Agent  (Windows-first)
──────────────────────────────────────────────
Distributed as a single OnsummTallyAgent.exe built with PyInstaller.
Customer double-clicks → setup UI → running forever in system tray.

Windows-specific features:
  • Auto-start on login via HKCU Run registry key (no admin needed)
  • Optional Windows Service install  (--install-service, needs admin once)
  • Self-update: downloads new .exe, schedules replacement via .bat on reboot
  • System tray icon with status + right-click menu
  • Logs to %APPDATA%\\OnsummTallyAgent\\agent.log (rotating, 5 MB × 5)
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ── Constants ──────────────────────────────────────────────────────────────────

AGENT_VERSION    = "1.2.1"
# Rewritten by the CI build per branch (uat2 → dev, everything else → prod).
BUILD_SERVER_URL = "https://api.onsumm.ai"

POLL_INTERVAL    = int(os.getenv("TALLY_POLL_INTERVAL", "900"))   # 15 min default
# Tally host/port: env > config.json > default. Remote/cloud Tally (RemoteApp, Tally on
# Cloud) listens on the provider's server, so the host is configurable.
TALLY_HOST       = "localhost"
TALLY_PORT       = 9000
TALLY_TIMEOUT    = 30

APP_NAME         = "OnsummTallyAgent"
REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

IS_WINDOWS = platform.system() == "Windows"


# ── Paths ──────────────────────────────────────────────────────────────────────

def _app_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


APP_DIR     = _app_dir()
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH    = APP_DIR / "agent.log"


def _server_url() -> str:
    """ONSUMM_API_URL env > server_url saved at pairing > the build's default."""
    env = os.getenv("ONSUMM_API_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("server_url", "")
    except Exception:
        saved = ""
    return (saved or BUILD_SERVER_URL).rstrip("/")


CLOUD_BASE_URL   = _server_url()


def _load_tally_target() -> None:
    global TALLY_HOST, TALLY_PORT
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    TALLY_HOST = (os.getenv("TALLY_HOST") or saved.get("tally_host") or "localhost").strip()
    try:
        TALLY_PORT = int(os.getenv("TALLY_PORT") or saved.get("tally_port") or 9000)
    except ValueError:
        TALLY_PORT = 9000


_load_tally_target()


def tally_is_local() -> bool:
    return TALLY_HOST.lower() in ("localhost", "127.0.0.1", "::1")


def set_tally_target(host: str, port: int, company: str | None = None) -> None:
    """Change where the agent reads Tally from, and remember it in config.json."""
    global TALLY_HOST, TALLY_PORT
    TALLY_HOST, TALLY_PORT = (host or "localhost").strip(), int(port)
    cfg = load_config()
    cfg["tally_host"], cfg["tally_port"] = TALLY_HOST, TALLY_PORT
    if company is not None:
        cfg["tally_company"] = company.strip()
    save_config(cfg)
    log.info("Tally target set to %s:%d (company=%r)", TALLY_HOST, TALLY_PORT, cfg.get("tally_company", ""))
UPDATE_CHECK_URL = f"{CLOUD_BASE_URL}/agent/version"
INGEST_URL       = f"{CLOUD_BASE_URL}/api/tally/ingest"
PAIR_URL         = f"{CLOUD_BASE_URL}/api/tally/pair"


# ── Logging ────────────────────────────────────────────────────────────────────

from logging.handlers import RotatingFileHandler  # noqa: E402

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] [%(threadName)s] %(funcName)s:%(lineno)d  %(message)s"
_handlers: list[logging.Handler] = [
    # 5 MB × 5 files — enough history to debug a failed week without filling the disk
    RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"),
]
if sys.stdout is not None:          # --windowed PyInstaller builds have no console
    _handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(level=logging.DEBUG if os.getenv("ONSUMM_DEBUG") else logging.INFO,
                    format=_LOG_FORMAT, handlers=_handlers)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("onsumm")


def _log_uncaught(exc_type, exc, tb) -> None:
    log.critical("Uncaught exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))


def _log_uncaught_thread(args) -> None:
    log.critical("Uncaught exception in thread %s:\n%s",
                 args.thread.name if args.thread else "?",
                 "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


sys.excepthook       = _log_uncaught
threading.excepthook = _log_uncaught_thread


def _mask(secret: str, keep: int = 6) -> str:
    """Show only the first few chars of a token/key in logs and error text."""
    secret = secret or ""
    return secret[:keep] + "…" if len(secret) > keep else "…"


# ── Errors — every failure carries enough context to debug from agent.log ─────

MAX_BODY_IN_ERROR = 4000   # characters of the server/Tally response kept in the error


class AgentError(Exception):
    """
    headline: one line for the UI / tray.
    details:  everything known — request, status, response body, cause.
    """

    def __init__(self, headline: str, details: str = ""):
        super().__init__(headline)
        self.headline = headline
        self.details  = details

    def __str__(self) -> str:
        return f"{self.headline}\n{self.details}" if self.details else self.headline


class CloudError(AgentError):
    def __init__(self, headline: str, details: str = "", status: int | None = None,
                 server_detail: str = ""):
        super().__init__(headline, details)
        self.status        = status
        self.server_detail = server_detail


class TallyError(AgentError):
    pass


def _body_excerpt(text: str) -> str:
    text = text or ""
    if len(text) > MAX_BODY_IN_ERROR:
        return text[:MAX_BODY_IN_ERROR] + f"\n… [{len(text) - MAX_BODY_IN_ERROR} more chars truncated]"
    return text


def _cloud_request(method: str, url: str, *, what: str, timeout: float, **kw) -> dict:
    """
    One call to the Onsumm API. Logs the request and outcome, and turns every
    failure into a CloudError that names the URL, status, request_id and the
    server's own message.
    """
    started = time.monotonic()
    log.info("→ %s %s (%s)", method, url, what)
    try:
        resp = requests.request(method, url, timeout=timeout, **kw)
    except requests.exceptions.SSLError as e:
        raise CloudError(f"Secure connection to {CLOUD_BASE_URL} failed while {what}.",
                         f"URL: {method} {url}\nSSL error: {e!r}\n"
                         "A proxy or antivirus may be intercepting HTTPS, or the PC clock is wrong.") from e
    except requests.exceptions.Timeout as e:
        raise CloudError(f"Onsumm server did not answer within {timeout:.0f}s while {what}.",
                         f"URL: {method} {url}\nError: {e!r}") from e
    except requests.exceptions.ConnectionError as e:
        raise CloudError(f"Cannot reach the Onsumm server ({CLOUD_BASE_URL}) while {what}.",
                         f"URL: {method} {url}\nError: {e!r}\n"
                         "Check the internet connection, firewall or proxy on this PC.") from e
    except requests.exceptions.RequestException as e:
        raise CloudError(f"Request to Onsumm failed while {what}.",
                         f"URL: {method} {url}\nError: {e!r}") from e

    elapsed_ms = (time.monotonic() - started) * 1000
    request_id = resp.headers.get("x-request-id", "")
    body_text  = resp.text or ""
    payload: object = None
    try:
        payload = resp.json()
    except ValueError:
        pass
    if isinstance(payload, dict):
        request_id = request_id or str(payload.get("request_id", "") or "")

    log.info("← %s %s → HTTP %d in %.0f ms%s", method, url, resp.status_code, elapsed_ms,
             f" (request_id {request_id})" if request_id else "")

    if resp.ok:
        if not isinstance(payload, dict):
            raise CloudError(f"Onsumm server sent an unexpected reply while {what}.",
                             f"URL: {method} {url}\nHTTP {resp.status_code}, "
                             f"Content-Type: {resp.headers.get('content-type', '?')}\n"
                             f"Body:\n{_body_excerpt(body_text)}")
        return payload

    server_detail = ""
    if isinstance(payload, dict):
        d = payload.get("detail", "")
        server_detail = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
    details = (
        f"URL: {method} {url}\n"
        f"HTTP {resp.status_code} {resp.reason or ''} in {elapsed_ms:.0f} ms\n"
        + (f"request_id: {request_id}\n" if request_id else "")
        + (f"Server message: {server_detail}\n" if server_detail else "")
        + f"Response body:\n{_body_excerpt(body_text)}"
    )
    log.error("Cloud call failed (%s):\n%s", what, details)
    raise CloudError(
        f"Onsumm server returned HTTP {resp.status_code} while {what}"
        + (f": {server_detail}" if server_detail else "."),
        details, status=resp.status_code, server_detail=server_detail,
    )


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Windows: auto-start on login (no admin needed) ────────────────────────────

def _set_autostart(enable: bool) -> None:
    """Add/remove agent from HKCU\\...\\Run — no admin rights needed."""
    if not IS_WINDOWS:
        return
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY,
            0, winreg.KEY_SET_VALUE,
        )
        if enable:
            # --minimized: on login, sync from the tray without popping the dashboard up
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
            log.info("Auto-start enabled in registry")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                log.info("Auto-start removed from registry")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        log.warning("Could not set auto-start: %s", e)


def _is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        key  = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY)
        val, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return sys.executable in val
    except Exception:
        return False


# ── Windows Service support (optional, needs admin once) ──────────────────────

def install_windows_service() -> None:
    """
    Install agent as a Windows Service that starts automatically at boot
    (even without any user logged in). Requires admin rights.
    Run once: OnsummTallyAgent.exe --install-service
    """
    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager

        class _AgentService(win32serviceutil.ServiceFramework):
            _svc_name_        = APP_NAME
            _svc_display_name_ = "Onsumm Tally Connector"
            _svc_description_  = "Syncs Tally Prime data to Onsumm AP Automation platform."

            def __init__(self, args):
                super().__init__(args)
                self._stop = win32event.CreateEvent(None, 0, 0, None)

            def SvcStop(self):
                self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                win32event.SetEvent(self._stop)

            def SvcDoRun(self):
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_INFORMATION_TYPE,
                    servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""),
                )
                cfg = load_config()
                if cfg.get("api_key"):
                    t = threading.Thread(target=run_background_loop, args=(cfg,), daemon=True)
                    t.start()
                win32event.WaitForSingleObject(self._stop, win32event.INFINITE)

        win32serviceutil.HandleCommandLine(_AgentService)

    except ImportError:
        log.error("pywin32 not available — cannot install Windows Service")
        print("ERROR: pywin32 is not bundled. Use --autostart instead.")
        sys.exit(1)


# ── Self-update ────────────────────────────────────────────────────────────────

def check_and_apply_update() -> None:
    """
    Check cloud for newer agent version.
    On Windows, can't replace a running .exe directly — writes a helper .bat
    that swaps the file and relaunches after this process exits.
    """
    try:
        info    = _cloud_request("GET", UPDATE_CHECK_URL, what="checking for updates", timeout=10)
        latest  = info.get("version", "0.0.0")
        dl_url  = info.get("download_url", "")

        if not dl_url or latest <= AGENT_VERSION:
            log.info("Agent v%s is up to date", AGENT_VERSION)
            return

        log.info("Update available: %s → %s. Downloading...", AGENT_VERSION, latest)

        exe_path = Path(sys.executable)
        tmp_exe  = exe_path.with_suffix(".new.exe")

        with requests.get(dl_url, stream=True, timeout=120) as dl:
            dl.raise_for_status()
            with open(tmp_exe, "wb") as f:
                for chunk in dl.iter_content(65536):
                    f.write(chunk)

        if IS_WINDOWS:
            # Can't overwrite running .exe on Windows — use a .bat trampoline
            bat_path = exe_path.with_suffix(".update.bat")
            bat_path.write_text(
                f'@echo off\n'
                f'ping 127.0.0.1 -n 3 > nul\n'          # wait 2s for this process to exit
                f'move /y "{tmp_exe}" "{exe_path}"\n'
                f'start "" "{exe_path}"\n'
                f'del "%~f0"\n',                          # self-delete the bat
                encoding="utf-8",
            )
            log.info("Update staged. Relaunching via updater bat...")
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            shutil.move(str(tmp_exe), str(exe_path))
            os.chmod(str(exe_path), 0o755)
            subprocess.Popen([str(exe_path)] + sys.argv[1:])

        sys.exit(0)

    except CloudError as e:
        log.warning("Update check skipped: %s", e)
    except Exception:
        log.warning("Update check failed:\n%s", traceback.format_exc())


# ── Tally XML calls ────────────────────────────────────────────────────────────

def _tally_post(xml: str, what: str = "querying Tally") -> str:
    url = f"http://{TALLY_HOST}:{TALLY_PORT}"
    started = time.monotonic()
    try:
        resp = requests.post(url, data=xml.encode("utf-8"),
                             headers={"Content-Type": "application/xml"}, timeout=TALLY_TIMEOUT)
    except requests.exceptions.ReadTimeout as e:
        raise TallyError(f"Tally did not answer within {TALLY_TIMEOUT}s while {what}.",
                         f"URL: {url}\nError: {e!r}\nTally accepted the connection but is busy (large company) "
                         "or stuck on a dialog/popup — check the Tally window.") from e
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
        if tally_is_local():
            hint = ("Check on this PC: is TallyPrime open with a company loaded, and is "
                    f"F1 → Settings → Connectivity → Client/Server set to 'Both' with port {TALLY_PORT}?\n"
                    "If Tally runs through Remote App / Tally on Cloud, it is on the provider's server, not this PC: "
                    "run this agent on that server, or have the provider open the port to this PC and set the "
                    "Tally host under Tally settings.")
        else:
            hint = (f"{TALLY_HOST}:{TALLY_PORT} did not accept the connection from this PC. Check that Tally on that "
                    "machine has Client/Server = 'Both' on this port, and that its firewall / VPN allows this PC.")
        raise TallyError(f"Cannot reach Tally at {TALLY_HOST}:{TALLY_PORT} while {what}.",
                         f"URL: {url}\nError: {e!r}\n{hint}") from e
    elapsed_ms = (time.monotonic() - started) * 1000
    log.debug("Tally %s → HTTP %d, %d bytes in %.0f ms", what, resp.status_code, len(resp.content), elapsed_ms)
    if not resp.ok:
        raise TallyError(f"Tally returned HTTP {resp.status_code} while {what}.",
                         f"URL: {url}\nResponse body:\n{_body_excerpt(resp.text)}")
    body_up = resp.text.upper()
    if "LICENSE SERVER IS RUNNING" in body_up:
        # Port 9999 on Tally servers is the License Server / Gateway: it answers every
        # request with this status page and never has company data.
        raise TallyError(
            f"{TALLY_HOST}:{TALLY_PORT} is the Tally License Server, not TallyPrime's data port.",
            f"URL: {url}\nThe License Server (usually port 9999) only reports licence status. Use the port "
            "set in TallyPrime → F1 → Settings → Connectivity → Client/Server configuration (default 9000), "
            "on the machine where TallyPrime itself runs.\n"
            f"Response:\n{_body_excerpt(resp.text)}")
    if "<ENVELOPE" not in body_up and "<RESPONSE" not in body_up:
        raise TallyError(
            f"{TALLY_HOST}:{TALLY_PORT} answered, but not with Tally data while {what}.",
            f"URL: {url}\nExpected Tally XML (<ENVELOPE>…). Another program may be using this port.\n"
            f"Response:\n{_body_excerpt(resp.text)}")
    if "<LINEERROR>" in body_up:
        raise TallyError(f"Tally reported an error while {what}.",
                         f"URL: {url}\nResponse body:\n{_body_excerpt(resp.text)}")
    return resp.text


def tally_is_running() -> bool:
    try:
        _tally_post(what="checking Tally is running", xml="""
        <ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
        <BODY><EXPORTDATA><REQUESTDESC>
          <REPORTNAME>List of Companies</REPORTNAME>
          <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
        </REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>""")
        return True
    except TallyError as e:
        log.warning("%s", e)
        return False


def fetch_ledgers(company: str = "") -> str:
    svc = f"<SVCNAME>{company}</SVCNAME>" if company else ""
    return _tally_post(f"""
    <ENVELOPE>
      <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{svc}</HEADER>
      <BODY><EXPORTDATA><REQUESTDESC>
        <REPORTNAME>List of Accounts</REPORTNAME>
        <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
      </REQUESTDESC></EXPORTDATA></BODY>
    </ENVELOPE>""")


def fetch_vouchers(from_date: str, to_date: str, company: str = "") -> str:
    svc = f"<SVCNAME>{company}</SVCNAME>" if company else ""
    xml = _tally_post(f"""
    <ENVELOPE>
      <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{svc}</HEADER>
      <BODY><EXPORTDATA><REQUESTDESC>
        <REPORTNAME>Voucher Register</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          <SVFROMDATE>{from_date}</SVFROMDATE>
          <SVTODATE>{to_date}</SVTODATE>
        </STATICVARIABLES>
      </REQUESTDESC></EXPORTDATA></BODY>
    </ENVELOPE>""")
    # Fallback to Day Book if Voucher Register returns empty
    if "<VOUCHER" not in xml.upper():
        fallback = _tally_post(f"""
        <ENVELOPE>
          <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{svc}</HEADER>
          <BODY><EXPORTDATA><REQUESTDESC>
            <REPORTNAME>Day Book</REPORTNAME>
            <STATICVARIABLES>
              <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
              <SVFROMDATE>{from_date}</SVFROMDATE>
              <SVTODATE>{to_date}</SVTODATE>
            </STATICVARIABLES>
          </REQUESTDESC></EXPORTDATA></BODY>
        </ENVELOPE>""")
        if "<VOUCHER" in fallback.upper():
            return fallback
    return xml


# ── Cloud push ─────────────────────────────────────────────────────────────────

def push_to_cloud(api_key: str, ledgers_xml: str, vouchers_xml: str) -> dict:
    log.info("Uploading %d KB ledgers + %d KB vouchers (api_key %s)",
             len(ledgers_xml) // 1024, len(vouchers_xml) // 1024, _mask(api_key))
    try:
        return _cloud_request(
            "POST", INGEST_URL, what="uploading Tally data", timeout=120,
            json={
                "agent_version": AGENT_VERSION,
                "ledgers_xml":   ledgers_xml,
                "vouchers_xml":  vouchers_xml,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except CloudError as e:
        if e.status == 401:
            e.headline = ("Onsumm rejected this agent's key (HTTP 401) — the connection was reset "
                          "or re-paired elsewhere. Click Re-pair… and paste a new token.")
            e.details += f"\nConfig file: {CONFIG_PATH}"
        raise


def pair_with_token(token: str) -> dict:
    token = token.strip()
    log.info("Pairing with %s using token %s (%d chars)", CLOUD_BASE_URL, _mask(token), len(token))
    try:
        data = _cloud_request(
            "POST", PAIR_URL, what="pairing", timeout=20,
            json={"token": token, "agent_version": AGENT_VERSION},
        )
    except CloudError as e:
        if e.status == 400:
            e.headline = (f"Pairing token rejected: {e.server_detail or 'invalid or expired'}. "
                          f"Make sure the token was generated on the same Onsumm environment "
                          f"this agent connects to ({CLOUD_BASE_URL}), then generate a new one.")
        elif e.status and e.status >= 500:
            e.headline = (f"Onsumm server error (HTTP {e.status}) while pairing"
                          + (f": {e.server_detail}" if e.server_detail else "")
                          + ". Send the details below to support@onsumm.ai.")
        e.details = f"Token: {_mask(token)} ({len(token)} chars)\n" + e.details
        raise
    if "api_key" not in data:
        raise CloudError("Onsumm accepted the token but sent no api_key.",
                         f"URL: POST {PAIR_URL}\nReply keys: {sorted(data)}")
    log.info("Paired OK: tenant=%s", data.get("tenant_name", "?"))
    return data  # { "api_key": "...", "tenant_name": "..." }


# ── Local view of Tally data (for the dashboard — the cloud re-parses on its own) ─

import re as _re                          # noqa: E402
import xml.etree.ElementTree as _ET       # noqa: E402

_BAD_XML_CHARS = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SYNC_WINDOW_DAYS = int(os.getenv("TALLY_SYNC_DAYS", "90"))
AGENT_STATUS_URL = f"{CLOUD_BASE_URL}/api/tally/agent/status"
HISTORY_PATH     = APP_DIR / "history.json"
MAX_HISTORY      = 200


def _parse_xml(xml_text: str, what: str):
    """Tally emits control chars and &#4;-style refs that strict XML rejects — strip them first."""
    text = _BAD_XML_CHARS.sub("", xml_text or "")
    text = _re.sub(r"&#x([0-9a-fA-F]+);", lambda m: "" if int(m.group(1), 16) < 32 else m.group(0), text)
    text = _re.sub(r"&#([0-9]+);", lambda m: "" if int(m.group(1)) < 32 else m.group(0), text)
    try:
        return _ET.fromstring(text.encode("utf-8", errors="replace"))
    except _ET.ParseError as e:
        raise AgentError(f"Could not read the {what} XML from Tally: {e}",
                         f"First 1000 chars:\n{text[:1000]}") from e


def _txt(el, tag: str) -> str:
    node = el.find(tag)
    return node.text.strip() if node is not None and node.text else ""


def _amt(raw: str) -> float:
    raw = (raw or "").strip().replace(",", "")
    sign = 1
    if raw.endswith(" Cr"):
        raw = raw[:-3]
    elif raw.endswith(" Dr"):
        raw, sign = raw[:-3], -1
    try:
        return sign * float(raw)
    except ValueError:
        return 0.0


# Same buckets as tally_parser.parse_vouchers on the server
_KIND_BY_TYPE = {"SALES": "Invoice", "PURCHASE": "Bill", "PAYMENT": "Payment", "RECEIPT": "Payment"}


def parse_vouchers_local(xml_text: str) -> list[dict]:
    root = _parse_xml(xml_text, "voucher")
    rows = []
    for v in root.iter("VOUCHER"):
        vtype = _txt(v, "VOUCHERTYPENAME") or v.get("VCHTYPE", "")
        party = _txt(v, "PARTYLEDGERNAME")
        total = tax = 0.0
        biggest = 0.0
        for e in v.findall(".//ALLLEDGERENTRIES.LIST") + v.findall(".//LEDGERENTRIES.LIST"):
            amount = _amt(_txt(e, "AMOUNT"))
            lname  = _txt(e, "LEDGERNAME")
            biggest = max(biggest, abs(amount))
            if any(t in lname.upper() for t in ("GST", "IGST", "CGST", "SGST", "TAX")):
                tax += abs(amount)
            elif lname == party:
                total = abs(amount)
        d = _txt(v, "DATE")
        rows.append({
            "date":      f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d,
            "kind":      _KIND_BY_TYPE.get(vtype.upper(), "Not synced"),
            "type":      vtype,
            "number":    _txt(v, "VOUCHERNUMBER"),
            "party":     party,
            "gstin":     _txt(v, "PARTYGSTIN"),
            "amount":    total or biggest,
            "tax":       tax,
            "narration": _txt(v, "NARRATION"),
            "items":     len(v.findall(".//ALLINVENTORYENTRIES.LIST") or v.findall(".//INVENTORYENTRIES.LIST")),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def parse_ledgers_local(xml_text: str) -> list[dict]:
    root = _parse_xml(xml_text, "ledger")
    rows = []
    for led in root.iter("LEDGER"):
        parent = _txt(led, "PARENT")
        rows.append({
            "name":   led.get("NAME", "").strip() or _txt(led, "NAME"),
            "group":  parent,
            "gstin":  _txt(led, "GSTIN") or _txt(led, "PARTYGSTIN"),
            "party":  "Supplier" if parent == "Sundry Creditors" else
                      "Customer" if parent == "Sundry Debtors" else "",
            "phone":  _txt(led, "LEDGERMOBILE"),
            "email":  _txt(led, "EMAIL"),
        })
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def tally_companies() -> list[str]:
    xml = _tally_post(what="listing Tally companies", xml="""
        <ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
        <BODY><EXPORTDATA><REQUESTDESC>
          <REPORTNAME>List of Companies</REPORTNAME>
          <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
        </REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>""")
    names = _re.findall(r'<COMPANY[^>]*\bNAME="([^"]+)"', xml) or \
            _re.findall(r"<(?:SVCURRENTCOMPANY|COMPANYNAME|NAME)[^>]*>([^<]+)</", xml)
    seen, out = set(), []
    for n in (x.strip() for x in names):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ── Shared agent state (written by worker threads, read by the dashboard) ─────

class AgentState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cloud: dict = {"state": "unknown"}     # unknown | ok | error
        self.tally: dict = {"state": "unknown"}
        self.sync:  dict = {"running": False, "step": "Idle", "last": None}
        self.next_sync_at: float | None = None
        self.vouchers: list[dict] = []
        self.ledgers:  list[dict] = []
        self.fetched_at: str = ""
        self.last_error: dict | None = None          # {at, headline, details}
        self.row_errors: list[str] = []
        self.history: list[dict] = self._load_history()
        self.consecutive_failures = 0

    @staticmethod
    def _load_history() -> list[dict]:
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))[-MAX_HISTORY:]
        except Exception:
            return []

    def add_history(self, entry: dict) -> None:
        with self.lock:
            self.history.append(entry)
            self.history = self.history[-MAX_HISTORY:]
            try:
                HISTORY_PATH.write_text(json.dumps(self.history, indent=1), encoding="utf-8")
            except Exception as e:
                log.warning("Could not save sync history: %s", e)

    def set_error(self, headline: str, details: str) -> None:
        with self.lock:
            self.last_error = {"at": datetime.now().strftime("%d %b %H:%M:%S"),
                               "headline": headline, "details": details}

    def tray_line(self) -> str:
        with self.lock:
            last = self.sync.get("last")
            if self.sync["running"]:
                return f"Syncing: {self.sync['step']}"
            if last:
                return last["summary"]
            return "Not synced yet"

    def healthy(self) -> bool:
        with self.lock:
            last = self.sync.get("last")
            return self.cloud.get("state") != "error" and (last is None or last["ok"])


STATE = AgentState()
_sync_now   = threading.Event()   # set → the sync thread runs a cycle immediately
_stop_event = threading.Event()


def check_cloud(cfg: dict) -> None:
    """Reachability + key validity + what the cloud has stored for this tenant."""
    started = time.monotonic()
    try:
        status = _cloud_request("GET", AGENT_STATUS_URL, what="checking the connection", timeout=15,
                                headers={"Authorization": f"Bearer {cfg.get('api_key', '')}"})
        with STATE.lock:
            STATE.cloud = {"state": "ok", "checked_at": datetime.now().strftime("%H:%M:%S"),
                           "latency_ms": (time.monotonic() - started) * 1000, "key_verified": True, **status}
    except CloudError as e:
        if e.status == 404:
            # Server without the status endpoint yet — fall back to a plain reachability check
            try:
                _cloud_request("GET", UPDATE_CHECK_URL, what="checking the server", timeout=10)
                with STATE.lock:
                    STATE.cloud = {"state": "ok", "checked_at": datetime.now().strftime("%H:%M:%S"),
                                   "latency_ms": (time.monotonic() - started) * 1000, "key_verified": False,
                                   "note": "Server reachable; it does not report connection details yet, "
                                           "so the key is checked on the next upload."}
                return
            except CloudError as e2:
                e = e2
        with STATE.lock:
            STATE.cloud = {"state": "error", "checked_at": datetime.now().strftime("%H:%M:%S"),
                           "error": e.headline, "status": e.status}
        STATE.set_error(e.headline, e.details)


def check_tally() -> None:
    started = time.monotonic()
    try:
        companies = tally_companies()
        with STATE.lock:
            STATE.tally = {"state": "ok", "checked_at": datetime.now().strftime("%H:%M:%S"),
                           "latency_ms": (time.monotonic() - started) * 1000, "companies": companies}
    except TallyError as e:
        with STATE.lock:
            STATE.tally = {"state": "error", "checked_at": datetime.now().strftime("%H:%M:%S"),
                           "error": e.headline}
        STATE.set_error(e.headline, e.details)


def _step(text: str) -> None:
    log.info("Sync step: %s", text)
    with STATE.lock:
        STATE.sync["step"] = text


def run_sync_cycle(cfg: dict) -> dict:
    """One full sync. Records every step, count and failure in STATE and history."""
    company = cfg.get("tally_company", "")
    api_key = cfg["api_key"]
    started_wall = datetime.now()
    started = time.monotonic()
    entry = {"started": started_wall.strftime("%Y-%m-%d %H:%M:%S"), "ok": False,
             "trigger": cfg.get("_trigger", "scheduled")}
    with STATE.lock:
        STATE.sync.update(running=True, step="Starting")
    try:
        _step(f"Checking Tally at {TALLY_HOST}:{TALLY_PORT}")
        check_tally()
        if STATE.tally.get("state") != "ok":
            raise TallyError(STATE.tally.get("error", "Tally not reachable"),
                             (STATE.last_error or {}).get("details", ""))
        open_companies = STATE.tally.get("companies") or []
        entry["tally"] = f"{TALLY_HOST}:{TALLY_PORT}"
        if company and open_companies and company not in open_companies:
            # Shared RemoteApp/cloud servers: another customer's Tally can be on this port
            raise TallyError(
                f"Company '{company}' is not open in the Tally at {TALLY_HOST}:{TALLY_PORT} — sync skipped.",
                f"Companies open there: {', '.join(open_companies)}\n"
                "Open the right company in Tally, or pick it under Tally settings. If none of these are yours, "
                "this port belongs to a different Tally (common on shared cloud servers) — ask your provider "
                "for the port of your own Tally session.")
        log.info("Tally at %s:%d has companies %s; syncing %r",
                 TALLY_HOST, TALLY_PORT, open_companies, company or "<open company>")

        _step("Fetching ledgers from Tally")
        t0 = time.monotonic()
        ledgers_xml = fetch_ledgers(company)
        entry["ledgers_kb"] = len(ledgers_xml) // 1024
        entry["tally_ledgers_ms"] = round((time.monotonic() - t0) * 1000)

        to_date   = date.today()
        from_date = to_date - timedelta(days=SYNC_WINDOW_DAYS)
        _step(f"Fetching vouchers {from_date:%d %b} → {to_date:%d %b %Y}")
        t0 = time.monotonic()
        vouchers_xml = fetch_vouchers(from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d"), company)
        entry["vouchers_kb"] = len(vouchers_xml) // 1024
        entry["tally_vouchers_ms"] = round((time.monotonic() - t0) * 1000)

        _step("Reading fetched data")
        try:
            vouchers = parse_vouchers_local(vouchers_xml)
            ledgers  = parse_ledgers_local(ledgers_xml)
            with STATE.lock:
                STATE.vouchers, STATE.ledgers = vouchers, ledgers
                STATE.fetched_at = datetime.now().strftime("%d %b %Y %H:%M:%S")
            by_kind: dict[str, int] = {}
            for v in vouchers:
                by_kind[v["kind"]] = by_kind.get(v["kind"], 0) + 1
            entry["fetched"] = {"vouchers": len(vouchers), "ledgers": len(ledgers), **by_kind}
            log.info("Fetched from Tally: %s", entry["fetched"])
        except AgentError as e:
            # If the agent can't read it as Tally XML, the server can't either — don't upload it
            raise TallyError(f"Tally's reply could not be read as Tally data — nothing uploaded. {e.headline}",
                             e.details) from e

        _step(f"Uploading {entry['ledgers_kb'] + entry['vouchers_kb']} KB to Onsumm")
        t0 = time.monotonic()
        result = push_to_cloud(api_key, ledgers_xml, vouchers_xml)
        entry["upload_ms"] = round((time.monotonic() - t0) * 1000)
        entry["saved"]  = {k: result.get(k, 0) for k in ("invoices", "bills", "payments", "ledgers", "errors")}
        entry["parsed"] = result.get("parsed", {})
        with STATE.lock:
            STATE.row_errors = list(result.get("error_samples", []))
        entry["ok"] = True
        s = entry["saved"]
        entry["summary"] = (f"Synced {started_wall:%d %b %H:%M} — {s['bills']} bills, {s['invoices']} invoices, "
                            f"{s['payments']} payments, {s['ledgers']} ledgers"
                            + (f", {s['errors']} row errors" if s["errors"] else ""))
        with STATE.lock:
            STATE.consecutive_failures = 0
        _step("Refreshing cloud totals")
        check_cloud(cfg)
        return result
    except AgentError as e:
        entry["error"] = e.headline
        entry["summary"] = f"Failed {started_wall:%d %b %H:%M} — {e.headline}"
        if isinstance(e, CloudError) and e.status in (401, 403):
            with STATE.lock:
                STATE.cloud.update(state="error", error=e.headline, key_verified=False,
                                   checked_at=datetime.now().strftime("%H:%M:%S"))
        STATE.set_error(e.headline, e.details)
        with STATE.lock:
            STATE.consecutive_failures += 1
        log.error("Sync failed: %s", e)
        raise
    except Exception as ex:
        tb = traceback.format_exc()
        entry["error"] = f"{type(ex).__name__}: {ex}"
        entry["summary"] = f"Crashed {started_wall:%d %b %H:%M} — {entry['error']}"
        STATE.set_error(f"Sync crashed: {type(ex).__name__}: {ex}", tb)
        with STATE.lock:
            STATE.consecutive_failures += 1
        log.error("Sync crashed:\n%s", tb)
        raise
    finally:
        entry["duration_s"] = round(time.monotonic() - started, 1)
        STATE.add_history(entry)
        with STATE.lock:
            STATE.sync.update(running=False, step="Idle", last=entry)
        log.info("Sync finished in %.1fs: %s", entry["duration_s"], entry.get("summary"))


def request_sync(trigger: str = "manual") -> None:
    """Ask the sync thread to run now (never runs two cycles at once)."""
    log.info("Sync requested (%s)", trigger)
    _sync_now.trigger = trigger  # type: ignore[attr-defined]
    _sync_now.set()


def run_background_loop(cfg: dict) -> None:
    CIRCUIT_BREAKER = 5
    check_cloud(cfg)
    while not _stop_event.is_set():
        trigger = getattr(_sync_now, "trigger", "scheduled") if _sync_now.is_set() else "scheduled"
        _sync_now.clear()
        try:
            run_sync_cycle({**cfg, "_trigger": trigger})
        except Exception:
            pass   # already recorded in STATE, history and agent.log

        wait = POLL_INTERVAL
        if STATE.consecutive_failures >= CIRCUIT_BREAKER:
            log.error("Circuit breaker: %d failures in a row — next automatic try in 1 hour",
                      STATE.consecutive_failures)
            wait = 3600
        with STATE.lock:
            STATE.next_sync_at = time.time() + wait
        _sync_now.wait(timeout=wait)


# ── Setup UI (Tkinter — bundled inside Python, zero extra deps) ────────────────

def run_setup_ui() -> dict | None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return _headless_setup()

    result: dict = {}
    root = tk.Tk()
    root.title("Onsumm Tally Connector — Setup")
    root.geometry("560x700")
    root.minsize(520, 640)

    # Center on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth()  // 2) - 280
    y = (root.winfo_screenheight() // 2) - 350
    root.geometry(f"+{x}+{y}")

    frame = tk.Frame(root, padx=28, pady=22)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Onsumm Tally Connector", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(frame, text="Connect your Tally Prime to Onsumm AP Automation",
             font=("Segoe UI", 9), fg="#555").pack(anchor="w", pady=(2, 4))
    tk.Label(frame, text=f"Server: {CLOUD_BASE_URL}   ·   Agent v{AGENT_VERSION}",
             font=("Segoe UI", 8), fg="#888").pack(anchor="w", pady=(0, 14))

    # Pairing token
    tk.Label(frame, text="Pairing Token  *", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(frame,
             text="From your Onsumm dashboard → Settings → Connect Tally → Generate Token",
             font=("Segoe UI", 8), fg="#666").pack(anchor="w")
    token_var = tk.StringVar()
    token_ent = tk.Entry(frame, textvariable=token_var, width=52, font=("Consolas", 10))
    token_ent.pack(anchor="w", pady=(4, 14))
    token_ent.focus()

    # Company name
    tk.Label(frame, text="Tally Company Name  (optional)", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(frame, text="Leave blank to use the company currently open in Tally",
             font=("Segoe UI", 8), fg="#666").pack(anchor="w")
    company_var = tk.StringVar()
    tk.Entry(frame, textvariable=company_var, width=52).pack(anchor="w", pady=(4, 14))

    # Where Tally listens. Leave localhost unless Tally runs on another machine (Remote App / cloud).
    tk.Label(frame, text="Tally host and port", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(frame, text="Keep localhost : 9000 when Tally runs on this PC. For Remote App / Tally on Cloud, "
                         "use the server your provider gives you.",
             font=("Segoe UI", 8), fg="#666", wraplength=490, justify="left").pack(anchor="w")
    hostrow = tk.Frame(frame)
    hostrow.pack(anchor="w", pady=(4, 14))
    host_var = tk.StringVar(value=TALLY_HOST)
    port_var = tk.StringVar(value=str(TALLY_PORT))
    tk.Entry(hostrow, textvariable=host_var, width=36).pack(side="left")
    tk.Label(hostrow, text=" : ").pack(side="left")
    tk.Entry(hostrow, textvariable=port_var, width=8).pack(side="left")

    # Auto-start checkbox
    autostart_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        frame, text="Start automatically when Windows starts (recommended)",
        variable=autostart_var, font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(0, 14))

    status_var = tk.StringVar()
    status_lbl = tk.Label(frame, textvariable=status_var, fg="#c00", font=("Segoe UI", 9, "bold"),
                          wraplength=490, justify="left")
    status_lbl.pack(anchor="w")

    # Full error details — selectable, scrollable, copyable. Hidden until an error happens.
    details_frame = tk.Frame(frame)
    details_txt = tk.Text(details_frame, height=9, wrap="word", font=("Consolas", 8),
                          bg="#f8f8f8", relief="solid", bd=1)
    details_scroll = tk.Scrollbar(details_frame, command=details_txt.yview)
    details_txt.config(yscrollcommand=details_scroll.set)
    details_scroll.pack(side="right", fill="y")
    details_txt.pack(side="left", fill="both", expand=True)
    details_btns = tk.Frame(frame)

    def copy_details():
        root.clipboard_clear()
        root.clipboard_append(details_txt.get("1.0", "end").strip())
        status_var.set(status_var.get().split("  (copied")[0] + "  (copied to clipboard)")

    def open_log():
        if IS_WINDOWS:
            os.startfile(str(LOG_PATH))

    tk.Button(details_btns, text="Copy details", command=copy_details).pack(side="left")
    tk.Button(details_btns, text="Open log file", command=open_log).pack(side="left", padx=(8, 0))

    def show_error(headline: str, details: str) -> None:
        status_var.set(headline)
        full = (f"{headline}\n\n{details}\n\n"
                f"Agent v{AGENT_VERSION} · {platform.platform()} · server {CLOUD_BASE_URL}\n"
                f"Log file: {LOG_PATH}")
        details_txt.config(state="normal")
        details_txt.delete("1.0", "end")
        details_txt.insert("1.0", full)
        details_txt.config(state="disabled")
        details_frame.pack(anchor="w", fill="both", expand=True, pady=(6, 4))
        details_btns.pack(anchor="w", pady=(0, 4))
        connect_btn.config(state="normal")

    def clear_error() -> None:
        details_frame.pack_forget()
        details_btns.pack_forget()

    def on_connect():
        token = token_var.get().strip()
        if not token:
            status_var.set("Please enter your pairing token.")
            return
        try:
            port = int(port_var.get().strip() or "9000")
        except ValueError:
            status_var.set("Tally port must be a number, e.g. 9000.")
            return
        clear_error()
        status_var.set(f"Connecting to {CLOUD_BASE_URL} …")
        connect_btn.config(state="disabled")
        root.update()

        try:
            data = pair_with_token(token)
            cfg = {
                "api_key":       data["api_key"],
                "tally_company": company_var.get().strip(),
                "tenant_name":   data.get("tenant_name", ""),
                "paired_at":     str(date.today()),
                "server_url":    CLOUD_BASE_URL,
            }
            save_config(cfg)
            set_tally_target(host_var.get(), port)
            cfg = load_config()
            if autostart_var.get():
                _set_autostart(True)
            result.update(cfg)
            messagebox.showinfo(
                "Connected!",
                f"Successfully connected to {cfg['tenant_name'] or 'Onsumm'}.\n\n"
                "The dashboard opens next and runs the first sync.\n"
                "Closing it keeps the agent syncing from the system tray (bottom-right).",
            )
            root.destroy()
        except AgentError as e:
            log.error("Pairing failed: %s", e)
            show_error(e.headline, e.details)
        except Exception as ex:
            tb = traceback.format_exc()
            log.error("Pairing crashed:\n%s", tb)
            show_error(f"Unexpected error while pairing: {type(ex).__name__}: {ex}", tb)

    connect_btn = tk.Button(
        frame, text="Connect to Onsumm", command=on_connect,
        bg="#2563eb", fg="white", font=("Segoe UI", 10, "bold"),
        padx=18, pady=7, relief="flat", cursor="hand2", bd=0,
        activebackground="#1d4ed8", activeforeground="white",
    )
    connect_btn.pack(anchor="w", pady=(10, 0))

    root.mainloop()
    return result if result else None


def _headless_setup() -> dict | None:
    token = os.getenv("ONSUMM_TOKEN", "").strip()
    if not token:
        log.error("No ONSUMM_TOKEN env var. Cannot pair in headless mode.")
        return None
    try:
        data = pair_with_token(token)
        cfg  = {
            "api_key":       data["api_key"],
            "tally_company": os.getenv("TALLY_COMPANY", ""),
            "tenant_name":   data.get("tenant_name", ""),
            "paired_at":     str(date.today()),
            "server_url":    CLOUD_BASE_URL,
        }
        save_config(cfg)
        _set_autostart(True)
        log.info("Headless pair OK: tenant=%s", cfg["tenant_name"])
        return cfg
    except AgentError as e:
        log.error("Headless pairing failed: %s", e)
        return None
    except Exception:
        log.error("Headless pairing crashed:\n%s", traceback.format_exc())
        return None


# ── Windows system tray icon ───────────────────────────────────────────────────

def start_tray(on_open, on_quit) -> bool:
    """
    System-tray icon (Windows). Runs detached so Tk keeps the main thread.
    Left-click / "Open dashboard" shows the window; the tooltip shows the
    latest sync result; the colour turns red when something is failing.
    """
    if not IS_WINDOWS:
        return False
    try:
        from PIL import Image, ImageDraw
        import pystray
    except ImportError:
        log.warning("Tray unavailable (pystray/Pillow missing) — dashboard only")
        return False

    def _icon_image(ok: bool):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, 60, 60], fill="#22c55e" if ok else "#ef4444")
        d.text((24, 22), "O", fill="white")
        return img

    icon = pystray.Icon(
        APP_NAME, _icon_image(True), f"Onsumm Tally Agent v{AGENT_VERSION}",
        menu=pystray.Menu(
            pystray.MenuItem("Open dashboard", lambda *_: on_open(), default=True),
            pystray.MenuItem("Sync now",       lambda *_: request_sync("tray")),
            pystray.MenuItem("Open log file",  lambda *_: os.startfile(str(LOG_PATH))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Disable auto-start", lambda *_: _set_autostart(False)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: (icon.stop(), on_quit())),
        ),
    )

    def _refresh():
        while not _stop_event.is_set():
            try:
                # Windows caps tray tooltips at 128 chars; the full text is in the dashboard/log
                icon.title = f"Onsumm Tally Agent\n{STATE.tray_line()}"[:127]
                icon.icon  = _icon_image(STATE.healthy())
            except Exception:
                log.debug("Tray refresh failed", exc_info=True)
            _stop_event.wait(5)
        icon.stop()

    icon.run_detached()
    threading.Thread(target=_refresh, daemon=True, name="tray").start()
    return True


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 78)
    log.info("Onsumm Tally Agent v%s starting | args=%s", AGENT_VERSION, sys.argv[1:])
    log.info("Server: %s (build default %s%s)", CLOUD_BASE_URL, BUILD_SERVER_URL,
             ", overridden by ONSUMM_API_URL" if os.getenv("ONSUMM_API_URL") else "")
    log.info("OS: %s | Python %s | frozen=%s | exe=%s",
             platform.platform(), sys.version.split()[0], getattr(sys, "frozen", False), sys.executable)
    log.info("Config: %s (exists=%s) | Log: %s | Tally %s:%d | poll %ds",
             CONFIG_PATH, CONFIG_PATH.exists(), LOG_PATH, TALLY_HOST, TALLY_PORT, POLL_INTERVAL)

    # Windows Service install (requires admin — run once by IT admin)
    if "--install-service" in sys.argv:
        install_windows_service()
        return

    # Remove from startup
    if "--remove-autostart" in sys.argv:
        _set_autostart(False)
        print("Auto-start removed.")
        return

    # 1. Self-update check
    check_and_apply_update()

    # 2. Load or create config
    cfg = load_config()
    if not cfg.get("api_key"):
        log.info("No config — launching setup")
        cfg = _headless_setup() if "--headless" in sys.argv else run_setup_ui()
        if not cfg:
            log.error("Setup cancelled. Exiting.")
            sys.exit(1)

    log.info("Tenant: %s | Poll: %ds | Tally: %s:%d | Log: %s",
             cfg.get("tenant_name", "?"), POLL_INTERVAL, TALLY_HOST, TALLY_PORT, LOG_PATH)

    # 3. Background sync loop (first cycle runs straight away)
    threading.Thread(target=run_background_loop, args=(cfg,), daemon=True, name="sync").start()

    # 4. Dashboard window + tray. --minimized (used by auto-start) starts in the tray only.
    try:
        import dashboard
    except Exception:
        log.error("Dashboard unavailable — running in the background only:\n%s", traceback.format_exc())
        start_tray(lambda: None, _stop_event.set)
        while not _stop_event.is_set():
            _stop_event.wait(5)
        return

    has_tray = start_tray(lambda: dashboard.UI_QUEUE.put("show"), lambda: dashboard.UI_QUEUE.put("quit"))
    ui = dashboard.Dashboard(sys.modules[__name__], cfg,
                             start_hidden="--minimized" in sys.argv and has_tray)
    ui.run()
    _stop_event.set()
    _sync_now.set()
    log.info("Agent stopped")


if __name__ == "__main__":
    main()
