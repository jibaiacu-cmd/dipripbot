import os
import time
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID   = os.environ.get("CHAT_ID",   "YOUR_CHAT_ID_HERE")

# ── STRATEGY PARAMETERS ─────────────────────────────────
MIN_PUMP_PCT         = 150
MAX_DIP_PCT          = 70
MIN_DIP_PCT          = 20
MIN_VOLUME_USD       = 50000
MIN_LIQUIDITY_USD    = 20000
SCAN_INTERVAL_SEC    = 30
ALERT_COOLDOWN_SEC   = 300

# ── STATE ────────────────────────────────────────────────
alerted_tokens = {}

# ── TELEGRAM ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# ── DEX SCREENER API ─────────────────────────────────────
def get_trending_solana_tokens():
    try:
        r = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:50]
    except Exception as e:
        print(f"[SCAN ERROR] {e}")
        return []

def get_new_tokens_solana():
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:30]
    except Exception as e:
        print(f"[NEW TOKEN ERROR] {e}")
        return []

def get_token_pairs(token_address: str):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=15)
        if r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs, key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)[0]
    except Exception as e:
        print(f"[PAIR ERROR] {e}")
        return None

# ── FILTER FUNCTIONS ─────────────────────────────────────
def check_volume_trend(vol_m5, vol_h1, vol_h6):
    score = 0
    warnings = []
    avg_5m = vol_h1 / 12 if vol_h1 > 0 else 0
    if avg_5m > 0:
        if vol_m5 < avg_5m * 0.7:
            score += 2  # Volume mengecil saat dip = bagus
        elif vol_m5 > avg_5m * 1.5:
            score -= 2
            warnings.append("⚠️ Volume masih tinggi saat dip — dump belum selesai")
    avg_h6 = vol_h6 / 6 if vol_h6 > 0 else 0
    if avg_h6 > 0 and vol_h1 / avg_h6 < 0.8:
        score += 1  # Activity melambat = akumulasi
    return score, warnings

def check_price_structure(change_m5, change_h1, change_h6, change_h24):
    score = 0
    warnings = []
    signals = []
    if change_h24 > 500:
        score += 3; signals.append("✅ Pump awal sangat kuat (+500%)")
    elif change_h24 > 200:
        score += 2; signals.append("✅ Pump awal kuat (+200%)")
    elif change_h24 > 150:
        score += 1
    if -50 < change_h1 < -10:
        score += 2; signals.append("✅ Dip sehat di h1")
    elif change_h1 < -70:
        score -= 3; warnings.append("🔴 Dip terlalu dalam — structure rusak!")
    if change_m5 < -15:
        score -= 2; warnings.append("🔴 Dump aktif di m5 — belum waktunya masuk!")
    elif -10 <= change_m5 <= 0:
        score += 1; signals.append("✅ m5 stabil — dip melambat")
    elif change_m5 > 0:
        score += 2; signals.append("✅ m5 sudah hijau — reversal dimulai!")
    return score, warnings, signals

def check_transaction_health(buys_m5, sells_m5, buys_h1, sells_h1):
    score = 0
    warnings = []
    signals = []
    r_h1 = buys_h1 / max(sells_h1, 1)
    r_m5 = buys_m5 / max(sells_m5, 1)
    if r_h1 >= 1.5:
        score += 2; signals.append(f"✅ Buy/Sell h1: {r_h1:.1f}x — buyer dominan")
    elif r_h1 >= 1.0:
        score += 1
    elif r_h1 < 0.5:
        score -= 2; warnings.append(f"🔴 Buy/Sell h1: {r_h1:.1f}x — seller dominan!")
    else:
        warnings.append(f"⚠️ Buy/Sell h1: {r_h1:.1f}x — seller sedikit dominan")
    if r_m5 >= 1.5:
        score += 2; signals.append(f"✅ Buy/Sell m5: {r_m5:.1f}x — reversal signal!")
    elif r_m5 >= 1.0:
        score += 1
    return score, warnings, signals

def check_liquidity(liquidity_usd, vol_h24):
    score = 0
    warnings = []
    if liquidity_usd < 10000:
        return -5, ["🔴 Likuiditas terlalu rendah — risiko rug pull!"]
    if liquidity_usd < 20000:
        score -= 1; warnings.append("⚠️ Likuiditas rendah — hati-hati")
    elif liquidity_usd >= 100000:
        score += 2
    elif liquidity_usd >= 50000:
        score += 1
    if vol_h24 / max(liquidity_usd, 1) > 50:
        warnings.append("⚠️ Volume/Likuiditas ekstrem — potensi manipulasi")
    return score, warnings

