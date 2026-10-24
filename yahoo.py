"""Yahoo Finance price reaction and options-implied move, ported from earnings-copilot's server.py.
curl, not urllib: the crumb endpoint needs a cookie jar and curl's TLS/HTTP2 stack gets past Yahoo's edge."""
import json, re, subprocess, tempfile, threading, time, urllib.parse
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
JAR = tempfile.NamedTemporaryFile(prefix="yahoo-", suffix=".jar", delete=False).name
LOCK = threading.Lock()
CRUMB = [""]


def curl(url):
    return subprocess.run(["curl", "-s", "--compressed", "-A", UA, "-b", JAR, "-c", JAR, "-m", "15", url],
                          capture_output=True, timeout=30).stdout.decode("utf-8", "replace")


def crumb(fresh=False):
    with LOCK:
        if fresh:
            CRUMB[0] = ""
        for _ in range(3):  # the edge 429s in short bursts; wait one out
            if CRUMB[0]:
                break
            curl("https://fc.yahoo.com")
            c = curl("https://query1.finance.yahoo.com/v1/test/getcrumb").strip()
            if re.match(r"^[A-Za-z0-9._~+/=-]{5,}$", c):
                CRUMB[0] = c
            else:
                time.sleep(1.5)
        return CRUMB[0]


def get(path, key, **q):
    """One Yahoo API call; retries with a fresh crumb, since the edge rotates sessions."""
    for i in range(3):
        url = f"https://query{2 if key != 'chart' else 1}.finance.yahoo.com{path}?" + urllib.parse.urlencode({**q, "crumb": crumb(i > 0)})
        try:
            res = json.loads(curl(url))[key]["result"]
            if res:
                return res[0]
        except (ValueError, KeyError, TypeError):
            pass
        time.sleep(1.5)
    raise RuntimeError(f"Yahoo {key} failed for {path}")


def day(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET).date().isoformat()


