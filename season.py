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
from decimal import Decimal
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
EXTRACT_VERSION = "v2"

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
    """Use the filing's exhibit type before guessing from a filename."""
    page = fetch(rel["base"] + "/" + rel["accession"] + "-index.htm")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        m = re.search(r'href="([^"]+\.html?)"', row, re.I)
        if m and re.search(r">\s*EX-99(\.1|\.01)?\s*<", row, re.I):
            return "https://www.sec.gov" + m.group(1) if m.group(1).startswith("/") else rel["base"] + "/" + m.group(1).split("/")[-1]
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
 "revenue": {"row": "exact table row OR narrative passage containing consolidated revenue for both quarters, or null if in separate rows",
             "current_row": "if separate: exact full row or passage containing the current value; otherwise null",
             "prior_row": "if separate: exact full row or passage containing the year-ago value; otherwise null",
             "label": "exact row label, e.g. 'Total net revenue'",
             "current": "exact current-quarter value string from that row, e.g. '92,512'",
             "prior": "exact year-ago same-quarter value string from that row",
             "scale": "exact unit header text governing that table, e.g. 'In millions, except per share amounts'"},
 "eps": {"row": "exact row or passage with GAAP diluted EPS for both quarters, or null if in separate rows",
         "current_row": "if separate: exact row or passage with current GAAP diluted EPS; otherwise null",
         "prior_row": "if separate: exact row or passage with year-ago GAAP diluted EPS; otherwise null",
         "label": "exact row label",
         "current": "exact current-quarter value string, e.g. '3.72' or '(0.12)'",
         "prior": "exact year-ago value string"},
 "headline": "one exact sentence from the release that best summarizes the quarter (often CEO quote)",
 "guidance": "one exact sentence giving forward revenue or EPS guidance, or null if none"
}
Rules: revenue = the PARENT COMPANY'S CONSOLIDATED GAAP top-line total (banks: total net revenue / net revenue; insurers: total revenues).
Never use an operating segment's revenue or an adjusted / organic / tax-equivalent figure in place of consolidated GAAP revenue.
current and prior must contain ONLY the numeric substring (e.g. "32.3"), without the word billion or million. Put the exact unit word/header in scale.
Use the three-month columns, not year-to-date. Check the year headers: some releases place each year's results in a separate table.
When a row has segment columns and a consolidated company column, use the consolidated company column.
The quote must retain ALL intervening columns (including YTD or last quarter). Do not remove columns to create an invented row.
If the consolidated total appears only in narrative, quote that passage and keep its stated precision and unit (e.g. billion).
Never join separate rows into an invented quote: use current_row and prior_row instead. Keep the metric label in each quote.
If a field is truly absent use null.

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
    tok = lambda v: r"(?<![\d.,(\-])" + re.escape(v) + r"(?![\d)]|[.,]\d)"
    return re.search(tok(a) + r".*?" + tok(b), text) is not None


