#!/usr/bin/env python3
"""
Bot 5 — NASDAQ (NQ) ICT Liquidity Sweep + Inverse FVG Bot
Strateji v5-HYBRID2 (backtest optimize — ksbtrades ICT + 2.5 ay veri):
  1. NQ'ya New York açılışında (09:30 NY) bak.
  2. Asia (20:00-00:00 NY) ve London (00:00-05:00 NY) seans high/low'larını işaretle.
  3. Likidite avını (sweep) bekle — 4 seviyeden biri süpürülür.
  4. v5-HYBRID2 FİLTRELER:
     - Asia High süpürülürse → İŞLEM YAPMA (atlanır)
     - Asia Low süpürülürse → İŞLEM YAPMA (atlanır)
     - London High süpürülürse → 15dk mumlarında SHORT (SL: sweep extreme, backtest: 67% win, +2.907%)
     - London Low süpürülürse → 5dk mumlarında TERS SHORT (SL: London High, backtest: 57% win, +4.558%)
  5. Giriş: inverse FVG kırılımı. TP: 1:2 RR. İşlem SL/TP olana kadar açık kalır (çoklu gün).
  6. Hybrid-2 backtest: 10 işlem, 6 TP, 4 SL, +7.464%, %60 win (2.5 ay).

Veri: Tradovate API (varsa) -> yfinance NQ=F fallback (Bot4/ES ile aynı desen).
Veritabanı: Base44 ActiveTrade / BotCache entity'leri, api_key header.
Telegram: TELEGRAM_BOT_TOKEN_9 (@... — kurulacak yeni bot).
"""

import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_9", "")
CHAT_ID        = "2055780815"
BASE44_TOKEN   = os.environ.get("BASE44_API_KEY", "")
APP_ID         = "6a1d973568af9b984e0f1cc8"
SYMBOL         = "NQ"
SYMBOL_DISPLAY = "NASDAQ 100 (NQ)"
RR_TARGET      = 2.0

NY = ZoneInfo("America/New_York")

TV_USERNAME = os.environ.get("TRADOVATE_USERNAME", "")
TV_PASSWORD = os.environ.get("TRADOVATE_PASSWORD", "")

TV_LIVE_URL  = "https://live.tradovateapi.com/v1"
TV_DEMO_URL  = "https://demo.tradovateapi.com/v1"

BASE_URL = f"https://app.base44.com/api/apps/{APP_ID}/entities"
HEADERS  = lambda: {"api_key": BASE44_TOKEN, "Content-Type": "application/json"}

# ── TRADOVATE TOKEN ────────────────────────────────────────────────────
_tv_token = None
_tv_token_exp = 0
_tv_api_url = TV_LIVE_URL

def get_tv_token():
    global _tv_token, _tv_token_exp, _tv_api_url
    now_ts = int(time.time())
    if _tv_token and now_ts < _tv_token_exp - 60:
        return _tv_token
    if not TV_USERNAME or not TV_PASSWORD:
        return None
    payload = {"name": TV_USERNAME, "password": TV_PASSWORD,
               "appId": "Sample App", "appVersion": "1.0", "cid": 0, "sec": ""}
    for api_url, label in [(TV_LIVE_URL, "Live"), (TV_DEMO_URL, "Demo")]:
        try:
            r = requests.post(f"{api_url}/auth/accesstokenrequest", json=payload, timeout=15)
            d = r.json()
            if "accessToken" in d:
                _tv_token = d["accessToken"]
                _tv_token_exp = now_ts + d.get("expirationTime", 4800) // 1000
                _tv_api_url = api_url
                print(f"  Tradovate {label} auth OK")
                return _tv_token
        except Exception as e:
            print(f"  Tradovate {label} auth hata: {e}")
    print("  Tradovate auth başarısız -> yfinance fallback")
    return None

def tv_headers():
    token = get_tv_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

_nq_contract_id = None

