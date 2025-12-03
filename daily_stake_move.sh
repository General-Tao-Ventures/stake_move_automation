#!/usr/bin/env bash
# daily_stake_move.sh
# ------------------------------------------------------------
# Daily automated stake move operation
# Moves stake from a specific hotkey to RT21 using wallet sn35
# Runs at 8AM PST daily via systemd timer
# ------------------------------------------------------------
set -euo pipefail

############################ CONSTANTS ############################
ORIGIN_NETUID=35
DEST_NETUID=35
ORIGIN_HOTKEY="5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx"
DEST_HOTKEY="5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35"
WALLET_NAME="sn35"
SECRET_NAME="stake-move-wallet-sn35-password"
TELEGRAM_BOT_TOKEN_SECRET="stake-move-telegram-bot-token"
TELEGRAM_CHAT_ID_SECRET="stake-move-telegram-chat-id"
LOG_DIR="/var/log/stake-move"
TELEGRAM_API_URL="https://api.telegram.org/bot"
###################################################################

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Generate log file with date
LOG_FILE="${LOG_DIR}/$(date +%Y-%m-%d).log"
SUMMARY_LOG="${LOG_DIR}/summary.log"

# Function to log with timestamp
log() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S %Z')
    echo "[$timestamp] $message" | tee -a "$LOG_FILE"
}

# Function to log summary
log_summary() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S %Z')
    echo "[$timestamp] $message" | tee -a "$SUMMARY_LOG"
}

