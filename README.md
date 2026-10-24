# 選股雷達：Minervini 動能 × Buffett 價值

自動追蹤台股與美股，依 Mark Minervini（SEPA／趨勢樣板）與 Warren Buffett（價值投資）的選股規則找出候選股，並對持股給出 **賣出／減碼／持有／加碼** 建議。

## 功能

- **選股掃描**：趨勢樣板 8 項條件、相對強度（RS）、VCP 波動收縮、樞紐點突破、EPS／營收成長；ROE、負債比、毛利率、淨利率、自由現金流、DCF 內在價值與安全邊際
- **持股訊號**：停損（可調）、保本停利、跌破 50／200 日均線、高潮噴出、金字塔加碼；基本面轉弱、嚴重高估、安全邊際加碼
- **自動更新**與桌面通知；個股圖表（收盤價與 50／150／200 日均線）
- 台股輸入數字即可（自動判斷上市 `.TW`／上櫃 `.TWO`），美股輸入英文代號

## 安裝與執行

```bash
pip install -r requirements.txt
python app.py
```

開啟 <http://127.0.0.1:5057>。Windows 也可以直接雙擊 `start.bat`。

觀察清單、持股與設定會存在 `data.json`（已列入 `.gitignore`，不會上傳）。

## 檔案

| 檔案 | 說明 |
| --- | --- |
| `analysis.py` | 資料抓取、Minervini／Buffett 評分、持股訊號規則 |
| `app.py` | Flask 伺服器與 API |
| `static/index.html` | 網頁介面 |

## 限制

- 資料來自 Yahoo Finance（yfinance），股價約延遲 15–20 分鐘；財報每 12 小時更新
- RS 為與大盤（加權指數／S&P 500）比較的近似值，非 IBD 官方 RS Rating
- DCF 對成長率假設敏感，僅供參考

> 本工具僅依公開規則自動計算，不構成投資建議。