def get_nq_contract_id():
    global _nq_contract_id
    if _nq_contract_id:
        return _nq_contract_id
    hdrs = tv_headers()
    if not hdrs:
        return None
    try:
        r = requests.get(f"{_tv_api_url}/contract/suggest", headers=hdrs,
                         params={"t": "NQ", "l": 5}, timeout=10)
        if r.status_code == 200:
            contracts = r.json()
            if contracts:
                now = datetime.now(timezone.utc)
                best = None
                for c in contracts:
                    exp_str = c.get("expirationDate", "")
                    if not exp_str: continue
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                        if exp_dt > now and (best is None or exp_dt < best[1]):
                            best = (c["id"], exp_dt, c.get("name", "NQ"))
                    except: continue
                if best:
                    _nq_contract_id = best[0]
                    print(f"  NQ kontrat: {best[2]} (ID:{best[0]})")
                    return _nq_contract_id
                _nq_contract_id = contracts[0]["id"]
                return _nq_contract_id
    except Exception as e:
        print(f"  NQ kontrat ID hatası: {e}")
    return None

def get_price():
    contract_id = get_nq_contract_id()
    hdrs = tv_headers()
    if contract_id and hdrs:
        try:
            r = requests.get(f"{_tv_api_url}/md/getQuote", headers=hdrs,
                             params={"contractId": contract_id}, timeout=8)
            if r.status_code == 200:
                d = r.json()
                price = d.get("lastPrice") or d.get("bidPrice") or d.get("offerPrice")
                if price: return float(price)
        except Exception as e:
            print(f"  Fiyat hatası: {e}")
    return get_price_yfinance()

def get_price_yfinance():
    try:
        import yfinance as yf
        t = yf.Ticker("NQ=F")
        h = t.history(period="1d", interval="1m")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except: pass
    return None

def get_candles_yfinance(interval, period):
    """interval: '5m'/'15m'/'1m' ; period: '1d'/'2d'/'5d'. Datetime NY-aware."""
    try:
        import yfinance as yf
        t = yf.Ticker("NQ=F")
        h = t.history(period=period, interval=interval)
        if h.empty:
            return []
        candles = []
        for idx, row in h.iterrows():
            dt = idx.tz_convert(NY) if idx.tzinfo else idx.tz_localize("UTC").tz_convert(NY)
            candles.append({
                "dt": dt.to_pydatetime(),
                "open": float(row["Open"]), "high": float(row["High"]),
                "low": float(row["Low"]), "close": float(row["Close"]),
            })
        return candles
    except Exception as e:
        print(f"  yfinance hatası: {e}")
        return []

# ── BASE44 CACHE / ENTITY ──────────────────────────────────────────────
def get_cache(key):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(), params={"key": key}, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    return item.get("value")
    except: pass
    return None

def set_cache(key, value):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(), params={"key": key}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key:
                    existing = item; break
        if existing:
            requests.put(f"{BASE_URL}/BotCache/{existing['id']}", headers=HEADERS(),
                        json={"value": value}, timeout=8)
        else:
            requests.post(f"{BASE_URL}/BotCache", headers=HEADERS(),
                         json={"key": key, "value": value}, timeout=8)
    except Exception as e:
        print(f"  Cache set error: {e}")

def get_open_trade():
    try:
        r = requests.get(f"{BASE_URL}/ActiveTrade", headers=HEADERS(),
                         params={"symbol": SYMBOL, "status": "OPEN"}, timeout=10)
        if r.status_code == 200:
            trades = [t for t in r.json() if t.get("symbol") == SYMBOL and t.get("status") == "OPEN"]
            return trades[0] if trades else None
    except: pass
    return None

def create_trade(data):
    r = requests.post(f"{BASE_URL}/ActiveTrade", headers=HEADERS(), json=data, timeout=10)
    if r.status_code in (200, 201): return r.json()
    print(f"  DB CREATE error: {r.status_code} {r.text[:150]}")
    return None

