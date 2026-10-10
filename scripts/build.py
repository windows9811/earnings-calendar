#!/usr/bin/env python3
"""Render the static site into dist/earnings/ from data/*.json.

Everything (pages, assets, logos, sitemap) lives inside dist/earnings/, so that one
folder can be uploaded as  https://your-site.com/earnings/  without touching WordPress.

  dist/earnings/index.html              -> 本週（目前這週）
  dist/earnings/2026-10-19/index.html   -> 指定週（網址 = 該週週一）
"""
import glob
import html
import json
import re
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    COMPANIES_FILE, LOGO_DIR, ROOT, WEEKS_DIR, current_week, dedupe_week, display_name,
    load_config, load_json, load_overrides, parse_date, select_rows, week_days,
)

DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "earnings")
SRC = os.path.join(ROOT, "src")
E = html.escape

WD_ZH = ["週一", "週二", "週三", "週四", "週五"]
WD_EN = ["MON", "TUE", "WED", "THU", "FRI"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
          "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def icon(paths, cls="ico"):
    return (f'<svg class="{cls}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{paths}</svg>')


I_SUNRISE = icon('<path d="M12 2v8"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/>'
                 '<path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m8 6 4-4 4 4"/>'
                 '<path d="M16 18a4 4 0 0 0-8 0"/>')
I_MOON = icon('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>')
I_CLOCK = icon('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>')
I_LEFT = icon('<path d="m15 18-6-6 6-6"/>')
I_RIGHT = icon('<path d="m9 18 6-6-6-6"/>')
I_DOWN = icon('<path d="m6 9 6 6 6-6"/>', "ico ico-sm")
I_SHARE = icon('<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>'
               '<line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>')
I_DOWNLOAD = icon('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
                  '<line x1="12" x2="12" y1="15" y2="3"/>')


def fmt_cap(v):
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.0f}B"
    return f"${v / 1e6:.0f}M"


def resolve_time(row, day, company, override):
    if override.get("time") in ("bmo", "amc"):
        return override["time"]
    if row.get("time") in ("bmo", "amc"):
        return row["time"]
    if row.get("hint") in ("bmo", "amc"):
        return row["hint"]
    if company.get("lastTime") in ("bmo", "amc") and company.get("lastTimeDate") != day:
        return company["lastTime"]
    return "tbd"


def build_week(wk, cfg, companies, overrides):
    days = {}
    for d, rows in wk.get("days", {}).items():
        days[d] = select_rows(rows, cfg, overrides)
    days = dedupe_week(days, companies)
    cap_n = int(cfg.get("maxPerWeek", 0) or 0)
    ranked = sorted(((d, r) for d, rows in days.items() for r in rows), key=lambda x: -x[1].get("marketCap", 0))
    if cap_n and len(ranked) > cap_n:
        keep = {(d, r["symbol"]) for d, r in ranked[:cap_n]}
        keep |= {(d, r["symbol"]) for d, r in ranked if overrides.get(r["symbol"], {}).get("include")}
        days = {d: [r for r in rows if (d, r["symbol"]) in keep] for d, rows in days.items()}
    out = {}
    for d, rows in days.items():
        groups = {"bmo": [], "amc": [], "tbd": []}
        for r in rows:
            c = companies.get(r["symbol"], {})
            ov = overrides.get(r["symbol"], {})
            groups[resolve_time(r, d, c, ov)].append({
                "symbol": r["symbol"],
                "name": ov.get("name") or c.get("label") or display_name(r["name"]),
                "website": ov.get("website") or c.get("website")
                or f"https://www.nasdaq.com/market-activity/stocks/{r['symbol'].lower()}/earnings",
                "logo": c.get("logo"),
                "cap": r.get("marketCap", 0),
                "eps": r.get("eps", ""),
            })
        for g in groups.values():
            g.sort(key=lambda x: -x["cap"])
        out[d] = groups
    return out


