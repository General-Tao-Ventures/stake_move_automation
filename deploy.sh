#!/usr/bin/env bash
# deploy.sh
# ------------------------------------------------------------
# Deployment script for GCP VM stake move automation
# Sets up Secret Manager, installs dependencies, and configures systemd
# ------------------------------------------------------------
set -euo pipefail

############################ CONSTANTS ############################
PROJECT_ID="${GCP_PROJECT_ID:-}"
SECRET_NAME="stake-move-wallet-sn35-password"
TELEGRAM_BOT_TOKEN_SECRET="stake-move-telegram-bot-token"
TELEGRAM_CHAT_ID_SECRET="stake-move-telegram-chat-id"
INSTALL_DIR="/opt/stake-move-automation"
SERVICE_USER="${SERVICE_USER:-root}"
###################################################################

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Check for required commands
check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed. Please install it first."
        return 1
    fi
}

log_info "Checking prerequisites..."
check_command "gcloud" || exit 1
check_command "expect" || log_warn "expect not found, will install"

# Get GCP project ID if not set
if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
    if [ -z "$PROJECT_ID" ]; then
        log_error "GCP_PROJECT_ID not set and unable to get from gcloud config"
        log_info "Please set GCP_PROJECT_ID environment variable or run: gcloud config set project YOUR_PROJECT_ID"
        exit 1
    fi
fi

log_info "Using GCP Project: $PROJECT_ID"

# Install dependencies
log_info "Installing system dependencies..."
if command -v apt-get &> /dev/null; then
    apt-get update
    apt-get install -y expect tcl tcl-dev curl
elif command -v yum &> /dev/null; then
    yum install -y expect tcl curl
elif command -v dnf &> /dev/null; then
    dnf install -y expect tcl curl
else
    log_warn "Unknown package manager. Please install 'expect' and 'curl' manually."
fi

# Check if btcli is installed
if ! command -v btcli &> /dev/null; then
    log_warn "btcli not found in PATH. Please ensure btcli is installed and accessible."
    log_info "You may need to install bittensor CLI separately."
fi

# Create installation directory
log_info "Creating installation directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p /var/log/stake-move

# Determine the user who will run the service (prefer SUDO_USER, fallback to current user)
SERVICE_USER="${SUDO_USER:-$USER}"
if [ "$SERVICE_USER" = "root" ] || [ -z "$SERVICE_USER" ]; then
    # If running as root without sudo, try to find a non-root user
    SERVICE_USER=$(getent passwd | awk -F: '$3 >= 1000 && $1 != "nobody" {print $1; exit}')
    if [ -z "$SERVICE_USER" ]; then
        SERVICE_USER="root"
    fi
fi

# Set proper permissions for log directory (service user needs write access)
log_info "Setting permissions for log directory (user: $SERVICE_USER)..."
if chown -R "$SERVICE_USER:$SERVICE_USER" /var/log/stake-move 2>/dev/null; then
    chmod 755 /var/log/stake-move
    log_info "Log directory ownership set to $SERVICE_USER"
else
    # If chown fails (e.g., directory owned by root), make it writable by the user
    chmod 775 /var/log/stake-move
    # Try to use ACLs if available, otherwise the user will need sudo to write
    if command -v setfacl &>/dev/null; then
        setfacl -m "u:$SERVICE_USER:rwx" /var/log/stake-move 2>/dev/null && \
        setfacl -d -m "u:$SERVICE_USER:rwx" /var/log/stake-move 2>/dev/null && \
        log_info "Log directory ACLs set for $SERVICE_USER"
    else
        log_warn "Could not set ownership. You may need to manually fix permissions:"
        log_warn "  sudo chown -R $SERVICE_USER:$SERVICE_USER /var/log/stake-move"
    fi
fi

# Copy scripts to installation directory
log_info "Copying scripts to $INSTALL_DIR..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/daily_stake_move.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/daily_stake_move.sh"

