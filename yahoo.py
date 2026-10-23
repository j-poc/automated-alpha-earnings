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


def reaction(qs, chart):
    """r2 = close two trading days after the report date vs the close before it (BMO and AMC alike)."""
    hist = [(day(q["reportedDate"]["raw"]), q.get("surprisePct"))
            for q in (qs.get("earnings") or {}).get("earningsChart", {}).get("quarterly") or []
            if (q.get("actual") or {}).get("raw") is not None and (q.get("reportedDate") or {}).get("raw")]
    dates = [day(t) for t in chart.get("timestamp") or []]
    closes = chart["indicators"]["quote"][0]["close"]
    now = datetime.now(ET)
    today = now.date().isoformat()
    session_end = ((chart.get("meta") or {}).get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    session_end = session_end or now.replace(hour=16, minute=0, second=0, microsecond=0).timestamp()
    # Yahoo includes today's unfinished daily candle during the trading session.
    closed = [d < today or d == today and now.timestamp() >= session_end for d in dates]
    cl = lambda i: closes[i] if 0 <= i < len(closes) and closed[i] and closes[i] else None
    rows = []
    for rep, sp in hist:
        i0 = next((i for i, d in enumerate(dates) if d >= rep), None)
        if not i0 or not cl(i0 - 1) or not cl(i0 + 1):
            continue
        r5 = cl(i0 + 5)
        rows.append({"reported": rep, "surprise": float(sp) if sp not in (None, "") else None,
                     "r2": round((cl(i0 + 1) / cl(i0 - 1) - 1) * 100, 1),
                     "r5": round((r5 / cl(i0 - 1) - 1) * 100, 1) if r5 else None})
    rows.reverse()  # most recent first
    beats = [r for r in rows if (r["surprise"] or 0) > 0]
    misses = [r for r in rows if r["surprise"] is not None and r["surprise"] <= 0]
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    fade = round(sum(r["r2"] < 0 for r in beats) / len(beats) * 100) if len(beats) >= 2 else None
    beat_r2 = mean([r["r2"] for r in beats])
    verdict = "fades its beats" if fade is not None and fade >= 50 else "rewards beats" if (beat_r2 or 0) > 1 else "mixed"
    return {"quarters": rows, "stats": {"verdict": verdict, "fade_pct": fade, "n_beat": len(beats), "n_miss": len(misses),
                                        "avg_abs_r2": mean([abs(r["r2"]) for r in rows[:4]])}}


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


def event_move(t, next_date):
    """Straddle on the first expiry on or after the report date."""
    oc = get(f"/v7/finance/options/{t}", "optionChain")
    spot, today = oc["quote"]["regularMarketPrice"], datetime.now(ET).date()
    exp = lambda ep: datetime.fromtimestamp(int(ep), tz=timezone.utc).date()  # stamped 00:00 UTC; in ET that is the day before
    eps = [ep for ep in oc.get("expirationDates") or [] if exp(ep) >= today]
    target = date.fromisoformat(next_date) if next_date else today
    ep = next((e for e in eps if exp(e) >= target), None)
    if ep is None:
        return {}
    move = straddle(oc if ep == eps[0] else get(f"/v7/finance/options/{t}", "optionChain", date=ep), spot)
    return {"move_pct": move, "expiry": exp(ep).isoformat()} if move else {}


def company(t):
    """The shape season.market() reads: price, reaction history, next report date, event-expiry implied move."""
    qs = get(f"/v10/finance/quoteSummary/{t}", "quoteSummary", modules="price,earnings,calendarEvents")
    ed = ((qs.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
    nd = day(ed[0]["raw"]) if ed else None
    chart = get(f"/v8/finance/chart/{t}", "chart", range="2y", interval="1d")  # 1y loses the year-old report from the last four
    return {"price": {"last": qs["price"]["regularMarketPrice"]["raw"]}, "reaction": reaction(qs, chart),
            "earnings": {"next_date": nd}, "options": {"event": event_move(t, nd)}}


if __name__ == "__main__":
    import sys
    print(json.dumps(company(sys.argv[1] if len(sys.argv) > 1 else "MSFT"), indent=1)[:1500])