def card(item, root, hidden):
    title = f"{item['name']}（{item['symbol']}）"
    tip = title + f" · 市值 {fmt_cap(item['cap'])}" + (f" · EPS 預估 {item['eps']}" if item["eps"] else "")
    if item["logo"]:
        inner = (f'<img src="{root}logos/{E(item["logo"])}" alt="{E(item["name"])} logo" loading="lazy" '
                 f'onerror="this.replaceWith(Object.assign(document.createElement(\'b\'),'
                 f'{{className:\'tk\',textContent:\'{E(item["symbol"])}\'}}))">')
    else:
        inner = f'<span class="txt"><b>{E(item["symbol"])}</b><small>{E(item["name"])}</small></span>'
    li_attr = ' class="extra" hidden' if hidden else ""
    return (f'<li{li_attr}><a class="card" href="{E(item["website"])}" '
            f'target="_blank" rel="noopener noreferrer nofollow" title="{E(tip)}">{inner}'
            f'<span class="sr-only">{E(title)}財報，前往官方網站</span></a></li>')


def column(day, key, items, root, visible):
    label = {"bmo": ("盤前", "Before Open", I_SUNRISE), "amc": ("盤後", "After Close", I_MOON),
             "tbd": ("時間未定", "Time TBD", I_CLOCK)}[key]
    lis = "".join(card(it, root, i >= visible) for i, it in enumerate(items)) or '<li class="empty">—</li>'
    more = ""
    if len(items) > visible:
        more = (f'<button type="button" class="more" aria-expanded="false" aria-controls="e-{day}-{key}">'
                f'{I_DOWN}<span>顯示更多 +{len(items) - visible}</span></button>')
    return (f'<div class="col col-{key}"><div class="col-h"><p class="col-zh">{label[2]}{label[0]}'
            f'<span class="col-en-inline">{label[1]}</span></p><p class="col-en">{label[1]}</p></div>'
            f'<ul id="e-{day}-{key}" class="cards">{lis}</ul>{more}</div>')


def render_board(monday, groups, root, visible):
    cols = []
    for i, d in enumerate(week_days(monday)):
        ds = d.isoformat()
        g = groups.get(ds, {"bmo": [], "amc": [], "tbd": []})
        tbd = ""
        if g["tbd"]:
            tbd = f'<div class="tbd-row">{column(ds, "tbd", g["tbd"], root, visible)}</div>'
        cols.append(
            f'<section class="day" data-date="{ds}" aria-label="{WD_ZH[i]} {d.month}/{d.day}">'
            f'<header class="day-h"><span class="zh">{WD_ZH[i]}</span>'
            f'<span class="en">{WD_EN[i]} · {d.month}/{d.day}</span><span class="today-tag">今天</span></header>'
            f'<div class="day-cols">{column(ds, "bmo", g["bmo"], root, visible)}'
            f'{column(ds, "amc", g["amc"], root, visible)}</div>{tbd}</section>')
    return f'<div class="board">{"".join(cols)}</div>'


