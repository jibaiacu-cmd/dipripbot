import os
import time
import requests
from datetime import datetime

# ══════════════════════════════════════════════════════════
#   DIP & RIP BOT v9.3 — LEAN & FOCUSED
#   Core Philosophy: Token Aman + Dump Sehat + Second Pump
# ══════════════════════════════════════════════════════════

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "YOUR_BOT_TOKEN_HERE")
CHAT_ID    = os.environ.get("CHAT_ID",    "YOUR_CHAT_ID_HERE")
HELIUS_KEY = os.environ.get("HELIUS_KEY", "")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")  # Optional display only

# ── TIER PARAMETERS ─────────────────────────────────────
# 🟣 T1 — EARLY (Lucia pattern: entry sebelum $300K)
T1_MIN_MCAP      = 50000
T1_MAX_MCAP      = 300000
T1_MIN_LIQUIDITY = 20000
T1_MIN_VOL_1H    = 20000
T1_MAX_POSITION  = 0.05

# 🟢 T2 — NORMAL
T2_MIN_MCAP      = 300000
T2_MAX_MCAP      = 1000000
T2_MIN_LIQUIDITY = 50000
T2_MIN_VOL_1H    = 50000
T2_MAX_POSITION  = 0.1

# 🔵 T3 — LATE
T3_MIN_MCAP      = 1000000
T3_MIN_LIQUIDITY = 80000
T3_MIN_VOL_1H    = 80000
T3_MAX_POSITION  = 0.2

# ── CORE PATTERN PARAMETERS ─────────────────────────────
MIN_PUMP_PCT   = 150    # Pump awal minimal 150%
MIN_DIP_PCT    = 20     # Dip minimal 20% dari high
MAX_DIP_PCT    = 70     # Dip maksimal 70% dari high
MIN_M5_SIGNAL  = 3.0    # m5 minimal +3% untuk entry signal

# ── SAFETY PARAMETERS ───────────────────────────────────
# Ini WAJIB — tanpa safety, second pump tidak genuine!
MIN_LP_BURNED_T1  = 50.0   # T1: LP Burned minimal 50%
MIN_LP_BURNED_T2  = 20.0   # T2: LP Burned minimal 20%
MAX_BUNDLE_PCT    = 10.0   # Bundle max 10%
MAX_DEV_PCT       = 5.0    # Dev hold max 5%
MAX_TOP10_PCT     = 30.0   # Top 10 holder max 30%
MIN_TOKEN_AGE_T1  = 1.0    # T1: Token minimal 1 jam
MIN_TOKEN_AGE_T2  = 0.5    # T2: Token minimal 30 menit
MIN_HOLDERS_T1    = 100    # T1: Minimal 100 holders
MIN_HOLDERS_T2    = 50     # T2: Minimal 50 holders

# Helius proxy (kalau GMGN kosong)
HELIUS_MAX_GINI   = 0.5
HELIUS_MAX_TOP10  = 30.0

# Anti false positive
MAX_CANDLE_DROP   = 25     # Satu candle max turun 25%

SCAN_INTERVAL_SEC  = 30
ALERT_COOLDOWN_SEC = 300

# ── CHANGELOG ────────────────────────────────────────────
# v9.3 — Lean & Focused (18 Mar 2026):
#   Kembali ke core values:
#   "Token Aman + Pump + Dump Sehat + Second Pump"
#
#   DIBUANG (tidak relevan ke core):
#   - LunarCrush (Galaxy Score, AltRank, KOL)
#   - DEX risk scoring
#   - Gini coefficient
#   - Liquidity Sweep detection
#
#   DIPERTAHANKAN (inti):
#   + Safety: LP Burned, Bundle, Dev, Top10
#   + Helius proxy kalau GMGN kosong
#   + Open C1 tracking (akurat)
#   + Pump + Dip sehat + Hold C1
#   + Volume Staircase
#   + Consolidation detection
#   + Entry signal: m5 + buyer ratio

# ── STATE ────────────────────────────────────────────────
alerted_tokens = {}
gmgn_cache     = {}
helius_cache   = {}
social_cache   = {}  # LunarCrush cache
price_tracker  = {}  # Open C1 tracking
CACHE_SEC      = 300

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

# ── DEX SCREENER ─────────────────────────────────────────
def get_trending_tokens():
    try:
        r = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:50]
    except: return []

def get_new_tokens():
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if r.status_code != 200: return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:30]
    except: return []

def get_pair(token_address: str):
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=15)
        if r.status_code != 200: return None
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs, key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)[0]
    except: return None