def update_trade(trade_id, data):
    r = requests.put(f"{BASE_URL}/ActiveTrade/{trade_id}", headers=HEADERS(), json=data, timeout=10)
    if r.status_code == 200: return r.json()
    print(f"  DB UPDATE error: {r.status_code} {r.text[:150]}")
    return None

# ── TELEGRAM ────────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        print(f"[TELEGRAM NO TOKEN] {msg[:150]}")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print("  ✅ Telegram gönderildi" if r.status_code == 200 else f"  ❌ Telegram hata: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"  ❌ Telegram exception: {e}")

# ── ZAMAN / SESSION YARDIMCILARI ────────────────────────────────────────
def ny_now():
    return datetime.now(NY)

def session_levels_for_today(today_ny):
    """Asia (dun 20:00 - bugun 00:00) + London (bugun 00:00 - 05:00) high/low.
    Seviyeler 5dk mumlarindan hesaplanir (backtest ile uyumlu)."""
    candles = get_candles_yfinance("5m", "2d")
    if not candles:
        return None

    asia_start = datetime.combine(today_ny - timedelta(days=1), dtime(20, 0), tzinfo=NY)
    asia_end   = datetime.combine(today_ny, dtime(0, 0), tzinfo=NY)
    london_start = asia_end
    london_end   = datetime.combine(today_ny, dtime(5, 0), tzinfo=NY)

    asia   = [c for c in candles if asia_start <= c["dt"] < asia_end]
    london = [c for c in candles if london_start <= c["dt"] < london_end]

    if not asia or not london:
        return None

    return {
        "date": today_ny.isoformat(),
        "asia_high": max(c["high"] for c in asia),
        "asia_low":  min(c["low"] for c in asia),
        "london_high": max(c["high"] for c in london),
        "london_low":  min(c["low"] for c in london),
        "levels": [
            {"name": "Asia High", "value": max(c["high"] for c in asia), "type": "high"},
            {"name": "Asia Low", "value": min(c["low"] for c in asia), "type": "low"},
            {"name": "London High", "value": max(c["high"] for c in london), "type": "high"},
            {"name": "London Low", "value": min(c["low"] for c in london), "type": "low"},
        ],
    }

def get_session_levels():
    today = ny_now().date()
    cached = get_cache("bot5_session_levels")
    if cached:
        try:
            data = json.loads(cached)
            if data.get("date") == today.isoformat():
                return data
        except: pass
    levels = session_levels_for_today(today)
    if levels:
        set_cache("bot5_session_levels", json.dumps(levels))
    return levels

# ── ICT MANTIĞI: SWEEP + INVERSE FVG ────────────────────────────────────
def detect_sweep(candles, levels_list):
    """Likidite seviyelerini kontrol et. Ilk supurulen seviyeyi dondurur."""
    for c in candles:
        for lv in levels_list:
            if lv["type"] == "high" and c["high"] > lv["value"] and c["close"] < lv["value"]:
                return {"direction": "SHORT", "level": lv["value"], "extreme": c["high"],
                        "time": c["dt"], "swept_level": lv["name"]}
            if lv["type"] == "low" and c["low"] < lv["value"] and c["close"] > lv["value"]:
                return {"direction": "LONG", "level": lv["value"], "extreme": c["low"],
                        "time": c["dt"], "swept_level": lv["name"]}
    return None

def find_fvgs(candles, kind):
    """3 mumluk fair value gap. kind: 'bearish' ya da 'bullish'."""
    fvgs = []
    for i in range(2, len(candles)):
        c1, c2, c3 = candles[i - 2], candles[i - 1], candles[i]
        if kind == "bearish":
            if c1["low"] > c3["high"]:
                fvgs.append({"top": c1["low"], "bottom": c3["high"], "strict": True})
            elif c1["low"] > c3["close"] and (c1["open"] - c1["close"]) > (c3["high"] - c3["low"]) * 0.5:
                fvgs.append({"top": c1["low"], "bottom": c3["high"], "strict": False})
        elif kind == "bullish":
            if c1["high"] < c3["low"]:
                fvgs.append({"top": c3["low"], "bottom": c1["high"], "strict": True})
            elif c1["high"] < c3["close"] and (c1["close"] - c1["open"]) > (c3["high"] - c3["low"]) * 0.5:
                fvgs.append({"top": c3["low"], "bottom": c1["high"], "strict": False})
    return fvgs

