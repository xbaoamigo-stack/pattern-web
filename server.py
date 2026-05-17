#!/usr/bin/env python3
"""
型態學波段分析網站 - 後端 API
- 提供台股 / 美股 K 線數據
- 不需要任何 pip 套件，純 stdlib
"""
import json
import re
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
PORT = int(os.environ.get("PORT", 5777))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# 記憶體 + 磁碟 cache，避免 Yahoo 限流 (大約 5-10 req/min)
_cache = {}
_CACHE_TTL = 300  # 5 分鐘
_CACHE_DIR = os.path.join(ROOT, "data", "cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_path(symbol, range_, interval):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{symbol}_{range_}_{interval}")
    return os.path.join(_CACHE_DIR, safe + ".json")


def _load_disk(symbol, range_, interval, max_age=_CACHE_TTL):
    p = _cache_path(symbol, range_, interval)
    if not os.path.exists(p):
        return None
    age = time.time() - os.path.getmtime(p)
    if age > max_age:
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _save_disk(symbol, range_, interval, data):
    try:
        with open(_cache_path(symbol, range_, interval), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _curl_json(url, headers=None, timeout=15):
    args = ["curl", "-s", "-A", UA, "--max-time", str(timeout)]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    args.append(url)
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode}: {r.stderr[:120]}")
    if not r.stdout.lstrip().startswith("{"):
        raise RuntimeError(f"non-json response: {r.stdout[:120]}")
    return json.loads(r.stdout)


def _range_to_months(range_):
    return {"3mo": 3, "6mo": 6, "1y": 12, "2y": 24, "5y": 60}.get(range_, 6)


def fetch_tw_chart(symbol: str, range_: str):
    """台股：優先用 Yahoo Finance，備用 TWSE"""
    s = symbol.replace(".TW", "").replace(".TWO", "")
    if not s.isdigit():
        raise RuntimeError(f"invalid TW symbol: {symbol}")
    
    # 優先嘗試 Yahoo Finance（Render 環境更穩定）
    try:
        symbol_yahoo = s + ".TW"
        range_map = {"1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "1y": "1y", "2y": "2y", "5y": "5y"}
        range_param = range_map.get(range_, "6mo")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_yahoo}?interval=1d&range={range_param}"
        data = _curl_json(url, timeout=15)
        
        if data.get("chart", {}).get("result"):
            result = data["chart"]["result"][0]
            timestamp = result.get("timestamp", [])
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            candles = []
            for i, ts in enumerate(timestamp):
                c = quote.get("close", [])[i] if i < len(quote.get("close", [])) else None
                o = quote.get("open", [])[i] if i < len(quote.get("open", [])) else None
                h = quote.get("high", [])[i] if i < len(quote.get("high", [])) else None
                l = quote.get("low", [])[i] if i < len(quote.get("low", [])) else None
                v = quote.get("volume", [])[i] if i < len(quote.get("volume", [])) else None
                if c is not None:
                    candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": int(v) if v else 0})
            if candles:
                return {
                    "symbol": symbol,
                    "currency": "TWD",
                    "exchangeName": "TWSE",
                    "longName": s,
                    "regularMarketPrice": candles[-1]["close"],
                    "previousClose": candles[-2]["close"] if len(candles) > 1 else candles[-1]["close"],
                    "candles": candles,
                }
    except Exception:
        pass
    
    # 備用：TWSE 官方 API
    months = _range_to_months(range_)
    today = time.localtime()
    candles = []
    name = ""
    # 抓近 N 個月份
    y, m = today.tm_year, today.tm_mon
    target_months = []
    for _ in range(months + 1):
        target_months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    target_months.reverse()
    seen_dates = set()
    for y, m in target_months:
        date_str = f"{y:04d}{m:02d}01"
        url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={s}"
        try:
            data = _curl_json(url, timeout=8)
        except Exception:
            continue
        if data.get("stat") != "OK":
            continue
        if not name:
            # title: "115年05月 2330 台積電           各日成交資訊"
            t = data.get("title", "")
            parts = t.split()
            if len(parts) >= 3:
                name = parts[2].strip()
        for row in data.get("data", []):
            # [日期, 成交股數, 成交金額, 開, 高, 低, 收, 漲跌, 成交筆數, 註記]
            try:
                roc_date = row[0]  # "115/05/04"
                yp, mp, dp = roc_date.split("/")
                ad_y = int(yp) + 1911
                ts = int(time.mktime(time.strptime(f"{ad_y}-{mp}-{dp}", "%Y-%m-%d")))
                if ts in seen_dates:
                    continue
                seen_dates.add(ts)
                def _f(x):
                    return float(x.replace(",", "")) if x and x != "--" else None
                def _i(x):
                    return int(x.replace(",", "")) if x and x != "--" else 0
                candles.append({
                    "time": ts,
                    "open": _f(row[3]),
                    "high": _f(row[4]),
                    "low": _f(row[5]),
                    "close": _f(row[6]),
                    "volume": _i(row[1]),
                })
            except Exception:
                continue
    candles.sort(key=lambda c: c["time"])
    if not candles:
        raise RuntimeError("twse: no data")
    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else last
    return {
        "symbol": symbol,
        "currency": "TWD",
        "exchangeName": "TWSE",
        "longName": name or s,
        "regularMarketPrice": last["close"],
        "previousClose": prev["close"],
        "candles": candles,
    }


