"""
Quantitative analysis over memory/events.jsonl.

The notice loop's generator used to scan raw event text — easy to
hallucinate "you've been making lots of coffee" without actually
counting. This module gives the generator real numbers to ground in.

Everything here is substring-matching on JSON-serialised event data,
not NLP. Honest about that limit in the tool descriptions, so the LLM
doesn't over-trust it.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
EVENTS_FILE = ROOT / "memory" / "events.jsonl"


def _iter_events(days: int | None = None):
    """Yield event dicts, optionally filtered to the last N days."""
    if not EVENTS_FILE.exists():
        return
    cutoff = None
    if days is not None and days > 0:
        cutoff = datetime.now() - timedelta(days=days)
    for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if cutoff is not None:
            try:
                if datetime.fromisoformat(e.get("time", "")) < cutoff:
                    continue
            except Exception:
                continue
        yield e


def _matches(event: dict, query: str) -> bool:
    q = query.lower()
    if q in (event.get("kind", "") or "").lower():
        return True
    blob = json.dumps(event.get("data", ""), ensure_ascii=False).lower()
    return q in blob


def matching(query: str, days: int | None = None) -> list:
    return [e for e in _iter_events(days) if _matches(e, query)]


def last_seen(query: str) -> dict:
    """Most recent matching event + age in days. {} if none."""
    matches = matching(query)
    if not matches:
        return {}
    last = matches[-1]
    try:
        when = datetime.fromisoformat(last.get("time", ""))
        age_days = (datetime.now() - when).total_seconds() / 86400
    except Exception:
        when = None
        age_days = None
    return {
        "time": last.get("time"),
        "age_days": round(age_days, 2) if age_days is not None else None,
        "kind": last.get("kind"),
        "data": last.get("data"),
    }


def rate(query: str, days: int = 7) -> float:
    """Matches per day over the last `days`."""
    if days <= 0:
        return 0.0
    n = len(matching(query, days=days))
    return round(n / days, 3)


def delta(query: str, recent_days: int = 7, baseline_days: int = 30) -> dict:
    """Compare recent rate to a longer baseline."""
    recent = rate(query, recent_days)
    base = rate(query, baseline_days)
    diff = recent - base
    pct = None
    if base > 0:
        pct = round((diff / base) * 100, 1)
    return {
        "recent_rate": recent,
        "baseline_rate": base,
        "delta_per_day": round(diff, 3),
        "delta_pct": pct,
        "recent_window_days": recent_days,
        "baseline_window_days": baseline_days,
    }


# Heuristic keywords for "inventory ageing" — last time the user
# said "bought / ordered" $thing, how long ago was it?
_PURCHASE_VERBS = ("bought", "ordered", "got", "picked up", "delivered", "arrived")


def inventory_age(item: str) -> dict:
    """Days since a purchase-flavoured event for `item`."""
    candidates = []
    for e in _iter_events():
        blob = json.dumps(e.get("data", ""), ensure_ascii=False).lower()
        if item.lower() not in blob:
            continue
        if any(v in blob for v in _PURCHASE_VERBS):
            candidates.append(e)
    if not candidates:
        return {}
    last = candidates[-1]
    try:
        age_days = (
            datetime.now() - datetime.fromisoformat(last["time"])
        ).total_seconds() / 86400
    except Exception:
        age_days = None
    return {
        "item": item,
        "last_purchase_time": last.get("time"),
        "age_days": round(age_days, 2) if age_days is not None else None,
    }


def find_pattern(query: str) -> dict:
    """Combined report for one keyword."""
    return {
        "query": query,
        "rate_per_day_7d": rate(query, 7),
        "rate_per_day_30d": rate(query, 30),
        "trend": delta(query),
        "last_seen": last_seen(query),
        "inventory_age": inventory_age(query),
    }


# Default watchlist topics — substring keywords scanned each notice
# tick. Augmented at runtime from facts that look like "watch X".
DEFAULT_WATCHLIST = [
    "coffee", "tea", "milk", "bread", "eggs", "headphones",
    "presence", "sitting", "weather",
]


def _watchlist() -> list:
    """Built-in topics plus anything the user explicitly asked Clyde to
    watch (a fact starting with 'watch: ')."""
    extra = []
    facts_file = ROOT / "memory" / "facts.jsonl"
    if facts_file.exists():
        for line in facts_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                f = json.loads(line)
            except Exception:
                continue
            fact = (f.get("fact") or "").strip().lower()
            if fact.startswith("watch:"):
                extra.append(fact[6:].strip())
    return list(dict.fromkeys(DEFAULT_WATCHLIST + extra))


def summary(max_notable: int = 5) -> str:
    """Compact text summary of *notable* patterns for the notice loop.
    Only surfaces things that look interesting — a big delta, a stale
    inventory item, a recent absence."""
    notable = []
    for topic in _watchlist():
        d = delta(topic)
        ia = inventory_age(topic)
        # Score: combine relative change with whether inventory is aging
        score = 0.0
        line_parts = []

        if d["baseline_rate"] > 0 and d["delta_pct"] is not None:
            if abs(d["delta_pct"]) >= 50 and d["recent_rate"] > 0:
                score += abs(d["delta_pct"]) / 100
                line_parts.append(
                    f"{topic}: {d['recent_rate']}/day recent vs {d['baseline_rate']}/day baseline "
                    f"({d['delta_pct']:+.0f}%)"
                )
        elif d["recent_rate"] > 0 and d["baseline_rate"] == 0:
            score += 1.0
            line_parts.append(f"{topic}: new — {d['recent_rate']}/day, no baseline")

        if ia and ia.get("age_days") is not None and ia["age_days"] >= 5:
            score += min(ia["age_days"] / 10, 2.0)
            line_parts.append(
                f"{topic}: {ia['age_days']:.1f} days since last bought"
            )

        if line_parts:
            notable.append((score, "; ".join(line_parts)))

    notable.sort(key=lambda t: -t[0])
    if not notable:
        return ""
    return "\n".join(line for _, line in notable[:max_notable])