def reaction(qs, chart, current=None):
    """Two/five completed sessions after release, from the last unaffected close.

    Exact Yahoo release timestamps classify historic reports. Midnight/date-only
    timestamps are not evidence of before/after-market timing and are omitted.
    A dated earnings event can supply the current report's published timing.
    """
    hist = []
    for q in (qs.get("earnings") or {}).get("earningsChart", {}).get("quarterly") or []:
        stamp = (q.get("reportedDate") or {}).get("raw")
        if (q.get("actual") or {}).get("raw") is None or not stamp: continue
        utc = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        if utc.hour == 0 and utc.minute == 0 and utc.second == 0: continue
        at = utc.astimezone(ET)
        hour = at.hour + at.minute / 60
        timing = "bmo" if 4 <= hour < 9.5 else "amc" if 16 <= hour < 22 else None
        rep = at.date().isoformat()
        period = (q.get("periodEndDate") or {}).get("raw")
        if current and period and datetime.fromtimestamp(int(period), timezone.utc).date().isoformat() == current.get("period_end"):
            rep = current["date"]
        if current and rep == current.get("date"): timing = current.get("hour") or timing
        hist.append((rep, timing, int(stamp)))
    if current and current.get("date") and not any(h[0] == current["date"] for h in hist):
        hist.append((current["date"], current.get("hour"), None))
    dates = [day(t) for t in chart.get("timestamp") or []]
    closes = chart["indicators"]["quote"][0]["close"]
    now = datetime.now(ET); today = now.date().isoformat()
    session_end = ((chart.get("meta") or {}).get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    session_end = session_end or now.replace(hour=16, minute=0, second=0, microsecond=0).timestamp()
    closed = [d < today or d == today and now.timestamp() >= session_end for d in dates]
    def cl(i): return closes[i] if 0 <= i < len(closes) and closed[i] and closes[i] else None
    rows = []
    for rep, timing, stamp in hist:
        if timing not in ("bmo", "amc"): continue
        first = next((i for i, d in enumerate(dates) if d >= rep and (timing == "bmo" or d > rep)), None)
        if first is None or not cl(first - 1) or not cl(first + 1): continue
        base, end = cl(first - 1), cl(first + 1); fifth = cl(first + 4)
        rows.append({"reported": rep, "hour": timing, "release_timestamp": stamp,
                     "baseline_date": dates[first - 1], "baseline_close": base,
                     "end_date": dates[first + 1], "end_close": end,
                     "r2": round((end / base - 1) * 100, 1),
                     "r5": round((fifth / base - 1) * 100, 1) if fifth else None,
                     "r5_end_date": dates[first + 4] if fifth else None})
    rows.sort(key=lambda r: r["reported"], reverse=True)
    recent = rows[:4]
    return {"quarters": rows, "stats": {"avg_abs_r2": round(sum(abs(r["r2"]) for r in recent) / len(recent), 1) if recent else None,
                                          "n": len(recent)}}


def straddle(oc, spot):
    """At-the-money call + put mid as a share of spot."""
    o = (oc.get("options") or [{}])[0]
    calls = {c["strike"]: c for c in o.get("calls") or []}
    puts = {p["strike"]: p for p in o.get("puts") or []}
    ks = calls.keys() & puts.keys()
    if not ks:
        return None
    k = min(ks, key=lambda s: abs(s - spot))
    mid = lambda x: (x["bid"] + x["ask"]) / 2 if x.get("bid") and x.get("ask") else x.get("lastPrice")
    c, p = mid(calls[k]), mid(puts[k])
    return c is not None and p is not None and round((c + p) / spot * 100, 1) or None


def event_move(t, next_date, hour=None, evidence=None):
    """Straddle on the first expiry on or after the report date."""
    oc = get(f"/v7/finance/options/{t}", "optionChain")
    spot, today = oc["quote"]["regularMarketPrice"], datetime.now(ET).date()
    exp = lambda ep: datetime.fromtimestamp(int(ep), tz=timezone.utc).date()  # stamped 00:00 UTC; in ET that is the day before
    eps = [ep for ep in oc.get("expirationDates") or [] if exp(ep) >= today]
    target = date.fromisoformat(next_date) if next_date else today
    ep = next((e for e in eps if exp(e) > target or exp(e) == target and hour != "amc"), None)
    if ep is None:
        return {}
    selected = oc if ep == eps[0] else get(f"/v7/finance/options/{t}", "optionChain", date=ep)
    if evidence is not None: evidence.update(spot=spot, chain=selected, report_date=next_date, report_hour=hour, expiry=exp(ep).isoformat())
    move = straddle(selected, spot)
    return {"move_pct": move, "expiry": exp(ep).isoformat()} if move else {}


def company(t, current=None, evidence=None, next_hour=None):
    """The shape season.market() reads: price, reaction history, next report date, event-expiry implied move."""
    qs = get(f"/v10/finance/quoteSummary/{t}", "quoteSummary", modules="price,earnings,calendarEvents")
    ed = ((qs.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
    nd = day(ed[0]["raw"]) if ed else None
    chart = get(f"/v8/finance/chart/{t}", "chart", range="2y", interval="1d")  # 1y loses the year-old report from the last four
    option_evidence = {}
    try: option = event_move(t, nd, next_hour, option_evidence)
    except Exception as exc: option = {}; option_evidence["error"] = str(exc)[:120]
    if evidence is not None:
        from pathlib import Path
        target = Path(evidence); target.mkdir(parents=True, exist_ok=True)
        (target / f"{t}.json").write_text(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(),
             "quote_source": f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}",
             "chart_source": f"https://query1.finance.yahoo.com/v8/finance/chart/{t}",
             "quoteSummary": qs, "chart": chart, "options": option_evidence}))
    return {"price": {"last": qs["price"]["regularMarketPrice"]["raw"]}, "reaction": reaction(qs, chart, current),
            "earnings": {"next_date": nd}, "options": {"event": option}}


if __name__ == "__main__":
    import sys
    print(json.dumps(company(sys.argv[1] if len(sys.argv) > 1 else "MSFT"), indent=1)[:1500])
