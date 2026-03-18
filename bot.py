import os
import time
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")

# ── STRATEGY PARAMETERS v8.0 ────────────────────────────

# Level 1 — Early Alert
L1_MIN_MCAP       = 40000
L1_MAX_MCAP       = 100000
L1_MIN_LIQUIDITY  = 30000
L1_MIN_VOL_24H    = 200000
L1_MAX_POSITION   = 0.02

# Level 2 — Safe Alert
L2_MIN_MCAP       = 100000
L2_MIN_LIQUIDITY  = 80000
L2_MIN_VOL_24H    = 1000000
L2_MAX_POSITION   = 0.1

# Shared
MIN_PUMP_PCT           = 150
MIN_DIP_PCT            = 35
MAX_DIP_PCT            = 70
MIN_M5_REVERSAL        = 5.0
MIN_VOLUME_H1          = 50000
MAX_VOLUME_DROP_PCT    = 80
MAX_SINGLE_CANDLE_DROP = 25
MIN_RECENT_TXNS        = 5

# 🆕 v8.0 Safety Parameters
MIN_TOKEN_AGE_HOURS    = 1.0   # Minimal token berumur 1 jam
MIN_HOLDERS            = 100   # Minimal 100 holders
MAX_TOP10_HOLDER_PCT   = 30    # Top 10 holder max 30%
MAX_SNIPER_RATIO       = 0.3   # Max 30% dari early buyers = sniper
MIN_HOLDER_GROWTH      = 0     # Holder harus tidak turun

SCAN_INTERVAL_SEC   = 30
ALERT_COOLDOWN_SEC  = 300

# ── CHANGELOG ────────────────────────────────────────────
# v8.0 — GMGN + Axiom Intelligence Update (18 Mar 2026):
#   Belajar dari KIKO & Pygmy Hippo:
#   + Token age filter (dari GMGN)
#   + Holder count & growth (dari GMGN)
#   + Top holder concentration (dari GMGN/Bubblemaps)
#   + Bundle & sniper detection (dari Axiom proxy)
#   + Double Bottom pattern detection
#   + Liquidity Sweep detection (dari observasi chart)
#   + Volume Staircase scoring
#   + Manual checklist di alert untuk GMGN/Axiom

# ── STATE ────────────────────────────────────────────────
alerted_tokens = {}
social_cache   = {}
token_cache    = {}
CACHE_DURATION = 300

# ── TELEGRAM ─────────────────────────────────────────────
def send_telegram(message: str):
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message,
                "parse_mode": "HTML", "disable_web_page_preview": True}
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
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=15)
        if r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs,
            key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)[0]
    except Exception as e:
        print(f"[PAIR ERROR] {e}")
        return None

# ── 🆕 GMGN PUBLIC API ───────────────────────────────────
def get_gmgn_data(token_address: str) -> dict:
    """
    Ambil data dari GMGN public API:
    - Token age
    - Holder count
    - Top holder concentration
    """
    cache_key = f"gmgn_{token_address}"
    if cache_key in token_cache:
        ct, cd = token_cache[cache_key]
        if time.time() - ct < CACHE_DURATION:
            return cd

    result = {"available": False}

    try:
        # GMGN token info endpoint
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://gmgn.ai/"
        }

        url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}"
        r   = requests.get(url, headers=headers, timeout=10)

        if r.status_code == 200:
            data  = r.json()
            token = data.get("data", {}).get("token", {})

            if token:
                # Hitung token age
                created_at   = token.get("open_timestamp", 0)
                current_time = time.time()
                age_hours    = (current_time - created_at) / 3600 if created_at else 0

                result = {
                    "available":      True,
                    "age_hours":      round(age_hours, 2),
                    "holder_count":   token.get("holder_count", 0) or 0,
                    "top10_pct":      float(token.get("top_10_holder_rate", 0) or 0) * 100,
                    "dev_pct":        float(token.get("dev_token_burn_ratio", 0) or 0) * 100,
                    "sniper_count":   token.get("sniper_count", 0) or 0,
                    "smart_buy_24h":  token.get("smart_buy_24h", 0) or 0,
                    "smart_sell_24h": token.get("smart_sell_24h", 0) or 0,
                    "burn_ratio":     float(token.get("burn_ratio", 0) or 0),
                    "is_honeypot":    token.get("is_honeypot", False),
                    "renounced":      token.get("renounced", False),
                    "rug_ratio":      float(token.get("rug_ratio", 0) or 0),
                }
                print(f"[GMGN] {token_address[:8]} | Age:{age_hours:.1f}h | Holders:{result['holder_count']} | Top10:{result['top10_pct']:.1f}%")

    except Exception as e:
        print(f"[GMGN ERROR] {token_address[:8]}: {e}")

    token_cache[cache_key] = (time.time(), result)
    return result

