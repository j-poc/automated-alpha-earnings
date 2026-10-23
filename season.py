"""Q3 2026 earnings season tracker (personal use). Fundamentals from SEC EDGAR; market layer from
Finnhub consensus + Yahoo options/price (yahoo.py).

Each company is compared to itself: revenue and diluted EPS vs the same quarter a year ago
(both columns read from the company's own EX-99.1 press release), and growth this quarter vs
last quarter (prior quarters from XBRL companyfacts). An LLM locates numbers; code verifies
every number appears verbatim in the filing before it is used.

  python3 season.py build     # fetch, extract, tie out, write data/season.json
  python3 season.py render    # data/dashboard.json + out/season.html from the last build
  python3 season.py check     # tie-out report; exit 1 on any failure
  python3 season.py deploy    # out/season.html -> Vercel production (once: vercel project add automated-alpha-earnings)
"""
import hashlib, json, os, re, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import yahoo

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
DATA = ROOT / "data"
UA = "DC dreamcllectr@gmail.com"
SEASON_START = "2026-10-01"
MODEL = "gemini-flash-lite-latest"
EXTRACT_VERSION = "v1"

UNIVERSE = """JPM GS MS BAC WFC C BLK SCHW AXP JNJ UNH ELV ABT ISRG LLY MRK ABBV PFE TMO
NFLX TSLA GOOGL MSFT META AMZN AAPL INTC IBM TXN NOW V MA KO PEP PG PM MCD SBUX CMCSA
VZ T TMUS GE GEV RTX LMT BA NOC HON CAT UPS UNP F GM XOM CVX COP PSX NEE""".split()
NAMES = dict(JPM="JPMorgan Chase", GS="Goldman Sachs", MS="Morgan Stanley", BAC="Bank of America",
    WFC="Wells Fargo", C="Citigroup", BLK="BlackRock", SCHW="Charles Schwab", AXP="American Express",
    JNJ="Johnson & Johnson", UNH="UnitedHealth", ELV="Elevance Health", ABT="Abbott", ISRG="Intuitive Surgical",
    LLY="Eli Lilly", MRK="Merck", ABBV="AbbVie", PFE="Pfizer", TMO="Thermo Fisher", NFLX="Netflix", TSLA="Tesla",
    GOOGL="Alphabet", MSFT="Microsoft", META="Meta", AMZN="Amazon", AAPL="Apple", INTC="Intel", IBM="IBM",
    TXN="Texas Instruments", NOW="ServiceNow", V="Visa", MA="Mastercard", KO="Coca-Cola", PEP="PepsiCo",
    PG="Procter & Gamble", PM="Philip Morris", MCD="McDonald's", SBUX="Starbucks", CMCSA="Comcast",
    VZ="Verizon", T="AT&T", TMUS="T-Mobile", GE="GE Aerospace", GEV="GE Vernova", RTX="RTX",
    LMT="Lockheed Martin", BA="Boeing", NOC="Northrop Grumman", HON="Honeywell", CAT="Caterpillar",
    UPS="UPS", UNP="Union Pacific", F="Ford", GM="General Motors", XOM="ExxonMobil", CVX="Chevron",
    COP="ConocoPhillips", PSX="Phillips 66", NEE="NextEra Energy")


# ---------------------------------------------------------------- fetching

SEC_LOCK = threading.Lock()


