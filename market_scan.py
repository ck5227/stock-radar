"""全市場掃描：台股大型股 + 美股 S&P 500，找出趨勢面與基本面都符合的股票。

由 GitHub Actions 每天收盤後執行（.github/workflows/scan.yml），結果存成 JSON，
網站「全市場掃描」分頁讀取顯示。也可在本機執行：python market_scan.py result.json

兩階段，避免對 600 多檔都抓財報：
  1. 批次下載股價，只算 Minervini 趨勢樣板，留下 8/8 的股票
  2. 只對這些股票抓財報，做 Buffett 基本面檢核
分組：
  A 全部符合：趨勢 8/8，基本面檢核全部通過（含價格低於內在價值）
  B 只差估值：趨勢 8/8，公司品質全部通過，但股價高於內在價值
  C 差一項  ：趨勢 8/8，公司品質 ≥ 80%
"""
import csv
import datetime as dt
import io
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

import analysis

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
# 台股：台灣 50、中型 100 的主要成分股與熱門電子股
TW_LISTED = """2330 2317 2454 2308 2382 2881 2882 2891 2303 3711 2412 2886 2884 1216 2885 2892 3231 2357 2345 3034
2379 2327 3008 2880 2890 5880 2887 2883 2301 1301 1303 2002 1326 6669 3037 2395 4938 2207 2912 2603
2609 2615 3661 3443 6415 2376 2377 3017 2324 2356 6505 1101 1102 9910 2105 4904 3045 2408 3653
8046 3533 2368 6239 2383 5871 2801 2834 1590 9904 2059 6446 2474 2353 2360 2449 3702 4958 6176
2404 3044 3036 1476 9921 2633 2618 2610 1402 1504 1513 1519 2201 2313 2367 3006 3023 3189 3406 6271
6409 6531""".split()
TW_OTC = "5274 3529 6488 8299 8069 5347 3105 6274 3324 3264".split()


def universe():
    with urllib.request.urlopen(SP500_CSV, timeout=60) as r:
        us = [row["Symbol"].replace(".", "-") for row in csv.DictReader(io.StringIO(r.read().decode("utf-8")))]
    return [s + ".TW" for s in TW_LISTED] + [s + ".TWO" for s in TW_OTC] + us


def trend_filter(syms):
    data = yf.download(syms + ["^TWII", "^GSPC"], period="2y", group_by="ticker",
                       auto_adjust=True, threads=True, progress=False)
    bench = {k: data[k]["Close"].dropna() for k in ("^TWII", "^GSPC")}
    passed, with_data = [], 0
    for s in syms:
        try:
            h = data[s][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        if len(h) < 260:
            continue
        with_data += 1
        b = bench[analysis.benchmark_for(s)].reindex(h.index, method="ffill")
        if analysis.minervini(h, b, {})["tt_pass"] == 8:
            passed.append(s)
    return passed, with_data


def classify(a):
    b = a["buffett"]
    crit = b["criteria"]
    quality = [c for c in crit[:-1] if c["pass"] is not None]
    if b["max"] >= 4 and b["score"] == b["max"]:
        return "A"
    if len(quality) >= 4 and all(c["pass"] for c in quality):
        return "B"
    if (b["quality"] or 0) >= 0.8:
        return "C"
    return None


def row(a, tier):
    b, m = a["buffett"], a["minervini"]
    return {
        "tier": tier, "symbol": a["symbol"], "name": a["name"], "price": a["price"],
        "change": a["change"], "currency": a["currency"],
        "tt": m["tt_pass"], "rs": m["rs_ratio"], "dist_pivot": m["dist_pivot"],
        "quality": b["quality"], "score": b["score"], "max": b["max"],
        "mos": b["margin_of_safety"], "iv": b["intrinsic_value"], "pe": a["pe"],
        "failed": [c["label"] for c in b["criteria"] if c["pass"] is False],
        "short": a["advice"]["action"], "long": a["advice_long"]["action"],
    }


def main(out):
    t0 = time.time()
    syms = universe()
    tt8, with_data = trend_filter(syms)
    print(f"{with_data} 檔有資料，{len(tt8)} 檔趨勢 8/8（{time.time() - t0:.0f}s）", flush=True)

    def one(s):
        try:
            return analysis.analyze(s)
        except Exception as e:
            print(s, "失敗：", e)
            return None
    with ThreadPoolExecutor(6) as pool:
        results = [a for a in pool.map(one, tt8) if a]

    rows = [row(a, t) for a in results if (t := classify(a))]
    rows.sort(key=lambda r: (r["tier"], -(r["mos"] if r["mos"] is not None else -99), -(r["quality"] or 0)))
    market = {}
    for idx in ("^TWII", "^GSPC"):
        try:
            m = analysis.market_status(idx)
            market[idx] = {"name": m["name"], "status": m["status"]}
        except Exception:
            pass
    try:
        ai = analysis.ai_watch()
        market["ai"] = {"level": ai["level"]}
    except Exception:
        pass
    result = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "universe": len(syms), "with_data": with_data, "tt8": len(tt8),
        "counts": {t: sum(r["tier"] == t for r in rows) for t in "ABC"},
        "market": market, "rows": rows,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"完成：A {result['counts']['A']}、B {result['counts']['B']}、C {result['counts']['C']}（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "scan_result.json")
