#!/usr/bin/env python3
"""ISPH (Ibnsina Pharma, EGX) daily signal engine.

Usage: python3 engine.py --prev data.json --out data.json [--news-assessment news_assessment.json]
                         [--analysis analysis.json]
Fetches prices (Yahoo Finance) + headlines (Google News RSS), computes indicators, a scored
BUY / SELL / HOLD signal with a written reason for every component, and a backtest of the
technical rules. Reads the previously published data.json (signal log, index history, written
analysis) and writes the new data.json that the tracker page loads. Stdlib only.
"""
import argparse, json, math, os, ssl, sys, urllib.request, urllib.parse, datetime as dt
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0"}
BUY_TH, SELL_TH = 4, -4


def get(url):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=UA)
    last = None
    for _ in range(3):
        try:
            return urllib.request.urlopen(req, timeout=30, context=ctx).read()
        except Exception as e:  # retry transient failures
            last = e
    raise last


def yahoo(sym, rng="3y"):
    for host in ("query1", "query2"):
        try:
            raw = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d&events=div")
            r = json.loads(raw)["chart"]["result"][0]
            break
        except Exception as e:
            err = e
    else:
        raise err
    q = r["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(r.get("timestamp", [])):
        c = q["close"][i]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t + r["meta"].get("gmtoffset", 0), dt.timezone.utc).date().isoformat()
        rows.append(dict(d=d, o=q["open"][i] or c, h=q["high"][i] or c, l=q["low"][i] or c, c=c, v=q["volume"][i] or 0))
    divs = [dict(d=dt.datetime.fromtimestamp(v["date"], dt.timezone.utc).date().isoformat(), amt=round(v["amount"], 3))
            for v in (r.get("events", {}).get("dividends", {}) or {}).values()]
    return rows, sorted(divs, key=lambda x: x["d"])


def news_feed():
    items, seen = [], set()
    for q in ('"Ibnsina Pharma"', '"ابن سينا فارما"'):
        try:
            raw = get("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "+when:30d&hl=en-US&gl=US&ceid=US:en")
            for it in ET.fromstring(raw).iter("item"):
                title = (it.findtext("title") or "").strip()
                if title in seen:
                    continue
                seen.add(title)
                try:
                    d = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
                except Exception:
                    d = ""
                src = it.findtext("source") or ""
                if src and title.endswith(" - " + src):
                    title = title[: -len(src) - 3]
                items.append(dict(title=title, source=src, date=d, url=it.findtext("link") or ""))
        except Exception as e:
            print("news fetch failed:", e, file=sys.stderr)
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:14]


