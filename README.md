# GCP VM Stake Move Automation

Automated daily stake move operation that runs at 8AM PST on a GCP Compute Engine VM. This system moves stake from a specific hotkey to RT21 using wallet `sn35`, with password stored securely in GCP Secret Manager and comprehensive logging.

## Overview

This automation system:
- Runs daily at **8AM PST** (16:00 UTC)
- Moves stake from hotkey `5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx` to `5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35` (RT21)
- Uses wallet `sn35` for authentication
- Stores wallet password securely in GCP Secret Manager
- Logs all operations with detailed stake amounts and timestamps
- Sends Telegram notifications for operation status and daily logs
- Handles errors gracefully with proper notifications

## Architecture

- **Platform**: GCP Compute Engine VM (persistent storage for wallet files)
- **Scheduling**: Systemd timer (reliable, persistent across reboots)
- **Secrets**: GCP Secret Manager for wallet password and Telegram credentials
- **Logging**: Structured logs with timestamps, stake amounts, and operation results
- **Notifications**: Telegram bot integration for real-time alerts and daily log delivery

## Prerequisites

1. **GCP Account** with billing enabled
2. **GCP VM Instance** (e2-micro or larger) running Linux (Ubuntu/Debian recommended)
3. **btcli** installed and configured on the VM
4. **Wallet files** for `sn35` wallet available
5. **gcloud CLI** installed on the VM
6. **Root/sudo access** on the VM

## Setup Instructions

### Step 1: Create GCP VM Instance

