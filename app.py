"""股市追蹤網站：python app.py 後開啟 http://127.0.0.1:5057

觀察清單與持股存在每位使用者自己的瀏覽器裡，伺服器只負責抓資料與計算，
所以同一個網址可以多人使用、互不影響（例如部署到 Hugging Face Spaces）。
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

import analysis

BASE = os.path.dirname(os.path.abspath(__file__))
LEGACY_FILE = os.path.join(BASE, "data.json")   # 舊版存檔，只用來帶入初始清單
MAX_SYMBOLS = 80
DEFAULTS = {
    "watchlist": [
        "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3711.TW", "2412.TW", "2881.TW",
        "1216.TW", "2912.TW", "3008.TW", "6669.TW",
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "COST", "KO", "AXP", "V", "BRK-B",
    ],
    "holdings": [],
}

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
_pool = ThreadPoolExecutor(max_workers=6)


def safe_analyze(sym):
    try:
        return analysis.analyze(sym)
    except Exception as e:
        return {"symbol": sym, "error": str(e)}


def clean_symbols(items):
    syms = []
    for s in items:
        if isinstance(s, dict):          # 舊版持股格式 {symbol, cost, ...}
            s = s.get("symbol", "")
        s = analysis.normalize_symbol(str(s))
        if s and s not in syms:
            syms.append(s)
    return syms[:MAX_SYMBOLS]


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/defaults")
def defaults():
    data = dict(DEFAULTS)
    if os.path.exists(LEGACY_FILE):
        try:
            with open(LEGACY_FILE, encoding="utf-8") as f:
                old = json.load(f)
            for k in DEFAULTS:
                if isinstance(old.get(k), list):
                    data[k] = clean_symbols(old[k])
        except (OSError, ValueError):
            pass
    return jsonify(data)


@app.post("/api/analyze")
def analyze_many():
    results = list(_pool.map(safe_analyze, clean_symbols((request.json or {}).get("symbols", []))))
    # 清單內的 RS 百分位排名（1~99）
    ok = [r for r in results if "error" not in r]
    ranked = sorted(ok, key=lambda r: r["minervini"]["stock_perf"] or 0)
    for i, r in enumerate(ranked):
        r["minervini"]["rs_rank"] = round(1 + 98 * i / max(len(ranked) - 1, 1))
    return jsonify(results)


@app.get("/api/stock/<sym>")
def stock(sym):
    try:
        return jsonify({**analysis.analyze(sym), "chart": analysis.chart_data(sym)})
    except Exception as e:
        return jsonify({"error": str(e)}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5057))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"   # 雲端部署時對外開放
    print(f"Stock tracker running at http://127.0.0.1:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