I_CHEV = ('<svg class="sh-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" fill="none" '
          'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def render_menu(items, depth=0):
    """Main-site menu (config.json "menu"), same structure as george-dewi.com."""
    lis = []
    for it in items:
        kids = it.get("children") or []
        cls = ["sh-item"]
        if kids:
            cls.append("has-sub")
        if it.get("cta"):
            cls.append("sh-cta")
        if it.get("url", "").rstrip("/").endswith("/earnings"):
            cls.append("current")
        arrow = I_CHEV if kids else ""
        sub = f'<ul class="sh-sub lvl-{depth + 1}">{render_menu(kids, depth + 1)}</ul>' if kids else ""
        toggle = ('<button type="button" class="sh-toggle" aria-label="展開子選單" aria-expanded="false">'
                  f'{I_CHEV}</button>') if kids else ""
        lis.append(f'<li class="{" ".join(cls)}"><a href="{E(it.get("url", "#"))}">'
                   f'<span>{E(it["label"])}</span>{arrow}</a>{toggle}{sub}</li>')
    return "".join(lis)


def site_header(cfg, home, here, site):
    menu = cfg.get("menu") or []
    logo = cfg.get("logo")
    brand = (f'<img src="{E(logo)}" alt="{E(cfg.get("maintainer") or site)}" width="175" height="47">' if logo
             else f'<span class="brand-main">{E(site)}</span>')
    if not menu:
        menu = [{"label": "財報行事曆", "url": here}]
    return f"""<header class="site-header">
  <div class="sh-in">
    <a class="sh-logo" href="{E(home)}">{brand}</a>
    <button type="button" class="sh-burger" aria-label="選單" aria-expanded="false" aria-controls="sh-nav"><span></span><span></span><span></span></button>
    <nav id="sh-nav" class="sh-nav" aria-label="主選單"><ul class="sh-menu">{render_menu(menu)}</ul></nav>
  </div>
</header>"""


def cap_zh(v):
    """10000000000 -> '100 億美元'"""
    return f"{v / 1e8:,.0f} 億美元"


def about_faq(cfg):
    """About + FAQ blocks below the calendar (SEO text) and the FAQPage JSON-LD."""
    min_cap = cap_zh(cfg.get("minMarketCap", 1e10))
    per_week = int(cfg.get("maxPerWeek", 0) or 0)
    cap_rule = f"財報旺季時每週依市值取前 {per_week} 家，" if per_week else ""
    about = [
        ("為什麼要看財報行事曆？",
         "財報公布前後，常常是一檔美股一季當中波動最大的時候：營收、獲利或財測只要和市場預期有落差，"
         "股價就可能在盤後或隔天開盤大幅跳動。事先知道<b>哪一天、盤前還是盤後</b>有哪些重點公司公布財報，"
         "就能提前檢視持股、評估要不要調整部位，或是單純避開這段不確定的時間。"
         "這個頁面把每週值得關注的美股財報整理成一張週曆，用繁體中文一眼看完。"),
        ("怎麼使用這份行事曆？",
         "每一欄是一個美股交易日（週一到週五），欄內分成<b>盤前（Before Open）</b>與<b>盤後（After Close）</b>；"
         "還沒公布時段的公司會放在下方「時間未定」。同一時段依市值由大到小排列，今天會以藍色外框標示。"
         "點公司 logo 可以前往該公司官方網站；用上方左右箭頭切換上一週／下一週，也可以按右上角下載整週圖片。"
         "所有日期皆為美東時間（US Eastern Time）。"),
        ("收錄哪些公司？",
         f"以市場關注度為標準，收錄在美國上市、市值約 {min_cap}以上的公司，包含 Apple、Microsoft、NVIDIA 等科技巨頭，"
         f"以及台積電（TSM）等在美掛牌的 ADR。{cap_rule}同一家公司若有多種股別（例如 GOOG／GOOGL）只列一次。"),
    ]
    faq = [
        ("美股財報行事曆是什麼？",
         "美股財報行事曆（US Earnings Calendar）是列出美國上市公司何時公布季度財報的時間表。"
         "本頁每週整理「最受矚目」的公司，以週一到週五分欄，並標示盤前或盤後公布，"
         "讓你快速掌握本週與未來幾週的重點財報。"),
        ("盤前與盤後是什麼意思？換算台灣時間是幾點？",
         "盤前是在美股開盤（美東時間上午 9:30）前公布，開盤就會反應；盤後是在收盤（美東時間下午 4:00）後公布，"
         "先在盤後交易反應、隔天開盤再延續。換算台灣時間：美國夏令時間（約 3 月中到 11 月初）開盤是晚上 9:30、"
         "收盤是清晨 4:00；冬令時間各晚一小時，也就是晚上 10:30 開盤、清晨 5:00 收盤。"),
        ("「時間未定」是什麼意思？",
         "代表公司還沒公布會在盤前或盤後發布財報。多數公司會在財報日前 2～4 週正式公告，"
         "本頁每天自動更新，公司公布後就會移到正確的欄位。"),
        ("美股財報季是什麼時候？",
         "每一季結束後約 2～6 週是財報最集中的「財報季」，大約從 1 月中、4 月中、7 月中與 10 月中開始。"
         "通常由 JPMorgan 等大型銀行率先公布，接下來三到四週進入高峰，"
         "Mag 7 等科技巨頭多半在財報季的第二到第四週登場。"),
        ("為什麼這裡的財報日期跟其他網站不一樣？",
         "公司正式公告之前，多數網站顯示的是依過去公布規律「推估」的日期，公司也可能臨時改期。"
         "本頁每天同步最新資料，公司確認或改期後會自動更新；日期與時段仍請以公司官方公告為準。"),
        ("資料多久更新一次？來源是什麼？",
         "每天自動更新兩次（台北時間早上 6:15 與晚上 7:30），涵蓋本週與未來 6 週的財報，過去週次的頁面也會保留供回顧。"
         "財報日期與時段來自 Nasdaq 財報行事曆，公司官網連結與 logo 則整理自公開資料。"),
    ]
    about_html = "".join(f'<h3>{q}</h3><p>{a}</p>' for q, a in about)
    faq_html = "".join(
        f'<details class="faq-item" open><summary><h3>{q}</h3><span class="faq-ico" aria-hidden="true"></span></summary>'
        f'<p>{a}</p></details>' for q, a in faq)
    block = (f'<section class="info" aria-labelledby="about-h"><p class="info-kicker">ABOUT</p>'
             f'<h2 id="about-h">關於美股財報行事曆</h2>{about_html}</section>'
             f'<section class="info faq" aria-labelledby="faq-h"><p class="info-kicker">FAQ</p>'
             f'<h2 id="faq-h">美股財報行事曆常見問題</h2>{faq_html}</section>')
    ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", "", a)}}
                       for q, a in faq],
    }
    ld_json = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    return block, f'<script type="application/ld+json">{ld_json}</script>'


