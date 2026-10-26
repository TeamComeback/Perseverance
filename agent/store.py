import json
import os
import urllib.request
from datetime import datetime, timezone

from . import config


def now() -> datetime:
    """Current UTC time. PERSEVERANCE_NOW lets tests time-travel."""
    fake = os.environ.get("PERSEVERANCE_NOW")
    if fake:
        return datetime.fromisoformat(fake.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load(name: str, default):
    p = config.DATA / name
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return default


def save(name: str, obj) -> None:
    config.DATA.mkdir(parents=True, exist_ok=True)
    tmp = config.DATA / (name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
    os.replace(tmp, config.DATA / name)


# ---- GitHub issue helpers (no-ops outside Actions) --------------------------
def _gh(method: str, path: str, body=None):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")
    except Exception as e:  # never let GitHub hiccups break the books
        print(f"[github] {method} {path} failed: {e}")
        return None


def gh_comment(issue: int, text: str):
    return _gh("POST", f"/issues/{issue}/comments", {"body": text})


def gh_close(issue: int):
    return _gh("PATCH", f"/issues/{issue}", {"state": "closed"})
