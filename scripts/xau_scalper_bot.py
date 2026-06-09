#!/usr/bin/env python3
"""
Bot 3 — XAU/USDT Scalper Bot v2
- Multi-timeframe analiz: 1m, 5m, 15m, 30m (hafif), 1H (hafif)
- Hacim konfirmasyonu (fake sinyal filtresi)
- Mum pattern kontrolü (engulfing, pin bar, marubozu)
- ATR dinamik eşik (volatiliteye uyarlanır)
- Self-learning: kapanan işlemleri analiz et, parametreleri otomatik güncelle
- Minimum 1:3 RR oranı | 100x-500x kaldıraç
"""

import os, requests, time, json, math
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_7", "")
CHAT_ID        = "2055780815"
BASE44_TOKEN   = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJjbGllbnRfaWQiOiJiOWJmNGFmZC1iMmIxLTQxMDYtYWU2OS04ZWYwYTFlNzQxMDQiLCJhcHBfaWQiOiI2YTFkOTczNTY4YWY5Yjk4NGUwZjFjYzgiLCJhdWQiOiJiYXNlNDRfYXBpIiwic2NvcGUiOiJhcHAuYWNjZXNzIiwiZXhwIjoxNzgxMDA1ODg0LCJpYXQiOjE3ODEwMDIyODR9.4td6NQ1J5hPgfYjbGcmXp7v3WqOQScZgnJAZYlmSwgs"
APP_ID         = "6a1d973568af9b984e0f1cc8"
SYMBOL         = "XAUUSDT"
BITGET_BASE    = "https://api.bitget.com"

def refresh_token():
    global BASE44_TOKEN
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "-c",
             "import os,requests; r=requests.post('https://api.base44.com/api/auth/service-token',"
             "headers={'Authorization':'Bearer '+os.environ.get('BASE44_SERVICE_TOKEN','')},timeout=8);"
             "print(r.json().get('token','')) if r.status_code==200 else print('')"],
            capture_output=True, text=True, timeout=15
        )
        new_tok = result.stdout.strip()
        if new_tok and len(new_tok) > 20:
            BASE44_TOKEN = new_tok
    except:
        pass

# ── HEADERS & BASE_URL ─────────────────────────────────────────────────
HEADERS  = lambda: {"Authorization": f"Bearer {BASE44_TOKEN}", "Content-Type": "application/json"}
BASE_URL = f"https://api.base44.com/api/apps/{APP_ID}/entities"

# ── SELF-LEARNING PARAMETRELERİ (BotCache'den yüklenir) ───────────────
DEFAULT_PARAMS = {
    "threshold":       3.5,   # sinyal eşiği
    "alert_threshold": 3.0,   # hazır ol eşiği
    "sl_atr_mult":     1.0,   # SL = ATR * mult
    "min_volume_mult": 1.2,   # hacim min = ort * mult
    "rr_min":          3.0,   # minimum RR
    "wins": 0, "losses": 0, "total_pct": 0.0,
    "version": 1
}

def load_params():
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": "bot3_params"}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == "bot3_params":
                    p = json.loads(item["value"])
                    # Eksik anahtarları default ile doldur
                    for k, v in DEFAULT_PARAMS.items():
                        if k not in p:
                            p[k] = v
                    return p
    except:
        pass
    return DEFAULT_PARAMS.copy()

def save_params(p):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": "bot3_params"}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == "bot3_params":
                    existing = item
                    break
        val = json.dumps(p)
        if existing:
            requests.patch(f"{BASE_URL}/BotCache/{existing['id']}",
                           headers=HEADERS(), json={"value": val}, timeout=8)
        else:
            requests.post(f"{BASE_URL}/BotCache", headers=HEADERS(),
                          json={"key": "bot3_params", "value": val}, timeout=8)
    except Exception as e:
        print(f"  Params save error: {e}")

# ── BOT CACHE (cooldown vs.) ───────────────────────────────────────────
def get_cache(key):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": key}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    return item.get("value")
    except:
        pass
    return None

def set_cache(key, value):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": key}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    existing = item
                    break
        if existing:
            requests.patch(f"{BASE_URL}/BotCache/{existing['id']}",
                           headers=HEADERS(), json={"value": value}, timeout=8)
        else:
            requests.post(f"{BASE_URL}/BotCache", headers=HEADERS(),
                          json={"key": key, "value": value}, timeout=8)
    except Exception as e:
        print(f"Cache set error: {e}")