1. Go to [GCP Console](https://console.cloud.google.com/compute/instances)
2. Create a new VM instance:
   - **Machine type**: e2-micro (sufficient for this task)
   - **OS**: Ubuntu 22.04 LTS or Debian 11+
   - **Boot disk**: 20GB standard persistent disk
   - **Firewall**: Allow HTTP/HTTPS traffic (if needed)
   - **Service account**: Create or use existing service account with Secret Manager access

3. **Important**: Ensure the VM's service account has the `Secret Manager Secret Accessor` role:
   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

### Step 2: Install btcli on VM

SSH into your VM and install bittensor CLI:

```bash
# Install Python and pip if not already installed
sudo apt-get update
sudo apt-get install -y python3 python3-pip

# Install bittensor CLI
pip3 install bittensor

# Verify installation
btcli --version
```

### Step 3: Transfer Wallet Files

Copy your wallet files to the VM. The wallet files should be located at `~/.bittensor/wallets/`:

```bash
# From your local machine, copy wallet directory
scp -r ~/.bittensor/wallets/sn35 USER@VM_IP:~/.bittensor/wallets/

# Or use gcloud compute scp
gcloud compute scp --recurse ~/.bittensor/wallets/sn35 VM_NAME:~/.bittensor/wallets/ --zone=ZONE
```

**Security Note**: Ensure wallet files have proper permissions:
```bash
chmod 600 ~/.bittensor/wallets/sn35/coldkey
chmod 600 ~/.bittensor/wallets/sn35/hotkeys/*
```

### Step 4: Install gcloud CLI on VM

If not already installed:

```bash
# Add gcloud repository
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list

# Import Google Cloud public key
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -

# Install gcloud
sudo apt-get update && sudo apt-get install -y google-cloud-sdk

# Authenticate (if needed)
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### Step 5: Set Up Telegram Bot (Optional but Recommended)

To receive notifications and daily logs via Telegram:

1. **Create a Telegram Bot**:
   - Open Telegram and search for [@BotFather](https://t.me/botfather)
   - Send `/newbot` and follow instructions to create a bot
   - Save the bot token (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Get Your Chat ID**:
   
   **Option A: Private Chat (Personal)**
   - Search for your bot in Telegram and start a conversation
   - Send any message to your bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find your chat ID in the response (look for `"chat":{"id":123456789}`)
   
   **Option B: Group Chat (Recommended for notifications)**
   - Create a Telegram group or use an existing one
   - Add your bot to the group as a member (or admin)
   - Send a message in the group (e.g., "Hello")
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find the group chat ID in the response (look for `"chat":{"id":-1001234567890}`)
   - **Note**: Group chat IDs are negative numbers (e.g., `-1003492410161`)
   - Make sure the bot has permission to send messages in the group

3. **Store credentials** (will be prompted during deployment, or manually):
   ```bash
   # Store bot token
   echo -n "YOUR_BOT_TOKEN" | gcloud secrets create stake-move-telegram-bot-token \
     --data-file=- --project=YOUR_PROJECT_ID
   
   # Store chat ID
   echo -n "YOUR_CHAT_ID" | gcloud secrets create stake-move-telegram-chat-id \
     --data-file=- --project=YOUR_PROJECT_ID
   ```

**Note**: The deployment script will prompt you to configure Telegram during setup. You can skip this step and configure it later if needed.

### Step 6: Deploy Automation Scripts

1. **Transfer files to VM**:
   ```bash
   # Create directory on VM
   mkdir -p ~/stake-move-automation
   
   # Copy files from local machine
   scp daily_stake_move.sh stake-move.service stake-move.timer deploy.sh USER@VM_IP:~/stake-move-automation/
   ```

2. **Run deployment script**:
   ```bash
   cd ~/stake-move-automation
   chmod +x deploy.sh
   sudo ./deploy.sh
   ```

   The deployment script will:
   - Install required dependencies (expect, tcl)
   - Create GCP Secret Manager secret (prompts for password)
   - Configure systemd service and timer
   - Enable and start the timer
   - Show next scheduled run time

### Step 7: Verify Installation

Check that everything is set up correctly:

```bash
# Check timer status
sudo systemctl status stake-move.timer

# Check next run time
sudo systemctl list-timers stake-move.timer

# View service logs (if test run was executed)
sudo journalctl -u stake-move.service -n 50
```

## Configuration

### Modify Schedule Time

To change the execution time, edit `/etc/systemd/system/stake-move.timer`:

```ini
[Timer]
# Change to desired time (format: HH:MM:00 TIMEZONE)
OnCalendar=*-*-* 08:00:00 America/Los_Angeles
```

Then reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart stake-move.timer
```

### Update Wallet Password

To update the password in Secret Manager:

```bash
# Update secret
echo -n "NEW_PASSWORD" | gcloud secrets versions add stake-move-wallet-sn35-password --data-file=-
```

### Configure or Update Telegram Settings

To configure Telegram after initial deployment:

```bash
# Update bot token
echo -n "YOUR_BOT_TOKEN" | gcloud secrets versions add stake-move-telegram-bot-token \
  --data-file=- --project=YOUR_PROJECT_ID

# Update chat ID
echo -n "YOUR_CHAT_ID" | gcloud secrets versions add stake-move-telegram-chat-id \
  --data-file=- --project=YOUR_PROJECT_ID
```

To disable Telegram notifications, delete the secrets (the script will gracefully skip notifications if secrets don't exist):

```bash
gcloud secrets delete stake-move-telegram-bot-token --project=YOUR_PROJECT_ID
gcloud secrets delete stake-move-telegram-chat-id --project=YOUR_PROJECT_ID
```

### Change Hotkey Addresses

Edit `/opt/stake-move-automation/daily_stake_move.sh`:

```bash
ORIGIN_HOTKEY="YOUR_ORIGIN_HOTKEY"
DEST_HOTKEY="YOUR_DEST_HOTKEY"
```

Then restart the service:
```bash
sudo systemctl daemon-reload
```

## Monitoring

### View Daily Logs

```bash
# Today's log
tail -f /var/log/stake-move/$(date +%Y-%m-%d).log

# Specific date
cat /var/log/stake-move/2024-01-15.log

# Summary log (all operations)
tail -f /var/log/stake-move/summary.log
```

### View Systemd Logs

```bash
# Service logs
sudo journalctl -u stake-move.service -f

# Timer logs
sudo journalctl -u stake-move.timer -f

# Last 100 lines
sudo journalctl -u stake-move.service -n 100
```

### Check Timer Status

```bash
# Current status
sudo systemctl status stake-move.timer

# List all timers
sudo systemctl list-timers

# Next run time
sudo systemctl list-timers stake-move.timer --no-pager
```

### Telegram Notifications

If Telegram is configured, you will receive:

1. **Start Notification**: Sent when the operation begins
   - Shows operation start time
   - Origin and destination hotkey addresses
   - Wallet name

2. **Success/Failure Notification**: Sent when operation completes
   - Success: Shows stake amounts moved
   - Failure: Shows error message

3. **Daily Log File**: Complete log file sent as a document attachment

4. **Daily Summary**: Summary of the operation with key details

**Test Telegram notifications**:
```bash
# Send a test notification
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/sendMessage" \
  -d "chat_id=<YOUR_CHAT_ID>" \
  -d "text=Test notification"
```

### Manual Execution

To run the operation manually (for testing):

```bash
# Start service manually
sudo systemctl start stake-move.service

# View output
sudo journalctl -u stake-move.service -f
```

## Log Format

### Daily Log File (`/var/log/stake-move/YYYY-MM-DD.log`)

```
[2024-01-15 08:00:01 PST] ==========================================
[2024-01-15 08:00:01 PST] Starting daily stake move operation
[2024-01-15 08:00:01 PST] ==========================================
[2024-01-15 08:00:01 PST] Origin Netuid: 35
[2024-01-15 08:00:01 PST] Destination Netuid: 35
[2024-01-15 08:00:01 PST] Origin Hotkey: 5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx
[2024-01-15 08:00:01 PST] Destination Hotkey: 5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35
[2024-01-15 08:00:01 PST] Wallet: sn35
[2024-01-15 08:00:02 PST] Fetching password from GCP Secret Manager...
[2024-01-15 08:00:03 PST] Password retrieved successfully
[2024-01-15 08:00:04 PST] Executing stake move operation...
[2024-01-15 08:00:10 PST] Operation completed successfully
[2024-01-15 08:00:10 PST] Origin stake info: 1,234.5886
[2024-01-15 08:00:10 PST] Destination stake info: 20,162.4246
[2024-01-15 08:00:11 PST] ==========================================
[2024-01-15 08:00:11 PST] Daily stake move operation completed
[2024-01-15 08:00:11 PST] ==========================================
```

### Summary Log (`/var/log/stake-move/summary.log`)

```
[2024-01-15 08:00:11 PST] SUCCESS: Stake moved from 5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx to 5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35
[2024-01-15 08:00:11 PST]   Origin stake: 1,234.5886
[2024-01-15 08:00:11 PST]   Destination stake: 20,162.4246
```

## Troubleshooting

### Timer Not Running

1. **Check timer status**:
   ```bash
   sudo systemctl status stake-move.timer
   ```

2. **Check if timer is enabled**:
   ```bash
   sudo systemctl is-enabled stake-move.timer
   ```

3. **Enable and start timer**:
   ```bash
   sudo systemctl enable stake-move.timer
   sudo systemctl start stake-move.timer
   ```

### Service Fails to Start

1. **Check service logs**:
   ```bash
   sudo journalctl -u stake-move.service -n 100
   ```

2. **Check script permissions**:
   ```bash
   ls -l /opt/stake-move-automation/daily_stake_move.sh
   sudo chmod +x /opt/stake-move-automation/daily_stake_move.sh
   ```

3. **Verify script path in service file**:
   ```bash
   cat /etc/systemd/system/stake-move.service
   ```

### Password Retrieval Fails

1. **Check Secret Manager access**:
   ```bash
   gcloud secrets versions access latest --secret=stake-move-wallet-sn35-password
   ```

2. **Verify service account permissions**:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID \
     --flatten="bindings[].members" \
     --filter="bindings.members:serviceAccount:*"
   ```

3. **Grant Secret Manager access**:
   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

### btcli Command Fails

1. **Verify btcli is installed**:
   ```bash
   which btcli
   btcli --version
   ```

2. **Check wallet files exist**:
   ```bash
   ls -la ~/.bittensor/wallets/sn35/
   ```

3. **Test btcli manually**:
   ```bash
   btcli stake show --netuid 35 --wallet.name sn35
   ```

### Logs Not Being Created

1. **Check log directory permissions**:
   ```bash
   ls -ld /var/log/stake-move
   sudo mkdir -p /var/log/stake-move
   sudo chmod 755 /var/log/stake-move
   ```

2. **Check script can write to log directory**:
   ```bash
   sudo -u root touch /var/log/stake-move/test.log
   ```

### Timezone Issues

1. **Check system timezone**:
   ```bash
   timedatectl
   ```

2. **Set timezone if needed**:
   ```bash
   sudo timedatectl set-timezone America/Los_Angeles
   ```

3. **Verify timer timezone**:
   ```bash
   cat /etc/systemd/system/stake-move.timer | grep OnCalendar
   ```

### Telegram Notifications Not Working

1. **Verify secrets exist**:
   ```bash
   gcloud secrets describe stake-move-telegram-bot-token --project=YOUR_PROJECT_ID
   gcloud secrets describe stake-move-telegram-chat-id --project=YOUR_PROJECT_ID
   ```

2. **Test bot token and chat ID**:
   ```bash
   BOT_TOKEN=$(gcloud secrets versions access latest --secret=stake-move-telegram-bot-token --project=YOUR_PROJECT_ID)
   CHAT_ID=$(gcloud secrets versions access latest --secret=stake-move-telegram-chat-id --project=YOUR_PROJECT_ID)
   curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
     -d "chat_id=${CHAT_ID}" \
     -d "text=Test message"
   ```

3. **Check service account permissions**:
   ```bash
   # Ensure service account can access Secret Manager
   gcloud projects get-iam-policy YOUR_PROJECT_ID \
     --flatten="bindings[].members" \
     --filter="bindings.members:serviceAccount:*"
   ```

4. **Check logs for Telegram errors**:
   ```bash
   sudo journalctl -u stake-move.service | grep -i telegram
   ```

5. **Verify curl is installed**:
   ```bash
   which curl
   # If not installed: sudo apt-get install curl
   ```

## Cost Estimation

- **GCP VM (e2-micro)**: ~$5-10/month (always-on) or ~$1-2/month (preemptible)
- **GCP Secret Manager**: Free tier covers this use case (first 6 versions per secret)
- **Storage**: Minimal (logs are small, ~1MB per month)
- **Network**: Minimal (only Secret Manager API calls)

**Total estimated cost**: ~$5-10/month for always-on VM

## Security Considerations

1. **Wallet Files**: Ensure wallet files have restrictive permissions (600)
2. **Secret Manager**: Use least-privilege IAM roles
3. **VM Access**: Restrict SSH access using firewall rules
4. **Logs**: Consider rotating logs to prevent disk fill-up
5. **Service Account**: Use dedicated service account with minimal permissions

## Maintenance

### Log Rotation

Create `/etc/logrotate.d/stake-move`:

```
/var/log/stake-move/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
```

### Update Scripts

To update scripts:

```bash
# Copy new files to VM
scp daily_stake_move.sh USER@VM_IP:~/stake-move-automation/

# Update installation
sudo cp ~/stake-move-automation/daily_stake_move.sh /opt/stake-move-automation/
sudo systemctl daemon-reload
```

## Support

For issues or questions:
1. Check logs: `/var/log/stake-move/` and `journalctl -u stake-move.service`
2. Verify all prerequisites are met
3. Test manual execution: `sudo systemctl start stake-move.service`

## Files Reference

- `daily_stake_move.sh` - Main automation script
- `stake-move.service` - Systemd service definition
- `stake-move.timer` - Systemd timer definition (scheduling)
- `deploy.sh` - Deployment and setup script
- `README.md` - This file

## License

This automation script is provided as-is for internal use.

