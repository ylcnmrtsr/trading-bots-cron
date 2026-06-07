#!/usr/bin/env python3
"""
Bot 2 Runner — Bitget Altcoin Signal Bot + Watchdog
State: Base44 ActiveTrade entity (REST API)
GitHub Actions içinde çalışır, kredi tüketmez (integration kredisi minimal)
"""

import os
import sys
import json
import time
import math
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_3", "")
CHAT_ID = "2055780815"
BITGET_BASE = "https://api.bitget.com/api/v2"
BASE44_API = "https://app.base44.com/api/apps/6a1d973568af9b984e0f1cc8/entities/ActiveTrade"
BASE44_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJjbGllbnRfaWQiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJhcHBfaWQiOiI2YTFkOTczNTY4YWY5Yjk4NGUwZjFjYzgiLCJhdWQiOiJiYXNlNDRfYXBpIiwic2NvcGUiOiJhcHAuYWNjZXNzIiwiZXhwIjoxNzgwODU3MDE4LCJpYXQiOjE3ODA4NTM0MTh9.7FBx9m9K715fZkUlgKh5VBsF2_UgsNTlbC7M7aBg8A8"

PARAMS = {
    "minRR": 2.0,
    "maxSlPct": 4.5,
    "minSlPct": 0.3,
    "blacklistHours": 48,
    "maxOpenTrades": 3,
    "tp1Ratio": 1.8,
    "tp2Ratio": 5.0,
    "avoidCoins": ["SKYAIUSDT"],
    "minScoreThreshold": 3.5,
    "leverage": 5,
}

MODE = sys.argv[1] if len(sys.argv) > 1 else "watchdog"

# ── BASE44 DB ─────────────────────────────────────────────────────────
def b44_headers():
    return {
        "Authorization": f"Bearer {BASE44_TOKEN}",
        "Content-Type": "application/json"
    }

def get_open_trades():
    r = requests.get(BASE44_API, headers=b44_headers(), params={"status": "OPEN"}, timeout=15)
    if r.status_code == 200:
        return r.json() if isinstance(r.json(), list) else r.json().get("records", [])
    print(f"DB GET error: {r.status_code} {r.text[:100]}")
    return []

def get_all_trades(limit=200):
    r = requests.get(BASE44_API, headers=b44_headers(), params={"_limit": limit}, timeout=15)
    if r.status_code == 200:
        return r.json() if isinstance(r.json(), list) else r.json().get("records", [])
    return []

def create_trade(trade_data):
    r = requests.post(BASE44_API, headers=b44_headers(), json=trade_data, timeout=15)
    if r.status_code in (200, 201):
        return r.json()
    print(f"DB CREATE error: {r.status_code} {r.text[:200]}")
    return None

def update_trade(trade_id, update_data):
    r = requests.put(f"{BASE44_API}/{trade_id}", headers=b44_headers(), json=update_data, timeout=15)
    if r.status_code == 200:
        return r.json()
    print(f"DB UPDATE error: {r.status_code} {r.text[:200]}")
    return None

def get_blacklisted():
    all_trades = get_all_trades()
    now = datetime.now(timezone.utc)
    blacklisted = set()
    for t in all_trades:
        if t.get("status") == "SL_HIT" and t.get("close_time"):
            try:
                close_dt = datetime.fromisoformat(t["close_time"].replace("Z", "+00:00"))
                if (now - close_dt).total_seconds() < PARAMS["blacklistHours"] * 3600:
                    blacklisted.add(t["symbol"])
            except:
                pass
    return blacklisted

# ── TELEGRAM ──────────────────────────────────────────────────────────
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")

# ── BITGET API ────────────────────────────────────────────────────────
def get_price(symbol):
    try:
        r = requests.get(
            f"{BITGET_BASE}/mix/market/ticker",
            params={"symbol": symbol, "productType": "USDT-FUTURES"},
            timeout=6
        )
        d = r.json()
        if d.get("code") == "00000" and d.get("data"):
            return float(d["data"][0]["lastPr"])
    except:
        pass
    return None

def get_ohlcv(symbol, granularity, limit=100):
    try:
        r = requests.get(
            f"{BITGET_BASE}/mix/market/candles",
            params={"symbol": symbol, "productType": "USDT-FUTURES", "granularity": granularity, "limit": limit},
            timeout=10
        )
        d = r.json()
        if d.get("code") != "00000":
            return []
        return [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in d["data"]]
    except:
        return []

