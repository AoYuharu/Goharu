"""GitHub 连通性探测与代理自动解析。

解析顺序：显式配置 → 环境代理 → 本机常见 HTTP 代理端口 → 直连。
探测用 `git ls-remote`，成功即视为 GitHub 可访问；代理成功后可回写配置。
"""

import logging
import os
import re
import subprocess
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_PROXY_CANDIDATES = (
    "http://127.0.0.1:7893",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7897",
    "http://127.0.0.1:10809",
    "http://127.0.0.1:1080",
)


def _host_of(url: str) -> str:
    if not url:
        return ""
    if "://" in url:
        return (urlparse(url).hostname or "").lower()
    m = re.match(r"^[^@]+@([^:]+):", url)
    return m.group(1).lower() if m else ""


def _env_proxy_candidates(env=None) -> list[str]:
    env = os.environ if env is None else env
    out = []
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(env.get(key) or "").strip()
        if value and urlparse(value).scheme in ("http", "https"):
            out.append(value)
    return out


def _default_runner(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except Exception as exc:
        return 1, str(exc)


def _remote_url(repo_dir: str, remote: str, runner) -> tuple[str, str]:
    code, out = runner(
        ["git", "-C", repo_dir, "config", "--get", f"remote.{remote}.url"], 10)
    return ("", out) if code != 0 else (out.strip(), "")


def _proxy_args(remote_url: str, proxy_url: str) -> list[str]:
    if not proxy_url or "://" not in remote_url:
        return []
    scheme = urlparse(remote_url).scheme or "https"
    host = _host_of(remote_url)
    if scheme not in ("http", "https") or not host:
        return []
    return ["-c", f"http.{scheme}://{host}/.proxy={proxy_url}"]


def probe_remote(repo_dir: str, remote: str, branch: str, proxy_url: str = "",
                 timeout: float = 15, runner=None) -> dict:
    runner = runner or _default_runner
    remote_url, error = _remote_url(repo_dir, remote, runner)
    if not remote_url:
        return {"ok": False, "error": f"remote.{remote}.url: {error}"}
    args = _proxy_args(remote_url, proxy_url)
    code, out = runner(
        ["git", "-C", repo_dir, *args, "ls-remote", "--heads", remote,
         f"refs/heads/{branch}"], timeout)
    return {"ok": code == 0, "output": out}


def detect_github_proxy(repo_dir: str, remote: str = "origin", branch: str = "master",
                        configured: str = "", candidates=None, env=None,
                        timeout: float = 15, runner=None) -> dict:
    ordered = []
    first = [configured] if str(configured or "").strip() else []
    for item in [*first, *_env_proxy_candidates(env), *(candidates or ()), ""]:
        item = str(item or "").strip()
        if item not in ordered:
            ordered.append(item)
    errors = []
    for proxy_url in ordered:
        result = probe_remote(repo_dir, remote, branch, proxy_url,
                              timeout=timeout, runner=runner)
        if result.get("ok"):
            return {"ok": True, "proxy": proxy_url,
                    "tried": ordered, "errors": errors}
        label = proxy_url or "direct"
        errors.append(f"{label}: {str(result.get('output') or result.get('error'))[:200]}")
    return {"ok": False, "proxy": "", "tried": ordered, "errors": errors}


class AutoProxyResolver:
    """可调用代理提供器：配置为空时自动探测，成功后回写配置。"""

    def __init__(self, repo_dir: str, remote: str, branch: str,
                 configured_provider, candidate_provider=None,
                 on_selected=None, timeout: float = 15, runner=None):
        self.repo_dir = str(repo_dir)
        self.remote = str(remote)
        self.branch = str(branch)
        self._configured_provider = configured_provider
        self._candidate_provider = candidate_provider
        self._on_selected = on_selected
        self._timeout = timeout
        self._runner = runner
        self._resolved = False
        self._selected = ""
        self.last_result = None

    def reset(self):
        self._resolved = False
        self._selected = ""

    def __call__(self):
        url, rules = self._configured_provider()
        rules = list(rules or [])
        if not self._resolved:
            raw_candidates = (self._candidate_provider
                              or (lambda: DEFAULT_PROXY_CANDIDATES))()
            candidates = list(raw_candidates or ())
            self.last_result = detect_github_proxy(
                self.repo_dir, self.remote, self.branch,
                configured=url, candidates=candidates,
                timeout=self._timeout, runner=self._runner)
            self._selected = str(self.last_result.get("proxy") or "")
            self._resolved = True
            logger.info(
                "GitHub 代理解析: ok=%s proxy=%s tried=%s",
                self.last_result.get("ok"), self._selected or "direct",
                len(self.last_result.get("tried") or []))
            if self._selected and self._on_selected:
                try:
                    self._on_selected(self._selected)
                except Exception:
                    pass
        return self._selected, rules
