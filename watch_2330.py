#!/usr/bin/env python3
"""
2330 (台積電) 買進訊號監控
- 直接呼叫本機 pattern-web 的分析 API
- 綜合評分：型態 + 趨勢 + 量價 + 突破訊號 = 0~100 分
- 達到買進門檻才輸出（避免雜訊）
- 給 cron 用，stdout 結果 → 由 cron delivery 推到 Telegram
"""
import json
import sys
import os
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:5777/api/analyze?symbol=2330&market=tw&range=6mo"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "2330_state.json")
BUY_THRESHOLD = 60  # 60 分以上才喊買


def fetch():
    req = urllib.request.Request(API, headers={"User-Agent": "watch_2330/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def score(data):
    """綜合評分 → (action, score, reasons)
    action: 'BUY' | 'SELL' | 'HOLD'
    """
    a = data.get("analysis", {})
    if a.get("status") != "ok":
        return "HOLD", 0, ["資料不足"]

    candles = data["candles"]
    if len(candles) < 30:
        return "HOLD", 0, ["K 線過少"]

    trend = a["trend"]["direction"]
    vp = a["volume_analysis"]
    patterns = a["patterns"]
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last = closes[-1]

    buy_score = 0
    sell_score = 0
    reasons = []

    # === 趨勢 (±25 分) ===
    if trend == "uptrend":
        buy_score += 25
        reasons.append("📈 趨勢上升（高低點墊高）")
    elif trend == "downtrend":
        sell_score += 25
        reasons.append("📉 趨勢下降（高低點下移）")
    else:
        reasons.append("➡️ 趨勢盤整")

    # === 型態 (±25 分) ===
    for p in patterns:
        if p["type"] == "double_bottom":
            buy_score += 25
            reasons.append(f"🟢 雙底成立（{p['first_bottom']['price']:.0f} / {p['second_bottom']['price']:.0f}）")
        elif p["type"] == "double_top":
            sell_score += 25
            reasons.append(f"🔴 雙頂成立（{p['first_top']['price']:.0f} / {p['second_top']['price']:.0f}）")
        elif p["type"] == "po_di_fan":
            buy_score += 30  # 加重
            reasons.append(f"🚀 底部反轉訊號（反彈 {p['current_rebound_pct']}%）")

    # === 量價結構 (±20 分) ===
    combo = vp["vol_price_combo"]
    vr = vp["vol_ratio"]
    if combo in ("放量上漲", "爆量上漲"):
        buy_score += 20
        reasons.append(f"💪 {combo}（{vr}x 均量）— 多方有撐")
    elif combo in ("放量下跌", "爆量下跌"):
        sell_score += 20
        reasons.append(f"⚠️ {combo}（{vr}x 均量）— 主力可能出貨")
    elif combo == "量縮上漲":
        sell_score += 5
        reasons.append(f"⚠️ {combo}（{vr}x）— 量價背離，動能不足")

    # === 突破前高 (買 15 分) ===
    if len(highs) >= 22:
        prev20_high = max(highs[-22:-1])  # 排除今日
        if last > prev20_high:
            buy_score += 15
            reasons.append(f"🎯 站上 20 日新高 {prev20_high:.0f}")
        prev20_low = min(lows[-22:-1])
        if last < prev20_low:
            sell_score += 15
            reasons.append(f"💥 跌破 20 日新低 {prev20_low:.0f}")

    # === 均線 (買 / 賣 15 分) ===
    if len(closes) >= 20:
        ma20 = sum(closes[-20:]) / 20
        if last > ma20 * 1.01:
            buy_score += 10
            reasons.append(f"✅ 站上 20 日均線 ({ma20:.0f})")
        elif last < ma20 * 0.99:
            sell_score += 10
            reasons.append(f"❌ 跌破 20 日均線 ({ma20:.0f})")
    if len(closes) >= 60:
        ma60 = sum(closes[-60:]) / 60
        if last > ma60:
            buy_score += 5
        else:
            sell_score += 5

    # === 決策 ===
    final = buy_score - sell_score
    if buy_score >= BUY_THRESHOLD and buy_score > sell_score + 15:
        action = "BUY"
    elif sell_score >= BUY_THRESHOLD and sell_score > buy_score + 15:
        action = "SELL"
    else:
        action = "HOLD"

    return action, max(buy_score, sell_score), reasons


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_action": None, "last_alert_ts": 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    force = "--force" in sys.argv
    try:
        data = fetch()
    except Exception as e:
        print(f"❌ 抓取失敗：{e}")
        return 1

    action, sc, reasons = score(data)
    price = data.get("regularMarketPrice")
    prev = data.get("previousClose")
    chg = ""
    if price and prev:
        diff = price - prev
        pct = diff / prev * 100
        sign = "+" if diff >= 0 else ""
        chg = f"{sign}{diff:.2f} ({sign}{pct:.2f}%)"

    state = load_state()
    now = int(time.time())
    # 推播條件：BUY 才主動推；同一個訊號 24h 內不重複；force 跳過抑制
    should_push = False
    if action == "BUY":
        if state.get("last_action") != "BUY" or now - state.get("last_alert_ts", 0) > 86400:
            should_push = True
    if force:
        should_push = True

    if should_push:
        emoji = {"BUY": "🟢 買入訊號", "SELL": "🔴 賣出訊號", "HOLD": "🟡 觀望"}.get(action, "🟡")
        msg = (
            f"⭐️ 2330 台積電 — {emoji}\n"
            f"💰 收盤 {price} TWD {chg}\n"
            f"📊 評分 {sc}/100\n\n"
            f"判斷依據：\n" + "\n".join(f"  {r}" for r in reasons) +
            f"\n\n⚠️ 僅供研究參考，非投資建議"
        )
        print(msg)
        state["last_action"] = action
        state["last_alert_ts"] = now
        save_state(state)
        return 0
    else:
        # 沒到買進門檻 → 靜默退出（cron 不會推播空訊息）
        # 把目前狀態存起來但不輸出
        state["last_check_ts"] = now
        state["last_check_action"] = action
        state["last_check_score"] = sc
        save_state(state)
        # 靜默退出（不輸出任何文字 → cron delivery 不會推送）
        return 0


if __name__ == "__main__":
    sys.exit(main())
