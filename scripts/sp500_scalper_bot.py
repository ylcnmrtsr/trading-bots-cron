#!/usr/bin/env python3
"""
Bot 4 — E-Mini S&P 500 (ES) Scalper Bot
- Fiyat & mum verisi: Tradovate API (Demo/Live)
- Multi-timeframe analiz: 1m, 5m, 15m, 30m, 1H
- Hacim konfirmasyonu, mum pattern, ATR dinamik eşik
- Self-learning: kapanan işlemleri analiz et, parametreleri otomatik güncelle
- Bildirim sistemi: Bot 3 ile birebir aynı
- Minimum 1:3 RR | SL/TP bildirim formatı: Bot 3 ile özdeş
"""

import os, requests, time, json, math
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN_8", "")
CHAT_ID         = "2055780815"
BASE44_TOKEN    = os.environ.get("BASE44_API_KEY", "")
APP_ID          = "6a1d973568af9b984e0f1cc8"
SYMBOL          = "ES"          # E-Mini S&P 500
SYMBOL_DISPLAY  = "S&P 500"

# Tradovate credentials
TV_USERNAME = os.environ.get("TRADOVATE_USERNAME", "")
TV_PASSWORD = os.environ.get("TRADOVATE_PASSWORD", "")

# Tradovate API endpoints — önce live, fallback demo
TV_LIVE_URL  = "https://live.tradovateapi.com/v1"
TV_DEMO_URL  = "https://demo.tradovateapi.com/v1"
TV_MD_LIVE   = "https://md.tradovateapi.com/v1"
TV_MD_DEMO   = "https://md-demo.tradovateapi.com/v1"

BASE_URL = f"https://app.base44.com/api/apps/{APP_ID}/entities"
HEADERS  = lambda: {"api_key": BASE44_TOKEN, "Content-Type": "application/json"}

# ── TRADOVATE TOKEN YÖNETİMİ ──────────────────────────────────────────
_tv_token      = None
_tv_token_exp  = 0
_tv_md_token   = None
_tv_md_url     = TV_MD_LIVE
_tv_api_url    = TV_LIVE_URL

def get_tv_token():
    """Tradovate access token al (önce live, sonra demo)."""
    global _tv_token, _tv_token_exp, _tv_api_url, _tv_md_url, _tv_md_token

    now_ts = int(time.time())
    if _tv_token and now_ts < _tv_token_exp - 60:
        return _tv_token

    payload = {
        "name":       TV_USERNAME,
        "password":   TV_PASSWORD,
        "appId":      "Sample App",
        "appVersion": "1.0",
        "cid":        0,
        "sec":        ""
    }

    for api_url, md_url, label in [
        (TV_LIVE_URL, TV_MD_LIVE, "Live"),
        (TV_DEMO_URL, TV_MD_DEMO, "Demo"),
    ]:
        try:
            r = requests.post(f"{api_url}/auth/accesstokenrequest",
                              json=payload, timeout=15)
            d = r.json()
            if "accessToken" in d:
                _tv_token     = d["accessToken"]
                _tv_token_exp = now_ts + d.get("expirationTime", 4800) // 1000
                _tv_api_url   = api_url
                _tv_md_url    = md_url
                # MD token için ayrı auth
                _tv_md_token  = d.get("mdAccessToken") or d.get("accessToken")
                print(f"  Tradovate {label} auth başarılı ✅")
                return _tv_token
            else:
                print(f"  Tradovate {label} auth hata: {d.get('errorText','?')}")
        except Exception as e:
            print(f"  Tradovate {label} bağlantı hatası: {e}")

    print("  ❌ Tradovate auth başarısız — yfinance fallback")
    return None

def tv_headers():
    token = get_tv_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }

# ── TRADOVATE MEVCUT ES KONTRAT ID ───────────────────────────────────
_es_contract_id = None