def fetch_us_chart(symbol: str, range_: str):
    """美股：使用 Nasdaq 公開 API"""
    months = _range_to_months(range_)
    today = time.gmtime()
    fromy = today.tm_year
    fromm = today.tm_mon - months
    while fromm <= 0:
        fromm += 12
        fromy -= 1
    from_date = f"{fromy:04d}-{fromm:02d}-{today.tm_mday:02d}"
    to_date = f"{today.tm_year:04d}-{today.tm_mon:02d}-{today.tm_mday:02d}"
    # ^GSPC / ^IXIC / ^DJI 要從其他來源拼
    if symbol.startswith("^"):
        return fetch_index_chart(symbol, range_)
    limit = max(months * 22, 60)
    headers = {
        "Accept": "application/json",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    rows = []
    data = None
    last_err = None
    for asset in ("stocks", "etf", "index"):
        url = (
            f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol)}/historical"
            f"?assetclass={asset}&fromdate={from_date}&todate={to_date}&limit={limit}"
        )
        try:
            data = _curl_json(url, headers=headers, timeout=15)
        except Exception as e:
            last_err = str(e)
            continue
        rows = (((data.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
        if rows:
            break
    if not rows:
        raise RuntimeError(f"nasdaq: no rows for {symbol} ({last_err})")
    candles = []
    for row in rows:
        try:
            # date "05/15/2026"
            mm, dd, yy = row["date"].split("/")
            ts = int(time.mktime(time.strptime(f"{yy}-{mm}-{dd}", "%Y-%m-%d")))
            def _f(x):
                if x is None: return None
                return float(str(x).replace("$", "").replace(",", "")) if x else None
            def _i(x):
                if x is None: return 0
                return int(str(x).replace(",", "")) if x else 0
            c = {
                "time": ts,
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _i(row.get("volume")),
            }
            if c["close"] is not None:
                candles.append(c)
        except Exception:
            continue
    candles.sort(key=lambda c: c["time"])
    if not candles:
        raise RuntimeError("nasdaq: parsed 0 rows")
    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else last
    company_name = ((data.get("data") or {}).get("symbol")) or symbol
    return {
        "symbol": symbol,
        "currency": "USD",
        "exchangeName": "NASDAQ/NYSE",
        "longName": company_name,
        "regularMarketPrice": last["close"],
        "previousClose": prev["close"],
        "candles": candles,
    }


def fetch_index_chart(symbol: str, range_: str):
    """指數：^TWII / ^GSPC / ^IXIC — 用 ETF 代理拼 Nasdaq。
    Stooq 現在都要 apikey，所以指數以 ETF 近似。"""
    # 複製指數的 ETF（走 Nasdaq API）
    etf_proxy = {
        "^GSPC": ("SPY", "S&P 500 (代理 SPY)"),
        "^IXIC": ("QQQ", "NASDAQ 100 (代理 QQQ)"),
        "^DJI": ("DIA", "道璚斯 (代理 DIA)"),
        "^TWII": (None, None),  # 由 TWSE 路徑處理 → 下面偶例外處理
    }
    if symbol == "^TWII":
        # 用 0050.TW 代理台股加權指數 (趴勢)
        data = fetch_tw_chart("0050.TW", range_)
        data["symbol"] = "^TWII"
        data["longName"] = "台股加權指數 (代理 0050)"
        return data
    proxy = etf_proxy.get(symbol)
    if proxy and proxy[0]:
        data = fetch_us_chart(proxy[0], range_)
        data["symbol"] = symbol
        data["longName"] = proxy[1]
        return data
    # 原本 Stooq 路徑 (作為 fallback)
    mapping = {
        "^GSPC": "^spx",
        "^IXIC": "^ndq",
        "^DJI": "^dji",
        "^TWII": "^twse",
    }
    stq = mapping.get(symbol)
    if not stq:
        raise RuntimeError(f"unsupported index: {symbol}")
    months = _range_to_months(range_)
    # 計算起始日
    today = time.localtime()
    fromy, fromm = today.tm_year, today.tm_mon - months
    while fromm <= 0:
        fromm += 12
        fromy -= 1
    d1 = f"{fromy:04d}{fromm:02d}{today.tm_mday:02d}"
    d2 = f"{today.tm_year:04d}{today.tm_mon:02d}{today.tm_mday:02d}"
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(stq)}&d1={d1}&d2={d2}&i=d"
    r = subprocess.run(["curl", "-s", "-A", UA, "--max-time", "15", url], capture_output=True, text=True, timeout=20)
    txt = r.stdout.strip()
    if not txt or txt.startswith("Get your apikey"):
        raise RuntimeError("stooq: apikey required")
    lines = txt.splitlines()
    if len(lines) < 2:
        raise RuntimeError("stooq: empty")
    # CSV header: Date,Open,High,Low,Close,Volume
    candles = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 5:
            continue
        try:
            ts = int(time.mktime(time.strptime(parts[0], "%Y-%m-%d")))
            candles.append({
                "time": ts,
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": int(parts[5]) if len(parts) > 5 and parts[5] else 0,
            })
        except Exception:
            continue
    candles.sort(key=lambda c: c["time"])
    if not candles:
        raise RuntimeError("stooq: 0 rows")
    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else last
    return {
        "symbol": symbol,
        "currency": "USD" if symbol != "^TWII" else "TWD",
        "exchangeName": "INDEX",
        "longName": symbol,
        "regularMarketPrice": last["close"],
        "previousClose": prev["close"],
        "candles": candles,
    }


def fetch_chart(symbol: str, market: str, range_: str = "6mo"):
    """統一抓入口：依市場路由到不同來源。帶多層 cache + 舊 cache fallback"""
    key = (symbol, market, range_)
    now = time.time()
    if key in _cache and now - _cache[key][0] < _CACHE_TTL:
        return _cache[key][1]
    disk = _load_disk(symbol, range_, "1d")
    if disk is not None:
        _cache[key] = (now, disk)
        return disk
    try:
        if market == "tw":
            data = fetch_tw_chart(symbol, range_)
        elif symbol.startswith("^"):
            data = fetch_index_chart(symbol, range_)
        else:
            data = fetch_us_chart(symbol, range_)
        _cache[key] = (now, data)
        _save_disk(symbol, range_, "1d", data)
        return data
    except Exception as e:
        # 試舊 cache
        stale = _load_disk(symbol, range_, "1d", max_age=86400 * 14)
        if stale is not None:
            return stale
        raise


def normalize_symbol(symbol: str, market: str = "auto") -> str:
    """規範化代號：台股自動加 .TW，美股保持原樣"""
    s = symbol.strip().upper()
    if market == "tw" and not s.endswith(".TW") and not s.endswith(".TWO"):
        # 純數字加 .TW
        if s.isdigit():
            return s + ".TW"
    if market == "us":
        # 移除可能的台股後綴
        return s.replace(".TW", "").replace(".TWO", "")
    # auto: 純數字判定為台股
    if s.isdigit():
        return s + ".TW"
    return s





# ----- 經典波段型態學 + 量價背離核心算法 -----

def detect_neckline(candles, lookback=60):
    """
    計算頸線位置（底部/頭部的關鍵支撐/壓力位）
    - 低點組：找最近 N 根 K 線中的兩個相近低點
    - 高點組：找最近 N 根 K 線中的兩個相近高點
    """
    if len(candles) < lookback:
        return None
    
    recent = candles[-lookback:]
    lows = [(i, c["low"]) for i, c in enumerate(recent)]
    highs = [(i, c["high"]) for i, c in enumerate(recent)]
    
    # 找最低的 2 個低點
    sorted_lows = sorted(lows, key=lambda x: x[1])[:2]
    neckline_low = sum(x[1] for x in sorted_lows) / 2 if sorted_lows else None
    
    # 找最高的 2 個高點
    sorted_highs = sorted(highs, key=lambda x: -x[1])[:2]
    neckline_high = sum(x[1] for x in sorted_highs) / 2 if sorted_highs else None
    
    return {"neckline_low": neckline_low, "neckline_high": neckline_high}

def detect_break_then_recovery(candles, lookback=30):
    """
    破底翻偵測：
    1. 跌破頸線
    2. 隨後 1-5 根 K 線內拉回上去
    -> 買進訊號
    """
    if len(candles) < lookback:
        return None
    
    recent = candles[-lookback:]
    nl_data = detect_neckline(recent)
    if not nl_data or not nl_data["neckline_low"]:
        return None
    
    neckline = nl_data["neckline_low"]
    
    # 找最近一次跌破頸線的點
    break_idx = None
    for i in range(len(recent) - 1, -1, -1):
        if recent[i]["low"] < neckline:
            break_idx = i
            break
    
    if break_idx is None or break_idx >= len(recent) - 1:
        return None
    
    # 檢查後續 5 根內有沒有拉回上頸線
    for i in range(break_idx + 1, min(break_idx + 6, len(recent))):
        if recent[i]["close"] > neckline:
            # 確認破底翻！
            return {
                "type": "break_then_recovery",
                "break_price": recent[break_idx]["low"],
                "recovery_price": recent[i]["close"],
                "neckline": neckline,
                "days_to_recovery": i - break_idx,
                "confidence": 0.8 if i - break_idx <= 3 else 0.6,
            }
    
    return None

def detect_volume_price_divergence(candles, lookback=30):
    """
    量價背離偵測：
    - 高檔「價漲量縮」= 出貨（空頭）
    - 低檔「價跌量增」= 吸籌（多頭）
    """
    if len(candles) < lookback:
        return None
    
    recent = candles[-lookback:]
    
    # 最近 5 天 vs 前 20 天的量 + 價
    recent_5 = recent[-5:]
    prev_20 = recent[-25:-5] if len(recent) >= 25 else recent[:-5]
    
    recent_vol = sum(c.get("volume", 0) or 0 for c in recent_5) / 5
    prev_vol = sum(c.get("volume", 0) or 0 for c in prev_20) / len(prev_20) if prev_20 else recent_vol
    vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1
    
    recent_high = max(c["high"] for c in recent_5)
    prev_high = max(c["high"] for c in prev_20) if prev_20 else recent_high
    
    recent_low = min(c["low"] for c in recent_5)
    prev_low = min(c["low"] for c in prev_20) if prev_20 else recent_low
    
    # 判斷高檔或低檔
    price_level = "high" if recent_high > prev_high * 1.05 else ("low" if recent_low < prev_low * 0.95 else "middle")
    
    divergence = None
    if price_level == "high" and vol_ratio < 0.9:
        # 高檔價漲量縮 = 出貨
        divergence = {"type": "price_up_volume_down", "signal": "sell", "confidence": 0.7}
    elif price_level == "low" and vol_ratio > 1.3:
        # 低檔價跌量增 = 吸籌
        divergence = {"type": "price_down_volume_up", "signal": "buy", "confidence": 0.75}
    
    return divergence

def analyze_patterns(candles):
    """
    K 線型態學的核心分析：
    - 趨勢判斷（高低點結構）
    - 型態識別（雙底、雙頂、底部反轉等）
    - 量價配合
    """
    if len(candles) < 30:
        return {"status": "insufficient_data", "message": "資料不足，需至少 30 根 K 線"}

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] or 0 for c in candles]

    # 1. 找尋擺動高低點（pivot points）
    pivots = find_pivots(candles, left=3, right=3)
    pivot_highs = [p for p in pivots if p["kind"] == "high"]
    pivot_lows = [p for p in pivots if p["kind"] == "low"]

    # 2. 趨勢判斷（最近 5 個 pivot 的結構）
    trend = detect_trend(pivot_highs, pivot_lows)

    # 3. 型態識別
    patterns = []
    db = detect_double_bottom(pivot_lows, closes, volumes)
    if db:
        patterns.append(db)
    dt = detect_double_top(pivot_highs, closes, volumes)
    if dt:
        patterns.append(dt)
    pbf = detect_pdf_break(candles, pivot_lows, pivot_highs)
    if pbf:
        patterns.append(pbf)

    # 4. 量價分析（最近 5 天 vs 前 20 天平均）
    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    base_vol = sum(volumes[-25:-5]) / 20 if len(volumes) >= 25 else recent_vol
    vol_ratio = recent_vol / base_vol if base_vol > 0 else 1.0

    recent_change = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

    vol_signal = "量縮"
    if vol_ratio > 1.5:
        vol_signal = "爆量"
    elif vol_ratio > 1.2:
        vol_signal = "放量"
    elif vol_ratio < 0.7:
        vol_signal = "極縮量"

    price_action = "盤整"
    if recent_change > 3:
        price_action = "上漲"
    elif recent_change < -3:
        price_action = "下跌"

    vol_price = f"{vol_signal}{price_action}"

    # 5. 量價綜合判斷
    advice = []
    if vol_price == "放量上漲" or vol_price == "爆量上漲":
        advice.append("✅ 量價配合佳，多方有撐")
    elif vol_price == "放量下跌" or vol_price == "爆量下跌":
        advice.append("⚠️ 主力可能出貨，注意風險")
    elif vol_price == "極縮量盤整":
        advice.append("⏳ 量縮整理中，等待方向選擇")
    elif vol_price == "量縮上漲":
        advice.append("⚠️ 量價背離，上漲動能不足")

    if trend["direction"] == "uptrend":
        advice.append("📈 高低點墊高，趨勢偏多")
    elif trend["direction"] == "downtrend":
        advice.append("📉 高低點下移，趨勢偏空")
    else:
        advice.append("➡️ 趨勢不明，盤整為主")

    # 6a. 經典波段核心訊號：破底翻、量價背離、頸線、進場點
    pattern_signals = {}
    
    # 破底翻偵測
    break_recovery = detect_break_then_recovery(candles)
    if break_recovery:
        pattern_signals["break_recovery"] = {
            "type": break_recovery["type"],
            "break_price": round(break_recovery["break_price"], 2),
            "recovery_price": round(break_recovery["recovery_price"], 2),
            "neckline": round(break_recovery["neckline"], 2),
            "days_to_recovery": break_recovery["days_to_recovery"],
            "confidence": round(break_recovery["confidence"], 2),
        }
    
    # 量價背離偵測
    vol_div = detect_volume_price_divergence(candles)
    if vol_div:
        pattern_signals["volume_divergence"] = vol_div
    
    # 頸線情報
    neckline_data = detect_neckline(candles)
    if neckline_data:
        if neckline_data["neckline_low"]:
            pattern_signals["neckline_low"] = round(neckline_data["neckline_low"], 2)
        if neckline_data["neckline_high"]:
            pattern_signals["neckline_high"] = round(neckline_data["neckline_high"], 2)
    
    # 計算進場、停損、目標價
    current = closes[-1]
    entry_price = None
    stop_loss = None
    target_price = None
    
    if break_recovery:
        entry_price = break_recovery["neckline"] * 1.01
        stop_loss = break_recovery["break_price"] * 0.99
        if neckline_data and neckline_data["neckline_high"]:
            target_price = neckline_data["neckline_high"]
    elif neckline_data and neckline_data["neckline_low"]:
        entry_price = neckline_data["neckline_low"] * 1.01
        stop_loss = neckline_data["neckline_low"] * 0.98
        if neckline_data["neckline_high"]:
            target_price = neckline_data["neckline_high"]

    # 6b. 綜合計分 → 明確訊號
    # 6. 綜合計分 → 明確訊號
    signal = compute_signal(trend, patterns, vol_signal, price_action, vol_price)

    return {
        "status": "ok",
        "trend": trend,
        "patterns": patterns,
        "volume_analysis": {
            "vol_ratio": round(vol_ratio, 2),
            "vol_signal": vol_signal,
            "price_action": price_action,
            "vol_price_combo": vol_price,
        },
        "pattern_signals": pattern_signals,
        "trading_plan": {
            "current_price": round(current, 2),
            "entry_price": round(entry_price, 2) if entry_price else None,
            "stop_loss": round(stop_loss, 2) if stop_loss else None,
            "target_price": round(target_price, 2) if target_price else None,
            "risk_reward_ratio": round((target_price - entry_price) / (entry_price - stop_loss), 2) if (entry_price and stop_loss and target_price and entry_price != stop_loss) else None,
        },
        "advice": advice,
        "signal": signal,
        "pivots": pivots[-10:],  # 最近 10 個轉折點
        "current_price": closes[-1],
        "recent_change_pct": round(recent_change, 2),
    }



