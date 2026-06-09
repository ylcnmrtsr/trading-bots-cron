#!/usr/bin/env python3
"""
Bot 3 — XAU/USDT Scalper Bot
- Multi-timeframe analiz: 1m, 5m, 15m
- Her dakika tarama + SL/TP izleme
- Minimum 1:3 RR oranı
- 100x-500x kaldıraç için optimize (küçük % hareketler)
- Tek aktif işlem, her iki yön (LONG/SHORT)
- Sadece TP/SL hit bildirimler
"""

import os, requests, time, json
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_7", "")
CHAT_ID        = "2055780815"
BASE44_TOKEN   = os.environ.get("BASE44_SERVICE_TOKEN", "")

def refresh_token():
    """Base44 token'ı runtime'da yenile — cache sorununu bypass et"""
    global BASE44_TOKEN
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "-c",
             "import os; import requests; "
             "r = requests.post('https://api.base44.com/api/auth/service-token', "
             "headers={'Authorization': 'Bearer ' + os.environ.get('BASE44_SERVICE_TOKEN','')}, timeout=8); "
             "print(r.json().get('token','')) if r.status_code == 200 else print('')"],
            capture_output=True, text=True, timeout=15
        )
        new_tok = result.stdout.strip()
        if new_tok and len(new_tok) > 20:
            BASE44_TOKEN = new_tok
            print(f"  Token yenilendi ✅")
        else:
            print(f"  Token yenileme başarısız, mevcut kullanılıyor")
    except Exception as e:
        print(f"  Token refresh hata: {e}")
APP_ID         = "6a1d973568af9b984e0f1cc8"

SYMBOL         = "XAUUSDT"
BITGET_BASE    = "https://api.bitget.com"

# ── BITGET API ──────────────────────────────────────────────────────────