# Copy systemd files
log_info "Installing systemd service and timer..."
cp "$SCRIPT_DIR/stake-move.service" /etc/systemd/system/
cp "$SCRIPT_DIR/stake-move.timer" /etc/systemd/system/

# SERVICE_USER should already be set from above
if [ -z "${SERVICE_USER:-}" ]; then
    SERVICE_USER="${SUDO_USER:-$USER}"
    if [ "$SERVICE_USER" = "root" ] || [ -z "$SERVICE_USER" ]; then
        SERVICE_USER=$(getent passwd | awk -F: '$3 >= 1000 && $1 != "nobody" {print $1; exit}')
        if [ -z "$SERVICE_USER" ]; then
            SERVICE_USER="root"
            log_warn "Could not determine non-root user, service will run as root"
            log_warn "You may need to set up Application Default Credentials for root:"
            log_warn "  sudo gcloud auth application-default login"
        fi
    fi
fi

log_info "Service will run as user: $SERVICE_USER"

# Update service file with correct paths and user
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|" /etc/systemd/system/stake-move.service
sed -i "s|ExecStart=.*|ExecStart=$INSTALL_DIR/daily_stake_move.sh|" /etc/systemd/system/stake-move.service
sed -i "s|^User=.*|User=$SERVICE_USER|" /etc/systemd/system/stake-move.service

# Update PATH to include common locations for btcli (pip installs to ~/.local/bin)
USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
if [ -n "$USER_HOME" ] && [ "$SERVICE_USER" != "root" ]; then
    # Add user's local bin and common Python paths
    UPDATED_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$USER_HOME/.local/bin:$USER_HOME/.cargo/bin"
    # Check if PATH line exists, if not add it
    if ! grep -q "^Environment=\"PATH=" /etc/systemd/system/stake-move.service; then
        sed -i "/^\[Service\]/a Environment=\"PATH=$UPDATED_PATH\"" /etc/systemd/system/stake-move.service
    else
        sed -i "s|^Environment=\"PATH=.*|Environment=\"PATH=$UPDATED_PATH\"|" /etc/systemd/system/stake-move.service
    fi
    log_info "Updated PATH in service file to include $USER_HOME/.local/bin"
fi

# Set up Application Default Credentials path if not root
if [ "$SERVICE_USER" != "root" ]; then
    USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
    ADC_PATH="$USER_HOME/.config/gcloud/application_default_credentials.json"
    if [ -f "$ADC_PATH" ]; then
        log_info "Found Application Default Credentials for $SERVICE_USER"
        # Set environment variable in service file
        sed -i "/^Environment=/a Environment=\"GOOGLE_APPLICATION_CREDENTIALS=$ADC_PATH\"" /etc/systemd/system/stake-move.service
    else
        log_warn "Application Default Credentials not found for $SERVICE_USER"
        log_warn "The service may fail to access secrets. Run as $SERVICE_USER:"
        log_warn "  gcloud auth application-default login"
    fi
fi