# ── GMGN SAFETY DATA ─────────────────────────────────────
def get_gmgn(token_address: str) -> dict:
    """
    Data safety dari GMGN:
    LP Burned, Bundle, Dev hold, Holders, Age
    INI YANG PALING PENTING!
    """
    ck = f"gmgn_{token_address}"
    if ck in gmgn_cache:
        ct, cd = gmgn_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd

    result = {"available": False}
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10)",
            "Accept": "application/json",
            "Referer": "https://gmgn.ai/"
        }
        r = requests.get(
            f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}",
            headers=headers, timeout=10)

        if r.status_code == 200:
            token = r.json().get("data", {}).get("token", {})
            if token:
                created_at = token.get("open_timestamp", 0)
                age_hours  = (time.time() - created_at) / 3600 if created_at else 0
                result = {
                    "available":    True,
                    "age_hours":    round(age_hours, 2),
                    "holders":      token.get("holder_count", 0) or 0,
                    "lp_burned":    float(token.get("burn_ratio", 0) or 0) * 100,
                    "bundle_pct":   float(token.get("bundle_pct", 0) or 0) * 100,
                    "dev_pct":      float(token.get("dev_token_burn_ratio", 0) or 0) * 100,
                    "is_honeypot":  token.get("is_honeypot", False),
                    "rug_ratio":    float(token.get("rug_ratio", 0) or 0),
                    "smart_buy":    token.get("smart_buy_24h", 0) or 0,
                    "smart_sell":   token.get("smart_sell_24h", 0) or 0,
                }
                print(f"[GMGN] {token_address[:8]} Age:{age_hours:.1f}h LP:{result['lp_burned']:.0f}% Bundle:{result['bundle_pct']:.1f}%")
    except Exception as e:
        print(f"[GMGN ERR] {e}")

    gmgn_cache[ck] = (time.time(), result)
    return result

# ── HELIUS HOLDER DATA ────────────────────────────────────
def get_helius(token_address: str) -> dict:
    """
    Holder distribution dari Helius:
    Top 10%, Hub & Spoke detection
    Digunakan sebagai safety proxy kalau GMGN kosong
    """
    ck = f"helius_{token_address}"
    if ck in helius_cache:
        ct, cd = helius_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd

    result = {"available": False}
    if not HELIUS_KEY: return result

    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

        # Get largest accounts
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address]
        }, timeout=15)

        accounts = r.json().get("result", {}).get("value", []) if r.status_code == 200 else []
        if not accounts: return result

        # Get total supply
        r2 = requests.post(url, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getTokenSupply",
            "params": [token_address]
        }, timeout=15)

        total = float(r2.json().get("result", {}).get("value", {}).get("uiAmount", 0) or 0) if r2.status_code == 200 else 0
        if total <= 0: return result

        balances  = [float(a.get("uiAmount", 0) or 0) for a in accounts if float(a.get("uiAmount", 0) or 0) > 0]
        top10_pct = sum(balances[:10]) / total * 100 if len(balances) >= 10 else sum(balances) / total * 100

        # Hub & Spoke: top1 > 5x top2
        hub_spoke = len(balances) >= 2 and balances[1] > 0 and balances[0] / balances[1] > 5

        result = {
            "available": True,
            "top10_pct": round(top10_pct, 2),
            "top1_pct":  round(balances[0] / total * 100, 2) if balances else 0,
            "hub_spoke": hub_spoke,
        }
        print(f"[HELIUS] {token_address[:8]} Top10:{top10_pct:.1f}% Hub:{hub_spoke}")
    except Exception as e:
        print(f"[HELIUS ERR] {e}")

    helius_cache[ck] = (time.time(), result)
    return result

# ── LUNARCRUSH — OPTIONAL DISPLAY ONLY ──────────────────
def get_social(symbol: str) -> dict:
    """
    Data sosial dari LunarCrush.
    DISPLAY ONLY — tidak mempengaruhi score/grade!
    Berguna untuk token established (T3)
    Sering kosong untuk token baru (T1/T2) = normal
    """
    ck = f"lunar_{symbol.lower()}"
    if ck in social_cache:
        ct, cd = social_cache[ck]
        if time.time() - ct < CACHE_SEC: return cd

    result = {"available": False}
    if not LUNARCRUSH_KEY: return result

    try:
        headers = {"Authorization": f"Bearer {LUNARCRUSH_KEY}"}
        r = requests.get(
            "https://lunarcrush.com/api4/public/coins/list/v2",
            headers=headers,
            params={"sort": "galaxy_score", "limit": 5, "search": symbol},
            timeout=10)

        if r.status_code == 200:
            coins = r.json().get("data", [])
            if coins:
                coin  = next((c for c in coins if c.get("symbol","").upper() == symbol.upper()), coins[0])
                v24h  = coin.get("social_volume_24h", 0) or 0
                vprev = coin.get("social_volume_prev", v24h) or v24h
                trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
                result = {
                    "available":     True,
                    "galaxy_score":  round(coin.get("galaxy_score", 0) or 0, 1),
                    "alt_rank":      coin.get("alt_rank", 0) or 0,
                    "mention_trend": round(trend, 1),
                    "kol_active":    (coin.get("interactions_24h", 0) or 0) > 10000,
                }
    except: pass

    social_cache[ck] = (time.time(), result)
    return result