def detect_inverse_fvg_entry(candles, sweep):
    """Sweep sonrasi inverse FVG girisini tespit et."""
    sweep_idx = next((i for i, c in enumerate(candles) if c["dt"] == sweep["time"]), None)
    if sweep_idx is None:
        return None
    direction = sweep["direction"]
    pre  = candles[:sweep_idx + 1]
    post = candles[sweep_idx + 1:]
    fvg_kind = "bearish" if direction == "LONG" else "bullish"
    fvgs = find_fvgs(pre, fvg_kind)

    if not fvgs and sweep_idx >= 1:
        sweep_candle = candles[sweep_idx]
        prev_candle = candles[sweep_idx - 1]
        if direction == "LONG":
            zone_top = max(sweep_candle["open"], sweep_candle["close"])
            zone_bottom = min(prev_candle["low"], sweep_candle["low"])
            if zone_top > zone_bottom:
                fvgs.append({"top": zone_top, "bottom": zone_bottom, "strict": False})
        else:
            zone_top = max(prev_candle["high"], sweep_candle["high"])
            zone_bottom = min(sweep_candle["open"], sweep_candle["close"])
            if zone_top > zone_bottom:
                fvgs.append({"top": zone_top, "bottom": zone_bottom, "strict": False})

    if not fvgs:
        return None

    best_fvg = max(fvgs, key=lambda f: f["top"] - f["bottom"])

    for c in post:
        if direction == "LONG" and c["close"] > best_fvg["top"]:
            entry, sl = c["close"], sweep["extreme"]
            risk = entry - sl
            if risk <= 0: continue
            tp = entry + risk * RR_TARGET
            return {"direction": "LONG", "entry": entry, "sl": sl, "tp": tp, "time": c["dt"]}
        if direction == "SHORT" and c["close"] < best_fvg["bottom"]:
            entry, sl = c["close"], sweep["extreme"]
            risk = sl - entry
            if risk <= 0: continue
            tp = entry - risk * RR_TARGET
            return {"direction": "SHORT", "entry": entry, "sl": sl, "tp": tp, "time": c["dt"]}
    return None

def detect_fvg_forced(candles, sweep, forced_direction, sl_price):
    """v5: Sweep seviyesine gore yonu zorla ve SL'i ayarla."""
    modified_sweep = {
        "direction": forced_direction,
        "level": sweep["level"],
        "extreme": sl_price,
        "time": sweep["time"],
        "swept_level": sweep.get("swept_level", "?")
    }
    return detect_inverse_fvg_entry(candles, modified_sweep)

