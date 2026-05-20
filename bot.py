import os
import time
import json
import requests
from datetime import datetime
from pathlib import Path

# ── EXECUTION ENGINE ─────────────────────────────────────────────────
# Modul eksekusi: paper trade / live trading, risk guard, exit engine.
# Pastikan execution_engine.py ada di direktori yang sama.
try:
    from execution_engine import ExecutionEngine
    _engine = ExecutionEngine()
    EXECUTION_ENABLED = True
except ImportError:
    EXECUTION_ENABLED = False
    _engine = None
    print("[EXEC] execution_engine.py tidak ditemukan — bot berjalan alert-only")

# ══════════════════════════════════════════════════════════
#   DIP & RIP BOT v11.3 — DATA-DRIVEN FILTER
#   Core Philosophy: Token Aman + Dump Sehat + Second Pump
#
#   FIX DARI DATA 86 ALERT (v11.2 → v11.3):
#   [DATA-FIX-1] Grade B dihapus semua tier — hanya A dan A+.
#                Data nyata: Grade B win rate sangat rendah.
#   [DATA-FIX-2] Hard reject: Rugcheck < 40 + no-data T0/T1 reject.
#                Rug rate 27.9% — filter keamanan diperketat.
#   [DATA-FIX-3] /status open count dari dua sumber ditampilkan
#                eksplisit — tidak ada discrepancy membingungkan.
#
#   ARSITEKTUR 7 GATE (urutan tidak bisa diubah):
#   G1  Basic filter          — 0 API call
#   G2  Pattern gate          — chart first
#   G3  Temporal filter F1/F2 — sebelum API call
#   G4  Update state + API    — age resolved sebelum G5
#   G5  Hard reject           — bahaya konkrit saja
#   G6  Pattern + Safety score
#   G7  Grade + format + kirim
#
#   BUG FIX v10 → v11 (10 perbaikan):
#   [V11-1]  Rugcheck score: normalisasi SELALU ÷10 jika raw > 10
#            (v10 hanya ÷10 jika > 100 — score 850 → 85, salah)
#   [V11-2]  safety_pct formula dikalibrasi ulang: range -10..+38
#            (v10: range terlalu sempit, banyak token dapat 100%)
#   [V11-3]  SL/TP dinamis berbasis dip_pct + pump_pct
#            (v10: statis per tier, tidak kontekstual)
#   [V11-4]  C1 hanya dicatat setelah lolos G5 (bukan di G3)
#            (v10: C1 tercatat di harga reject, pakai di scan berikut)
#   [V11-5]  Hub & Spoke T0: reject jika holders < 50 (bukan hanya -5 score)
#            (v10: T0 terlalu lunak, tier paling berisiko justru dibiarkan)
#   [V11-6]  F1 threshold T0 diturunkan ke m5 > 50% (T1+ tetap 150%)
#            (v10: m5 80–149% masih masuk untuk T0 = terlalu terlambat)
#   [V11-7]  Exit engine: kirim alert TP hit / SL hit / trailing SL
#            (v10: tidak ada exit signal sama sekali)
#   [V11-8]  Cooldown per tier: T0=10m T1=15m T2=20m T3=30m
#            (v10: semua tier 5 menit — terlalu pendek)
#   [V11-9]  SOL market context: suppress T0/T1 jika SOL h1 < -5%
#            (v10: tidak ada filter kondisi pasar global)
#   [V11-10] scan_once: sorted(all_addr) + trending diproses duluan
#            (v10: set acak, token bagus bisa terlewat setiap siklus)
#
#   DILARANG KERAS (belum divalidasi atau tidak relevan):
#   - Pump Memory / Watchlist Bounce
#   - Price Position F4
#   - F3 MCAP threshold multiplier
#   - Gini coefficient
#   - LunarCrush mempengaruhi score/grade
# ══════════════════════════════════════════════════════════

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
HELIUS_KEY     = os.environ.get("HELIUS_KEY",     "")
LUNARCRUSH_KEY = os.environ.get("LUNARCRUSH_KEY", "")  # display only

# ── PERSISTENT STATE ─────────────────────────────────────
STATE_FILE            = Path("bot_state.json")
PRICE_TRACKER_MAX_AGE = 7 * 86400   # [B-13] 7 hari

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                print(f"[STATE] Loaded: {len(data.get('price_tracker', {}))} C1, "
                      f"{len(data.get('alerted_tokens', {}))} alerts")
                return data
        except Exception as e:
            print(f"[STATE ERR] Load gagal: {e} — fresh start")
    return {"price_tracker": {}, "alerted_tokens": {}}

def save_state():
    now = time.time()
    # [B-13] Bersihkan entry > 7 hari
    clean_prices = {}
    for k, v in price_tracker.items():
        if isinstance(v, dict):
            if now - v.get("ts", 0) < PRICE_TRACKER_MAX_AGE:
                clean_prices[k] = v
        else:
            # Legacy float — migrate saat diakses, ikutkan dulu
            clean_prices[k] = v
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({
                "price_tracker":  clean_prices,
                "alerted_tokens": {k: v for k, v in alerted_tokens.items()
                                   if now - v < 86400},
            }, f)
    except Exception as e:
        print(f"[STATE ERR] Save gagal: {e}")

_state         = load_state()
alerted_tokens = _state.get("alerted_tokens", {})
price_tracker  = _state.get("price_tracker",  {})

# ── TIER PARAMETERS ─────────────────────────────────────
# T0 — PRE-PUMP: MCAP $25K–$100K
T0_MIN_MCAP      = 25_000
T0_MAX_MCAP      = 100_000
T0_MIN_LIQUIDITY = 8_000
T0_MIN_VOL_1H    = 5_000
T0_MAX_POSITION  = 0.02

# T1 — EARLY: MCAP $100K–$300K
T1_MIN_MCAP      = 100_000
T1_MAX_MCAP      = 300_000
T1_MIN_LIQUIDITY = 20_000
T1_MIN_VOL_1H    = 15_000
T1_MAX_POSITION  = 0.05

# T2 — NORMAL: MCAP $300K–$1M
T2_MIN_MCAP      = 300_000
T2_MAX_MCAP      = 1_000_000
T2_MIN_LIQUIDITY = 50_000
T2_MIN_VOL_1H    = 50_000
T2_MAX_POSITION  = 0.1

# T3 — LATE: MCAP $1M–$50M (ada batas atas [B-13 era])
T3_MIN_MCAP      = 1_000_000
T3_MAX_MCAP      = 50_000_000
T3_MIN_LIQUIDITY = 80_000
T3_MIN_VOL_1H    = 80_000
T3_MAX_POSITION  = 0.2

# T0 pool health filter
T0_LIQ_MCAP_MIN  = 0.15   # liq ≥ 15% mcap
T0_LIQ_MCAP_GOOD = 0.30   # ≥ 30% = bonus score

# ── PATTERN PARAMETERS ──────────────────────────────────
MIN_PUMP_PCT       = 150
T0_MIN_PUMP_PCT    = 30
MIN_DIP_PCT        = 20
MAX_DIP_PCT        = 70
MIN_M5_SIGNAL      = 3.0
MAX_CANDLE_DROP    = 25     # m5 max drop satu candle

# T0 volume & TX spike
VOL_SPIKE_EXTREME  = 10.0
VOL_SPIKE_STRONG   = 5.0
TX_ACCEL_STRONG    = 3.0
TX_ACCEL_EXTREME   = 6.0

# ── SAFETY PARAMETERS ───────────────────────────────────
MIN_LP_BURNED_T1        = 50.0
MIN_LP_BURNED_T2        = 20.0
MAX_BUNDLE_PCT          = 10.0
MAX_DEV_BURNED_WARN     = 20.0   # [B-1] dev_burned < 20% = warning
MAX_TOP10_PCT           = 30.0
HARD_REJECT_TOP10       = 70.0   # top10 > 70% = reject
MIN_TOKEN_AGE_T0        = 0.1
MIN_TOKEN_AGE_T1        = 0.5
MIN_TOKEN_AGE_T2        = 0.5
MIN_HOLDERS_T0          = 20
MIN_HOLDERS_T1          = 50
MIN_HOLDERS_T2          = 50

RUGCHECK_MAX_RISKS         = 2
RUGCHECK_HARD_REJECT_RISKS = 5
RUGCHECK_DANGEROUS_RISKS   = {"copycat", "honeypot", "freeze", "blacklist", "rugpull"}

SCAN_INTERVAL_SEC       = 30
# [V11-8] Cooldown per tier — v10 semua 5 menit, terlalu pendek
ALERT_COOLDOWN_BY_TIER  = {"T0": 600, "T1": 900, "T2": 1200, "T3": 1800}
ALERT_COOLDOWN_SEC      = 600          # fallback jika tier unknown
UPDATE_LABEL_WINDOW_SEC = 900          # 15 menit = label UPDATE

# [V11-9] SOL market context
SOL_MINT               = "So11111111111111111111111111111111111111112"
SOL_SUPPRESS_THRESHOLD = -5.0          # SOL h1 < -5% → suppress T0/T1
_sol_context           = {"h1": 0.0, "ts": 0.0}  # cache 5 menit

# ── CACHE ─────────────────────────────────────────────
gmgn_cache     = {}
rugcheck_cache = {}
helius_cache   = {}
social_cache   = {}
CACHE_SEC      = 300

# ── HARD REJECT CACHE ────────────────────────────────
# [LOG-FIX-1] Token yang hard reject (freeze/mint/honeypot/hub&spoke)
# dievaluasi ulang setiap siklus meski selalu ditolak — buang API call.
# Solusi: cache hasil reject selama 60 menit.
# Hanya token dengan reason eksplisit (bukan sekadar age/holders) yang
# masuk cache — supaya token yang belum mature tetap dicoba lagi nanti.
hard_reject_cache = {}   # addr → (reason: str, timestamp: float)
HARD_REJECT_TTL   = 3600 # 60 menit

# ── TRACKER ──────────────────────────────────────────
# [LOG-FIX-2] Tracker dari v10.1 — diport ke v11.
# TRACKER_NOTIFY_OUTCOME = False karena check_exits() sudah
# menangani notifikasi TP/SL via Telegram. Jangan kirim duplikat.
TRACKER_FILE           = Path("tracker.json")
TRACKER_MAX_OPEN_H     = 4
TRACKER_CHECK_MINS     = [30, 60, 240]
TRACKER_NOTIFY_OUTCOME = False  # check_exits() sudah handle ini

def _load_tracker() -> dict:
    if TRACKER_FILE.exists():
        try:
            with open(TRACKER_FILE, "r") as f:
                data = json.load(f)
            open_n = sum(1 for a in data.get("alerts", [])
                         if a.get("outcome") == "open")
            print(f"[TRACKER] Loaded: "
                  f"{len(data.get('alerts', []))} total, "
                  f"{open_n} open")
            return data
        except Exception as e:
            print(f"[TRACKER ERR] Load gagal: {e} — fresh start")
    return {"alerts": []}

def _save_tracker():
    try:
        with open(TRACKER_FILE, "w") as f:
            json.dump(tracker_db, f, indent=2)
    except Exception as e:
        print(f"[TRACKER ERR] Save gagal: {e}")

tracker_db = _load_tracker()

def record_alert(signal: dict):
    """Rekam snapshot alert ke tracker.json. Jangan duplikasi record open."""
    now = time.time()
    for existing in tracker_db["alerts"]:
        if (existing.get("addr")    == signal["addr"]
                and existing.get("outcome") == "open"):
            return

    record = {
        "addr":           signal["addr"],
        "symbol":         signal["symbol"],
        "name":           signal["name"],
        "tier":           signal["tier"],
        "grade":          signal["grade"],
        "alert_time":     now,
        "alert_time_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(now)),
        "entry_price": signal["price"],
        "open_c1":     signal["open_c1"],
        "sl_price":    signal["sl"],
        "tp1_price":   signal["tp1"],
        "tp2_price":   signal["tp2"],
        "sl_pct":      signal["sl_pct"],
        "pump_pct":    signal["pump_pct"],
        "dip_pct":     signal["dip_pct"],
        "holds_c1":    signal["holds_c1"],
        "m5":          signal["m5"],
        "h1":          signal["h1"],
        "h6":          signal["h6"],
        "h24":         signal["h24"],
        "score":       signal["total"],
        "safety_pct":  signal["safety_pct"],
        "lp_burned":      signal.get("lp_burned"),
        "bundle_pct":     signal.get("bundle_pct"),
        "dev_burned_pct": signal.get("dev_burned_pct"),
        "holders":        signal.get("holders"),
        "sniper_count":   signal.get("sniper_count"),
        "rc_score":       signal.get("rc_score", 0),
        "rc_risks":       signal.get("rc_risks", 0),
        "mcap":  signal["mcap"],
        "liq":   signal["liq"],
        "vh24":  signal["vh24"],
        "chart": signal["chart"],
        "outcome":            "open",
        "outcome_time":       None,
        "outcome_time_str":   None,
        "minutes_to_outcome": None,
        "price_30m":  None,
        "price_60m":  None,
        "price_240m": None,
        "max_price_seen": signal["price"],
        "max_gain_pct":   0.0,
        "result_pct":     None,
    }
    tracker_db["alerts"].append(record)
    _save_tracker()
    print(f"[TRACKER] ✅ {signal['symbol']} "
          f"{signal['tier']}/{signal['grade']} "
          f"entry=${signal['price']:.8f}")

