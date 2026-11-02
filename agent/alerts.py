"""Phone alerts for the plays worth stopping for. Sends to an ntfy.sh topic when NTFY_TOPIC is set
(free app: subscribe to the same topic name), and always comments on a "Perseverance alerts" issue,
which GitHub's app turns into a notification. Each alert goes out once."""
import os
import urllib.request
from datetime import timedelta

from . import store

FILE = "alerts.json"


def send(items, title="Perseverance"):
    """items: [(unique_key, text)]. Returns how many new alerts went out."""
    db = store.load(FILE, {"sent": {}, "issue": None})
    new = [(k, t) for k, t in items if k not in db["sent"]]
    if not new:
        return 0
    text = "\n".join("• " + t for _, t in new)
    ok = False
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode(), method="POST",
                                         headers={"Title": title, "Tags": "basketball"})
            urllib.request.urlopen(req, timeout=15).read()
            ok = True
        except Exception as e:
            print(f"[alerts] ntfy failed: {e}")
    if os.environ.get("GITHUB_TOKEN"):
        if not db.get("issue"):
            r = store._gh("POST", "/issues", {"title": "Perseverance alerts",
                                              "body": "The agent comments here when a high-conviction play or an injury "
                                                      "opportunity shows up. Keep notifications on for this issue."})
            db["issue"] = r and r.get("number")
        if db.get("issue") and store.gh_comment(db["issue"], f"**{title}**\n\n{text}"):
            ok = True
    if not ok:
        return 0
    now = store.now()
    for k, _ in new:
        db["sent"][k] = store.iso(now)
    cut = store.iso(now - timedelta(days=14))
    db["sent"] = {k: v for k, v in db["sent"].items() if v >= cut}
    store.save(FILE, db)
    return len(new)
