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
BASE44_TOKEN = os.environ.get("BASE44_API_KEY", "d1e53ae9295b46a0bd197d93627ca7a0")

PARAMS = {
    "minRR": 2.0,
    "maxSlPct": 4.5,
    "minSlPct": 0.3,
    "blacklistHours": 48,
    "maxOpenTrades": 3,
    "tp1Ratio": 1.8,
    "tp2Ratio": 5.0,
    "avoidCoins": ["SKYAIUSDT", "XAUUSDT"],  # XAUUSDT sadece Bot3 yönetir
    "minScoreThreshold": 3.5,
    "leverage": 5,
}

MODE = sys.argv[1] if len(sys.argv) > 1 else "watchdog"

# ── BASE44 DB ─────────────────────────────────────────────────────────
def b44_headers():
    return {
        "api_key": BASE44_TOKEN,
        "Content-Type": "application/json"
    }

def get_open_trades():
    """Bot2'ye ait açık işlemleri döndür — XAUUSDT (Bot3) hariç, 403 durumunda retry"""
    for attempt in range(2):
        try:
            resp = requests.get(BASE44_API, headers=b44_headers(), params={"status": "OPEN"}, timeout=15)
            if resp.status_code == 200:
                trades = resp.json() if isinstance(resp.json(), list) else resp.json().get("records", [])
                return [t for t in trades if t.get("symbol") != "XAUUSDT"]
            elif resp.status_code == 403 and attempt == 0:
                print(f"  get_open_trades 403 — token yenileniyor...")
                refresh_token()
                continue
            else:
                print(f"  DB GET error: {resp.status_code} {resp.text[:100]}")
                break
        except Exception as e:
            print(f"  DB GET exception: {e}")
            break
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


# ── SELF-LEARNING ─────────────────────────────────────────────────────
BOTCACHE_API = "https://app.base44.com/api/apps/6a1d973568af9b984e0f1cc8/entities/BotCache"