def _get_tracker_price(addr: str) -> float | None:
    """Ambil harga token dari DexScreener untuk tracker."""
    try:
        r = api_get(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
            timeout=10)
        if not r or r.status_code != 200:
            return None
        pairs = r.json().get("pairs", [])
        if not pairs:
            return None
        best  = sorted(pairs,
                       key=lambda x: x.get("volume", {}).get("h24", 0),
                       reverse=True)[0]
        price = float(best.get("priceUsd", 0) or 0)
        return price if price > 0 else None
    except Exception as e:
        print(f"[TRACKER PRICE ERR] {addr[:8]}: {e}")
        return None

def _close_record(rec: dict, outcome: str,
                  close_price: float, now: float):
    entry      = rec["entry_price"]
    result_pct = (
        (close_price - entry) / entry * 100
        if entry > 0 and close_price > 0 else -100.0
    )
    rec["outcome"]             = outcome
    rec["outcome_time"]        = now
    rec["outcome_time_str"]    = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(now))
    rec["minutes_to_outcome"]  = round((now - rec["alert_time"]) / 60, 1)
    rec["result_pct"]          = round(result_pct, 2)
    if rec.get("price_240m") is None:
        rec["price_240m"] = close_price if close_price > 0 else None

def _notify_outcome(rec: dict, result_pct: float, outcome: str):
    """TRACKER_NOTIFY_OUTCOME = False di v11 — check_exits() sudah handle."""
    if not TRACKER_NOTIFY_OUTCOME:
        return
    label_map = {
        "win_tp2":          "🎯🎯 TP2 TERCAPAI!",
        "win_tp1":          "🎯 TP1 tercapai",
        "win_trailing_sl":  "✅ Profit terkunci via trailing SL",
        "loss_sl":          "🔴 Stop Loss",
        "rug_suspected":    "💀 Rug suspected",
        "expired":          "⏰ Expired (4 jam)",
    }
    sign = "+" if result_pct >= 0 else ""
    send_telegram(
        f"📊 <b>TRACKER</b> {label_map.get(outcome, outcome)}\n"
        f"🪙 <b>{rec['symbol']}</b> {rec['tier']}/{rec['grade']}\n"
        f"⏱ {rec.get('minutes_to_outcome', 0):.0f} menit | "
        f"<b>{sign}{result_pct:.1f}%</b>\n"
        f"🔗 <a href=\"{rec['chart']}\">Chart</a>"
    )

def _sync_tracker_exit(addr: str, outcome: str,
                        close_price: float, now: float):
    """
    Sync tracker_db saat check_exits() menutup posisi.

    Dipanggil dari check_exits() pada:
    - SL hit (outcome: win_trailing_sl jika profit, loss_sl jika rugi)
    - TP2 hit (outcome: win_tp2)

    TIDAK dipanggil pada TP1 hit karena posisi belum ditutup.
    Tanpa fungsi ini, tracker_db tidak tahu posisi sudah ditutup
    oleh check_exits() dan terus memantau dengan SL/TP lama yang salah.
    """
    for rec in tracker_db.get("alerts", []):
        if rec.get("addr") == addr and rec.get("outcome") == "open":
            _close_record(rec, outcome, close_price, now)
            _save_tracker()
            result = rec.get("result_pct", 0) or 0
            sign   = "+" if result >= 0 else ""
            print(f"[TRACKER SYNC] {rec.get('symbol','?')} "
                  f"→ {outcome} {sign}{result:.1f}%")
            return

def check_open_alerts():
    """
    Cek harga terbaru untuk semua alert tracker yang masih open.
    Dipanggil di akhir scan_once(). Tidak kirim Telegram (check_exits() sudah).
    Hanya catat outcome dan snapshot ke tracker.json.
    """
    now       = time.time()
    open_recs = [a for a in tracker_db["alerts"]
                 if a.get("outcome") == "open"]
    if not open_recs:
        return

    print(f"[TRACKER] Checking {len(open_recs)} open...")
    changed = False

    for rec in open_recs:
        addr        = rec["addr"]
        symbol      = rec["symbol"]
        entry       = rec["entry_price"]
        alert_time  = rec["alert_time"]
        elapsed_min = (now - alert_time) / 60
        elapsed_h   = elapsed_min / 60

        current = _get_tracker_price(addr)

        if current is None:
            if elapsed_h >= 0.5:
                _close_record(rec, "rug_suspected", 0.0, now)
                _notify_outcome(rec, -100.0, "rug_suspected")
                changed = True
                print(f"[TRACKER] 💀 {symbol} rug suspected")
            continue

        # Update max price
        if current > rec.get("max_price_seen", entry):
            rec["max_price_seen"] = current
            if entry > 0:
                rec["max_gain_pct"] = round(
                    (current - entry) / entry * 100, 2)
            changed = True

        # Snapshot di menit tertentu
        for mins in TRACKER_CHECK_MINS:
            key = f"price_{mins}m"
            if rec.get(key) is None and elapsed_min >= mins:
                rec[key] = current
                changed  = True
                print(f"[TRACKER] 📸 {symbol} {mins}m=${current:.8f}")

        # Result pct sekarang
        result_pct = (current - entry) / entry * 100 if entry > 0 else 0.0

        # Cek outcome (TP2 dulu)
        if current >= rec["tp2_price"]:
            _close_record(rec, "win_tp2", current, now)
            _notify_outcome(rec, result_pct, "win_tp2")
            changed = True
            print(f"[TRACKER] 🎯🎯 {symbol} TP2 +{result_pct:.1f}%")
            continue
        if current >= rec["tp1_price"]:
            _close_record(rec, "win_tp1", current, now)
            _notify_outcome(rec, result_pct, "win_tp1")
            changed = True
            print(f"[TRACKER] 🎯 {symbol} TP1 +{result_pct:.1f}%")
            continue
        if current <= rec["sl_price"]:
            _close_record(rec, "loss_sl", current, now)
            _notify_outcome(rec, result_pct, "loss_sl")
            changed = True
            print(f"[TRACKER] 🔴 {symbol} SL {result_pct:.1f}%")
            continue
        if elapsed_h >= TRACKER_MAX_OPEN_H:
            _close_record(rec, "expired", current, now)
            _notify_outcome(rec, result_pct, "expired")
            changed = True
            print(f"[TRACKER] ⏰ {symbol} expired {result_pct:+.1f}%")
            continue

        print(f"[TRACKER] ⏳ {symbol} {elapsed_min:.0f}m "
              f"${current:.8f} ({result_pct:+.1f}%)")

    if changed:
        _save_tracker()

# ── TELEGRAM ─────────────────────────────────────────
def send_telegram(message: str) -> bool:
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID, "text": message,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERR] {e}")
        return False

# ── KILL SWITCH (built-in) ───────────────────────────
# Mendengarkan command Telegram langsung di bot_v11.
# Tidak bergantung pada execution_engine.py.
#
# Commands:
#   /pause   — tahan sinyal baru (exit engine tetap jalan)
#   /resume  — lanjut kirim sinyal
#   /stop    — pause + ringkasan posisi terbuka
#   /status  — ringkasan lengkap: posisi, P&L, saldo
#   /balance — jumlah token yang sedang dipegang
#   /help    — daftar semua command

import threading as _threading

_ks_offset  = 0          # last processed Telegram update_id
_ks_running = False
_bot_paused = False      # True = tahan sinyal baru

def _ks_send(msg: str):
    """Kirim pesan dari kill switch (non-blocking)."""
    send_telegram(msg)

def _ks_handle(text: str):
    """Proses satu command dari Telegram."""
    global _bot_paused
    cmd   = text.strip().lower().split()[0] if text.strip() else ""
    parts = text.strip().lower().split()

    if cmd == "/pause":
        _bot_paused = True
        _ks_send(
            "⏸ <b>Bot di-pause</b>\n\n"
            "Sinyal baru: <b>DITAHAN</b>\n"
            "Exit engine (SL/TP/Trailing): tetap aktif\n\n"
            "Kirim /resume untuk lanjut.")

    elif cmd == "/resume":
        _bot_paused = False
        _ks_send("▶️ <b>Bot di-resume</b>\n\nSinyal baru: <b>AKTIF</b>")

    elif cmd == "/stop":
        _bot_paused = True
        _ks_send(
            f"🛑 <b>Bot di-STOP (pause)</b>\n\n"
            f"{_ks_status_text()}\n\n"
            "Kirim /resume untuk lanjut.")

    elif cmd == "/status":
        _ks_send(f"📊 <b>Status Bot</b>\n\n{_ks_status_text()}")

    elif cmd == "/balance":
        _ks_send(_ks_balance_text())

    elif cmd == "/pnl":
        # /pnl       → semua waktu
        # /pnl 1     → hari ini
        # /pnl 7     → 7 hari terakhir
        # /pnl 30    → 30 hari terakhir
        try:
            days = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            days = 0
        _ks_send(_ks_pnl_text(days=days))

    elif cmd == "/help":
        _ks_send(
            "🤖 <b>DipRip Bot v11 — Commands</b>\n\n"
            "/status     — posisi, sinyal hari ini &amp; P&amp;L ringkas\n"
            "/balance    — detail token yang dipantau\n"
            "/pnl        — P&amp;L lengkap (semua waktu)\n"
            "/pnl 1      — P&amp;L hari ini\n"
            "/pnl 7      — P&amp;L 7 hari terakhir\n"
            "/pnl 30     — P&amp;L 30 hari terakhir\n"
            "/pause      — tahan sinyal baru sementara\n"
            "/resume     — lanjut kirim sinyal\n"
            "/stop       — pause + lihat status\n"
            "/help       — daftar command ini")

def _ks_pnl_text(days: int = 0) -> str:
    """
    Hitung P&L dari tracker_db.
    days=0 → semua waktu. days=1 → hari ini. days=7 → 7 hari.
    """
    now         = time.time()
    cutoff      = now - (days * 86400) if days > 0 else 0
    today_start = now - (now % 86400)

    # Filter alert yang sudah closed
    closed = [
        a for a in tracker_db.get("alerts", [])
        if a.get("outcome") != "open"
        and (a.get("alert_time", 0) >= cutoff if days > 0 else True)
    ]
    open_n = sum(
        1 for a in tracker_db.get("alerts", [])
        if a.get("outcome") == "open"
    )
    today_closed = [
        a for a in closed
        if a.get("alert_time", 0) >= today_start
    ]

    if not closed:
        return (
            f"📊 <b>P&amp;L Tracker</b>\n\n"
            f"Open    : {open_n}\n"
            f"Closed  : 0\n\n"
            f"Belum ada data closed alert."
        )

    wins     = [a for a in closed if a.get("outcome", "").startswith("win")]
    tp1      = [a for a in closed if a.get("outcome") == "win_tp1"]
    tp2      = [a for a in closed if a.get("outcome") == "win_tp2"]
    trailing = [a for a in closed if a.get("outcome") == "win_trailing_sl"]
    losses   = [a for a in closed if a.get("outcome") == "loss_sl"]
    rugs     = [a for a in closed if a.get("outcome") == "rug_suspected"]
    expired  = [a for a in closed if a.get("outcome") == "expired"]

    n        = len(closed)
    win_rate = len(wins) / n * 100 if n > 0 else 0

    results     = [a["result_pct"] for a in closed
                   if a.get("result_pct") is not None]
    win_results = [a["result_pct"] for a in wins
                   if a.get("result_pct") is not None]
    loss_results= [a["result_pct"] for a in losses
                   if a.get("result_pct") is not None]

    avg_r    = sum(results)      / len(results)       if results      else 0
    avg_win  = sum(win_results)  / len(win_results)   if win_results  else 0
    avg_loss = sum(loss_results) / len(loss_results)  if loss_results else 0

    # Expectancy
    wr  = win_rate / 100
    exp = (wr * avg_win) + ((1 - wr) * avg_loss)

    # Hari ini
    today_wins   = [a for a in today_closed if a.get("outcome","").startswith("win")]
    today_losses = [a for a in today_closed if a.get("outcome") == "loss_sl"]
    today_res    = [a["result_pct"] for a in today_closed
                    if a.get("result_pct") is not None]
    today_avg    = sum(today_res) / len(today_res) if today_res else 0

    # Emoji
    wr_e   = "🟢" if win_rate >= 60 else "🟡" if win_rate >= 40 else "🔴"
    exp_e  = "🟢" if exp > 0        else "🔴"
    avg_e  = "🟢" if avg_r  > 0     else "🔴"
    td_e   = "🟢" if today_avg > 0  else "🔴"

    label  = "Semua waktu" if days == 0 else f"{days} hari terakhir"

    return (
        f"📊 <b>P&amp;L Tracker — {label}</b>\n\n"
        f"<b>Total closed : {n}</b>  |  Open: {open_n}\n"
        f"🎯 TP1: {len(tp1)}  🎯🎯 TP2: {len(tp2)}"
        f"  ✅ Trail: {len(trailing)}"
        f"  🔴 SL: {len(losses)}  💀 Rug: {len(rugs)}  ⏰ Expired: {len(expired)}\n\n"
        f"{wr_e} Win rate    : <b>{win_rate:.1f}%</b>\n"
        f"{avg_e} Avg result  : <b>{avg_r:+.1f}%</b>\n"
        f"📈 Avg win    : <b>{avg_win:+.1f}%</b>\n"
        f"📉 Avg loss   : <b>{avg_loss:+.1f}%</b>\n"
        f"{exp_e} Expectancy  : <b>{exp:+.1f}%</b> per trade\n\n"
        f"<b>Hari ini:</b>  "
        f"W:{len(today_wins)} L:{len(today_losses)} "
        f"| Avg: {td_e} <b>{today_avg:+.1f}%</b>"
    )


