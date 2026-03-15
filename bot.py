import os
import time
import requests
import json
from datetime import datetime, timezone

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID   = os.environ.get("CHAT_ID",   "YOUR_CHAT_ID_HERE")

# ── STRATEGY PARAMETERS (bisa diubah) ───────────────────
MIN_PUMP_PCT       = 150    # Minimal pump awal (%)
MAX_DIP_PCT        = 70     # Maksimal dip yang diizinkan (%)
MIN_VOLUME_USD     = 50000  # Minimal volume USD
MIN_LIQUIDITY_USD  = 10000  # Minimal likuiditas pool
SCAN_INTERVAL_SEC  = 30     # Scan tiap berapa detik
ALERT_COOLDOWN_SEC = 300    # Jangan alert token sama dalam 5 menit

# ── STATE ────────────────────────────────────────────────
alerted_tokens = {}   # token_address -> timestamp terakhir alert
pump_tracker   = {}   # token_address -> data pump pertama

# ── TELEGRAM ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# ── DEX SCREENER API ─────────────────────────────────────
def get_trending_solana_tokens():
    """Ambil token trending di Solana dari DEX Screener"""
    try:
        url = "https://api.dexscreener.com/token-boosts/latest/v1"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        # Filter hanya Solana
        solana_tokens = [t for t in data if t.get("chainId") == "solana"]
        return solana_tokens[:50]
    except Exception as e:
        print(f"[SCAN ERROR] {e}")
        return []

def get_token_pairs(token_address: str):
    """Ambil data pair & candle token"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return None
        # Ambil pair dengan volume terbesar
        pairs_sorted = sorted(pairs, key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)
        return pairs_sorted[0]
    except Exception as e:
        print(f"[PAIR ERROR] {e}")
        return None

def get_new_tokens_solana():
    """Ambil token baru di Solana (listing terbaru)"""
    try:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        solana = [t for t in data if t.get("chainId") == "solana"]
        return solana[:30]
    except Exception as e:
        print(f"[NEW TOKEN ERROR] {e}")
        return []

# ── PATTERN DETECTION ─────────────────────────────────────
def analyze_pair(pair: dict) -> dict | None:
    """
    Analisa pair untuk pola Dip & Rip:
    1. Cek ada pump awal (h1 atau m5 besar)
    2. Cek price sekarang vs high (apakah sudah dip)
    3. Cek apakah dip tidak tembus harga awal
    4. Cek volume masih bagus
    """
    try:
        token_address = pair.get("baseToken", {}).get("address", "")
        token_name    = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_address  = pair.get("pairAddress", "")
        chain_id      = pair.get("chainId", "")
        dex_id        = pair.get("dexId", "")

        price_usd     = float(pair.get("priceUsd", 0) or 0)
        price_native  = float(pair.get("priceNative", 0) or 0)

        # Price changes
        price_change  = pair.get("priceChange", {})
        change_m5     = float(price_change.get("m5",  0) or 0)
        change_h1     = float(price_change.get("h1",  0) or 0)
        change_h6     = float(price_change.get("h6",  0) or 0)
        change_h24    = float(price_change.get("h24", 0) or 0)

        # Volume
        volume        = pair.get("volume", {})
        vol_h1        = float(volume.get("h1",  0) or 0)
        vol_h24       = float(volume.get("h24", 0) or 0)

        # Liquidity
        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

        # Txns
        txns_h1       = pair.get("txns", {}).get("h1", {})
        buys_h1       = int(txns_h1.get("buys", 0) or 0)
        sells_h1      = int(txns_h1.get("sells", 0) or 0)

        # ── FILTER DASAR ──────────────────────────────────
        if price_usd <= 0:            return None
        if liquidity_usd < MIN_LIQUIDITY_USD: return None
        if vol_h1 < MIN_VOLUME_USD:   return None

        # ── DETEKSI PUMP AWAL ─────────────────────────────
        # Pump valid jika h24 besar tapi sekarang sedang koreksi
        pump_pct = change_h24
        if pump_pct < MIN_PUMP_PCT:
            # Coba lihat dari h6
            pump_pct = change_h6
            if pump_pct < MIN_PUMP_PCT:
                return None

        # ── DETEKSI DIP ───────────────────────────────────
        # Sekarang harga turun dari high (h1 negatif atau m5 negatif)
        # Tapi h24 masih positif besar = sudah pump, sekarang dip
        currently_dipping = (change_h1 < -10) or (change_m5 < -5)
        if not currently_dipping:
            return None

        # Estimasi dip depth dari high
        # High kira-kira = price_usd / (1 + change_h1/100) jika h1 negatif
        if change_h1 < 0:
            estimated_high = price_usd / (1 + change_h1 / 100)
            dip_from_high  = ((estimated_high - price_usd) / estimated_high) * 100
        else:
            dip_from_high = abs(change_m5)

        if dip_from_high > MAX_DIP_PCT:
            return None   # Dip terlalu dalam, structure mungkin rusak

        if dip_from_high < 15:
            return None   # Belum dip cukup dalam, belum waktunya entry

        # ── CEK OPEN CANDLE PERTAMA (APPROX) ──────────────
        # Jika h24 masih positif besar = harga sekarang masih di atas open 24h
        holds_open_c1 = change_h24 > 20  # Masih +20% dari 24h ago

        # ── SIGNAL STRENGTH ───────────────────────────────
        strength = 0
        if pump_pct > 200: strength += 3
        elif pump_pct > 150: strength += 2
        else: strength += 1

        if dip_from_high > 30: strength += 2
        if holds_open_c1: strength += 3
        if buys_h1 > sells_h1: strength += 2  # Masih ada buyer
        if vol_h1 > 100000: strength += 1

        if strength < 5:
            return None   # Signal terlalu lemah

        # ── HITUNG LEVELS ─────────────────────────────────
        sl_price  = price_usd * 0.85   # SL -15%
        tp1_price = price_usd * 2.0    # TP1 +100%
        tp2_price = price_usd * 3.0    # TP2 +200%

        chart_url = f"https://dexscreener.com/{chain_id}/{pair_address}"

        return {
            "token_address": token_address,
            "token_name":    token_name,
            "token_symbol":  token_symbol,
            "pair_address":  pair_address,
            "price_usd":     price_usd,
            "pump_pct":      round(pump_pct, 1),
            "dip_pct":       round(dip_from_high, 1),
            "holds_open_c1": holds_open_c1,
            "vol_h1":        vol_h1,
            "vol_h24":       vol_h24,
            "liquidity_usd": liquidity_usd,
            "buys_h1":       buys_h1,
            "sells_h1":      sells_h1,
            "change_h24":    change_h24,
            "strength":      strength,
            "sl_price":      sl_price,
            "tp1_price":     tp1_price,
            "tp2_price":     tp2_price,
            "chart_url":     chart_url,
            "dex_id":        dex_id,
        }

    except Exception as e:
        print(f"[ANALYZE ERROR] {e}")
        return None

# ── FORMAT ALERT MESSAGE ──────────────────────────────────
def format_alert(signal: dict) -> str:
    strength_stars = "⭐" * min(signal["strength"], 5)
    holds_emoji    = "✅" if signal["holds_open_c1"] else "⚠️"
    buy_sell_ratio = signal["buys_h1"] / max(signal["sells_h1"], 1)

    vol_h1_fmt  = f"${signal['vol_h1']:,.0f}"
    vol_h24_fmt = f"${signal['vol_h24']:,.0f}"
    liq_fmt     = f"${signal['liquidity_usd']:,.0f}"

    msg = f"""