def get_cache(key):
    try:
        r = requests.get(BOTCACHE_API, headers=b44_headers(), params={"key": key}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    return item.get("value")
    except: pass
    return None

def set_cache(key, value):
    try:
        r = requests.get(BOTCACHE_API, headers=b44_headers(), params={"key": key}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    existing = item; break
        if existing:
            requests.patch(f"{BOTCACHE_API}/{existing['id']}", headers=b44_headers(), json={"value": value}, timeout=8)
        else:
            requests.post(BOTCACHE_API, headers=b44_headers(), json={"key": key, "value": value}, timeout=8)
    except Exception as e:
        print(f"Cache set error: {e}")

# ── PER-COIN PARAMETRE DEFAULTS ───────────────────────────────────────
COIN_PARAM_DEFAULTS = {
    "minScoreThreshold": 3.5,
    "maxSlPct": 4.5,
    "wins": 0,
    "losses": 0,
    "version": 1,
    # Her indikatörün güvenilirlik ağırlığı (1.0=tam, 0.5=yarı, 0.0=devre dışı)
    "ind_weights": {
        "rsi":   1.0,
        "ema":   1.0,
        "macd":  1.0,
        "stoch": 1.0,
        "bb":    1.0,
    },
    # Her indikatörün geçmiş isabeti: {ind: [toplam_sinyal, doğru_sinyal]}
    "ind_stats": {
        "rsi":   [0, 0],
        "ema":   [0, 0],
        "macd":  [0, 0],
        "stoch": [0, 0],
        "bb":    [0, 0],
    },
}

def get_coin_params(symbol):
    """Bu coin için BotCache'den parametre profilini yükle."""
    cache_key = f"bot2_coin_{symbol}"
    raw = get_cache(cache_key)
    if raw:
        try:
            p = json.loads(raw)
            for k, v in COIN_PARAM_DEFAULTS.items():
                if k not in p:
                    p[k] = v
            return p
        except:
            pass
    return COIN_PARAM_DEFAULTS.copy()

def save_coin_params(symbol, p):
    """Bu coin için parametre profilini BotCache'e kaydet."""
    cache_key = f"bot2_coin_{symbol}"
    set_cache(cache_key, json.dumps(p))


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
        if ask_liq: wall_note.append(f"Satış@{ask_liq[0]:.4f}")
        if bid_liq: wall_note.append(f"Alış@{bid_liq[0]:.4f}")
        wall_note = " | ".join(wall_note) if wall_note else "Duvar yok"
        ob_score = 1 if bid_ratio >= 0.60 else (-1 if bid_ratio <= 0.40 else 0)
        return ob_score, round(bid_wall,2), round(ask_wall,2), wall_note
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

def self_learn():
    """
    Tüm kapanan işlemleri coin bazlı gruplandır.
    Her coin kendi geçmişinden öğrenir — diğer coinleri etkilemez.
    Sessiz çalışır.
    """
    try:
        all_trades = get_all_trades(100)
        closed = [t for t in all_trades
                  if t.get("status") in ("TP_HIT", "SL_HIT")
                  and t.get("result_pct") is not None
                  and t.get("symbol")]

        if not closed:
            print("  Bot2 self-learn: hiç kapanan işlem yok")
            return

        # Coin bazlı gruplama
        from collections import defaultdict
        by_symbol = defaultdict(list)
        for t in closed:
            by_symbol[t["symbol"]].append(t)

        for symbol, trades in by_symbol.items():
            recent = sorted(trades, key=lambda x: x.get("close_time", ""), reverse=True)[:8]
            if len(recent) < 3:
                continue

            wins   = [t for t in recent if float(t.get("result_pct", 0)) > 0]
            losses = [t for t in recent if float(t.get("result_pct", 0)) <= 0]
            win_rate = len(wins) / len(recent)
            avg_loss = sum(abs(float(t.get("result_pct", 0))) for t in losses) / len(losses) if losses else 0

            p = get_coin_params(symbol)
            changed = []
            ind_w = p.get("ind_weights", COIN_PARAM_DEFAULTS["ind_weights"].copy())
            ind_stats = p.get("ind_stats", {k: [0, 0] for k in ["rsi","ema","macd","stoch","bb"]})

            # ── DERİN ANALİZ: Her indikatörü ayrı değerlendir ──────────
            # Her işlemin notes'ından entry_snapshot çek
            for trade in recent:
                try:
                    notes = json.loads(trade.get("notes") or "{}")
                    snap  = notes.get("entry_snapshot", {})
                    if not snap:
                        continue
                    outcome_win = float(trade.get("result_pct", 0)) > 0
                    direction   = trade.get("direction", "LONG")
                    sign        = 1 if direction == "LONG" else -1

                    # Her indikatörün işlem yönüne katkısını kontrol et
                    # Pozitif katkı = işlem yönünü destekledi, negatif = karşı çıktı
                    for ind in ["rsi", "ema", "macd", "stoch", "bb"]:
                        # 1h ve 4h skorlarını al (daha güvenilir TF)
                        contrib_1h = snap.get("1h", {}).get(ind, 0)
                        contrib_4h = snap.get("4h", {}).get(ind, 0)
                        contrib    = contrib_1h * 0.4 + contrib_4h * 0.6

                        # İndikatör işlem yönünü desteklediyse (aynı işaret)
                        supported_direction = (contrib * sign) > 0

                        if supported_direction:
                            # Bu indikatör giriş kararını destekledi
                            ind_stats[ind][0] += 1          # toplam sinyal
                            if outcome_win:
                                ind_stats[ind][1] += 1      # doğru sinyal
                except:
                    continue

            # ── İNDİKATÖR AĞIRLIKLARINI GÜNCELLE ──────────────────────
            ind_analysis = []
            for ind in ["rsi", "ema", "macd", "stoch", "bb"]:
                total_sig, correct_sig = ind_stats.get(ind, [0, 0])
                if total_sig < 3:
                    continue  # yeterli veri yok bu indikatör için
                ind_wr = correct_sig / total_sig

                old_w = ind_w.get(ind, 1.0)
                if ind_wr < 0.30:
                    # Bu indikatör %30'dan az doğru → yarıya indir (min 0.2)
                    new_w = round(max(old_w - 0.2, 0.2), 1)
                elif ind_wr < 0.45:
                    new_w = round(max(old_w - 0.1, 0.3), 1)
                elif ind_wr >= 0.70:
                    # Çok güvenilir → ağırlığı artır (max 1.5)
                    new_w = round(min(old_w + 0.1, 1.5), 1)
                elif ind_wr >= 0.60:
                    new_w = round(min(old_w + 0.05, 1.3), 1)
                else:
                    new_w = old_w

                if new_w != old_w:
                    ind_analysis.append(f"{ind}:{old_w}→{new_w}(WR:{ind_wr:.0%})")
                    ind_w[ind] = new_w
                    changed.append(f"{ind}_w:{new_w}")

            # ── GENEL PARAMETRELER ──────────────────────────────────────
            if win_rate < 0.38:
                if p["minScoreThreshold"] < 5.5:
                    p["minScoreThreshold"] = round(p["minScoreThreshold"] + 0.3, 1)
                    changed.append(f"score↑{p['minScoreThreshold']}")
                if p["maxSlPct"] > 2.0:
                    p["maxSlPct"] = round(p["maxSlPct"] - 0.3, 1)
                    changed.append(f"sl↓{p['maxSlPct']}")
            elif win_rate >= 0.70:
                if p["minScoreThreshold"] > 2.5:
                    p["minScoreThreshold"] = round(p["minScoreThreshold"] - 0.1, 1)
                    changed.append(f"score↓{p['minScoreThreshold']}")
            if avg_loss > 3.5 and p["maxSlPct"] > 2.0:
                p["maxSlPct"] = round(p["maxSlPct"] - 0.2, 1)
                changed.append(f"sl↓{p['maxSlPct']}")

            p["wins"]       = len(wins)
            p["losses"]     = len(losses)
            p["ind_weights"] = ind_w
            p["ind_stats"]   = ind_stats
            p["version"]     = p.get("version", 1) + (1 if changed else 0)

            save_coin_params(symbol, p)

            sym_short = symbol.replace("USDT", "")
            if ind_analysis:
                print(f"  Self-learn [{sym_short}]: WR={win_rate:.0%} | İnd: {' | '.join(ind_analysis)}")
            if changed and not ind_analysis:
                print(f"  Self-learn [{sym_short}]: WR={win_rate:.0%} | {', '.join(changed)}")
            if not changed:
                print(f"  Self-learn [{sym_short}]: WR={win_rate:.0%} AvgLoss={avg_loss:.2f}% — stabil")

    except Exception as e:
        print(f"  Bot2 self-learn hata: {e}")

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

def score_tf_detailed(candles, ind_weights=None):
    """
    Her indikatörün katkısını ayrı döndür.
    ind_weights: coin bazlı öğrenilmiş ağırlıklar (1.0 = normal, 0.5 = yarı güvenilir, 0.0 = devre dışı)
    Döner: (toplam_skor, {indikatör: (katkı, ham_değer)})
    """
    if not candles or len(candles) < 50:
        return 0, {}
    closes = [c["close"] for c in candles]
    price  = closes[-1]
    w      = ind_weights or {}

    breakdown = {}

    # RSI
    rsi = calc_rsi(closes)
    if   rsi < 30: rsi_contrib = +3
    elif rsi < 40: rsi_contrib = +2
    elif rsi < 45: rsi_contrib = +1
    elif rsi > 70: rsi_contrib = -3
    elif rsi > 60: rsi_contrib = -2
    elif rsi > 55: rsi_contrib = -1
    else:          rsi_contrib = 0
    breakdown["rsi"] = (round(rsi_contrib * w.get("rsi", 1.0)), round(rsi, 1))

    # EMA trend
    ema9  = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    if   ema9 > ema21 > ema50: ema_contrib = +2
    elif ema9 < ema21 < ema50: ema_contrib = -2
    else:                       ema_contrib = 0
    ema_contrib += 1 if price > ema50 else -1
    ema_trend = "bull" if ema9 > ema21 else "bear"
    breakdown["ema"] = (round(ema_contrib * w.get("ema", 1.0)), ema_trend)

    # MACD
    macd_contrib = 0
    if len(closes) >= 35:
        macd = calc_ema(closes, 12) - calc_ema(closes, 26)
        macd_contrib = +2 if macd > 0 else -2
    breakdown["macd"] = (round(macd_contrib * w.get("macd", 1.0)), "pos" if macd_contrib > 0 else "neg")

    # Stochastic
    stoch_contrib = 0
    if len(candles) >= 14:
        sl     = candles[-14:]
        lo_min = min(c["low"]  for c in sl)
        hi_max = max(c["high"] for c in sl)
        k = ((price - lo_min) / (hi_max - lo_min)) * 100 if hi_max != lo_min else 50
        if   k < 20: stoch_contrib = +2
        elif k < 30: stoch_contrib = +1
        elif k > 80: stoch_contrib = -2
        elif k > 70: stoch_contrib = -1
    breakdown["stoch"] = (round(stoch_contrib * w.get("stoch", 1.0)), round(k if len(candles) >= 14 else 50, 1))

    # Bollinger
    bb_contrib = 0
    if len(closes) >= 20:
        closes20 = closes[-20:]
        mid = sum(closes20) / 20
        std = math.sqrt(sum((x - mid)**2 for x in closes20) / 20)
        if   price <= mid - 2*std: bb_contrib = +2
        elif price >= mid + 2*std: bb_contrib = -2
    breakdown["bb"] = (round(bb_contrib * w.get("bb", 1.0)), round(price - mid if len(closes) >= 20 else 0, 2))

    total = sum(v[0] for v in breakdown.values())
    return total, breakdown

def score_tf(candles, ind_weights=None):
    """Geriye uyumluluk için — sadece skoru döndür."""
    score, _ = score_tf_detailed(candles, ind_weights)
    return score


def check_volume(candles, mult=1.3):
    """Son mumun hacmi, son 20 mum ort. mult katı mı?"""
    if len(candles) < 21:
        return True, 1.0
    vols = [c["volume"] for c in candles[-21:-1]]
    avg_vol = sum(vols) / len(vols)
    last_vol = candles[-1]["volume"]
    ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
    return ratio >= mult, round(ratio, 2)

def calc_atr_pct(candles, period=14):
    """ATR yüzdesi — dinamik eşik için"""
    if len(candles) < period + 1:
        return 0.5
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-period:]) / period
    price = candles[-1]["close"]
    return (atr / price) * 100 if price > 0 else 0.5