def _ks_status_text() -> str:
    """Teks ringkasan status untuk /status dan /stop."""
    now        = time.time()
    pause_str  = " | ⏸ PAUSED" if _bot_paused else ""

    # Open dari tracker_db (source of truth untuk P&L)
    tracker_open = [
        a for a in tracker_db.get("alerts", [])
        if a.get("outcome") == "open"
    ]
    # Open dari exit engine (price_tracker — yang dipantau SL/TP)
    engine_open = [
        addr for addr, v in price_tracker.items()
        if isinstance(v, dict) and v.get("entry_sl")
    ]

    # Posisi terbuka dari exit engine
    pos_lines = ""
    for addr, v in list(price_tracker.items()):
        if not isinstance(v, dict) or not v.get("entry_sl"):
            continue
        sym     = v.get("symbol",       addr[:8])
        tier_v  = v.get("tier",         "T?")
        entry_p = v.get("entry_price",  0)
        tp1_hit = v.get("tp1_hit",      False)
        tp2_hit = v.get("tp2_hit",      False)
        status  = "✅TP1" if tp1_hit else ("🟢TP2" if tp2_hit else "⏳open")
        pos_lines += (f"\n  • <b>{sym}</b> {tier_v} | "
                      f"entry ${entry_p:.8f} | {status}")

    # P&L ringkas dari tracker
    pnl_section = "\n\n" + _ks_pnl_text(days=0)

    # [DATA-FIX-3] Tampilkan dua sumber secara eksplisit
    # supaya tidak ada discrepancy yang membingungkan
    return (
        f"Mode: 🔔 Alert-only{pause_str}\n\n"
        f"📡 Sinyal hari ini: <b>{today_alerts}</b>\n"
        f"👁 Exit engine: <b>{len(engine_open)}</b> posisi dipantau\n"
        f"📊 Tracker open: <b>{len(tracker_open)}</b> alert belum closed\n\n"
        f"Posisi terbuka ({len(engine_open)}):"
        f"{pos_lines if pos_lines else chr(10) + '  (kosong)'}"
        f"{pnl_section}"
    )

def _ks_balance_text() -> str:
    """Teks ringkasan token yang sedang dipantau untuk /balance."""
    lines = []
    for addr, v in list(price_tracker.items()):
        if not isinstance(v, dict) or not v.get("entry_sl"):
            continue
        sym    = v.get("symbol", addr[:8])
        tier   = v.get("tier", "T?")
        entry  = v.get("entry_price", 0)
        sl     = v.get("entry_sl", 0)
        tp1    = v.get("entry_tp1", 0)
        tp2    = v.get("entry_tp2", 0)
        t1h    = "✅" if v.get("tp1_hit") else "⬜"
        t2h    = "✅" if v.get("tp2_hit") else "⬜"
        lines.append(
            f"🪙 <b>{sym}</b> | {tier}\n"
            f"   Entry: ${entry:.8f}\n"
            f"   SL: ${sl:.8f}\n"
            f"   TP1:{t1h} ${tp1:.8f}\n"
            f"   TP2:{t2h} ${tp2:.8f}"
        )
    if not lines:
        return "💼 <b>Tidak ada posisi terbuka saat ini.</b>"
    header = f"💼 <b>Posisi terbuka ({len(lines)})</b>\n\n"
    return header + "\n\n".join(lines)

def _ks_poll():
    """Poll Telegram getUpdates. Dipanggil di thread terpisah."""
    global _ks_offset
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}"
            f"/getUpdates?offset={_ks_offset}&timeout=2",
            timeout=6)
        if r.status_code != 200:
            return
        for upd in r.json().get("result", []):
            _ks_offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            # Hanya proses dari CHAT_ID yang diizinkan
            if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                print(f"[KS] Command: {text}")
                _ks_handle(text)
    except Exception as e:
        print(f"[KS ERR] {e}")

def _ks_loop():
    """Loop thread kill switch — poll setiap 3 detik."""
    global _ks_running
    print("[KS] Kill switch aktif — mendengarkan /pause /resume /stop /status /balance /help")
    while _ks_running:
        _ks_poll()
        time.sleep(3)

def start_kill_switch():
    """Start kill switch thread. Dipanggil dari main()."""
    global _ks_running
    _ks_running = True
    t = _threading.Thread(target=_ks_loop, daemon=True, name="KillSwitch")
    t.start()

# ── API HELPERS ──────────────────────────────────────
# [B-7] for-loop dengan continue eksplisit — Timeout tidak infinite
def api_get(url: str, headers: dict = None, params: dict = None,
            timeout: int = 10, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API GET] Rate limit, retry {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.Timeout:
            print(f"[API GET] Timeout {attempt + 1}/{retries + 1}")
            if attempt < retries:
                time.sleep(1)
            # loop lanjut natural ke attempt berikutnya
        except Exception as e:
            print(f"[API GET ERR] {e}")
            break
    return None

def api_post(url: str, json_body: dict = None,
             timeout: int = 15, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=json_body, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API POST] Rate limit, retry {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.Timeout:
            print(f"[API POST] Timeout {attempt + 1}/{retries + 1}")
            if attempt < retries:
                time.sleep(1)
        except Exception as e:
            print(f"[API POST ERR] {e}")
            break
    return None

# ── SOL MARKET CONTEXT [V11-9] ───────────────────────
def get_sol_context() -> float:
    """
    Ambil perubahan h1 SOL/USDC dari DexScreener.
    Di-cache 5 menit. Return h1 change (float).
    Suppress T0/T1 jika hasilnya < SOL_SUPPRESS_THRESHOLD.
    """
    now = time.time()
    if now - _sol_context["ts"] < 300:
        return _sol_context["h1"]
    try:
        r = api_get(
            f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}",
            timeout=10)
        if r and r.status_code == 200:
            pairs = r.json().get("pairs", [])
            # Ambil pair SOL/USDC atau SOL/USDT terbesar
            sol_pairs = [p for p in pairs
                         if p.get("quoteToken", {}).get("symbol", "")
                         in ("USDC", "USDT", "USD")]
            if sol_pairs:
                best = sorted(sol_pairs,
                              key=lambda x: x.get("volume", {}).get("h24", 0),
                              reverse=True)[0]
                h1 = float(best.get("priceChange", {}).get("h1", 0) or 0)
                _sol_context["h1"] = h1
                _sol_context["ts"] = now
                print(f"[SOL CTX] h1={h1:+.1f}%")
                return h1
    except Exception as e:
        print(f"[SOL CTX ERR] {e}")
    return _sol_context["h1"]

# ── DEX SCREENER ────────────────────────────────────
def get_trending_tokens() -> list:
    try:
        r = api_get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=15)
        if not r or r.status_code != 200:
            return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:50]
    except:
        return []

def get_new_tokens() -> list:
    try:
        r = api_get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if not r or r.status_code != 200:
            return []
        return [t for t in r.json() if t.get("chainId") == "solana"][:30]
    except:
        return []

def get_pair(token_address: str) -> dict | None:
    try:
        r = api_get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=15)
        if not r or r.status_code != 200:
            return None
        pairs = r.json().get("pairs", [])
        if not pairs:
            return None
        # Sort by h24 volume — pair paling aktif
        return sorted(pairs,
                      key=lambda x: x.get("volume", {}).get("h24", 0),
                      reverse=True)[0]
    except:
        return None

# ── GMGN ────────────────────────────────────────────
def get_gmgn(token_address: str) -> dict:
    """
    Field GMGN → v10.0 mapping:
    burn_ratio            → lp_burned     (0.0–1.0 → ×100)
    bundle_pct            → bundle_pct    (0.0–1.0 → ×100)
    dev_token_burn_ratio  → dev_burned_pct (0.0–1.0 → ×100)
                            [B-1] tinggi = dev sudah burn = BAGUS
    """
    ck = f"gmgn_{token_address}"
    if ck in gmgn_cache:
        ct, cd = gmgn_cache[ck]
        if time.time() - ct < CACHE_SEC:
            return cd

    result = {"available": False}
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10)",
            "Accept":     "application/json",
            "Referer":    "https://gmgn.ai/",
        }
        r = api_get(
            f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}",
            headers=headers, timeout=10)

        if r and r.status_code == 200:
            token = r.json().get("data", {}).get("token", {})
            if token:
                created_at = token.get("open_timestamp", 0)
                age_hours  = (time.time() - created_at) / 3600 if created_at else 0

                # [B-4] ×100 karena GMGN kirim 0.0–1.0, lalu clamp ke 100
                raw_lp  = float(token.get("burn_ratio",           0) or 0) * 100
                raw_bnd = float(token.get("bundle_pct",            0) or 0) * 100
                raw_dev = float(token.get("dev_token_burn_ratio",  0) or 0) * 100

                if raw_lp > 100 or raw_bnd > 100 or raw_dev > 100:
                    print(f"[GMGN WARN] {token_address[:8]} nilai >100% "
                          f"(LP:{raw_lp:.0f}% Bnd:{raw_bnd:.0f}% Dev:{raw_dev:.0f}%) "
                          f"— kemungkinan API sudah kirim persen, hapus ×100!")

                result = {
                    "available":     True,
                    "age_hours":     round(age_hours, 2),
                    "holders":       int(token.get("holder_count", 0) or 0),
                    "lp_burned":     min(raw_lp,  100.0),
                    "bundle_pct":    min(raw_bnd, 100.0),
                    # [B-1] dev_burned_pct: tinggi = BAGUS
                    "dev_burned_pct": min(raw_dev, 100.0),
                    "sniper_count":  int(token.get("sniper_count", 0) or 0),
                    "is_honeypot":   bool(token.get("is_honeypot", False)),
                    "rug_ratio":     float(token.get("rug_ratio",  0) or 0),
                    "smart_buy":     int(token.get("smart_buy_24h",  0) or 0),
                    "smart_sell":    int(token.get("smart_sell_24h", 0) or 0),
                }
                print(f"[GMGN] {token_address[:8]} "
                      f"Age:{age_hours:.1f}h LP:{result['lp_burned']:.0f}% "
                      f"Bnd:{result['bundle_pct']:.1f}% "
                      f"DevBurned:{result['dev_burned_pct']:.0f}% "
                      f"Sniper:{result['sniper_count']}")
    except Exception as e:
        print(f"[GMGN ERR] {e}")

    gmgn_cache[ck] = (time.time(), result)
    return result

