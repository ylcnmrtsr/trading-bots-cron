#!/usr/bin/env python3
"""
BTC Signal Bot — Bitget API
State: Base44 BotCache entity (REST API)
GitHub Actions içinde çalışır, Base44 function çağırmaz.
"""

import os
import json
import math
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_2", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "2055780815")
BITGET_BASE = "https://api.bitget.com/api/v2"

BASE44_CACHE_API = "https://app.base44.com/api/apps/6a1d973568af9b984e0f1cc8/entities/BotCache"
BASE44_TOKEN = os.environ.get("BASE44_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJjbGllbnRfaWQiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJhcHBfaWQiOiI2YTFkOTczNTY4YWY5Yjk4NGUwZjFjYzgiLCJhdWQiOiJiYXNlNDRfYXBpIiwic2NvcGUiOiJhcHAuYWNjZXNzIiwiZXhwIjoxNzgxMDUyNjg3LCJpYXQiOjE3ODEwNDkwODd9.8VDre2MJ-S-pn62OnBMPnc4MT-XnLbQmAvWF16t8HOg")
CACHE_KEY = "btc_signal_cache"

SIGNAL_THRESHOLD = 3.0
READY_THRESHOLD = 1.8

# ── CACHE (Base44 DB) ─────────────────────────────────────────────────
def b44_headers():
    return {"Authorization": f"Bearer {BASE44_TOKEN}", "Content-Type": "application/json"}

def load_cache():
    try:
        r = requests.get(BASE44_CACHE_API, headers=b44_headers(), params={"key": CACHE_KEY}, timeout=10)
        if r.status_code == 200:
            records = r.json() if isinstance(r.json(), list) else r.json().get("records", [])
            if records:
                return json.loads(records[0]["value"]), records[0]["id"]
    except Exception as e:
        print(f"Cache load error: {e}")
    return {"last_signal": "", "last_ready": "", "last_price": 0}, None

def save_cache(data, record_id=None):
    try:
        payload = {"key": CACHE_KEY, "value": json.dumps(data)}
        if record_id:
            r = requests.put(f"{BASE44_CACHE_API}/{record_id}", headers=b44_headers(), json=payload, timeout=10)
        else:
            r = requests.post(BASE44_CACHE_API, headers=b44_headers(), json=payload, timeout=10)
        if r.status_code not in (200, 201):
            print(f"Cache save error: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"Cache save error: {e}")

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


def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_atr_pct(candles, period=14):
    atr = calc_atr(candles, period)
    price = candles[-1]["close"] if candles else 0
    return (atr / price) * 100 if atr and price > 0 else 0.5

def check_volume(candles, mult=1.2):
    """Son mumun hacmi, son 20 mum ort. mult katı mı?"""
    if len(candles) < 21:
        return True, 1.0
    vols = [c["volume"] for c in candles[-21:-1]]
    avg_vol = sum(vols) / len(vols)
    last_vol = candles[-1]["volume"]
    ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
    return ratio >= mult, round(ratio, 2)


def get_order_book_signal(symbol, price, product_type="USDT-FUTURES", range_pct=0.005):
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/merge-depth",
            params={"symbol": symbol, "productType": product_type, "limit": "100"},
            timeout=5
        )
        if r.status_code != 200:
            return 0, 0, 0, "OB hata"
        d = r.json()["data"]
        asks = [[float(x[0]), float(x[1])] for x in d["asks"]]
        bids = [[float(x[0]), float(x[1])] for x in d["bids"]]
        ask_wall = sum(x[1] for x in asks if x[0] <= price * (1 + range_pct))
        bid_wall = sum(x[1] for x in bids if x[0] >= price * (1 - range_pct))
        total = ask_wall + bid_wall
        if total == 0:
            return 0, 0, 0, "OB veri yok"
        bid_ratio = bid_wall / total
        avg_ask_vol = sum(a[1] for a in asks) / len(asks)
        avg_bid_vol = sum(b[1] for b in bids) / len(bids)
        ask_liq = [x[0] for x in asks if x[1] > avg_ask_vol * 3]
        bid_liq = [x[0] for x in bids if x[1] > avg_bid_vol * 3]
        wall_note = []
        if ask_liq: wall_note.append(f"Satış@{ask_liq[0]:.0f}")
        if bid_liq: wall_note.append(f"Alış@{bid_liq[0]:.0f}")
        wall_note = " | ".join(wall_note) if wall_note else "Duvar yok"
        ob_score = 1 if bid_ratio >= 0.60 else (-1 if bid_ratio <= 0.40 else 0)
        return ob_score, round(bid_wall,1), round(ask_wall,1), wall_note
    except Exception as e:
        return 0, 0, 0, f"OB err:{e}"


def get_tp_from_liquidity(symbol, price, direction, product_type="USDT-FUTURES"):
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/merge-depth",
            params={"symbol": symbol, "productType": product_type, "limit": "200"},
            timeout=5
        )
        if r.status_code != 200:
            return None
        d = r.json()["data"]
        asks = [[float(x[0]), float(x[1])] for x in d["asks"]]
        bids = [[float(x[0]), float(x[1])] for x in d["bids"]]
        avg_ask = sum(a[1] for a in asks) / len(asks) if asks else 0
        avg_bid = sum(b[1] for b in bids) / len(bids) if bids else 0
        if direction == "LONG":
            walls = [x[0] for x in asks if x[0] > price and x[1] > avg_ask * 2.5]
            return min(walls) if walls else None
        else:
            walls = [x[0] for x in bids if x[0] < price and x[1] > avg_bid * 2.5]
            return max(walls) if walls else None
    except:
        return None