def get_candles(interval_str, limit=100):
    url = f"{BITGET_BASE}/api/v2/mix/market/candles"
    params = {
        "symbol": SYMBOL,
        "productType": "USDT-FUTURES",
        "granularity": interval_str,
        "limit": str(limit)
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("code") != "00000":
            return []
        candles = []
        for c in data["data"]:
            candles.append({
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        return candles
    except Exception as e:
        print(f"Candle error ({interval_str}): {e}")
        return []

def get_price():
    url = f"{BITGET_BASE}/api/v2/mix/market/ticker"
    params = {"symbol": SYMBOL, "productType": "USDT-FUTURES"}
    try:
        r = requests.get(url, params=params, timeout=8)
        d = r.json()
        if d.get("code") == "00000":
            return float(d["data"][0]["lastPr"])
    except:
        pass
    return None


# ── BOT CACHE (Cooldown için) ──────────────────────────────────────────

def get_cache(key):
    try:
        r = requests.get(
            f"{BASE_URL}/BotCache",
            headers=HEADERS(),
            params={"key": key},
            timeout=8
        )
        if r.status_code == 200:
            items = r.json()
            for item in items:
                if item.get("key") == key:
                    return item.get("value")
    except:
        pass
    return None

def set_cache(key, value):
    try:
        # Varsa güncelle, yoksa oluştur
        r = requests.get(
            f"{BASE_URL}/BotCache",
            headers=HEADERS(),
            params={"key": key},
            timeout=8
        )
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    existing = item
                    break
        if existing:
            requests.patch(
                f"{BASE_URL}/BotCache/{existing['id']}",
                headers=HEADERS(),
                json={"value": value},
                timeout=8
            )
        else:
            requests.post(
                f"{BASE_URL}/BotCache",
                headers=HEADERS(),
                json={"key": key, "value": value},
                timeout=8
            )
    except Exception as e:
        print(f"Cache set error: {e}")

# ── TEKNIK INDIKATÖRLER ────────────────────────────────────────────────

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_adx(candles, period=14):
    """ADX hesapla — trend gücü"""
    if len(candles) < period * 2:
        return 20
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(candles)):
        h, l, ph, pl = candles[i]["high"], candles[i]["low"], candles[i-1]["high"], candles[i-1]["low"]
        pc = candles[i-1]["close"]
        plus_dms.append(max(h - ph, 0) if (h - ph) > (pl - l) else 0)
        minus_dms.append(max(pl - l, 0) if (pl - l) > (h - ph) else 0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    def smooth(arr, p):
        s = sum(arr[:p])
        res = [s]
        for v in arr[p:]:
            s = s - s/p + v
            res.append(s)
        return res
    str_ = smooth(trs, period)
    sdm_plus = smooth(plus_dms, period)
    sdm_minus = smooth(minus_dms, period)
    dx_list = []
    for i in range(len(str_)):
        if str_[i] == 0:
            continue
        di_plus = 100 * sdm_plus[i] / str_[i]
        di_minus = 100 * sdm_minus[i] / str_[i]
        denom = di_plus + di_minus
        if denom == 0:
            continue
        dx_list.append(100 * abs(di_plus - di_minus) / denom)
    if not dx_list:
        return 20
    return sum(dx_list[-period:]) / min(len(dx_list), period)

def find_support_resistance(candles, lookback=20):
    """Yakın destek/direnç seviyelerini bul"""
    highs = [c["high"] for c in candles[-lookback:]]
    lows  = [c["low"]  for c in candles[-lookback:]]
    resistance = max(highs)
    support    = min(lows)
    return support, resistance

# ── TIMEFRAME SKORU ────────────────────────────────────────────────────

def score_tf(candles):
    """Her timeframe için yön skoru hesapla. Pozitif=LONG, Negatif=SHORT"""
    if not candles or len(candles) < 50:
        return 0
    closes = [c["close"] for c in candles]
    score  = 0

    # RSI
    rsi = calc_rsi(closes)
    if   rsi < 30: score += 3
    elif rsi < 40: score += 2
    elif rsi < 45: score += 1
    elif rsi > 70: score -= 3
    elif rsi > 60: score -= 2
    elif rsi > 55: score -= 1

    # EMA trend
    ema9  = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    price = closes[-1]
    if   ema9 > ema21 > ema50: score += 3
    elif ema9 < ema21 < ema50: score -= 3
    elif ema9 > ema21: score += 1
    elif ema9 < ema21: score -= 1
    if price > ema50: score += 1
    else:              score -= 1

    # MACD
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd  = ema12 - ema26
    if macd > 0: score += 2
    else:         score -= 2

    # Momentum (son 3 mum)
    if closes[-1] > closes[-4]: score += 1
    else:                        score -= 1

    # ADX — trend gücü (zayıf trendde sinyal verme)
    adx = calc_adx(candles)
    if adx < 20:
        score = score // 2  # Zayıf trend, skoru yarıya indir

    return score

# ── SINYAL ÜRETIMI ─────────────────────────────────────────────────────

TIMEFRAMES = [
    ("1m",  "1m",  100),
    ("5m",  "5m",  100),
    ("15m", "15m", 100),
]

# Ağırlıklar
TF_WEIGHTS = {
    "1m":  1.0,
    "5m":  2.0,
    "15m": 3.0,
}

def analyze():
    """Multi-timeframe analiz. Sinyal varsa dict döner, yoksa None."""
    scores = {}
    all_candles = {}

    for tf_key, tf_api, limit in TIMEFRAMES:
        candles = get_candles(tf_api, limit)
        if not candles:
            print(f"  [{tf_key}] veri alınamadı")
            continue
        s = score_tf(candles)
        scores[tf_key] = s
        all_candles[tf_key] = candles
        print(f"  [{tf_key}] skor: {s:+d}")

    if len(scores) < 3:
        print("Yeterli timeframe verisi yok")
        return None

    # Ağırlıklı toplam skor
    total_weight = sum(TF_WEIGHTS[k] for k in scores)
    weighted_sum = sum(scores[k] * TF_WEIGHTS[k] for k in scores)
    weighted_avg = weighted_sum / total_weight if total_weight else 0

    print(f"  Ağırlıklı skor: {weighted_avg:.2f}")

    # Eşik: uyarı ve sinyal
    ALERT_THRESHOLD = 3.0   # "Hazır ol" bildirimi
    THRESHOLD       = 3.5   # Gerçek sinyal

    direction = None
    if weighted_avg >= THRESHOLD:
        direction = "LONG"
    elif weighted_avg <= -THRESHOLD:
        direction = "SHORT"
    elif abs(weighted_avg) >= ALERT_THRESHOLD:
        # Hazır ol uyarısı — sinyal eşiğine yakın
        alert_dir = "LONG" if weighted_avg > 0 else "SHORT"

        # Cooldown kontrolü — aynı yönde 15 dk içinde tekrar bildirim gönderme
        cache_key = f"bot3_alert_{alert_dir}"
        last_alert = get_cache(cache_key)
        now_ts = int(__import__('time').time())
        if last_alert and (now_ts - int(last_alert)) < 900:  # 15 dakika
            print(f"  HAZIR OL cooldown aktif ({alert_dir}), atlandı")
            return None

        set_cache(cache_key, str(now_ts))

        price = get_price()
        tf_str = " | ".join([f"{k}:{v:+d}" for k,v in scores.items()])
        msg = f"""⚠️ *XAU HAZIR OL — {alert_dir}*
━━━━━━━━━━━━━━━━━━
📊 Skor: `{weighted_avg:+.2f}` (eşik: ±{THRESHOLD})
💰 Fiyat: `{price:.2f}`
📐 TF: {tf_str}
🔔 Sinyal eşiğine yakın, izle!
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
        send_telegram(msg)
        print(f"  HAZIR OL bildirimi gönderildi: {alert_dir} skor:{weighted_avg:.2f}")
        return None
    else:
        return None

    # ATR bazlı SL/TP hesapla (5m mum ATR)
    candles_5m = all_candles.get("5m") or all_candles.get("15m")
    if not candles_5m:
        return None

    price = get_price()
    if not price:
        return None

    atr = calc_atr(candles_5m, 14)
    if not atr:
        return None

    # Destek/Direnç
    support, resistance = find_support_resistance(candles_5m, 20)

    # SL: ATR * 1.0 ama max %0.5 (yüksek kaldıraç koruması)
    sl_distance = min(atr * 1.0, price * 0.005)
    sl_distance = max(sl_distance, price * 0.001)  # min %0.1

    # TP: SL * 3 (minimum 1:3 RR)
    tp_distance = sl_distance * 3.2  # biraz fazla, 1:3 garantili

    if direction == "LONG":
        sl = price - sl_distance
        tp = price + tp_distance
        # Direnç altındaysa TP'yi oraya çek
        if resistance < tp and resistance > price * 1.001:
            # RR kontrolü
            new_tp_dist = resistance - price
            if new_tp_dist >= sl_distance * 3:
                tp = resistance * 0.999
    else:
        sl = price + sl_distance
        tp = price - tp_distance
        # Destek üzerindeyse TP'yi oraya çek
        if support > tp and support < price * 0.999:
            new_tp_dist = price - support
            if new_tp_dist >= sl_distance * 3:
                tp = support * 1.001

    # Son RR kontrolü
    actual_rr = tp_distance / sl_distance
    if actual_rr < 3.0:
        print(f"  RR {actual_rr:.2f} < 3.0, sinyal atlandı")
        return None

    sl_pct = (sl_distance / price) * 100
    tp_pct = (tp_distance / price) * 100

    return {
        "direction":   direction,
        "entry_price": round(price, 4),
        "sl":          round(sl, 4),
        "tp":          round(tp, 4),
        "sl_pct":      round(sl_pct, 3),
        "tp_pct":      round(tp_pct, 3),
        "rr":          round(actual_rr, 2),
        "score":       round(weighted_avg, 2),
        "tf_scores":   scores,
    }

# ── BASE44 API ─────────────────────────────────────────────────────────

HEADERS = lambda: {
    "Authorization": f"Bearer {BASE44_TOKEN}",
    "Content-Type":  "application/json"
}
BASE_URL = f"https://api.base44.com/api/apps/{APP_ID}/entities"

def get_open_trade():
    """XAU için açık işlem var mı?"""
    try:
        r = requests.get(
            f"{BASE_URL}/ActiveTrade",
            headers=HEADERS(),
            params={"status": "OPEN", "symbol": SYMBOL},
            timeout=10
        )
        if r.status_code == 200:
            trades = r.json()
            for t in trades:
                if t.get("symbol") == SYMBOL and t.get("status") == "OPEN":
                    return t
    except Exception as e:
        print(f"DB GET error: {e}")
    return None

def create_trade(signal):
    payload = {
        "symbol":       SYMBOL,
        "direction":    signal["direction"],
        "entry_price":  signal["entry_price"],
        "tp":           signal["tp"],
        "sl":           signal["sl"],
        "original_sl":  signal["sl"],
        "rr":           signal["rr"],
        "score":        signal["score"],
        "status":       "OPEN",
        "sl_moved_breakeven": False,
        "sl_moved_profit":    False,
        "tp_extended":        False,
        "open_time":    datetime.now(timezone.utc).isoformat(),
        "notes":        f"Bot3 XAU Scalper | SL%:{signal['sl_pct']} TP%:{signal['tp_pct']}"
    }
    try:
        r = requests.post(
            f"{BASE_URL}/ActiveTrade",
            headers=HEADERS(),
            json=payload,
            timeout=10
        )
        if r.status_code in (200, 201):
            return r.json().get("id")
    except Exception as e:
        print(f"DB CREATE error: {e}")
    return None

def close_trade(trade_id, result, result_pct):
    payload = {
        "status":     result,
        "close_time": datetime.now(timezone.utc).isoformat(),
        "result_pct": round(result_pct, 2)
    }
    try:
        requests.patch(
            f"{BASE_URL}/ActiveTrade/{trade_id}",
            headers=HEADERS(),
            json=payload,
            timeout=10
        )
    except Exception as e:
        print(f"DB PATCH error: {e}")

# ── TELEGRAM ───────────────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        print("Telegram token yok!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    CHAT_ID,
            "text":       msg,
            "parse_mode": "Markdown"
        }, timeout=10)
        if r.status_code != 200:
            print(f"Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"Telegram exception: {e}")

# ── WATCHDOG: Açık işlemi izle ─────────────────────────────────────────

def watch_trade(trade):
    trade_id  = trade["id"]
    direction = trade["direction"]
    entry     = float(trade["entry_price"])
    sl        = float(trade["sl"])
    tp        = float(trade["tp"])
    orig_sl   = float(trade.get("original_sl", sl))

    price = get_price()
    if not price:
        return

    sl_pct = abs(entry - orig_sl) / entry * 100
    tp_pct = abs(entry - tp) / entry * 100
    rr     = trade.get("rr", 3.0)

    # TP hit?
    tp_hit = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)
    # SL hit?
    sl_hit = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)

    if tp_hit:
        if direction == "LONG":
            result_pct = (price - entry) / entry * 100
        else:
            result_pct = (entry - price) / entry * 100

        # Breakeven mi kar mı zarar mı?
        if result_pct > 0.05:
            outcome_label = "✅ KAR"
            outcome_emoji = "🟢"
        elif result_pct >= -0.05:
            outcome_label = "〽️ BREAKEVEN"
            outcome_emoji = "🟡"
        else:
            outcome_label = "❌ ZARAR"
            outcome_emoji = "🔴"

        close_trade(trade_id, "TP_HIT", result_pct)
        msg = f"""🎯 *XAU TP ULAŞTI* {outcome_emoji}
━━━━━━━━━━━━━━━━━━
{outcome_label}
📍 Fiyat: `{price:.2f}`
💰 Giriş: `{entry:.2f}`
🎯 TP: `{tp:.2f}` (+{tp_pct:.2f}%)
🔒 SL: `{orig_sl:.2f}` (-{sl_pct:.2f}%)
📊 Sonuç: `{result_pct:+.2f}%`
⚖️ RR: 1:{rr}
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
        send_telegram(msg)
        print(f"TP HIT: {direction} {result_pct:+.2f}%")

    elif sl_hit:
        if direction == "LONG":
            result_pct = (price - entry) / entry * 100
        else:
            result_pct = (entry - price) / entry * 100

        if abs(result_pct) < 0.05:
            outcome_label = "〽️ BREAKEVEN"
            outcome_emoji = "🟡"
            result_label  = "BREAKEVEN"
        else:
            outcome_label = "❌ ZARAR"
            outcome_emoji = "🔴"
            result_label  = "SL_HIT"

        close_trade(trade_id, result_label, result_pct)
        msg = f"""🛑 *XAU SL ULAŞTI* {outcome_emoji}
━━━━━━━━━━━━━━━━━━
{outcome_label}
📍 Fiyat: `{price:.2f}`
💰 Giriş: `{entry:.2f}`
🎯 TP: `{tp:.2f}` (+{tp_pct:.2f}%)
🔒 SL: `{sl:.2f}` (-{sl_pct:.2f}%)
📊 Sonuç: `{result_pct:+.2f}%`
⚖️ RR: 1:{rr}
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
        send_telegram(msg)
        print(f"SL HIT: {direction} {result_pct:+.2f}%")

    else:
        # Sessiz — sadece log
        if direction == "LONG":
            current_pnl = (price - entry) / entry * 100
        else:
            current_pnl = (entry - price) / entry * 100
        print(f"  İşlem devam: {direction} | Fiyat:{price:.2f} | PnL:{current_pnl:+.2f}% | TP:{tp:.2f} | SL:{sl:.2f}")

# ── ANA DÖNGÜ ──────────────────────────────────────────────────────────

def main():
    mode = os.environ.get("BOT3_MODE", "both")  # "scan", "watch", "both", "test"
    
    # TEST MODU — sadece Telegram bağlantısını kontrol et
    if mode == "test":
        print("TEST MODU — Telegram bağlantı testi")
        price = get_price()
        msg = f"""🧪 *Bot 3 XAU Scalper — TEST*
━━━━━━━━━━━━━━━━━━
✅ GitHub Actions çalışıyor
✅ Telegram bağlantısı OK
💰 XAU Fiyat: `{price:.2f}`
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
        send_telegram(msg)
        print("Test mesajı gönderildi!")
        return
    print(f"\n{'='*50}")
    print(f"🏅 Bot 3 — XAU Scalper | {datetime.now().strftime('%H:%M:%S')} | Mode: {mode}")
    print(f"{'='*50}")

    # Açık işlem var mı?
    open_trade = get_open_trade()

    # WATCHDOG
    if open_trade and mode in ("watch", "both"):
        print(f"\n👁️ Açık işlem izleniyor: {open_trade['direction']} @ {open_trade['entry_price']}")
        watch_trade(open_trade)

    # SCAN — sadece açık işlem yoksa
    if not open_trade and mode in ("scan", "both"):
        print(f"\n🔍 XAU taranıyor...")
        signal = analyze()

        if signal:
            print(f"\n✅ SİNYAL: {signal['direction']} | SL:{signal['sl_pct']}% | TP:{signal['tp_pct']}% | RR:1:{signal['rr']}")

            trade_id = create_trade(signal)
            if trade_id:
                dir_emoji = "📈" if signal["direction"] == "LONG" else "📉"
                tf_scores_str = " | ".join([f"{k}:{v:+d}" for k, v in signal["tf_scores"].items()])
                msg = f"""{dir_emoji} *XAU {signal['direction']} SİNYALİ*
━━━━━━━━━━━━━━━━━━
💰 Giriş: `{signal['entry_price']:.2f}`
🎯 TP: `{signal['tp']:.2f}` (+{signal['tp_pct']}%)
🔒 SL: `{signal['sl']:.2f}` (-{signal['sl_pct']}%)
⚖️ RR: 1:{signal['rr']}
📊 Skor: {signal['score']:+.1f}
📐 TF: {tf_scores_str}
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
                send_telegram(msg)
                print(f"İşlem açıldı ve Telegram'a bildirildi.")
            else:
                print("DB'ye kaydedilemedi!")
        else:
            print("Sinyal bulunamadı.")
    elif open_trade:
        print(f"\nAktif işlem var — tarama atlandı.")

    print(f"\n{'='*50}\n")

if __name__ == "__main__":
    main()