# ── RUGCHECK ────────────────────────────────────────
def get_rugcheck(token_address: str) -> dict:
    ck = f"rugcheck_{token_address}"
    if ck in rugcheck_cache:
        ct, cd = rugcheck_cache[ck]
        if time.time() - ct < CACHE_SEC:
            return cd

    result = {"available": False}
    try:
        r = api_get(
            f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report/summary",
            headers={"Accept": "application/json"}, timeout=10)

        if r and r.status_code == 200:
            data = r.json()

            # [V11-1] Score normalisasi: Rugcheck kirim skala 0–1000.
            # ÷10 jika raw > 10. Untuk anomali ekstrem (> 10.000),
            # log warn tambahan agar terdeteksi jika skala API berubah.
            raw_score = float(data.get("score", 0) or 0)
            if raw_score > 10_000:
                print(f"[RUGCHECK WARN] {token_address[:8]} "
                      f"score raw={raw_score:.0f} — SANGAT BESAR, "
                      f"kemungkinan skala API berubah")
            if raw_score > 10:
                if raw_score > 100:
                    print(f"[RUGCHECK WARN] {token_address[:8]} "
                          f"score raw={raw_score:.0f} → ÷10")
                raw_score = raw_score / 10
            score = min(int(raw_score), 100)

            risks      = data.get("risks", []) or []
            risk_names = [r.get("name", "") for r in risks]

            markets   = data.get("markets", []) or []
            lp_locked = False
            lp_burned = False
            for m in markets:
                lp = m.get("lp", {}) or {}
                if float(lp.get("lpLockedPct", 0) or 0) > 0:
                    lp_locked = True
                if float(lp.get("lpBurnedPct", 0) or 0) > 80:
                    lp_burned = True

            token_meta  = data.get("token", {}) or {}
            mint_auth   = token_meta.get("mintAuthority")  is not None
            freeze_auth = token_meta.get("freezeAuthority") is not None

            top_holders = data.get("topHolders", []) or []
            raw_pcts    = [float(h.get("pct", 0) or 0) for h in top_holders[:10]]

            # [B-6] Guard desimal vs persen
            if raw_pcts and max(raw_pcts) > 1.0:
                top10_sum = sum(raw_pcts)        # sudah dalam persen
            else:
                top10_sum = sum(raw_pcts) * 100  # konversi desimal → persen

            result = {
                "available":       True,
                "score":           score,
                "risks":           risk_names[:5],
                "risks_lower":     [n.lower() for n in risk_names],
                "risk_count":      len(risks),
                "lp_locked":       lp_locked,
                "lp_burned":       lp_burned,
                "mint_auth":       mint_auth,
                "freeze_auth":     freeze_auth,
                "top10_pct":       round(top10_sum, 2),
            }
            print(f"[RUGCHECK] {token_address[:8]} "
                  f"Score:{score} LP_burned:{lp_burned} "
                  f"Mint:{mint_auth} Risks:{len(risks)}")
    except Exception as e:
        print(f"[RUGCHECK ERR] {e}")

    rugcheck_cache[ck] = (time.time(), result)
    return result

# ── HELIUS ──────────────────────────────────────────
def get_helius(token_address: str) -> dict:
    ck = f"helius_{token_address}"
    if ck in helius_cache:
        ct, cd = helius_cache[ck]
        if time.time() - ct < CACHE_SEC:
            return cd

    result = {"available": False}
    if not HELIUS_KEY:
        return result

    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

        r = api_post(url, json_body={
            "jsonrpc": "2.0", "id": 1,
            "method":  "getTokenLargestAccounts",
            "params":  [token_address],
        })
        if not r or r.status_code != 200:
            return result
        accounts = r.json().get("result", {}).get("value", [])
        if not accounts:
            return result

        r2 = api_post(url, json_body={
            "jsonrpc": "2.0", "id": 2,
            "method":  "getTokenSupply",
            "params":  [token_address],
        })
        if not r2 or r2.status_code != 200:
            return result
        total = float(
            r2.json().get("result", {}).get("value", {}).get("uiAmount", 0) or 0)
        if total <= 0:
            return result

        # [B-12] Guard: filter nol, cek len sebelum indexing
        balances  = [float(a.get("uiAmount", 0) or 0) for a in accounts
                     if float(a.get("uiAmount", 0) or 0) > 0]
        if not balances:
            return result

        top10_pct = (sum(balances[:10]) / total * 100
                     if len(balances) >= 10
                     else sum(balances) / total * 100)

        hub_spoke = (len(balances) >= 2
                     and balances[1] > 0
                     and balances[0] / balances[1] > 5)

        result = {
            "available": True,
            "top10_pct": round(top10_pct, 2),
            "top1_pct":  round(balances[0] / total * 100, 2),
            "hub_spoke": hub_spoke,
        }
        print(f"[HELIUS] {token_address[:8]} "
              f"Top10:{top10_pct:.1f}% Hub:{hub_spoke}")
    except Exception as e:
        print(f"[HELIUS ERR] {e}")

    helius_cache[ck] = (time.time(), result)
    return result

# ── LUNARCRUSH — DISPLAY ONLY ────────────────────────
def get_social(symbol: str, token_address: str = "") -> dict:
    ck = f"lunar_{token_address if token_address else symbol.lower()}"
    if ck in social_cache:
        ct, cd = social_cache[ck]
        if time.time() - ct < CACHE_SEC:
            return cd

    result = {"available": False}

    # [B-8] Cache result kosong juga saat tidak ada key
    if not LUNARCRUSH_KEY:
        social_cache[ck] = (time.time(), result)
        return result

    try:
        r = api_get(
            "https://lunarcrush.com/api4/public/coins/list/v2",
            headers={"Authorization": f"Bearer {LUNARCRUSH_KEY}"},
            params={"sort": "galaxy_score", "limit": 5, "search": symbol},
            timeout=10)
        if r and r.status_code == 200:
            coins = r.json().get("data", [])
            if coins:
                coin  = next(
                    (c for c in coins if c.get("symbol", "").upper() == symbol.upper()),
                    coins[0])
                v24h  = coin.get("social_volume_24h",  0) or 0
                vprev = coin.get("social_volume_prev", v24h) or v24h
                trend = ((v24h - vprev) / vprev * 100) if vprev > 0 else 0
                result = {
                    "available":     True,
                    "galaxy_score":  round(float(coin.get("galaxy_score", 0) or 0), 1),
                    "alt_rank":      int(coin.get("alt_rank", 0) or 0),
                    "mention_trend": round(trend, 1),
                    "kol_active":    int(coin.get("interactions_24h", 0) or 0) > 10_000,
                }
    except:
        pass

    social_cache[ck] = (time.time(), result)
    return result

# ── C1 & HIGH TRACKING ──────────────────────────────
def get_open_c1(token_address: str, price: float) -> float:
    """
    Kembalikan harga C1 (pertama kali terdeteksi).
    Update tracked_high jika harga sekarang lebih tinggi.
    [B-13] Simpan sebagai dict {price, high, ts}.
    """
    entry = price_tracker.get(token_address)
    now   = time.time()

    if entry is None:
        price_tracker[token_address] = {
            "price": price, "high": price, "ts": now}
        print(f"[C1] {token_address[:8]} = ${price:.8f}")
    elif isinstance(entry, dict):
        if price > entry.get("high", 0):
            price_tracker[token_address]["high"] = price
    else:
        # Migrate legacy float
        old = float(entry)
        price_tracker[token_address] = {
            "price": old, "high": max(old, price), "ts": now}

    e = price_tracker[token_address]
    return e["price"] if isinstance(e, dict) else float(e)

def get_tracked_high(token_address: str) -> float:
    """[B-2] High dari internal tracker, bukan highPrice24h DexScreener."""
    entry = price_tracker.get(token_address)
    if entry is None:
        return 0.0
    if isinstance(entry, dict):
        return entry.get("high", 0.0)
    return float(entry)

def calc_dip_from_high(price: float, tracked_high: float,
                       change_h1: float, change_m5: float) -> float:
    """
    Priority: tracked_high (akurat) > estimasi h1 > estimasi m5.
    """
    if tracked_high > 0 and price < tracked_high:
        return ((tracked_high - price) / tracked_high) * 100
    if change_h1 < 0:
        est_high = price / (1 + change_h1 / 100)
        return ((est_high - price) / est_high) * 100
    if change_m5 < 0:
        return abs(change_m5)
    return 0.0

# ── G1+G2: PATTERN EXISTS CHECK ─────────────────────
def check_pattern_exists(tier: str, m5: float, h1: float, h6: float,
                         h24: float, vm5: float, vh1: float) -> bool:
    """
    G2: Gate pattern paling awal — 0 API call.
    Jika tidak ada pattern → return False langsung.
    """
    if tier == "T0":
        # [B-12] Guard avg5m > 0
        avg5m = vh1 / 12 if vh1 > 0 else 0
        has_spike    = avg5m > 0 and vm5 / avg5m >= 2.0
        has_momentum = h6 >= T0_MIN_PUMP_PCT or h1 >= 15 or has_spike
        if not has_momentum:
            return False
        if m5 < -MAX_CANDLE_DROP:
            return False
        return True
    else:
        pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
        if pump < MIN_PUMP_PCT:
            return False
        has_signal = (h1 < -10) or (m5 < -5) or (h6 > 20 and m5 >= 0)
        if not has_signal:
            return False
        if m5 < -MAX_CANDLE_DROP:
            return False
        avg5m = vh1 / 12 if vh1 > 0 else 0
        if avg5m > 0 and vm5 / avg5m < 0.05:
            return False   # volume collapse
        return True

# ── G3: TEMPORAL FILTER F1 & F2 ─────────────────────
def check_temporal_filter(tier: str, m5: float, h1: float,
                          open_c1: float, price: float,
                          symbol: str) -> bool:
    """
    F1: m5 terlalu tinggi = deteksi saat spike, sudah terlambat. Skip.
        [V11-6] T0 threshold diturunkan ke 50% (v10: 150% — terlalu longgar).
        T0 MCAP kecil: m5 +80% biasanya sudah puncak pertama.
    F2: h1 < -30% + harga tembus C1 = distribusi aktif. Skip.
    Return True = lolos (lanjut), False = filtered (skip).
    """
    # F1 — threshold berbeda per tier
    f1_threshold = 50.0 if tier == "T0" else 150.0
    if m5 > f1_threshold:
        print(f"[F1 SKIP] {symbol} m5={m5:.1f}% > {f1_threshold:.0f}% — spike too late")
        return False

    # F2 — hanya untuk T1+ (T0 tidak ada C1 yang reliable)
    if tier != "T0" and h1 < -30:
        holds_c1 = price >= open_c1 * 0.9 if open_c1 > 0 else True
        if not holds_c1:
            print(f"[F2 SKIP] {symbol} h1={h1:.1f}% + no C1 hold — distribusi aktif")
            return False

    return True

# ── G5: HARD REJECT ─────────────────────────────────
def check_hard_reject(gmgn: dict, rugcheck: dict,
                      helius: dict, tier: str) -> tuple[bool, str]:
    """
    Return (rejected: bool, reason: str).
    Hanya reject jika ada bukti nyata bahaya.
    [DATA-FIX-2] Tambahan: reject token tanpa data safety sama sekali
    untuk T0/T1 — data 86 alert menunjukkan rug rate 27.9%, banyak
    dari token yang lulus filter tapi tidak ada data GMGN + Rugcheck.
    """
    # Rugcheck: bahaya terbukti
    if rugcheck.get("available"):
        if rugcheck.get("mint_auth"):
            return True, "🔴 MINT AUTHORITY — dev bisa cetak token!"
        if rugcheck.get("freeze_auth"):
            return True, "🔴 FREEZE AUTHORITY — dev bisa freeze wallet!"
        if rugcheck.get("risk_count", 0) > RUGCHECK_HARD_REJECT_RISKS:
            return True, f"🔴 Rugcheck: {rugcheck['risk_count']} risiko!"
        for dangerous in RUGCHECK_DANGEROUS_RISKS:
            for rname in rugcheck.get("risks_lower", []):
                if dangerous in rname:
                    return True, f"🔴 Rugcheck: '{rname}' terdeteksi!"
        # [DATA-FIX-2] Score Rugcheck sangat rendah = tanda bahaya nyata
        rc_score = rugcheck.get("score", 0)
        if rc_score < 40:
            return True, f"🔴 Rugcheck score terlalu rendah: {rc_score}/100!"

    # GMGN: bahaya terbukti
    if gmgn.get("is_honeypot"):
        return True, "🔴 HONEYPOT!"
    if gmgn.get("rug_ratio", 0) > 0.8:
        return True, "🔴 RUG RATIO TINGGI!"

    # [DATA-FIX-2] Tidak ada data safety sama sekali = terlalu berisiko
    # Rug rate 27.9% di data nyata — banyak dari token tanpa data apapun
    gmgn_ok     = gmgn.get("available", False)
    rugcheck_ok = rugcheck.get("available", False)
    if not gmgn_ok and not rugcheck_ok:
        if tier == "T0":
            return True, "🔴 T0 tanpa data GMGN + Rugcheck — rug risk terlalu tinggi!"
        elif tier == "T1":
            return True, "🔴 T1 tanpa data GMGN + Rugcheck — tidak bisa verifikasi keamanan!"

    # Helius: konsentrasi ekstrem
    if helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        if top10 > HARD_REJECT_TOP10:
            return True, f"🔴 Top10 ekstrem: {top10:.0f}%!"
        if helius.get("hub_spoke"):
            if tier in ("T1", "T2", "T3"):
                return True, "🔴 Hub & Spoke pada token established!"
            # [V11-5] T0: reject jika holders sedikit
            if tier == "T0":
                holders = gmgn.get("holders", 0) if gmgn.get("available") else 0
                if holders < 50:
                    return True, "🔴 Hub & Spoke + holders < 50 pada T0!"

    # Per-tier minimum — HANYA jika GMGN available
    if gmgn.get("available"):
        age     = gmgn.get("age_hours", 0)
        holders = gmgn.get("holders",   0)
        lp      = gmgn.get("lp_burned", 0)
        if tier == "T0":
            if age     < MIN_TOKEN_AGE_T0: return True, ""
            if holders < MIN_HOLDERS_T0:   return True, ""
        elif tier == "T1":
            if age     < MIN_TOKEN_AGE_T1: return True, ""
            if lp      < MIN_LP_BURNED_T1: return True, ""
            if holders < MIN_HOLDERS_T1:   return True, ""
        elif tier == "T2":
            if age     < MIN_TOKEN_AGE_T2: return True, ""
            if lp      < MIN_LP_BURNED_T2: return True, ""
            if holders < MIN_HOLDERS_T2:   return True, ""

    return False, ""