def parse_num(s):
    """'(1,234.5)' -> -1234.5. Returns None if not a plain number."""
    s = (s or "").replace("$", "").replace(",", "").replace("—", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return -Decimal(s) if neg else Decimal(s)


def scale_of(header):
    h = (header or "").lower()
    return 1e9 if "billion" in h else 1e6 if "million" in h else 1e3 if "thousand" in h else None


def verified_pair(doc, field, scale=1.0):
    """Require exact source quotes for both values, including their signs."""
    if not field or not (field.get("row") or field.get("current_row") and field.get("prior_row")):
        return None, "missing"
    d = norm(doc)
    vals = [parse_num(field.get(k)) for k in ("current", "prior")]
    if None in vals:
        return None, "value is not a plain number"
    cur, pri = (norm(field[k]) for k in ("current", "prior"))
    label = norm(field.get("label"))
    if field.get("row"):
        row = norm(field["row"])
        if row not in d or not label or label not in row:
            return None, "row and label not found verbatim in filing"
        if not in_order(row, cur, pri):
            return None, "values or signs not found in order in quoted row"
    else:
        for key, value in (("current_row", cur), ("prior_row", pri)):
            row = norm(field[key])
            if row not in d or not label or label not in row:
                return None, f"{key} and label not found verbatim in filing"
            if not re.search(r"(?<![\d.,(\-])" + re.escape(value) + r"(?![\d)]|[.,]\d)", row):
                return None, f"value or sign not found in {key}"
    result = {k: field[k] for k in ("label", "row", "current_row", "prior_row") if field.get(k)}
    result.update(current=float(vals[0] * Decimal(str(scale))), prior=float(vals[1] * Decimal(str(scale))))
    return result, "ok"


def verified_sentence(doc, s):
    return s if s and norm(s) in norm(doc) else None


def recheck_cached(doc, field, scales):
    """Revalidate saved source evidence without spending another model request."""
    if not field:
        return None
    quotes = " ".join(field.get(k, "") for k in ("row", "current_row", "prior_row"))
    tokens = re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", quotes)
    for scale in scales:
        values = [[s for s in tokens if parse_num(s) is not None and
                   parse_num(s) * Decimal(str(scale)) == Decimal(str(field[k]))] for k in ("current", "prior")]
        for current in values[0]:
            for prior in values[1]:
                pair, status = verified_pair(doc, {**field, "current": current, "prior": prior}, scale)
                if status == "ok":
                    return pair
    return None


def extract(url, feedback=""):
    doc = html_to_text(fetch(url))
    raw = gemini(PROMPT + doc[:120000] + (f"\n\nYOUR PREVIOUS ANSWER FAILED VERIFICATION: {feedback}\n"
                                           "Copy the actual consolidated row(s) or narrative passage exactly; never invent a quote or change the stated basis." if feedback else ""))
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
                "InterestAndNoninterestIncome", "NoninterestIncomeAndNetInterestIncome", "RegulatedAndUnregulatedOperatingRevenue"]


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
    facts = json.loads(fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ttl=1800))["facts"]
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
        observations = facts["us-gaap"][c]["units"]["USD"]
        same_period = [f for f in observations if f.get("start") and
                       80 <= (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days <= 100 and
                       abs((date.fromisoformat(f["end"]) - date.fromisoformat(yr_ago)).days) <= 10]
        if same_period:
            original = min(same_period, key=lambda f: f.get("filed", "9999"))
            matched = [f for f in same_period if f["val"] == v]
            latest = max(matched, key=lambda f: f.get("filed", "")) if matched else None
            out.update(original_prior=original["val"], comparison_filed=latest.get("filed") if latest else None,
                       comparison_accession=latest.get("accn") if latest else None,
                       comparison_revised=bool(original["val"] and abs(v / original["val"] - 1) > TIE_TOL))
        pq, pq_ya = near(s, shift(period_end, -3)), near(s, shift(period_end, -15))
        if pq and pq_ya and not out.get("comparison_revised"):
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
                m["rev_surprise"] = e["revenueActual"] / e["revenueEstimate"] - 1  # provider basis; reviewed corrections are applied below
    except Exception as e:
        m["finnhub_error"] = str(e)[:120]
    try:
        p = CACHE / f"yahoo-closed-v2-{t}.json"
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
        if old and old.get("accession") == rel["accession"] and old.get("source_url") == url and not old.get("error") and old.get("revenue"):
            doc = html_to_text(fetch(url))
            scales = [s for word, s in (("billion", 1e9), ("million", 1e6), ("thousand", 1e3)) if word in doc.lower()]
            revenue = recheck_cached(doc, old["revenue"], scales)
            eps = recheck_cached(doc, old.get("eps"), [1])
            old = {**old, "revenue": revenue, "eps": eps} if revenue and (eps or not old.get("eps")) else None
        elif old and not old.get("not_earnings"):
            old = None
        if old and old.get("accession") == rel["accession"] and old.get("source_url") == url and not old.get("error"):
            row.update({k: v for k, v in old.items() if k != "market"})  # same filing, same extractor: no new Gemini call
            if row.get("revenue") and row.get("period_end"):
                row["xbrl"] = xbrl_context(cik, row["period_end"], row["revenue"]["prior"])
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
                                     f"XBRL history {xb['xbrl_prior']:,.0f} (USD); check the consolidated total revenue row. "
                                     "A revised comparison can be genuine: do not substitute a different metric just to match it.")
                row.update(x, xbrl=xb, attempts=attempt + 1)
                if x["revenue_status"] == x["eps_status"] == "missing":
                    row["not_earnings"] = True  # Item 2.02 8-K without results (deliveries, guidance updates)
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"[:300]
    row["market"] = market(t, row.get("filed"))
    reconcile_market(row)
    print(t, row.get("filed", "—"), row.get("revenue_status", ""), row.get("eps_status", ""), file=sys.stderr)
    return row