def fetch(url, ttl=None, headers=None, data=None):
    """Disk-cached GET (POST when data is given). ttl=None caches forever (immutable filings)."""
    key = hashlib.sha1((url.split("key=")[0] + (data or b"").decode()).encode()).hexdigest()
    p = CACHE / key
    if p.exists() and (ttl is None or time.time() - p.stat().st_mtime < ttl):
        return p.read_text("utf-8", "replace")
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
    body = ""
    for attempt in range(4):
        try:
            if "sec.gov" in url:
                with SEC_LOCK:  # SEC fair-access limit is 10 req/s, shared across threads
                    time.sleep(0.12)
            body = urllib.request.urlopen(req, timeout=240 if data else 60).read().decode("utf-8", "replace")
            break
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 2)
    CACHE.mkdir(exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return body


def cik_map():
    j = json.loads(fetch("https://www.sec.gov/files/company_tickers.json", ttl=7 * 86400))
    return {v["ticker"]: (str(v["cik_str"]).zfill(10), v["title"]) for v in j.values()}


def releases(cik):
    """8-K Item 2.02 filings (earnings releases), newest first."""
    r = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik}.json", ttl=1800))["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        if form == "8-K" and "2.02" in (r["items"][i] or ""):
            accn = r["accessionNumber"][i].replace("-", "")
            out.append({"accession": r["accessionNumber"][i], "filed": r["filingDate"][i],
                        "base": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}"})
    return out


EX99_RE = re.compile(r"(ex[a-z]*99|(^|[^0-9])99)[._\-]?0?1")
BAD_RE = re.compile(r"supplement|commentary|slide|presentation|transcript|webcast|992|993|ex99-?2|ex99-?3")


def ex99_url(rel):
    """Press-release exhibit. Ranking from earnings-copilot's locator (1.5% miss on 2,702 releases)."""
    idx = json.loads(fetch(rel["base"] + "/index.json"))["directory"]["item"]
    cands = []
    for it in idx:
        name = it["name"]; low = name.lower()
        if not low.endswith((".htm", ".html")) or "index" in low or BAD_RE.search(low) or re.search(r"r\d+\.htm$", low):
            continue
        rank = 0 if EX99_RE.search(low) else 1 if "press" in low else 2 if "release" in low else 3 if low[:-4].endswith("pr") else None
        if rank is not None:
            cands.append((rank, len(low), name))
    if cands:
        return rel["base"] + "/" + sorted(cands)[0][2]
    # fall back to the -index.htm table, whose type column is authoritative
    page = fetch(rel["base"] + "/" + rel["accession"] + "-index.htm")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        m = re.search(r'href="([^"]+\.html?)"', row, re.I)
        if m and re.search(r">\s*EX-99(\.1|\.01)?\s*<", row):
            return "https://www.sec.gov" + m.group(1) if m.group(1).startswith("/") else rel["base"] + "/" + m.group(1).split("/")[-1]
    return None


def html_to_text(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"</(tr|p|div|br|h\d|li)>|<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</t[dh]>", " | ", html, flags=re.I)
    txt = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.split("\n")]
    lines = [re.sub(r"(\|\s*)+", "| ", ln).strip(" |") for ln in lines]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------- extraction

PROMPT = """You read a company's quarterly earnings press release (SEC 8-K Exhibit 99.1).
Return JSON only. Copy text EXACTLY as it appears in the document (character for character). Never compute or convert numbers.

{
 "period_end": "YYYY-MM-DD end date of the quarter being reported",
 "fiscal_label": "how the company names the quarter, e.g. 'third quarter 2026' or 'Q1 FY27'",
 "revenue": {"row": "exact table row text containing the quarter's total revenue (current and year-ago values)",
             "label": "exact row label, e.g. 'Total net revenue'",
             "current": "exact current-quarter value string from that row, e.g. '92,512'",
             "prior": "exact year-ago same-quarter value string from that row",
             "scale": "exact unit header text governing that table, e.g. 'In millions, except per share amounts'"},
 "eps": {"row": "exact table row text with GAAP diluted EPS for the quarter (current and year-ago)",
         "label": "exact row label",
         "current": "exact current-quarter value string, e.g. '3.72' or '(0.12)'",
         "prior": "exact year-ago value string"},
 "headline": "one exact sentence from the release that best summarizes the quarter (often CEO quote)",
 "guidance": "one exact sentence giving forward revenue or EPS guidance, or null if none"
}
Rules: revenue = the company's top-line total (banks: total net revenue / net revenue; insurers: total revenues).
Use the three-month columns, not year-to-date. If a field is truly absent use null.

DOCUMENT:
"""


def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}).encode()
    out = json.loads(fetch(url, headers={"Content-Type": "application/json"}, data=body))
    return json.loads(out["candidates"][0]["content"]["parts"][0]["text"])


def norm(s):
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s or "")  # en/em dashes and minus signs
    s = re.sub(r"\.{3,}|[\u200b\xa0]", " ", s)  # dot leaders, zero-width and non-breaking spaces
    return re.sub(r"[\s|$]+", " ", s).strip().lower()


def in_order(text, a, b):
    """a then b as whole numeric tokens in text."""
    tok = lambda v: r"(?<![\d.,])\(?" + re.escape(v) + r"\)?(?![\d]|[.,]\d)"
    return re.search(tok(a) + r".*?" + tok(b), text) is not None