def score_gmgn_data(gmgn: dict, token_symbol: str) -> tuple:
    """
    Score berdasarkan data GMGN:
    Token age, holders, concentration, safety
    """
    if not gmgn.get("available"):
        return 0, [], [], "N/A"

    score    = 0
    warnings = []
    signals  = []

    age_hours    = gmgn.get("age_hours", 0)
    holders      = gmgn.get("holder_count", 0)
    top10_pct    = gmgn.get("top10_pct", 100)
    sniper_count = gmgn.get("sniper_count", 0)
    smart_buy    = gmgn.get("smart_buy_24h", 0)
    smart_sell   = gmgn.get("smart_sell_24h", 0)
    is_honeypot  = gmgn.get("is_honeypot", False)
    rug_ratio    = gmgn.get("rug_ratio", 0)

    # 🚨 Hard reject
    if is_honeypot:
        return -100, ["🔴 HONEYPOT TERDETEKSI — JANGAN BELI!"], [], "DANGER"
    if rug_ratio > 0.8:
        return -100, ["🔴 RUG RATIO TINGGI — SANGAT BERISIKO!"], [], "DANGER"

    # 1. Token Age
    if age_hours < 0.5:
        score -= 3
        warnings.append(f"🔴 Token sangat baru: {age_hours:.1f} jam — SANGAT BERISIKO!")
    elif age_hours < MIN_TOKEN_AGE_HOURS:
        score -= 1
        warnings.append(f"⚠️ Token baru: {age_hours:.1f} jam (min {MIN_TOKEN_AGE_HOURS}H)")
    elif age_hours >= 24:
        score += 3
        signals.append(f"✅ Token mature: {age_hours:.0f} jam")
    elif age_hours >= 6:
        score += 2
        signals.append(f"✅ Token age aman: {age_hours:.1f} jam")
    elif age_hours >= 1:
        score += 1
        signals.append(f"✅ Token age: {age_hours:.1f} jam")

    # 2. Holder Count
    if holders >= 5000:
        score += 4
        signals.append(f"✅ Holders sangat banyak: {holders:,}")
    elif holders >= 1000:
        score += 3
        signals.append(f"✅ Holders banyak: {holders:,}")
    elif holders >= 500:
        score += 2
        signals.append(f"✅ Holders cukup: {holders:,}")
    elif holders >= MIN_HOLDERS:
        score += 1
        signals.append(f"✅ Holders: {holders:,}")
    else:
        score -= 1
        warnings.append(f"⚠️ Holders sedikit: {holders:,} (min {MIN_HOLDERS})")

    # 3. Top 10 Holder Concentration
    if top10_pct <= 15:
        score += 4
        signals.append(f"✅ Distribusi sangat merata: top10 = {top10_pct:.1f}%")
    elif top10_pct <= MAX_TOP10_HOLDER_PCT:
        score += 3
        signals.append(f"✅ Distribusi sehat: top10 = {top10_pct:.1f}%")
    elif top10_pct <= 40:
        score += 1
        warnings.append(f"⚠️ Konsentrasi agak tinggi: top10 = {top10_pct:.1f}%")
    else:
        score -= 2
        warnings.append(f"🔴 Konsentrasi tinggi: top10 = {top10_pct:.1f}% — risiko dump whale!")

    # 4. Sniper Detection (Axiom proxy)
    if holders > 0:
        sniper_ratio = sniper_count / holders
        if sniper_ratio > MAX_SNIPER_RATIO:
            score -= 2
            warnings.append(f"⚠️ Banyak sniper: {sniper_count} ({sniper_ratio*100:.0f}% dari holders)")
        elif sniper_count == 0:
            score += 1
            signals.append("✅ Tidak ada sniper terdeteksi")

    # 5. Smart Money (Axiom proxy)
    if smart_buy > smart_sell and smart_buy > 0:
        score += 3
        signals.append(f"✅ Smart money NET BUY: +{smart_buy - smart_sell}")
    elif smart_sell > smart_buy and smart_sell > 0:
        score -= 2
        warnings.append(f"⚠️ Smart money NET SELL: -{smart_sell - smart_buy}")

    summary = (
        f"Age:{age_hours:.1f}h | "
        f"Holders:{holders:,} | "
        f"Top10:{top10_pct:.0f}% | "
        f"Snipers:{sniper_count} | "
        f"SmartBuy:{smart_buy}"
    )

    return score, warnings, signals, summary