# ── BITGET API ─────────────────────────────────────────────────────────
def get_candles(interval_str, limit=100):
    url = f"{BITGET_BASE}/api/v2/mix/market/candles"
    params = {"symbol": SYMBOL, "productType": "USDT-FUTURES",
               "granularity": interval_str, "limit": str(limit)}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("code") != "00000":
            return []
        return [{"open": float(c[1]), "high": float(c[2]),
                 "low": float(c[3]), "close": float(c[4]),
                 "volume": float(c[5])} for c in data["data"]]
    except Exception as e:
        print(f"Candle error ({interval_str}): {e}")
        return []

def get_price():
    try:
        r = requests.get(f"{BITGET_BASE}/api/v2/mix/market/ticker",
                         params={"symbol": SYMBOL, "productType": "USDT-FUTURES"}, timeout=8)
        d = r.json()
        if d.get("code") == "00000":
            return float(d["data"][0]["lastPr"])
    except:
        pass
    return None

# ── TEKNİK İNDİKATÖRLER ───────────────────────────────────────────────
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
    return 100 - (100 / (1 + avg_g / avg_l))

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_adx(candles, period=14):
    if len(candles) < period * 2:
        return 20
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i-1]["high"], candles[i-1]["low"], candles[i-1]["close"]
        plus_dms.append(max(h - ph, 0) if (h - ph) > (pl - l) else 0)
        minus_dms.append(max(pl - l, 0) if (pl - l) > (h - ph) else 0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    def smooth(arr, p):
        s = sum(arr[:p]); res = [s]
        for v in arr[p:]: s = s - s/p + v; res.append(s)
        return res
    st = smooth(trs, period)
    sp = smooth(plus_dms, period)
    sm = smooth(minus_dms, period)
    dx_list = []
    for i in range(len(st)):
        if st[i] == 0: continue
        dip = 100 * sp[i] / st[i]
        dim = 100 * sm[i] / st[i]
        d = dip + dim
        if d == 0: continue
        dx_list.append(100 * abs(dip - dim) / d)
    if not dx_list: return 20
    return sum(dx_list[-period:]) / min(len(dx_list), period)

# ── HAİCM KONFİRMASYONU ───────────────────────────────────────────────

def get_order_book_signal(symbol, price, product_type="USDT-FUTURES", range_pct=0.003):
    """XAU için daha dar aralık: ±0.3%"""
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
        if ask_liq: wall_note.append(f"Satış@{ask_liq[0]:.2f}")
        if bid_liq: wall_note.append(f"Alış@{bid_liq[0]:.2f}")
        wall_note = " | ".join(wall_note) if wall_note else "Duvar yok"
        ob_score = 1 if bid_ratio >= 0.60 else (-1 if bid_ratio <= 0.40 else 0)
        return ob_score, round(bid_wall,2), round(ask_wall,2), wall_note
    except Exception as e:
        return 0, 0, 0, f"OB err:{e}"


def get_tp_from_liquidity(symbol, price, direction, product_type="USDT-FUTURES"):
    """Büyük emir duvarını TP hedefi olarak kullan"""
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

def check_volume(candles, mult=1.2):
    """Son mumun hacmi, son 20 mumun ortalamasının mult katı mı?"""
    if len(candles) < 20:
        return True  # yeterli veri yoksa geç
    vols = [c["volume"] for c in candles[-21:-1]]  # son 20 mum (son hariç)
    avg_vol = sum(vols) / len(vols)
    last_vol = candles[-1]["volume"]
    ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
    return ratio >= mult, ratio

# ── MUM PATTERN ───────────────────────────────────────────────────────
def check_candle_pattern(candles, direction):
    """
    Son 2 mumda güçlü pattern var mı?
    LONG için: bullish engulfing, bullish pin bar, marubozu
    SHORT için: bearish engulfing, bearish pin bar, bearish marubozu
    Puan: 0 (pattern yok) veya 1-3 (güçlü pattern)
    """
    if len(candles) < 3:
        return 0, "yok"

    c1 = candles[-2]   # önceki mum
    c2 = candles[-1]   # son mum
    score = 0
    patterns = []

    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    range1 = c1["high"] - c1["low"] if c1["high"] != c1["low"] else 0.001
    range2 = c2["high"] - c2["low"] if c2["high"] != c2["low"] else 0.001

    if direction == "LONG":
        # Bullish engulfing: önceki kırmızı, son mum öncekini tamamen yutmuş yeşil
        if (c1["close"] < c1["open"] and            # önceki kırmızı
            c2["close"] > c2["open"] and            # son yeşil
            c2["open"] <= c1["close"] and
            c2["close"] >= c1["open"]):
            score += 3
            patterns.append("engulfing")

        # Bullish pin bar: uzun alt gölge (en az %60 range), küçük body
        lower_shadow = min(c2["open"], c2["close"]) - c2["low"]
        if lower_shadow / range2 >= 0.6 and body2 / range2 <= 0.3:
            score += 2
            patterns.append("pin_bar")

        # Bullish marubozu: güçlü yeşil mum, gölge yok
        if (c2["close"] > c2["open"] and
            body2 / range2 >= 0.85):
            score += 2
            patterns.append("marubozu")

    else:  # SHORT
        # Bearish engulfing
        if (c1["close"] > c1["open"] and
            c2["close"] < c2["open"] and
            c2["open"] >= c1["close"] and
            c2["close"] <= c1["open"]):
            score += 3
            patterns.append("engulfing")

        # Bearish pin bar: uzun üst gölge
        upper_shadow = c2["high"] - max(c2["open"], c2["close"])
        if upper_shadow / range2 >= 0.6 and body2 / range2 <= 0.3:
            score += 2
            patterns.append("pin_bar")

        # Bearish marubozu
        if (c2["close"] < c2["open"] and
            body2 / range2 >= 0.85):
            score += 2
            patterns.append("marubozu")

    return score, ", ".join(patterns) if patterns else "yok"

# ── ATR DİNAMİK EŞİK ──────────────────────────────────────────────────
def get_dynamic_threshold(candles_15m, base_threshold=3.5):
    """
    ATR yüksekse (volatil piyasa) eşiği düşür — daha kolay sinyal.
    ATR düşükse (sıkışık piyasa) eşiği yükselt — sahte sinyali engelle.
    """
    atr = calc_atr(candles_15m, 14)
    price = candles_15m[-1]["close"] if candles_15m else 3000
    if not atr or price == 0:
        return base_threshold

    atr_pct = (atr / price) * 100  # ATR yüzdesi

    # XAU için normal ATR %: ~0.1-0.3
    if atr_pct > 0.3:    # Çok volatil — daha kolay sinyal
        adj = base_threshold - 0.5
    elif atr_pct > 0.2:  # Normal-yüksek
        adj = base_threshold - 0.2
    elif atr_pct < 0.08: # Sıkışık — zor sinyal
        adj = base_threshold + 0.5
    elif atr_pct < 0.12: # Normal-düşük
        adj = base_threshold + 0.2
    else:
        adj = base_threshold

    adj = max(2.5, min(5.0, adj))  # 2.5-5.0 arasında tut
    print(f"  ATR%: {atr_pct:.3f} | Dinamik eşik: {adj:.1f}")
    return adj

# ── TIMEFRAME SKORU ────────────────────────────────────────────────────
def score_tf(candles):
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

    # EMA
    ema9  = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    price = closes[-1]
    if   ema9 > ema21 > ema50: score += 3
    elif ema9 < ema21 < ema50: score -= 3
    elif ema9 > ema21:         score += 1
    elif ema9 < ema21:         score -= 1
    if price > ema50: score += 1
    else:             score -= 1

    # MACD
    macd = calc_ema(closes, 12) - calc_ema(closes, 26)
    if macd > 0: score += 2
    else:        score -= 2

    # Momentum
    if closes[-1] > closes[-4]: score += 1
    else:                        score -= 1

    # ADX
    adx = calc_adx(candles)
    if adx < 20:
        score = score // 2

    return score

TIMEFRAMES = [
    ("1m",  "1m",  100),
    ("5m",  "5m",  100),
    ("15m", "15m", 100),
    ("30m", "30m", 80),
    ("1H",  "1H",  80),
]
# 1m×1 + 5m×2 + 15m×3 + 30m×0.5 + 1H×0.5 = toplam 7
TF_WEIGHTS = {"1m": 1.0, "5m": 2.0, "15m": 3.0, "30m": 0.5, "1H": 0.5}

# ── ANALİZ ────────────────────────────────────────────────────────────
def analyze(params):
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

    total_weight = sum(TF_WEIGHTS[k] for k in scores)
    weighted_sum = sum(scores[k] * TF_WEIGHTS[k] for k in scores)
    weighted_avg = weighted_sum / total_weight if total_weight else 0
    print(f"  Ağırlıklı skor: {weighted_avg:.2f}")

    # ATR dinamik eşik
    candles_15m = all_candles.get("15m", [])
    base_threshold   = params.get("threshold", 3.5)
    base_alert       = params.get("alert_threshold", 3.0)
    dyn_threshold    = get_dynamic_threshold(candles_15m, base_threshold)
    dyn_alert        = dyn_threshold - 0.5

    direction = None
    if weighted_avg >= dyn_threshold:
        direction = "LONG"
    elif weighted_avg <= -dyn_threshold:
        direction = "SHORT"
    elif abs(weighted_avg) >= dyn_alert:
        alert_dir = "LONG" if weighted_avg > 0 else "SHORT"
        cache_key = f"bot3_alert_{alert_dir}"
        cache_key_active = f"bot3_alert_active_{alert_dir}"
        now_ts = int(time.time())

        # Ters yönde aktif alert varsa sıfırla
        opposite = "SHORT" if alert_dir == "LONG" else "LONG"
        set_cache(f"bot3_alert_active_{opposite}", "0")

        # Şu an alert bölgesinde miyiz?
        is_active = get_cache(cache_key_active)
        if is_active and is_active == "1":
            print(f"  HAZIR OL zaten gönderildi ({alert_dir}), skor bölgeden çıkmadan tekrar atılmaz")
            return None

        # İlk kez bu bölgeye giriş → gönder ve flag'i set et
        set_cache(cache_key_active, "1")
        set_cache(cache_key, str(now_ts))
        price = get_price()
        tf_str = " | ".join([f"{k}:{v:+d}" for k, v in scores.items()])
        msg = f"""⚠️ *XAU HAZIR OL — {alert_dir}*
━━━━━━━━━━━━━━━━━━
📊 Skor: `{weighted_avg:+.2f}` (eşik: ±{dyn_threshold:.1f})
💰 Fiyat: `{price:.2f}`
📐 TF: {tf_str}
🔔 Sinyal eşiğine yakın, izle!
━━━━━━━━━━━━━━━━━━
📚 OB: Alış `{bid_wall:.1f}` / Satış `{ask_wall:.1f}` | {wall_note}
🎯 Liq TP: `{liq_tp:.2f}`
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*"""
        send_telegram(msg)
        print(f"  HAZIR OL: {alert_dir} skor:{weighted_avg:.2f}")
        return None
    else:
        # Skor HAZIR OL bölgesinin altına düştü → flag sıfırla (tekrar girinc bildirim gelsin)
        set_cache("bot3_alert_active_LONG", "0")
        set_cache("bot3_alert_active_SHORT", "0")
        return None

    # ── HACIM KONFİRMASYONU ──
    candles_5m = all_candles.get("5m", [])
    vol_ok, vol_ratio = check_volume(candles_5m, params.get("min_volume_mult", 1.2))
    print(f"  Hacim oranı: {vol_ratio:.2f}x (min:{params.get('min_volume_mult',1.2):.1f}x) {'✅' if vol_ok else '❌'}")
    if not vol_ok:
        print(f"  Hacim yetersiz — sinyal atlandı")
        return None

    # ── ORDER BOOK filtresi (scalp için kritik) ──
    price_now = get_price()
    ob_score, bid_wall, ask_wall, wall_note = get_order_book_signal(SYMBOL, price_now or 3000)
    print(f"  OB: bid={bid_wall} ask={ask_wall} skor={ob_score} | {wall_note}")
    if (direction == "LONG" and ob_score == -1) or (direction == "SHORT" and ob_score == 1):
        print(f"  OB karşı duvar çok güçlü — sinyal engellendi")
        return None

    # ── LİKİDASYON TP OPTİMİZASYONU ──
    liq_tp = get_tp_from_liquidity(SYMBOL, price_now or 3000, direction)
    if liq_tp:
        print(f"  Likidasyon TP hedefi: {liq_tp:.2f}")

    # ── MUM PATTERN ──
    pat_score, pat_name = check_candle_pattern(candles_5m, direction)
    print(f"  Mum pattern: {pat_name} (skor:{pat_score})")
    # Pattern yoksa (0) sinyal yine de gidebilir ama log'a yaz
    # Pattern 2+ ise skoru artır (bonus)

    # ── ATR bazlı SL/TP ──
    atr = calc_atr(candles_5m, 14)
    price = get_price()
    if not atr or not price:
        return None

    sl_mult     = params.get("sl_atr_mult", 1.0)
    sl_distance = min(atr * sl_mult, price * 0.005)
    sl_distance = max(sl_distance, price * 0.001)
    tp_distance = sl_distance * 3.2

    if direction == "LONG":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    actual_rr = tp_distance / sl_distance
    if actual_rr < params.get("rr_min", 3.0):
        print(f"  RR {actual_rr:.2f} < {params.get('rr_min',3.0)}, atlandı")
        return None

    return {
        "direction":   direction,
        "entry_price": round(price, 4),
        "sl":          round(sl, 4),
        "tp":          round(tp, 4),
        "sl_pct":      round((sl_distance / price) * 100, 3),
        "tp_pct":      round((tp_distance / price) * 100, 3),
        "rr":          round(actual_rr, 2),
        "score":       round(weighted_avg, 2),
        "tf_scores":   scores,
        "pattern":     pat_name,
        "pat_score":   pat_score,
        "vol_ratio":   round(vol_ratio, 2),
        "dyn_threshold": round(dyn_threshold, 2),
    }

# ── SELF-LEARNING ─────────────────────────────────────────────────────
def self_learn(params):
    """
    Kapanan işlemleri analiz et, parametreleri otomatik güncelle.
    Sessizce çalışır — bildirim yok.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/ActiveTrade",
            headers=HEADERS(),
            params={"symbol": SYMBOL},
            timeout=10
        )
        if r.status_code != 200:
            return params

        all_trades = [t for t in r.json()
                      if t.get("symbol") == SYMBOL and t.get("status") != "OPEN"]

        # Son 5 işlemi analiz et (daha eski geçmiş gürültü olur)
        recent = sorted(all_trades, key=lambda x: x.get("close_time", ""), reverse=True)[:5]

        if len(recent) < 3:
            print(f"  Self-learn: yeterli geçmiş yok ({len(recent)}/3)")
            return params

        wins   = [t for t in recent if t.get("result_pct", 0) > 0]
        losses = [t for t in recent if t.get("result_pct", 0) <= 0]
        win_rate = len(wins) / len(recent)

        total_pct = sum(t.get("result_pct", 0) for t in recent)
        avg_pct   = total_pct / len(recent)

        print(f"  Self-learn: {len(recent)} işlem | WR:{win_rate:.0%} | Ort:{avg_pct:+.2f}%")

        new_params = params.copy()
        changed    = []

        # Win rate düşükse → daha seçici ol (eşiği artır, hacim filtresini sıkılaştır)
        if win_rate < 0.4 and len(recent) >= 4:
            if new_params["threshold"] < 4.5:
                new_params["threshold"] = round(new_params["threshold"] + 0.2, 1)
                changed.append(f"eşik ↑{new_params['threshold']}")
            if new_params["min_volume_mult"] < 2.0:
                new_params["min_volume_mult"] = round(new_params["min_volume_mult"] + 0.1, 1)
                changed.append(f"hacim ↑{new_params['min_volume_mult']}")

        # Win rate yüksekse → biraz daha agresif ol (eşiği azalt)
        elif win_rate >= 0.7 and len(recent) >= 4:
            if new_params["threshold"] > 2.8:
                new_params["threshold"] = round(new_params["threshold"] - 0.1, 1)
                changed.append(f"eşik ↓{new_params['threshold']}")

        # Zarar büyükse → SL'yi sıkılaştır
        avg_loss = sum(t.get("result_pct", 0) for t in losses) / len(losses) if losses else 0
        if avg_loss < -0.3 and new_params["sl_atr_mult"] > 0.7:
            new_params["sl_atr_mult"] = round(new_params["sl_atr_mult"] - 0.1, 1)
            changed.append(f"sl_mult ↓{new_params['sl_atr_mult']}")

        # İstatistikleri güncelle
        new_params["wins"]      = params.get("wins", 0) + len(wins)
        new_params["losses"]    = params.get("losses", 0) + len(losses)
        new_params["total_pct"] = round(params.get("total_pct", 0) + total_pct, 2)
        new_params["version"]   = params.get("version", 1) + (1 if changed else 0)
        new_params["alert_threshold"] = round(new_params["threshold"] - 0.5, 1)

        if changed:
            print(f"  Self-learn güncelleme: {', '.join(changed)}")
        else:
            print(f"  Self-learn: parametre değişikliği yok")

        save_params(new_params)
        return new_params

    except Exception as e:
        print(f"  Self-learn hata: {e}")
        return params

# ── BASE44 CRUD ────────────────────────────────────────────────────────
def get_open_trade():
    try:
        r = requests.get(f"{BASE_URL}/ActiveTrade", headers=HEADERS(),
                         params={"status": "OPEN", "symbol": SYMBOL}, timeout=10)
        if r.status_code == 200:
            for t in r.json():
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
        "notes": f"Bot3 v2 | Pattern:{signal.get('pattern','yok')} | Vol:{signal.get('vol_ratio','?')}x | DynEşik:{signal.get('dyn_threshold','?')}"
    }
    try:
        r = requests.post(f"{BASE_URL}/ActiveTrade", headers=HEADERS(),
                          json=payload, timeout=10)
        if r.status_code in (200, 201):
            return r.json().get("id")
    except Exception as e:
        print(f"DB CREATE error: {e}")
    return None

def close_trade(trade_id, result, result_pct):
    try:
        requests.patch(f"{BASE_URL}/ActiveTrade/{trade_id}", headers=HEADERS(),
                       json={"status": result, "close_time": datetime.now(timezone.utc).isoformat(),
                             "result_pct": round(result_pct, 2)}, timeout=10)
    except Exception as e:
        print(f"DB PATCH error: {e}")

# ── TELEGRAM ──────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if r.status_code != 200:
            print(f"Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"Telegram exception: {e}")

# ── WATCHDOG ──────────────────────────────────────────────────────────
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

    sl_pct  = abs(entry - orig_sl) / entry * 100
    tp_pct  = abs(entry - tp) / entry * 100
    rr      = trade.get("rr", 3.0)
    tp_hit  = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)
    sl_hit  = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)

    if tp_hit:
        result_pct = (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100
        if result_pct > 0.05:
            label, emoji = "✅ KAR", "🟢"
        elif result_pct >= -0.05:
            label, emoji = "〽️ BREAKEVEN", "🟡"
        else:
            label, emoji = "❌ ZARAR", "🔴"
        close_trade(trade_id, "TP_HIT", result_pct)
        send_telegram(f"""🎯 *XAU TP ULAŞTI* {emoji}
━━━━━━━━━━━━━━━━━━
{label}
📍 Fiyat: `{price:.2f}` | 💰 Giriş: `{entry:.2f}`
🎯 TP: `{tp:.2f}` (+{tp_pct:.2f}%) | 🔒 SL: `{orig_sl:.2f}` (-{sl_pct:.2f}%)
📊 Sonuç: `{result_pct:+.2f}%` | ⚖️ RR: 1:{rr}
━━━━━━━━━━━━━━━━━━
📚 OB: Alış `{bid_wall:.1f}` / Satış `{ask_wall:.1f}` | {wall_note}
🎯 Liq TP: `{liq_tp:.2f}`
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*""")
        print(f"TP HIT: {direction} {result_pct:+.2f}%")

    elif sl_hit:
        result_pct = (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100
        if abs(result_pct) < 0.05:
            label, emoji, res = "〽️ BREAKEVEN", "🟡", "BREAKEVEN"
        else:
            label, emoji, res = "❌ ZARAR", "🔴", "SL_HIT"
        close_trade(trade_id, res, result_pct)
        send_telegram(f"""🛑 *XAU SL ULAŞTI* {emoji}
━━━━━━━━━━━━━━━━━━
{label}
📍 Fiyat: `{price:.2f}` | 💰 Giriş: `{entry:.2f}`
🎯 TP: `{tp:.2f}` (+{tp_pct:.2f}%) | 🔒 SL: `{sl:.2f}` (-{sl_pct:.2f}%)
📊 Sonuç: `{result_pct:+.2f}%` | ⚖️ RR: 1:{rr}
━━━━━━━━━━━━━━━━━━
📚 OB: Alış `{bid_wall:.1f}` / Satış `{ask_wall:.1f}` | {wall_note}
🎯 Liq TP: `{liq_tp:.2f}`
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 — XAU Scalper*""")
        print(f"SL HIT: {direction} {result_pct:+.2f}%")

    else:
        pnl = (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100
        print(f"  İşlem devam: {direction} | Fiyat:{price:.2f} | PnL:{pnl:+.2f}%")

        # SL breakeven'a taşı (TP'nin %40'ına ulaşıldığında)
        if not trade.get("sl_moved_breakeven"):
            progress = abs(price - entry) / abs(tp - entry)
            if progress >= 0.4:
                new_sl = entry * 1.0002 if direction == "LONG" else entry * 0.9998
                try:
                    requests.patch(f"{BASE_URL}/ActiveTrade/{trade_id}",
                                   headers=HEADERS(),
                                   json={"sl": round(new_sl, 4), "sl_moved_breakeven": True},
                                   timeout=10)
                    print(f"  SL breakeven'a taşındı: {new_sl:.4f}")
                    send_telegram(f"🔒 *XAU SL Breakeven'a Taşındı*\n📍 Yeni SL: `{new_sl:.2f}` | İlerleme: %{progress*100:.0f}\n📡 *Bot 3*")
                except:
                    pass

# ── ANA DÖNGÜ ─────────────────────────────────────────────────────────
def main():
    mode = os.environ.get("BOT3_MODE", "both")

    if mode == "test":
        print("TEST MODU")
        price = get_price()
        params = load_params()
        send_telegram(f"""🧪 *Bot 3 XAU Scalper v2 — TEST*
━━━━━━━━━━━━━━━━━━
✅ GitHub Actions çalışıyor
✅ Telegram OK
💰 XAU: `{price:.2f}`
🧠 Eşik: `{params['threshold']}` | WR: {params.get('wins',0)}/{params.get('wins',0)+params.get('losses',0)}
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 v2 — XAU Scalper*""")
        print("Test mesajı gönderildi!")
        return

    refresh_token()

    # Self-learning: her çalışmada parametreleri kontrol et
    params = load_params()
    print(f"  Parametreler: eşik={params['threshold']} | hacim={params['min_volume_mult']}x | sl_mult={params['sl_atr_mult']}")

    # Her 10 dakikada bir self-learn çalıştır (BotCache timestamp)
    last_learn = get_cache("bot3_last_learn")
    now_ts = int(time.time())
    if not last_learn or (now_ts - int(last_learn)) >= 600:
        params = self_learn(params)
        set_cache("bot3_last_learn", str(now_ts))

    open_trade = get_open_trade()

    if mode in ("watch", "both") and open_trade:
        print(f"  Açık işlem izleniyor: {open_trade['direction']} | ID:{open_trade['id']}")
        watch_trade(open_trade)

    if mode in ("scan", "both") and not open_trade:
        print("🔍 XAU taranıyor...")
        signal = analyze(params)
        if signal:
            trade_id = create_trade(signal)
            pat_info = f" | Pattern: {signal['pattern']}" if signal.get("pattern") and signal["pattern"] != "yok" else ""
            msg = f"""🚀 *XAU SİNYAL — {signal['direction']}*
━━━━━━━━━━━━━━━━━━
💰 Giriş: `{signal['entry_price']:.2f}`
🎯 TP: `{signal['tp']:.2f}` (+{signal['tp_pct']:.2f}%)
🛑 SL: `{signal['sl']:.2f}` (-{signal['sl_pct']:.2f}%)
⚖️ RR: 1:{signal['rr']} | 📊 Skor: `{signal['score']:+.2f}`
📈 Hacim: `{signal['vol_ratio']:.1f}x` | 🕯 {signal['pattern']}{pat_info}
━━━━━━━━━━━━━━━━━━
📡 *Bot 3 v2 — XAU Scalper*"""
            send_telegram(msg)
            print(f"SİNYAL: {signal['direction']} @ {signal['entry_price']} | RR:{signal['rr']}")
        else:
            print("Sinyal bulunamadı.")

if __name__ == "__main__":
    main()