# Function to send Telegram notification
send_telegram() {
    local message="$1"
    local parse_mode="${2:-HTML}"
    
    # Fetch Telegram credentials from Secret Manager
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
        TELEGRAM_BOT_TOKEN=$(gcloud secrets versions access latest --secret="$TELEGRAM_BOT_TOKEN_SECRET" 2>/dev/null || echo "")
    fi
    
    if [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        TELEGRAM_CHAT_ID=$(gcloud secrets versions access latest --secret="$TELEGRAM_CHAT_ID_SECRET" 2>/dev/null || echo "")
    fi
    
    # Skip if credentials not available
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        log "Warning: Telegram credentials not configured, skipping notification"
        return 0
    fi
    
    # Send message via Telegram API
    # Use --data-urlencode for proper URL encoding of special characters
    local response=$(curl -s -X POST \
        "${TELEGRAM_API_URL}${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${message}" \
        --data-urlencode "parse_mode=${parse_mode}" \
        -d "disable_web_page_preview=true" 2>&1)
    
    if echo "$response" | grep -q '"ok":true'; then
        log "Telegram notification sent successfully"
    else
        log "Warning: Failed to send Telegram notification: $response"
    fi
}

# Function to send log file to Telegram
send_log_to_telegram() {
    local log_file="$1"
    
    if [ ! -f "$log_file" ]; then
        return 1
    fi
    
    # Fetch Telegram credentials
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
        TELEGRAM_BOT_TOKEN=$(gcloud secrets versions access latest --secret="$TELEGRAM_BOT_TOKEN_SECRET" 2>/dev/null || echo "")
    fi
    
    if [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        TELEGRAM_CHAT_ID=$(gcloud secrets versions access latest --secret="$TELEGRAM_CHAT_ID_SECRET" 2>/dev/null || echo "")
    fi
    
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        return 0
    fi
    
    # Send log file as document
    local response=$(curl -s -X POST \
        "${TELEGRAM_API_URL}${TELEGRAM_BOT_TOKEN}/sendDocument" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "document=@${log_file}" \
        -F "caption=Daily Stake Move Log - $(date +%Y-%m-%d)" 2>&1)
    
    if echo "$response" | grep -q '"ok":true'; then
        log "Log file sent to Telegram successfully"
    else
        log "Warning: Failed to send log file to Telegram: $response"
    fi
}

# Error handler
error_exit() {
    local error_msg="$1"
    log "ERROR: $error_msg"
    log_summary "FAILED: $error_msg"
    
    # Send error notification to Telegram
    local telegram_msg="❌ <b>Stake Move Failed</b>

Date: $(date '+%Y-%m-%d %H:%M:%S %Z')
Origin Hotkey: <code>$ORIGIN_HOTKEY</code>
Destination Hotkey: <code>$DEST_HOTKEY</code>
Error: $error_msg

Please check the logs for more details."
    send_telegram "$telegram_msg"
    
    exit 1
}

# Start logging
log "=========================================="
log "Starting daily stake move operation"
log "=========================================="
log "Origin Netuid: $ORIGIN_NETUID"
log "Destination Netuid: $DEST_NETUID"
log "Origin Hotkey: $ORIGIN_HOTKEY"
log "Destination Hotkey: $DEST_HOTKEY"
log "Wallet: $WALLET_NAME"

# Send start notification to Telegram
START_MSG="🚀 <b>Daily Stake Move Started</b>

Date: $(date '+%Y-%m-%d %H:%M:%S %Z')
Origin Hotkey: <code>$ORIGIN_HOTKEY</code>
Destination Hotkey: <code>$DEST_HOTKEY</code>
Wallet: $WALLET_NAME"
send_telegram "$START_MSG"

# Fetch password from GCP Secret Manager
log "Fetching password from GCP Secret Manager..."
PASSWORD=$(gcloud secrets versions access latest --secret="$SECRET_NAME" 2>&1)
if [ $? -ne 0 ]; then
    error_exit "Failed to fetch password from Secret Manager: $PASSWORD"
fi
log "Password retrieved successfully"

# Note: Initial stake amounts will be shown in the stake move output
log "Starting stake move operation..."

# Perform the stake move
log "Executing stake move operation..."
log "Command: btcli stake move --origin-netuid $ORIGIN_NETUID --dest-netuid $DEST_NETUID --hotkey-ss58 $ORIGIN_HOTKEY --dest-ss58 $DEST_HOTKEY --all --yes --wallet.name $WALLET_NAME"

# Capture full output for parsing
OUTPUT_FILE=$(mktemp)
EXPECT_SCRIPT=$(mktemp)

cat > "$EXPECT_SCRIPT" <<EOF
set timeout 300
set log_file [open "$OUTPUT_FILE" w]

spawn btcli stake move \
    --origin-netuid $ORIGIN_NETUID \
    --dest-netuid $DEST_NETUID \
    --hotkey-ss58 $ORIGIN_HOTKEY \
    --dest-ss58 $DEST_HOTKEY \
    --all --yes \
    --wallet.name $WALLET_NAME

expect {
    "Enter your password:" {
        send -- "$PASSWORD\r"
        exp_continue
    }
    "Decrypting..." {
        exp_continue
    }
    "✅ Sent" {
        puts \$log_file "SUCCESS: Stake move completed"
        exp_continue
    }
    -re "Origin stake:.*?(\[0-9,\]+\.\[0-9\]+)" {
        set origin_stake [regsub -all {,} \$expect_out(1,string) {}]
        puts \$log_file "ORIGIN_STAKE: \$origin_stake"
        exp_continue
    }
    -re "Destination stake:.*?(\[0-9,\]+\.\[0-9\]+)" {
        set dest_stake [regsub -all {,} \$expect_out(1,string) {}]
        puts \$log_file "DEST_STAKE: \$dest_stake"
        exp_continue
    }
    eof {
        catch {close \$log_file}
    }
    timeout {
        puts \$log_file "ERROR: Operation timed out"
        exit 1
    }
}

wait
EOF

# Capture full output for parsing fallback
FULL_OUTPUT_FILE=$(mktemp)
if ! expect "$EXPECT_SCRIPT" > "$FULL_OUTPUT_FILE" 2>&1; then
    cat "$FULL_OUTPUT_FILE" >> "$LOG_FILE"
    rm -f "$EXPECT_SCRIPT" "$OUTPUT_FILE" "$FULL_OUTPUT_FILE"
    error_exit "Stake move operation failed. Check logs for details."
fi

# Append full output to log file
cat "$FULL_OUTPUT_FILE" >> "$LOG_FILE"

# Parse output for stake amounts from btcli stake move output
ORIGIN_AMOUNT="N/A"
DEST_AMOUNT="N/A"

# First try to extract from expect captured values (already has commas removed)
if [ -f "$OUTPUT_FILE" ]; then
    ORIGIN_AMOUNT=$(grep "^ORIGIN_STAKE:" "$OUTPUT_FILE" | sed 's/ORIGIN_STAKE: //' | tail -1 | tr -d ' ' || echo "")
    DEST_AMOUNT=$(grep "^DEST_STAKE:" "$OUTPUT_FILE" | sed 's/DEST_STAKE: //' | tail -1 | tr -d ' ' || echo "")
fi

# Fallback: parse from full output if expect capture failed
# Match pattern: "Origin stake: 2,924.0256 ך" -> extract "2,924.0256" -> remove commas -> "2924.0256"
if [ -z "$ORIGIN_AMOUNT" ] || [ "$ORIGIN_AMOUNT" = "N/A" ]; then
    ORIGIN_AMOUNT=$(grep -i "Origin stake:" "$FULL_OUTPUT_FILE" 2>/dev/null | \
        sed -n 's/.*Origin stake:[[:space:]]*\([0-9,]\+\.[0-9]*\).*/\1/p' | \
        head -1 | tr -d ',' || echo "N/A")
fi

if [ -z "$DEST_AMOUNT" ] || [ "$DEST_AMOUNT" = "N/A" ]; then
    DEST_AMOUNT=$(grep -i "Destination stake:" "$FULL_OUTPUT_FILE" 2>/dev/null | \
        sed -n 's/.*Destination stake:[[:space:]]*\([0-9,]\+\.[0-9]*\).*/\1/p' | \
        head -1 | tr -d ',' || echo "N/A")
fi

log "Operation completed successfully"
log "Origin stake amount (moved): $ORIGIN_AMOUNT"
log "Destination stake amount (current total): $DEST_AMOUNT"
    
    log_summary "SUCCESS: Stake moved from $ORIGIN_HOTKEY to $DEST_HOTKEY"
    log_summary "  Stake moved: $ORIGIN_AMOUNT ך"
    log_summary "  Destination total: $DEST_AMOUNT ך"
    
    # Send success notification to Telegram
    SUCCESS_MSG="✅ <b>Stake Move Completed Successfully</b>

Date: $(date '+%Y-%m-%d %H:%M:%S %Z')
Stake Moved: <b>$ORIGIN_AMOUNT ך</b>
Destination Total: <b>$DEST_AMOUNT ך</b>
Origin Hotkey: <code>$ORIGIN_HOTKEY</code>
Destination Hotkey: <code>$DEST_HOTKEY</code>"
    send_telegram "$SUCCESS_MSG"
else
    log "Warning: Could not parse output file"
    log_summary "SUCCESS: Stake move completed (amounts not parsed)"
    
    # Send success notification without amounts
    SUCCESS_MSG="✅ <b>Stake Move Completed</b>

Date: $(date '+%Y-%m-%d %H:%M:%S %Z')
Status: Completed (amounts not parsed)
Origin Hotkey: <code>$ORIGIN_HOTKEY</code>
Destination Hotkey: <code>$DEST_HOTKEY</code>
Please check logs for details."
    send_telegram "$SUCCESS_MSG"
fi

# Cleanup
rm -f "$EXPECT_SCRIPT" "$OUTPUT_FILE" "$FULL_OUTPUT_FILE"

# Verify final state using stake list
log "Verifying final stake state..."
FINAL_OUTPUT=$(btcli stake list --netuid "$DEST_NETUID" --wallet.name "$WALLET_NAME" 2>&1 | grep -A 10 "$DEST_HOTKEY" | grep -i "Stake (α)" | head -1 || echo "Could not retrieve final stake details")
log "Final stake verification: $FINAL_OUTPUT"

log "=========================================="
log "Daily stake move operation completed"
log "=========================================="

# Send daily log file to Telegram
log "Sending daily log to Telegram..."
send_log_to_telegram "$LOG_FILE"

# Send summary to Telegram
SUMMARY_CONTENT=$(tail -20 "$SUMMARY_LOG" | tail -5 || echo "No summary available")
SUMMARY_MSG="📊 <b>Daily Summary</b>

$(date '+%Y-%m-%d %H:%M:%S %Z')

<pre>$SUMMARY_CONTENT</pre>"
send_telegram "$SUMMARY_MSG"

exit 0