def calc_tp_sl(price, is_long, atr, res, sup, coin_p=None):
    """coin_p verilirse o coin'in öğrenilmiş maxSlPct kullanılır."""
    max_sl_pct = coin_p["maxSlPct"] if coin_p else PARAMS["maxSlPct"]
    min_sl_dist = atr * 1.5
    max_sl_dist = price * (max_sl_pct / 100)
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
    # Self-learning her scan başında çalışır
    last_learn = get_cache("bot2_last_learn")
    now_ts = int(time.time())
    if not last_learn or (now_ts - int(last_learn)) >= 900:
        self_learn()
        set_cache("bot2_last_learn", str(now_ts))
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
            c30m = get_ohlcv(symbol, "30m", 100)
            c1h  = get_ohlcv(symbol, "1H",  100)
            c4h  = get_ohlcv(symbol, "4H",  80)
            if not c1h or not c4h:
                continue

            # Bu coin için öğrenilmiş indikatör ağırlıklarını al
            ind_w = coin_p.get("ind_weights", COIN_PARAM_DEFAULTS["ind_weights"])

            s15, bd15 = score_tf_detailed(c15m, ind_w)
            s30, bd30 = score_tf_detailed(c30m, ind_w)
            s1h, bd1h = score_tf_detailed(c1h,  ind_w)
            s4h, bd4h = score_tf_detailed(c4h,  ind_w)
            weighted = (s15 * 1 + s30 * 2 + s1h * 3 + s4h * 2) / 8

            # Giriş snapshot'ı — hangi indikatör ne dedi
            entry_snapshot = {
                "15m": {k: v[0] for k, v in bd15.items()},
                "1h":  {k: v[0] for k, v in bd1h.items()},
                "4h":  {k: v[0] for k, v in bd4h.items()},
                "raw": {  # ham değerler
                    "rsi_15m":  bd15.get("rsi", [0,0])[1],
                    "rsi_1h":   bd1h.get("rsi", [0,0])[1],
                    "ema_15m":  bd15.get("ema", [0,""])[1],
                    "ema_1h":   bd1h.get("ema", [0,""])[1],
                    "macd_1h":  bd1h.get("macd", [0,""])[1],
                    "stoch_1h": bd1h.get("stoch", [0,0])[1],
                    "bb_1h":    bd1h.get("bb", [0,0])[1],
                }
            }

            # Bu coin için öğrenilmiş parametreleri yükle
            coin_p = get_coin_params(symbol)
            coin_score_thresh = coin_p["minScoreThreshold"]

            # ATR dinamik eşik — coin'in kendi öğrenilmiş eşiği üzerine uygulanır
            atr_pct = calc_atr_pct(c1h)
            if atr_pct > 1.5:    dyn_thresh = coin_score_thresh - 0.4
            elif atr_pct > 0.8:  dyn_thresh = coin_score_thresh - 0.2
            elif atr_pct < 0.2:  dyn_thresh = coin_score_thresh + 0.5
            elif atr_pct < 0.4:  dyn_thresh = coin_score_thresh + 0.2
            else:                dyn_thresh = coin_score_thresh
            dyn_thresh = max(2.5, min(6.5, dyn_thresh))

            if abs(weighted) < dyn_thresh:
                continue

            # Hacim konfirmasyonu (1H mumunda)
            vol_ok, vol_ratio = check_volume(c1h, 1.3)
            if not vol_ok:
                continue

            # OB filtresi: karşı duvar çok güçlüyse sinyali engelle
            if (weighted > 0 and ob_score == -1) or (weighted < 0 and ob_score == 1):
                print(f"  [{symbol}] OB karşı duvar — sinyal engellendi")
                continue
            is_long = weighted > 0
            price = c1h[-1]["close"]
            atr = calc_atr(c1h)
            combined = c1h + c4h
            res, sup = find_sr_levels(combined)
            calc = calc_tp_sl(price, is_long, atr, res, sup, coin_p)

            if calc["rr"] < PARAMS["minRR"]:
                continue

            liq_tp = get_tp_from_liquidity(symbol, price_now, "LONG" if is_long else "SHORT")
            results.append({
                "symbol": symbol, "score": weighted,
                "direction": "LONG" if is_long else "SHORT",
                "tp1": calc["tp1"], "tp2": calc["tp2"], "sl": calc["sl"],
                "rr": calc["rr"], "price": price,
                "vol_ratio": vol_ratio, "dyn_thresh": round(dyn_thresh, 2),
                "coin_score_thresh": round(coin_score_thresh, 1),
                "entry_snapshot": entry_snapshot,
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
                "vol_ratio": sig.get("vol_ratio", "?"),
                "dyn_thresh": sig.get("dyn_thresh", "?"),
                "coin_score_thresh": sig.get("coin_score_thresh", "?"),
                "entry_snapshot": sig.get("entry_snapshot", {}),
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
            f"📊 *Skor* → {sig['score']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📚 OB: Alış `{bid_wall:.1f}` / Satış `{ask_wall:.1f}` | {wall_note}" +
            (f"\n🎯 Liq TP: `{liq_tp:.6g}`" if liq_tp else "")
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
            # SL kâr bölgesinde mi? (Long: sl>entry | Short: sl<entry)
            sl_in_profit = (is_long and sl > entry) or (not is_long and sl < entry)
            # Breakeven mi? (sl == entry)
            sl_at_be = abs(sl - entry) < 0.0001 * entry

            if sl_in_profit:
                # Kârlı kapanış
                update_trade(trade_id, {
                    "status": "TP_HIT",
                    "close_time": datetime.now(timezone.utc).isoformat(),
                    "result_pct": round(pct, 2)
                })
                add_to_whitelist(symbol)
                send_telegram(
                    f"✅ *KÂR İLE KAPANDI — {sym}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{'📈 LONG' if is_long else '📉 SHORT'} SL kâr bölgesinde tetiklendi\n"
                    f"💰 Giriş: `{entry:.6g}` → Kapanış: `{sl:.6g}`\n"
                    f"📊 +{pct:.2f}% | 🔗 @{PARAMS['leverage']}x: +{lev:.2f}%\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                print(f"✅ KÂR SL: {sym} +{pct:.2f}%")
            elif sl_at_be:
                # Breakeven kapanış
                update_trade(trade_id, {
                    "status": "SL_HIT",
                    "close_time": datetime.now(timezone.utc).isoformat(),
                    "result_pct": 0.0
                })
                send_telegram(
                    f"⚖️ *BREAKEVEN — {sym}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{'📈 LONG' if is_long else '📉 SHORT'} başa baş kapandı\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                print(f"⚖️ BREAKEVEN: {sym}")
            else:
                # Gerçek zarar
                update_trade(trade_id, {
                    "status": "SL_HIT",
                    "close_time": datetime.now(timezone.utc).isoformat(),
                    "result_pct": round(-pct, 2)
                })
                add_to_blacklist(symbol)
                send_telegram(
                    f"❌ *SL HIT — {sym}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{'📈 LONG' if is_long else '📉 SHORT'} stoploss!\n"
                    f"💸 -{pct:.2f}% | 🔗 @{PARAMS['leverage']}x: -{lev:.2f}%\n"
                    f"⛔ 48 saat blacklist'e eklendi\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                print(f"❌ SL HIT: {sym} -{pct:.2f}%")
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
