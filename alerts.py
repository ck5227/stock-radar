"""手機推播：檢查持股與觀察清單，建議改變時透過 ntfy 推播到手機。

由 GitHub Actions 定時執行（.github/workflows/alerts.yml），也可在本機執行：
    NTFY_TOPIC=你的主題 HOLDINGS="2330,NVDA" WATCHLIST="2454,AAPL" python alerts.py

通知時機：
  持股：建議變成「賣出／減碼／加碼」
  觀察清單：建議變成「強力買進／買進」
短線與長線分開追蹤；STYLE=short 或 long 可只收其中一種（預設 both 兩種都收）。
同一檔股票的同一個建議，一天最多通知一次（盤中訊號可能來回變動）。
"""
import datetime as dt
import json
import os
import urllib.request

import analysis

STATE_FILE = "alert_state.json"
HOLD_ALERT = {"賣出": ("rotating_light", 5), "減碼": ("warning", 4), "加碼": ("chart_with_upwards_trend", 4)}
BUY_ALERT = {"強力買進": ("star", 4), "買進": ("moneybag", 3)}
VIEWS = {"short": ("短線", "hold", "advice"), "long": ("長線", "hold_long", "advice_long")}


def symbols(env):
    return [analysis.normalize_symbol(s) for s in os.environ.get(env, "").replace("，", ",").replace(" ", ",").split(",") if s.strip()]


def push(topic, title, message, tag, priority, url):
    body = {"topic": topic, "title": title, "message": message, "tags": [tag], "priority": priority}
    if url:
        body["click"] = url
    req = urllib.request.Request("https://ntfy.sh", json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("未設定 NTFY_TOPIC，略過通知")
        return
    url = os.environ.get("SITE_URL", "")
    today = dt.date.today().isoformat()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    first_run = not state.get("actions")
    actions, notified = state.get("actions", {}), state.get("notified", {})
    alerts, summary = [], []
    style = os.environ.get("STYLE", "both").strip().lower()
    views = [v for v in VIEWS if style in ("both", v)] or list(VIEWS)

    def check(kind, sym, action, reasons, table, label):
        key = f"{kind}:{sym}:{label}"
        changed = actions.get(key) != action
        actions[key] = action
        if action in table and changed and notified.get(key) != f"{action}@{today}":
            notified[key] = f"{action}@{today}"
            alerts.append((sym, label, action, reasons, *table[action]))

    for sym in symbols("HOLDINGS"):
        try:
            a = analysis.analyze(sym)
        except Exception as e:
            print(sym, "失敗：", e)
            continue
        parts = []
        for v in views:
            label, hold_key, _ = VIEWS[v]
            check("H", a["symbol"], a[hold_key]["action"], a[hold_key]["reasons"], HOLD_ALERT, label)
            parts.append(f"{label}{a[hold_key]['action']}")
        summary.append(f"{a['symbol']}（{'、'.join(parts)}）")
    for sym in symbols("WATCHLIST"):
        try:
            a = analysis.analyze(sym)
        except Exception as e:
            print(sym, "失敗：", e)
            continue
        for v in views:
            label, _, buy_key = VIEWS[v]
            check("W", a["symbol"], a[buy_key]["action"], a[buy_key]["reasons"], BUY_ALERT, label)

    if first_run:
        push(topic, "選股雷達通知已啟用", "目前持股：" + ("；".join(summary) or "無") +
             "\n之後建議改變時會通知你。", "white_check_mark", 3, url)
    for sym, label, action, reasons, tag, priority in alerts:
        kind = "持股" if action in HOLD_ALERT else "買點"
        push(topic, f"{sym} {label}建議{action}（{kind}）", "；".join(reasons), tag, priority, url)
        print("已通知", sym, label, action)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"actions": actions, "notified": notified, "updated": dt.datetime.now().isoformat()}, f, ensure_ascii=False)
    print(f"檢查完成：{len(actions)} 項建議，通知 {len(alerts)} 則")


if __name__ == "__main__":
    main()