# ── SCAN (HYBRID-2) ─────────────────────────────────────────────────────
def run_scan():
    print("🔍 Bot5 Scan başlıyor (NASDAQ / NQ — ICT Hybrid-2)...")
    now = ny_now()

    if now.weekday() >= 5:
        print("  Hafta sonu — piyasa kapalı."); return

    session_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    session_end   = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if not (session_start <= now <= session_end):
        print(f"  Aktif tarama penceresi dışında (09:30-12:00 NY). Şu an: {now.strftime('%H:%M')} NY"); return

    if get_open_trade():
        print("  Açık NQ işlemi var — scan atlandı."); return

    levels = get_session_levels()
    if not levels:
        print("  Asia/London seviyeleri hesaplanamadı."); return

    levels_list = levels["levels"]
    print(f"  Seviyeler → Asia H/L:{levels['asia_high']:.2f}/{levels['asia_low']:.2f} "
          f"London H/L:{levels['london_high']:.2f}/{levels['london_low']:.2f}")

    # ── HYBRID-2: İki zaman dilimi çek ──
    # London High → 15dk mumlarında sweep + FVG
    # London Low  → 5dk mumlarında sweep + FVG
    candles_5m = get_candles_yfinance("5m", "1d")
    candles_15m = get_candles_yfinance("15m", "1d")

    candles_5m  = [c for c in candles_5m  if c["dt"] >= session_start]
    candles_15m = [c for c in candles_15m if c["dt"] >= session_start]

    if len(candles_5m) < 5 and len(candles_15m) < 3:
        print("  Yeterli mum yok (5m/15m)."); return

    # Asia High/Low sweep kontrolu (5dk uzerinde — sadece skip icin)
    asia_levels = [lv for lv in levels_list if lv["name"] in ("Asia High", "Asia Low")]
    sweep_asia = detect_sweep(candles_5m, asia_levels) if candles_5m else None

    # London High sweep kontrolu (15dk)
    lon_high_level = [lv for lv in levels_list if lv["name"] == "London High"]
    sweep_lon_high_15m = detect_sweep(candles_15m, lon_high_level) if candles_15m else None

    # London Low sweep kontrolu (5dk)
    lon_low_level = [lv for lv in levels_list if lv["name"] == "London Low"]
    sweep_lon_low_5m = detect_sweep(candles_5m, lon_low_level) if candles_5m else None

    # En erken sweep'i bul
    all_sweeps = []
    if sweep_asia:
        all_sweeps.append(("ASIA", sweep_asia))
    if sweep_lon_high_15m:
        all_sweeps.append(("LON_HIGH_15M", sweep_lon_high_15m))
    if sweep_lon_low_5m:
        all_sweeps.append(("LON_LOW_5M", sweep_lon_low_5m))

    if not all_sweeps:
        print("  Henüz likidite avı (sweep) yok."); return

    all_sweeps.sort(key=lambda x: x[1]["time"])
    earliest_type, earliest_sweep = all_sweeps[0]

    swept = earliest_sweep.get("swept_level", "?")
    print(f"  🎯 İlk Sweep: {swept} @ {earliest_sweep['level']:.2f} (ext: {earliest_sweep['extreme']:.2f}) @{earliest_sweep['time'].strftime('%H:%M')} [{earliest_type}]")

    # ── v5-HYBRID2 FİLTRELER ──
    if earliest_type == "ASIA":
        print(f"  ⏭️ {swept} süpürüldü — v5 Hybrid-2'de Asia seviyeleri ATLANIR."); return

    if earliest_type == "LON_HIGH_15M":
        forced_dir = "SHORT"
        sl_price = earliest_sweep["extreme"]
        strategy = "NORMAL (15dk)"
        entry_candles = candles_15m
        sweep = earliest_sweep
        print(f"  → London High NORMAL: SHORT (15dk) | SL = sweep extreme ({sl_price:.2f})")
    elif earliest_type == "LON_LOW_5M":
        forced_dir = "SHORT"
        sl_price = levels["london_high"]
        strategy = "TERS (5dk)"
        entry_candles = candles_5m
        sweep = earliest_sweep
        print(f"  → London Low TERS: SHORT (5dk) | SL = London High ({sl_price:.2f})")
    else:
        print(f"  ⏭️ Bilinmeyen seviye: {swept}"); return

    signal = detect_fvg_forced(entry_candles, sweep, forced_dir, sl_price)
    if not signal:
        print(f"  Sweep sonrası henüz inverse FVG girişi oluşmadı — bekleniyor ({strategy})."); return

    direction, entry, sl, tp = signal["direction"], signal["entry"], signal["sl"], signal["tp"]
    rr = RR_TARGET

    trade_data = {
        "symbol": SYMBOL, "direction": direction, "entry_price": round(entry, 2),
        "tp": round(tp, 2), "sl": round(sl, 2), "original_sl": round(sl, 2),
        "rr": rr, "score": 0, "status": "OPEN",
        "sl_moved_breakeven": False, "sl_moved_profit": False, "tp_extended": False,
        "open_time": datetime.now(timezone.utc).isoformat(), "close_time": None, "result_pct": None,
        "notes": json.dumps({
            "strategy": f"v5-Hybrid2: {strategy} ({swept})",
            "swept_level": swept,
            "sweep_level": sweep["level"], "sweep_extreme": sweep["extreme"],
            "forced_direction": forced_dir,
            "tf_used": "15m" if "15dk" in strategy else "5m",
            "asia_high": levels["asia_high"], "asia_low": levels["asia_low"],
            "london_high": levels["london_high"], "london_low": levels["london_low"],
        })
    }
    created = create_trade(trade_data)
    if not created:
        print("  ❌ DB'ye kaydedilemedi"); return

    dir_str = "📈 LONG 🟢" if direction == "LONG" else "📉 SHORT 🔴"
    sl_pct = abs(entry - sl) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    tf_label = "15dk" if "15dk" in strategy else "5dk"
    send_telegram(
        f"🚨 *BOT 5 — NASDAQ (NQ) ICT SİNYALİ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *{SYMBOL_DISPLAY}* | {dir_str}\n"
        f"📍 Giriş: `{entry:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *TP*: `{tp:.2f}` (+{tp_pct:.3f}%)\n"
        f"🛡️ *SL*: `{sl:.2f}` (-{sl_pct:.3f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌏 *Asia* → H: `{levels['asia_high']:.2f}` | L: `{levels['asia_low']:.2f}`\n"
        f"🇬🇧 *London* → H: `{levels['london_high']:.2f}` | L: `{levels['london_low']:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *R:R* → {rr:.1f}R\n"
        f"🧠 Likidite Avı: {swept} @ {sweep['level']:.2f} [{strategy}]\n"
        f"📊 Zaman Dilimi: {tf_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 *Bot 5 — NASDAQ ICT Scalper (Hybrid-2)*"
    )
    print(f"  ✅ Sinyal gönderildi: {direction} @ {entry:.2f} [{tf_label}]")