def compute_signal(trend, patterns, vol_signal, price_action, vol_price):
    """綜合計分 → 給明確的訊號
    
    回傳 dict: {action, score, level, color, reasons}
    
    action: BUY / SELL / WATCH / WAIT
    score: -100 ~ +100
    level: 強烈買進 / 買進 / 偏多觀望 / 中性 / 偏空觀望 / 賣出 / 強烈賣出
    """
    score = 0
    reasons = []

    # ── 趨勢分數 (±30) ──
    if trend["direction"] == "uptrend":
        score += 25
        reasons.append("+25 高低點墊高")
    elif trend["direction"] == "downtrend":
        score -= 25
        reasons.append("-25 高低點下移")

    # ── 型態訊號 (±40) ──
    for p in patterns:
        ptype = p.get("type", "")
        if ptype == "double_bottom":
            score += 35
            reasons.append(f"+35 雙底（{p.get('signal','')}\u3009")
        elif ptype == "double_top":
            score -= 35
            reasons.append(f"-35 雙頂（{p.get('signal','')}）")
        elif ptype == "pdf_break":
            # 破底翻
            score += 30
            reasons.append("+30 破底翻訊號（古典：抄底結構成立）")

    # ── 量價配合 (±20) ──
    if vol_price in ("放量上漲", "爆量上漲"):
        score += 15
        reasons.append(f"+15 {vol_price}（量價配合）")
    elif vol_price in ("放量下跌", "爆量下跌"):
        score -= 15
        reasons.append(f"-15 {vol_price}（主力可能出貨）")
    elif vol_price == "量縮上漲":
        score -= 8
        reasons.append("-8 量縮上漲（動能不足）")
    elif vol_price == "極縮量盤整":
        reasons.append("±0 極縮量盤整（等待方向）")

    # ── 假突破檢測（爆量但價格沒推上去）──
    if vol_signal == "爆量" and price_action == "盤整":
        score -= 10
        reasons.append("-10 爆量未過頂（疑似假突破）")

    # 結算
    if score >= 50:
        action, level, color = "BUY", "強烈買進", "#22c55e"
    elif score >= 25:
        action, level, color = "BUY", "買進", "#84cc16"
    elif score >= 10:
        action, level, color = "WATCH", "偏多觀望", "#a3e635"
    elif score >= -10:
        action, level, color = "WAIT", "中性等待", "#94a3b8"
    elif score >= -25:
        action, level, color = "WATCH", "偏空觀望", "#fb923c"
    elif score >= -50:
        action, level, color = "SELL", "賣出", "#ef4444"
    else:
        action, level, color = "SELL", "強烈賣出", "#dc2626"

    return {
        "action": action,
        "level": level,
        "score": score,
        "color": color,
        "reasons": reasons,
    }