def get_futures_symbols():
    try:
        r = requests.get(
            f"{BITGET_BASE}/mix/market/tickers",
            params={"productType": "USDT-FUTURES"},
            timeout=15
        )
        d = r.json()
        if d.get("code") != "00000":
            return []
        items = d["data"]
        items = [x for x in items if x.get("symbol", "").endswith("USDT") and float(x.get("usdtVolume") or x.get("quoteVolume") or 0) > 5_000_000]
        items.sort(key=lambda x: float(x.get("usdtVolume") or x.get("quoteVolume") or 0), reverse=True)
        return [x["symbol"] for x in items[:80]]
    except:
        return []

# ── İNDİKATÖRLER ─────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100
    return 100 - 100 / (1 + ag / al)

def calc_ema(data, period):
    if len(data) < period:
        return data[-1] if data else 0
    ema = sum(data[:period]) / period
    k = 2 / (period + 1)
    for v in data[period:]:
        ema = v * k + ema * (1 - k)
    return ema

def calc_atr(candles, period=14):
    if len(candles) < 2:
        return candles[0]["close"] * 0.02 if candles else 1
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / min(period, len(trs))

def find_sr_levels(candles):
    left, right = 5, 5
    res, sup = [], []
    for i in range(left, len(candles) - right):
        hi_slice = [c["high"] for c in candles[i-left:i+right+1]]
        lo_slice = [c["low"] for c in candles[i-left:i+right+1]]
        if candles[i]["high"] == max(hi_slice):
            res.append(candles[i]["high"])
        if candles[i]["low"] == min(lo_slice):
            sup.append(candles[i]["low"])

    def cluster(levels):
        if not levels:
            return []
        sorted_levels = sorted(set(levels))
        out = [sorted_levels[0]]
        for lv in sorted_levels[1:]:
            if abs(lv - out[-1]) / out[-1] > 0.003:
                out.append(lv)
        return out

    return cluster(res)[-6:], cluster(sup)[:6]

def score_tf(candles):
    if not candles or len(candles) < 50:
        return 0
    closes = [c["close"] for c in candles]
    price = closes[-1]
    score = 0

    rsi = calc_rsi(closes)
    if rsi < 30: score += 3
    elif rsi < 40: score += 2
    elif rsi < 45: score += 1
    elif rsi > 70: score -= 3
    elif rsi > 60: score -= 2
    elif rsi > 55: score -= 1

    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    if ema9 > ema21 > ema50: score += 2
    elif ema9 < ema21 < ema50: score -= 2
    if price > ema50: score += 1
    else: score -= 1

    if len(closes) >= 35:
        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)
        macd = ema12 - ema26
        if macd > 0: score += 2
        else: score -= 2

    if len(candles) >= 14:
        sl = candles[-14:]
        lo_min = min(c["low"] for c in sl)
        hi_max = max(c["high"] for c in sl)
        k = ((price - lo_min) / (hi_max - lo_min)) * 100 if hi_max != lo_min else 50
        if k < 20: score += 2
        elif k < 30: score += 1
        elif k > 80: score -= 2
        elif k > 70: score -= 1

    if len(closes) >= 20:
        closes20 = closes[-20:]
        mid = sum(closes20) / 20
        std = math.sqrt(sum((x - mid)**2 for x in closes20) / 20)
        if price <= mid - 2*std: score += 2
        elif price >= mid + 2*std: score -= 2

    return score

def calc_tp_sl(price, is_long, atr, res, sup):
    min_sl_dist = atr * 1.5
    max_sl_dist = price * (PARAMS["maxSlPct"] / 100)
    min_sl_dist_floor = price * (PARAMS["minSlPct"] / 100)

    if is_long:
        sup_below = sorted([s for s in sup if s < price * 0.998], reverse=True)
        sl = sup_below[0] if sup_below else (price - min_sl_dist)
    else:
        res_above = sorted([r for r in res if r > price * 1.002])
        sl = res_above[0] if res_above else (price + min_sl_dist)

    sl_dist = abs(price - sl)
    sl_dist = max(sl_dist, min_sl_dist_floor)
    sl_dist = min(sl_dist, max_sl_dist)
    sl_dist = max(sl_dist, min_sl_dist)

    sl = (price - sl_dist) if is_long else (price + sl_dist)
    tp1 = (price + sl_dist * PARAMS["tp1Ratio"]) if is_long else (price - sl_dist * PARAMS["tp1Ratio"])
    tp2 = (price + sl_dist * PARAMS["tp2Ratio"]) if is_long else (price - sl_dist * PARAMS["tp2Ratio"])

    return {"tp1": round(tp1, 6), "tp2": round(tp2, 6), "sl": round(sl, 6), "rr": PARAMS["tp2Ratio"], "sl_dist": sl_dist}