# ---------- indicators ----------
def sma(a, n):
    out, s = [None] * len(a), 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(a, n):
    out, k, e = [], 2 / (n + 1), a[0]
    for x in a:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(a, n=14):
    out, ag, al = [None] * len(a), 0.0, 0.0
    for i in range(1, len(a)):
        ch = a[i] - a[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= n:
            ag += g / n; al += l / n
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
        if i >= n:
            out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def stdev(a, n):
    out = [None] * len(a)
    for i in range(n - 1, len(a)):
        w = a[i - n + 1:i + 1]; m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return out


def atr(rows, n=14):
    tr = [rows[0]["h"] - rows[0]["l"]] + [max(r["h"] - r["l"], abs(r["h"] - p["c"]), abs(r["l"] - p["c"])) for p, r in zip(rows, rows[1:])]
    return sma(tr, n)


def build(rows):
    c = [r["c"] for r in rows]; v = [r["v"] for r in rows]
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    return dict(c=c, v=v, s20=sma(c, 20), s50=sma(c, 50), s200=sma(c, 200), rsi=rsi(c), macd=macd, sig=sig,
                hist=[a - b for a, b in zip(macd, sig)], sd20=stdev(c, 20), atr=atr(rows), v20=sma(v, 20))


def f2(x):
    return f"{x:,.2f}"


def technical(I, i):
    """Scored technical components at bar i. Each: (name, score, max, text)."""
    c, out = I["c"][i], []
    s20, s50, s200 = I["s20"][i], I["s50"][i], I["s200"][i]
    if s200 is None:
        return None
    # 1 long-term trend
    if c > s200 and s50 > s200:
        out.append(("Long-term trend", 2, 2, f"Price {f2(c)} is above the 200-day average ({f2(s200)}) and the 50-day ({f2(s50)}) sits above the 200-day: the primary trend is up."))
    elif c < s200 and s50 < s200:
        out.append(("Long-term trend", -2, 2, f"Price {f2(c)} is below the 200-day average ({f2(s200)}) and the 50-day ({f2(s50)}) is below the 200-day: the primary trend is down."))
    elif c > s200:
        out.append(("Long-term trend", 1, 2, f"Price {f2(c)} has reclaimed the 200-day average ({f2(s200)}), but the 50-day ({f2(s50)}) has not crossed above it yet."))
    else:
        out.append(("Long-term trend", -1, 2, f"Price {f2(c)} has slipped under the 200-day average ({f2(s200)}) while the 50-day ({f2(s50)}) is still above it: the uptrend is in doubt."))
    # 2 medium-term trend: slope of the 50-day and price vs 50-day
    slope = (s50 / I["s50"][i - 10] - 1) * 100
    if c > s50 and slope > 0:
        out.append(("Medium-term trend", 1, 1, f"Price is above a rising 50-day average (50-day up {slope:.1f}% in 10 sessions)."))
    elif c < s50 and slope < 0:
        out.append(("Medium-term trend", -1, 1, f"Price is below a falling 50-day average (50-day down {abs(slope):.1f}% in 10 sessions)."))
    else:
        out.append(("Medium-term trend", 0, 1, f"Mixed: price is {'above' if c > s50 else 'below'} the 50-day average, which is {'rising' if slope > 0 else 'falling'} ({slope:+.1f}% in 10 sessions)."))
    # 3 momentum (MACD)
    h, hp = I["hist"][i], I["hist"][i - 3]
    if h > 0 and h > hp:
        out.append(("Momentum (MACD)", 2, 2, "MACD is above its signal line and the gap is widening: buying momentum is building."))
    elif h > 0:
        out.append(("Momentum (MACD)", 1, 2, "MACD is above its signal line, but the gap is narrowing: momentum is positive and fading."))
    elif h < 0 and h < hp:
        out.append(("Momentum (MACD)", -2, 2, "MACD is below its signal line and the gap is widening: selling momentum is building."))
    else:
        out.append(("Momentum (MACD)", -1, 2, "MACD is below its signal line, but the gap is narrowing: selling pressure is easing."))
    # 4 RSI: overbought / oversold with trend context
    r = I["rsi"][i]
    if r < 30:
        s = 2 if c > s200 else 1
        out.append(("RSI (14)", s, 2, f"RSI is {r:.0f}, oversold. " + ("In an uptrend this is usually a dip worth buying." if c > s200 else "A bounce is likely, but the trend is down, so it counts for less.")))
    elif r > 75:
        out.append(("RSI (14)", -2, 2, f"RSI is {r:.0f}, heavily overbought: the rally is stretched and prone to a pullback."))
    elif r > 68:
        out.append(("RSI (14)", -1, 2, f"RSI is {r:.0f}, approaching overbought."))
    elif r < 38:
        out.append(("RSI (14)", 1 if c > s200 else 0, 2, f"RSI is {r:.0f}, near oversold" + (" inside an uptrend." if c > s200 else ", but the trend is down so it earns no credit.")))
    else:
        out.append(("RSI (14)", 0, 2, f"RSI is {r:.0f}, neutral."))
    # 5 Bollinger position
    up, lo = s20 + 2 * I["sd20"][i], s20 - 2 * I["sd20"][i]
    if c < lo:
        out.append(("Bollinger bands", 1, 1, f"Price closed under the lower band ({f2(lo)}): statistically stretched to the downside."))
    elif c > up:
        out.append(("Bollinger bands", -1, 1, f"Price closed over the upper band ({f2(up)}): statistically stretched to the upside."))
    else:
        out.append(("Bollinger bands", 0, 1, f"Price is inside its 20-day bands ({f2(lo)} to {f2(up)})."))
    # 6 volume confirmation
    vr = I["v"][i] / I["v20"][i] if I["v20"][i] else 1
    ch = c / I["c"][i - 1] - 1
    if vr > 1.5 and ch > 0.01:
        out.append(("Volume", 1, 1, f"Up {ch*100:.1f}% on {vr:.1f}x the 20-day average volume: buyers are committing."))
    elif vr > 1.5 and ch < -0.01:
        out.append(("Volume", -1, 1, f"Down {abs(ch)*100:.1f}% on {vr:.1f}x the 20-day average volume: sellers are in control."))
    else:
        out.append(("Volume", 0, 1, f"Volume is {vr:.1f}x its 20-day average: no unusual conviction either way."))
    return out


def backtest(rows, I):
    """Long-only replay of the technical score: buy at score >= BUY_TH, sell at score <= SELL_TH (next-day open)."""
    pos, entry, trades, marks, eq, curve = False, None, [], [], 1.0, []
    start = next(i for i, x in enumerate(I["s200"]) if x is not None) + 10
    for i in range(start, len(rows) - 1):
        sc = sum(t[1] for t in technical(I, i))
        nxt = rows[i + 1]
        if not pos and sc >= BUY_TH:
            pos, entry = True, (nxt["d"], nxt["o"])
            marks.append(dict(d=rows[i]["d"], t="B", p=rows[i]["c"], s=sc))
        elif pos and sc <= SELL_TH:
            pos = False
            ret = nxt["o"] / entry[1] - 1
            eq *= 1 + ret
            trades.append(dict(buy=entry[0], buy_px=round(entry[1], 2), sell=nxt["d"], sell_px=round(nxt["o"], 2), ret=round(ret * 100, 1)))
            marks.append(dict(d=rows[i]["d"], t="S", p=rows[i]["c"], s=sc))
    open_ret = None
    if pos:
        open_ret = rows[-1]["c"] / entry[1] - 1
        trades.append(dict(buy=entry[0], buy_px=round(entry[1], 2), sell=None, sell_px=None, ret=round(open_ret * 100, 1)))
    closed = [t for t in trades if t["sell"]]
    wins = [t for t in closed if t["ret"] > 0]
    bh = rows[-1]["c"] / rows[start + 1]["o"] - 1
    return dict(start=rows[start]["d"], end=rows[-1]["d"], trades=trades, marks=marks, n=len(closed), wins=len(wins),
                strat_ret=round((eq * (1 + (open_ret or 0)) - 1) * 100, 1), buyhold_ret=round(bh * 100, 1),
                avg_win=round(sum(t["ret"] for t in wins) / len(wins), 1) if wins else 0,
                avg_loss=round(sum(t["ret"] for t in closed if t["ret"] <= 0) / max(1, len(closed) - len(wins)), 1),
                in_position=pos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev"); ap.add_argument("--news-assessment"); ap.add_argument("--analysis")
    a = ap.parse_args()
    prev = json.load(open(a.prev)) if a.prev and os.path.exists(a.prev) else {}

    rows, divs = yahoo("ISPH.CA")
    if len(rows) < 260:
        sys.exit("not enough ISPH history returned")
    I = build(rows)
    i = len(rows) - 1
    c, prev_c = rows[i]["c"], rows[i - 1]["c"]
    comps = [dict(group="Technical", name=n, score=s, max=m, text=t) for n, s, m, t in technical(I, i)]

    # ----- market context -----
    market = dict(dates=[], egx30=[])
    if prev.get("market"):
        m = prev["market"]
        market = dict(dates=list(m.get("dates", [])), egx30=list(m.get("egx30", [])))
    try:
        ex, _ = yahoo("^CASE30", "5d")
        for r in ex:
            if r["d"] in market["dates"]:
                market["egx30"][market["dates"].index(r["d"])] = round(r["c"], 1)
            else:
                market["dates"].append(r["d"]); market["egx30"].append(round(r["c"], 1))
        z = sorted(zip(market["dates"], market["egx30"])); market = dict(dates=[x[0] for x in z], egx30=[x[1] for x in z])
    except Exception as e:
        print("EGX30 fetch failed:", e, file=sys.stderr)
    eg = market["egx30"]
    if len(eg) >= 4:
        n = min(len(eg), 20); base = sum(eg[-n:]) / n; dev = (eg[-1] / base - 1) * 100
        s = 1 if dev > 1 else -1 if dev < -1 else 0
        comps.append(dict(group="Market", name="EGX30 index", score=s, max=1,
                          text=f"EGX30 at {eg[-1]:,.0f} is {dev:+.1f}% versus its {n}-session average: the broad market is {'supportive' if s > 0 else 'a headwind' if s < 0 else 'flat'}."))
    elif eg:
        comps.append(dict(group="Market", name="EGX30 index", score=0, max=1, text=f"EGX30 at {eg[-1]:,.0f}. Index history is still being collected, so it is not scored yet."))
    usd = None
    try:
        fx, _ = yahoo("EGP=X", "6mo")
        usd = fx[-1]["c"]; ch20 = (usd / fx[-21]["c"] - 1) * 100
        s = -1 if ch20 > 3 else 0
        comps.append(dict(group="Market", name="USD/EGP", score=s, max=1,
                          text=f"The dollar is at EGP {usd:.2f}, {ch20:+.1f}% in 20 sessions. " + ("A sliding pound raises Ibnsina's financing costs and squeezes fixed-price drug margins." if s < 0 else "The pound is stable enough not to pressure margins or financing costs.")))
        market["usd"], market["usd_ch20"] = round(usd, 2), round(ch20, 1)
    except Exception as e:
        print("FX fetch failed:", e, file=sys.stderr)

    # ----- news -----
    news = news_feed()
    na = dict(score=0, text="No analyst reading of the headlines was supplied for this run, so news is scored neutral.", items=[])
    if a.news_assessment and os.path.exists(a.news_assessment):
        na.update(json.load(open(a.news_assessment)))
    na["score"] = max(-2, min(2, int(na["score"])))
    comps.append(dict(group="News", name="News and fundamentals", score=na["score"], max=2, text=na["text"]))

    score = sum(x["score"] for x in comps)
    action = "BUY" if score >= BUY_TH else "SELL" if score <= SELL_TH else "HOLD"
    log = [x for x in prev.get("log", []) if x.get("date") != rows[i]["d"]]
    earlier = sorted([x for x in log if x.get("date", "") < rows[i]["d"]], key=lambda x: x["date"])
    prev_action = earlier[-1]["signal"] if earlier else None          # signal on the previous trading day
    last_run_action = (prev.get("latest") or {}).get("signal")        # signal reported by the previous run

    w = rows[-252:]
    hi52, lo52 = max(r["h"] for r in w), min(r["l"] for r in w)
    lows20, highs20 = min(r["l"] for r in rows[-20:]), max(r["h"] for r in rows[-20:])
    A = I["atr"][i]
    pos = sorted([x for x in comps if x["score"] > 0], key=lambda x: -x["score"])
    neg = sorted([x for x in comps if x["score"] < 0], key=lambda x: x["score"])
    if action == "BUY":
        head = f"BUY: score {score:+d} clears the +{BUY_TH} buy line. " + " ".join(x["text"] for x in pos[:3])
        if neg: head += " Against that: " + neg[0]["text"]
    elif action == "SELL":
        head = f"SELL: score {score:+d} is under the {SELL_TH} sell line. " + " ".join(x["text"] for x in neg[:3])
        if pos: head += " The one offset: " + pos[0]["text"]
    else:
        head = f"HOLD: score {score:+d} sits between the sell line ({SELL_TH}) and the buy line (+{BUY_TH}). "
        head += ("Leaning negative. " + neg[0]["text"]) if score < 0 and neg else ("Leaning positive. " + pos[0]["text"]) if score > 0 and pos else "The evidence is balanced."
    need_buy, need_sell = BUY_TH - score, score - SELL_TH

    bt = backtest(rows, I)
    latest = dict(
        asof=rows[i]["d"], updated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        close=round(c, 2), prev_close=round(prev_c, 2), change_pct=round((c / prev_c - 1) * 100, 2),
        open=round(rows[i]["o"], 2), high=round(rows[i]["h"], 2), low=round(rows[i]["l"], 2), volume=rows[i]["v"],
        signal=action, prev_signal=prev_action, changed=bool(prev_action and prev_action != action),
        score=score, buy_th=BUY_TH, sell_th=SELL_TH, score_max=sum(x["max"] for x in comps),
        headline=head, components=comps,
        to_buy=f"{need_buy} more point{'s' if need_buy != 1 else ''} needed for a BUY" if action != "BUY" else None,
        to_sell=f"{need_sell} point{'s' if need_sell != 1 else ''} of deterioration would trigger a SELL" if action != "SELL" else None,
        levels=dict(sma20=round(I["s20"][i], 2), sma50=round(I["s50"][i], 2), sma200=round(I["s200"][i], 2),
                    support=round(lows20, 2), resistance=round(highs20, 2), hi52=round(hi52, 2), lo52=round(lo52, 2),
                    atr=round(A, 2), stop=round(c - 2 * A, 2), target=round(c + 3 * A, 2)),
        ind=dict(rsi=round(I["rsi"][i], 1), macd=round(I["macd"][i], 3), macd_sig=round(I["sig"][i], 3),
                 vol_ratio=round(I["v"][i] / I["v20"][i], 2) if I["v20"][i] else None,
                 off_high=round((c / hi52 - 1) * 100, 1), off_low=round((c / lo52 - 1) * 100, 1),
                 ret_1m=round((c / rows[i - 21]["c"] - 1) * 100, 1), ret_3m=round((c / rows[i - 63]["c"] - 1) * 100, 1),
                 ret_1y=round((c / rows[i - 252]["c"] - 1) * 100, 1)),
        market=dict(egx30=eg[-1] if eg else None, egx30_prev=eg[-2] if len(eg) > 1 else None, usd=market.get("usd"), usd_ch20=market.get("usd_ch20")),
        news=news, news_assessment=na, dividends=divs,
        backtest={k: v for k, v in bt.items() if k != "marks"},
    )
    n = 520
    rd = lambda arr: [None if x is None else round(x, 3) for x in arr[-n:]]
    prices = dict(d=[r["d"] for r in rows[-n:]], c=rd(I["c"]), h=rd([r["h"] for r in rows]), l=rd([r["l"] for r in rows]),
                  v=[r["v"] for r in rows[-n:]], s50=rd(I["s50"]), s200=rd(I["s200"]), rsi=rd(I["rsi"]),
                  marks=[m for m in bt["marks"] if m["d"] >= rows[-n]["d"]])
    signal = dict(date=rows[i]["d"], signal=action, score=score, close=round(c, 2), changed=latest["changed"], headline=head,
                  reasons=[dict(name=x["name"], score=x["score"], text=x["text"]) for x in comps if x["score"] != 0])
    log = sorted(log + [signal], key=lambda x: x["date"])[-120:]
    analysis = json.load(open(a.analysis)) if a.analysis and os.path.exists(a.analysis) else prev.get("analysis")
    latest["runs_today"] = ((prev.get("latest") or {}).get("runs_today", 0) + 1) if (prev.get("latest") or {}).get("updated_at", "")[:10] == latest["updated_at"][:10] else 1
    json.dump(dict(latest=latest, prices=prices, market=market, log=log, analysis=analysis), open(a.out, "w"), ensure_ascii=False)
    print(json.dumps(dict(asof=latest["asof"], close=latest["close"], signal=action, prev_day_signal=prev_action, changed=latest["changed"],
                          last_run_signal=last_run_action, notify=bool(last_run_action and last_run_action != action),
                          stop=latest["levels"]["stop"], support=latest["levels"]["support"],
                          score=score, headline=head, backtest={k: bt[k] for k in ("n", "wins", "strat_ret", "buyhold_ret", "avg_win", "avg_loss", "in_position")}), indent=1, ensure_ascii=False))
    for x in comps:
        print(f"  {x['score']:+d}/{x['max']}  {x['name']}: {x['text']}")
    print("HEADLINES:")
    for x in news:
        print(f"  {x['date']} [{x['source']}] {x['title']}")


if __name__ == "__main__":
    main()