def find_pivots(candles, left=3, right=3):
    """找擺動高低點：左右各 N 根都比它低（高）就是擺動高（低）"""
    pivots = []
    for i in range(left, len(candles) - right):
        h = candles[i]["high"]
        l = candles[i]["low"]
        is_high = all(candles[i - k]["high"] <= h for k in range(1, left + 1)) and \
                  all(candles[i + k]["high"] <= h for k in range(1, right + 1))
        is_low = all(candles[i - k]["low"] >= l for k in range(1, left + 1)) and \
                 all(candles[i + k]["low"] >= l for k in range(1, right + 1))
        if is_high:
            pivots.append({"index": i, "time": candles[i]["time"], "price": h, "kind": "high"})
        if is_low:
            pivots.append({"index": i, "time": candles[i]["time"], "price": l, "kind": "low"})
    return pivots


def detect_trend(pivot_highs, pivot_lows):
    """根據最近的高低點結構判斷趨勢"""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return {"direction": "unknown", "label": "資料不足"}
    last_highs = pivot_highs[-2:]
    last_lows = pivot_lows[-2:]
    higher_high = last_highs[-1]["price"] > last_highs[0]["price"]
    higher_low = last_lows[-1]["price"] > last_lows[0]["price"]
    lower_high = last_highs[-1]["price"] < last_highs[0]["price"]
    lower_low = last_lows[-1]["price"] < last_lows[0]["price"]
    if higher_high and higher_low:
        return {"direction": "uptrend", "label": "上升趨勢（高低點墊高）"}
    if lower_high and lower_low:
        return {"direction": "downtrend", "label": "下降趨勢（高低點下移）"}
    return {"direction": "sideways", "label": "盤整或趨勢未明"}