def parse_num(s):
    """'(1,234.5)' -> -1234.5. Returns None if not a plain number."""
    s = (s or "").replace("$", "").replace(",", "").replace("—", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return -float(s) if neg else float(s)


def scale_of(header):
    h = (header or "").lower()
    return 1e9 if "billion" in h else 1e6 if "million" in h else 1e3 if "thousand" in h else None


def verified_pair(doc, field, scale=1.0):
    """Both values must sit inside the quoted row, and the row inside the document."""
    if not field or not field.get("row"):
        return None, "missing"
    row, d = norm(field["row"]), norm(doc)
    vals = [parse_num(field.get(k)) for k in ("current", "prior")]
    if None in vals:
        return None, "value is not a plain number"
    cur, pri = (norm(field[k]).strip("()") for k in ("current", "prior"))
    # The quoted row must be verbatim; failing that, both values must follow the row label within 400 chars.
    label = norm(field.get("label"))
    windows = [row] if row in d else [d[m.end():m.end() + 400] for m in re.finditer(re.escape(label), d)] if label else []
    if not windows:
        return None, "row not found verbatim in filing"
    if not any(in_order(w, cur, pri) for w in windows):
        return None, "values not found in order after the row label"
    return {"current": vals[0] * scale, "prior": vals[1] * scale, "label": field.get("label"), "row": field["row"]}, "ok"


def verified_sentence(doc, s):
    return s if s and norm(s) in norm(doc) else None


def extract(url, feedback=""):
    doc = html_to_text(fetch(url))
    raw = gemini(PROMPT + doc[:120000] + (f"\n\nYOUR PREVIOUS ANSWER FAILED VERIFICATION: {feedback}\n"
                                           "Quote a row from a financial table, copied exactly." if feedback else ""))
    rev_scale = scale_of((raw.get("revenue") or {}).get("scale"))
    if rev_scale and norm(raw["revenue"]["scale"]) not in norm(doc):
        rev_scale = None
    rev, rev_status = verified_pair(doc, raw.get("revenue"), rev_scale or 1.0)
    if rev and not rev_scale:
        rev, rev_status = None, "scale header not verified"
    eps, eps_status = verified_pair(doc, raw.get("eps"))
    return {"period_end": raw.get("period_end"), "fiscal_label": raw.get("fiscal_label"),
            "revenue": rev, "revenue_status": rev_status, "eps": eps, "eps_status": eps_status,
            "headline": verified_sentence(doc, raw.get("headline")),
            "guidance": verified_sentence(doc, raw.get("guidance"))}


# ---------------------------------------------------------------- XBRL

REV_CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenuesNetOfInterestExpense", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "InterestAndNoninterestIncome", "NoninterestIncomeAndNetInterestIncome"]


def quarterly(facts, concept, unit):
    """{end_date: value} for ~3-month durations; Q4 derived as annual minus 9-month YTD when needed."""
    series = (facts.get("us-gaap", {}).get(concept) or {}).get("units", {}).get(unit, [])
    q, annual, ytd9 = {}, {}, {}
    for f in series:
        if "start" not in f:
            continue
        days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
        bucket = q if 80 <= days <= 100 else annual if 350 <= days <= 380 else ytd9 if 260 <= days <= 285 else None
        if bucket is not None:
            bucket[f["end"]] = f["val"]
    if unit == "USD":
        for end, val in annual.items():
            start = date.fromisoformat(end) - timedelta(days=365)
            nine = [v for e, v in ytd9.items() if 0 < (date.fromisoformat(end) - date.fromisoformat(e)).days < 100
                    and date.fromisoformat(e) > start]
            if end not in q and nine:
                q[end] = val - nine[0]
    return q


def near(series, target, tol=10):
    t = date.fromisoformat(target)
    hits = [(abs((date.fromisoformat(e) - t).days), v) for e, v in series.items()]
    hits = [h for h in hits if h[0] <= tol]
    return min(hits)[1] if hits else None


def shift(d, months):
    d = date.fromisoformat(d)
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28)).isoformat()  # near() tolerance absorbs the clamp