# ── G6: SAFETY SCORING ──────────────────────────────
def score_safety(gmgn: dict, rugcheck: dict,
                 helius: dict, tier: str) -> tuple:
    """
    Data tidak ada = 0 netral. Bukan penalti. Bukan reject.
    Returns: (score, signals, warnings, safety_pct)
    """
    score    = 0
    signals  = []
    warnings = []

    # ── GMGN ─────────────────────────────────────────
    if gmgn.get("available"):
        lp      = gmgn.get("lp_burned",     0)
        bnd     = gmgn.get("bundle_pct",    0)
        dev     = gmgn.get("dev_burned_pct", None)  # [B-11] None jika tidak ada
        holders = gmgn.get("holders",       0)
        sniper  = gmgn.get("sniper_count",  0)
        sm_buy  = gmgn.get("smart_buy",     0)
        sm_sell = gmgn.get("smart_sell",    0)

        # LP Burned
        if lp >= 100:
            score += 8; signals.append("🔒 LP Burned 100% — tidak bisa rug!")
        elif lp >= 80:
            score += 6; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp >= 50:
            score += 3; signals.append(f"🔒 LP Burned {lp:.0f}%")
        elif lp > 0:
            score += 1; warnings.append(f"⚠️ LP Burned rendah: {lp:.0f}%")
        else:
            if tier not in ("T0", "T1"):
                score -= 3; warnings.append("🔴 LP tidak diburn!")
            else:
                warnings.append("⚠️ LP belum diburn (wajar token sangat baru)")

        # Bundle
        if bnd <= 2:
            score += 5; signals.append(f"✅ Bundle {bnd:.1f}% — sangat bersih!")
        elif bnd <= MAX_BUNDLE_PCT:
            score += 3; signals.append(f"✅ Bundle {bnd:.1f}%")
        elif bnd <= 20:
            score -= 1; warnings.append(f"⚠️ Bundle {bnd:.1f}%")
        else:
            score -= 4; warnings.append(f"🔴 Bundle tinggi {bnd:.1f}%!")

        # [B-1] Dev Burned — tinggi = dev komit = BAGUS
        if dev is not None:
            if dev >= 80:
                score += 4; signals.append(f"✅ Dev burned {dev:.0f}% — komitmen!")
            elif dev >= 50:
                score += 2; signals.append(f"✅ Dev burned {dev:.0f}%")
            elif dev >= MAX_DEV_BURNED_WARN:
                score += 1  # sedikit positif, tidak tampil
            elif dev > 0:
                warnings.append(f"⚠️ Dev burned rendah: {dev:.0f}%")
            else:
                if tier not in ("T0", "T1"):
                    score -= 2; warnings.append("🔴 Dev belum burn token!")
                else:
                    warnings.append("⚠️ Dev belum burn (umum token baru)")
        else:
            warnings.append("⚠️ Dev burned: data tidak tersedia")

        # Holders
        if holders >= 2000:
            score += 3; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 500:
            score += 2; signals.append(f"✅ Holders {holders:,}")
        elif holders >= 100:
            score += 1

        # Sniper
        if sniper == 0:
            score += 2; signals.append("✅ Tidak ada sniper")
        elif sniper <= 3:
            score += 1
        elif sniper <= 10:
            warnings.append(f"⚠️ {sniper} sniper terdeteksi")
        else:
            score -= 2; warnings.append(f"🔴 Banyak sniper: {sniper}!")

        # Smart Money
        sm = sm_buy - sm_sell
        if sm > 5:
            score += 2; signals.append(f"✅ Smart money NET BUY +{sm}")
        elif sm < -5:
            score -= 1; warnings.append(f"⚠️ Smart money NET SELL {sm}")

    # ── Rugcheck ──────────────────────────────────────
    if rugcheck.get("available"):
        rc       = rugcheck.get("score",      0)
        rc_risks = rugcheck.get("risk_count", 0)

        if rc >= 90:
            score += 4; signals.append(f"✅ Rugcheck {rc}/100 — AMAN!")
        elif rc >= 70:
            score += 3; signals.append(f"✅ Rugcheck {rc}/100")
        elif rc >= 50:
            score += 1; signals.append(f"⚠️ Rugcheck {rc}/100")
        else:
            score -= 2; warnings.append(f"🔴 Rugcheck rendah: {rc}/100!")

        if rc_risks == 0:
            score += 2; signals.append("✅ Rugcheck: 0 risiko!")
        elif rc_risks <= RUGCHECK_MAX_RISKS:
            score += 1
        else:
            risk_names = rugcheck.get("risks", [])
            warnings.append(
                f"⚠️ Rugcheck: {rc_risks} risiko: "
                f"{', '.join(risk_names[:2])}")

    # ── Helius ────────────────────────────────────────
    if helius.get("available"):
        top10 = helius.get("top10_pct", 0)
        hub   = helius.get("hub_spoke",  False)

        if hub and tier == "T0":
            # T0: penalti score, bukan reject (distribusi belum merata = normal)
            score -= 5; warnings.append("🔴 Hub & Spoke (T0 — token sangat baru)")
        elif top10 <= 10:
            score += 3; signals.append(f"✅ Top10: {top10:.1f}% — sempurna!")
        elif top10 <= MAX_TOP10_PCT:
            score += 2; signals.append(f"✅ Top10: {top10:.1f}%")
        elif top10 <= 40:
            score -= 1; warnings.append(f"⚠️ Top10 agak tinggi: {top10:.1f}%")
        elif top10 <= HARD_REJECT_TOP10:
            score -= 2; warnings.append(f"🔴 Top10 tinggi: {top10:.1f}%!")

    # [V11-2] Safety bar dikalibrasi ulang.
    # Range realistis score: min ≈ -10 (banyak penalti), max ≈ 38 (semua sinyal ada).
    # v10 pakai (score+15)/50*100 → score 28 → 86%, terlalu optimistis.
    # Fix: normalisasi ke range empiris -10..38, clamp 0–100.
    SAFETY_MIN = -10
    SAFETY_MAX = 38
    safety_pct = max(0, min(100, int(
        (score - SAFETY_MIN) / (SAFETY_MAX - SAFETY_MIN) * 100)))
    return score, signals, warnings, safety_pct

