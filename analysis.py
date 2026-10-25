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
    elif s in ("BRKB", "BRKA"):
        s = s[:3] + "-" + s[3]
    elif "." in s and not s.endswith((".TW", ".TWO")) and len(s.rsplit(".", 1)[1]) == 1:
        s = s.replace(".", "-")   # 美股類股代號：BRK.B → Yahoo 的 BRK-B
    return s


def benchmark_for(sym):
    return "^TWII" if sym.endswith((".TW", ".TWO")) else "^GSPC"


INDEX_NAMES = {"^TWII": "台股加權指數", "^GSPC": "美股 S&P 500"}


# ---------------------------------------------------------------- 資料抓取

def get_history(sym):
    def fetch():
        t = yf.Ticker(sym)
        h = t.history(period="2y", auto_adjust=True)
        if h is None or h.empty or len(h) < 30:
            raise ValueError(f"找不到 {sym} 的股價資料")
        h = h[["Open", "High", "Low", "Close", "Volume"]].dropna()
        h.index = h.index.tz_localize(None)
        try:
            meta = t.history_metadata or {}
        except Exception:
            meta = {}
        h.attrs["meta"] = {k: meta.get(k) for k in ("longName", "shortName", "currency")}
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

        inc, qinc, cf, bs = (safe(a) for a in ("income_stmt", "quarterly_income_stmt", "cashflow", "balance_sheet"))
        qbs = safe("quarterly_balance_sheet")
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

        def last(df, *names):
            r = _row(df, *names)
            return float(r.iloc[-1]) if r is not None else None

        def ratio(a, b):
            return a / b if a is not None and b not in (None, 0) else None

        # 雲端主機常被 Yahoo 擋下公司簡介（info），這些指標改由財務報表計算
        def ttm(*names):
            # 近四季合計；季資料不足時用最近一個年度
            r = _row(qinc, *names)
            if r is not None and len(r) >= 4:
                return float(r.iloc[-4:].sum())
            return last(inc, *names)

        def latest_bs(*names):
            v = last(qbs, *names)
            return v if v is not None else last(bs, *names)

        revenue = ttm("Total Revenue", "Operating Revenue")
        gross = ttm("Gross Profit")
        ni_last = ttm("Net Income", "Net Income Common Stockholders")
        equity = latest_bs("Stockholders Equity", "Common Stock Equity")
        debt = latest_bs("Total Debt")
        shares_bs = latest_bs("Ordinary Shares Number", "Share Issued")
        eps_ttm = float(q_eps.iloc[-4:].sum()) if q_eps is not None and len(q_eps) >= 4 else last(inc, "Diluted EPS", "Basic EPS")
        # 沒有毛利、但有利息淨收入 → 銀行／保險等金融業
        looks_financial = gross is None and last(inc, "Net Interest Income", "Interest Income") is not None

        if not info and inc is None and bs is None:
            raise ValueError(f"暫時抓不到 {sym} 的財報")   # 不快取，下次重試

        q_eps_growth = yoy(q_eps)
        if q_eps_growth is None:
            q_eps_growth = info.get("earningsQuarterlyGrowth") or info.get("earningsGrowth")
        q_rev_growth = yoy(q_rev)
        if q_rev_growth is None:
            q_rev_growth = info.get("revenueGrowth")

        return {
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector") or ("Financial Services" if looks_financial else None),
            "industry": info.get("industry"),
            "has_profile": bool(info.get("sector") or info.get("returnOnEquity") is not None),
            "currency": info.get("currency"),
            "financial_currency": info.get("financialCurrency"),
            "roe": info.get("returnOnEquity") if info.get("returnOnEquity") is not None else ratio(ni_last, equity),
            "debt_to_equity": (info["debtToEquity"] / 100) if info.get("debtToEquity") is not None else ratio(debt, equity),
            "gross_margin": info.get("grossMargins") if info.get("grossMargins") is not None else ratio(gross, revenue),
            "net_margin": info.get("profitMargins") if info.get("profitMargins") is not None else ratio(ni_last, revenue),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "eps_ttm": eps_ttm,
            "dividend_yield": info.get("dividendYield"),
            "shares": info.get("sharesOutstanding") or info.get("impliedSharesOutstanding") or shares_bs,
            "book_per_share": ratio(equity, info.get("sharesOutstanding") or shares_bs),
            "fcf_latest": info.get("freeCashflow") or last(cf, "Free Cash Flow"),
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
        fund = {"net_income": [], "fcf": []}

    c = h["Close"]
    price, prev = c.iloc[-1], c.iloc[-2]
    try:
        mkt = market_status(benchmark_for(sym))
    except Exception:
        mkt = None
    meta = h.attrs.get("meta", {})
    fund = {**fund}   # 不改到快取
    fund["name"] = fund.get("name") or meta.get("longName") or meta.get("shortName") or sym
    fund["currency"] = fund.get("currency") or meta.get("currency")
    if fund.get("pe") is None and (fund.get("eps_ttm") or 0) > 0:
        fund["pe"] = price / fund["eps_ttm"]
    if fund.get("pb") is None and (fund.get("book_per_share") or 0) > 0:
        fund["pb"] = price / fund["book_per_share"]
    tech = minervini(h, bench, fund)
    buff = buffett(fund, price)
    return {
        "symbol": sym,
        "name": fund["name"],
        "sector": fund.get("sector"),
        "currency": fund.get("currency"),
        "price": num(price, 2),
        "change": num(price / prev - 1),
        "date": h.index[-1].strftime("%Y-%m-%d"),
        "pe": num(fund.get("pe"), 2),
        "minervini": tech,
        "buffett": buff,
        "market": mkt,
        "advice": buy_advice(tech, buff, mkt),
        "hold": hold_advice(h, tech, buff, mkt),
        "advice_long": buy_advice_long(price, tech, buff, mkt),
        "hold_long": hold_advice_long(h, tech, buff, mkt),
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


# ---------------------------------------------------------------- 大盤方向

def market_status(index_sym):
    """大盤多空：約四分之三的個股跟著大盤走，空頭時不進場。
    多頭：指數在 50 日與 200 日線之上且 200 日線上揚；修正：仍在 200 日線上；空頭：跌破 200 日線。"""
    h = get_history(index_sym)
    c = h["Close"]
    last = c.iloc[-1]
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    m50, m200, m200_1m = ma50.iloc[-1], ma200.iloc[-1], ma200.iloc[-22]
    if last > m200 and last > m50 and m200 > m200_1m:
        status, note = "多頭", "指數在 50 日與 200 日均線之上，適合進場"
    elif last > m200:
        status, note = "修正", "指數跌破 50 日均線或 200 日線走平，進場要更挑剔"
    else:
        status, note = "空頭", "指數跌破 200 日均線，暫停買進"
    return {
        "symbol": index_sym, "name": INDEX_NAMES.get(index_sym, index_sym),
        "status": status, "note": note,
        "price": num(last, 2), "change": num(last / c.iloc[-2] - 1),
        "ma50": num(m50, 2), "ma200": num(m200, 2),
        "date": h.index[-1].strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------- 短線建議
# 趨勢面（Minervini）決定「時機」，基本面（Buffett）決定「值不值得」，兩者合併成單一建議。
# 回測中短線持有中位數約 3–4 週、賺錢的部位約 2 個月。

def buy_advice(t, b, mkt=None):
    """選股：強力買進 / 買進 / 觀察 / 避開。"""
    q = b["quality"] if b["quality"] is not None else 0.5
    good, cheap = q >= 0.7, b["candidate"]
    downtrend = t["stage"].startswith("第四")
    reasons = []

    if t["candidate"]:
        reasons.append("趨勢強且在買點" + ("（帶量突破）" if t["breakout"] else "（接近樞紐點）"))
    elif t["tt_pass"] == 8:
        reasons.append("上升趨勢中，等待突破買點")
    elif downtrend:
        reasons.append("股價處於下跌趨勢")
    else:
        reasons.append(f"趨勢條件 {t['tt_pass']}/8，尚未成形")

    if good and cheap:
        reasons.append("優質企業且價格低於內在價值")
    elif good:
        reasons.append("優質企業，但價格偏高")
    elif q >= 0.5:
        reasons.append("基本面普通")
    else:
        reasons.append("基本面偏弱")

    if t["candidate"] and good:
        action = "強力買進"
    elif (t["candidate"] and q >= 0.5) or (cheap and not downtrend and t["tt_pass"] >= 5):
        action = "買進"
    elif downtrend or (q < 0.5 and t["tt_pass"] < 5):
        action = "避開"
    else:
        action = "觀察"

    status = mkt["status"] if mkt else None
    if status == "空頭" and action in ("強力買進", "買進"):
        action = "觀察"
        reasons.append(f"{mkt['name']}為空頭（跌破 200 日線），暫不進場")
    elif status == "修正" and action == "強力買進":
        action = "買進"
        reasons.append(f"{mkt['name']}修正中，建議降一級")
    return {"action": action, "reasons": reasons}


def hold_advice(h, t, b, mkt=None):
    """持股：賣出 / 減碼 / 續抱 / 加碼（不需要成本或買進日期）。"""
    c, v = h["Close"], h["Volume"]
    price = c.iloc[-1]
    avg_vol50 = v.iloc[-50:].mean()
    vol_ratio = v.iloc[-1] / avg_vol50 if avg_vol50 else 1
    q = b["quality"] if b["quality"] is not None else 0.5
    good = q >= 0.7
    iv, mos = b["intrinsic_value"], b["margin_of_safety"]

    sell, trim, add = [], [], []
    # 趨勢面
    if t["ma200"] and price < t["ma200"]:
        sell.append("跌破 200 日均線，主要趨勢轉弱")
    if t["ma50"] and price < t["ma50"]:
        (sell if vol_ratio >= 1.5 else trim).append(
            f"{'帶量' if vol_ratio >= 1.5 else ''}跌破 50 日均線")
    high20 = c.iloc[-20:].max()
    if price <= high20 * 0.85:
        trim.append(f"從近一個月高點回落 {(1 - price / high20) * 100:.0f}%")
    run15 = price / c.iloc[-16] - 1 if len(c) > 16 else 0
    if run15 >= 0.25:
        trim.append(f"15 日內急漲 {run15 * 100:.0f}%，可能過熱，可分批獲利")
    if t["tt_pass"] == 8 and t["breakout"] and t["in_buy_zone"]:
        add.append(f"帶量突破樞紐點 {t['pivot']:.2f}")
    if (t["tt_pass"] >= 7 and t["ema21"] and abs(price / t["ema21"] - 1) <= 0.02
            and v.iloc[-5:].mean() < avg_vol50 and price > (t["ma50"] or 0)):
        add.append("回測 21 日均線且量縮，趨勢仍在")
    # 基本面
    if q < 0.5:
        sell.append("基本面轉弱，不符合優質企業標準")
    if iv and price > iv * 2:
        trim.append(f"股價超過內在價值 {iv:.2f} 的 2 倍")
    if good and mos is not None and mos >= 0.25:
        add.append(f"優質企業且安全邊際 {mos * 100:.0f}%")

    # 合併：好公司趨勢轉壞先減碼；普通公司趨勢轉壞直接賣
    if sell and (not good or len(sell) >= 2):
        action, reasons = "賣出", sell + trim
    elif sell or trim:
        action, reasons = "減碼", sell + trim
        if good and sell:
            reasons.append("但仍是優質企業，可先減碼觀察")
    elif add and mkt and mkt["status"] == "空頭":
        action, reasons = "續抱", add + [f"但{mkt['name']}為空頭，暫不加碼"]
    elif add:
        action, reasons = "加碼", add
    else:
        action = "續抱"
        reasons = ["趨勢與基本面都沒有轉壞" if good else "趨勢未轉壞，持續觀察"]
    return {"action": action, "reasons": reasons}


# ---------------------------------------------------------------- 長線建議
# 回測顯示長線持有（只在跌破 200 日線時出場）中位數約 6 個月、賺錢的部位約 11 個月，
# 每筆期望值遠高於短線規則。長線以 Buffett 的公司品質為主，趨勢只用 200 日線把關。

def buy_advice_long(price, t, b, mkt=None):
    """長線選股：好公司、價格不離譜、主要趨勢（200 日線）向上，回檔到 50 日線附近是好買點。"""
    q = b["quality"] if b["quality"] is not None else 0.5
    good, cheap = q >= 0.7, b["candidate"]
    iv = b["intrinsic_value"]
    above200 = bool(t["ma200"]) and price > t["ma200"]
    near50 = bool(t["ma50"]) and abs(price / t["ma50"] - 1) <= 0.05
    pricey = bool(iv) and price > iv * 2
    reasons = []

    if q < 0.5:
        action = "避開"
        reasons.append("公司體質不符合長期持有標準")
    elif not above200:
        action = "觀察" if good else "避開"
        reasons.append("股價在 200 日線之下，主要趨勢未轉強" + ("，好公司可等站回再買" if good else ""))
    elif good and cheap:
        action = "強力買進"
        reasons.append("優質企業、價格低於內在價值，且主要趨勢向上")
    elif good and near50 and not pricey:
        action = "買進"
        reasons.append("優質企業回檔到 50 日線附近，長線好買點")
    elif good:
        action = "觀察"
        reasons.append("優質企業，但" + ("股價遠高於內在價值" if pricey else "離 50 日線較遠，可等回檔"))
    else:
        action = "觀察"
        reasons.append("基本面普通，長線吸引力有限")

    if mkt and mkt["status"] == "空頭" and action == "強力買進":
        action = "買進"
        reasons.append(f"{mkt['name']}為空頭，可分批慢慢買")
    return {"action": action, "reasons": reasons}


def hold_advice_long(h, t, b, mkt=None):
    """長線持股：一般回檔都續抱，只有跌破 200 日線、基本面轉弱或嚴重高估才動作。"""
    c, v = h["Close"], h["Volume"]
    price = c.iloc[-1]
    q = b["quality"] if b["quality"] is not None else 0.5
    good = q >= 0.7
    iv, mos = b["intrinsic_value"], b["margin_of_safety"]
    reasons, action = [], "續抱"

    if q < 0.5:
        action = "賣出"
        reasons.append("基本面轉弱，不符合長期持有標準")
    if t["ma200"] and price < t["ma200"]:
        if good and action != "賣出":
            action = "減碼"
            reasons += ["跌破 200 日均線，主要趨勢轉弱", "仍是優質企業，可先減碼，站回 200 日線再補"]
        else:
            action = "賣出"
            reasons.append("跌破 200 日均線，主要趨勢轉弱")
    pricey = bool(iv) and price > iv * 2   # 長線不因貴就賣（Buffett 長抱可口可樂、蘋果），只是不加碼

    if action == "續抱":
        add = []
        if good and mos is not None and mos >= 0.25:
            add.append(f"優質企業且安全邊際 {mos * 100:.0f}%")
        if (good and t["tt_pass"] >= 6 and t["ma50"] and abs(price / t["ma50"] - 1) <= 0.03
                and v.iloc[-5:].mean() < v.iloc[-50:].mean()):
            add.append("回測 50 日線且量縮，主要趨勢仍向上")
        if add and pricey and not (mos is not None and mos >= 0.25):
            reasons = add + ["但股價超過內在價值 2 倍，不宜加碼"]
        elif add and mkt and mkt["status"] == "空頭":
            reasons = add + [f"但{mkt['name']}為空頭，暫不加碼"]
        elif add:
            action, reasons = "加碼", add
        else:
            reasons = ["站穩 200 日線，回檔屬正常波動" + ("，好公司長期持有" if good else "")]
            if pricey:
                reasons.append("估值偏高（超過內在價值 2 倍），續抱但不加碼")
    return {"action": action, "reasons": reasons}


# ---------------------------------------------------------------- AI 族群警示
# 沒有人能預測泡沫何時破，這裡只追蹤「泡沫破裂時通常會先出現」的訊號：
# 費城半導體指數與 AI 龍頭股跌破長期均線、從高點大幅回落。

AI_INDEX = "^SOX"
AI_BASKET = ["NVDA", "AVGO", "AMD", "TSM", "MU", "2330.TW"]
AI_LEVELS = ["正常", "注意", "警戒", "危險"]


def ai_watch():
    h = get_history(AI_INDEX)
    c = h["Close"]
    last = c.iloc[-1]
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    m50, m200, m200_1m = ma50.iloc[-1], ma200.iloc[-1], ma200.iloc[-22]
    drawdown = 1 - last / h["High"].iloc[-252:].max()

    below50 = below200 = 0
    members = []
    for sym in AI_BASKET:
        try:
            s = get_history(sym)["Close"]
        except Exception:
            continue
        p, s50, s200 = s.iloc[-1], s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
        below50 += p < s50
        below200 += p < s200
        members.append({"symbol": sym, "below50": bool(p < s50), "below200": bool(p < s200),
                        "drawdown": num(1 - p / s.iloc[-252:].max())})
    n = max(len(members), 1)

    if last < m200 and m200 < m200_1m and drawdown >= 0.25:
        level, note = "危險", "費半跌破 200 日線且均線下彎、離高點跌超過 25%，過去泡沫破裂時多是這種走勢"
    elif last < m200 or below200 / n >= 0.5:
        level, note = "警戒", "費半或過半 AI 龍頭跌破 200 日線，長期趨勢可能轉弱"
    elif last < m50 or below50 / n >= 0.5:
        level, note = "注意", "費半或過半 AI 龍頭跌破 50 日線，短期轉弱，留意是否續跌"
    else:
        level, note = "正常", "費半與 AI 龍頭股都在均線之上，趨勢仍向上"
    return {
        "name": "AI 族群（費城半導體）", "symbol": AI_INDEX, "level": level, "note": note,
        "price": num(last, 2), "change": num(last / c.iloc[-2] - 1),
        "drawdown": num(drawdown), "ma50": num(m50, 2), "ma200": num(m200, 2),
        "below50": int(below50), "below200": int(below200), "count": len(members), "members": members,
        "date": h.index[-1].strftime("%Y-%m-%d"),
    }