def xbrl_context(cik, period_end, rev_prior):
    """Pick the revenue concept matching the press release's year-ago value, then pull
    last quarter's YoY growth from the same concept."""
    facts = json.loads(fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ttl=86400))["facts"]
    yr_ago = shift(period_end, -12)
    best = None
    for c in REV_CONCEPTS:
        s = quarterly(facts, c, "USD")
        v = near(s, yr_ago)
        if v and rev_prior:
            err = abs(v - rev_prior) / abs(rev_prior)
            if best is None or err < best[0]:
                best = (err, c, s, v)
    out = {"concept": None, "tie_err": None, "prev_q_growth": None}
    if best:
        err, c, s, v = best
        out.update(concept=c, xbrl_prior=v, tie_err=err)
        pq, pq_ya = near(s, shift(period_end, -3)), near(s, shift(period_end, -15))
        if pq and pq_ya:
            out["prev_q_growth"] = pq / pq_ya - 1
    eps = quarterly(facts, "EarningsPerShareDiluted", "USD/shares")
    out["xbrl_eps_prior"] = near(eps, yr_ago)
    return out


# ---------------------------------------------------------------- build

def market(t, reported):
    """Consensus, surprise, implied move and price reaction. Never blocks the EDGAR row."""
    m = {}
    try:
        fh = json.loads(fetch(f"https://finnhub.io/api/v1/calendar/earnings?from={SEASON_START}&to=2026-11-30"
                              f"&symbol={t}&token={os.environ['FINNHUB_API_KEY']}", ttl=3 * 3600))["earningsCalendar"]
        if fh:
            e = fh[-1]
            m.update(date=e["date"], hour=e["hour"], eps_est=e["epsEstimate"], eps_act=e["epsActual"],
                     rev_est=e["revenueEstimate"], rev_act=e["revenueActual"])
            if e["epsActual"] is not None and e["epsEstimate"]:
                m["eps_surprise"] = e["epsActual"] / abs(e["epsEstimate"]) - 1 if e["epsEstimate"] > 0 else None
            if e["revenueActual"] and e["revenueEstimate"]:
                m["rev_surprise"] = e["revenueActual"] / e["revenueEstimate"] - 1  # same (Finnhub) basis both sides
    except Exception as e:
        m["finnhub_error"] = str(e)[:120]
    try:
        p = CACHE / f"yahoo-{t}.json"
        if p.exists() and time.time() - p.stat().st_mtime < 3 * 3600:
            d = json.loads(p.read_text())
        else:
            d = yahoo.company(t)
            CACHE.mkdir(exist_ok=True)
            p.write_text(json.dumps(d))
        m["price"] = d["price"]["last"]
        rx = d["reaction"]
        m["history"] = {k: rx["stats"].get(k) for k in ("verdict", "fade_pct", "n_beat", "n_miss", "avg_abs_r2")}
        if reported:
            q = next((q for q in rx["quarters"] if q.get("reported") and abs(
                (date.fromisoformat(q["reported"]) - date.fromisoformat(reported)).days) <= 3), None)
            if q:
                m["r2"], m["r5"] = q.get("r2"), q.get("r5")
        else:
            ev, nd = d["options"].get("event") or {}, d["earnings"].get("next_date")
            today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
            ahead = nd and (nd > today or nd == today and m.get("hour") == "amc")  # a reading after a before-open release is not pre-report
            if ev.get("move_pct") and ahead and 0 <= (date.fromisoformat(ev["expiry"]) - date.fromisoformat(nd)).days <= 7:
                m["implied"] = {"move_pct": ev["move_pct"], "expiry": ev["expiry"], "taken": today}
            m["next_date"] = nd
    except Exception as e:
        m["yahoo_error"] = str(e)[:120]
    return m


def snapshot_implied(rows):
    """Implied moves vanish after the print, so keep the last pre-report reading on disk."""
    p = DATA / "implied.json"
    snap = json.loads(p.read_text()) if p.exists() else {}
    for r in rows:
        mk = r.get("market", {})
        if mk.get("implied"):
            snap[r["ticker"]] = {**mk["implied"], "report": mk.get("next_date")}
        elif r["ticker"] in snap and r.get("filed"):
            mk["implied"] = snap[r["ticker"]]  # pre-report reading, compared against the realised r2
    p.write_text(json.dumps(snap, indent=1))