🚨 <b>DIP &amp; RIP ALERT!</b>

🪙 <b>{signal['token_name']} ({signal['token_symbol']})</b>
📊 DEX: {signal['dex_id'].upper()} | Solana

💹 <b>PRICE:</b> ${signal['price_usd']:.8f}

📈 Pump Awal (24h): <b>+{signal['pump_pct']}%</b>
📉 Dip dari High: <b>-{signal['dip_pct']}%</b>
{holds_emoji} Hold Open C1: <b>{'YA ✅' if signal['holds_open_c1'] else 'TIDAK PASTI ⚠️'}</b>

💰 <b>ENTRY ZONE:</b>
  Beli: <b>${signal['price_usd']:.8f}</b>
  🔴 Stop Loss: <b>${signal['sl_price']:.8f}</b> (-15%)
  🟡 Target 1:  <b>${signal['tp1_price']:.8f}</b> (+100%)
  🟢 Target 2:  <b>${signal['tp2_price']:.8f}</b> (+200%)

📊 <b>VOLUME:</b>
  1 Jam: {vol_h1_fmt}
  24 Jam: {vol_h24_fmt}
  Likuiditas: {liq_fmt}

🔄 Buy/Sell Ratio (1h): {buy_sell_ratio:.1f}x
{strength_stars} Signal Strength: {signal['strength']}/8

🔗 <a href="{signal['chart_url']}">Buka Chart DEX Screener</a>

⚡ Jangan lupa pasang SL setelah beli!
⚠️ BUKAN financial advice. DYOR!
"""
    return msg.strip()

# ── MAIN SCANNER LOOP ─────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    signals_found = 0

    # Scan trending tokens
    trending = get_trending_solana_tokens()
    new_tokens = get_new_tokens_solana()

    all_token_addresses = set()
    for t in trending + new_tokens:
        addr = t.get("tokenAddress", "") or t.get("address", "")
        if addr:
            all_token_addresses.add(addr)

    print(f"[SCAN] Menganalisa {len(all_token_addresses)} token...")

    for token_address in list(all_token_addresses)[:40]:  # Batasi 40 per scan
        # Cooldown check
        last_alerted = alerted_tokens.get(token_address, 0)
        if time.time() - last_alerted < ALERT_COOLDOWN_SEC:
            continue

        pair = get_token_pairs(token_address)
        if not pair:
            continue

        signal = analyze_pair(pair)
        if signal:
            signals_found += 1
            print(f"[SIGNAL] {signal['token_symbol']} | Pump: +{signal['pump_pct']}% | Dip: -{signal['dip_pct']}% | Strength: {signal['strength']}/8")

            msg = format_alert(signal)
            if send_telegram(msg):
                alerted_tokens[token_address] = time.time()
                print(f"[ALERT SENT] {signal['token_symbol']}")
            else:
                print(f"[ALERT FAILED] {signal['token_symbol']}")

        time.sleep(0.5)  # Rate limit DEX Screener

    print(f"[SCAN DONE] {signals_found} sinyal ditemukan dari {len(all_token_addresses)} token")

def main():
    print("=" * 50)
    print("  DIP & RIP ALERT BOT — v1.0")
    print("  Solana Memecoin Pattern Scanner")
    print("=" * 50)
    print(f"  Min Pump   : {MIN_PUMP_PCT}%")
    print(f"  Max Dip    : {MAX_DIP_PCT}%")
    print(f"  Min Volume : ${MIN_VOLUME_USD:,}")
    print(f"  Scan Every : {SCAN_INTERVAL_SEC}s")
    print("=" * 50)

    # Kirim pesan startup
    send_telegram("🤖 <b>DIP &amp; RIP Bot aktif!</b>\n\nBot sedang scan Solana memecoin setiap 30 detik.\nKamu akan dapat alert ketika pola terdeteksi! 🚨")

    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"[MAIN ERROR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
