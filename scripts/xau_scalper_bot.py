#!/usr/bin/env python3
"""
Bot 3 — XAU/USD ICT Liquidity Sweep + Inverse FVG Bot (Strateji B1)
Backtest: 254 gun Binance verisi — 53 islem, 21 TP, 32 SL, %40 WR, +5.155% P&L, PF 1.48

Strateji B1:
  1. Asia (20:00-00:00 NY) ve London (00:00-05:00 NY) seans high/low'larini 5dk'dan hesapla.
  2. NY acilisinda (09:30-12:00) likidite avi (sweep) bekle.
  3. FILTRELER:
     - Asia High/Low supurulurse → ISLEM YAPMA (atlanir)
     - London High supurulurse → 15dk mumlarinda SHORT (SL: sweep extreme)
     - London Low supurulurse → 5dk mumlarinda LONG (SL: sweep extreme)
  4. Giris: inverse FVG kirilimi. TP: 1:2 RR.
  5. Islem SL/TP olana kadar acik kalir (coklu gun).

Veri: Binance Futures API (XAUUSDT).
Veritabani: Base44 ActiveTrade / BotCache entity'leri, api_key header.
Telegram: TELEGRAM_BOT_TOKEN_7 (@XAUmmT_bot).
"""

import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_7", "")
CHAT_ID        = "2055780815"
BASE44_TOKEN   = os.environ.get("BASE44_API_KEY", "")
APP_ID         = "6a1d973568af9b984e0f1cc8"
SYMBOL         = "XAUUSDT"
SYMBOL_DISPLAY = "XAU/USD (Gold)"
RR_TARGET      = 2.0

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"

BASE_URL = f"https://app.base44.com/api/apps/{APP_ID}/entities"
HEADERS  = lambda: {"api_key": BASE44_TOKEN, "Content-Type": "application/json"}

# ── BINANCE API ────────────────────────────────────────────────────────
def get_price():
    """Binance Futures'ten XAUUSDT son fiyat."""
    try:
        r = requests.get(f"{BINANCE_FAPI}/ticker/price", params={"symbol": SYMBOL}, timeout=8)
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception as e:
        print(f"  Fiyat hatasi: {e}")
    return None

def get_candles(interval, limit=500):
    """Binance Futures klines. interval: '5m', '15m'. Datetime NY-aware."""
    try:
        r = requests.get(f"{BINANCE_FAPI}/klines", params={
            "symbol": SYMBOL, "interval": interval, "limit": str(limit)
        }, timeout=10)
        if r.status_code == 200:
            candles = []
            for c in r.json():
                dt = datetime.fromtimestamp(c[0] / 1000, tz=UTC).astimezone(NY)
                candles.append({
                    "dt": dt,
                    "open": float(c[1]), "high": float(c[2]),
                    "low": float(c[3]), "close": float(c[4])
                })
            return candles
    except Exception as e:
        print(f"  Mum verisi hatasi: {e}")
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
        print("  ✅ Telegram gonderildi" if r.status_code == 200 else f"  ❌ Telegram hata: {r.status_code}")
    except Exception as e:
        print(f"  ❌ Telegram exception: {e}")

# ── ZAMAN / SESSION YARDIMCILARI ────────────────────────────────────────
def ny_now():
    return datetime.now(NY)