def company(t, ciks, old=None):
    cik, name = ciks[t.replace(".", "-")]
    rels = releases(cik)
    cur = [r for r in rels if r["filed"] >= SEASON_START]
    last_year = [r for r in rels if "2025-10-01" <= r["filed"] <= "2025-11-15"]
    row = {"ticker": t, "name": name, "cik": cik}
    if last_year:
        est = date.fromisoformat(last_year[-1]["filed"]) + timedelta(days=364)  # same weekday a year on
        row["expected"] = est.isoformat()
    if cur:
        rel = cur[-1]
        row.update(filed=rel["filed"], accession=rel["accession"], filing_index=rel["base"] + "/")
        url = ex99_url(rel)
        row["source_url"] = url
        if old and old.get("accession") == rel["accession"] and not old.get("error"):
            row.update({k: v for k, v in old.items() if k != "market"})  # same filing, same extractor: no new Gemini call
        elif url:
            try:
                feedback = ""
                for attempt in range(2):  # one retry, told why the first answer failed
                    x = extract(url, feedback)
                    xb = xbrl_context(cik, x["period_end"], x["revenue"]["prior"]) if x["revenue"] and x["period_end"] else {}
                    tied = xb.get("tie_err") is not None and xb["tie_err"] <= TIE_TOL
                    if tied and x["eps_status"] == "ok":
                        break
                    feedback = f"revenue: {x['revenue_status']}; eps: {x['eps_status']}."
                    if xb.get("xbrl_prior") and not tied:
                        feedback += (f" Your year-ago revenue {x['revenue']['prior']:,.0f} does not match the company's "
                                     f"originally filed {xb['xbrl_prior']:,.0f} (USD); find the consolidated total revenue row.")
                row.update(x, xbrl=xb, attempts=attempt + 1)
                if x["revenue_status"] == x["eps_status"] == "missing":
                    row["not_earnings"] = True  # Item 2.02 8-K without results (deliveries, guidance updates)
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"[:300]
    row["market"] = market(t, row.get("filed"))
    print(t, row.get("filed", "—"), row.get("revenue_status", ""), row.get("eps_status", ""), file=sys.stderr)
    return row


