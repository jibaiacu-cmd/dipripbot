# 🤖 DIP & RIP Alert Bot

Bot scanner pola Dip & Rip untuk memecoin Solana.

## Setup

### 1. Buat Telegram Bot
- Buka @BotFather di Telegram
- Ketik /newbot
- Simpan BOT_TOKEN yang diberikan

### 2. Dapat Chat ID
- Buka: https://api.telegram.org/bot[TOKEN]/getUpdates
- Cari angka "id"

### 3. Deploy ke Railway
- Buka railway.app
- New Project → Deploy from GitHub
- Set environment variables:
  - BOT_TOKEN = token dari BotFather
  - CHAT_ID = chat id kamu
- Deploy!

## Parameter (bisa diubah di bot.py)
- MIN_PUMP_PCT = 150 (minimal pump awal %)
- MAX_DIP_PCT = 70 (maksimal dip yang diizinkan %)
- MIN_VOLUME_USD = 50000 (minimal volume)
- SCAN_INTERVAL_SEC = 30 (scan tiap berapa detik)

## Disclaimer
Bukan financial advice. Trading crypto berisiko tinggi.