def get_session_levels():
    """Asia (dun 20:00 - bugun 00:00) + London (bugun 00:00 - 05:00) high/low.
    Seviyeler 5dk mumlarindan hesaplanir. Cache'li."""
    today = ny_now().date()
    cached = get_cache("bot3_session_levels")
    if cached:
        try:
            data = json.loads(cached)
            if data.get("date") == today.isoformat():
                return data
        except: pass

    # 5dk mumlar getir (2 gunluk — Asia session dunu kapsar)
    candles = get_candles("5m", 600)
    if not candles:
        return None

    asia_start = datetime.combine(today - timedelta(days=1), dtime(20, 0), tzinfo=NY)
    asia_end   = datetime.combine(today, dtime(0, 0), tzinfo=NY)
    london_start = asia_end
    london_end   = datetime.combine(today, dtime(5, 0), tzinfo=NY)

    asia   = [c for c in candles if asia_start <= c["dt"] < asia_end]
    london = [c for c in candles if london_start <= c["dt"] < london_end]

    if not asia or not london:
        return None

    levels = {
        "date": today.isoformat(),
        "asia_high": max(c["high"] for c in asia),
        "asia_low": min(c["low"] for c in asia),
        "london_high": max(c["high"] for c in london),
        "london_low": min(c["low"] for c in london),
    }
    set_cache("bot3_session_levels", json.dumps(levels))
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
    """3 mumluk fair value gap. kind: 'bearish' veya 'bullish'."""
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

    # Fallback: 2 mumluk zone
    if not fvgs and sweep_idx >= 1:
        sc = candles[sweep_idx]
        pc = candles[sweep_idx - 1]
        if direction == "LONG":
            zt = max(sc["open"], sc["close"])
            zb = min(pc["low"], sc["low"])
            if zt > zb: fvgs.append({"top": zt, "bottom": zb, "strict": False})
        else:
            zt = max(pc["high"], sc["high"])
            zb = min(sc["open"], sc["close"])
            if zt > zb: fvgs.append({"top": zt, "bottom": zb, "strict": False})

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