# ── SCAN ──────────────────────────────────────────────────────────────
def run_scan():
    print("🔍 Bot2 Scan başlıyor...")
    open_trades = get_open_trades()

    if len(open_trades) >= PARAMS["maxOpenTrades"]:
        print(f"Scan skipped — {len(open_trades)}/{PARAMS['maxOpenTrades']} slots full.")
        return

    blacklisted = get_blacklisted()
    open_symbols = {t["symbol"] for t in open_trades}
    symbols = get_futures_symbols()
    print(f"Toplam {len(symbols)} coin taranıyor...")

    results = []
    for symbol in symbols:
        if symbol in blacklisted or symbol in PARAMS["avoidCoins"] or symbol in open_symbols:
            continue
        try:
            c15m = get_ohlcv(symbol, "15m", 100)
            c1h = get_ohlcv(symbol, "1H", 100)
            c4h = get_ohlcv(symbol, "4H", 80)
            if not c1h or not c4h:
                continue

            s15 = score_tf(c15m)
            s1h = score_tf(c1h)
            s4h = score_tf(c4h)
            weighted = (s15 * 1 + s1h * 2 + s4h * 3) / 6

            if abs(weighted) < PARAMS["minScoreThreshold"]:
                continue

            is_long = weighted > 0
            price = c1h[-1]["close"]
            atr = calc_atr(c1h)
            combined = c1h + c4h
            res, sup = find_sr_levels(combined)
            calc = calc_tp_sl(price, is_long, atr, res, sup)

            if calc["rr"] < PARAMS["minRR"]:
                continue

            results.append({
                "symbol": symbol, "score": weighted,
                "direction": "LONG" if is_long else "SHORT",
                "tp1": calc["tp1"], "tp2": calc["tp2"], "sl": calc["sl"],
                "rr": calc["rr"], "price": price
            })
        except:
            pass
        time.sleep(0.05)

    results.sort(key=lambda x: abs(x["score"]), reverse=True)
    slots_available = PARAMS["maxOpenTrades"] - len(open_trades)
    top_signals = results[:min(slots_available, 3)]

    if not top_signals:
        print("Scan complete — no qualifying signals found.")
        return

    for sig in top_signals:
        sym = sig["symbol"].replace("USDT", "")
        real_price = get_price(sig["symbol"]) or sig["price"]
        is_long = sig["direction"] == "LONG"

        tp1_pct = abs(sig["tp1"] - real_price) / real_price * 100
        tp2_pct = abs(sig["tp2"] - real_price) / real_price * 100
        sl_pct = abs(sig["sl"] - real_price) / real_price * 100
        tp1_lev = tp1_pct * PARAMS["leverage"]
        tp2_lev = tp2_pct * PARAMS["leverage"]
        sl_lev = sl_pct * PARAMS["leverage"]

        trade_data = {
            "symbol": sig["symbol"],
            "direction": sig["direction"],
            "entry_price": real_price,
            "tp": sig["tp1"],
            "sl": sig["sl"],
            "original_sl": sig["sl"],
            "rr": round(sig["rr"], 2),
            "score": round(sig["score"], 2),
            "status": "OPEN",
            "sl_moved_breakeven": False,
            "sl_moved_profit": False,
            "tp_extended": False,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "close_time": None,
            "result_pct": None,
            "notes": json.dumps({
                "tp2": sig["tp2"],
                "tp1_pct": f"{tp1_pct:.2f}",
                "tp1_lev": f"{tp1_lev:.2f}",
                "tp2_pct": f"{tp2_pct:.2f}",
                "tp2_lev": f"{tp2_lev:.2f}",
                "sl_pct": f"{sl_pct:.2f}",
                "sl_lev": f"{sl_lev:.2f}",
            })
        }

        created = create_trade(trade_data)
        if not created:
            print(f"❌ DB'ye kaydedilemedi: {sym}")
            continue

        dir_str = "📈 LONG 🟢" if is_long else "📉 SHORT 🔴"
        send_telegram(
            f"🚨 *BOT 2 — İŞLEM SİNYALİ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *{sym}* | {dir_str}\n"
            f"📍 Giriş: `{real_price:.6g}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *TP1*: `{sig['tp1']:.6g}`\n"
            f"   💰 Basit: +{tp1_pct:.2f}% | 🔗 @{PARAMS['leverage']}x: +{tp1_lev:.2f}%\n"
            f"🎯 *TP2*: `{sig['tp2']:.6g}`\n"
            f"   💰 Basit: +{tp2_pct:.2f}% | 🔗 @{PARAMS['leverage']}x: +{tp2_lev:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ *SL*: `{sig['sl']:.6g}`\n"
            f"   💸 Basit: -{sl_pct:.2f}% | 🔗 @{PARAMS['leverage']}x: -{sl_lev:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ *R:R* → {sig['rr']:.2f}R\n"
            f"📊 *Skor* → {sig['score']:.2f}"
        )
        print(f"✅ Sinyal gönderildi ve DB'ye kaydedildi: {sym} {sig['direction']}")