def range_text(monday):
    fri = monday + timedelta(days=4)
    end = f"{fri.month} 月 {fri.day} 日"
    if fri.year != monday.year:
        end = f"{fri.year} 年 " + end
    return f"{monday.year} 年 {monday.month} 月 {monday.day} 日 – {end}"


def page(cfg, monday, groups, prev_m, next_m, cur_m, updated_at, is_index):
    base = cfg.get("basePath", "").rstrip("/")
    root = "" if is_index else "../"
    here = root or "./"
    site = cfg.get("siteName", "美股財報行事曆")
    visible = int(cfg.get("visiblePerColumn", 10))
    total = sum(len(v) for g in groups.values() for v in g.values())
    fri = monday + timedelta(days=4)
    site_url = cfg.get("siteUrl", "").rstrip("/")
    if "example.com" in site_url:
        site_url = ""
    canonical = site_url + base + ("/earnings/" if is_index else f"/earnings/{monday.isoformat()}/")
    top = sorted((it for g in groups.values() for v in g.values() for it in v), key=lambda x: -x["cap"])[:8]
    title = f"美股財報行事曆 {monday.month}/{monday.day} – {fri.month}/{fri.day}｜本週最受矚目財報 | {site}"
    desc = (f"{range_text(monday)}（美東時間）美股重點財報行事曆，共 {total} 家公司："
            + "、".join(t["name"] for t in top) + " 等，依盤前／盤後整理，每日自動更新。")

    def nav(m, cls, ic, label):
        if m is None:
            return f'<span class="circle {cls} disabled" aria-hidden="true">{ic}</span>'
        return f'<a class="circle {cls}" href="{root}{m.isoformat()}/" aria-label="{label}" data-key="{cls}">{ic}</a>'

    back = "" if monday == cur_m else f'<a class="back" href="{here}">回到本週</a>'
    try:
        upd = datetime.fromisoformat(updated_at).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        upd = "—"
    css_v = str(int(os.path.getmtime(os.path.join(SRC, "style.css"))))
    js_v = str(int(os.path.getmtime(os.path.join(SRC, "app.js"))))
    home = cfg.get("homeLink") or here
    if "example.com" in home:
        home = here
    info_html, faq_ld = about_faq(cfg)
    maintainer = cfg.get("maintainer") or cfg.get("siteTagline", "")
    canon_tags = "" if not site_url else (f'<link rel="canonical" href="{E(canonical)}">\n'
                                          f'<meta property="og:url" content="{E(canonical)}">\n')

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
{canon_tags}<meta property="og:type" content="website">
<meta property="og:title" content="{E(title)}">
<meta property="og:description" content="{E(desc)}">
<meta name="twitter:card" content="summary">
{faq_ld}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=Roboto:wght@400;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{root}assets/style.css?v={css_v}">
</head>
<body>
{site_header(cfg, home, here, site)}
<main class="wrap main">
  <div id="capture" class="capture">
    <div class="hero">
      <h1>本週<span class="accent">最受矚目</span>財報</h1>
      <p class="sub">美股財報行事曆 · The Most Anticipated US Earnings Calendar</p>
      <p class="kicker">FOR THE WEEK BEGINNING {MONTHS[monday.month - 1]} {monday.day}, {monday.year}</p>
    </div>
    <div class="weekbar">
      {nav(prev_m, "prev", I_LEFT, "上一週")}
      <div class="weekbar-text">
        <p>{range_text(monday)}（美東時間），該週目前收錄 <b>{total}</b> 家重點公司的財報。</p>
        {back}
      </div>
      <div class="weekbar-actions">
        {nav(next_m, "next", I_RIGHT, "下一週")}
        <button type="button" class="circle" id="share" aria-label="分享">{I_SHARE}</button>
        <button type="button" class="circle" id="download" aria-label="下載圖片" data-file="earnings-{monday.isoformat()}.png">{I_DOWNLOAD}</button>
      </div>
    </div>
    {render_board(monday, groups, root, visible)}
    <p class="capture-foot">{E(site)}{(" · " + E(canonical)) if site_url else ""}</p>
  </div>
  {info_html}
  <footer class="foot">
    <p>資料來源：Nasdaq Earnings Calendar · 收錄條件：市值 ≥ {cap_zh(cfg.get("minMarketCap", 1e10))} · 每日自動更新 · 最後更新 {upd}（台北時間）</p>
    <p>本頁內容僅供參考，不構成任何投資建議；財報日期與時段以公司正式公告為準。</p>
    <p class="foot-by">由 <a href="{E(home)}">{E(maintainer)}</a> 編輯與維護 · 美股財報行事曆 US Earnings Calendar</p>
  </footer>