def build():
    ciks = cik_map()
    p = DATA / "season.json"
    prev = json.loads(p.read_text()) if p.exists() else {}
    old = {r["ticker"]: r for r in prev.get("companies", [])} if prev.get("extract", "").split("/")[0] == MODEL else {}
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
        return (f"Comparison needs review: last year's revenue is ${r['revenue']['prior'] / 1e9:.1f}B in the release but "
                f"${x['xbrl_prior'] / 1e9:.1f}B in the XBRL history; the cause has not been verified")
    fh = (r.get("market") or {}).get("rev_act")
    if fh and abs(r["revenue"]["current"] / fh - 1) > XCHECK_TOL:
        return (f"Revenue is ${r['revenue']['current'] / 1e9:.1f}B in the release but ${fh / 1e9:.1f}B at Finnhub, "
                f"the basis difference needs review")
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


def reconcile_market(row):
    """Apply a source-bound comparison review without overwriting raw provider actuals.

    A provider can put segment revenue in its actual field. A 5% cross-check
    does not establish an identical accounting basis. Reviewed corrections stay
    attached to their filing and observed values; changed inputs need review.
    """
    mk = row.setdefault("market", {})
    review = row.get("consensus_reconciliation")
    if not review:
        return
    mk["rev_surprise"], mk["eps_surprise"] = None, None
    mk["eps_basis"] = "needs review"
    mk["market_note"] = "The reviewed comparison inputs changed; revenue and EPS surprises need review."
    rev, eps = row.get("revenue") or {}, row.get("eps") or {}
    if (review.get("source_url") != row.get("source_url") or
            review.get("reported_revenue") != rev.get("current") or
            review.get("provider_revenue") != mk.get("rev_act") or
            review.get("reported_eps") != eps.get("current") or
            review.get("adjusted_eps") != mk.get("eps_act")):
        return
    if mk.get("rev_est") and mk["rev_est"] > 0:
        mk["rev_surprise"] = float(Decimal(str(rev["current"])) / Decimal(str(mk["rev_est"])) - 1)
    if mk.get("eps_est") and mk["eps_est"] > 0:
        mk["eps_surprise"] = float(Decimal(str(mk["eps_act"])) / Decimal(str(mk["eps_est"])) - 1)
    mk["rev_actual_used"] = rev["current"]
    mk["eps_actual_used"] = mk["eps_act"]
    mk["eps_basis"] = "adjusted"
    mk["market_note"] = review["note"]


def export(data=None):
    """One flat row per company for the dashboard: data/dashboard.json."""
    data = data or json.loads((DATA / "season.json").read_text())
    g = lambda a, b: a / b - 1 if a is not None and b else None
    rows = []
    for r in data["companies"]:
        reconcile_market(r)
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
                       eps_basis=mk.get("eps_basis", "provider basis"),
                       market_note=mk.get("market_note"),
                       r2=mk.get("r2"), r5=mk.get("r5"), headline=r.get("headline"), guidance=r.get("guidance"),
                       quarter=r.get("fiscal_label"),
                       comparison_note=("The year-ago revenue comparison changed in a newer filing. Growth versus last quarter is omitted until the bases are reconciled."
                                        if x.get("comparison_revised") else None))
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
        project = os.environ.get("VERCEL_PROJECT_ID", "prj_dO9tkbP1346TU7HTrLnQTuxxuqBJ")
        scope = os.environ.get("VERCEL_ORG_ID", "team_3frY5U1rw8Yv6c0EVZeDBqwB")
        sys.exit(subprocess.run(["vercel", "deploy", str(site), "--prod", "--yes", "--project", project, "--scope", scope, *tok]).returncode)