def detect_double_bottom(pivot_lows, closes, volumes):
    """雙底偵測：最近兩個低點高度相近，且第二個略高"""
    if len(pivot_lows) < 2:
        return None
    p1, p2 = pivot_lows[-2], pivot_lows[-1]
    diff_pct = abs(p2["price"] - p1["price"]) / p1["price"] * 100
    if diff_pct > 5:
        return None  # 兩底差距過大
    if p2["price"] < p1["price"]:
        return None  # 第二底反而更低，不是標準雙底
    return {
        "type": "double_bottom",
        "label": "雙底型態形成中",
        "confidence": "medium" if diff_pct < 2 else "low",
        "first_bottom": p1,
        "second_bottom": p2,
        "neckline_hint": "中間高點為頸線，突破後確認",
    }


def detect_double_top(pivot_highs, closes, volumes):
    """雙頂偵測"""
    if len(pivot_highs) < 2:
        return None
    p1, p2 = pivot_highs[-2], pivot_highs[-1]
    diff_pct = abs(p2["price"] - p1["price"]) / p1["price"] * 100
    if diff_pct > 5:
        return None
    if p2["price"] > p1["price"]:
        return None
    return {
        "type": "double_top",
        "label": "雙頂型態形成中",
        "confidence": "medium" if diff_pct < 2 else "low",
        "first_top": p1,
        "second_top": p2,
        "neckline_hint": "中間低點為頸線，跌破後確認",
    }


