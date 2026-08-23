#!/usr/bin/env python3
"""
Bot 1 — BTC/USD PDH/PDL Liquidity Sweep + Inverse FVG Bot (Strateji D)
Backtest: 3 yil Binance verisi — 246 islem, %37-49 WR, +29-121% P&L (cikis zaman dilimine gore)

Strateji D (Previous Day Low Liquidity Sweep):
  1. Onceki gunun daily high/low'larini hesapla (5dk mumlarindan).
  2. Tum gun boyunca (24 saat) likidite avi (sweep) bekle — NY saati bekleme yok.
  3. FILTRELER:
     - Previous Day High (PDH) supurulurse → ISLEM YAPMA (atlanir)
     - Previous Day Low (PDL) supurulurse → 5dk mumlarinda LONG (SL: sweep extreme)
  4. Giris: inverse FVG kirilimi. TP: 1:2 RR.
  5. Islem SL/TP olana kadar acik kalir (coklu gun).

Neden PDL→LONG?
  - BTC fundamental olarak yukselis trendinde → dip alimi mantigi
  - PDH sweep SHORT 3 yilda neredeyse break-even (-1.79%)
  - PDL sweep LONG 3 yilda +29% (5dk cikis) / +121% (1dk cikis)
  - 6 aylik testte tek pozitif strateji (+1.90%)

Veri: Binance Futures API (BTCUSDT).
Veritabani: Base44 ActiveTrade / BotCache entity'leri, api_key header.
Telegram: TELEGRAM_BOT_TOKEN_2 (@Bot).
"""

import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_2", "")
CHAT_ID        = "2055780815"
BASE44_TOKEN   = os.environ.get("BASE44_API_KEY", "")
APP_ID         = "6a1d973568af9b984e0f1cc8"
SYMBOL         = "BTCUSDT"
SYMBOL_DISPLAY = "BTC/USD (Bitcoin)"
RR_TARGET      = 2.0

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"

BASE_URL = f"https://app.base44.com/api/apps/{APP_ID}/entities"
HEADERS  = lambda: {"api_key": BASE44_TOKEN, "Content-Type": "application/json"}

# ── BINANCE API ────────────────────────────────────────────────────────
def get_price():
    try:
        r = requests.get(f"{BINANCE_FAPI}/ticker/price", params={"symbol": SYMBOL}, timeout=8)
        if r.status_code == 200: return float(r.json()["price"])
    except Exception as e:
        print(f"  Fiyat hatasi: {e}")
    return None

def get_candles(interval, limit=500):
    try:
        r = requests.get(f"{BINANCE_FAPI}/klines", params={
            "symbol": SYMBOL, "interval": interval, "limit": str(limit)
        }, timeout=10)
        if r.status_code == 200:
            candles = []
            for c in r.json():
                dt = datetime.fromtimestamp(c[0] / 1000, tz=UTC).astimezone(NY)
                candles.append({"dt": dt, "open": float(c[1]), "high": float(c[2]),
                                "low": float(c[3]), "close": float(c[4])})
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
                if item.get("key") == key: return item.get("value")
    except: pass
    return None

def set_cache(key, value):
    try:
        r = requests.get(f"{BASE_URL}/BotCache", headers=HEADERS(), params={"key": key}, timeout=8)
        existing = None
        if r.status_code == 200:
            for item in r.json():
                if item.get("key") == key: existing = item; break
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

# ── ZAMAN / PREVIOUS DAY HIGH/LOW ───────────────────────────────────────
def ny_now():
    return datetime.now(NY)

def get_pdh_pdl():
    """Onceki gunun daily high/low'larini hesapla (NY gunu baz alinir).
    Cache'li — gun basinda bir kere hesaplanir."""
    today = ny_now().date()
    cached = get_cache("bot1_pdh_pdl")
    if cached:
        try:
            data = json.loads(cached)
            if data.get("date") == today.isoformat(): return data
        except: pass

    # 5dk mumlar getir (2 gunluk — onceki gunu kapsar)
    candles = get_candles("5m", 600)
    if not candles: return None

    prev_day = today - timedelta(days=1)
    prev_start = datetime.combine(prev_day, dtime(0, 0), tzinfo=NY)
    prev_end   = datetime.combine(today, dtime(0, 0), tzinfo=NY)

    prev_candles = [c for c in candles if prev_start <= c["dt"] < prev_end]
    if not prev_candles:
        return None

    levels = {
        "date": today.isoformat(),
        "pdh": max(c["high"] for c in prev_candles),
        "pdl": min(c["low"] for c in prev_candles),
    }
    set_cache("bot1_pdh_pdl", json.dumps(levels))
    return levels

# ── ICT MANTIĞI: SWEEP + INVERSE FVG ────────────────────────────────────
def detect_pdl_sweep(candles, pdl):
    """PDL (Previous Day Low) supurulmus mu?
    Low < PDL ve close > PDL → likidite alindi, LONG sinyali."""
    for c in candles:
        if c["low"] < pdl and c["close"] > pdl:
            return {"direction": "LONG", "level": pdl, "extreme": c["low"],
                    "time": c["dt"], "swept_level": "PDL"}
    return None