def get_es_contract_id():
    """Mevcut aktif ES kontrat ID'sini bul."""
    global _es_contract_id
    if _es_contract_id:
        return _es_contract_id
    hdrs = tv_headers()
    if not hdrs:
        return None
    try:
        r = requests.get(
            f"{_tv_api_url}/contract/suggest",
            headers=hdrs,
            params={"t": "ES", "l": 5},
            timeout=10
        )
        if r.status_code == 200:
            contracts = r.json()
            if contracts:
                # En yakın expiry'yi seç
                now = datetime.now(timezone.utc)
                best = None
                for c in contracts:
                    exp_str = c.get("expirationDate", "")
                    if not exp_str:
                        continue
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                        if exp_dt > now:
                            if best is None or exp_dt < best[1]:
                                best = (c["id"], exp_dt, c.get("name", "ES"))
                    except:
                        continue
                if best:
                    _es_contract_id = best[0]
                    print(f"  ES kontrat: {best[2]} (ID:{best[0]})")
                    return _es_contract_id
                # Fallback: ilk kontrat
                _es_contract_id = contracts[0]["id"]
                return _es_contract_id
    except Exception as e:
        print(f"  ES kontrat ID hatası: {e}")
    return None

# ── TRADOVATE FİYAT ───────────────────────────────────────────────────
def get_price():
    """ES anlık fiyatı al."""
    contract_id = get_es_contract_id()
    if not contract_id:
        return get_price_yfinance()

    hdrs = tv_headers()
    if not hdrs:
        return get_price_yfinance()

    try:
        r = requests.get(
            f"{_tv_api_url}/md/getQuote",
            headers=hdrs,
            params={"contractId": contract_id},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            price = d.get("lastPrice") or d.get("bidPrice") or d.get("offerPrice")
            if price:
                return float(price)
    except Exception as e:
        print(f"  Fiyat hatası: {e}")

    return get_price_yfinance()

def get_price_yfinance():
    """Tradovate başarısız olursa yfinance fallback."""
    try:
        import yfinance as yf
        t = yf.Ticker("ES=F")
        h = t.history(period="1d", interval="1m")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except:
        pass
    return None

# ── TRADOVATE MUMB VERİSİ ─────────────────────────────────────────────
# Tradovate chart interval -> API unitCode + unit mapping
TV_TF_MAP = {
    "1m":  {"unitCode": "m", "unit": 1},
    "5m":  {"unitCode": "m", "unit": 5},
    "15m": {"unitCode": "m", "unit": 15},
    "30m": {"unitCode": "m", "unit": 30},
    "1H":  {"unitCode": "h", "unit": 1},
}

def get_candles(tf_key, limit=100):
    """Tradovate'den OHLCV mum verisi al, fallback yfinance."""
    contract_id = get_es_contract_id()
    if not contract_id:
        return get_candles_yfinance(tf_key, limit)

    hdrs = tv_headers()
    if not hdrs:
        return get_candles_yfinance(tf_key, limit)

    tf = TV_TF_MAP.get(tf_key)
    if not tf:
        return []

    try:
        # Tradovate chart endpoint
        r = requests.get(
            f"{_tv_api_url}/md/getChart",
            headers=hdrs,
            params={
                "contractId":  contract_id,
                "chartDescription": json.dumps({
                    "underlyingType": "SimpleContinuous",
                    "elementSize": tf["unit"],
                    "elementSizeUnit": tf["unitCode"],
                    "withHistogram": False
                }),
                "timeRange": json.dumps({
                    "closestTimeTo": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "asFarAsTimestamp": (datetime.now(timezone.utc) - timedelta(hours=limit * tf["unit"] // 60 + 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                })
            },
            timeout=15
        )
        if r.status_code == 200:
            bars = r.json().get("bars", [])
            if bars:
                candles = []
                for b in bars[-limit:]:
                    candles.append({
                        "open":   float(b.get("open", 0)),
                        "high":   float(b.get("high", 0)),
                        "low":    float(b.get("low", 0)),
                        "close":  float(b.get("close", 0)),
                        "volume": float(b.get("upVolume", 0)) + float(b.get("downVolume", 0))
                    })
                if candles:
                    print(f"  [{tf_key}] Tradovate: {len(candles)} mum ✅")
                    return candles
    except Exception as e:
        print(f"  [{tf_key}] Tradovate chart hatası: {e}")

    return get_candles_yfinance(tf_key, limit)

def get_candles_yfinance(tf_key, limit=100):
    """yfinance fallback — ES=F sembolünden veri çek."""
    try:
        import yfinance as yf
        yf_interval_map = {
            "1m":  "1m",
            "5m":  "5m",
            "15m": "15m",
            "30m": "30m",
            "1H":  "1h",
        }
        period_map = {
            "1m":  "1d",
            "5m":  "5d",
            "15m": "5d",
            "30m": "5d",
            "1H":  "30d",
        }
        interval = yf_interval_map.get(tf_key, "5m")
        period   = period_map.get(tf_key, "5d")

        t = yf.Ticker("ES=F")
        h = t.history(period=period, interval=interval)
        if h.empty:
            return []

        candles = []
        for _, row in h.tail(limit).iterrows():
            candles.append({
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": float(row["Volume"])
            })
        print(f"  [{tf_key}] yfinance fallback: {len(candles)} mum")
        return candles
    except Exception as e:
        print(f"  [{tf_key}] yfinance hatası: {e}")
        return []

# ── SELF-LEARNING PARAMETRELERİ ───────────────────────────────────────
DEFAULT_PARAMS = {
    "threshold":       3.5,
    "alert_threshold": 3.0,
    "sl_atr_mult":     1.0,
    "min_volume_mult": 1.2,
    "rr_min":          3.0,
    "wins": 0, "losses": 0, "total_pct": 0.0,
    "version": 1
}

def load_params():
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": "bot4_params"}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == "bot4_params":
                    p = json.loads(item["value"])
                    for k, v in DEFAULT_PARAMS.items():
                        if k not in p: p[k] = v
                    return p
    except: pass
    return DEFAULT_PARAMS.copy()

def save_params(p):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": "bot4_params"}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == "bot4_params":
                    existing = item; break
        val = json.dumps(p)
        if existing:
            requests.patch(f"{BASE_URL}/BotCache/{existing['id']}",
                           headers=HEADERS(), json={"value": val}, timeout=8)
        else:
            requests.post(f"{BASE_URL}/BotCache", headers=HEADERS(),
                          json={"key": "bot4_params", "value": val}, timeout=8)
    except Exception as e:
        print(f"  Params save error: {e}")

# ── BOT CACHE ─────────────────────────────────────────────────────────
def get_cache(key):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": key}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    return item.get("value")
    except: pass
    return None

def set_cache(key, value):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         params={"key": key}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    existing = item; break
        if existing:
            requests.patch(f"{BASE_URL}/BotCache/{existing['id']}",
                           headers=HEADERS(), json={"value": value}, timeout=8)
        else:
            requests.post(f"{BASE_URL}/BotCache", headers=HEADERS(),
                          json={"key": key, "value": value}, timeout=8)
    except Exception as e:
        print(f"Cache set error: {e}")

# ── TELEGRAM ──────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        print(f"[TELEGRAM] {msg[:100]}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram hata: {e}")

# ── TEKNİK İNDİKATÖRLER ───────────────────────────────────────────────
def calc_ema(closes, period):
    if len(closes) < period: return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]: ema = c * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0: return 100
    return 100 - (100 / (1 + avg_g / avg_l))

def calc_atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_adx(candles, period=14):
    if len(candles) < period * 2: return 20
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
    st = smooth(trs, period); sp = smooth(plus_dms, period); sm = smooth(minus_dms, period)
    dx_list = []
    for i in range(len(st)):
        if st[i] == 0: continue
        dip = 100 * sp[i] / st[i]; dim = 100 * sm[i] / st[i]; d = dip + dim
        if d == 0: continue
        dx_list.append(100 * abs(dip - dim) / d)
    if not dx_list: return 20
    return sum(dx_list[-period:]) / min(len(dx_list), period)

def check_volume(candles, min_mult=1.2):
    if not candles or len(candles) < 20: return True, 1.0
    vols = [c["volume"] for c in candles]
    avg  = sum(vols[:-1]) / len(vols[:-1])
    last = vols[-1]
    if avg == 0: return True, 1.0
    ratio = last / avg
    return ratio >= min_mult, round(ratio, 2)

def check_candle_pattern(candles, direction):
    if not candles or len(candles) < 3: return 0, "yok"
    c1, c2 = candles[-2], candles[-1]
    score, patterns = 0, []
    range2 = c2["high"] - c2["low"]
    if range2 == 0: return 0, "yok"
    body2 = abs(c2["close"] - c2["open"])

    if direction == "LONG":
        if (c1["close"] < c1["open"] and c2["close"] > c2["open"] and
                c2["open"] <= c1["close"] and c2["close"] >= c1["open"]):
            score += 3; patterns.append("engulfing")
        lower_shadow = min(c2["open"], c2["close"]) - c2["low"]
        if lower_shadow / range2 >= 0.6 and body2 / range2 <= 0.3:
            score += 2; patterns.append("pin_bar")
        if c2["close"] > c2["open"] and body2 / range2 >= 0.85:
            score += 2; patterns.append("marubozu")
    else:
        if (c1["close"] > c1["open"] and c2["close"] < c2["open"] and
                c2["open"] >= c1["close"] and c2["close"] <= c1["open"]):
            score += 3; patterns.append("engulfing")
        upper_shadow = c2["high"] - max(c2["open"], c2["close"])
        if upper_shadow / range2 >= 0.6 and body2 / range2 <= 0.3:
            score += 2; patterns.append("pin_bar")
        if c2["close"] < c2["open"] and body2 / range2 >= 0.85:
            score += 2; patterns.append("marubozu")

    return score, ", ".join(patterns) if patterns else "yok"

def get_dynamic_threshold(candles_15m, base_threshold=3.5):
    """
    S&P 500 için ATR dinamik eşik.
    Normal ATR%: ~0.05-0.15 (ES ~5500 seviyesinde)
    """
    atr   = calc_atr(candles_15m, 14)
    price = candles_15m[-1]["close"] if candles_15m else 5500
    if not atr or price == 0: return base_threshold
    atr_pct = (atr / price) * 100

    if   atr_pct > 0.20:  adj = base_threshold - 0.5   # Çok volatil
    elif atr_pct > 0.12:  adj = base_threshold - 0.2
    elif atr_pct < 0.04:  adj = base_threshold + 0.5   # Sıkışık
    elif atr_pct < 0.07:  adj = base_threshold + 0.2
    else:                 adj = base_threshold

    adj = max(2.5, min(5.0, adj))
    print(f"  ATR%: {atr_pct:.4f} | Dinamik eşik: {adj:.1f}")
    return adj

# ── TIMEFRAME SKORU ───────────────────────────────────────────────────
def score_tf(candles):
    if not candles or len(candles) < 50: return 0
    closes = [c["close"] for c in candles]
    score  = 0

    rsi = calc_rsi(closes)
    if   rsi < 30: score += 3
    elif rsi < 40: score += 2
    elif rsi < 45: score += 1
    elif rsi > 70: score -= 3
    elif rsi > 60: score -= 2
    elif rsi > 55: score -= 1

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

    macd = calc_ema(closes, 12) - calc_ema(closes, 26)
    if macd > 0: score += 2
    else:        score -= 2

    if closes[-1] > closes[-4]: score += 1
    else:                        score -= 1

    adx = calc_adx(candles)
    if adx < 20: score = score // 2

    return score

TIMEFRAMES  = [("1m",100), ("5m",100), ("15m",100), ("30m",80), ("1H",80)]
TF_WEIGHTS  = {"1m": 1.0, "5m": 2.0, "15m": 3.0, "30m": 0.5, "1H": 0.5}

# ── ANALİZ ────────────────────────────────────────────────────────────
def analyze(params):
    scores = {}; all_candles = {}

    for tf_key, limit in TIMEFRAMES:
        candles = get_candles(tf_key, limit)
        if not candles:
            print(f"  [{tf_key}] veri alınamadı"); continue
        s = score_tf(candles)
        scores[tf_key]      = s
        all_candles[tf_key] = candles
        print(f"  [{tf_key}] skor: {s:+d}")

    if len(scores) < 3:
        print("Yeterli timeframe verisi yok"); return None

    total_weight  = sum(TF_WEIGHTS[k] for k in scores)
    weighted_avg  = sum(scores[k] * TF_WEIGHTS[k] for k in scores) / total_weight
    print(f"  Ağırlıklı skor: {weighted_avg:.2f}")

    candles_15m    = all_candles.get("15m", [])
    dyn_threshold  = get_dynamic_threshold(candles_15m, params.get("threshold", 3.5))
    dyn_alert      = dyn_threshold - 0.5

    direction = None
    if   weighted_avg >=  dyn_threshold: direction = "LONG"
    elif weighted_avg <= -dyn_threshold: direction = "SHORT"
    elif abs(weighted_avg) >= dyn_alert:
        # ── HAZIR OL bildirimi ─────────────────────────────────────────
        alert_dir        = "LONG" if weighted_avg > 0 else "SHORT"
        cache_key_active = f"bot4_alert_active_{alert_dir}"
        opposite         = "SHORT" if alert_dir == "LONG" else "LONG"
        set_cache(f"bot4_alert_active_{opposite}", "0")

        is_active = get_cache(cache_key_active)
        if is_active == "1":
            print(f"  HAZIR OL zaten gönderildi ({alert_dir})")
            return None

        set_cache(cache_key_active, "1")
        set_cache(f"bot4_alert_{alert_dir}", str(int(time.time())))
        price  = get_price()
        tf_str = " | ".join([f"{k}:{v:+d}" for k, v in scores.items()])
        send_telegram(
            f"⚠️ *S&P 500 HAZIR OL — {alert_dir}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Skor: `{weighted_avg:+.2f}` (eşik: ±{dyn_threshold:.1f})\n"
            f"💰 Fiyat: `{price:.2f}`\n"
            f"📐 TF: {tf_str}\n"
            f"🔔 Sinyal eşiğine yakın, izle!\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )
        print(f"  HAZIR OL: {alert_dir} skor:{weighted_avg:.2f}")
        return None
    else:
        set_cache("bot4_alert_active_LONG",  "0")
        set_cache("bot4_alert_active_SHORT", "0")
        return None

    # ── HACIM KONFİRMASYONU ───────────────────────────────────────────
    candles_5m = all_candles.get("5m", [])
    vol_ok, vol_ratio = check_volume(candles_5m, params.get("min_volume_mult", 1.2))
    print(f"  Hacim oranı: {vol_ratio:.2f}x ({'✅' if vol_ok else '❌'})")
    if not vol_ok:
        print("  Hacim yetersiz — sinyal atlandı"); return None

    # ── MUM PATTERN ───────────────────────────────────────────────────
    pat_score, pat_name = check_candle_pattern(candles_5m, direction)
    print(f"  Mum pattern: {pat_name} (skor:{pat_score})")

    # ── ATR BAZLI SL/TP ───────────────────────────────────────────────
    atr   = calc_atr(candles_5m, 14)
    price = get_price()
    if not atr or not price: return None

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
        print(f"  RR {actual_rr:.2f} < {params.get('rr_min',3.0)}, atlandı"); return None

    return {
        "direction":     direction,
        "entry_price":   round(price, 2),
        "sl":            round(sl, 2),
        "tp":            round(tp, 2),
        "sl_pct":        round((sl_distance / price) * 100, 3),
        "tp_pct":        round((tp_distance / price) * 100, 3),
        "rr":            round(actual_rr, 2),
        "score":         round(weighted_avg, 2),
        "tf_scores":     scores,
        "pattern":       pat_name,
        "pat_score":     pat_score,
        "vol_ratio":     round(vol_ratio, 2),
        "dyn_threshold": round(dyn_threshold, 2),
    }

# ── SELF-LEARNING ─────────────────────────────────────────────────────
def self_learn(params):
    try:
        r = requests.get(f"{BASE_URL}/ActiveTrade", headers=HEADERS(),
                         params={"symbol": "ES"}, timeout=10)
        if r.status_code != 200:
            return params

        all_trades = [t for t in r.json()
                      if t.get("symbol") == "ES" and t.get("status") != "OPEN"]
        recent = sorted(all_trades,
                        key=lambda x: x.get("close_time", ""), reverse=True)[:5]

        if len(recent) < 3:
            print(f"  Self-learn: yeterli geçmiş yok ({len(recent)}/3)")
            return params

        wins   = [t for t in recent if float(t.get("result_pct", 0)) > 0]
        losses = [t for t in recent if float(t.get("result_pct", 0)) <= 0]
        win_rate  = len(wins) / len(recent)
        total_pct = sum(float(t.get("result_pct", 0)) for t in recent)
        avg_loss  = (sum(abs(float(t.get("result_pct", 0))) for t in losses)
                     / len(losses)) if losses else 0

        changed = []

        if win_rate < 0.40:
            if params["threshold"] < 5.0:
                params["threshold"] = round(params["threshold"] + 0.2, 1)
                changed.append(f"threshold↑{params['threshold']}")
            if params["sl_atr_mult"] > 0.5:
                params["sl_atr_mult"] = round(params["sl_atr_mult"] - 0.1, 1)
                changed.append(f"sl_mult↓{params['sl_atr_mult']}")
        elif win_rate >= 0.65:
            if params["threshold"] > 2.5:
                params["threshold"] = round(params["threshold"] - 0.1, 1)
                changed.append(f"threshold↓{params['threshold']}")

        if avg_loss > 0.4 and params["sl_atr_mult"] > 0.5:
            params["sl_atr_mult"] = round(params["sl_atr_mult"] - 0.1, 1)
            changed.append(f"sl_mult↓{params['sl_atr_mult']}")

        if total_pct > 1.0 and params["rr_min"] < 4.0:
            params["rr_min"] = round(params["rr_min"] + 0.1, 1)
            changed.append(f"rr↑{params['rr_min']}")

        params["wins"]      = len(wins)
        params["losses"]    = len(losses)
        params["total_pct"] = round(total_pct, 2)
        params["version"]   = params.get("version", 1) + (1 if changed else 0)

        save_params(params)
        if changed:
            print(f"  Self-learn güncellendi: {', '.join(changed)}")
        else:
            print(f"  Self-learn: parametre değişikliği yok (WR:{win_rate:.0%})")
    except Exception as e:
        print(f"  Self-learn hata: {e}")
    return params

# ── DB — AKTİF İŞLEM ──────────────────────────────────────────────────
def get_open_trade():
    try:
        r = requests.get(f"{BASE_URL}/ActiveTrade", headers=HEADERS(),
                         params={"symbol": "ES", "status": "OPEN"}, timeout=10)
        if r.status_code == 200:
            trades = [t for t in r.json()
                      if t.get("symbol") == "ES" and t.get("status") == "OPEN"]
            return trades[0] if trades else None
    except: pass
    return None

def create_trade(data):
    r = requests.post(f"{BASE_URL}/ActiveTrade",
                      headers=HEADERS(), json=data, timeout=10)
    if r.status_code in (200, 201): return r.json()
    print(f"  DB CREATE error: {r.status_code} {r.text[:100]}")
    return None

def update_trade(trade_id, data):
    r = requests.patch(f"{BASE_URL}/ActiveTrade/{trade_id}",
                       headers=HEADERS(), json=data, timeout=10)
    if r.status_code == 200: return r.json()
    print(f"  DB UPDATE error: {r.status_code} {r.text[:100]}")
    return None

# ── WATCHDOGGözetleme ─────────────────────────────────────────────────
def run_watchdog():
    print("👁️ Bot4 Watchdog başlıyor...")
    trade = get_open_trade()
    if not trade:
        print("  Açık ES işlemi yok."); return

    price = get_price()
    if not price:
        print("  Fiyat alınamadı."); return

    trade_id  = trade["id"]
    entry     = float(trade["entry_price"])
    sl        = float(trade["sl"])
    tp        = float(trade["tp"])
    direction = trade["direction"]
    orig_sl   = float(trade.get("original_sl") or sl)
    sl_be     = trade.get("sl_moved_breakeven", False)
    sl_pr     = trade.get("sl_moved_profit", False)

    sl_distance = abs(entry - orig_sl)
    result_pct  = ((price - entry) / entry * 100) if direction == "LONG" \
                  else ((entry - price) / entry * 100)
    rr_current  = result_pct / (sl_distance / entry * 100) if sl_distance > 0 else 0

    print(f"  {direction} | Giriş:{entry:.2f} | Şimdi:{price:.2f} | "
          f"SL:{sl:.2f} | TP:{tp:.2f} | Sonuç:{result_pct:+.3f}%")

    updates = {}
    notify_msg = None

    # ── SL KAPANIŞ KONTROLÜ ───────────────────────────────────────────
    if direction == "LONG" and price <= sl:
        if result_pct >= 0:
            label = "〽️ S&P 500 BREAKEVEN ÇIKTI"
        elif abs(result_pct) < 0.05:
            label = "〽️ S&P 500 BREAKEVEN ÇIKTI"
        else:
            label = "🛑 S&P 500 SL ULAŞTI"
        updates = {"status": "SL_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (
            f"{label}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
            f"{'💰' if result_pct >= 0 else '💸'} Sonuç: `{result_pct:+.3f}%`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    elif direction == "SHORT" and price >= sl:
        if result_pct >= 0:
            label = "〽️ S&P 500 BREAKEVEN ÇIKTI"
        elif abs(result_pct) < 0.05:
            label = "〽️ S&P 500 BREAKEVEN ÇIKTI"
        else:
            label = "🛑 S&P 500 SL ULAŞTI"
        updates = {"status": "SL_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (
            f"{label}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
            f"{'💰' if result_pct >= 0 else '💸'} Sonuç: `{result_pct:+.3f}%`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    # ── TP KONTROLÜ ───────────────────────────────────────────────────
    elif direction == "LONG" and price >= tp:
        updates = {"status": "TP_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (
            f"✅ S&P 500 KAR İLE ÇIKTI\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
            f"💰 Sonuç: `{result_pct:+.3f}%`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    elif direction == "SHORT" and price <= tp:
        updates = {"status": "TP_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (
            f"✅ S&P 500 KAR İLE ÇIKTI\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
            f"💰 Sonuç: `{result_pct:+.3f}%`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    # ── SL KAYDIR: Breakeven ──────────────────────────────────────────
    elif not sl_be and rr_current >= 1.0:
        new_sl = entry + (sl_distance * 0.1) if direction == "LONG" \
                 else entry - (sl_distance * 0.1)
        new_sl = round(new_sl, 2)
        updates = {"sl": new_sl, "sl_moved_breakeven": True}
        notify_msg = (
            f"🔄 *S&P 500 SL → BREAKEVEN*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Şimdi: `{price:.2f}`\n"
            f"🛡️ Yeni SL: `{new_sl:.2f}` (breakeven)\n"
            f"📈 Mevcut: `{result_pct:+.3f}%` | RR: `{rr_current:.1f}R`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    # ── SL KAYDIR: Kâr bölgesi ────────────────────────────────────────
    elif sl_be and not sl_pr and rr_current >= 2.0:
        new_sl = entry + sl_distance * 1.0 if direction == "LONG" \
                 else entry - sl_distance * 1.0
        new_sl = round(new_sl, 2)
        updates = {"sl": new_sl, "sl_moved_profit": True}
        notify_msg = (
            f"📈 *S&P 500 SL → KÂR BÖLGESİ*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giriş: `{entry:.2f}` | Şimdi: `{price:.2f}`\n"
            f"🛡️ Yeni SL: `{new_sl:.2f}` (+1R kârda)\n"
            f"📈 Mevcut: `{result_pct:+.3f}%` | RR: `{rr_current:.1f}R`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 *Bot 4 — S&P 500 Scalper*"
        )

    if updates:
        update_trade(trade_id, updates)
        print(f"  İşlem güncellendi: {updates}")
    if notify_msg:
        send_telegram(notify_msg)

# ── SCAN ──────────────────────────────────────────────────────────────
def run_scan():
    print("🔍 Bot4 Scan başlıyor (S&P 500 / ES)...")
    params = load_params()

    # Açık işlem varsa scan yapma
    open_trade = get_open_trade()
    if open_trade:
        print(f"  Açık işlem var ({open_trade['direction']} @ {open_trade['entry_price']}) — scan atlandı")
        return

    # Self-learn
    last_learn = get_cache("bot4_last_learn")
    now_ts = int(time.time())
    if not last_learn or (now_ts - int(last_learn)) >= 900:
        params = self_learn(params)
        set_cache("bot4_last_learn", str(now_ts))

    signal = analyze(params)
    if not signal:
        print("  Sinyal yok."); return

    direction = signal["direction"]
    price     = signal["entry_price"]
    sl        = signal["sl"]
    tp        = signal["tp"]
    sl_pct    = signal["sl_pct"]
    tp_pct    = signal["tp_pct"]
    rr        = signal["rr"]
    score     = signal["score"]
    pattern   = signal["pattern"]

    # DB'ye kaydet
    trade_data = {
        "symbol":           "ES",
        "direction":        direction,
        "entry_price":      price,
        "tp":               tp,
        "sl":               sl,
        "original_sl":      sl,
        "rr":               rr,
        "score":            score,
        "status":           "OPEN",
        "sl_moved_breakeven": False,
        "sl_moved_profit":    False,
        "tp_extended":        False,
        "open_time":          datetime.now(timezone.utc).isoformat(),
        "close_time":         None,
        "result_pct":         None,
        "notes": json.dumps({
            "pattern": pattern,
            "tf_scores": signal["tf_scores"],
            "vol_ratio": signal["vol_ratio"],
            "dyn_threshold": signal["dyn_threshold"],
        })
    }

    created = create_trade(trade_data)
    if not created:
        print("  ❌ DB'ye kaydedilemedi"); return

    dir_str = "📈 LONG 🟢" if direction == "LONG" else "📉 SHORT 🔴"
    send_telegram(
        f"🚨 *BOT 4 — S&P 500 SİNYALİ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *ES (E-Mini S&P 500)* | {dir_str}\n"
        f"📍 Giriş: `{price:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *TP*: `{tp:.2f}` (+{tp_pct:.3f}%)\n"
        f"🛡️ *SL*: `{sl:.2f}` (-{sl_pct:.3f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *R:R* → {rr:.2f}R\n"
        f"📊 *Skor* → {score:.2f}\n"
        f"🕯️ *Pattern* → {pattern}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 *Bot 4 — S&P 500 Scalper*"
    )
    print(f"  ✅ Sinyal gönderildi: {direction} @ {price:.2f}")

# ── MAIN ──────────────────────────────────────────────────────────────
import sys
MODE = sys.argv[1] if len(sys.argv) > 1 else "scan"

if __name__ == "__main__":
    if MODE == "scan":
        run_scan()
    elif MODE == "watchdog":
        run_watchdog()
    else:
        print(f"Bilinmeyen mod: {MODE}")