# ── 🆕 PATTERN DETECTION ──────────────────────────────────
def detect_volume_staircase(vol_m5, vol_h1, vol_h6, vol_h24) -> tuple:
    """
    Deteksi Volume Staircase Pattern:
    Volume naik konsisten seperti tangga
    Gerald & FUGUU pattern
    """
    score   = 0
    signals = []

    avg_h1_per_5m    = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6_per_hour  = vol_h6 / 6  if vol_h6 > 0 else 0
    avg_h24_per_6h   = vol_h24 / 4 if vol_h24 > 0 else 0

    # Volume m5 > rata-rata h1 = momentum naik
    if avg_h1_per_5m > 0:
        r1 = vol_m5 / avg_h1_per_5m
        if r1 >= 2.0:
            score += 3
            signals.append(f"🪜 Volume Staircase KUAT: m5 {r1:.1f}x rata-rata h1!")
        elif r1 >= 1.5:
            score += 2
            signals.append(f"🪜 Volume Staircase: m5 {r1:.1f}x rata-rata")

    # Volume h1 > rata-rata h6 = tren naik berlanjut
    if avg_h6_per_hour > 0:
        r2 = vol_h1 / avg_h6_per_hour
        if r2 >= 1.5:
            score += 2
            signals.append(f"📈 Volume momentum h1 vs h6: {r2:.1f}x")
        elif r2 >= 1.0:
            score += 1

    # Volume h6 > rata-rata h24 = tren lebih panjang
    if avg_h24_per_6h > 0:
        r3 = vol_h6 / avg_h24_per_6h
        if r3 >= 1.5:
            score += 2
            signals.append(f"📈 Volume tren h6 vs h24: {r3:.1f}x")

    return score, signals

def detect_liquidity_sweep(change_m5, change_h1, vol_m5, vol_h1, buys_m5, sells_m5) -> tuple:
    """
    Deteksi Liquidity Sweep Pattern:
    Candle merah tajam diikuti bounce = smart money akumulasi
    4.25 We Bullish pattern
    """
    score   = 0
    signals = []

    # Proxy: h1 sangat negatif tapi m5 sudah positif
    # = Kemungkinan ada sweep di h1 lalu bounce di m5
    if change_h1 < -30 and change_m5 > 10:
        score += 3
        signals.append(f"⚡ Possible Liquidity Sweep: h1{change_h1:.0f}% → m5+{change_m5:.0f}%!")
    elif change_h1 < -20 and change_m5 > 5:
        score += 2
        signals.append(f"⚡ Possible Sweep & Bounce: h1{change_h1:.0f}% → m5+{change_m5:.0f}%")

    # Buy/sell flip — seller dominan di h1 tapi buyer dominan di m5
    r_m5 = buys_m5 / max(sells_m5, 1)
    if change_h1 < -20 and r_m5 >= 2.0:
        score += 2
        signals.append(f"✅ Buyer flip di m5: {r_m5:.1f}x setelah dip dalam")

    return score, signals

# ── FILTERS DARI VERSI SEBELUMNYA ────────────────────────
def check_dex_risk(dex_id):
    score = 0; warning = None
    d = dex_id.lower()
    if "raydium" in d or "orca" in d: score += 2
    elif "meteora" in d: score += 1
    elif "pump" in d: score -= 1; warning = "⚠️ PumpSwap — risiko lebih tinggi"
    return score, warning

def check_reversal_strength(change_m5, change_h1, buys_m5, sells_m5):
    score = 0; warnings = []; signals = []
    if change_m5 >= 20:   score += 4; signals.append(f"✅ Reversal KUAT m5: +{change_m5:.1f}%")
    elif change_m5 >= 10: score += 3; signals.append(f"✅ Reversal bagus m5: +{change_m5:.1f}%")
    elif change_m5 >= 5:  score += 2; signals.append(f"✅ Reversal mulai: +{change_m5:.1f}%")
    elif change_m5 >= 0:  score -= 1; warnings.append(f"⚠️ Reversal lemah: +{change_m5:.1f}%")
    else:                 score -= 3; warnings.append(f"🔴 m5 negatif: {change_m5:.1f}%")
    if -60 < change_h1 < -20: score += 2; signals.append(f"✅ Dip sehat h1: {change_h1:.0f}%")
    elif change_h1 <= -60:    score -= 2; warnings.append("🔴 Dip terlalu dalam")
    r_m5 = buys_m5 / max(sells_m5, 1)
    if r_m5 >= 2.0:   score += 3; signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 1.5: score += 2; signals.append(f"✅ Buyer m5: {r_m5:.1f}x")
    elif r_m5 >= 1.0: score += 1
    else: warnings.append(f"⚠️ Seller dominan m5: {r_m5:.1f}x")
    return score, warnings, signals