# ── OPEN C1 TRACKING ─────────────────────────────────────
def get_open_c1(token_address: str, price: float) -> float:
    """
    Simpan harga pertama kali discan = Open C1
    Kunci untuk deteksi "dump sehat tidak tembus C1"
    """
    if token_address not in price_tracker:
        price_tracker[token_address] = price
        print(f"[C1] {token_address[:8]} = ${price:.8f}")
    return price_tracker[token_address]

# ── SAFETY CHECK ─────────────────────────────────────────
def check_safety(gmgn: dict, helius: dict, tier: str) -> tuple:
    """
    CEK KEAMANAN TOKEN — PRIORITAS TERTINGGI!
    Tanpa token aman, second pump tidak genuine.

    Returns: (passed, score, signals, warnings, summary)
    """
    score    = 0
    signals  = []
    warnings = []

    # ── Hard reject ──────────────────────────────────────
    if gmgn.get("is_honeypot"):
        return False, -100, [], ["🔴 HONEYPOT!"], "DANGER"
    if gmgn.get("rug_ratio", 0) > 0.8:
        return False, -100, [], ["🔴 RUG RATIO TINGGI!"], "DANGER"

    if gmgn.get("available"):
        age     = gmgn.get("age_hours", 0)
        lp      = gmgn.get("lp_burned", 0)
        holders = gmgn.get("holders", 0)
        bundle  = gmgn.get("bundle_pct", 0)
        dev     = gmgn.get("dev_pct", 0)

        # Hard reject per tier
        if tier == "T1":
            if age < MIN_TOKEN_AGE_T1:
                return False, 0, [], [], ""
            if lp < MIN_LP_BURNED_T1:
                return False, 0, [], [], ""
            if holders < MIN_HOLDERS_T1:
                return False, 0, [], [], ""
        elif tier == "T2":
            if age < MIN_TOKEN_AGE_T2:
                return False, 0, [], [], ""
            if lp < MIN_LP_BURNED_T2:
                return False, 0, [], [], ""
            if holders < MIN_HOLDERS_T2:
                return False, 0, [], [], ""

        # LP Burned scoring
        if lp >= 100:
            score += 8; signals.append("🔒 LP Burned 100% — tidak bisa rug!")
        elif lp >= 80:
            score += 6; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp >= 50:
            score += 3; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp > 0:
            score += 1; warnings.append(f"⚠️ LP Burned rendah: {lp:.0f}%")
        else:
            score -= 3; warnings.append("🔴 LP tidak diburn!")

        # Bundle scoring
        if bundle <= 2:
            score += 5; signals.append(f"✅ Bundle {bundle:.1f}% — sangat bersih!")
        elif bundle <= MAX_BUNDLE_PCT:
            score += 3; signals.append(f"✅ Bundle {bundle:.1f}%")
        elif bundle <= 20:
            score -= 1; warnings.append(f"⚠️ Bundle {bundle:.1f}%")
        else:
            score -= 4; warnings.append(f"🔴 Bundle tinggi {bundle:.1f}%!")

        # Dev hold scoring
        if dev <= 1:
            score += 4; signals.append(f"✅ Dev hold {dev:.1f}% — hampir nol!")
        elif dev <= MAX_DEV_PCT:
            score += 2; signals.append(f"✅ Dev hold {dev:.1f}%")
        elif dev <= 15:
            warnings.append(f"⚠️ Dev hold {dev:.1f}%")
        else:
            score -= 3; warnings.append(f"🔴 Dev hold tinggi {dev:.1f}%!")

        # Holders
        if holders >= 2000:
            score += 3; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 500:
            score += 2; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 100:
            score += 1

        # Smart money
        sm = gmgn.get("smart_buy", 0) - gmgn.get("smart_sell", 0)
        if sm > 5:
            score += 2; signals.append(f"✅ Smart money NET BUY +{sm}")
        elif sm < -5:
            score -= 1; warnings.append(f"⚠️ Smart money NET SELL {sm}")

        summary = f"LP:{lp:.0f}% | Bundle:{bundle:.1f}% | Dev:{dev:.1f}% | Holders:{holders:,}"

    else:
        # GMGN kosong → pakai Helius sebagai proxy
        if not helius.get("available"):
            if tier in ["T1", "T2"]:
                return False, 0, [], [], ""
        else:
            top10 = helius.get("top10_pct", 100)
            hub   = helius.get("hub_spoke", True)

            if hub:
                return False, 0, [], ["🔴 Hub & Spoke terdeteksi!"], ""
            if top10 > HELIUS_MAX_TOP10:
                return False, 0, [], [f"🔴 Top 10 terlalu tinggi: {top10:.1f}%"], ""

            score += 3
            signals.append(f"✅ Helius proxy OK — Top10: {top10:.1f}%")
            warnings.append("⚠️ Safety dari Helius (GMGN belum ada data)")
            summary = f"Helius proxy | Top10:{top10:.1f}%"

    # Helius top10 check (kalau GMGN ada)
    if gmgn.get("available") and helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        hub   = helius.get("hub_spoke", False)
        if hub:
            score -= 3; warnings.append("🔴 Hub & Spoke pattern!")
        elif top10 <= 10:
            score += 3; signals.append(f"✅ Top 10 hanya {top10:.1f}% — distribusi sempurna!")
        elif top10 <= MAX_TOP10_PCT:
            score += 2; signals.append(f"✅ Top 10: {top10:.1f}%")
        elif top10 > 40:
            score -= 2; warnings.append(f"🔴 Top 10 tinggi: {top10:.1f}%!")

    # Reject kalau terlalu banyak warning kritis
    critical = [w for w in warnings if w.startswith("🔴")]
    if len(critical) >= 2:
        return False, score, signals, warnings, ""

    return True, score, signals, warnings, summary if gmgn.get("available") else "Helius proxy"

