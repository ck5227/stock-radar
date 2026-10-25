"""歷史回測：把網站的趨勢買賣規則套用到過去的股價，看實際表現。

只能回測「趨勢面 + 大盤方向」：免費資料只有最近幾年的財報，用今天的財報去判斷
過去的買點會偷看未來，所以 Buffett 基本面不納入回測。

規則（每天收盤後判斷，隔天開盤價成交）：
  進場：趨勢樣板 8/8，且股價在樞紐點（近 4 週高點）之上 5% 以內，且大盤未跌破 200 日線
  出場（三種風格可比較）：
    短線 short：網站持股建議的「減碼／賣出」規則——跌破 50 日線、從近 20 日高點回落 15%、
               15 日內急漲 25%（獲利了結）、跌破 200 日線
    中線 mid  ：帶量（1.5 倍均量）跌破 50 日線，或跌破 200 日線
    長線 long ：只在跌破 200 日線時出場
"""
import math

import pandas as pd
import yfinance as yf

import analysis

MODES = {"short": "短線", "mid": "中線", "long": "長線"}
COST = {"tw": 0.00585, "us": 0.001}   # 來回交易成本：台股手續費 0.1425%×2 + 證交稅 0.3%；美股約 0.1%


def long_history(sym):
    def fetch():
        h = yf.Ticker(sym).history(period="15y", auto_adjust=True)
        if h is None or h.empty or len(h) < 300:
            raise ValueError(f"{sym} 歷史資料不足")
        h = h[["Open", "High", "Low", "Close", "Volume"]].dropna()
        h.index = h.index.tz_localize(None)
        return h
    return analysis._cached(("long", sym), 12 * 3600, fetch)


def _weighted(c):
    r = lambda n: c / c.shift(n) - 1
    return 0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(252)


def signals(h, bench, mode="short"):
    """逐日計算進出場訊號（與 analysis.minervini / hold_advice 相同邏輯的向量化版本）。"""
    c, v = h["Close"], h["Volume"]
    ma50, ma150, ma200 = (c.rolling(n).mean() for n in (50, 150, 200))
    hi52, lo52 = h["High"].rolling(252).max(), h["Low"].rolling(252).min()
    b = bench.reindex(h.index, method="ffill")
    rs = (1 + _weighted(c)) / (1 + _weighted(b))
    tt = ((c > ma150) & (c > ma200)).astype(int) + (ma150 > ma200) + (ma200 > ma200.shift(21)) \
        + ((ma50 > ma150) & (ma50 > ma200)) + (c > ma50) + (c >= lo52 * 1.3) + (c >= hi52 * 0.75) + (rs > 1)
    pivot = h["High"].shift(1).rolling(20).max()
    market_ok = b > b.rolling(200).mean()
    entry = (tt == 8) & (c > pivot) & (c <= pivot * 1.05) & market_ok

    high20 = c.rolling(20).max()
    run15 = c / c.shift(15) - 1
    heavy = v > 1.5 * v.rolling(50).mean()
    rules = {
        "short": ((run15 >= 0.25, "急漲獲利了結"), (c <= high20 * 0.85, "從高點回落 15%"), (c < ma50, "跌破 50 日線")),
        "mid": (((c < ma50) & heavy, "帶量跌破 50 日線"),),
        "long": (),
    }[mode] + ((c < ma200, "跌破 200 日線"),)
    exit_reason = pd.Series("", index=h.index)
    for cond, why in rules:   # 後面的條件優先顯示
        exit_reason = exit_reason.where(~cond.fillna(False), why)
    return entry.fillna(False), exit_reason


def run_symbol(sym, years, mode="short"):
    sym, _ = analysis.resolve(sym)
    h = long_history(sym)
    bench = long_history(analysis.benchmark_for(sym))["Close"]
    entry, exit_reason = signals(h, bench, mode)
    start = h.index[-1] - pd.DateOffset(years=years)
    idx = h.index
    first = idx.searchsorted(start)
    if first < 260:
        first = 260          # 至少留一年暖機，均線與 52 週高低點才算得出來
    cost = COST["tw" if sym.endswith((".TW", ".TWO")) else "us"]

    trades, pos = [], None
    for i in range(first, len(idx) - 1):
        if pos is None and entry.iloc[i]:
            pos = {"i": i + 1, "price": float(h["Open"].iloc[i + 1])}
        elif pos is not None and i >= pos["i"] and exit_reason.iloc[i]:
            px = float(h["Open"].iloc[i + 1])
            trades.append(_trade(idx, pos, i + 1, px, exit_reason.iloc[i], cost))
            pos = None
    if pos is not None:   # 尚未出場：以最後收盤價計算
        trades.append(_trade(idx, pos, len(idx) - 1, float(h["Close"].iloc[-1]), "持有中", cost))

    period = h["Close"].iloc[first:]
    strat = math.prod(1 + t["ret"] for t in trades) - 1
    held_days = sum(t["days"] for t in trades)
    b = bench.reindex(period.index, method="ffill")
    return {
        "symbol": sym,
        "start": period.index[0].strftime("%Y-%m-%d"),
        "end": period.index[-1].strftime("%Y-%m-%d"),
        "trades": trades,
        **stats(trades),
        "strategy_return": analysis.num(strat),
        "buy_hold_return": analysis.num(period.iloc[-1] / period.iloc[0] - 1),
        "index_return": analysis.num(b.iloc[-1] / b.iloc[0] - 1),
        "exposure": analysis.num(held_days / max(len(period), 1)),
    }


def _trade(idx, pos, j, px, why, cost):
    ret = px / pos["price"] * (1 - cost) - 1
    return {
        "entry_date": idx[pos["i"]].strftime("%Y-%m-%d"), "entry": round(pos["price"], 2),
        "exit_date": idx[j].strftime("%Y-%m-%d"), "exit": round(px, 2),
        "ret": round(ret, 4), "days": int(j - pos["i"]), "reason": why,
    }


def stats(trades):
    rets = [t["ret"] for t in trades]
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    avg = lambda xs: sum(xs) / len(xs) if xs else None
    return {
        "n": len(rets),
        "win_rate": analysis.num(len(wins) / len(rets)) if rets else None,
        "avg_win": analysis.num(avg(wins)),
        "avg_loss": analysis.num(avg(losses)),
        "expectancy": analysis.num(avg(rets)),
        "best": analysis.num(max(rets)) if rets else None,
        "worst": analysis.num(min(rets)) if rets else None,
        "profit_factor": analysis.num(sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
    }


def run(symbols, years, pool, mode="short"):
    def one(s):
        try:
            return run_symbol(s, years, mode)
        except Exception as e:
            return {"symbol": s, "error": str(e)}
    results = list(pool.map(one, symbols))
    ok = [r for r in results if "error" not in r]
    all_trades = [t for r in ok for t in r["trades"]]
    avg = lambda k: analysis.num(sum(r[k] for r in ok) / len(ok)) if ok else None
    return {
        "years": years,
        "mode": mode,
        "summary": {
            **stats(all_trades),
            "stocks": len(ok),
            "avg_strategy_return": avg("strategy_return"),
            "avg_buy_hold_return": avg("buy_hold_return"),
            "avg_index_return": avg("index_return"),
            "beat_buy_hold": sum(r["strategy_return"] > r["buy_hold_return"] for r in ok),
            "avg_exposure": avg("exposure"),
        },
        "results": results,
    }