# ── WATCHDOG ──────────────────────────────────────────────────────────
def run_watchdog():
    print("👁️ Bot2 Watchdog başlıyor...")
    open_trades = get_open_trades()

    if not open_trades:
        print("Watchdog: açık işlem yok.")
        return

    status_lines = []

    for trade in open_trades:
        trade_id = trade["id"]
        symbol = trade["symbol"]
        direction = trade["direction"]
        entry = float(trade["entry_price"])
        sl = float(trade["sl"])
        tp = float(trade["tp"])
        orig_sl = float(trade.get("original_sl") or sl)
        is_long = direction == "LONG"

        # tp2'yi notes'tan al
        try:
            notes = json.loads(trade.get("notes") or "{}")
            tp2 = float(notes.get("tp2", tp))
        except:
            tp2 = tp

        price = get_price(symbol)
        if not price:
            print(f"Fiyat alınamadı: {symbol}")
            continue

        sym = symbol.replace("USDT", "")
        sl_dist = abs(entry - orig_sl)

        pnl_raw = ((price - entry) / entry * 100) if is_long else ((entry - price) / entry * 100)
        pnl_lev = pnl_raw * PARAMS["leverage"]
        pnl_emoji = "🟢" if pnl_raw >= 0 else "🔴"
        pnl_sign = "+" if pnl_raw >= 0 else ""

        updated = False

        # TP2 HIT
        if (is_long and price >= tp2) or (not is_long and price <= tp2):
            pct = abs(tp2 - entry) / entry * 100
            lev = pct * PARAMS["leverage"]
            update_trade(trade_id, {
                "status": "TP_HIT",
                "close_time": datetime.now(timezone.utc).isoformat(),
                "result_pct": round(pct, 2)
            })
            send_telegram(
                f"🏆 *TP2 HIT — {sym}* 🏆\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{'📈 LONG' if is_long else '📉 SHORT'} tam hedef!\n"
                f"💰 Giriş: `{entry:.6g}` → TP2: `{tp2:.6g}`\n"
                f"📊 Basit: +{pct:.2f}% | 🔗 @{PARAMS['leverage']}x: +{lev:.2f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            print(f"🏆 TP2 HIT: {sym}")
            continue

        # TP1 HIT
        if (is_long and price >= tp) or (not is_long and price <= tp):
            pct = abs(tp - entry) / entry * 100
            lev = pct * PARAMS["leverage"]
            if not trade.get("sl_moved_breakeven"):
                update_trade(trade_id, {
                    "sl": entry,
                    "sl_moved_breakeven": True
                })
                trade["sl"] = entry
                sl = entry
                send_telegram(
                    f"✅ *TP1 HIT — {sym}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{'📈 LONG' if is_long else '📉 SHORT'} ilk hedef tuttu!\n"
                    f"💰 Basit: +{pct:.2f}% | 🔗 @{PARAMS['leverage']}x: +{lev:.2f}%\n"
                    f"🛡️ SL → Breakeven (`{entry:.6g}`)\n"
                    f"🎯 TP2'ye gitmek için bekle\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                print(f"✅ TP1 HIT → BE: {sym}")
                updated = True

        # SL HIT
        elif (is_long and price <= sl) or (not is_long and price >= sl):
            pct = abs(sl - entry) / entry * 100
            lev = pct * PARAMS["leverage"]
            update_trade(trade_id, {
                "status": "SL_HIT",
                "close_time": datetime.now(timezone.utc).isoformat(),
                "result_pct": round(-pct, 2)
            })
            send_telegram(
                f"❌ *SL HIT — {sym}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{'📈 LONG' if is_long else '📉 SHORT'} stoploss!\n"
                f"💸 Basit: -{pct:.2f}% | 🔗 @{PARAMS['leverage']}x: -{lev:.2f}%\n"
                f"⛔ 48 saat blacklist'e eklendi\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            print(f"❌ SL HIT: {sym}")
            continue

        # +1R SL Yönetimi
        if sl_dist > 0 and not updated:
            current_r = ((price - entry) / sl_dist) if is_long else ((entry - price) / sl_dist)
            if current_r >= 1.0 and not trade.get("sl_moved_profit"):
                new_sl = round((entry + sl_dist * 0.5) if is_long else (entry - sl_dist * 0.5), 6)
                update_trade(trade_id, {"sl": new_sl, "sl_moved_profit": True})
                trade["sl"] = new_sl
                sl = new_sl
                print(f"+1R SL taşındı: {sym} → {new_sl}")

        # Durum satırı
        be_status = " 🔒BE" if trade.get("sl_moved_breakeven") else ""
        status_lines.append(
            f"🪙 *{sym}* {'📈' if is_long else '📉'} {direction}{be_status}\n"
            f"   📍 Giriş: `{entry:.6g}`\n"
            f"   🎯 TP1: `{tp:.6g}` | TP2: `{tp2:.6g}`\n"
            f"   🛡️ SL: `{sl:.6g}`\n"
            f"   💵 Şu an: `{price:.6g}` {pnl_emoji} {pnl_sign}{pnl_raw:.2f}% ({pnl_sign}{pnl_lev:.1f}% kaldıraçlı)"
        )

    if status_lines:
        now_str = datetime.now(tz=timezone(timedelta(hours=3))).strftime("%H:%M")
        sep = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        send_telegram(
            f"📊 *BOT 2 — AÇIK İŞLEMLER* ({now_str})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + sep.join(status_lines) +
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Toplam: {len(status_lines)}/{PARAMS['maxOpenTrades']} slot dolu"
        )
        print(f"📊 Durum raporu gönderildi: {len(status_lines)} işlem")

# ── WEEKLY REVIEW ─────────────────────────────────────────────────────
def run_weekly():
    print("📅 Haftalık rapor başlıyor...")
    all_trades = get_all_trades()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    closed = [
        t for t in all_trades
        if t.get("close_time") and
        datetime.fromisoformat(t["close_time"].replace("Z", "+00:00")) >= week_ago
    ]

    if not closed:
        send_telegram("📅 *HAFTALIK RAPOR*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nBu hafta kapanan işlem yok.")
        return

    tp_hits = [t for t in closed if t["status"] == "TP_HIT"]
    sl_hits = [t for t in closed if t["status"] == "SL_HIT"]
    win_rate = len(tp_hits) / len(closed) * 100 if closed else 0
    avg_win = sum(t.get("result_pct", 0) or 0 for t in tp_hits) / len(tp_hits) if tp_hits else 0
    avg_loss = sum(abs(t.get("result_pct", 0) or 0) for t in sl_hits) / len(sl_hits) if sl_hits else 0

    send_telegram(
        f"📅 *HAFTALIK RAPOR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Toplam İşlem: {len(closed)}\n"
        f"✅ TP Hit: {len(tp_hits)} | ❌ SL Hit: {len(sl_hits)}\n"
        f"🎯 Kazanma Oranı: %{win_rate:.1f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ort. Kazanç: +{avg_win:.2f}%\n"
        f"💸 Ort. Kayıp: -{avg_loss:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    print("📅 Haftalık rapor gönderildi.")

# ── MAIN ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if MODE == "scan":
        run_scan()
    elif MODE == "watchdog":
        run_watchdog()
    elif MODE == "weekly":
        run_weekly()
    else:
        print(f"Bilinmeyen mod: {MODE}")