# ── G6: T0 PRE-PUMP PATTERN SCORING ─────────────────
def score_prepump(price: float, open_c1: float,
                  m5: float, h1: float, h6: float,
                  vm5: float, vh1: float,
                  bm5: int, sm5: int, bh1: int, sh1: int,
                  age_h: float, liq: float, mcap: float) -> tuple:
    score    = 0
    signals  = []
    warnings = []

    # [B-12] Guard semua division
    avg5m = vh1 / 12 if vh1 > 0 else 0
    r_m5  = bm5 / max(sm5, 1)
    r_h1  = bh1 / max(sh1, 1)

    # Volume spike
    if avg5m > 0:
        vr = vm5 / avg5m
        if vr >= VOL_SPIKE_EXTREME:
            score += 8; signals.append(f"🚨 Volume EXTREME {vr:.1f}x!")
        elif vr >= VOL_SPIKE_STRONG:
            score += 6; signals.append(f"🔥 Volume spike {vr:.1f}x")
        elif vr >= 3.0:
            score += 4; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr >= 2.0:
            score += 2; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr < 0.5:
            warnings.append("⚠️ Volume sepi")

    # TX acceleration
    tx_m5  = bm5 + sm5
    avg_tx = (bh1 + sh1) / 12 if (bh1 + sh1) > 0 else 0
    if avg_tx > 0:
        ta = tx_m5 / avg_tx
        if ta >= TX_ACCEL_EXTREME:
            score += 5; signals.append(f"🌊 TX acceleration {ta:.1f}x!")
        elif ta >= TX_ACCEL_STRONG:
            score += 3; signals.append(f"📈 TX naik {ta:.1f}x")
        elif ta >= 1.5:
            score += 1

    # Pump awal
    if h6 >= T0_MIN_PUMP_PCT:
        score += 3; signals.append(f"📈 Pump h6: +{h6:.0f}%")
    if h1 >= 20:
        score += 2; signals.append(f"📈 h1 kuat: +{h1:.0f}%")
    if m5 >= 5:
        score += 2; signals.append(f"🚀 m5 hijau: +{m5:.1f}%")
    elif m5 < -MAX_CANDLE_DROP:
        return 0, [], ["🔴 Dump masif m5!"], 0.0

    # Buyer dominan
    if r_m5 >= 3.0:
        score += 4; signals.append(f"✅ Buyer sangat dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 2.0:
        score += 3; signals.append(f"✅ Buyer dominan m5: {r_m5:.1f}x")
    elif r_m5 >= 1.5:
        score += 1; signals.append(f"✅ Buyer m5: {r_m5:.1f}x")
    else:
        warnings.append(f"⚠️ Buyer ratio rendah: {r_m5:.1f}x")

    if r_h1 >= 1.5:
        score += 2; signals.append(f"✅ Buyer dominan h1: {r_h1:.1f}x")

    # Token fresh
    if 0.1 <= age_h <= 1.0:
        score += 3; signals.append(f"🆕 Token sangat fresh: {age_h:.1f}h!")
    elif age_h <= 3.0:
        score += 1; signals.append(f"🆕 Token fresh: {age_h:.1f}h")

    # C1 — [B-11 era] skip jika age < 1h (FX3: C1 belum establish)
    if age_h < 1.0:
        pass
    elif open_c1 > 0 and price >= open_c1 * 0.9:
        above_pct = (price - open_c1) / open_c1 * 100
        score += 2; signals.append(f"✅ Di atas C1 (+{above_pct:.0f}%)")
    elif open_c1 > 0:
        warnings.append("⚠️ Harga di bawah C1")

    # Liq/MCAP ratio — pool sehat susah dimanipulasi
    if mcap > 0 and liq > 0:
        lr = liq / mcap
        if lr >= T0_LIQ_MCAP_GOOD:
            score += 3; signals.append(f"💧 Pool dalam: {lr:.0%}")
        elif lr >= T0_LIQ_MCAP_MIN:
            score += 1; signals.append(f"💧 Pool cukup: {lr:.0%}")

    return score, signals, warnings, 0.0

# ── G6: DIP & RIP CORE PATTERN SCORING ──────────────
def score_core_pattern(price: float, open_c1: float,
                       m5: float, h1: float, h6: float, h24: float,
                       vm5: float, vh1: float, vh6: float,
                       bm5: int, sm5: int, bh1: int, sh1: int,
                       tracked_high: float = 0,
                       age_h: float = 0) -> tuple:
    score    = 0
    signals  = []
    warnings = []

    # [B-12] Guard division
    avg5m = vh1 / 12 if vh1 > 0 else 0
    avg_h6 = vh6 / 6 if vh6 > 0 else 0
    r_m5  = bm5 / max(sm5, 1)
    r_h1  = bh1 / max(sh1, 1)

    # Pump awal
    pump = h24 if h24 >= MIN_PUMP_PCT else (h6 if h6 >= MIN_PUMP_PCT else 0)
    if pump >= 500:
        score += 4; signals.append(f"🚀 Pump sangat kuat: +{pump:.0f}%")
    elif pump >= 200:
        score += 3; signals.append(f"🚀 Pump kuat: +{pump:.0f}%")
    elif pump >= MIN_PUMP_PCT:
        score += 2; signals.append(f"📈 Pump: +{pump:.0f}%")
    else:
        return 0, [], [], 0

    # [B-2] Dip dari tracked_high internal
    dip = calc_dip_from_high(price, tracked_high, h1, m5)
    if dip > MAX_DIP_PCT:
        warnings.append(f"🔴 Dip terlalu dalam: -{dip:.0f}%")
        return score, signals, warnings, dip
    elif dip >= 35:
        score += 3; signals.append(f"✅ Dip ideal: -{dip:.0f}%")
    elif dip >= MIN_DIP_PCT:
        score += 2; signals.append(f"✅ Dip cukup: -{dip:.0f}%")
    elif h1 > 0:
        score += 1
    else:
        warnings.append(f"⚠️ Dip dangkal: -{dip:.0f}%")

    # C1 hold — konsisten threshold 0.9x
    # [FX3] skip jika age < 1h
    if age_h >= 1.0 and open_c1 > 0:
        if price >= open_c1 * 0.9:
            above_pct = (price - open_c1) / open_c1 * 100
            score += 3; signals.append(f"✅ Hold C1 (+{above_pct:.0f}%)")
        else:
            warnings.append("🔴 Harga tembus Open C1!")
            score -= 2

    # Volume staircase
    if avg5m > 0:
        vr = vm5 / avg5m
        if vr >= 2.0:
            score += 3; signals.append(f"📊 Volume naik {vr:.1f}x!")
        elif vr >= 1.5:
            score += 2; signals.append(f"📊 Volume naik {vr:.1f}x")
        elif vr >= 1.0:
            score += 1
        elif vr < 0.3:
            warnings.append("⚠️ Volume sangat sepi")

    if avg_h6 > 0 and vh1 / avg_h6 >= 1.5:
        score += 2; signals.append(f"📈 Volume momentum h1/h6: {(vh1/avg_h6):.1f}x")

    # Konsolidasi + entry signal
    is_consol = -20 <= h1 <= 20
    if is_consol:
        if avg5m > 0 and vm5 / avg5m < 0.7:
            score += 2; signals.append("🔄 Konsolidasi sehat — volume mengecil")
        if m5 >= 10:
            score += 5; signals.append(f"🚀 BREAKOUT konsolidasi! m5: +{m5:.1f}%")
        elif m5 >= MIN_M5_SIGNAL:
            score += 3; signals.append(f"📈 Entry signal m5: +{m5:.1f}%")
        elif m5 >= 0:
            score += 1; signals.append("⏳ Konsolidasi berlanjut")
        else:
            warnings.append(f"⚠️ m5 masih negatif: {m5:.1f}%")
    else:
        if m5 >= 10:
            score += 4; signals.append(f"✅ Reversal kuat m5: +{m5:.1f}%")
        elif m5 >= MIN_M5_SIGNAL:
            score += 2; signals.append(f"✅ Reversal m5: +{m5:.1f}%")
        elif m5 < -MAX_CANDLE_DROP:
            return score, signals, ["🔴 Dump masif m5!"], dip

    # Buyer ratio
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

    return score, signals, warnings, dip

# ── DETERMINE TIER ────────────────────────────────────
def get_tier(mcap: float, liq: float, vol_h1: float) -> str | None:
    if (T0_MIN_MCAP <= mcap <= T0_MAX_MCAP
            and liq >= T0_MIN_LIQUIDITY
            and vol_h1 >= T0_MIN_VOL_1H):
        # [B-12] Guard mcap > 0
        if mcap > 0 and liq / mcap < T0_LIQ_MCAP_MIN:
            print(f"[T0 SKIP] liq/mcap={liq/mcap:.2f} < {T0_LIQ_MCAP_MIN}")
            return None
        return "T0"
    if (T1_MIN_MCAP <= mcap <= T1_MAX_MCAP
            and liq >= T1_MIN_LIQUIDITY
            and vol_h1 >= T1_MIN_VOL_1H):
        return "T1"
    if (T2_MIN_MCAP <= mcap <= T2_MAX_MCAP
            and liq >= T2_MIN_LIQUIDITY
            and vol_h1 >= T2_MIN_VOL_1H):
        return "T2"
    if (T3_MIN_MCAP <= mcap <= T3_MAX_MCAP
            and liq >= T3_MIN_LIQUIDITY
            and vol_h1 >= T3_MIN_VOL_1H):
        return "T3"
    return None

# ── DYNAMIC SL/TP [V11-3] ────────────────────────────
def get_sl_tp(price: float, tier: str,
              dip_pct: float = 0.0, pump_pct: float = 0.0) -> tuple:
    """
    [V11-3] SL/TP dinamis berbasis konteks — v10 statis per tier.

    SL: makin dalam dip sebelumnya → SL lebih longgar (token volatile).
        Base SL per tier, lebar dikurangi 30% dari dip_pct aktual.
        Min SL: 8%, Max SL: 30%.

    TP: makin kuat pump awal → TP lebih tinggi (momentum ada).
        Base TP per tier, dinaikkan proporsional pump_pct.
        TP2 selalu ≥ 2× TP1 distance dari entry.

    R:R minimum dijaga: (TP1 - entry) ≥ 1.5 × (entry - SL).
    """
    # Base SL per tier (persentase loss dari entry)
    base_sl_pct = {"T0": 20, "T1": 15, "T2": 12, "T3": 10}.get(tier, 15)

    # Pelebaran dinamis: dip dalam → SL lebih longgar, tapi max 30%
    dip_adj    = min(dip_pct * 0.3, 10.0)   # max tambah 10%
    sl_pct_raw = min(base_sl_pct + dip_adj, 30.0)
    sl_pct     = max(sl_pct_raw, 8.0)

    sl = price * (1 - sl_pct / 100)
    sl_distance = price - sl   # positif

    # Base TP multiplier per tier
    base_tp1_r = {"T0": 2.5, "T1": 2.0, "T2": 1.8, "T3": 1.5}.get(tier, 2.0)
    base_tp2_r = {"T0": 5.0, "T1": 3.5, "T2": 2.8, "T3": 2.2}.get(tier, 3.0)

    # Bonus TP jika pump awal kuat
    pump_bonus = 0.0
    if pump_pct >= 500:   pump_bonus = 1.5
    elif pump_pct >= 300: pump_bonus = 1.0
    elif pump_pct >= 150: pump_bonus = 0.5

    tp1_r = base_tp1_r + pump_bonus * 0.5
    tp2_r = base_tp2_r + pump_bonus

    tp1 = price * tp1_r
    tp2 = price * tp2_r

    # Guard R:R minimum 1.5 untuk TP1
    min_tp1 = price + sl_distance * 1.5
    if tp1 < min_tp1:
        tp1 = min_tp1
        tp2 = price + (tp1 - price) * 2.0   # TP2 = 2× TP1 distance

    return sl, tp1, tp2, int(sl_pct)

# ── MAIN ANALYZE ─────────────────────────────────────
def analyze_pair(pair: dict) -> dict | None:
    try:
        addr    = pair.get("baseToken", {}).get("address", "")
        if not addr:
            return None
        name    = pair.get("baseToken", {}).get("name",   "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        pair_id = pair.get("pairAddress", "")
        chain   = pair.get("chainId",    "")
        dex     = pair.get("dexId",      "")
        price   = float(pair.get("priceUsd", 0) or 0)

        pc  = pair.get("priceChange", {})
        m5  = float(pc.get("m5",  0) or 0)
        h1  = float(pc.get("h1",  0) or 0)
        h6  = float(pc.get("h6",  0) or 0)
        h24 = float(pc.get("h24", 0) or 0)

        vol  = pair.get("volume", {})
        vm5  = float(vol.get("m5",  0) or 0)
        vh1  = float(vol.get("h1",  0) or 0)
        vh6  = float(vol.get("h6",  0) or 0)
        vh24 = float(vol.get("h24", 0) or 0)

        liq  = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

        tx    = pair.get("txns", {})
        bm5   = int(tx.get("m5",  {}).get("buys",  0) or 0)
        sm5   = int(tx.get("m5",  {}).get("sells", 0) or 0)
        bh1   = int(tx.get("h1",  {}).get("buys",  0) or 0)
        sh1   = int(tx.get("h1",  {}).get("sells", 0) or 0)
        txh24 = (int(tx.get("h24", {}).get("buys",  0) or 0)
               + int(tx.get("h24", {}).get("sells", 0) or 0))

        # ── G1: BASIC FILTER ──────────────────────────
        if price <= 0 or mcap <= 0:
            return None
        tier = get_tier(mcap, liq, vh1)
        if not tier:
            return None

        # ── G2: PATTERN GATE ──────────────────────────
        if not check_pattern_exists(tier, m5, h1, h6, h24, vm5, vh1):
            return None

        # [V11-4] C1 dibaca saja untuk F2 check — belum ditulis.
        # Penulisan C1 dipindah ke setelah G5 agar harga reject tidak tersimpan.
        existing_entry = price_tracker.get(addr)
        if existing_entry is None:
            # Token baru: pakai harga sekarang sebagai estimasi C1 sementara
            open_c1_preview = price
        elif isinstance(existing_entry, dict):
            open_c1_preview = existing_entry.get("price", price)
        else:
            open_c1_preview = float(existing_entry)
        tracked_high = get_tracked_high(addr)

        # [B-10] age_h_fallback clamp ≥ 0
        pair_created   = pair.get("pairCreatedAt", 0) or 0
        age_h_fallback = max(0.0, round(
            (time.time() - pair_created / 1000) / 3600, 2)
        ) if pair_created else 0.0

        # ── G3: TEMPORAL FILTER F1 & F2 ───────────────
        if not check_temporal_filter(tier, m5, h1, open_c1_preview, price, symbol):
            return None

        # ── G4: PANGGIL API ───────────────────────────
        gmgn_data     = get_gmgn(addr)
        rugcheck_data = get_rugcheck(addr)
        helius_data   = get_helius(addr)

        # [B-3] Resolve age_h SEBELUM G5
        age_h = gmgn_data.get("age_hours", 0) or age_h_fallback
        if age_h != gmgn_data.get("age_hours", 0):
            print(f"[AGE FALLBACK] {addr[:8]} → {age_h:.1f}h dari DexScreener")
            gmgn_data = {**gmgn_data, "age_hours": age_h}  # inject ke copy

        # ── G5: HARD REJECT ───────────────────────────
        rejected, reason = check_hard_reject(
            gmgn_data, rugcheck_data, helius_data, tier)
        if rejected:
            if reason:
                print(f"[HARD REJECT] {symbol} — {reason}")
                # [LOG-FIX-1] Cache token berbahaya — skip 60 menit ke depan
                hard_reject_cache[addr] = (reason, time.time())
            return None

        # [V11-4] Token lolos G5 — baru tulis/update C1 yang valid
        open_c1 = get_open_c1(addr, price)

        # ── G6: PATTERN SCORING (primer) ──────────────
        if tier == "T0":
            p_score, p_sig, p_warn, dip_pct = score_prepump(
                price, open_c1, m5, h1, h6, vm5, vh1,
                bm5, sm5, bh1, sh1, age_h, liq, mcap)
            pump_pct = max(h6, h1)
        else:
            p_score, p_sig, p_warn, dip_pct = score_core_pattern(
                price, open_c1, m5, h1, h6, h24,
                vm5, vh1, vh6, bm5, sm5, bh1, sh1,
                tracked_high, age_h)
            pump_pct = h24 if h24 >= MIN_PUMP_PCT else h6

        # ── G6: SAFETY SCORING (sekunder) ─────────────
        s_score, s_sig, s_warn, safety_pct = score_safety(
            gmgn_data, rugcheck_data, helius_data, tier)

        total        = p_score + s_score
        all_signals  = p_sig  + s_sig
        all_warnings = p_warn + s_warn

        # [B-14] Sort: 🔴 dulu, limit 4
        red_w   = [w for w in all_warnings if w.startswith("🔴")]
        other_w = [w for w in all_warnings if not w.startswith("🔴")]
        all_warnings = (red_w + other_w)[:4]

        # Reject jika 2+ critical warning
        if len(red_w) >= 2:
            return None

        # ── GRADE — hanya A+ dan A, Grade B dihapus ──────
        # [DATA-FIX-1] Grade B win rate terlalu rendah di data nyata.
        # Lebih sedikit alert tapi kualitas lebih tinggi.
        if tier == "T0":
            if   total >= 15: grade, status = "A",  "🔥 PRE-PUMP! Entry ultra early!"
            else: return None
        elif tier == "T1":
            if   total >= 20: grade, status = "A",  "🟢 Setup bagus — EARLY!"
            else: return None
        elif tier == "T2":
            if   total >= 24: grade, status = "A+", "💎 Setup premium!"
            elif total >= 18: grade, status = "A",  "🟢 Setup sangat bagus!"
            else: return None
        else:  # T3
            if   total >= 26: grade, status = "A+", "💎 Setup premium!"
            elif total >= 20: grade, status = "A",  "🟢 Setup bagus"
            else: return None

        pos_map = {"T0": T0_MAX_POSITION, "T1": T1_MAX_POSITION,
                   "T2": T2_MAX_POSITION, "T3": T3_MAX_POSITION}
        # [V11-3] SL/TP dinamis — pakai konteks dip + pump
        sl, tp1, tp2, sl_pct = get_sl_tp(price, tier, dip_pct, pump_pct)
        social = get_social(symbol, addr)

        # [B-11] Safety fields — None jika tidak tersedia
        lp_out  = (gmgn_data.get("lp_burned") if gmgn_data.get("available")
                   else (100.0 if rugcheck_data.get("lp_burned")
                         else (50.0 if rugcheck_data.get("lp_locked")
                               else None)))

        return {
            "name": name, "symbol": symbol, "addr": addr,
            "chain": chain, "dex": dex, "pair_id": pair_id,
            "price": price,
            "pump_pct":  round(pump_pct, 1),
            "dip_pct":   round(dip_pct,  1),
            "open_c1":   open_c1,
            # [FX3] holds_c1 display: netral (True) untuk age < 1h
            "holds_c1": (True if age_h < 1.0
                         else (price >= open_c1 * 0.9 if open_c1 > 0 else True)),
            "m5": m5, "h1": h1, "h6": h6, "h24": h24,
            "vm5": vm5, "vh1": vh1, "vh24": vh24,
            "bm5": bm5, "sm5": sm5, "bh1": bh1, "sh1": sh1, "txh24": txh24,
            "liq": liq, "mcap": mcap,
            "total": total, "status": status, "grade": grade,
            "tier": tier, "max_pos": pos_map[tier],
            "signals":    all_signals[:5],
            "warnings":   all_warnings,
            "safety_pct": safety_pct,
            # [B-11] None = data tidak ada
            "lp_burned":      lp_out,
            "bundle_pct":     gmgn_data.get("bundle_pct")     if gmgn_data.get("available") else None,
            "dev_burned_pct": gmgn_data.get("dev_burned_pct") if gmgn_data.get("available") else None,
            "holders":        gmgn_data.get("holders")         if gmgn_data.get("available") else None,
            "sniper_count":   gmgn_data.get("sniper_count", 0) if gmgn_data.get("available") else None,
            "age_h":          age_h,
            "smart_buy":      gmgn_data.get("smart_buy",  0),
            "smart_sell":     gmgn_data.get("smart_sell", 0),
            "gmgn_ok":        gmgn_data.get("available",  False),
            "rc_ok":          rugcheck_data.get("available",  False),
            "rc_score":       rugcheck_data.get("score",      0),
            "rc_risks":       rugcheck_data.get("risk_count", 0),
            "mint_auth":      rugcheck_data.get("mint_auth",  False),
            "helius_ok":      helius_data.get("available",  False),
            "top10_pct":      helius_data.get("top10_pct",  0),
            "hub_spoke":      helius_data.get("hub_spoke",  False),
            "social_ok":     social.get("available",     False),
            "galaxy_score":  social.get("galaxy_score",  0),
            "alt_rank":      social.get("alt_rank",      0),
            "mention_trend": social.get("mention_trend", 0),
            "kol_active":    social.get("kol_active",    False),
            "sl": sl, "tp1": tp1, "tp2": tp2, "sl_pct": sl_pct,
            "chart":     f"https://dexscreener.com/{chain}/{pair_id}",
            "gmgn_url":  f"https://gmgn.ai/sol/token/{addr}",
            "axiom_url": f"https://axiom.xyz/sol/{addr}",
        }

    except Exception as e:
        print(f"[ANALYZE ERR] {e}")
        return None

# ── FORMAT ALERT ─────────────────────────────────────
def format_alert(s: dict, is_update: bool = False) -> str:
    tier_map = {
        "T0": ("🔥", "PRE-PUMP ULTRA EARLY",
               f"MCAP ${s['mcap']/1000:.0f}K — potensi 10-50x! SANGAT BERISIKO",
               f"Max {s['max_pos']} SOL ⛔ EXTREME RISK"),
        "T1": ("🟣", "EARLY ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — potensi 5-10x!",
               f"Max {s['max_pos']} SOL ⚠️ HIGH RISK"),
        "T2": ("🟢", "NORMAL ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — setup bagus",
               f"Max {s['max_pos']} SOL"),
        "T3": ("🔵", "LATE ENTRY",
               f"MCAP ${s['mcap']/1000:.0f}K — established",
               f"Max {s['max_pos']} SOL"),
    }
    temoji, tlabel, tdesc, tpos = tier_map.get(s["tier"], ("⚪", "", "", ""))
    grade_emoji = {"A+": "💎", "A": "🏆", "B": "🥈"}.get(s["grade"], "")
    alert_label = "🔄 UPDATE v11.0!" if is_update else "🚨 DIP &amp; RIP ALERT v11.0!"
    mode_lbl    = "🔥 PRE-PUMP" if s["tier"] == "T0" else "📉 DIP &amp; RIP"

    signals_text = "\n".join(s["signals"]) if s["signals"] else "—"
    warn_text    = "\n".join(s["warnings"]) if s["warnings"] else "✅ Tidak ada warning"

    # [B-11] Display N/A jika None
    lp  = s.get("lp_burned")
    bnd = s.get("bundle_pct")
    dev = s.get("dev_burned_pct")
    hld = s.get("holders")
    snp = s.get("sniper_count")

    lp_str  = f"{lp:.0f}%"         if lp  is not None else "N/A"
    bnd_str = f"{bnd:.1f}%"        if bnd is not None else "N/A"
    dev_str = f"{dev:.0f}% burned" if dev is not None else "N/A"
    hld_str = f"{hld:,}"           if hld is not None else "N/A"
    snp_str = str(snp)             if snp is not None else "N/A"

    lp_e  = ("🔒" if lp  is not None and lp  >= 80
             else "⚠️" if lp  is not None and lp  >= 50
             else "🔴" if lp  is not None and lp  <  50
             else "❓")
    bnd_e = ("✅" if bnd is not None and bnd <= 5
             else "⚠️" if bnd is not None and bnd <= 15
             else "🔴" if bnd is not None
             else "❓")
    # [B-1] dev_burned: tinggi = bagus
    dev_e = ("✅" if dev is not None and dev >= 80
             else "🟡" if dev is not None and dev >= 20
             else "🔴" if dev is not None and dev == 0
             else "⚠️" if dev is not None
             else "❓")

    # Safety bar
    sp        = s.get("safety_pct", 0)
    bar_fill  = "█" * (sp // 10)
    bar_empty = "░" * (10 - sp // 10)
    sp_color  = "🟢" if sp >= 60 else "🟡" if sp >= 30 else "🔴"

    # Smart Money
    sm_line = ""
    if s.get("gmgn_ok"):
        sm   = s["smart_buy"] - s["smart_sell"]
        sm_t = (f"+{sm} NET BUY 🟢" if sm > 0
                else f"{sm} NET SELL 🔴" if sm < 0
                else "Netral")
        sm_line = f"\n  💡 Smart $: {sm_t}"

    # Rugcheck block
    rc_block = ""
    if s.get("rc_ok"):
        rc_e   = ("✅" if s["rc_score"] >= 70
                  else "⚠️" if s["rc_score"] >= 50
                  else "🔴")
        mint_t = "🔴 ADA!" if s.get("mint_auth") else "✅ Disabled"
        rc_block = (f"\n  {rc_e} Rugcheck: <b>{s['rc_score']}/100</b> "
                    f"| Risks: <b>{s['rc_risks']}</b> | Mint: {mint_t}")

    # Helius block
    helius_block = ""
    if s["helius_ok"]:
        hub_t = "⚠️ ADA!" if s["hub_spoke"] else "✅ Tidak ada"
        helius_block = (f"\n  🏦 Top10: <b>{s['top10_pct']:.1f}%</b> "
                        f"| Hub&amp;Spoke: {hub_t}")

    # Social block — display only, tidak ada di score
    social_block = ""
    if s.get("social_ok"):
        arr  = "📈" if s["mention_trend"] >= 0 else "📉"
        kol  = "👑 YA!" if s["kol_active"] else "—"
        social_block = (f"\n🌐 <b>Social:</b> Galaxy {s['galaxy_score']}/100 "
                        f"| #{s['alt_rank']} "
                        f"| {arr}{s['mention_trend']:+.0f}% | KOL: {kol}")

    vol24_t  = (f"${s['vh24']/1e6:.1f}M" if s['vh24'] >= 1e6
                else f"${s['vh24']/1000:.0f}K")
    c1_t     = f"${s['open_c1']:.8f}" if s['open_c1'] > 0 else "N/A"
    age_t    = f"{s['age_h']:.1f}h"   if s['age_h']   > 0 else "N/A"
    mode_lbl_full = "🔥 PRE-PUMP MODE" if s["tier"] == "T0" else "📉 DIP &amp; RIP MODE"

    dip_line = (f"📈 Momentum h6: <b>+{s['pump_pct']}%</b>"
                if s["tier"] == "T0"
                else f"📉 Dip dari high: <b>-{s['dip_pct']}%</b>")

    footer = ("⛔ T0 ULTRA EARLY — Max 0.02 SOL! Konfirmasi chart dulu!"
              if s["tier"] == "T0"
              else "⚡ Konfirmasi LP + Bundle di GMGN / Axiom!")

    # [B-12] Guard div zero di buyer ratio display
    bh1_ratio = s["bh1"] / max(s["sh1"], 1)
    bm5_ratio = s["bm5"] / max(s["sm5"], 1)

    return f"""
{alert_label} {mode_lbl_full}

{temoji} <b>{tlabel}</b>
📍 {tdesc}
💰 <b>{tpos}</b>

🪙 <b>{s['name']} ({s['symbol']})</b>
📊 {s['dex'].upper()} | Solana | ⏱ {age_t}

{grade_emoji} <b>{s['status']}</b>
📊 Score: {s['total']} | Grade: {s['grade']}

💹 Harga: <b>${s['price']:.8f}</b>
📈 Pump: <b>+{s['pump_pct']}%</b>
{dip_line}
🏁 Open C1: {c1_t}
{'✅' if s['holds_c1'] else '🔴'} Hold C1: <b>{'YA ✅' if s['holds_c1'] else 'TIDAK ❌'}</b>
📊 m5: <b>{s['m5']:+.1f}%</b> | h1: {s['h1']:+.1f}% | h6: {s['h6']:+.1f}%

🔒 <b>SAFETY</b> {sp_color} {sp}/100
  [{bar_fill}{bar_empty}]
  {lp_e} LP Burned: <b>{lp_str}</b>
  {bnd_e} Bundle: <b>{bnd_str}</b>
  {dev_e} Dev Burned: <b>{dev_str}</b>
  👥 Holders: <b>{hld_str}</b>
  🎯 Snipers: <b>{snp_str}</b>{sm_line}{rc_block}{helius_block}{social_block}

✅ <b>SINYAL:</b>
{signals_text}

⚠️ <b>PERINGATAN:</b>
{warn_text}

💰 <b>ENTRY ZONE:</b>
  Beli:        <b>${s['price']:.8f}</b>
  🔴 Stop Loss: <b>${s['sl']:.8f}</b> (-{s['sl_pct']}%)
  🟡 Target 1:  <b>${s['tp1']:.8f}</b> — jual 50%
  🟢 Target 2:  <b>${s['tp2']:.8f}</b> — jual sisanya
  📌 R:R ≈ {round((s['tp1']-s['price'])/max(s['price']-s['sl'],0.000000001),1)}:1

📊 Vol 24H: {vol24_t} | Liq: ${s['liq']/1000:.0f}K
🔄 Buy/Sell h1: {bh1_ratio:.1f}x | m5: {bm5_ratio:.1f}x

🔗 <a href="{s['chart']}">Chart</a> | <a href="{s['gmgn_url']}">GMGN</a> | <a href="{s['axiom_url']}">Axiom</a>

{footer}
⚠️ BUKAN financial advice. DYOR!
""".strip()

# ── EXIT ENGINE [V11-7] ──────────────────────────────
def check_exits():
    """
    [V11-7] Periksa semua token yang sedang terbuka (ada di price_tracker
    dengan entry_sl/entry_tp1/entry_tp2 tersimpan). Kirim alert jika:
    - Harga saat ini < SL              → ⛔ STOP LOSS
    - Harga saat ini > TP1 (sekali)    → 🟡 TP1 HIT — jual 50%
    - Harga saat ini > TP2             → 🟢 TP2 HIT — jual sisanya
    - Tracked high jauh di atas harga  → trailing SL otomatis update
    """
    if not price_tracker:
        return

    to_close = []
    now      = time.time()

    for addr, entry in list(price_tracker.items()):
        if not isinstance(entry, dict):
            continue
        # Hanya proses token yang punya SL/TP tersimpan
        if "entry_sl" not in entry:
            continue

        # Tidak cek token yang baru saja di-alert (< 2 menit)
        alerted_ts = alerted_tokens.get(addr, 0)
        if now - alerted_ts < 120:
            continue

        pair = get_pair(addr)
        if not pair:
            continue

        price_now = float(pair.get("priceUsd", 0) or 0)
        if price_now <= 0:
            continue

        sl   = entry["entry_sl"]
        tp1  = entry["entry_tp1"]
        tp2  = entry["entry_tp2"]
        sym  = entry.get("symbol", addr[:8])
        tier = entry.get("tier",   "T?")

        # Update trailing SL jika harga naik > 30% dari entry
        entry_price = entry.get("entry_price", 0)
        if entry_price > 0 and price_now > entry_price * 1.30:
            new_trailing_sl = price_now * 0.85
            if new_trailing_sl > sl:
                old_sl = sl
                price_tracker[addr]["entry_sl"] = new_trailing_sl
                sl = new_trailing_sl
                print(f"[TRAILING SL] {sym} SL update "
                      f"${old_sl:.8f} → ${new_trailing_sl:.8f}")
                send_telegram(
                    f"🔄 <b>TRAILING SL UPDATE</b>\n\n"
                    f"🪙 <b>{sym}</b> | {tier}\n"
                    f"💹 Harga: <b>${price_now:.8f}</b>\n"
                    f"🔴 SL baru: <b>${new_trailing_sl:.8f}</b>\n"
                    f"📈 Profit saat ini: "
                    f"<b>+{(price_now/entry_price - 1)*100:.1f}%</b>\n\n"
                    f"⚡ SL dinaikkan otomatis untuk kunci profit!")

        # Cek SL kena
        if price_now <= sl:
            pct = (price_now / entry_price - 1) * 100 if entry_price > 0 else 0
            print(f"[SL HIT] {sym} ${price_now:.8f} ≤ SL ${sl:.8f}")
            send_telegram(
                f"⛔ <b>STOP LOSS HIT</b>\n\n"
                f"🪙 <b>{sym}</b> | {tier}\n"
                f"💹 Harga: <b>${price_now:.8f}</b>\n"
                f"🔴 SL: <b>${sl:.8f}</b>\n"
                f"📉 P&amp;L: <b>{pct:+.1f}%</b>\n\n"
                f"⚠️ Keluar posisi sekarang!")
            # Sync tracker: trailing SL dengan profit = win, SL biasa = loss
            outcome_sl = "win_trailing_sl" if pct > 0 else "loss_sl"
            _sync_tracker_exit(addr, outcome_sl, price_now, now)
            to_close.append(addr)
            continue

        # Cek TP2 kena
        if price_now >= tp2 and not entry.get("tp2_hit"):
            pct = (price_now / entry_price - 1) * 100 if entry_price > 0 else 0
            print(f"[TP2 HIT] {sym} ${price_now:.8f} ≥ TP2 ${tp2:.8f}")
            send_telegram(
                f"🟢 <b>TARGET 2 TERCAPAI!</b>\n\n"
                f"🪙 <b>{sym}</b> | {tier}\n"
                f"💹 Harga: <b>${price_now:.8f}</b>\n"
                f"🎯 TP2: <b>${tp2:.8f}</b>\n"
                f"📈 P&amp;L: <b>+{pct:.1f}%</b> 🎉\n\n"
                f"✅ Jual SEMUA sisa posisi!")
            # Sync tracker: TP2 hit = win_tp2
            _sync_tracker_exit(addr, "win_tp2", price_now, now)
            price_tracker[addr]["tp2_hit"] = True
            to_close.append(addr)
            continue

        # Cek TP1 kena (hanya sekali) — posisi TIDAK ditutup, tidak sync tracker
        if price_now >= tp1 and not entry.get("tp1_hit"):
            pct = (price_now / entry_price - 1) * 100 if entry_price > 0 else 0
            print(f"[TP1 HIT] {sym} ${price_now:.8f} ≥ TP1 ${tp1:.8f}")
            send_telegram(
                f"🟡 <b>TARGET 1 TERCAPAI!</b>\n\n"
                f"🪙 <b>{sym}</b> | {tier}\n"
                f"💹 Harga: <b>${price_now:.8f}</b>\n"
                f"🎯 TP1: <b>${tp1:.8f}</b>\n"
                f"📈 P&amp;L: <b>+{pct:.1f}%</b>\n\n"
                f"💡 Jual <b>50%</b> posisi sekarang!\n"
                f"🟢 Biarkan 50% sisanya jalan ke TP2: <b>${tp2:.8f}</b>")
            price_tracker[addr]["tp1_hit"] = True
            # Tracker TIDAK di-sync di sini — posisi masih terbuka 50%

    # Hapus token yang sudah ditutup dari tracker
    for addr in to_close:
        price_tracker.pop(addr, None)
        alerted_tokens.pop(addr, None)
    if to_close:
        save_state()

# ── MAIN LOOP ─────────────────────────────────────────
def scan_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")

    # [V11-9] Cek kondisi pasar SOL terlebih dahulu
    sol_h1 = get_sol_context()
    sol_bearish = sol_h1 < SOL_SUPPRESS_THRESHOLD
    if sol_bearish:
        print(f"[SOL WARN] h1={sol_h1:+.1f}% — T0/T1 akan di-suppress")

    # [V11-7] Jalankan exit engine sebelum scan token baru
    check_exits()   # exit engine bawaan v11 (Telegram alert saja)
    if EXECUTION_ENABLED and _engine:
        _engine.run_exit_checks()  # exit engine baru (eksekusi on-chain)

    trending = get_trending_tokens()
    new_tok  = get_new_tokens()

    # [V11-10] Prioritaskan trending, deduplikasi, lalu sort agar deterministik
    trending_addrs = []
    for t in trending:
        a = t.get("tokenAddress", "") or t.get("address", "")
        if a:
            trending_addrs.append(a)

    new_addrs = []
    for t in new_tok:
        a = t.get("tokenAddress", "") or t.get("address", "")
        if a and a not in set(trending_addrs):
            new_addrs.append(a)

    # Trending diproses duluan, masing-masing sorted untuk determinisme
    ordered_addrs = sorted(set(trending_addrs)) + sorted(set(new_addrs))

    print(f"[SCAN] {len(ordered_addrs)} token ditemukan "
          f"({len(trending_addrs)} trending + {len(new_addrs)} new)")

    t0 = t1 = t2 = t3 = 0
    state_dirty = False
    now = time.time()

    for addr in ordered_addrs[:50]:
        now = time.time()

        # [LOG-FIX-1] Cek hard reject cache sebelum ANY API call
        cached_reject = hard_reject_cache.get(addr)
        if cached_reject:
            if now - cached_reject[1] < HARD_REJECT_TTL:
                continue   # masih dalam TTL — skip tanpa API call
            else:
                del hard_reject_cache[addr]  # expired — retry

        # [V11-8] Cooldown per tier — ambil dari alerted_tokens dulu
        last_alert = alerted_tokens.get(addr, 0)
        # Pakai cooldown T1 sebagai default sebelum tier diketahui
        if now - last_alert < ALERT_COOLDOWN_BY_TIER.get("T1", ALERT_COOLDOWN_SEC):
            continue

        pair = get_pair(addr)
        if not pair:
            continue

        # [V11-8] Tentukan tier dari pair data untuk cooldown yang akurat
        mcap_quick  = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
        liq_quick   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vh1_quick   = float(pair.get("volume", {}).get("h1", 0) or 0)
        tier_quick  = get_tier(mcap_quick, liq_quick, vh1_quick)
        cooldown    = ALERT_COOLDOWN_BY_TIER.get(tier_quick or "T1", ALERT_COOLDOWN_SEC)
        if now - last_alert < cooldown:
            continue

        # [V11-9] Suppress T0/T1 saat SOL bearish
        if sol_bearish and tier_quick in ("T0", "T1"):
            print(f"[SOL SUPPRESS] {addr[:8]} {tier_quick} — SOL h1={sol_h1:+.1f}%")
            continue

        signal = analyze_pair(pair)
        if signal:
            tier = signal["tier"]
            if   tier == "T0": t0 += 1
            elif tier == "T1": t1 += 1
            elif tier == "T2": t2 += 1
            else:              t3 += 1

            # UPDATE label jika token sama dalam 15 menit
            is_update = last_alert > 0 and now - last_alert < UPDATE_LABEL_WINDOW_SEC
            print(f"[{tier}{'*UPD' if is_update else ''}] "
                  f"{signal['symbol']} Grade:{signal['grade']} "
                  f"Score:{signal['total']} Safety:{signal['safety_pct']}/100")

            # [B-9] Tandai dulu, simpan state terlepas dari Telegram
            alerted_tokens[addr] = now
            state_dirty          = True

            # [V11-7] Simpan SL/TP ke price_tracker untuk exit engine
            if addr in price_tracker and isinstance(price_tracker[addr], dict):
                price_tracker[addr].update({
                    "entry_price": signal["price"],
                    "entry_sl":    signal["sl"],
                    "entry_tp1":   signal["tp1"],
                    "entry_tp2":   signal["tp2"],
                    "symbol":      signal["symbol"],
                    "tier":        signal["tier"],
                    "tp1_hit":     False,
                    "tp2_hit":     False,
                })

            # Kirim sinyal hanya jika tidak di-pause
            if _bot_paused:
                print(f"[KS] Paused — sinyal {signal['symbol']} ditahan")
            else:
                if send_telegram(format_alert(signal, is_update)):
                    # [LOG-FIX-2] Rekam ke tracker hanya jika Telegram berhasil
                    if not is_update:
                        record_alert(signal)
                # ── EXECUTION ENGINE ──────────────────────────────
                if EXECUTION_ENABLED and _engine and not is_update:
                    _engine.on_signal(signal)

        time.sleep(0.5)

    # [B-9] save_state di akhir scan, bukan per token
    if state_dirty:
        save_state()

    # [LOG-FIX-2] Pantau outcome alert tracker yang masih open
    check_open_alerts()
    print(f"[DONE] T0:{t0} T1:{t1} T2:{t2} T3:{t3}")

def main():
    print("=" * 60)
    print("  DIP & RIP BOT v11.1 — HARDENED + LOG FIXES")
    print("  Core: Token Aman + Dump Sehat + Second Pump")
    print("  10 fix v11 + 2 fix dari analisis log")
    print("=" * 60)
    print(f"  Helius  : {'✅' if HELIUS_KEY     else '⚠️  Belum ada key'}")
    print(f"  Lunar   : {'✅' if LUNARCRUSH_KEY else '⚠️  Belum ada key'}")
    print(f"  State   : {'✅ ' + str(STATE_FILE)   if STATE_FILE.exists()   else '🆕 Fresh start'}")
    print(f"  Tracker : {'✅ ' + str(TRACKER_FILE) if TRACKER_FILE.exists() else '🆕 Fresh start'}")
    print(f"  T0 Pre-pump : ${T0_MIN_MCAP/1000:.0f}K–${T0_MAX_MCAP/1000:.0f}K")
    print(f"  T1 Early    : ${T1_MIN_MCAP/1000:.0f}K–${T1_MAX_MCAP/1000:.0f}K")
    print(f"  T2 Normal   : ${T2_MIN_MCAP/1000:.0f}K–${T2_MAX_MCAP/1000:.0f}K")
    print(f"  T3 Late     : ${T3_MIN_MCAP/1000:.0f}K–${T3_MAX_MCAP/1_000_000:.0f}M")
    print("=" * 60)

    send_telegram(
        "🤖 <b>DIP &amp; RIP Bot v11.3 aktif!</b>\n\n"
        "📊 <b>Data-driven fixes (86 closed alert):</b>\n"
        "✅ Grade B dihapus — hanya A dan A+\n"
        "✅ Hard reject: no-data T0/T1 = skip\n"
        "✅ Rugcheck &lt; 40 = hard reject\n"
        "✅ /status open count lebih jelas\n\n"
        "🏗️ <b>Arsitektur 7 gate + Exit Engine + Tracker:</b>\n"
        "G1 Basic → G2 Pattern → G3 F1/F2\n"
        "→ G4 API → G5 Hard reject\n"
        "→ G6 Score → G7 Alert\n"
        "→ Exit Engine + Tracker Sync\n\n"
        "🎮 /status /pnl /pnl 7 /balance /pause /resume /help\n"
        "🚨 Scan setiap 30 detik!"
    )

    # Mulai kill switch built-in (selalu aktif, tidak butuh execution_engine)
    start_kill_switch()

    # Mulai execution engine (kill switch thread)
    if EXECUTION_ENABLED and _engine:
        _engine.start()

    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"[ERR] {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    main()