# ── MAIN ANALYZE ─────────────────────────────────────────
def analyze_pair(pair: dict):
    try:
        token_address = pair.get("baseToken", {}).get("address", "")
        token_name    = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_address  = pair.get("pairAddress", "")
        chain_id      = pair.get("chainId", "")
        dex_id        = pair.get("dexId", "")
        price_usd     = float(pair.get("priceUsd", 0) or 0)

        pc            = pair.get("priceChange", {})
        change_m5     = float(pc.get("m5",  0) or 0)
        change_h1     = float(pc.get("h1",  0) or 0)
        change_h6     = float(pc.get("h6",  0) or 0)
        change_h24    = float(pc.get("h24", 0) or 0)

        vol           = pair.get("volume", {})
        vol_m5        = float(vol.get("m5",  0) or 0)
        vol_h1        = float(vol.get("h1",  0) or 0)
        vol_h6        = float(vol.get("h6",  0) or 0)
        vol_h24       = float(vol.get("h24", 0) or 0)

        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

        txns_m5       = pair.get("txns", {}).get("m5", {})
        txns_h1       = pair.get("txns", {}).get("h1", {})
        buys_m5       = int(txns_m5.get("buys",  0) or 0)
        sells_m5      = int(txns_m5.get("sells", 0) or 0)
        buys_h1       = int(txns_h1.get("buys",  0) or 0)
        sells_h1      = int(txns_h1.get("sells", 0) or 0)

        # Filter keras
        if price_usd <= 0 or liquidity_usd < MIN_LIQUIDITY_USD or vol_h1 < MIN_VOLUME_USD:
            return None

        pump_pct = change_h24 if change_h24 >= MIN_PUMP_PCT else (change_h6 if change_h6 >= MIN_PUMP_PCT else None)
        if pump_pct is None: return None

        if not ((change_h1 < -10) or (change_m5 < -5)): return None

        if change_h1 < 0:
            est_high     = price_usd / (1 + change_h1 / 100)
            dip_from_high = ((est_high - price_usd) / est_high) * 100
        else:
            dip_from_high = abs(change_m5)

        if dip_from_high > MAX_DIP_PCT or dip_from_high < MIN_DIP_PCT: return None

        holds_open_c1 = change_h24 > 20

        # Jalankan semua filter
        all_warnings = []
        all_signals  = []
        total_score  = 0

        s1, w1       = check_volume_trend(vol_m5, vol_h1, vol_h6)
        total_score += s1; all_warnings.extend(w1)

        s2, w2, sig2 = check_price_structure(change_m5, change_h1, change_h6, change_h24)
        total_score += s2; all_warnings.extend(w2); all_signals.extend(sig2)

        s3, w3, sig3 = check_transaction_health(buys_m5, sells_m5, buys_h1, sells_h1)
        total_score += s3; all_warnings.extend(w3); all_signals.extend(sig3)

        s4, w4       = check_liquidity(liquidity_usd, vol_h24)
        total_score += s4; all_warnings.extend(w4)

        if pump_pct > 500: total_score += 2
        if holds_open_c1:  total_score += 2

        # Reject jika 2+ warning kritis
        if len([w for w in all_warnings if w.startswith("🔴")]) >= 2:
            print(f"[REJECTED] {token_symbol} — critical warnings")
            return None

        if total_score >= 10:
            grade, status = "A", "🟢 VALID — Setup sangat bagus!"
        elif total_score >= 7:
            grade, status = "B", "🟡 CUKUP VALID — Tetap hati-hati"
        elif total_score >= 4:
            grade, status = "C", "🟠 LEMAH — Risiko tinggi, skip lebih aman"
        else:
            print(f"[WEAK] {token_symbol} score={total_score}")
            return None

        return {
            "token_name": token_name, "token_symbol": token_symbol,
            "price_usd": price_usd, "pump_pct": round(pump_pct, 1),
            "dip_pct": round(dip_from_high, 1), "holds_open_c1": holds_open_c1,
            "vol_m5": vol_m5, "vol_h1": vol_h1, "liquidity_usd": liquidity_usd,
            "buys_h1": buys_h1, "sells_h1": sells_h1,
            "buys_m5": buys_m5, "sells_m5": sells_m5,
            "change_m5": change_m5, "change_h1": change_h1,
            "total_score": total_score, "pattern_status": status, "pattern_grade": grade,
            "signals": all_signals[:4], "warnings": all_warnings[:3],
            "sl_price": price_usd * 0.85, "tp1_price": price_usd * 2.0, "tp2_price": price_usd * 3.0,
            "chart_url": f"https://dexscreener.com/{chain_id}/{pair_address}",
            "dex_id": dex_id,
        }

    except Exception as e:
        print(f"[ANALYZE ERROR] {e}")
        return None