def detect_pdf_break(candles, pivot_lows, pivot_highs):
    """底部反轉：跌破前低後快速翻身向上"""
    if len(pivot_lows) < 1 or len(candles) < 10:
        return None
    last_low = pivot_lows[-1]
    closes = [c["close"] for c in candles]
    last_price = closes[-1]
    # 最近收盤是否高過前低 + 至少 3% 反彈
    if last_low["index"] >= len(candles) - 3:
        return None  # 太近，沒有反彈
    rebound = (last_price - last_low["price"]) / last_low["price"] * 100
    if rebound < 3:
        return None
    # 檢查曾經有跌破再翻身
    after_low = candles[last_low["index"]:]
    min_after = min(c["low"] for c in after_low)
    if min_after >= last_low["price"]:
        return None  # 沒有破底
    return {
        "type": "po_di_fan",
        "label": "底部反轉訊號（破前低後翻揚）",
        "confidence": "medium",
        "breakdown_low": min_after,
        "current_rebound_pct": round(rebound, 2),
        "note": "需確認量能配合，且站穩中間高點才算成立",
    }


# ----- HTTP Server -----

class PatternAnalysisHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        
        # 行事曆 API - POST
        if path == "/api/events":
            try:
                from calendar_api import add_event
                content_len = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_len).decode('utf-8')
                data = json.loads(body)
                event_id = add_event(data)
                return self._send(201, {"id": event_id, "status": "created"})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        
        return self._send(405, {"error": "method not allowed"})

    def do_DELETE(self):
        u = urlparse(self.path)
        path = u.path
        
        # 行事曆 API - DELETE
        if path.startswith("/api/events/"):
            try:
                from calendar_api import delete_event
                event_id = int(path.split('/')[-1])
                delete_event(event_id)
                return self._send(200, {"status": "deleted"})
            except Exception as e:
                return self._send(400, {"error": str(e)})
        
        return self._send(405, {"error": "method not allowed"})

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        qs = parse_qs(u.query)

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/calendar" or path == "/calendar.html":
            return self._serve_static("calendar.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):], None)

        # 行事曆 API - GET
        if path == "/api/events":
            try:
                from calendar_api import load_events
                events = load_events()
                return self._send(200, events)
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if path == "/api/analyze":
            symbol = (qs.get("symbol", [""])[0]).strip()
            market = (qs.get("market", ["auto"])[0]).strip()
            range_ = (qs.get("range", ["6mo"])[0]).strip()
            if not symbol:
                return self._send(400, {"error": "missing symbol"})
            sym = normalize_symbol(symbol, market)
            # 重新判斷 market：若為純數字 / .TW / .TWO → tw；若為 ^xxx → index；其他 → us
            if market == "auto":
                if sym.endswith(".TW") or sym.endswith(".TWO"):
                    market = "tw"
                elif sym.startswith("^"):
                    market = "index"
                else:
                    market = "us"
            try:
                parsed = fetch_chart(sym, market, range_=range_)
            except Exception as e:
                return self._send(502, {"error": str(e), "symbol": sym})
            if not parsed or not parsed.get("candles"):
                return self._send(404, {"error": "no_data", "symbol": sym})
            parsed["analysis"] = analyze_patterns(parsed["candles"])
            return self._send(200, parsed)

        return self._send(404, {"error": "not found"})

    def _serve_static(self, rel, ctype):
        p = os.path.normpath(os.path.join(STATIC, rel))
        if not p.startswith(STATIC) or not os.path.isfile(p):
            return self._send(404, "404")
        if not ctype:
            ext = os.path.splitext(p)[1].lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".png": "image/png",
                ".svg": "image/svg+xml",
            }.get(ext, "application/octet-stream")
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    # Render/cloud deployment 自動設定 PORT，本機預設 5777
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    print(f"🟢 型態學波段分析網站 http://{host}:{PORT}/")
    HTTPServer((host, PORT), PatternAnalysisHandler).serve_forever()
