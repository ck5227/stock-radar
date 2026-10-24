"""選股與持股訊號分析：Mark Minervini (SEPA / Trend Template) 與 Warren Buffett (價值投資)。

資料來源：Yahoo Finance (yfinance)。股價約有 15~20 分鐘延遲。
"""
import math
import threading
import time

import pandas as pd
import yfinance as yf

PRICE_TTL = 60          # 股價快取秒數
FUND_TTL = 12 * 3600    # 財報快取秒數

_cache = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def num(x, nd=4):
    """轉成可 JSON 化的 float，NaN/None 回傳 None。"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def normalize_symbol(s):
    s = s.strip().upper()
    if s.isdigit():          # 台股只輸入代號時預設上市
        s += ".TW"
    return s


def benchmark_for(sym):
    return "^TWII" if sym.endswith((".TW", ".TWO")) else "^GSPC"


# ---------------------------------------------------------------- 資料抓取

def get_history(sym):
    def fetch():
        h = yf.Ticker(sym).history(period="2y", auto_adjust=True)
        if h is None or h.empty or len(h) < 30:
            raise ValueError(f"找不到 {sym} 的股價資料")
        h = h[["Open", "High", "Low", "Close", "Volume"]].dropna()
        h.index = h.index.tz_localize(None)
        return h
    return _cached(("h", sym), PRICE_TTL, fetch)


def resolve(sym):
    """回傳 (實際代號, 股價歷史)。上市 .TW 找不到時改試上櫃 .TWO。"""
    sym = normalize_symbol(sym)
    try:
        return sym, get_history(sym)
    except ValueError:
        if sym.endswith(".TW"):
            alt = sym[:-3] + ".TWO"
            return alt, get_history(alt)
        raise


def _row(df, *names):
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            return s.sort_index() if not s.empty else None   # 舊 -> 新
    return None


def get_fundamentals(sym):
    def fetch():
        t = yf.Ticker(sym)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass

        def safe(attr):
            try:
                return getattr(t, attr)
            except Exception:
                return None

        inc, qinc, cf = safe("income_stmt"), safe("quarterly_income_stmt"), safe("cashflow")
        net_income = _row(inc, "Net Income", "Net Income Common Stockholders")
        fcf = _row(cf, "Free Cash Flow")
        q_eps = _row(qinc, "Diluted EPS", "Basic EPS")
        q_rev = _row(qinc, "Total Revenue", "Operating Revenue")

        def yoy(series):
            # 最新一季 vs 去年同季；資料不足則回傳 None
            if series is None or len(series) < 5:
                return None
            prev = series.iloc[-5]
            if prev is None or prev <= 0:
                return None
            return series.iloc[-1] / prev - 1

        q_eps_growth = yoy(q_eps)
        if q_eps_growth is None:
            q_eps_growth = info.get("earningsQuarterlyGrowth") or info.get("earningsGrowth")
        q_rev_growth = yoy(q_rev)
        if q_rev_growth is None:
            q_rev_growth = info.get("revenueGrowth")

        return {
            "name": info.get("longName") or info.get("shortName") or sym,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "currency": info.get("currency"),
            "financial_currency": info.get("financialCurrency"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": (info["debtToEquity"] / 100) if info.get("debtToEquity") is not None else None,
            "gross_margin": info.get("grossMargins"),
            "net_margin": info.get("profitMargins"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "shares": info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"),
            "fcf_latest": info.get("freeCashflow"),
            "net_income": [float(x) for x in net_income] if net_income is not None else [],
            "fcf": [float(x) for x in fcf] if fcf is not None else [],
            "q_eps_growth": q_eps_growth,
            "q_rev_growth": q_rev_growth,
        }
    return _cached(("f", sym), FUND_TTL, fetch)


# ---------------------------------------------------------------- Minervini

def _ret(c, n):
    if len(c) <= n:
        return c.iloc[-1] / c.iloc[0] - 1
    return c.iloc[-1] / c.iloc[-n - 1] - 1


def weighted_perf(c):
    """IBD 式相對強度：近 3 個月權重加倍。"""
    return 0.4 * _ret(c, 63) + 0.2 * _ret(c, 126) + 0.2 * _ret(c, 189) + 0.2 * _ret(c, 252)


def minervini(h, bench_close, fund):
    c, v = h["Close"], h["Volume"]
    last = c.iloc[-1]
    ma50, ma150, ma200 = (c.rolling(n).mean() for n in (50, 150, 200))
    ema10, ema21 = c.ewm(span=10, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    yr = h.iloc[-252:]
    hi52, lo52 = yr["High"].max(), yr["Low"].min()
    m50, m150, m200 = ma50.iloc[-1], ma150.iloc[-1], ma200.iloc[-1]
    m200_1m = ma200.iloc[-22] if len(ma200.dropna()) > 22 else float("nan")

    stock_wp = weighted_perf(c)
    bench_wp = weighted_perf(bench_close) if bench_close is not None else 0.0
    rs_ratio = (1 + stock_wp) / (1 + bench_wp)

    def crit(label, ok, value=None):
        return {"label": label, "pass": None if ok is None else bool(ok), "value": value}

    have200 = not math.isnan(m200)
    criteria = [
        crit("股價 > 150日 & 200日均線", (last > m150 and last > m200) if have200 else None),
        crit("150日均線 > 200日均線", (m150 > m200) if have200 else None),
        crit("200日均線上揚至少 1 個月", (m200 > m200_1m) if not math.isnan(m200_1m) else None),
        crit("50日均線 > 150日 & 200日均線", (m50 > m150 and m50 > m200) if have200 else None),
        crit("股價 > 50日均線", last > m50),
        crit("股價高於 52 週低點 30% 以上", last >= lo52 * 1.30, f"{(last / lo52 - 1) * 100:.0f}%"),
        crit("股價距 52 週高點 25% 以內", last >= hi52 * 0.75, f"-{(1 - last / hi52) * 100:.0f}%"),
        crit("相對強度勝過大盤 (RS)", rs_ratio > 1.0, f"{rs_ratio:.2f}x"),
    ]
    tt_pass = sum(1 for x in criteria if x["pass"])

    # 波動收縮 (VCP)：三段區間振幅遞減 + 量縮
    def rng(seg):
        return (seg["High"].max() - seg["Low"].min()) / seg["High"].max()
    r1, r2, r3 = rng(h.iloc[-60:-40]), rng(h.iloc[-40:-20]), rng(h.iloc[-20:])
    avg_vol50 = v.iloc[-50:].mean()
    vol_dry = v.iloc[-10:].mean() < avg_vol50 * 0.9
    vcp = r1 > r2 > r3 and r3 < 0.12 and vol_dry

    pivot = h["High"].iloc[-21:-1].max()          # 近 4 週高點作為樞紐點
    dist_pivot = last / pivot - 1
    vol_ratio = v.iloc[-1] / avg_vol50 if avg_vol50 else None
    breakout = last > pivot and vol_ratio is not None and vol_ratio >= 1.4
    in_buy_zone = 0 <= dist_pivot <= 0.05

    # SEPA 基本面：盈餘與營收成長
    eps_g, rev_g = fund.get("q_eps_growth"), fund.get("q_rev_growth")
    sepa = [
        crit("最近一季 EPS 年增 ≥ 25%", None if eps_g is None else eps_g >= 0.25,
             None if eps_g is None else f"{eps_g * 100:.0f}%"),
        crit("最近一季營收年增 ≥ 20%", None if rev_g is None else rev_g >= 0.20,
             None if rev_g is None else f"{rev_g * 100:.0f}%"),
    ]

    if have200 and last < m200 and m200 < m200_1m:
        stage = "第四階段（下跌）"
    elif tt_pass == 8:
        stage = "第二階段（上升）"
    elif have200 and last > m200 and m200 <= m200_1m:
        stage = "第一/三階段（整理）"
    else:
        stage = "非上升趨勢"

    candidate = tt_pass == 8 and (breakout or in_buy_zone or (vcp and -0.08 <= dist_pivot <= 0))
    return {
        "criteria": criteria,
        "tt_pass": tt_pass,
        "sepa": sepa,
        "stage": stage,
        "rs_ratio": num(rs_ratio),
        "stock_perf": num(stock_wp),
        "vcp": bool(vcp),
        "contractions": [num(r1), num(r2), num(r3)],
        "vol_dry": bool(vol_dry),
        "pivot": num(pivot),
        "dist_pivot": num(dist_pivot),
        "breakout": bool(breakout),
        "in_buy_zone": bool(in_buy_zone),
        "vol_ratio": num(vol_ratio),
        "ma50": num(m50), "ma150": num(m150), "ma200": num(m200),
        "ema10": num(ema10.iloc[-1]), "ema21": num(ema21.iloc[-1]),
        "hi52": num(hi52), "lo52": num(lo52),
        "candidate": bool(candidate),
    }


# ---------------------------------------------------------------- Buffett

def _cagr(series):
    s = [x for x in series if x is not None]
    if len(s) < 2 or s[0] <= 0 or s[-1] <= 0:
        return None
    return (s[-1] / s[0]) ** (1 / (len(s) - 1)) - 1


def is_financial(fund):
    """銀行、保險、券商等以資產負債表為主的金融股（Visa、Mastercard 等支付網路除外）。"""
    return fund.get("sector") == "Financial Services" and fund.get("industry") != "Credit Services"


def intrinsic_value(fund):
    """自由現金流折現 (DCF)：前 5 年用歷史獲利成長率（上限 20%），後 5 年減半，折現率 10%，永續成長 3%。"""
    if is_financial(fund):
        return None, None, "金融股不適用自由現金流折現"
    if fund.get("financial_currency") and fund.get("currency") and fund["financial_currency"] != fund["currency"]:
        return None, None, "財報幣別與股價幣別不同，略過估值"
    shares = fund.get("shares")
    fcf = [x for x in fund.get("fcf", []) if x is not None]
    base = fund.get("fcf_latest") or (fcf[-1] if fcf else None)
    if base is None or base <= 0:
        base = sum(fcf[-3:]) / len(fcf[-3:]) if fcf else None
    if not shares or not base or base <= 0:
        return None, None, "自由現金流為負或資料不足"
    g = _cagr(fund.get("net_income", []))
    if g is None:
        g = _cagr(fcf)
    g = min(max(g if g is not None else 0.05, 0.0), 0.20)
    r, tg = 0.10, 0.03
    pv, cash = 0.0, base
    for year in range(1, 11):
        cash *= 1 + (g if year <= 5 else g / 2)
        pv += cash / (1 + r) ** year
    pv += cash * (1 + tg) / (r - tg) / (1 + r) ** 10
    return pv / shares, g, None


def buffett(fund, price):
    fin = is_financial(fund)

    def crit(label, ok, value=None):
        return {"label": label, "pass": None if ok is None else bool(ok), "value": value}

    def pct(x):
        return None if x is None else f"{x * 100:.1f}%"

    roe, de, gm, nm = fund.get("roe"), fund.get("debt_to_equity"), fund.get("gross_margin"), fund.get("net_margin")
    ni, fcf = fund.get("net_income", []), fund.get("fcf", [])
    iv, g, iv_note = intrinsic_value(fund)
    mos = (iv - price) / iv if iv else None

    stable = None
    if len(ni) >= 3:
        stable = all(x > 0 for x in ni) and ni[-1] > ni[0]

    criteria = [
        crit("股東權益報酬率 ROE ≥ 15%", None if roe is None else roe >= 0.15, pct(roe)),
        crit("負債 / 股東權益 < 0.5", None if (de is None or fin) else de < 0.5,
             "金融股不適用" if fin else (None if de is None else f"{de:.2f}")),
        crit("毛利率 ≥ 40%（護城河）", None if (gm is None or fin or gm == 0) else gm >= 0.40,
             "金融股不適用" if fin else pct(gm)),
        crit("淨利率 ≥ 10%", None if nm is None else nm >= 0.10, pct(nm)),
        crit("近年自由現金流皆為正", None if (not fcf or fin) else all(x > 0 for x in fcf),
             "金融股不適用" if fin else (f"{len(fcf)} 年" if fcf else None)),
        crit("獲利穩定成長（各年皆賺錢且成長）", stable,
             None if _cagr(ni) is None else f"年複合 {_cagr(ni) * 100:.1f}%"),
    ]
    if fin:
        pb = fund.get("pb")
        criteria.append(crit("股價淨值比 ≤ 1.5（金融股估值）", None if pb is None else pb <= 1.5,
                             None if pb is None else f"{pb:.2f}"))
    else:
        criteria.append(crit("股價低於內在價值（安全邊際 ≥ 0）", None if mos is None else mos >= 0,
                             iv_note if mos is None else (f"{mos * 100:.0f}%" if mos >= -1 else f"股價為價值的 {1 - mos:.1f} 倍")))

    quality = [x for x in criteria[:-1] if x["pass"] is not None]
    q_score = sum(x["pass"] for x in quality) / len(quality) if quality else None
    applicable = [x for x in criteria if x["pass"] is not None]
    score = sum(x["pass"] for x in applicable)
    cheap = criteria[-1]["pass"]

    if q_score is None:
        verdict = "資料不足"
    elif q_score >= 0.7 and cheap:
        verdict = "優質且價格合理"
    elif q_score >= 0.7:
        verdict = "優質但偏貴"
    elif q_score >= 0.5:
        verdict = "品質普通"
    else:
        verdict = "不符合"

    return {
        "criteria": criteria,
        "score": score,
        "max": len(applicable),
        "quality": num(q_score),
        "intrinsic_value": num(iv, 2),
        "growth_used": num(g),
        "margin_of_safety": num(mos),
        "iv_note": iv_note,
        "verdict": verdict,
        "candidate": bool(q_score is not None and q_score >= 0.7 and cheap),
    }


# ---------------------------------------------------------------- 整合

def analyze(sym):
    sym, h = resolve(sym)
    try:
        bench = get_history(benchmark_for(sym))["Close"]
        bench = bench.reindex(h.index, method="ffill")
    except Exception:
        bench = None
    try:
        fund = get_fundamentals(sym)
    except Exception:
        fund = {"name": sym, "net_income": [], "fcf": []}

    c = h["Close"]
    price, prev = c.iloc[-1], c.iloc[-2]
    tech = minervini(h, bench, fund)
    buff = buffett(fund, price)
    return {
        "symbol": sym,
        "name": fund.get("name", sym),
        "sector": fund.get("sector"),
        "currency": fund.get("currency"),
        "price": num(price, 2),
        "change": num(price / prev - 1),
        "date": h.index[-1].strftime("%Y-%m-%d"),
        "pe": num(fund.get("pe"), 2),
        "minervini": tech,
        "buffett": buff,
    }


def chart_data(sym, days=260):
    sym, h = resolve(sym)
    c = h["Close"]
    df = pd.DataFrame({
        "close": c,
        "ma50": c.rolling(50).mean(),
        "ma150": c.rolling(150).mean(),
        "ma200": c.rolling(200).mean(),
        "volume": h["Volume"],
    }).iloc[-days:]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        **{k: [num(x, 2) for x in df[k]] for k in df.columns},
    }


# ---------------------------------------------------------------- 持股訊號

SEVERITY = {"賣出": 3, "減碼": 2, "持有": 1, "加碼": 0}


def _action(signals):
    if not signals:
        return "持有"
    worst = max(signals, key=lambda s: SEVERITY[s["action"]])
    if SEVERITY[worst["action"]] >= 2:
        return worst["action"]
    return "加碼" if any(s["action"] == "加碼" for s in signals) else "持有"


def position_signals(pos, a, stop_pct=0.075):
    """pos: {symbol, cost, shares, date?, strategy}; a: analyze() 結果。"""
    h = get_history(a["symbol"])
    c, v = h["Close"], h["Volume"]
    t, b = a["minervini"], a["buffett"]
    price, cost = a["price"], float(pos["cost"])
    gain = price / cost - 1

    since = c
    if pos.get("date"):
        try:
            s = c[c.index >= pd.Timestamp(pos["date"])]
            if not s.empty:
                since = s
        except Exception:
            pass
    else:
        since = c.iloc[-60:]
    peak = max(float(since.max()), price)
    peak_gain = peak / cost - 1

    # 停損：預設 7.5%；獲利達停損幅度 3 倍後移到成本價（保本）
    stop = cost * (1 - stop_pct)
    if peak_gain >= 3 * stop_pct:
        stop = max(stop, cost)
    avg_vol50 = v.iloc[-50:].mean()
    vol_ratio = v.iloc[-1] / avg_vol50 if avg_vol50 else 1

    m = []   # Minervini 觀點
    if price <= stop:
        m.append({"action": "賣出", "reason": f"觸及停損價 {stop:.2f}" + ("（保本停利）" if stop >= cost else "")})
    if t["ma200"] and price < t["ma200"]:
        m.append({"action": "賣出", "reason": "跌破 200 日均線，主要趨勢轉弱"})
    if t["ma50"] and price < t["ma50"]:
        if vol_ratio >= 1.5:
            m.append({"action": "賣出", "reason": f"帶量（{vol_ratio:.1f} 倍均量）跌破 50 日均線"})
        else:
            m.append({"action": "減碼", "reason": "跌破 50 日均線，留意是否續弱"})
    run15 = price / c.iloc[-16] - 1 if len(c) > 16 else 0
    if run15 >= 0.25 and gain >= 0.25:
        m.append({"action": "減碼", "reason": f"15 日內急漲 {run15 * 100:.0f}%，可能為高潮噴出，分批獲利"})
    if gain >= 0.20 and t["ema21"] and price < t["ema21"] and vol_ratio >= 1.3:
        m.append({"action": "減碼", "reason": "獲利部位帶量跌破 21 日均線，鎖住部分獲利"})
    if gain < 0:
        m.append({"action": "持有", "reason": "虧損部位不攤平（Minervini 只對賺錢部位加碼）"})
    else:
        if t["tt_pass"] == 8 and t["breakout"] and t["in_buy_zone"]:
            m.append({"action": "加碼", "reason": f"帶量突破樞紐點 {t['pivot']:.2f}，可金字塔式加碼"})
        near_ema = t["ema21"] and abs(price / t["ema21"] - 1) <= 0.02
        if (0.02 <= gain <= 0.25 and t["tt_pass"] >= 7 and near_ema
                and v.iloc[-5:].mean() < avg_vol50 and price > (t["ma50"] or 0)):
            m.append({"action": "加碼", "reason": "回測 21 日均線且量縮，趨勢仍在，可小幅加碼"})

    f = []   # Buffett 觀點
    q, mos, iv = b["quality"], b["margin_of_safety"], b["intrinsic_value"]
    if q is not None and q < 0.5:
        f.append({"action": "賣出", "reason": "基本面已不符合優質企業標準（護城河轉弱）"})
    if iv and price > iv * 2:
        f.append({"action": "減碼", "reason": f"股價超過內在價值 {iv:.2f} 的 2 倍，嚴重高估"})
    if q is not None and q >= 0.7 and mos is not None and mos >= 0.25:
        f.append({"action": "加碼", "reason": f"優質企業且安全邊際 {mos * 100:.0f}%，可逢低加碼"})
    if not f:
        f.append({"action": "持有", "reason": "優質企業長期持有" if (q or 0) >= 0.7 else "基本面普通，持續觀察"})

    m_action, f_action = _action(m), _action(f)
    strategy = pos.get("strategy", "minervini")
    return {
        **pos,
        "symbol": a["symbol"],
        "name": a["name"],
        "currency": a["currency"],
        "price": price,
        "change": a["change"],
        "gain": num(gain),
        "pnl": num((price - cost) * float(pos.get("shares") or 0), 0),
        "peak": num(peak, 2),
        "stop": num(stop, 2),
        "minervini_action": m_action,
        "minervini_signals": m,
        "buffett_action": f_action,
        "buffett_signals": f,
        "action": m_action if strategy == "minervini" else f_action,
    }