</main>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script src="https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js" defer></script>
<script src="{root}assets/app.js?v={js_v}" defer></script>
</body>
</html>
"""


HTACCESS = """DirectoryIndex index.html
<IfModule mod_headers.c>
  <FilesMatch "\\.html$">
    Header set Cache-Control "public, max-age=600, must-revalidate"
  </FilesMatch>
  <FilesMatch "\\.(svg|png|jpg|webp|css|js)$">
    Header set Cache-Control "public, max-age=604800"
  </FilesMatch>
  <FilesMatch "\\.xml$">
    Header set Cache-Control "public, max-age=3600"
  </FilesMatch>
</IfModule>
<IfModule mod_mime.c>
  AddType image/svg+xml .svg
</IfModule>
"""


def main():
    cfg = load_config()
    companies = load_json(COMPANIES_FILE, {})
    overrides = load_overrides()
    base = cfg.get("basePath", "").rstrip("/")

    files = sorted(glob.glob(os.path.join(WEEKS_DIR, "*.json")))
    weeks = [load_json(f, {}) for f in files]
    weeks = [w for w in weeks if w.get("week") and any(w.get("days", {}).values())]
    if not weeks:
        sys.exit("no week data yet — run scripts/update.py first")
    mondays = [parse_date(w["week"]) for w in weeks]

    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(os.path.join(OUT, "assets"))
    for f in ("style.css", "app.js"):
        shutil.copy(os.path.join(SRC, f), os.path.join(OUT, "assets", f))
    if os.path.isdir(LOGO_DIR):
        shutil.copytree(LOGO_DIR, os.path.join(OUT, "logos"))

    cur = current_week(cfg)
    if cur not in mondays:  # fall back to the nearest week we have
        cur = min(mondays, key=lambda m: abs((m - cur).days))
    urls = []
    for i, (m, wk) in enumerate(zip(mondays, weeks)):
        groups = build_week(wk, cfg, companies, overrides)
        prev_m = mondays[i - 1] if i > 0 else None
        next_m = mondays[i + 1] if i + 1 < len(mondays) else None
        out = os.path.join(OUT, m.isoformat())
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
            f.write(page(cfg, m, groups, prev_m, next_m, cur, wk.get("updatedAt", ""), False))
        urls.append(f"/earnings/{m.isoformat()}/")
        if m == cur:
            with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
                f.write(page(cfg, m, groups, prev_m, next_m, cur, wk.get("updatedAt", ""), True))

    site_url = cfg.get("siteUrl", "").rstrip("/") + base
    if "example.com" in site_url:
        site_url = ""
    with open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in ["/earnings/"] + urls:
            f.write(f"  <url><loc>{E(site_url + u)}</loc></url>\n")
        f.write("</urlset>\n")
    # Apache (Cloudways): short cache for pages so daily updates show up, long cache for logos/assets
    with open(os.path.join(OUT, ".htaccess"), "w", encoding="utf-8") as f:
        f.write(HTACCESS)
    print(f"built {len(urls)} week pages -> dist/ (current week {cur})")


if __name__ == "__main__":
    main()
