"""股市追蹤網站：python app.py 後開啟 http://127.0.0.1:5057"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

import analysis

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "data.json")
DEFAULT_DATA = {
    "watchlist": [
        "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3711.TW", "2412.TW", "2881.TW",
        "1216.TW", "2912.TW", "3008.TW", "6669.TW",
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "COST", "KO", "AXP", "V", "BRK-B",
    ],
    "holdings": [],
    "settings": {"stop_pct": 0.075, "refresh_min": 5},
}

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
_file_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6)


def load():
    with _file_lock:
        if not os.path.exists(DATA_FILE):
            return json.loads(json.dumps(DEFAULT_DATA))
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    for k, v in DEFAULT_DATA.items():
        data.setdefault(k, v)
    return data


def save(data):
    with _file_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def safe_analyze(sym):
    try:
        return analysis.analyze(sym)
    except Exception as e:
        return {"symbol": sym, "error": str(e)}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/scan")
def scan():
    data = load()
    results = list(_pool.map(safe_analyze, data["watchlist"]))
    # 觀察清單內的 RS 百分位排名（1~99）
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


@app.get("/api/holdings")
def holdings():
    data = load()
    stop_pct = float(data["settings"].get("stop_pct", 0.075))

    def one(pos):
        try:
            return analysis.position_signals(pos, analysis.analyze(pos["symbol"]), stop_pct)
        except Exception as e:
            return {**pos, "error": str(e)}
    return jsonify(list(_pool.map(one, data["holdings"])))


@app.get("/api/data")
def get_data():
    return jsonify(load())


@app.post("/api/watchlist")
def set_watchlist():
    data = load()
    syms = []
    for s in request.json.get("watchlist", []):
        s = analysis.normalize_symbol(s)
        if s and s not in syms:
            syms.append(s)
    data["watchlist"] = syms
    save(data)
    return jsonify(data)


@app.post("/api/holdings")
def set_holdings():
    data = load()
    clean = []
    for p in request.json.get("holdings", []):
        try:
            clean.append({
                "symbol": analysis.normalize_symbol(p["symbol"]),
                "cost": float(p["cost"]),
                "shares": float(p.get("shares") or 0),
                "date": p.get("date") or "",
                "strategy": p.get("strategy") if p.get("strategy") in ("minervini", "buffett") else "minervini",
            })
        except (KeyError, TypeError, ValueError):
            continue
    data["holdings"] = clean
    save(data)
    return jsonify(data)


@app.post("/api/settings")
def set_settings():
    data = load()
    s = request.json or {}
    if "stop_pct" in s:
        data["settings"]["stop_pct"] = min(max(float(s["stop_pct"]), 0.01), 0.5)
    if "refresh_min" in s:
        data["settings"]["refresh_min"] = min(max(int(s["refresh_min"]), 1), 120)
    save(data)
    return jsonify(data)


if __name__ == "__main__":
    print("Stock tracker running at http://127.0.0.1:5057")
    app.run(host="127.0.0.1", port=5057, debug=False, threaded=True)
