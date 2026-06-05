#!/usr/bin/env python3
"""
BTC Signal Bot — Bitget API
GitHub Actions içinde doğrudan çalışır, Base44'e bağlanmaz.
"""

import os
import json
import math
import time
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_2", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "2055780815")
BITGET_BASE = "https://api.bitget.com/api/v2"
CACHE_FILE = "btc_cache.json"

SIGNAL_THRESHOLD = 3.0
READY_THRESHOLD = 1.8

# ── CACHE ─────────────────────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {"last_signal": "", "last_ready": "", "last_price": 0}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

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
def get_ohlcv(granularity, limit=200):
    try:
        r = requests.get(
            f"{BITGET_BASE}/mix/market/candles",
            params={"symbol": "BTCUSDT", "productType": "USDT-FUTURES", "granularity": granularity, "limit": limit},
            timeout=10
        )
        d = r.json()
        if d.get("code") == "00000":
            return [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in d["data"]]
    except:
        pass
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

def analyze_tf(candles):
    if not candles or len(candles) < 50:
        return 0, {}
    closes = [c["close"] for c in candles]
    price = closes[-1]
    score = 0
    details = {}

    rsi = calc_rsi(closes)
    details["rsi"] = rsi
    if rsi < 35: score += 2
    elif rsi > 65: score -= 2
    elif rsi < 45: score += 1
    elif rsi > 55: score -= 1

    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    details["ema_bull"] = ema9 > ema21 > ema50
    if ema9 > ema21 > ema50: score += 2
    elif ema9 < ema21 < ema50: score -= 2

    return score, details

def find_pivot_levels(candles):
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    resistances, supports = [], []
    left, right = 5, 5
    for i in range(left, len(candles) - right):
        if highs[i] == max(highs[i-left:i+right+1]):
            resistances.append(highs[i])
        if lows[i] == min(lows[i-left:i+right+1]):
            supports.append(lows[i])

    def cluster(levels):
        if not levels:
            return []
        sorted_l = sorted(set(levels))
        out = [sorted_l[0]]
        for lv in sorted_l[1:]:
            if abs(lv - out[-1]) / out[-1] > 0.003:
                out.append(lv)
        return out

    return cluster(resistances), cluster(supports)

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    print("🔍 BTC Signal Bot başlıyor...")
    cache = load_cache()

    tf15m = get_ohlcv("15min", 200)
    tf1h = get_ohlcv("1H", 200)
    tf4h = get_ohlcv("4H", 200)

    if not tf15m:
        print("Veri alınamadı.")
        return

    price = tf15m[-1]["close"]
    score15, _ = analyze_tf(tf15m)
    score1h, _ = analyze_tf(tf1h)
    score4h, _ = analyze_tf(tf4h)

    weighted = (score15 * 1 + score1h * 2 + score4h * 3) / 6

    res1h, sup1h = find_pivot_levels(tf1h)
    res4h, sup4h = find_pivot_levels(tf4h)
    all_res = sorted(set(res1h + res4h))
    all_sup = sorted(set(sup1h + sup4h), reverse=True)

    nearest_res = next((r for r in all_res if r > price), None)
    nearest_sup = next((s for s in all_sup if s < price), None)

    scores_str = f"15m:{score15:.0f} 1H:{score1h:.0f} 4H:{score4h:.0f}"
    res_str = f"{nearest_res:.0f}" if nearest_res else "-"
    sup_str = f"{nearest_sup:.0f}" if nearest_sup else "-"

    is_long_signal = weighted >= SIGNAL_THRESHOLD
    is_short_signal = weighted <= -SIGNAL_THRESHOLD
    is_long_ready = READY_THRESHOLD <= weighted < SIGNAL_THRESHOLD
    is_short_ready = -SIGNAL_THRESHOLD < weighted <= -READY_THRESHOLD

    last_price = cache.get("last_price", 0)
    price_moved = abs(price - last_price) / last_price * 100 > 2 if last_price else True

    if is_long_signal or is_short_signal:
        direction_raw = "LONG" if is_long_signal else "SHORT"
        direction = "LONG 🟢" if is_long_signal else "SHORT 🔴"
        if cache.get("last_signal") != direction_raw or price_moved:
            send_telegram(
                f"🚨 *BTC İŞLEM SİNYALİ*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 Fiyat: `{price:.0f}`\n"
                f"📊 Yön: *{direction}*\n"
                f"📈 Skor: {weighted:.1f} | {scores_str}\n"
                f"📐 Resistance: `{res_str}` | Support: `{sup_str}`"
            )
            cache["last_signal"] = direction_raw
            cache["last_ready"] = ""
            cache["last_price"] = price
            save_cache(cache)
            print(f"✅ Sinyal gönderildi: {direction_raw} @ {price:.0f}")
    elif is_long_ready or is_short_ready:
        direction_raw = "LONG" if is_long_ready else "SHORT"
        direction = "LONG 🟢" if is_long_ready else "SHORT 🔴"
        if cache.get("last_ready") != direction_raw or price_moved:
            send_telegram(
                f"⚠️ *HAZIR OL — BTC*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 Fiyat: `{price:.0f}`\n"
                f"🔔 {direction} işlem koşulları oluşuyor\n"
                f"📊 Skor: {weighted:.1f} | {scores_str}\n"
                f"📐 Resistance: `{res_str}` | Support: `{sup_str}`"
            )
            cache["last_ready"] = direction_raw
            cache["last_price"] = price
            save_cache(cache)
            print(f"⚠️ Hazır ol gönderildi: {direction_raw} @ {price:.0f}")
    else:
        print(f"Silent — BTC @ {price:.0f}, skor: {weighted:.1f}")

if __name__ == "__main__":
    main()
