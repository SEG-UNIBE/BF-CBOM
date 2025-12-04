import json
import logging
import os
import re
import threading
import time
import uuid
from urllib import error as urlerror, parse, request

import websocket

from common.config import GITHUB_TOKEN
from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "300"))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def _base_url() -> str:
    # Default to local dev server if not provided
    base = os.getenv("CBOMKIT_BASE_URL") or "http://127.0.0.1:8081"
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


class CbomKitClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or _base_url()).rstrip("/")
        # Persistent WebSocket session configuration (fixed, no env needed)
        self.client_id = "cbomkitclient"
        # WebSocket endpoint template; supports {clientId} placeholder
        self.ws_url_tmpl = self._derive_ws_url_tmpl()
        self.ws_url = self._make_ws_url(self.client_id)
        # Where to fetch the freshly generated CBOM (latest 1)
        self.last_url = f"{self.base}/api/v1/cbom/last/1"
        # Polling interval seconds
        self.poll_interval = 2.0
        # Backend health wait settings and OOM sentinel (written by entrypoint on OOM)
        self.state_dir = os.getenv("CBOMKIT_BACKEND_STATE_DIR") or os.path.expanduser("~/.cbomkit")
        self.oom_file = os.getenv("CBOMKIT_BACKEND_OOM_FILE") or os.path.join(self.state_dir, "backend_oom")
        # Max time to wait for backend to become healthy after a restart
        try:
            self.health_wait_sec = float(os.getenv("CBOMKIT_HEALTH_WAIT_SEC", "20"))
            if self.health_wait_sec < 0:
                self.health_wait_sec = 0.0
        except Exception:
            self.health_wait_sec = 20.0
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

    def _derive_ws_url_tmpl(self) -> str:
        try:
            parts = parse.urlsplit(self.base)
            scheme = "wss" if parts.scheme == "https" else "ws"
            netloc = parts.netloc or parts.path
            netloc = netloc.strip("/")
            return f"{scheme}://{netloc}/v1/scan/{{clientId}}"
        except Exception:
            return "ws://127.0.0.1:8081/v1/scan/{clientId}"

    def _teardown_ws(self, wait: float = 1.0):
        try:
            if self._ws_app is not None:
                try:
                    self._ws_app.close()
                except Exception:
                    pass
            if self._ws_thread is not None and self._ws_thread.is_alive():
                self._ws_thread.join(timeout=max(0.1, wait))
        finally:
            self._ws_app = None
            self._ws_thread = None
            self._ws_connected = False
            self._ws_error = None

    def _ensure_ws(self, timeout_sec: float = 3.0, *, force_new: bool = False) -> bool:
        # Reuse only if thread is alive and flags are healthy
        if (
            self._ws_app
            and self._ws_thread is not None
            and self._ws_thread.is_alive()
            and self._ws_connected
            and not self._ws_error
            and not force_new
        ):
            return True

        # Reset state
        self._ws_error = None
        self._ws_connected = False
        self._finished_evt.clear()
        # If explicitly requested, tear down any existing connection
        if force_new:
            self._teardown_ws()

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
        start_deadline = time.monotonic() + timeout_sec

        # Attempt initial connect, and if we hit an immediate error (e.g., backend just restarted),
        # retry by recreating the WebSocket until the timeout expires.
        while time.monotonic() < start_deadline:
            # Start the thread if not alive
            if self._ws_thread is not None and not self._ws_thread.is_alive():
                try:
                    self._ws_thread.start()
                except RuntimeError:
                    # Already started, ignore
                    pass

            # Wait in short intervals, monitoring for success or transient errors
            t_wait_deadline = time.monotonic() + 1.0
            while time.monotonic() < t_wait_deadline:
                if self._ws_connected:
                    logger.debug("Websocket connected")
                    return True
                if self._ws_error:
                    break
                time.sleep(0.05)

            if self._ws_connected:
                return True

            # If we hit an error, tear down and retry a fresh connection if time remains
            if self._ws_error:
                logger.warning("WS connect error: %s; retrying...", self._ws_error)
                self._ws_error = None
                self._ws_connected = False
                try:
                    self._teardown_ws()
                except Exception:
                    pass
                # Recreate app and thread
                self._ws_app = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws_thread = threading.Thread(
                    target=self._ws_app.run_forever,
                    kwargs={"ping_interval": 30, "ping_timeout": 10},
                    daemon=True,
                )
                # Loop will try starting again
                continue

        return False

    def _backend_oom_flagged(self) -> bool:
        try:
            return bool(self.oom_file and os.path.exists(self.oom_file))
        except Exception:
            return False

    def _backend_health_ready(self, timeout: float = 0.8) -> bool:
        # Prefer Quarkus health endpoint; fall back to OpenAPI if needed
        try:
            st, _, _ = _http_get_json(f"{self.base}/q/health", timeout=max(0.1, int(timeout)))
            if st == 200:
                return True
        except Exception:
            pass
        try:
            st, _, _ = _http_get_json(f"{self.base}/q/openapi", timeout=max(0.1, int(timeout)))
            return st == 200
        except Exception:
            return False

    def _wait_backend_ready(self, max_wait: float) -> bool:
        if max_wait <= 0:
            return self._backend_health_ready(timeout=1.0)
        deadline = time.monotonic() + max_wait
        # First quick check
        if self._backend_health_ready(timeout=1.0):
            return True
        while time.monotonic() < deadline:
            if self._backend_health_ready(timeout=1.0):
                return True
            time.sleep(0.25)
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

    # (old candidate-polling helper removed; not used in current flow)

    def trigger_scan_ws(self, git_url: str, branch: str, budget_sec: float) -> tuple[bool, bool, str | None]:
        """Send scan using a fresh WS session per job and wait for Finished.

        Returns (started, finished, error). If backend is unavailable, returns (True, False, "backend_unavailable").
        Retries WS session on transient errors within the provided budget.
        """
        deadline = time.monotonic() + max(1.0, budget_sec)
        wait_budget = min(self.health_wait_sec, max(0.0, budget_sec * 0.5))
        while time.monotonic() < deadline:
            # Use a unique client id per attempt to avoid sticky server state
            job_client_id = f"{self.client_id}-{uuid.uuid4().hex[:8]}"
            prev_url = self.ws_url
            self.ws_url = self._make_ws_url(job_client_id)
            try:
                if not self._wait_backend_ready(max_wait=wait_budget):
                    return True, False, "backend_unavailable"
                if not self._ensure_ws(timeout_sec=10.0, force_new=True):
                    time.sleep(0.3)
                    continue
                self._finished_evt.clear()
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
                except Exception:
                    time.sleep(0.3)
                    continue
                # Wait for Finished or WS error; if error, retry until deadline
                while time.monotonic() < deadline:
                    if self._finished_evt.is_set():
                        return True, True, None
                    if self._backend_oom_flagged():
                        return True, False, "backend_oom"
                    if self._ws_error:
                        break
                    time.sleep(0.2)
                if self._finished_evt.is_set():
                    return True, True, None
                # Otherwise fall through to retry
            finally:
                try:
                    self._teardown_ws(wait=0.5)
                finally:
                    self.ws_url = prev_url
        return True, False, None

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
            if trig_err == "backend_unavailable":
                return None, time.monotonic() - start, "backend_unavailable"
            if trig_err == "backend_oom":
                return None, time.monotonic() - start, "backend_oom"
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
        trace.add(
            f"cbomkit WebSocket did not signal 'Finished' within budget: "
            f"repo={instr.repo_info.full_name} branch={instr.repo_info.branch} "
            f"worker_timeout_sec={TIMEOUT_SEC}"
        )
        raise TimeoutError(err)
    if err == "backend_oom":
        trace.add("cbomkit backend encountered OutOfMemoryError")
        # Give the supervisor time to restart backend before we release the job,
        # so the next job doesn't immediately fail due to backend_unavailable.
        try:
            client._wait_backend_ready(max_wait=getattr(client, "health_wait_sec", 20))  # type: ignore[attr-defined]
        except Exception:
            pass
        raise RuntimeError(err)
    if err == "backend_unavailable":
        # Do not requeue; mark as error so the inspection reflects the failure
        trace.add("cbomkit backend not yet ready after restart")
        raise RuntimeError(err)
    raise RuntimeError(err or "cbomkit_failed")


handle_instruction = build_handle_instruction(NAME, _produce)

def main():
    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