def self_learn_btc(cache):
    """BTC geçmiş sinyalleri analiz et, eşiği otomatik ayarla. Sessiz çalışır."""
    try:
        history = cache.get("signal_history", [])
        if len(history) < 5:
            print(f"  Bot1 self-learn: yeterli geçmiş yok ({len(history)}/5)")
            return cache
        recent = history[-8:]
        wins   = [h for h in recent if h.get("outcome") == "win"]
        losses = [h for h in recent if h.get("outcome") == "loss"]
        win_rate = len(wins) / len(recent)
        changed = []
        threshold = cache.get("signal_threshold", 3.0)
        ready_threshold = cache.get("ready_threshold", 1.8)
        if win_rate < 0.38 and len(recent) >= 5:
            threshold = round(min(threshold + 0.2, 4.5), 1)
            ready_threshold = round(min(ready_threshold + 0.1, 3.0), 1)
            changed.append(f"eşik↑{threshold}")
        elif win_rate >= 0.70 and len(recent) >= 5:
            threshold = round(max(threshold - 0.1, 2.0), 1)
            changed.append(f"eşik↓{threshold}")
        cache["signal_threshold"] = threshold
        cache["ready_threshold"] = ready_threshold
        print(f"  Bot1 self-learn: WR={win_rate:.0%} | {(', '.join(changed)) if changed else 'değişiklik yok'} | eşik={threshold}")
    except Exception as e:
        print(f"  Bot1 self-learn hata: {e}")
    return cache

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
    cache, cache_id = load_cache()
    print(f"Cache yüklendi: {cache}")

    tf15m = get_ohlcv("15m", 200)
    tf30m = get_ohlcv("30m", 200)
    tf1h  = get_ohlcv("1H", 200)
    tf4h  = get_ohlcv("4H", 200)

    if not tf15m:
        print("Veri alınamadı.")
        return

    price = tf15m[-1]["close"]
    score15, _ = analyze_tf(tf15m)
    score30, _ = analyze_tf(tf30m)
    score1h, _ = analyze_tf(tf1h)
    score4h, _ = analyze_tf(tf4h)

    weighted = (score15 * 1 + score30 * 2 + score1h * 3 + score4h * 2) / 8

    # Self-learning — cache'den eşikleri yükle
    cache = self_learn_btc(cache)
    sig_threshold = cache.get("signal_threshold", SIGNAL_THRESHOLD)
    rdy_threshold = cache.get("ready_threshold", READY_THRESHOLD)

    # ATR dinamik eşik (1H üzerinden)
    atr_pct = calc_atr_pct(tf1h)
    if atr_pct > 2.0:    sig_threshold = round(max(sig_threshold - 0.4, 2.0), 1)
    elif atr_pct > 1.0:  sig_threshold = round(max(sig_threshold - 0.2, 2.0), 1)
    elif atr_pct < 0.3:  sig_threshold = round(min(sig_threshold + 0.5, 5.0), 1)
    elif atr_pct < 0.5:  sig_threshold = round(min(sig_threshold + 0.2, 5.0), 1)
    rdy_threshold = round(sig_threshold - 1.2, 1)
    print(f"  ATR%:{atr_pct:.2f} | Dinamik eşik: {sig_threshold:.1f} | Hazır: {rdy_threshold:.1f}")

    # Hacim konfirmasyonu (1H)
    vol_ok_1h, vol_ratio_1h = check_volume(tf1h, 1.2)
    print(f"  Hacim 1H: {vol_ratio_1h:.2f}x {'✅' if vol_ok_1h else '(düşük)'}")

    res1h, sup1h = find_pivot_levels(tf1h)
    res4h, sup4h = find_pivot_levels(tf4h)
    all_res = sorted(set(res1h + res4h))
    all_sup = sorted(set(sup1h + sup4h), reverse=True)

    nearest_res = next((r for r in all_res if r > price), None)
    nearest_sup = next((s for s in all_sup if s < price), None)

    scores_str = f"15m:{score15:.0f} 30m:{score30:.0f} 1H:{score1h:.0f} 4H:{score4h:.0f}"
    res_str = f"{nearest_res:.0f}" if nearest_res else "-"
    sup_str = f"{nearest_sup:.0f}" if nearest_sup else "-"

    # ── ORDER BOOK filtresi ──
    ob_score, bid_wall, ask_wall, wall_note = get_order_book_signal("BTCUSDT", price)
    print(f"  OB: bid={bid_wall} ask={ask_wall} skor={ob_score} | {wall_note}")
    direction_for_liq = "LONG" if weighted >= 0 else "SHORT"
    liq_tp = get_tp_from_liquidity("BTCUSDT", price, direction_for_liq)

    is_long_signal  = weighted >= sig_threshold and vol_ok_1h and ob_score >= 0
    is_short_signal = weighted <= -sig_threshold and vol_ok_1h and ob_score <= 0
    is_long_ready   = rdy_threshold <= weighted < sig_threshold
    is_short_ready  = -sig_threshold < weighted <= -rdy_threshold

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
                f"📐 Resistance: `{res_str}` | Support: `{sup_str}`\n"
                f"📦 Hacim: `{vol_ratio_1h:.1f}x` | 📉 ATR%: `{atr_pct:.2f}`\n"
                f"📚 OB: Alış `{bid_wall:.0f}` / Satış `{ask_wall:.0f}` | {wall_note}" +
                (f"\n🎯 Likidasyon TP: `{liq_tp:.0f}`" if liq_tp else "")
            )
            cache["last_signal"] = direction_raw
            cache["last_ready"] = ""
            cache["last_price"] = price
            save_cache(cache, cache_id)
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
            save_cache(cache, cache_id)
            print(f"⚠️ Hazır ol gönderildi: {direction_raw} @ {price:.0f}")
    else:
        print(f"Silent — BTC @ {price:.0f}, skor: {weighted:.1f}")

if __name__ == "__main__":
    main()
