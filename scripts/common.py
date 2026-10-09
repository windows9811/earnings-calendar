"""Shared helpers for update.py and build.py (Python 3.9+, stdlib only)."""
import json
import os
import re
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WEEKS_DIR = os.path.join(DATA, "weeks")
LOGO_DIR = os.path.join(ROOT, "public", "logos")
COMPANIES_FILE = os.path.join(DATA, "companies.json")
OVERRIDES_FILE = os.path.join(DATA, "overrides.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_config():
    return load_json(os.path.join(ROOT, "config.json"), {})


def load_overrides():
    raw = load_json(OVERRIDES_FILE, {})
    return {k.upper(): v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


def today_in(tz_name):
    if ZoneInfo:
        return datetime.now(ZoneInfo(tz_name)).date()
    return (datetime.utcnow() - timedelta(hours=4)).date()


def monday_of(d):
    return d - timedelta(days=d.weekday())


def current_week(cfg):
    t = today_in(cfg.get("timezone", "America/New_York"))
    if cfg.get("showNextWeekOnWeekend", True) and t.weekday() >= 5:
        t = t + timedelta(days=7 - t.weekday())
    return monday_of(t)


def parse_date(s):
    return date.fromisoformat(s)


def week_days(monday):
    return [monday + timedelta(days=i) for i in range(5)]


def parse_money(s):
    digits = re.sub(r"[^0-9]", "", s or "")
    return int(digits) if digits else 0


_SUFFIX = re.compile(
    r"(,?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|n\.?v|s\.?a|s\.?a\.?b\.? de c\.?v|ag|se|sa|lp|l\.?p|llc|holdings?|group|the)\.?)+$",
    re.I,
)
_NOISE = re.compile(
    r"\b(common stock|ordinary shares?|american depositary shares?|american depository shares?|ads|adr|class [abc]( capital stock| common stock| shares?)?|capital stock|sponsored|each representing.*|new|depositary shares?)\b",
    re.I,
)


_SUFFIX_LIGHT = re.compile(
    r"(,?\s+(inc|incorporated|corp|corporation|co|ltd|limited|plc|n\.?v|s\.?a|s\.?a\.?b\.? de c\.?v|ag|se|sa|lp|l\.?p|llc)\.?)+$",
    re.I,
)


def display_name(name):
    """'Netflix, Inc. Common Stock' -> 'Netflix' (keeps words like 'Group')."""
    return clean_name(name, _SUFFIX_LIGHT)


def clean_name(name, suffix=_SUFFIX):
    n = _NOISE.sub("", name or "")
    n = re.sub(r"\(.*?\)", "", n)
    n = re.sub(r"\s+", " ", n).strip(" ,.-")
    prev = None
    while prev != n:
        prev = n
        n = suffix.sub("", n).strip(" ,.-")
    return n or (name or "").strip()


def dedupe_key(name):
    return re.sub(r"[^a-z0-9]", "", clean_name(name).lower())


def host_of(url):
    m = re.match(r"^https?://([^/]+)", url or "", re.I)
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


def select_rows(rows, cfg, overrides):
    """Apply the 'most anticipated' rule: market cap threshold + manual include/exclude,
    and drop duplicate share classes (GOOG/GOOGL, BBD/BBDO ...)."""
    min_cap = cfg.get("minMarketCap", 10_000_000_000)
    picked = []
    for r in sorted(rows, key=lambda r: -r.get("marketCap", 0)):
        ov = overrides.get(r["symbol"], {})
        if ov.get("exclude"):
            continue
        if r.get("marketCap", 0) >= min_cap or ov.get("include"):
            picked.append(r)
    return picked


def dedupe_week(day_rows, companies):
    """Remove duplicate share classes within one week, keeping the larger listing."""
    seen = set()
    out = {}
    all_rows = [(d, r) for d, rows in day_rows.items() for r in rows]
    all_rows.sort(key=lambda x: -x[1].get("marketCap", 0))
    keep = set()
    for d, r in all_rows:
        c = companies.get(r["symbol"], {})
        keys = {"n:" + dedupe_key(r["name"])}
        h = host_of(c.get("website"))
        if h:
            keys.add("h:" + h)
        if keys & seen:
            continue
        seen |= keys
        keep.add((d, r["symbol"]))
    for d, rows in day_rows.items():
        out[d] = [r for r in rows if (d, r["symbol"]) in keep]
    return out