def detect_pdh_sweep(candles, pdh):
    """PDH (Previous Day High) supurulmus mu?
    High > PDH ve close < PDH → likidite alindi, SHORT sinyali."""
    for c in candles:
        if c["high"] > pdh and c["close"] < pdh:
            return {"direction": "SHORT", "level": pdh, "extreme": c["high"],
                    "time": c["dt"], "swept_level": "PDH"}
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
    if sweep_idx is None: return None
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

    if not fvgs: return None
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

# ── SCAN (STRATEJI D: PDL → LONG) ─────────────────────────────────────
def run_scan():
    print("🔍 Bot1 Scan basliyor (BTC/USD — PDH/PDL Strateji D)...")
    now = ny_now()

    if get_open_trade():
        print("  Acik BTC islemi var — scan atlandi."); return

    levels = get_pdh_pdl()
    if not levels:
        print("  Onceki gun PDH/PDL hesaplanamadi."); return

    pdh = levels["pdh"]
    pdl = levels["pdl"]
    print(f"  PDH: {pdh:.2f} | PDL: {pdl:.2f} | Bugun: {now.strftime('%Y-%m-%d %H:%M')} NY")

    # 5dk mumlar getir (bugunun tum mumlari)
    candles_5m = get_candles("5m", 500)
    candles_5m = [c for c in candles_5m if c["dt"].date() == now.date()]
    if len(candles_5m) < 5:
        print("  Yeterli 5dk mum yok."); return

    # ── Strateji D: Sadece PDL sweep → LONG ──
    # PDH sweep'i kontrol et (atlanir)
    pdh_sweep = detect_pdh_sweep(candles_5m, pdh)
    if pdh_sweep:
        print(f"  ⏭️ PDH supuruldu ({pdh:.2f}) — D stratejisinde atlanir.")
        return

    # PDL sweep'i kontrol et
    pdl_sweep = detect_pdl_sweep(candles_5m, pdl)
    if not pdl_sweep:
        print("  Henuz PDL (Previous Day Low) supurulmedi — bekleniyor.")
        return

    swept = pdl_sweep["swept_level"]
    print(f"  🎯 PDL Supuruldu: {pdl:.2f} (ext: {pdl_sweep['extreme']:.2f}) "
          f"@{pdl_sweep['time'].strftime('%H:%M')} NY")

    # FVG giris tespiti (5dk)
    signal = detect_inverse_fvg_entry(candles_5m, pdl_sweep)
    if not signal:
        print(f"  Sweep sonrasi henuz inverse FVG girisi olusmadi — bekleniyor.")
        return

    direction, entry, sl, tp = signal["direction"], signal["entry"], signal["sl"], signal["tp"]
    rr = RR_TARGET

    trade_data = {
        "symbol": SYMBOL, "direction": direction, "entry_price": round(entry, 2),
        "tp": round(tp, 2), "sl": round(sl, 2), "original_sl": round(sl, 2),
        "rr": rr, "score": 0, "status": "OPEN",
        "sl_moved_breakeven": False, "sl_moved_profit": False, "tp_extended": False,
        "open_time": datetime.now(timezone.utc).isoformat(), "close_time": None, "result_pct": None,
        "notes": json.dumps({
            "strategy": "D: PDL Sweep LONG (5dk)",
            "swept_level": "PDL",
            "sweep_level": pdl, "sweep_extreme": pdl_sweep["extreme"],
            "pdh": pdh, "pdl": pdl,
            "tf_used": "5m",
        })
    }
    created = create_trade(trade_data)
    if not created:
        print("  ❌ DB'ye kaydedilemedi"); return

    dir_str = "📈 LONG 🟢" if direction == "LONG" else "📉 SHORT 🔴"
    sl_pct = abs(entry - sl) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    send_telegram(
        f"🚨 *BOT 1 — BTC/USD ICT SİNYALİ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"₿ *{SYMBOL_DISPLAY}* | {dir_str}\n"
        f"📍 Giriş: `{entry:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *TP*: `{tp:.2f}` (+{tp_pct:.3f}%)\n"
        f"🛡️ *SL*: `{sl:.2f}` (-{sl_pct:.3f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *PDH*: `{pdh:.2f}` | *PDL*: `{pdl:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *R:R* → {rr:.1f}R\n"
        f"🧠 Likidite Avı: {swept} @ {pdl:.2f}\n"
        f"📈 Zaman Dilimi: 5dk\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 *Bot 1 — BTC ICT (Strateji D)*"
    )
    print(f"  ✅ Sinyal gonderildi: {direction} @ {entry:.2f} [5dk]")

# ── WATCHDOG ─────────────────────────────────────────────────────────────
def run_watchdog():
    print("👁️ Bot1 Watchdog basliyor...")
    trade = get_open_trade()
    if not trade:
        print("  Acik BTC islemi yok."); return

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
        notify_msg = (f"🛑 *BTC/USD SL ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💸 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 1 — BTC ICT*")
    elif hit_tp:
        updates = {"status": "TP_HIT", "close_time": datetime.now(timezone.utc).isoformat(),
                   "result_pct": round(result_pct, 4)}
        notify_msg = (f"✅ *BTC/USD TP ULAŞTI*\n━━━━━━━━━━━━━━━━━━\n"
                      f"📍 Giriş: `{entry:.2f}` | Çıkış: `{price:.2f}`\n"
                      f"💰 Sonuç: `{result_pct:+.3f}%`\n"
                      f"━━━━━━━━━━━━━━━━━━\n📡 *Bot 1 — BTC ICT*")

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
