#!/usr/bin/env python3
"""Fetch the earnings calendar, enrich companies (website + logo) and store JSON.

Usage:  python3 scripts/update.py            # refresh default window
        python3 scripts/update.py --weeks 8  # look further ahead
Env:    FINNHUB_API_KEY (optional) fills in 盤前/盤後 for companies Nasdaq lists as "time not supplied".
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    COMPANIES_FILE, LOGO_DIR, WEEKS_DIR, current_week, display_name, load_config,
    load_json, load_overrides, parse_money, save_json, select_rows, week_days,
)

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
NASDAQ_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
TIME_MAP = {"time-pre-market": "bmo", "time-after-hours": "amc"}
RECHECK_DAYS = 30
MAX_LOGO_BYTES = 150_000  # bigger logos get a smaller rendering (faster pages)


def log(*a):
    print(*a, flush=True)


def http_get(url, headers=None, timeout=25, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410):
                return e.code, "", b""
            last = e
        except Exception as e:  # timeouts, resets
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def get_json(url, headers=None):
    status, _, body = http_get(url, headers)
    if status != 200:
        return None
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------- calendar

def fetch_day(d):
    j = get_json(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}", NASDAQ_HEADERS)
    if not j or not j.get("data"):
        raise RuntimeError("bad payload")
    rows = j["data"].get("rows") or []
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
            "marketCap": parse_money(r.get("marketCap")),
            "time": TIME_MAP.get(r.get("time"), "tbd"),
            "eps": r.get("epsForecast") or "",
            "fq": r.get("fiscalQuarterEnding") or "",
            "lastYearEps": r.get("lastYearEPS") or "",
        })
    return out


def fetch_finnhub(start, end):
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {}
    url = ("https://finnhub.io/api/v1/calendar/earnings?"
           + urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat(), "token": key}))
    try:
        j = get_json(url) or {}
    except Exception as e:
        log("  ! finnhub failed:", e)
        return {}
    hints = {}
    for e in j.get("earningsCalendar", []):
        if e.get("hour") in ("bmo", "amc"):
            hints[(e.get("symbol", "").upper(), e.get("date"))] = e["hour"]
    log(f"  finnhub hints: {len(hints)}")
    return hints


# ---------------------------------------------------------------- enrichment

WD_EXCHANGES = "wd:Q13677 wd:Q82059 wd:Q846626"  # NYSE, Nasdaq, NYSE American


def wikidata_lookup(symbols, ua):
    """ticker -> {label, logo, website} from Wikidata (wordmark SVG logos like the reference site)."""
    found = {}
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i + 60]
        values = " ".join('"%s"' % s.replace('"', "") for s in chunk)
        q = f"""SELECT ?sym ?item ?itemLabel ?logo ?web ?article WHERE {{
  VALUES ?sym {{ {values} }}
  VALUES ?ex {{ {WD_EXCHANGES} }}
  ?item p:P414 ?st . ?st ps:P414 ?ex ; pq:P249 ?sym .
  FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
  OPTIONAL {{ ?item wdt:P154 ?logo }}
  OPTIONAL {{ ?item wdt:P856 ?web }}
  OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}"""
        url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": q, "format": "json"})
        try:
            j = get_json(url, {"User-Agent": ua, "Accept": "application/sparql-results+json"})
        except Exception as e:
            log("  ! wikidata failed:", e)
            continue
        for b in (j or {}).get("results", {}).get("bindings", []):
            sym = b["sym"]["value"]
            cand = found.setdefault(sym, {"items": {}})
            item = cand["items"].setdefault(b["item"]["value"], {"logos": [], "webs": []})
            item["label"] = b.get("itemLabel", {}).get("value", "")
            if "article" in b:
                item["article"] = urllib.parse.unquote(b["article"]["value"].rsplit("/", 1)[-1]).replace("_", " ")
            if "logo" in b and b["logo"]["value"] not in item["logos"]:
                item["logos"].append(b["logo"]["value"])
            if "web" in b and b["web"]["value"] not in item["webs"]:
                item["webs"].append(b["web"]["value"])
        time.sleep(1)
    return found


def pick_wikidata(cand, nasdaq_name):
    if not cand:
        return {}
    key = re.sub(r"[^a-z]", "", nasdaq_name.lower())[:6]

    def score(it):
        lbl = re.sub(r"[^a-z]", "", it.get("label", "").lower())
        return (bool(key) and lbl.startswith(key[:4]), bool(it["logos"]), bool(it["webs"]))

    best = max(cand["items"].values(), key=score)
    logos = sorted(best["logos"], key=lambda u: (
        "logo" not in u.lower(), any(w in u.lower() for w in ("icon", "symbol", "emblem")), -len(u)))
    webs = sorted(best["webs"], key=lambda u: ("investor" in u or "/ir" in u or "://ir." in u, len(u)))
    label = best.get("label", "")
    if re.fullmatch(r"Q\d+", label):
        label = ""
    return {"label": label, "logo": logos[0] if logos else None, "website": webs[0] if webs else None,
            "article": best.get("article")}


WIKI_API = "https://en.wikipedia.org/w/api.php?"
_GENERIC_FILES = ("commons-logo", "wiki", "edit-", "oojs", "question_book", "question book", "symbol_", "portal",
                  "stock", "nyse", "nasdaq", "flag", "icon")


def _name_words(name):
    stop = {"the", "inc", "corp", "corporation", "company", "group", "holdings", "co", "of", "and", "plc", "ltd"}
    return [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in stop and len(w) > 1]


def wikipedia_logo(article, name, ua):
    """Find a 'logo' file used on the company's English Wikipedia article."""
    hdr = {"User-Agent": ua}
    try:
        if not article:  # no Wikidata match: search Wikipedia by company name
            j = get_json(WIKI_API + urllib.parse.urlencode({
                "action": "query", "list": "search", "srsearch": f"{name} company", "srlimit": 1,
                "format": "json"}), hdr) or {}
            hits = j.get("query", {}).get("search", [])
            words = _name_words(name)
            if not hits or not words or words[0] not in hits[0]["title"].lower():
                return None
            article = hits[0]["title"]
        j = get_json(WIKI_API + urllib.parse.urlencode({
            "action": "query", "prop": "images", "imlimit": "max", "titles": article,
            "redirects": 1, "format": "json"}), hdr) or {}
        files = [i["title"] for p in j.get("query", {}).get("pages", {}).values() for i in p.get("images", [])]
        words = _name_words(name) + _name_words(article)
        cands = []
        for f in files:
            low = f.lower()
            if "logo" not in low or any(g in low for g in _GENERIC_FILES):
                continue
            hit = sum(1 for w in set(words) if w in low)
            if not hit:
                continue
            cands.append((-hit, not low.endswith(".svg"), "(" in low, f))
        if not cands:
            return None
        best = sorted(cands)[0][-1]
        j = get_json(WIKI_API + urllib.parse.urlencode({
            "action": "query", "prop": "imageinfo", "iiprop": "url", "titles": best, "format": "json"}), hdr) or {}
        for p in j.get("query", {}).get("pages", {}).values():
            for ii in p.get("imageinfo", []):
                return (ii.get("url") or "").split("?")[0] or None
    except Exception:
        return None
    return None


def nasdaq_website(sym):
    try:
        j = get_json(f"https://api.nasdaq.com/api/company/{urllib.parse.quote(sym)}/company-profile",
                     NASDAQ_HEADERS)
        url = (((j or {}).get("data") or {}).get("CompanyUrl") or {}).get("value")
        if url and not url.startswith("http"):
            url = "https://" + url
        return url
    except Exception:
        return None


def logo_filename(sym):
    return re.sub(r"[^A-Z0-9\-]", "-", sym.upper())


def wikimedia_png(url):
    """PNG rendering of a (huge) Wikimedia SVG."""
    m = re.match(r"^(https://upload\.wikimedia\.org/wikipedia/[^/]+)/([0-9a-f]/[0-9a-f]{2})/([^/?]+)$", url)
    if m:
        name = m.group(3)
        thumb = f"400px-{name}.png" if name.lower().endswith(".svg") else f"400px-{name}"
        return f"{m.group(1)}/thumb/{m.group(2)}/{name}/{thumb}"
    return url + "?width=400"


def shrink_raster(body):
    """Downscale a big PNG/JPG logo with Pillow (if installed). Returns (bytes, ext) or None."""
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(BytesIO(body))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        scale = min(1.0, 120 / im.height, 600 / im.width)
        if scale < 1:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        out = BytesIO()
        im.save(out, "PNG", optimize=True)
        return out.getvalue(), "png"
    except Exception:
        return None


def download_logo(sym, sources, ua):
    base = logo_filename(sym)
    for src, url in sources:
        if not url:
            continue
        if url.startswith("http://commons.wikimedia.org"):
            url = "https://" + url[len("http://"):]
        try:
            status, ctype, body = http_get(url, {"User-Agent": ua}, timeout=30, retries=2)
            if status != 200 or len(body) < 200:
                continue
            is_svg = "svg" in ctype or body.lstrip()[:200].lower().find(b"<svg") >= 0
            if len(body) > MAX_LOGO_BYTES and "wikimedia" in url:
                # big file: ask Wikimedia for a small PNG rendering instead
                status, ctype, body = http_get(wikimedia_png(url), {"User-Agent": ua}, timeout=30, retries=2)
                is_svg = False
                if status != 200 or len(body) < 200:
                    continue
            if is_svg and len(body) > MAX_LOGO_BYTES:
                continue  # oversized SVG we can't shrink: try the next source
            if not is_svg and len(body) > MAX_LOGO_BYTES:
                small = shrink_raster(body)
                if small:
                    body, ctype = small[0], "image/png"
            if len(body) > MAX_LOGO_BYTES * 3:
                continue
            if is_svg:
                ext = "svg"
            elif "png" in ctype or body[:8] == b"\x89PNG\r\n\x1a\n":
                ext = "png"
            elif "jpeg" in ctype or body[:3] == b"\xff\xd8\xff":
                ext = "jpg"
            elif "webp" in ctype or body[8:12] == b"WEBP":
                ext = "webp"
            else:
                continue
            for old in ("svg", "png", "jpg", "webp"):
                p = os.path.join(LOGO_DIR, f"{base}.{old}")
                if old != ext and os.path.exists(p):
                    os.remove(p)
            with open(os.path.join(LOGO_DIR, f"{base}.{ext}"), "wb") as f:
                f.write(body)
            return f"{base}.{ext}", src
        except Exception:
            continue
    return None, None


def optimize_existing_logos(companies):
    """Shrink oversized logos already on disk (re-fetch Wikimedia ones as small PNGs)."""
    fixed = 0
    for sym, c in companies.items():
        f = c.get("logo")
        p = os.path.join(LOGO_DIR, f) if f else None
        if not p or not os.path.exists(p) or os.path.getsize(p) <= MAX_LOGO_BYTES:
            continue
        if c.get("logoSource") in ("wikidata", "wikipedia") or f.endswith(".svg"):
            os.remove(p)
            c["logo"] = None  # enrich() will re-download a small rendering / another source
            c.pop("checkedAt", None)
            fixed += 1
        elif not f.endswith(".svg"):
            with open(p, "rb") as fh:
                small = shrink_raster(fh.read())
            if small:
                os.remove(p)
                c["logo"] = f"{logo_filename(sym)}.png"
                with open(os.path.join(LOGO_DIR, c["logo"]), "wb") as fh:
                    fh.write(small[0])
                fixed += 1
    if fixed:
        log(f"  optimized {fixed} oversized logo(s)")


def enrich(symbols_with_names, companies, overrides, ua, force=False, retry_fallback=False):
    now = datetime.now(timezone.utc)
    todo = []
    for sym, name in symbols_with_names.items():
        c = companies.get(sym)
        ov = overrides.get(sym, {})
        stale = True
        if c and c.get("checkedAt") and not force:
            age = now - datetime.fromisoformat(c["checkedAt"])
            logo_ok = c.get("logo") and os.path.exists(os.path.join(LOGO_DIR, c["logo"]))
            want_override_logo = ov.get("logo") and c.get("logoSource") != "override:" + ov["logo"]
            stale = (not logo_ok and age > timedelta(days=RECHECK_DAYS)) or want_override_logo
            if not logo_ok and (c.get("logo") or c.get("logoSource") in ("wikidata", "wikipedia")):
                stale = True
            if retry_fallback and c.get("logoSource") in (None, "parqet", "fmp"):
                stale = True
        if stale:
            todo.append(sym)
    if not todo:
        log("  enrichment: nothing new")
        return
    log(f"  enrichment: {len(todo)} companies (website + logo)...")
    wd = wikidata_lookup(todo, ua)

    def work(sym):
        name = symbols_with_names[sym]
        ov = overrides.get(sym, {})
        w = pick_wikidata(wd.get(sym), name)
        website = ov.get("website") or nasdaq_website(sym) or w.get("website")
        sources = []
        if ov.get("logo"):
            sources.append(("override:" + ov["logo"], ov["logo"]))
        sources.append(("wikidata", w.get("logo")))
        if not w.get("logo"):
            sources.append(("wikipedia", wikipedia_logo(w.get("article"), w.get("label") or name, ua)))
        sources += [
            ("parqet", f"https://assets.parqet.com/logos/symbol/{urllib.parse.quote(sym)}?format=svg"),
            ("fmp", f"https://financialmodelingprep.com/image-stock/{urllib.parse.quote(sym.replace('.', '-'))}.png"),
        ]
        logo, src = download_logo(sym, sources, ua)
        return sym, {
            "label": w.get("label") or "",
            "website": website or "",
            "logo": logo,
            "logoSource": src,
            "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    with ThreadPoolExecutor(max_workers=6) as ex:
        for n, (sym, info) in enumerate(ex.map(work, todo), 1):
            c = companies.setdefault(sym, {})
            c.update(info)
            if n % 25 == 0:
                log(f"    {n}/{len(todo)}")
    got = sum(1 for s in todo if companies[s].get("logo"))
    log(f"  enrichment done: logos {got}/{len(todo)}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, help="weeks ahead to fetch (default from config)")
    ap.add_argument("--behind", type=int, help="past weeks to refresh (default from config)")
    ap.add_argument("--force-enrich", action="store_true", help="re-download every logo/website")
    ap.add_argument("--retry-fallback-logos", action="store_true",
                    help="retry companies whose logo came from an icon fallback (parqet/fmp)")
    args = ap.parse_args()

    cfg = load_config()
    overrides = load_overrides()
    companies = load_json(COMPANIES_FILE, {})
    ua = f"EarningsCalendarBot/1.0 (+{cfg.get('siteUrl', 'https://example.com')})"
    os.makedirs(LOGO_DIR, exist_ok=True)

    cur = current_week(cfg)
    ahead = args.weeks if args.weeks is not None else cfg.get("weeksAhead", 6)
    behind = args.behind if args.behind is not None else cfg.get("weeksBehindRefresh", 1)
    mondays = [cur + timedelta(weeks=i) for i in range(-behind, ahead + 1)]
    store_min = cfg.get("storeMinMarketCap", 2_000_000_000)
    hints = fetch_finnhub(mondays[0], mondays[-1] + timedelta(days=4))

    failures = 0
    touched = {}
    for monday in mondays:
        path = os.path.join(WEEKS_DIR, f"{monday.isoformat()}.json")
        wk = load_json(path, {"week": monday.isoformat(), "days": {}})
        for d in week_days(monday):
            ds = d.isoformat()
            try:
                rows = fetch_day(d)
            except Exception as e:
                failures += 1
                log(f"  ! {ds}: {e} (keeping previous data)")
                continue
            rows = [r for r in rows if r["marketCap"] >= store_min or overrides.get(r["symbol"], {}).get("include")]
            for r in rows:
                if (r["symbol"], ds) in hints:
                    r["hint"] = hints[(r["symbol"], ds)]
                if r["time"] in ("bmo", "amc"):  # remember for next quarter's TBD guesses
                    c = companies.setdefault(r["symbol"], {})
                    c["lastTime"], c["lastTimeDate"] = r["time"], ds
            if not rows and wk["days"].get(ds):
                log(f"  ! {ds}: empty response, keeping previous data")
                continue
            wk["days"][ds] = rows
            time.sleep(0.4)
        wk["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_json(path, wk)
        touched[monday] = wk
        n = sum(len(v) for v in wk["days"].values())
        log(f"week {monday}: {n} rows stored")

    need = {}
    for wk in touched.values():
        for rows in wk["days"].values():
            for r in select_rows(rows, cfg, overrides):
                need.setdefault(r["symbol"], r["name"])
    optimize_existing_logos(companies)
    enrich(need, companies, overrides, ua, force=args.force_enrich, retry_fallback=args.retry_fallback_logos)
    save_json(COMPANIES_FILE, companies)

    total_days = len(mondays) * 5
    if failures == total_days:
        log("ERROR: every request to Nasdaq failed.")
        sys.exit(1)
    log(f"done. {failures} failed day(s) of {total_days}.")


if __name__ == "__main__":
    main()
