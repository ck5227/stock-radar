"""股市追蹤網站：python app.py 後開啟 http://127.0.0.1:5057

觀察清單與持股存在每位使用者自己的瀏覽器裡，伺服器只負責抓資料與計算，
所以同一個網址可以多人使用、互不影響（例如部署到 Hugging Face Spaces）。
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import analysis
import backtest

BASE = os.path.dirname(os.path.abspath(__file__))
LEGACY_FILE = os.path.join(BASE, "data.json")   # 舊版存檔，只用來帶入初始清單
MAX_SYMBOLS = 80
# 全市場掃描結果：GitHub Actions 每天產生，存在 scan-data 分支；本機可用 python market_scan.py 產生 scan_result.json
SCAN_URL = os.environ.get("SCAN_URL", "https://raw.githubusercontent.com/ck5227/stock-radar/scan-data/result.json")
LOCAL_SCAN = os.path.join(BASE, "scan_result.json")
_scan_cache = {"t": 0, "data": None}
DEFAULTS = {
    "watchlist": [
        "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3711.TW", "2412.TW", "2881.TW",
        "1216.TW", "2912.TW", "3008.TW", "6669.TW",
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "COST", "KO", "AXP", "V", "BRK-B",
    ],
    "holdings": [],
}

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
# 雲端主機在 https 代理後面，讓 request.url_root 顯示正確的 https 網址（robots.txt、sitemap 用）
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
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


@app.get("/robots.txt")
def robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {request.url_root}sitemap.xml\n"
    return app.response_class(body, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{request.url_root}</loc><changefreq>daily</changefreq></url></urlset>\n")
    return app.response_class(body, mimetype="application/xml")


@app.get("/api/market-scan")
def market_scan():
    # 優先用線上每日結果（快取 10 分鐘）；抓不到時才用本機 scan_result.json
    if _scan_cache["data"] is None or time.time() - _scan_cache["t"] > 600:
        try:
            with urllib.request.urlopen(SCAN_URL, timeout=20) as r:
                _scan_cache.update(t=time.time(), data=json.loads(r.read().decode("utf-8")))
        except Exception as e:
            if _scan_cache["data"] is None:
                if os.path.exists(LOCAL_SCAN):
                    with open(LOCAL_SCAN, encoding="utf-8") as f:
                        return jsonify(json.load(f))
                return jsonify({"error": f"還沒有掃描結果：{e}"}), 503
    return jsonify(_scan_cache["data"])


@app.get("/api/version")
def version():
    # Render 會提供部署的 git commit，方便確認雲端跑的是哪一版
    return jsonify({"commit": os.environ.get("RENDER_GIT_COMMIT", "local")[:7]})


@app.get("/api/market")
def market():
    out = []
    for idx in ("^TWII", "^GSPC"):
        try:
            out.append(analysis.market_status(idx))
        except Exception as e:
            out.append({"symbol": idx, "name": analysis.INDEX_NAMES[idx], "error": str(e)})
    try:
        ai = analysis.ai_watch()
    except Exception as e:
        ai = {"name": "AI 族群", "error": str(e)}
    return jsonify({"indexes": out, "ai": ai})


@app.post("/api/backtest")
def run_backtest():
    body = request.json or {}
    years = min(max(int(body.get("years", 5)), 1), 12)
    mode = body.get("mode") if body.get("mode") in backtest.MODES else "short"
    return jsonify(backtest.run(clean_symbols(body.get("symbols", [])), years, _pool, mode))


@app.post("/api/rolling")
def rolling():
    return jsonify(backtest.rolling_hold(clean_symbols((request.json or {}).get("symbols", [])), 5, _pool))


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