# ── CORE PATTERN DETECTION ───────────────────────────────
def check_core_pattern(
    price_usd, open_c1,
    change_m5, change_h1, change_h6, change_h24,
    vol_m5, vol_h1, vol_h6,
    buys_m5, sells_m5, buys_h1, sells_h1
) -> tuple:
    """
    INI INTI BOT KITA:
    Deteksi Pump → Dump Sehat → Konsolidasi → Entry Signal

    Pattern yang kita cari:
    1. Ada pump besar (>150%)
    2. Dip 20-70% dari high
    3. Tidak tembus Open C1
    4. Volume mengecil saat dip (opsional)
    5. Konsolidasi (h1 ranging)
    6. m5 mulai hijau = ENTRY SIGNAL
    """
    score    = 0
    signals  = []
    warnings = []

    r_m5 = buys_m5 / max(sells_m5, 1)
    r_h1 = buys_h1 / max(sells_h1, 1)

    # ── 1. PUMP AWAL ──────────────────────────────────────
    pump_pct = change_h24 if change_h24 >= MIN_PUMP_PCT else (
               change_h6  if change_h6  >= MIN_PUMP_PCT else 0)

    if pump_pct >= 500:
        score += 4; signals.append(f"🚀 Pump awal sangat kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 200:
        score += 3; signals.append(f"🚀 Pump awal kuat: +{pump_pct:.0f}%")
    elif pump_pct >= 150:
        score += 2; signals.append(f"📈 Pump awal: +{pump_pct:.0f}%")
    else:
        return score, signals, warnings, 0  # Tidak ada pump = skip

    # ── 2. DIP SEHAT ──────────────────────────────────────
    if change_h1 < 0:
        est_high      = price_usd / (1 + change_h1 / 100)
        dip_from_high = ((est_high - price_usd) / est_high) * 100
    else:
        dip_from_high = abs(change_m5) if change_m5 < 0 else 0

    if dip_from_high > MAX_DIP_PCT:
        warnings.append(f"🔴 Dip terlalu dalam: -{dip_from_high:.0f}%")
        return score, signals, warnings, dip_from_high
    elif dip_from_high >= 35:
        score += 3; signals.append(f"✅ Dip ideal: -{dip_from_high:.0f}%")
    elif dip_from_high >= MIN_DIP_PCT:
        score += 2; signals.append(f"✅ Dip cukup: -{dip_from_high:.0f}%")
    elif dip_from_high < MIN_DIP_PCT and change_h1 > 0:
        # Masih momentum naik — bisa jadi early
        score += 1
    else:
        warnings.append(f"⚠️ Dip dangkal: -{dip_from_high:.0f}%")

    # ── 3. TIDAK TEMBUS OPEN C1 ───────────────────────────
    if open_c1 > 0:
        holds_c1 = price_usd >= open_c1 * 0.9  # Toleransi 10%
        if holds_c1:
            above_pct = ((price_usd - open_c1) / open_c1 * 100)
            score += 3; signals.append(f"✅ Hold di atas C1 (+{above_pct:.0f}%)")
        else:
            warnings.append("🔴 Harga tembus Open C1!")
            score -= 2

    # ── 4. VOLUME STAIRCASE ───────────────────────────────
    avg_5m = vol_h1 / 12 if vol_h1 > 0 else 0
    avg_h6 = vol_h6 / 6  if vol_h6 > 0 else 0

    if avg_5m > 0:
        vol_ratio = vol_m5 / avg_5m
        if vol_ratio >= 2.0:
            score += 3; signals.append(f"📊 Volume naik {vol_ratio:.1f}x — momentum kuat!")
        elif vol_ratio >= 1.5:
            score += 2; signals.append(f"📊 Volume naik {vol_ratio:.1f}x")
        elif vol_ratio >= 1.0:
            score += 1
        elif vol_ratio < 0.3:
            warnings.append("⚠️ Volume sangat sepi")

    if avg_h6 > 0:
        h1_ratio = vol_h1 / avg_h6
        if h1_ratio >= 1.5:
            score += 2; signals.append(f"📈 Volume momentum h1/h6: {h1_ratio:.1f}x")

    # ── 5. KONSOLIDASI + ENTRY SIGNAL ─────────────────────
    is_consolidating = -20 <= change_h1 <= 20

    if is_consolidating:
        # Volume mengecil saat konsolidasi = sehat
        if avg_5m > 0 and vol_m5 / avg_5m < 0.7:
            score += 2; signals.append("🔄 Konsolidasi sehat — volume mengecil")

        # m5 breakout dari konsolidasi = ENTRY SIGNAL UTAMA
        if change_m5 >= 10:
            score += 5; signals.append(f"🚀 BREAKOUT konsolidasi! m5: +{change_m5:.1f}%")
        elif change_m5 >= MIN_M5_SIGNAL:
            score += 3; signals.append(f"📈 Entry signal m5: +{change_m5:.1f}%")
        elif change_m5 >= 0:
            score += 1; signals.append("⏳ Konsolidasi berlanjut")
        else:
            warnings.append(f"⚠️ m5 masih negatif: {change_m5:.1f}%")
    else:
        # Tidak konsolidasi — cek reversal biasa
        if change_m5 >= 10:
            score += 4; signals.append(f"✅ Reversal kuat m5: +{change_m5:.1f}%")
        elif change_m5 >= MIN_M5_SIGNAL:
            score += 2; signals.append(f"✅ Reversal m5: +{change_m5:.1f}%")
        elif change_m5 < -MAX_CANDLE_DROP:
            return score, signals, ["🔴 Dump masif m5!"], dip_from_high

    # ── 6. BUYER RATIO ────────────────────────────────────
    if r_m5 >= 2.0:
        score += 3; signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 1.5:
        score += 2; signals.append(f"✅ Buyer m5: {r_m5:.1f}x")
    elif r_m5 >= 1.0:
        score += 1
    else:
        warnings.append(f"⚠️ Seller dominan m5: {r_m5:.1f}x")

    if r_h1 >= 1.5:
        score += 2; signals.append(f"✅ Buyer dominan h1: {r_h1:.1f}x")
    elif r_h1 >= 1.0:
        score += 1

    return score, signals, warnings, dip_from_high

# ── DETERMINE TIER ────────────────────────────────────────
def get_tier(mcap, liq, vol_h1):
    if T1_MIN_MCAP <= mcap <= T1_MAX_MCAP and liq >= T1_MIN_LIQUIDITY and vol_h1 >= T1_MIN_VOL_1H:
        return "T1"
    if T2_MIN_MCAP <= mcap <= T2_MAX_MCAP and liq >= T2_MIN_LIQUIDITY and vol_h1 >= T2_MIN_VOL_1H:
        return "T2"
    if mcap >= T3_MIN_MCAP and liq >= T3_MIN_LIQUIDITY and vol_h1 >= T3_MIN_VOL_1H:
        return "T3"
    return None

# ── MAIN ANALYZE ─────────────────────────────────────────
def analyze_pair(pair: dict):
    try:
        addr    = pair.get("baseToken", {}).get("address", "")
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_id = pair.get("pairAddress", "")
        chain   = pair.get("chainId", "")
        dex     = pair.get("dexId", "")
        price   = float(pair.get("priceUsd", 0) or 0)

        pc  = pair.get("priceChange", {})
        m5  = float(pc.get("m5",  0) or 0)
        h1  = float(pc.get("h1",  0) or 0)
        h6  = float(pc.get("h6",  0) or 0)
        h24 = float(pc.get("h24", 0) or 0)

        vol    = pair.get("volume", {})
        vm5    = float(vol.get("m5",  0) or 0)
        vh1    = float(vol.get("h1",  0) or 0)
        vh6    = float(vol.get("h6",  0) or 0)
        vh24   = float(vol.get("h24", 0) or 0)

        liq    = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        mcap   = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

        tx     = pair.get("txns", {})
        bm5    = int(tx.get("m5", {}).get("buys",  0) or 0)
        sm5    = int(tx.get("m5", {}).get("sells", 0) or 0)
        bh1    = int(tx.get("h1", {}).get("buys",  0) or 0)
        sh1    = int(tx.get("h1", {}).get("sells", 0) or 0)
        txh24  = sum([int(tx.get("h24", {}).get("buys", 0) or 0),
                      int(tx.get("h24", {}).get("sells", 0) or 0)])

        # Filter dasar
        if price <= 0 or mcap <= 0: return None

        # Pump check dulu — ini syarat utama!
        pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
        if pump < MIN_PUMP_PCT: return None

        # Harus ada tanda dip atau momentum
        dipping   = (h1 < -10) or (m5 < -5)
        momentum  = (h6 > 20 and m5 >= 0)
        if not dipping and not momentum: return None

        # Anti dump masif
        if m5 < -MAX_CANDLE_DROP: return None

        # Volume collapse check
        avg5m = vh1 / 12 if vh1 > 0 else 0
        if avg5m > 0 and vm5 / avg5m < 0.05:
            return None  # Volume collapse

        # Tentukan tier
        tier = get_tier(mcap, liq, vh1)
        if not tier: return None

        # ── OPEN C1 TRACKING ─────────────────────────────
        open_c1 = get_open_c1(addr, price)

        # ── SAFETY CHECK (PRIORITY #1) ────────────────────
        gmgn_data   = get_gmgn(addr)
        helius_data = get_helius(addr)

        safety_ok, safety_score, safety_sig, safety_warn, safety_sum = check_safety(
            gmgn_data, helius_data, tier)

        if not safety_ok:
            return None

        # ── CORE PATTERN CHECK (PRIORITY #2) ─────────────
        pattern_score, pattern_sig, pattern_warn, dip_pct = check_core_pattern(
            price, open_c1, m5, h1, h6, h24,
            vm5, vh1, vh6, bm5, sm5, bh1, sh1)

        # Total score
        total = safety_score + pattern_score

        # Semua warnings
        all_warnings = safety_warn + pattern_warn
        all_signals  = safety_sig  + pattern_sig

        # Reject kalau ada warning kritis
        critical = [w for w in all_warnings if w.startswith("🔴")]
        if len(critical) >= 2: return None

        # Grade per tier
        if tier == "T1":
            if total >= 20:   grade, status = "A", "🟢 Setup bagus — EARLY!"
            elif total >= 13: grade, status = "B", "🟡 Setup cukup"
            elif total >= 7:  grade, status = "C", "🟠 Setup lemah"
            else: return None
        elif tier == "T2":
            if total >= 24:   grade, status = "A+", "💎 Setup premium!"
            elif total >= 18: grade, status = "A",  "🟢 Setup sangat bagus!"
            elif total >= 12: grade, status = "B",  "🟡 Setup cukup"
            elif total >= 6:  grade, status = "C",  "🟠 Setup lemah"
            else: return None
        else:  # T3
            if total >= 26:   grade, status = "A+", "💎 Setup premium!"
            elif total >= 20: grade, status = "A",  "🟢 Setup bagus"
            elif total >= 13: grade, status = "B",  "🟡 Setup cukup"
            elif total >= 7:  grade, status = "C",  "🟠 Setup lemah"
            else: return None

        pos_map = {"T1": T1_MAX_POSITION, "T2": T2_MAX_POSITION, "T3": T3_MAX_POSITION}

        # LunarCrush — display only, tidak pengaruhi score
        social = get_social(symbol)

        return {
            "name": name, "symbol": symbol, "addr": addr,
            "price": price, "pump_pct": round(pump, 1),
            "dip_pct": round(dip_pct, 1),
            "open_c1": open_c1,
            "holds_c1": price >= open_c1 * 0.9,
            "m5": m5, "h1": h1, "h6": h6, "h24": h24,
            "vm5": vm5, "vh1": vh1, "vh24": vh24,
            "liq": liq, "mcap": mcap, "txh24": txh24,
            "bm5": bm5, "sm5": sm5, "bh1": bh1, "sh1": sh1,
            "total": total, "status": status, "grade": grade,
            "tier": tier, "max_pos": pos_map[tier],
            "signals": all_signals[:5],
            "warnings": all_warnings[:3],
            "safety_sum": safety_sum,
            "lp_burned": gmgn_data.get("lp_burned", 0),
            "bundle_pct": gmgn_data.get("bundle_pct", 0),
            "dev_pct": gmgn_data.get("dev_pct", 0),
            "holders": gmgn_data.get("holders", 0),
            "age_h": gmgn_data.get("age_hours", 0),
            "smart_buy": gmgn_data.get("smart_buy", 0),
            "smart_sell": gmgn_data.get("smart_sell", 0),
            "top10_pct": helius_data.get("top10_pct", 0),
            "hub_spoke": helius_data.get("hub_spoke", False),
            "helius_ok": helius_data.get("available", False),
            "gmgn_ok": gmgn_data.get("available", False),
            # LunarCrush — display only
            "social_ok":      social.get("available", False),
            "galaxy_score":   social.get("galaxy_score", 0),
            "alt_rank":       social.get("alt_rank", 0),
            "mention_trend":  social.get("mention_trend", 0),
            "kol_active":     social.get("kol_active", False),
            "sl": price * 0.85,
            "tp1": price * 2.0,
            "tp2": price * 3.0,
            "chart": f"https://dexscreener.com/{chain}/{pair_id}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{addr}",
            "axiom_url": f"https://axiom.xyz/sol/{addr}",
            "dex": dex,
        }

    except Exception as e:
        print(f"[ANALYZE ERR] {e}")
        return None

# ── FORMAT ALERT ─────────────────────────────────────────
def format_alert(s: dict) -> str:
    tier_map = {
        "T1": ("🟣", "EARLY ENTRY", f"MCAP ${s['mcap']/1000:.0f}K — potensi 5-10x!", f"Max {s['max_pos']} SOL ⚠️ HIGH RISK"),
        "T2": ("🟢", "NORMAL ENTRY", f"MCAP ${s['mcap']/1000:.0f}K — setup bagus", f"Max {s['max_pos']} SOL"),
        "T3": ("🔵", "LATE ENTRY", f"MCAP ${s['mcap']/1000:.0f}K — established", f"Max {s['max_pos']} SOL"),
    }
    temoji, tlabel, tdesc, tpos = tier_map.get(s["tier"], ("⚪","","",""))
    grade_emoji = {"A+":"💎","A":"🏆","B":"🥈","C":"⚡"}.get(s["grade"],"")

    signals_text = "\n".join(s["signals"]) if s["signals"] else "—"
    warn_text    = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"

    # Safety block
    lp_e  = "🔒" if s['lp_burned'] >= 80 else "⚠️" if s['lp_burned'] >= 50 else "🔴"
    bun_e = "✅" if s['bundle_pct'] <= 5 else "⚠️" if s['bundle_pct'] <= 15 else "🔴"
    dev_e = "✅" if s['dev_pct'] <= 5 else "⚠️" if s['dev_pct'] <= 10 else "🔴"
    sm    = s['smart_buy'] - s['smart_sell']
    sm_t  = f"+{sm} NET BUY 🟢" if sm > 0 else (f"{sm} NET SELL 🔴" if sm < 0 else "Netral")

    # Helius block
    if s["helius_ok"]:
        hub_t = "⚠️ ADA!" if s["hub_spoke"] else "✅ Tidak ada"
        helius_block = f"\n  🏦 Top 10: <b>{s['top10_pct']:.1f}%</b> | Hub&Spoke: {hub_t}"
    else:
        helius_block = ""

    # LunarCrush — tampil kalau ada data, skip kalau kosong
    if s.get("social_ok"):
        trend_arrow = "📈" if s["mention_trend"] >= 0 else "📉"
        kol_text    = "👑 YA!" if s["kol_active"] else "—"
        social_block = (
            f"\n🌐 <b>Social (LunarCrush):</b>\n"
            f"  🌟 Galaxy: {s['galaxy_score']}/100 | "
            f"🏅 #{s['alt_rank']}\n"
            f"  {trend_arrow} Mention: {s['mention_trend']:+.0f}% | "
            f"KOL: {kol_text}"
        )
    else:
        social_block = ""  # Kosong = tidak ditampilkan sama sekali
    c1_t    = f"${s['open_c1']:.8f}" if s['open_c1'] > 0 else "N/A"
    age_t   = f"{s['age_h']:.1f}h" if s['age_h'] > 0 else "N/A"

    return f"""
🚨 <b>DIP &amp; RIP ALERT v9.3!</b>

{temoji} <b>{tlabel}</b>
📍 {tdesc}
💰 <b>{tpos}</b>

🪙 <b>{s['name']} ({s['symbol']})</b>
📊 {s['dex'].upper()} | Solana | ⏱ {age_t}

{grade_emoji} <b>{s['status']}</b>
📊 Score: {s['total']} | Grade: {s['grade']}

💹 Harga: <b>${s['price']:.8f}</b>
📈 Pump awal: <b>+{s['pump_pct']}%</b>
📉 Dip dari high: <b>-{s['dip_pct']}%</b>
🏁 Open C1: {c1_t}
{'✅' if s['holds_c1'] else '🔴'} Hold di atas C1: <b>{'YA ✅' if s['holds_c1'] else 'TIDAK ❌'}</b>
📊 m5: <b>{s['m5']:+.1f}%</b> | h1: {s['h1']:+.1f}% | h6: {s['h6']:+.1f}%

🔒 <b>SAFETY:</b>
  {lp_e} LP Burned: <b>{s['lp_burned']:.0f}%</b>
  {bun_e} Bundle: <b>{s['bundle_pct']:.1f}%</b>
  {dev_e} Dev Hold: <b>{s['dev_pct']:.1f}%</b>
  👥 Holders: <b>{s['holders']:,}</b>
  💡 Smart $: {sm_t}{helius_block}{social_block}

✅ <b>SINYAL:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}

💰 <b>ENTRY ZONE:</b>
  Beli:        <b>${s['price']:.8f}</b>
  🔴 Stop Loss: <b>${s['sl']:.8f}</b> (-15%)
  🟡 Target 1:  <b>${s['tp1']:.8f}</b> (+100%)
  🟢 Target 2:  <b>${s['tp2']:.8f}</b> (+200%)

📊 Vol 24H: {vol24_t} | Liq: ${s['liq']/1000:.0f}K
🔄 Buy/Sell h1: {s['bh1']/max(s['sh1'],1):.1f}x | m5: {s['bm5']/max(s['sm5'],1):.1f}x

🔗 <a href="{s['chart']}">Chart</a> | <a href="{s['gmgn_url']}">GMGN</a> | <a href="{s['axiom_url']}">Axiom</a>

⚡ Konfirmasi LP Burned &amp; Bundle di Axiom!
⚠️ BUKAN financial advice. DYOR!
""".strip()

# ── MAIN LOOP ─────────────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    trending = get_trending_tokens()
    new_tok  = get_new_tokens()
    all_addr = set()
    for t in trending + new_tok:
        a = t.get("tokenAddress","") or t.get("address","")
        if a: all_addr.add(a)

    print(f"[SCAN] {len(all_addr)} token...")
    t1=t2=t3=0
    for addr in list(all_addr)[:40]:
        if time.time() - alerted_tokens.get(addr, 0) < ALERT_COOLDOWN_SEC: continue
        pair   = get_pair(addr)
        if not pair: continue
        signal = analyze_pair(pair)
        if signal:
            if signal["tier"]=="T1": t1+=1
            elif signal["tier"]=="T2": t2+=1
            else: t3+=1
            print(f"[{signal['tier']}] {signal['symbol']} Grade:{signal['grade']} Score:{signal['total']} Pump:+{signal['pump_pct']}% Dip:-{signal['dip_pct']}%")
            if send_telegram(format_alert(signal)):
                alerted_tokens[addr] = time.time()
        time.sleep(0.5)
    print(f"[DONE] T1:{t1} T2:{t2} T3:{t3}")

def main():
    print("=" * 60)
    print("  DIP & RIP BOT v9.3 — LEAN & FOCUSED")
    print("  Core: Token Aman + Dump Sehat + Second Pump")
    print("=" * 60)
    print(f"  Helius : {'✅' if HELIUS_KEY else '⚠️ Belum ada key'}")
    print(f"  T1 Early : MCAP ${T1_MIN_MCAP/1000:.0f}K-${T1_MAX_MCAP/1000:.0f}K")
    print(f"  T2 Normal: MCAP ${T2_MIN_MCAP/1000:.0f}K-${T2_MAX_MCAP/1000:.0f}K")
    print(f"  T3 Late  : MCAP >${T3_MIN_MCAP/1000:.0f}K")
    print("=" * 60)

    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v9.3 aktif!</b>\n\n"
        "🎯 <b>Core Philosophy:</b>\n"
        "Token Aman → Pump → Dump Sehat\n"
        "→ Sideway → Second Pump 🚀\n\n"
        "🔒 <b>Safety Check:</b>\n"
        "LP Burned | Bundle | Dev | Top10\n\n"
        "📈 <b>Pattern Check:</b>\n"
        "Pump >150% → Dip sehat → Hold C1\n"
        "→ Konsolidasi → m5 breakout!\n\n"
        f"🌐 LunarCrush: {'✅ Display only' if LUNARCRUSH_KEY else '⚠️ Tidak ada key'}\n"
        f"📊 Helius: {'✅ Aktif' if HELIUS_KEY else '⚠️ Tidak ada key'}\n\n"
        "🚨 Scan setiap 30 detik!"
    )
    while True:
        try: scan_once()
        except Exception as e: print(f"[ERR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