# ── FORMAT ALERT ──────────────────────────────────────────
def format_alert(s: dict) -> str:
    grade_emoji   = {"A": "🏆", "B": "🥈", "C": "⚡"}.get(s["pattern_grade"], "")
    holds_emoji   = "✅" if s["holds_open_c1"] else "⚠️"
    r_h1          = s["buys_h1"] / max(s["sells_h1"], 1)
    r_m5          = s["buys_m5"] / max(s["sells_m5"], 1)
    signals_text  = "\n".join(s["signals"])  if s["signals"]  else "—"
    warnings_text = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"

    return f"""
🚨 <b>DIP &amp; RIP ALERT v2.0!</b>

🪙 <b>{s['token_name']} ({s['token_symbol']})</b>
📊 DEX: {s['dex_id'].upper()} | Solana

{grade_emoji} <b>STATUS: {s['pattern_status']}</b>
📊 Score: {s['total_score']} poin | Grade: {s['pattern_grade']}

💹 Harga: <b>${s['price_usd']:.8f}</b>
📈 Pump Awal: <b>+{s['pump_pct']}%</b>
📉 Dip dari High: <b>-{s['dip_pct']}%</b>
{holds_emoji} Hold Open C1: <b>{'YA ✅' if s['holds_open_c1'] else 'BELUM PASTI ⚠️'}</b>
📊 m5: {s['change_m5']:+.1f}% | h1: {s['change_h1']:+.1f}%

✅ <b>SINYAL POSITIF:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warnings_text}

💰 <b>ENTRY ZONE:</b>
  Beli:         <b>${s['price_usd']:.8f}</b>
  🔴 Stop Loss:  <b>${s['sl_price']:.8f}</b> (-15%)
  🟡 Target 1:   <b>${s['tp1_price']:.8f}</b> (+100%)
  🟢 Target 2:   <b>${s['tp2_price']:.8f}</b> (+200%)

📊 Vol 5m: ${s['vol_m5']:,.0f} | Vol 1h: ${s['vol_h1']:,.0f}
💧 Likuiditas: ${s['liquidity_usd']:,.0f}
🔄 Buy/Sell h1: {r_h1:.1f}x | m5: {r_m5:.1f}x

🔗 <a href="{s['chart_url']}">Buka Chart DEX Screener</a>

⚡ Selalu cek chart sebelum eksekusi!
⚠️ BUKAN financial advice. DYOR!
""".strip()

# ── MAIN ─────────────────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    trending   = get_trending_solana_tokens()
    new_tokens = get_new_tokens_solana()
    all_addr   = set()
    for t in trending + new_tokens:
        addr = t.get("tokenAddress", "") or t.get("address", "")
        if addr: all_addr.add(addr)

    print(f"[SCAN] Menganalisa {len(all_addr)} token...")
    found = 0
    for addr in list(all_addr)[:40]:
        if time.time() - alerted_tokens.get(addr, 0) < ALERT_COOLDOWN_SEC:
            continue
        pair   = get_token_pairs(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            found += 1
            print(f"[SIGNAL] {signal['token_symbol']} Grade:{signal['pattern_grade']} Score:{signal['total_score']}")
            if send_telegram(format_alert(signal)):
                alerted_tokens[addr] = time.time()
                print(f"[SENT] {signal['token_symbol']}")
        time.sleep(0.5)
    print(f"[DONE] {found} sinyal dari {len(all_addr)} token")

def main():
    print("=" * 55)
    print("  DIP & RIP BOT v2.0 — UPGRADED FILTERS")
    print("=" * 55)
    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v2.0 aktif!</b>\n\n"
        "🆕 Filter baru:\n"
        "✅ Cek volume trend saat dip\n"
        "✅ Analisa struktur harga m5/h1/h6\n"
        "✅ Cek kesehatan transaksi buy/sell\n"
        "✅ Grade A/B/C di setiap alert\n"
        "✅ Warning otomatis jika pola lemah\n\n"
        "🚨 Scan setiap 30 detik!"
    )
    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"[MAIN ERROR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