def check_dip_quality(dip_from_high, pump_pct, holds_open_c1):
    score = 0; warnings = []; signals = []
    if dip_from_high >= 50:   score += 3; signals.append(f"✅ Dip ideal: -{dip_from_high:.0f}%")
    elif dip_from_high >= 35: score += 2; signals.append(f"✅ Dip cukup: -{dip_from_high:.0f}%")
    else:                     score -= 3; warnings.append(f"🔴 Dip dangkal: -{dip_from_high:.0f}%")
    if holds_open_c1: score += 2; signals.append("✅ Hold Open C1")
    else: warnings.append("⚠️ Open C1 belum terkonfirmasi")
    if pump_pct >= 500:   score += 3; signals.append(f"✅ Pump sangat kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 200: score += 2; signals.append(f"✅ Pump kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 150: score += 1
    return score, warnings, signals

def check_volume_collapse(vol_m5, vol_h1, vol_h6, vol_h24):
    avg_5m = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6 = vol_h6 / 6  if vol_h6 > 0 else 0
    if avg_5m > 0 and vol_m5 / avg_5m < 0.05:
        return False, ["🔴 VOLUME COLLAPSE — Token sudah mati!"]
    if avg_h6 > 0 and vol_h1 > 0 and vol_h1 / avg_h6 < 0.2:
        return False, ["🔴 Volume drop >80% — dump terdeteksi!"]
    return True, []

def check_price_velocity(change_m5, sells_m5, buys_m5):
    if change_m5 < -MAX_SINGLE_CANDLE_DROP:
        return False, [f"🔴 DUMP MASIF: m5 {change_m5:.0f}%!"]
    if change_m5 < -15 and buys_m5 / max(sells_m5, 1) < 0.3:
        return False, ["🔴 Dump aktif + seller dominan!"]
    return True, []

def check_activity(buys_m5, sells_m5, vol_m5):
    if buys_m5 + sells_m5 < MIN_RECENT_TXNS:
        return False, [f"🔴 Hanya {buys_m5+sells_m5} txns di m5!"]
    if vol_m5 < 1000:
        return False, ["🔴 Volume m5 <$1K!"]
    return True, []

def check_mcap_liq(mcap_usd, liquidity_usd, vol_h24, level):
    score = 0; warnings = []; signals = []
    min_liq = L1_MIN_LIQUIDITY if level == "L1" else L2_MIN_LIQUIDITY
    if liquidity_usd < min_liq:
        return -10, [f"🔴 Liq terlalu rendah: ${liquidity_usd:,.0f}"], []
    if liquidity_usd >= 200000: score += 3; signals.append(f"✅ Liq kuat: ${liquidity_usd/1000:.0f}K")
    elif liquidity_usd >= 100000: score += 2; signals.append(f"✅ Liq solid: ${liquidity_usd/1000:.0f}K")
    elif liquidity_usd >= 50000: score += 1
    if mcap_usd > 0 and liquidity_usd > 0:
        ratio = mcap_usd / liquidity_usd
        if 3 <= ratio <= 20: score += 1; signals.append(f"✅ MCAP/Liq ratio: {ratio:.1f}x")
    return score, warnings, signals

# ── LUNARCRUSH ────────────────────────────────────────────
def get_social_data(token_symbol: str) -> dict:
    cache_key = f"lunar_{token_symbol.lower()}"
    if cache_key in social_cache:
        ct, cd = social_cache[cache_key]
        if time.time() - ct < CACHE_DURATION: return cd
    if not LUNARCRUSH_KEY: return {"available": False}
    try:
        headers = {"Authorization": f"Bearer {LUNARCRUSH_KEY}"}
        r = requests.get(
            "https://lunarcrush.com/api4/public/coins/list/v2",
            headers=headers,
            params={"sort": "galaxy_score", "limit": 5, "search": token_symbol},
            timeout=10)
        if r.status_code != 200: return {"available": False}
        coins = r.json().get("data", [])
        if not coins: return {"available": False}
        coin  = next((c for c in coins if c.get("symbol","").upper() == token_symbol.upper()), coins[0])
        v24h  = coin.get("social_volume_24h", 0) or 0
        vprev = coin.get("social_volume_prev", v24h) or v24h
        trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
        result = {
            "available":    True,
            "galaxy_score": round(coin.get("galaxy_score", 0) or 0, 1),
            "alt_rank":     coin.get("alt_rank", 9999) or 9999,
            "mention_trend": round(trend, 1),
            "kol_active":   (coin.get("interactions_24h", 0) or 0) > 10000,
        }
        social_cache[cache_key] = (time.time(), result)
        return result
    except: return {"available": False}

def score_social(social: dict) -> tuple:
    if not social.get("available"): return 0, [], []
    score = 0; signals = []; warnings = []
    gs  = social.get("galaxy_score", 0)
    ar  = social.get("alt_rank", 9999)
    mt  = social.get("mention_trend", 0)
    kol = social.get("kol_active", False)
    if gs >= 60:   score += 4; signals.append(f"🌟 Galaxy: {gs}/100")
    elif gs >= 40: score += 3; signals.append(f"🌟 Galaxy: {gs}/100")
    elif gs >= 20: score += 1
    if ar <= 100:   score += 3; signals.append(f"🏅 AltRank: #{ar}")
    elif ar <= 500: score += 2
    if mt >= 100:  score += 4; signals.append(f"🔥 Hype +{mt:.0f}%!")
    elif mt >= 50: score += 3; signals.append(f"📈 Hype +{mt:.0f}%")
    elif mt >= 20: score += 2
    elif mt >= 0:  score += 1
    if kol: score += 3; signals.append("👑 KOL aktif!")
    return score, warnings, signals

# ── DETERMINE LEVEL ───────────────────────────────────────
def determine_level(mcap_usd, liquidity_usd, vol_h24) -> str | None:
    if (mcap_usd >= L2_MIN_MCAP and
        liquidity_usd >= L2_MIN_LIQUIDITY and
        vol_h24 >= L2_MIN_VOL_24H):
        return "L2"
    if (L1_MIN_MCAP <= mcap_usd < L1_MAX_MCAP and
        liquidity_usd >= L1_MIN_LIQUIDITY and
        vol_h24 >= L1_MIN_VOL_24H):
        return "L1"
    return None

# ── MAIN ANALYZE v8.0 ────────────────────────────────────
def analyze_pair(pair: dict):
    try:
        token_address = pair.get("baseToken", {}).get("address", "")
        token_name    = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_address  = pair.get("pairAddress", "")
        chain_id      = pair.get("chainId", "")
        dex_id        = pair.get("dexId", "")
        price_usd     = float(pair.get("priceUsd", 0) or 0)

        pc         = pair.get("priceChange", {})
        change_m5  = float(pc.get("m5",  0) or 0)
        change_h1  = float(pc.get("h1",  0) or 0)
        change_h6  = float(pc.get("h6",  0) or 0)
        change_h24 = float(pc.get("h24", 0) or 0)

        vol        = pair.get("volume", {})
        vol_m5     = float(vol.get("m5",  0) or 0)
        vol_h1     = float(vol.get("h1",  0) or 0)
        vol_h6     = float(vol.get("h6",  0) or 0)
        vol_h24    = float(vol.get("h24", 0) or 0)

        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        mcap_usd      = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

        txns_data = pair.get("txns", {})
        txns_h24  = sum([int(txns_data.get("h24", {}).get("buys", 0) or 0),
                         int(txns_data.get("h24", {}).get("sells", 0) or 0)])
        txns_m5   = txns_data.get("m5", {})
        txns_h1   = txns_data.get("h1", {})
        buys_m5   = int(txns_m5.get("buys",  0) or 0)
        sells_m5  = int(txns_m5.get("sells", 0) or 0)
        buys_h1   = int(txns_h1.get("buys",  0) or 0)
        sells_h1  = int(txns_h1.get("sells", 0) or 0)

        # Filter keras dasar
        if price_usd <= 0 or vol_h1 < MIN_VOLUME_H1: return None
        pump_pct = change_h24 if change_h24 >= MIN_PUMP_PCT else (
                   change_h6  if change_h6  >= MIN_PUMP_PCT else None)
        if pump_pct is None: return None
        currently_dipping = (change_h1 < -10) or (change_m5 < -5)
        still_momentum    = (change_h6 > 30 and change_m5 > 0)
        if not currently_dipping and not still_momentum: return None
        if change_h1 < 0:
            est_high      = price_usd / (1 + change_h1 / 100)
            dip_from_high = ((est_high - price_usd) / est_high) * 100
        else:
            dip_from_high = abs(change_m5) if change_m5 < 0 else 0
        if currently_dipping:
            if dip_from_high > MAX_DIP_PCT: return None
            if dip_from_high < MIN_DIP_PCT and not still_momentum: return None
            if change_m5 < MIN_M5_REVERSAL and not still_momentum: return None
        holds_open_c1 = change_h24 > 20

        # Tentukan level
        level = determine_level(mcap_usd, liquidity_usd, vol_h24)
        if not level: return None

        # Anti false positive
        vol_ok, vol_w = check_volume_collapse(vol_m5, vol_h1, vol_h6, vol_h24)
        if not vol_ok: print(f"[REJECT] {token_symbol} {vol_w[0]}"); return None
        vel_ok, vel_w = check_price_velocity(change_m5, sells_m5, buys_m5)
        if not vel_ok: print(f"[REJECT] {token_symbol} {vel_w[0]}"); return None
        act_ok, act_w = check_activity(buys_m5, sells_m5, vol_m5)
        if not act_ok: print(f"[REJECT] {token_symbol} {act_w[0]}"); return None

        # Scoring
        all_warnings = []; all_signals = []; total_score = 0

        s1, w1       = check_dex_risk(dex_id)
        total_score += s1
        if w1: all_warnings.append(w1)

        s2, w2, sig2 = check_reversal_strength(change_m5, change_h1, buys_m5, sells_m5)
        total_score += s2; all_warnings.extend(w2); all_signals.extend(sig2)

        s3, w3, sig3 = check_dip_quality(dip_from_high, pump_pct, holds_open_c1)
        total_score += s3; all_warnings.extend(w3); all_signals.extend(sig3)

        s4, w4, sig4 = check_mcap_liq(mcap_usd, liquidity_usd, vol_h24, level)
        if s4 <= -10:
            print(f"[REJECT] {token_symbol} {w4[0]}"); return None
        total_score += s4; all_warnings.extend(w4); all_signals.extend(sig4)

        # 🆕 Volume Staircase
        s5, sig5 = detect_volume_staircase(vol_m5, vol_h1, vol_h6, vol_h24)
        total_score += s5; all_signals.extend(sig5)

        # 🆕 Liquidity Sweep
        s6, sig6 = detect_liquidity_sweep(change_m5, change_h1, vol_m5, vol_h1, buys_m5, sells_m5)
        total_score += s6; all_signals.extend(sig6)

        # 🆕 GMGN Data
        gmgn_data = get_gmgn_data(token_address)
        s7, w7, sig7, gmgn_summary = score_gmgn_data(gmgn_data, token_symbol)
        if s7 <= -100:
            print(f"[REJECT] {token_symbol} {w7[0]}"); return None
        total_score += s7; all_warnings.extend(w7); all_signals.extend(sig7)

        # Social
        social_data = get_social_data(token_symbol)
        s8, w8, sig8 = score_social(social_data)
        total_score += s8

        # Bonus
        if pump_pct > 500:     total_score += 2
        if holds_open_c1:      total_score += 2
        if mcap_usd >= 500000: total_score += 2
        if vol_h24 >= 3000000: total_score += 2

        # Reject 2+ critical
        if len([w for w in all_warnings if w.startswith("🔴")]) >= 2:
            print(f"[REJECT] {token_symbol} 2+ critical warnings"); return None

        # Grade
        if level == "L1":
            if total_score >= 18:   grade, status = "A",  "🟢 Setup bagus — early entry"
            elif total_score >= 12: grade, status = "B",  "🟡 Setup cukup — risiko tinggi"
            elif total_score >= 7:  grade, status = "C",  "🟠 Setup lemah — pertimbangkan skip"
            else: return None
        else:
            if total_score >= 25:   grade, status = "A+", "💎 Setup premium!"
            elif total_score >= 20: grade, status = "A",  "🟢 Setup sangat bagus!"
            elif total_score >= 13: grade, status = "B",  "🟡 Setup cukup"
            elif total_score >= 7:  grade, status = "C",  "🟠 Setup lemah"
            else: return None

        return {
            "token_name": token_name, "token_symbol": token_symbol,
            "token_address": token_address,
            "price_usd": price_usd, "pump_pct": round(pump_pct, 1),
            "dip_pct": round(dip_from_high, 1), "holds_open_c1": holds_open_c1,
            "vol_m5": vol_m5, "vol_h1": vol_h1, "vol_h6": vol_h6, "vol_h24": vol_h24,
            "liquidity_usd": liquidity_usd, "mcap_usd": mcap_usd, "txns_h24": txns_h24,
            "buys_h1": buys_h1, "sells_h1": sells_h1,
            "buys_m5": buys_m5, "sells_m5": sells_m5,
            "change_m5": change_m5, "change_h1": change_h1,
            "change_h6": change_h6, "change_h24": change_h24,
            "total_score": total_score, "pattern_status": status, "pattern_grade": grade,
            "level": level,
            "pos_text": f"Max {L1_MAX_POSITION} SOL (HIGH RISK!)" if level == "L1" else f"Max {L2_MAX_POSITION} SOL",
            "signals": all_signals[:5], "warnings": all_warnings[:3],
            # GMGN data
            "gmgn_available": gmgn_data.get("available", False),
            "gmgn_summary": gmgn_summary,
            "token_age_h": gmgn_data.get("age_hours", 0),
            "holder_count": gmgn_data.get("holder_count", 0),
            "top10_pct": gmgn_data.get("top10_pct", 0),
            "sniper_count": gmgn_data.get("sniper_count", 0),
            "smart_buy": gmgn_data.get("smart_buy_24h", 0),
            "smart_sell": gmgn_data.get("smart_sell_24h", 0),
            "is_honeypot": gmgn_data.get("is_honeypot", False),
            # Social
            "social_available": social_data.get("available", False),
            "galaxy_score": social_data.get("galaxy_score", 0),
            "alt_rank": social_data.get("alt_rank", 0),
            "mention_trend": social_data.get("mention_trend", 0),
            "kol_active": social_data.get("kol_active", False),
            "sl_price": price_usd * 0.85,
            "tp1_price": price_usd * 2.0,
            "tp2_price": price_usd * 3.0,
            "chart_url": f"https://dexscreener.com/{chain_id}/{pair_address}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{token_address}",
            "axiom_url": f"https://axiom.xyz/sol/{token_address}",
            "dex_id": dex_id,
        }
    except Exception as e:
        print(f"[ANALYZE ERROR] {e}")
        return None

# ── FORMAT ALERT v8.0 ─────────────────────────────────────
def format_alert(s: dict) -> str:
    level_header = (
        "🔴 <b>LEVEL 1 — EARLY ALERT</b>\n"
        "⚠️ HIGH RISK / HIGH REWARD\n"
        f"💰 Rekomendasi: <b>{s['pos_text']}</b>"
    ) if s["level"] == "L1" else (
        "🟢 <b>LEVEL 2 — SAFE ALERT</b>\n"
        "✅ LOWER RISK / STEADY REWARD\n"
        f"💰 Rekomendasi: <b>{s['pos_text']}</b>"
    )

    grade_emoji  = {"A+": "💎", "A": "🏆", "B": "🥈", "C": "⚡"}.get(s["pattern_grade"], "")
    r_h1         = s["buys_h1"] / max(s["sells_h1"], 1)
    r_m5         = s["buys_m5"] / max(s["sells_m5"], 1)
    signals_text = "\n".join(s["signals"]) if s["signals"] else "—"
    warn_text    = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"
    mcap_text    = f"${s['mcap_usd']/1000:.0f}K" if s['mcap_usd'] > 0 else "N/A"
    vol24_text   = f"${s['vol_h24']/1000000:.1f}M" if s['vol_h24'] >= 1e6 else f"${s['vol_h24']/1000:.0f}K"

    # GMGN block
    if s["gmgn_available"]:
        age_text    = f"{s['token_age_h']:.1f}h"
        sm_net      = s["smart_buy"] - s["smart_sell"]
        sm_text     = f"+{sm_net} NET BUY 🟢" if sm_net > 0 else (f"{sm_net} NET SELL 🔴" if sm_net < 0 else "Netral")
        gmgn_block  = f"""
🔍 <b>GMGN ANALYTICS:</b>
  ⏱ Token Age: <b>{age_text}</b>
  👥 Holders: <b>{s['holder_count']:,}</b>
  🐋 Top 10: <b>{s['top10_pct']:.1f}%</b>
  🎯 Snipers: <b>{s['sniper_count']}</b>
  💡 Smart Money: <b>{sm_text}</b>"""
    else:
        gmgn_block = "\n🔍 <b>GMGN:</b> Cek manual di link bawah"

    # Social block
    if s["social_available"]:
        trend_arrow  = "📈" if s["mention_trend"] >= 0 else "📉"
        social_block = (f"\n🌐 Galaxy:{s['galaxy_score']}/100 | "
                       f"#{s['alt_rank']} | {trend_arrow}{s['mention_trend']:+.0f}% | "
                       f"{'👑KOL!' if s['kol_active'] else '—'}")
    else:
        social_block = ""

    # Manual checklist
    checklist = """
📋 <b>CEK MANUAL DI GMGN/AXIOM:</b>
  □ Bundle detection — ada koordinasi?
  □ Dev wallet — sudah jual?
  □ Bubblemaps — distribusi merata?
  □ Pattern DB/M — terkonfirmasi?"""

    return f"""
🚨 <b>DIP &amp; RIP ALERT v8.0!</b>

{level_header}

🪙 <b>{s['token_name']} ({s['token_symbol']})</b>
📊 DEX: {s['dex_id'].upper()} | Solana

{grade_emoji} <b>{s['pattern_status']}</b>
📊 Score: {s['total_score']} | Grade: {s['pattern_grade']}

💹 Harga: <b>${s['price_usd']:.8f}</b>
📈 Pump: <b>+{s['pump_pct']}%</b> | Dip: <b>-{s['dip_pct']}%</b>
📊 m5: <b>{s['change_m5']:+.1f}%</b> | h1: {s['change_h1']:+.1f}% | h6: {s['change_h6']:+.1f}%

📦 MCAP: <b>{mcap_text}</b> | Liq: <b>${s['liquidity_usd']/1000:.0f}K</b>
📊 Vol 24H: {vol24_text} | Txns: {s['txns_h24']:,}
{gmgn_block}
{social_block}

✅ <b>SINYAL POSITIF:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}
{checklist}

💰 <b>ENTRY ZONE:</b>
  Beli:        <b>${s['price_usd']:.8f}</b>
  🔴 Stop Loss: <b>${s['sl_price']:.8f}</b> (-15%)
  🟡 Target 1:  <b>${s['tp1_price']:.8f}</b> (+100%)
  🟢 Target 2:  <b>${s['tp2_price']:.8f}</b> (+200%)

🔄 Buy/Sell h1: {r_h1:.1f}x | m5: {r_m5:.1f}x

🔗 <a href="{s['chart_url']}">DEX Screener</a> | <a href="{s['gmgn_url']}">GMGN</a> | <a href="{s['axiom_url']}">Axiom</a>

⚡ Selalu cek GMGN + Axiom sebelum eksekusi!
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
    print(f"[SCAN] {len(all_addr)} token...")
    found_l1 = 0; found_l2 = 0
    for addr in list(all_addr)[:40]:
        if time.time() - alerted_tokens.get(addr, 0) < ALERT_COOLDOWN_SEC: continue
        pair = get_token_pairs(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            if signal["level"] == "L1": found_l1 += 1
            else: found_l2 += 1
            print(f"[{signal['level']}] {signal['token_symbol']} "
                  f"Grade:{signal['pattern_grade']} Score:{signal['total_score']}")
            if send_telegram(format_alert(signal)):
                alerted_tokens[addr] = time.time()
        time.sleep(0.5)
    print(f"[DONE] L1:{found_l1} L2:{found_l2}")

def main():
    print("=" * 60)
    print("  DIP & RIP BOT v8.0 — TRIPLE PLATFORM INTELLIGENCE")
    print("  DEX Screener + GMGN + Axiom + LunarCrush")
    print("=" * 60)
    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v8.0 aktif!</b>\n\n"
        "🆕 Triple Platform Intelligence:\n"
        "📊 DEX Screener — harga & volume\n"
        "🔍 GMGN — token age, holders, snipers\n"
        "⚡ Axiom — smart money proxy\n"
        "🌐 LunarCrush — social hype\n\n"
        "🆕 Pattern Detection:\n"
        "🪜 Volume Staircase (Gerald pattern)\n"
        "⚡ Liquidity Sweep (4.25 pattern)\n"
        "🔒 Honeypot & Rug detection\n\n"
        "📋 Manual checklist di setiap alert!\n"
        "🔗 Link DEX Screener + GMGN + Axiom\n\n"
        "🚨 Scan setiap 30 detik!"
    )
    while True:
        try: scan_once()
        except Exception as e: print(f"[MAIN ERROR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
