# SN35 Stake Move Automation

Runs daily at **8AM PST** on a GCP VM. Sweeps stake from the SN35 owner hotkey into the RT21 destination, logs every sweep to Google Sheets, sends Telegram notifications, and alerts on bi-weekly distribution Fridays.

---

## Stack

| What | Detail |
|---|---|
| VM | `sn35-stake-automation` · `us-central1-a` · project `bittensor1` |
| Script | `/opt/stake-move-automation/daily_stake_move.py` |
| Schedule | systemd timer — `8:00 AM America/Los_Angeles` daily |
| Logs | `/var/log/stake-move/YYYY-MM-DD.log` |
| Sheet | [SN35 Distribution (GTV & PTN)](https://docs.google.com/spreadsheets/d/1_FvpOzJQRSR6x-5Q0fT7187-1yHlC37Ornb-j5hYqh0) |

---

## First-Time Deploy

```bash
# 1. SSH into the VM
gcloud compute ssh sn35-stake-automation --project=bittensor1 --zone=us-central1-a

# 2. Clone the repo
sudo git clone git@github.com:General-Tao-Ventures/sn35_emission_move.git /opt/stake-move-automation

# 3. Install dependencies
cd /opt/stake-move-automation
sudo pip3 install -r requirements.txt

# 4. Create .env (copy example and fill in values)
sudo cp env.example .env
sudo nano .env

# 5. Copy service account JSON (never committed to git)
#    Upload your google-sheets-sa.json to the VM:
#    (run this locally)
gcloud compute scp ~/path/to/google-sheets-sa.json \
  sn35-stake-automation:/opt/stake-move-automation/google-sheets-sa.json \
  --project=bittensor1 --zone=us-central1-a
sudo chmod 600 /opt/stake-move-automation/google-sheets-sa.json

# 6. Install systemd service and timer
sudo cp stake-move.service /etc/systemd/system/
sudo cp stake-move.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stake-move.timer

# 7. (One-time) Set up the Google Sheet structure
#    Run this locally from the repo root:
python3 setup_sheets.py
```

---

## Redeploy After Changes

Run this **locally** from the repo root after pushing to `main`:

```bash
# Pull latest and copy changed files to VM
gcloud compute ssh sn35-stake-automation --project=bittensor1 --zone=us-central1-a --command="
  cd /opt/stake-move-automation && sudo git pull origin main && sudo pip3 install -r requirements.txt -q
"

# Restart the timer to pick up changes
gcloud compute ssh sn35-stake-automation --project=bittensor1 --zone=us-central1-a --command="
  sudo systemctl restart stake-move.timer
"
```

Or as a one-liner:

```bash
gcloud compute ssh sn35-stake-automation --project=bittensor1 --zone=us-central1-a \
  --command="cd /opt/stake-move-automation && sudo git pull origin main && sudo pip3 install -r requirements.txt -q && sudo systemctl restart stake-move.timer && echo 'Done'"
```

---

## Check Everything is Running

```bash
# SSH in first
gcloud compute ssh sn35-stake-automation --project=bittensor1 --zone=us-central1-a
```

```bash
# 1. Is the timer active and when does it next fire?
sudo systemctl status stake-move.timer

# 2. What did the last run do?
sudo systemctl status stake-move.service

# 3. Full log for today
sudo cat /var/log/stake-move/$(date +%Y-%m-%d).log

# 4. Summary of all past runs (one line per run)
sudo cat /var/log/stake-move/summary.log

# 5. Journal logs (real-time, last 50 lines)
sudo journalctl -u stake-move.service -n 50

# 6. Watch live as the job runs (useful at 8AM)
sudo journalctl -u stake-move.service -f
```

**Expected output from `systemctl status stake-move.timer`:**
```
● stake-move.timer - Daily Stake Move Timer (8AM PST)
     Active: active (waiting)
    Trigger: 2026-04-02 15:00:00 UTC; Xh left
   Triggers: ● stake-move.service
```

> If `Active` shows anything other than `active (waiting)` — re-run:
> `sudo systemctl enable --now stake-move.timer`

---

## Manually Trigger a Run

```bash
sudo systemctl start stake-move.service
# Then watch the output:
sudo journalctl -u stake-move.service -f
```

---

## .env Reference

```bash
# /opt/stake-move-automation/.env

WALLET_PASSWORD=your_coldkey_password

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

GOOGLE_SERVICE_ACCOUNT_JSON=/opt/stake-move-automation/google-sheets-sa.json
GOOGLE_SHEET_ID=1_FvpOzJQRSR6x-5Q0fT7187-1yHlC37Ornb-j5hYqh0
```

> `google-sheets-sa.json` is **not in git** — copy it manually to the VM (see First-Time Deploy step 5).

---

## Key Files

```
daily_stake_move.py      # Main script
utils/sheets_logger.py   # Google Sheets read/write
utils/telegram_notifier.py # Telegram notifications
setup_sheets.py          # One-time Sheet initialisation (run locally)
add_charts.py            # Add/refresh Dashboard charts (run locally when ready)
stake-move.service       # Systemd service unit
stake-move.timer         # Systemd timer unit (8AM PST daily)
env.example              # .env template
```