# ── SCAN (STRATEJI B1) ─────────────────────────────────────────────────
def run_scan():
    print("🔍 Bot3 Scan basliyor (XAU/USD — ICT Strateji B1)...")
    now = ny_now()

    if now.weekday() >= 5:
        print("  Hafta sonu — piyasa kapali."); return

    session_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    session_end   = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if not (session_start <= now <= session_end):
        print(f"  Aktif tarama penceresi disinda (09:30-12:00 NY). Su an: {now.strftime('%H:%M')} NY")
        return

    if get_open_trade():
        print("  Acik XAU islemi var — scan atlandi."); return

    levels = get_session_levels()
    if not levels:
        print("  Asia/London seviyeleri hesaplanamadi."); return

    print(f"  Seviyeler → Asia H/L:{levels['asia_high']:.2f}/{levels['asia_low']:.2f} "
          f"London H/L:{levels['london_high']:.2f}/{levels['london_low']:.2f}")

    # ── Strateji B1: London High → 15dk SHORT, London Low → 5dk LONG ──
    candles_5m  = get_candles("5m", 500)
    candles_15m = get_candles("15m", 200)

    # NY session mumlarini filtrele
    candles_5m  = [c for c in candles_5m  if c["dt"].date() == now.date() and c["dt"] >= session_start]
    candles_15m = [c for c in candles_15m if c["dt"].date() == now.date() and c["dt"] >= session_start]

    if len(candles_5m) < 5:
        print("  Yeterli 5dk mum yok."); return
    if len(candles_15m) < 3:
        print("  Yeterli 15dk mum yok."); return

    # Asia H/L sweep (5dk — sadece skip icin)
    asia_levels = [
        {"name": "Asia High", "value": levels["asia_high"], "type": "high"},
        {"name": "Asia Low", "value": levels["asia_low"], "type": "low"},
    ]
    sweep_asia = detect_sweep(candles_5m, asia_levels)

    # London High sweep (15dk — SHORT islem)
    lon_high_level = [{"name": "London High", "value": levels["london_high"], "type": "high"}]
    sweep_lon_high = detect_sweep(candles_15m, lon_high_level)

    # London Low sweep (5dk — LONG islem)
    lon_low_level = [{"name": "London Low", "value": levels["london_low"], "type": "low"}]
    sweep_lon_low = detect_sweep(candles_5m, lon_low_level)

    # En erken sweep'i bul
    all_sweeps = []
    if sweep_asia: all_sweeps.append(("ASIA", sweep_asia))
    if sweep_lon_high: all_sweeps.append(("LON_HIGH", sweep_lon_high))
    if sweep_lon_low: all_sweeps.append(("LON_LOW", sweep_lon_low))

    if not all_sweeps:
        print("  Henuz likidite avi (sweep) yok."); return

    all_sweeps.sort(key=lambda x: x[1]["time"])
    earliest_type, earliest_sweep = all_sweeps[0]

    swept = earliest_sweep.get("swept_level", "?")
    print(f"  🎯 Ilk Sweep: {swept} @ {earliest_sweep['level']:.2f} "
          f"(ext: {earliest_sweep['extreme']:.2f}) @{earliest_sweep['time'].strftime('%H:%M')} [{earliest_type}]")

    # ── B1 FILTRELER ──
    if earliest_type == "ASIA":
        print(f"  ⏭️ {swept} supuruldu — Asia seviyeleri B1'de atlanir."); return

    if earliest_type == "LON_HIGH":
        # London High → 15dk mumlarinda SHORT
        forced_dir = "SHORT"
        sl_price = earliest_sweep["extreme"]
        strategy = "B1: London High SHORT (15dk)"
        entry_candles = candles_15m
        sweep = earliest_sweep
        print(f"  → London High: SHORT (15dk) | SL = sweep extreme ({sl_price:.2f})")

    elif earliest_type == "LON_LOW":
        # London Low → 5dk mumlarinda LONG
        forced_dir = "LONG"
        sl_price = earliest_sweep["extreme"]
        strategy = "B1: London Low LONG (5dk)"
        entry_candles = candles_5m
        sweep = earliest_sweep
        print(f"  → London Low: LONG (5dk) | SL = sweep extreme ({sl_price:.2f})")

    else:
        print(f"  ⏭️ Bilinmeyen: {swept}"); return

    # FVG giris tespiti
    mod_sweep = {
        "direction": forced_dir,
        "level": sweep["level"],
        "extreme": sl_price,
        "time": sweep["time"],
        "swept_level": sweep.get("swept_level", "?")
    }
    signal = detect_inverse_fvg_entry(entry_candles, mod_sweep)
    if not signal:
        print(f"  Sweep sonrasi henuz inverse FVG girisi olusmadi — bekleniyor."); return

    direction, entry, sl, tp = signal["direction"], signal["entry"], signal["sl"], signal["tp"]
    rr = RR_TARGET

    trade_data = {
        "symbol": SYMBOL, "direction": direction, "entry_price": round(entry, 2),
        "tp": round(tp, 2), "sl": round(sl, 2), "original_sl": round(sl, 2),
        "rr": rr, "score": 0, "status": "OPEN",
        "sl_moved_breakeven": False, "sl_moved_profit": False, "tp_extended": False,
        "open_time": datetime.now(timezone.utc).isoformat(), "close_time": None, "result_pct": None,
        "notes": json.dumps({
            "strategy": strategy,
            "swept_level": swept,
            "sweep_level": sweep["level"], "sweep_extreme": sweep["extreme"],
            "tf_used": "15m" if forced_dir == "SHORT" else "5m",
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
    send_telegram(
        f"🚨 *BOT 3 — XAU/USD ICT SİNYALİ*\n"
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
        f"🧠 Likidite Avı: {swept} @ {sweep['level']:.2f}\n"
        f"📊 Zaman Dilimi: {'15dk' if forced_dir == 'SHORT' else '5dk'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 *Bot 3 — XAU ICT (Strateji B1)*"
    )
    print(f"  ✅ Sinyal gonderildi: {direction} @ {entry:.2f} [{'15dk' if forced_dir == 'SHORT' else '5dk'}]")

# ── WATCHDOG ─────────────────────────────────────────────────────────────
def run_watchdog():
    print("👁️ Bot3 Watchdog basliyor...")
    trade = get_open_trade()
    if not trade:
        print("  Acik XAU islemi yok."); return

    price = get_price()
    if not price:
        print("  Fiyat alinamadi."); return

    trade_id  = trade["id"]
    entry     = float(trade["entry_price"])
    sl        = float(trade["sl"])
    tp        = float(trade["tp"])
    direction = trade["direction"]
    result_pct = ((price - entry) / entry * 100) if direction == "LONG" else ((entry - price) / entry * 100)

    print(f"  {direction} | Giris:{entry:.2f} | Simdi:{price:.2f} | SL:{sl:.2f} | TP:{tp:.2f} | Sonuc:{result_pct:+.3f}%")

    updates, notify_msg = {}, None

    hit_sl = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)
    hit_tp = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)

    if hit_sl:
        updates = {"status": "SL_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (f"🛑 *XAU/USD SL ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💸 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 3 — XAU ICT*")
    elif hit_tp:
        updates = {"status": "TP_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (f"✅ *XAU/USD TP ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💰 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 3 — XAU ICT*")

    if updates:
        update_trade(trade_id, updates)
        print(f"  Islem guncellendi: {updates}")
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
