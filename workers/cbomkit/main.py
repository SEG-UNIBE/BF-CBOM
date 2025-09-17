import json
import logging
import os
import re
import threading
import time
from urllib import error as urlerror, parse, request

import websocket

from common.config import GITHUB_TOKEN
from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC"))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def _base_url() -> str:
    # Default to local dev server if not provided
    base = os.getenv("CBOMKIT_BASE_URL")
    return base.strip().rstrip("/")


def _http_get_json(url: str, timeout: int = 30):
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read().decode(charset, errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = None
            return resp.status, data, text
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, None, body
    except Exception as e:
        return None, None, str(e)


def _http_post_json(url: str, payload: dict, timeout: int = 30):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read().decode(charset, errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = None
            return resp.status, data, text
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, None, body
    except Exception as e:
        return None, None, str(e)


def _http_delete(url: str, timeout: int = 15):
    req = request.Request(url, method="DELETE")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, None, ""
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, None, body
    except Exception as e:
        return None, None, str(e)


def _repo_host_path(git_url: str) -> str:
    # Normalize to github.com/org/repo
    url = git_url.strip()
    # Remove .git suffix
    if url.endswith(".git"):
        url = url[:-4]
    host_path = ""
    if url.startswith("http://") or url.startswith("https://"):
        try:
            parts = parse.urlsplit(url)
            host_path = parts.netloc + parts.path
        except Exception:
            host_path = url
    elif url.startswith("git@"):
        # e.g., git@github.com:org/repo
        try:
            host_path = url.split("@", 1)[1].replace(":", "/", 1)
        except Exception:
            host_path = url
    else:
        host_path = url
    return host_path.strip("/")


def _repo_id_from_git_url(git_url: str) -> str:
    # Percent-encoded project id for path param
    return parse.quote(_repo_host_path(git_url), safe="")


class CbomKitClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or _base_url()).rstrip("/")
        # Persistent WebSocket session configuration (fixed, no env needed)
        self.client_id = "cbomkitclient"
        # WebSocket endpoint template; supports {clientId} placeholder
        self.ws_url_tmpl = "ws://127.0.0.1:8081/v1/scan/{clientId}"
        self.ws_url = self._make_ws_url(self.client_id)
        # Where to fetch the freshly generated CBOM (latest 1)
        self.last_url = f"{self.base}/api/v1/cbom/last/1"
        # Polling interval seconds
        self.poll_interval = 2.0
        # Internal WS state
        self._ws_app: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_connected = False
        self._ws_error: str | None = None
        self._lock = threading.Lock()
        self._finished_evt = threading.Event()
        self._last_purl: str | None = None

    def _make_ws_url(self, client_id: str) -> str:
        u = self.ws_url_tmpl
        if "{clientId}" in u:
            return u.replace("{clientId}", client_id)
        if "{jobId}" in u:
            return u.replace("{jobId}", client_id)
        # If template ends with /v1/scan[/], append client_id
        if u.endswith("/v1/scan") or u.endswith("/v1/scan/"):
            return u.rstrip("/") + "/" + client_id
        return u

    def _ensure_ws(self, timeout_sec: float = 3.0) -> bool:
        # Reuse only if thread is alive and flags are healthy
        if (
            self._ws_app
            and self._ws_thread is not None
            and self._ws_thread.is_alive()
            and self._ws_connected
            and not self._ws_error
        ):
            return True

        # Reset state
        self._ws_error = None
        self._ws_connected = False
        self._finished_evt.clear()

        def on_open(ws):
            # Give server a brief moment to complete @OnOpen
            time.sleep(0.1)
            self._ws_connected = True

        def on_message(ws, message):
            try:
                obj = json.loads(message)
            except Exception:
                obj = None
            txt = (obj or {}).get("message") if isinstance(obj, dict) else None
            if txt:
                # Try to capture the stored PURL with commit, optionally with ?branch=...
                try:
                    m = re.search(
                        r"(pkg:github/[\w.-]+/[\w.-]+@[0-9a-fA-F]+(?:\?branch=[\w.-]+)?)",
                        txt,
                    )
                    if m:
                        self._last_purl = m.group(1)
                        logger.info("Captured stored CBOM id: %s", self._last_purl)
                except Exception:
                    pass
                if txt.strip() == "Finished":
                    self._finished_evt.set()

        def on_error(ws, err):
            self._ws_error = f"ws_error:{err}"

        def on_close(ws, *args):
            self._ws_connected = False

        self._ws_app = websocket.WebSocketApp(
            self.ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws_app.run_forever,
            # Ensure ping_interval > ping_timeout per websocket-client requirement
            kwargs={"ping_interval": 30, "ping_timeout": 10},
            daemon=True,
        )
        self._ws_thread.start()

        # Wait for connect or error
        start = time.monotonic()
        while time.monotonic() - start < timeout_sec:
            if self._ws_connected:
                logger.info("Websocket connected")
                return True
            if self._ws_error:
                break
            time.sleep(0.05)
        return False

    def get_cbom(self, repo_id: str):
        url = f"{self.base}/api/v1/cbom/{repo_id}"
        return _http_get_json(url, timeout=30)

    def get_last_cboms(self, n: int = 1):
        if n == 1 and self.last_url:
            return _http_get_json(self.last_url, timeout=30)
        url = f"{self.base}/api/v1/cbom/last/{max(1, n)}"
        return _http_get_json(url, timeout=30)

    # ---- Normalization helpers ----

    def _normalize_cbom_text(self, text: str) -> str:
        """Best-effort normalize CBOM JSON to avoid duplicated components.

        If the payload is {"bom": {...}}, normalization happens inside "bom".
        """
        try:
            obj = json.loads(text or "{}")
        except Exception:
            return text
        target = obj.get("bom") if isinstance(obj, dict) and isinstance(obj.get("bom"), dict) else obj
        try:
            comps = target.get("components") if isinstance(target, dict) else None
            if isinstance(comps, list):
                seen: set[str] = set()
                uniq: list = []
                for c in comps:
                    if isinstance(c, dict):
                        key = c.get("bom-ref") or (
                            c.get("type"),
                            c.get("name"),
                            json.dumps(
                                c.get("cryptoProperties", {}),
                                sort_keys=True,
                                ensure_ascii=False,
                            ),
                        )
                    else:
                        key = json.dumps(c, sort_keys=True, ensure_ascii=False)
                    k = json.dumps(key, sort_keys=True, ensure_ascii=False)
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(c)
                target["components"] = uniq
        except Exception:
            pass
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return text

    def _get_last_raw(self) -> str | None:
        """Return raw JSON/text of last/1 if available, else None."""
        try:
            st, data, text = self.get_last_cboms(1)
            if st == 200 and (data is not None or text):
                return text if data is None else json.dumps(data)
        except Exception as e:
            logger.debug("get_last_raw error: %s", e)
        return None

    # ---- ProjectIdentifier reconstruction helpers ----
    def _owner_repo_from_giturl(self, git_url: str) -> tuple[str | None, str | None, str | None]:
        try:
            parts = parse.urlsplit(git_url.strip())
            host = (parts.netloc or "").lower()
            path = (parts.path or "").strip("/")
            segs = path.split("/")
            if host == "" and git_url.startswith("git@"):
                # git@github.com:owner/repo(.git)
                after_at = git_url.split("@", 1)[1]
                host_path = after_at.replace(":", "/", 1)
                host = host_path.split("/", 1)[0].lower()
                segs = host_path.split("/", 1)[1].strip("/").split("/")
            if len(segs) >= 2:
                owner = segs[0].lower()
                repo = segs[1].lower().removesuffix(".git")
                return host, owner, repo
        except Exception as e:
            logger.debug("owner_repo parse failed: %s", e)
        return None, None, None

    def _purl_namespace_name(self, git_url: str) -> tuple[str | None, str | None]:
        host, owner, repo = self._owner_repo_from_giturl(git_url)
        if not owner or not repo:
            return None, None
        if host == "github.com":
            namespace = owner
        else:
            namespace = f"{host}/{owner}"
        return namespace, repo

    def _github_head_sha(self, owner: str, repo: str, branch: str) -> str | None:
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "cbomkitclient/worker-cbomkit",
            }
            if GITHUB_TOKEN:
                headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                sha = data.get("sha")
                if isinstance(sha, str) and len(sha) >= 7:
                    return sha[:7]
        except Exception as e:
            logger.debug("github head sha fetch failed: %s", e)
        return None

    def _build_purl_candidates(self, namespace: str, name: str, branch: str, sha7: str | None) -> list[str]:
        base = f"pkg:github/{namespace}/{name}"
        qs = f"?branch={branch}" if branch and branch.lower() != "main" else ""
        cands: list[str] = []
        if sha7:
            # Prefer short 7-char commit, with and without branch qualifier
            cands.append(f"{base}@{sha7}{qs}")
            if qs:
                cands.append(f"{base}@{sha7}")
        # Some builds may omit commit in id; keep as final fallback
        cands.append(f"{base}{qs}")
        return cands

    def _poll_by_candidates(self, git_url: str, deadline: float):
        host_path = _repo_host_path(git_url)
        # Try both repo id and purl id forms
        candidates = []
        if "/" in host_path:
            org_repo = host_path.split("/", 1)[1]
            # Prefer PURL form first; storage uses pkg:github/<org>/<repo>@<sha>
            candidates.append(parse.quote(f"pkg:github/{org_repo}", safe=""))
        # Also try host path form
        candidates.append(parse.quote(host_path, safe=""))
        for rid in candidates:
            while time.monotonic() < deadline:
                st, data, text = self.get_cbom(rid)
                if st == 200 and (data is not None or text):
                    return json.dumps(data) if data is not None else text
                time.sleep(self.poll_interval)
        return None

    def trigger_scan_ws(self, git_url: str, branch: str, budget_sec: float) -> tuple[bool, bool, str | None]:
        """Send scan over persistent WS and wait for Finished within budget."""
        if not self._ensure_ws():
            return False, False, self._ws_error or "ws_not_connected"
        self._finished_evt.clear()
        # clear any previous captured PURL
        self._last_purl = None
        try:
            with self._lock:
                payload = {
                    "scanUrl": git_url,
                    "branch": branch or "main",
                    "subfolder": None,
                    "credentials": None,
                }
                self._ws_app.send(json.dumps(payload))
        except Exception as e:
            return False, False, f"ws_send_error:{e}"
        finished = self._finished_evt.wait(timeout=max(1.0, budget_sec))
        return True, bool(finished), None if finished else None

    def delete_cbom_for_url(self, git_url: str):
        """Best-effort cleanup: delete stored CBOM for this repo by both id forms."""
        host_path = _repo_host_path(git_url).lower()
        ids = [
            parse.quote(host_path, safe=""),
        ]
        if "/" in host_path:
            org_repo = host_path.split("/", 1)[1]
            ids.append(parse.quote(f"pkg:github/{org_repo}", safe=""))
        for rid in ids:
            url = f"{self.base}/api/v1/cbom/{rid}"
            st, _, body = _http_delete(url, timeout=10)
            # 200 OK or 404 Not Found are both acceptable outcomes for cleanup
            if st in (200, 204, 404):
                logger.debug("deleted CBOM id=%s status=%s", rid, st)
            else:
                logger.warning(
                    "CBOM delete returned %s for id=%s: %s",
                    st,
                    rid,
                    body[:120] if body else "",
                )

    def delete_cbom_by_id(self, project_id: str, *, try_without_branch: bool = False):
        ids = [project_id]
        if try_without_branch and "?branch=" in project_id:
            ids.append(project_id.split("?", 1)[0])
        for pid in ids:
            rid = parse.quote(pid, safe="")
            url = f"{self.base}/api/v1/cbom/{rid}"
            st, _, body = _http_delete(url, timeout=10)
            if st not in (200, 204, 404):
                logger.warning("CBOM delete returned %s for id=%s: %s", st, rid, (body or "")[:120])

    def generate_cbom(self, git_url, branch="main") -> tuple[str | None, float, str | None]:
        start = time.monotonic()
        # 1) Snapshot last/1 before scan (sequential change detection)
        before = self._get_last_raw()
        # 2) Trigger via WebSocket and wait until Finished (within budget)
        budget = max(5.0, float(str(max(10, TIMEOUT_SEC - 5))))
        ws_started, ws_finished, trig_err = self.trigger_scan_ws(git_url, branch, budget)

        if not ws_started:
            return (
                None,
                time.monotonic() - start,
                f"ws_not_started:{trig_err or 'unknown'}",
            )

        # Must not fetch before finish; if not finished within budget, bail out
        if not ws_finished:
            return None, time.monotonic() - start, "ws_not_finished"

        # 3) After finishing, allow a short grace for persistence
        time.sleep(2.0)

        # 3a) Try exact projectIdentifier reconstruction and fetch
        ns, name = self._purl_namespace_name(git_url)
        host, owner, repo = self._owner_repo_from_giturl(git_url)
        if ns and name and owner and repo:
            sha = self._github_head_sha(owner, repo, branch)
            for pid in self._build_purl_candidates(ns, name, branch, sha):
                rid = parse.quote(pid, safe="")
                logger.info("Trying CBOM fetch for reconstructed id: %s", pid)
                # Slim retry loop: 3 attempts with 1.0s backoff
                for _ in range(3):
                    stp, datp, txtp = self.get_cbom(rid)
                    logger.debug("Fetch reconstructed CBOM id=%s status=%s", pid, stp)
                    if stp == 200 and (datp is not None or txtp):
                        raw = txtp if datp is None else json.dumps(datp)
                        raw = self._normalize_cbom_text(raw)
                        duration = time.monotonic() - start
                        try:
                            self.delete_cbom_by_id(pid)
                        except Exception as e:
                            logger.debug("cleanup delete by id failed: %s", e)
                        return raw, duration, None
                    time.sleep(3.0)

        # 4) Poll last/1 until it changes from 'before' and references our repo
        host_path = _repo_host_path(git_url).lower()
        needle_host = host_path
        needle_purl_prefix = None
        if "/" in host_path:
            org_repo = host_path.split("/", 1)[1]
            needle_purl_prefix = f"pkg:github/{org_repo}@"

        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            cur = self._get_last_raw()
            if cur and cur != before:
                cur_lower = cur.lower()
                if (needle_host in cur_lower) or (needle_purl_prefix and needle_purl_prefix in cur_lower):
                    duration = time.monotonic() - start
                    cur = self._normalize_cbom_text(cur)
                    # Best-effort cleanup for a fresh store next run
                    try:
                        logger.info("Attempting to delete CBOM for: %s", git_url)
                        self.delete_cbom_for_url(git_url)
                    except Exception as e:
                        logger.debug("cleanup delete by repo failed: %s", e)
                    return cur, duration, None
            time.sleep(0.3)

        logger.error("CBOM not available within budget for %s", git_url)
        return None, time.monotonic() - start, "cbom_not_available"


def _produce(instr: JobInstruction, trace: Trace) -> str | tuple[str, float]:
    logger.info("[%s] Running on repo %s", NAME, instr.repo_info.full_name)
    client = CbomKitClient()
    payload, duration, err = client.generate_cbom(
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
    )
    if payload is not None:
        return payload, duration
    if err and str(err).startswith("ws_not_finished"):
        trace.add("cbomkit websocket did not finish within budget")
        raise TimeoutError(err)
    raise RuntimeError(err or "cbomkit_failed")


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
