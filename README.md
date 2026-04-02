# Bittensor Stake-Move Automation

Runs daily on a Linux VM (via systemd timer). Each run sweeps accumulated stake from an **origin hotkey** into a **destination hotkey**, logs every sweep to Google Sheets, and sends Telegram notifications — including alerts on bi-weekly distribution days.

> Originally built for SN35. Now fully configurable — zero hardcoded values, any number of distribution partners.

---

## How it works

```
systemd timer (8 AM local)
  → daily_stake_move.py
      → btcli  : move stake from ORIGIN_HOTKEY → DEST_HOTKEY
      → sheets : log balance, sweep amount, running total
      → telegram: notify on sweep or skip; alert on distribution days
```

---

## What you need before starting

| Requirement | Notes |
|---|---|
| Linux VM (Ubuntu 22.04+) | Any cloud provider works |
| Python 3.10+ | Usually pre-installed |
| `btcli` installed | `pip install bittensor bittensor-cli` |
| A Bittensor wallet on the VM | Cold-key + hot-keys already registered |
| (Optional) Telegram bot | Create via [@BotFather](https://t.me/BotFather) |
| (Optional) Google Sheet + service account | For sweep logging |

---

## Setup guide

### 1. Clone the repo on your VM

```bash
git clone https://github.com/General-Tao-Ventures/stake_move_automation.git /opt/stake-move-automation
cd /opt/stake-move-automation
sudo pip3 install -r requirements.txt
```

### 2. Create your `.env` file

```bash
sudo cp env.example .env
```

Fill in all values — there are four sections:

| Section | Required for |
|---|---|
| Wallet & Network | Daily automation (always required) |
| Telegram | Notifications (optional) |
| Google Sheets | Sweep logging (optional) |
| Sheet Setup | One-time `setup_sheets.py` run only |

See [`.env` Reference](#env-reference) below for every variable.

### 3. (If using Google Sheets) Copy service-account credentials

The JSON file is **never committed to git**. Copy it to the VM manually:

```bash
# Run this on your local machine
scp ~/path/to/google-sheets-sa.json user@your-vm:/opt/stake-move-automation/google-sheets-sa.json

# Then on the VM
sudo chmod 600 /opt/stake-move-automation/google-sheets-sa.json
```

Make sure `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` points to this path, and that the service account has **Editor** access to your sheet.

### 4. (If using Google Sheets) Set up the sheet structure

Run this **once**, locally or on the VM, with the Sheet Setup section of `.env` filled in:

```bash
python3 setup_sheets.py
```

This creates four tabs: `Dashboard`, `Daily Sweeps`, `Distributions`, `Config`.  
After it runs you no longer need the Sheet Setup variables for daily operation.

### 5. Install the systemd service

```bash
# Either use the automated deploy script:
sudo bash deploy.sh

# Or manually:
sudo cp stake-move.service /etc/systemd/system/
sudo cp stake-move.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stake-move.timer
```

The timer fires daily at **8:00 AM** in the VM's local timezone.

---

## Verify everything is running

```bash
# Is the timer active?
sudo systemctl status stake-move.timer

# What did the last run do?
sudo systemctl status stake-move.service

# Full log for today
sudo cat /var/log/stake-move/$(date +%Y-%m-%d).log

# One-line summary of every past run
sudo cat /var/log/stake-move/summary.log

# Live journal output (useful during a run)
sudo journalctl -u stake-move.service -f
```

Expected timer status:
```
● stake-move.timer - Daily Stake Move Timer
     Active: active (waiting)
    Trigger: 2026-04-03 15:00:00 UTC; Xh left
```

---

## Manually trigger a run

```bash
sudo systemctl start stake-move.service
sudo journalctl -u stake-move.service -f
```

---

## Redeploy after code changes

```bash
# On the VM
cd /opt/stake-move-automation
sudo git pull origin main
sudo pip3 install -r requirements.txt -q
sudo systemctl restart stake-move.timer
```

Or as a one-liner from your local machine:

```bash
ssh user@your-vm "cd /opt/stake-move-automation && sudo git pull origin main && sudo pip3 install -r requirements.txt -q && sudo systemctl restart stake-move.timer && echo Done"
```

---

## `.env` Reference

### Section 1 — Wallet & Network _(required)_

| Variable | Description |
|---|---|
| `WALLET_NAME` | Name of the Bittensor cold-key wallet on the VM |
| `WALLET_PASSWORD` | Password for that cold-key |
| `ORIGIN_NETUID` | Subnet UID of the hotkey to sweep **from** |
| `DEST_NETUID` | Subnet UID of the hotkey to sweep **into** (can equal `ORIGIN_NETUID`) |
| `ORIGIN_HOTKEY` | SS58 address of the source hotkey |
| `DEST_HOTKEY` | SS58 address of the destination hotkey |
| `MINIMUM_STAKE_THRESHOLD` | Skip the sweep if stake below this (α). Default: `0.001` |

### Section 2 — Telegram _(optional)_

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID to send messages to |

### Section 3 — Google Sheets _(optional)_

| Variable | Description |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Absolute path to service-account `.json` on the VM |
| `GOOGLE_SHEET_ID` | The ID part of your sheet URL (`/d/<ID>/`) |

### Section 4 — Sheet Setup _(only for `setup_sheets.py`)_

| Variable | Default | Description |
|---|---|---|
| `OPENING_BALANCE` | — | Starting TAO balance (numeric) |
| `OPENING_DATE` | — | Date tracking began (`YYYY-MM-DD`) |
| `PARTNER_COUNT` | `2` | Number of partners sharing distributions |
| `PARTNER_N_NAME` | `PartnerN` | Display name for partner N (e.g. `PARTNER_1_NAME=GTV`) |
| `PARTNER_N_SHARE` | equal split | Decimal share for partner N, e.g. `0.5` = 50% |
| `PARTNER_N_WALLET` | — | SS58 wallet address for partner N |
| `FIRST_DIST_DATE` | — | First distribution date (`YYYY-MM-DD`) |
| `CYCLE_DAYS` | `14` | Days between distributions |
| `ARCHIVE_TAB_NAMES` | _(empty)_ | Comma-separated legacy tab names to rename as `[Archive] …` |

**Example for three partners:**
```bash
PARTNER_COUNT=3
PARTNER_1_NAME=Alice
PARTNER_1_SHARE=0.5
PARTNER_1_WALLET=5ABC...
PARTNER_2_NAME=Bob
PARTNER_2_SHARE=0.3
PARTNER_2_WALLET=5DEF...
PARTNER_3_NAME=Carol
PARTNER_3_SHARE=0.2
PARTNER_3_WALLET=5GHI...
```
Shares should sum to `1.0`. The Distributions sheet will automatically gain one amount column and one Tx Link column per partner.

---

## Key files

```
daily_stake_move.py        Main automation script (run by systemd)
setup_sheets.py            One-time Google Sheet initialisation
utils/sheets_logger.py     Google Sheets read/write helpers
utils/telegram_notifier.py Telegram notification helpers
stake-move.service         Systemd service unit
stake-move.timer           Systemd timer unit
deploy.sh                  Interactive deployment helper
diagnose.sh                Troubleshooting helper
env.example                .env template (copy → .env, fill in)
requirements.txt           Python dependencies
```

---

## Troubleshooting

Run the diagnostic script for a structured overview:

```bash
sudo bash diagnose.sh
```

Common issues:

| Symptom | Check |
|---|---|
| `'ORIGIN_HOTKEY' is required but not set` | `.env` is missing that variable |
| `btcli` not found | Add `~/.local/bin` to PATH in `stake-move.service` |
| Sweep skipped every day | `MINIMUM_STAKE_THRESHOLD` may be too high |
| Sheet not updating | Service account lacks Editor access to the sheet |
| Timer not firing | `sudo systemctl enable --now stake-move.timer` |