def build():
    ciks = cik_map()
    p = DATA / "season.json"
    prev = json.loads(p.read_text()) if p.exists() else {}
    old = {r["ticker"]: r for r in prev.get("companies", [])} if prev.get("extract") == f"{MODEL}/{EXTRACT_VERSION}" else {}
    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(lambda t: company(t, ciks, old.get(t)), UNIVERSE))
    DATA.mkdir(exist_ok=True)
    snapshot_implied(rows)
    out = {"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "extract": f"{MODEL}/{EXTRACT_VERSION}", "companies": rows}
    (DATA / "season.json").write_text(json.dumps(out, indent=1))
    return out


TIE_TOL = 0.005


XCHECK_TOL = 0.05


def hold_reason(r):
    """Why a reported company's numbers stay off the page, or None when they tie out."""
    x = r.get("xbrl") or {}
    if not r.get("revenue"):
        return "Couldn't read revenue reliably from the press release"
    if x.get("tie_err") is None:
        return "No prior-year filing to check the numbers against"
    if x["tie_err"] > TIE_TOL:
        return (f"Last year's revenue is ${r['revenue']['prior'] / 1e9:.1f}B in the release but "
                f"${x['xbrl_prior'] / 1e9:.1f}B as originally filed, likely restated or reorganized")
    fh = (r.get("market") or {}).get("rev_act")
    if fh and abs(r["revenue"]["current"] / fh - 1) > XCHECK_TOL:
        return (f"Revenue is ${r['revenue']['current'] / 1e9:.1f}B in the release but ${fh / 1e9:.1f}B at Finnhub, "
                f"likely reported vs adjusted revenue")
    return None


def check(data=None):
    """Pass condition: every published row ties to XBRL (year-ago, within 0.5%) and to Finnhub's
    reported actual (current, within 5%), else it is withheld; and market data (Yahoo) reached most rows."""
    data = data or json.loads((DATA / "season.json").read_text())
    ok, held = [], []
    for r in data["companies"]:
        if not r.get("filed") or r.get("not_earnings"):
            continue
        why = hold_reason(r)
        (held.append((r["ticker"], why)) if why else ok.append(r["ticker"]))
    priced = sum(bool((r.get("market") or {}).get("price")) for r in data["companies"])
    print(f"published: {len(ok)}  withheld: {len(held)}  priced: {priced}/{len(data['companies'])}")
    for t, why in held:
        print("  HOLD", t, why)
    return priced >= 0.8 * len(data["companies"])


def logo(t):
    """64px PNG logo as a data URI (Finnhub profile2), cached in cache/logos; None if unavailable."""
    import base64, io
    from PIL import Image
    p = CACHE / "logos" / f"{t}.png"
    if not p.exists():
        try:
            url = json.loads(fetch(f"https://finnhub.io/api/v1/stock/profile2?symbol={t}"
                                   f"&token={os.environ['FINNHUB_API_KEY']}", ttl=30 * 86400)).get("logo")
            if not url:
                return None
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            im = Image.open(io.BytesIO(raw)).convert("RGBA")
            im.thumbnail((64, 64))
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p, optimize=True)
        except Exception as e:
            print("logo", t, e, file=sys.stderr)
            return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def export(data=None):
    """One flat row per company for the dashboard: data/dashboard.json."""
    data = data or json.loads((DATA / "season.json").read_text())
    g = lambda a, b: a / b - 1 if a is not None and b else None
    rows = []
    for r in data["companies"]:
        mk, x = r.get("market") or {}, r.get("xbrl") or {}
        imp, hist = mk.get("implied") or {}, mk.get("history") or {}
        status = ("upcoming" if not r.get("filed") else "not_earnings" if r.get("not_earnings")
                  else "withheld" if hold_reason(r) else "reported")
        row = {"ticker": r["ticker"], "name": NAMES.get(r["ticker"], r["name"].title()), "status": status,
               "date": r.get("filed") or mk.get("next_date") or mk.get("date") or r.get("expected"),
               "hour": mk.get("hour"), "price": mk.get("price"),
               "implied_move": imp.get("move_pct"), "verdict": hist.get("verdict"),
               "avg_abs_r2": hist.get("avg_abs_r2"), "eps_est": mk.get("eps_est"), "rev_est": mk.get("rev_est"),
               "reason": hold_reason(r) if status == "withheld" else None, "source_url": r.get("source_url"),
               "logo": logo(r["ticker"])}
        if status == "reported":
            rev, eps = r["revenue"], r.get("eps") or {}
            yoy, prev = g(rev["current"], rev["prior"]), x.get("prev_q_growth")
            row.update(revenue=rev["current"], rev_prior=rev["prior"], yoy=yoy, prev_yoy=prev,
                       accel=yoy - prev if prev is not None else None,
                       eps=eps.get("current"), eps_prior=eps.get("prior"),
                       eps_yoy=g(eps.get("current"), eps.get("prior")) if (eps.get("prior") or 0) > 0 else None,
                       eps_surprise=mk.get("eps_surprise"), rev_surprise=mk.get("rev_surprise"),
                       r2=mk.get("r2"), r5=mk.get("r5"), headline=r.get("headline"), guidance=r.get("guidance"),
                       quarter=r.get("fiscal_label"))
        rows.append(row)
    out = {"generated_at": data["generated_at"], "rows": rows}
    (DATA / "dashboard.json").write_text(json.dumps(out, indent=1))
    return out


def render(data=None):
    """Self-contained page: page.html with the export inlined, written to out/season.html."""
    data = data or export()
    blob = json.dumps(data).replace("</", "<\\/")  # filing text must not close the <script>
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "season.html").write_text((ROOT / "page.html").read_text().replace("/*DATA*/null", blob))


def publishable(r):
    """A company's numbers appear on the page only if its year-ago revenue tied out."""
    x = r.get("xbrl") or {}
    return bool(r.get("revenue")) and x.get("tie_err") is not None and x["tie_err"] <= TIE_TOL


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        render(export(build())); sys.exit(0 if check() else 1)
    elif cmd in ("export", "render"):
        render()
    elif cmd == "check":
        sys.exit(0 if check() else 1)
    elif cmd == "deploy":
        site = ROOT / "out" / "automated-alpha-earnings"
        site.mkdir(exist_ok=True)
        (site / "index.html").write_bytes((ROOT / "out" / "season.html").read_bytes())
        tok = ["--token", os.environ["VERCEL_TOKEN"]] if os.environ.get("VERCEL_TOKEN") else []  # CI; locally the CLI login
        sys.exit(subprocess.run(["vercel", "deploy", str(site), "--prod", "--yes", "--project", site.name, "--scope", "evoa2", *tok]).returncode)