# ── WATCHDOG ─────────────────────────────────────────────────────────────
def run_watchdog():
    print("👁️ Bot5 Watchdog başlıyor...")
    trade = get_open_trade()
    if not trade:
        print("  Açık NQ işlemi yok."); return

    price = get_price()
    if not price:
        print("  Fiyat alınamadı."); return

    trade_id  = trade["id"]
    entry     = float(trade["entry_price"])
    sl        = float(trade["sl"])
    tp        = float(trade["tp"])
    direction = trade["direction"]
    result_pct  = ((price - entry) / entry * 100) if direction == "LONG" else ((entry - price) / entry * 100)

    print(f"  {direction} | Giriş:{entry:.2f} | Şimdi:{price:.2f} | SL:{sl:.2f} | TP:{tp:.2f} | Sonuç:{result_pct:+.3f}%")

    updates, notify_msg = {}, None

    hit_sl = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)
    hit_tp = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)

    if hit_sl:
        updates = {"status": "SL_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (f"🛑 *NASDAQ SL ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💸 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 5 — NASDAQ ICT*")
    elif hit_tp:
        updates = {"status": "TP_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (f"✅ *NASDAQ TP ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💰 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 5 — NASDAQ ICT*")

    if updates:
        update_trade(trade_id, updates)
        print(f"  İşlem güncellendi: {updates}")
    if notify_msg:
        send_telegram(notify_msg)

# ── MAIN ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    MODE = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if MODE == "scan":
        run_scan()
    elif MODE == "watchdog":
        run_watchdog()
    else:
        print(f"Bilinmeyen mod: {MODE}")