# Function to mask secret value for display
mask_secret() {
    local secret="$1"
    local len=${#secret}
    if [ $len -le 8 ]; then
        echo "****"
    elif [ $len -le 16 ]; then
        echo "${secret:0:4}****${secret: -4}"
    else
        echo "${secret:0:6}****${secret: -6}"
    fi
}

# Set up GCP Secret Manager
log_info "Setting up GCP Secret Manager secret: $SECRET_NAME"

# Check if secret exists (use the actual user's gcloud config, not root's)
# Try to access the secret - if it works, it exists
if sudo -u "$SUDO_USER" gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null 2>&1 || \
   gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null 2>&1; then
    log_info "Secret $SECRET_NAME already exists - skipping creation"
    
    # Try to retrieve and show masked value
    SECRET_VALUE=""
    if [ -n "${SUDO_USER:-}" ]; then
        SECRET_VALUE=$(sudo -u "$SUDO_USER" gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT_ID" 2>/dev/null || echo "")
    else
        SECRET_VALUE=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT_ID" 2>/dev/null || echo "")
    fi
    
    if [ -n "$SECRET_VALUE" ]; then
        MASKED=$(mask_secret "$SECRET_VALUE")
        log_info "  Current value: $MASKED"
    else
        log_warn "  Could not retrieve secret value (may need permissions)"
    fi
    
    log_info "To update the secret later, run:"
    log_info "  echo -n 'NEW_PASSWORD' | gcloud secrets versions add $SECRET_NAME --data-file=- --project=$PROJECT_ID"
else
    log_info "Secret $SECRET_NAME does not exist"
    read -p "Do you want to create it now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -sp "Enter wallet password for $SECRET_NAME: " PASSWORD
        echo
        # Use the actual user's gcloud credentials
        if [ -n "${SUDO_USER:-}" ]; then
            sudo -u "$SUDO_USER" bash -c "echo -n '$PASSWORD' | gcloud secrets create $SECRET_NAME --data-file=- --project=$PROJECT_ID --replication-policy=automatic"
        else
            echo -n "$PASSWORD" | gcloud secrets create "$SECRET_NAME" \
                --data-file=- \
                --project="$PROJECT_ID" \
                --replication-policy="automatic"
        fi
        log_info "Secret created successfully"
    else
        log_warn "Skipping secret creation. Make sure the secret exists before running the automation."
    fi
fi

# Set up Telegram secrets (optional)
log_info ""
log_info "Setting up Telegram notifications (optional)..."

# Check if Telegram secrets already exist
TELEGRAM_BOT_EXISTS=false
TELEGRAM_CHAT_EXISTS=false

if (sudo -u "${SUDO_USER:-root}" gcloud secrets describe "$TELEGRAM_BOT_TOKEN_SECRET" --project="$PROJECT_ID" &>/dev/null 2>&1) || \
   (gcloud secrets describe "$TELEGRAM_BOT_TOKEN_SECRET" --project="$PROJECT_ID" &>/dev/null 2>&1); then
    TELEGRAM_BOT_EXISTS=true
fi

if (sudo -u "${SUDO_USER:-root}" gcloud secrets describe "$TELEGRAM_CHAT_ID_SECRET" --project="$PROJECT_ID" &>/dev/null 2>&1) || \
   (gcloud secrets describe "$TELEGRAM_CHAT_ID_SECRET" --project="$PROJECT_ID" &>/dev/null 2>&1); then
    TELEGRAM_CHAT_EXISTS=true
fi

if [ "$TELEGRAM_BOT_EXISTS" = true ] && [ "$TELEGRAM_CHAT_EXISTS" = true ]; then
    log_info "Telegram secrets already exist - skipping configuration"
    log_info "Bot token secret: $TELEGRAM_BOT_TOKEN_SECRET ✓"
    
    # Show masked bot token
    BOT_TOKEN_VALUE=""
    if [ -n "${SUDO_USER:-}" ]; then
        BOT_TOKEN_VALUE=$(sudo -u "$SUDO_USER" gcloud secrets versions access latest --secret="$TELEGRAM_BOT_TOKEN_SECRET" --project="$PROJECT_ID" 2>/dev/null || echo "")
    else
        BOT_TOKEN_VALUE=$(gcloud secrets versions access latest --secret="$TELEGRAM_BOT_TOKEN_SECRET" --project="$PROJECT_ID" 2>/dev/null || echo "")
    fi
    if [ -n "$BOT_TOKEN_VALUE" ]; then
        MASKED_BOT=$(mask_secret "$BOT_TOKEN_VALUE")
        log_info "  Bot token: $MASKED_BOT"
    fi
    
    log_info "Chat ID secret: $TELEGRAM_CHAT_ID_SECRET ✓"
    
    # Show chat ID (can show full value as it's not sensitive)
    CHAT_ID_VALUE=""
    if [ -n "${SUDO_USER:-}" ]; then
        CHAT_ID_VALUE=$(sudo -u "$SUDO_USER" gcloud secrets versions access latest --secret="$TELEGRAM_CHAT_ID_SECRET" --project="$PROJECT_ID" 2>/dev/null || echo "")
    else
        CHAT_ID_VALUE=$(gcloud secrets versions access latest --secret="$TELEGRAM_CHAT_ID_SECRET" --project="$PROJECT_ID" 2>/dev/null || echo "")
    fi
    if [ -n "$CHAT_ID_VALUE" ]; then
        log_info "  Chat ID: $CHAT_ID_VALUE"
    fi
    
    log_info "To update Telegram credentials later, run:"
    log_info "  echo -n 'BOT_TOKEN' | gcloud secrets versions add $TELEGRAM_BOT_TOKEN_SECRET --data-file=- --project=$PROJECT_ID"
    log_info "  echo -n 'CHAT_ID' | gcloud secrets versions add $TELEGRAM_CHAT_ID_SECRET --data-file=- --project=$PROJECT_ID"
else
    read -p "Do you want to configure Telegram notifications? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Telegram Bot Token
        if [ "$TELEGRAM_BOT_EXISTS" = true ]; then
            log_info "Telegram bot token secret already exists"
            read -p "Do you want to update it? (y/N): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                read -p "Enter Telegram bot token: " BOT_TOKEN
                if [ -n "${SUDO_USER:-}" ]; then
                    sudo -u "$SUDO_USER" bash -c "echo -n '$BOT_TOKEN' | gcloud secrets versions add $TELEGRAM_BOT_TOKEN_SECRET --data-file=- --project=$PROJECT_ID"
                else
                    echo -n "$BOT_TOKEN" | gcloud secrets versions add "$TELEGRAM_BOT_TOKEN_SECRET" \
                        --data-file=- \
                        --project="$PROJECT_ID"
                fi
                log_info "Telegram bot token updated"
            fi
        else
            read -p "Enter Telegram bot token: " BOT_TOKEN
            if [ -n "${SUDO_USER:-}" ]; then
                sudo -u "$SUDO_USER" bash -c "echo -n '$BOT_TOKEN' | gcloud secrets create $TELEGRAM_BOT_TOKEN_SECRET --data-file=- --project=$PROJECT_ID --replication-policy=automatic"
            else
                echo -n "$BOT_TOKEN" | gcloud secrets create "$TELEGRAM_BOT_TOKEN_SECRET" \
                    --data-file=- \
                    --project="$PROJECT_ID" \
                    --replication-policy="automatic"
            fi
            log_info "Telegram bot token secret created"
        fi
        
        # Telegram Chat ID
        if [ "$TELEGRAM_CHAT_EXISTS" = true ]; then
            log_info "Telegram chat ID secret already exists"
            read -p "Do you want to update it? (y/N): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                read -p "Enter Telegram chat ID: " CHAT_ID
                if [ -n "${SUDO_USER:-}" ]; then
                    sudo -u "$SUDO_USER" bash -c "echo -n '$CHAT_ID' | gcloud secrets versions add $TELEGRAM_CHAT_ID_SECRET --data-file=- --project=$PROJECT_ID"
                else
                    echo -n "$CHAT_ID" | gcloud secrets versions add "$TELEGRAM_CHAT_ID_SECRET" \
                        --data-file=- \
                        --project="$PROJECT_ID"
                fi
                log_info "Telegram chat ID updated"
            fi
        else
            read -p "Enter Telegram chat ID: " CHAT_ID
            if [ -n "${SUDO_USER:-}" ]; then
                sudo -u "$SUDO_USER" bash -c "echo -n '$CHAT_ID' | gcloud secrets create $TELEGRAM_CHAT_ID_SECRET --data-file=- --project=$PROJECT_ID --replication-policy=automatic"
            else
                echo -n "$CHAT_ID" | gcloud secrets create "$TELEGRAM_CHAT_ID_SECRET" \
                    --data-file=- \
                    --project="$PROJECT_ID" \
                    --replication-policy="automatic"
            fi
            log_info "Telegram chat ID secret created"
        fi
        
        log_info "Telegram notifications configured successfully"
        log_info "You will receive notifications for:"
        log_info "  - Operation start"
        log_info "  - Operation success/failure"
        log_info "  - Daily log file"
        log_info "  - Daily summary"
    else
        log_info "Skipping Telegram configuration. You can configure it later by running:"
        log_info "  echo -n 'BOT_TOKEN' | gcloud secrets create $TELEGRAM_BOT_TOKEN_SECRET --data-file=- --project=$PROJECT_ID"
        log_info "  echo -n 'CHAT_ID' | gcloud secrets create $TELEGRAM_CHAT_ID_SECRET --data-file=- --project=$PROJECT_ID"
    fi
fi

# Grant Secret Manager access to VM service account
log_info "Configuring service account permissions..."
SERVICE_ACCOUNT_EMAIL=$(gcloud compute instances describe $(hostname) \
    --zone=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/zone" -H "Metadata-Flavor: Google" | cut -d/ -f4) \
    --format="value(serviceAccounts[0].email)" 2>/dev/null || echo "")

if [ -n "$SERVICE_ACCOUNT_EMAIL" ]; then
    log_info "VM service account: $SERVICE_ACCOUNT_EMAIL"
    log_info "Granting Secret Manager Secret Accessor role..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
        --role="roles/secretmanager.secretAccessor" \
        --condition=None 2>/dev/null || log_warn "Failed to grant permissions. You may need to do this manually."
else
    log_warn "Could not determine VM service account. Please grant Secret Manager access manually:"
    log_info "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
    log_info "    --member='serviceAccount:YOUR_SERVICE_ACCOUNT@$PROJECT_ID.iam.gserviceaccount.com' \\"
    log_info "    --role='roles/secretmanager.secretAccessor'"
fi

# Reload systemd
log_info "Reloading systemd daemon..."
systemctl daemon-reload

# Enable and start timer
log_info "Enabling and starting stake-move timer..."
systemctl enable stake-move.timer
systemctl start stake-move.timer

# Check timer status
log_info "Checking timer status..."
systemctl status stake-move.timer --no-pager || true

# Show next run time
NEXT_RUN=$(systemctl list-timers stake-move.timer --no-pager | grep stake-move.timer | awk '{print $1, $2, $3, $4, $5}' || echo "Unable to determine")
log_info "Next scheduled run: $NEXT_RUN"

# Test run option
log_info ""
log_info "You can test the automation now, or wait for the scheduled run at 8AM PST."
read -p "Do you want to run a test execution now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log_info "Running test execution..."
    log_info "This will perform an actual stake move operation!"
    systemctl start stake-move.service
    
    # Wait a bit for the service to start
    sleep 3
    
    # Show recent logs
    log_info "Recent service logs:"
    journalctl -u stake-move.service -n 50 --no-pager || true
    
    log_info ""
    log_info "To follow the logs in real-time, run:"
    log_info "  journalctl -u stake-move.service -f"
    log_info ""
    log_info "To view the daily log file:"
    log_info "  tail -f /var/log/stake-move/\$(date +%Y-%m-%d).log"
else
    log_info "Skipping test run. The automation will run automatically at 8AM PST daily."
    log_info "To manually trigger a test run later, use:"
    log_info "  sudo systemctl start stake-move.service"
fi

log_info "=========================================="
log_info "Deployment completed successfully!"
log_info "=========================================="
log_info "Installation directory: $INSTALL_DIR"
log_info "Log directory: /var/log/stake-move"
log_info ""
log_info "Useful commands:"
log_info "  Check timer status: systemctl status stake-move.timer"
log_info "  Check service logs: journalctl -u stake-move.service -f"
log_info "  View daily logs: tail -f /var/log/stake-move/\$(date +%Y-%m-%d).log"
log_info "  View summary: tail -f /var/log/stake-move/summary.log"
log_info "  Manual run: systemctl start stake-move.service"
log_info ""

