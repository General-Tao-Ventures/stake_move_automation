#!/usr/bin/env bash
# deploy.sh
# ------------------------------------------------------------
# Deployment script for stake move automation
# Sets up .env file, installs dependencies, and configures systemd
# ------------------------------------------------------------
set -euo pipefail

############################ CONSTANTS ############################
INSTALL_DIR="/opt/stake-move-automation"
ENV_FILE="$INSTALL_DIR/.env"
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
check_command "python3" || log_warn "python3 not found, will install"
check_command "pip3" || log_warn "pip3 not found, will install"

# Install dependencies
log_info "Installing system dependencies..."
if command -v apt-get &> /dev/null; then
    apt-get update
    apt-get install -y python3 python3-pip python3-venv curl git
elif command -v yum &> /dev/null; then
    yum install -y python3 python3-pip curl git
elif command -v dnf &> /dev/null; then
    dnf install -y python3 python3-pip curl git
else
    log_warn "Unknown package manager. Please install 'python3', 'pip3', and 'curl' manually."
fi

# Verify Python installation
if ! command -v python3 &> /dev/null; then
    log_error "python3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

# Verify pip installation
if ! command -v pip3 &> /dev/null; then
    log_error "pip3 is not installed. Please install pip3."
    exit 1
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
cp "$SCRIPT_DIR/daily_stake_move.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/daily_stake_move.py"

# Copy utils directory
if [ -d "$SCRIPT_DIR/utils" ]; then
    cp -r "$SCRIPT_DIR/utils" "$INSTALL_DIR/"
    log_info "Copied utils directory"
fi

# Copy requirements.txt
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    log_info "Copied requirements.txt"
fi

# Install Python dependencies
log_info "Installing Python dependencies..."
if [ "$SERVICE_USER" != "root" ]; then
    USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
    PIP_CMD="pip3 install --user"
    PIP_TARGET="$USER_HOME/.local"
else
    PIP_CMD="pip3 install"
    PIP_TARGET="/usr/local"
fi

# Install requirements
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    log_info "Installing Python packages from requirements.txt..."
    if sudo -u "$SERVICE_USER" $PIP_CMD -r "$INSTALL_DIR/requirements.txt" 2>&1 | tee /tmp/pip_install.log; then
        log_info "Python dependencies installed successfully"
    else
        log_error "Failed to install Python dependencies. Check /tmp/pip_install.log for details."
        log_warn "You may need to install dependencies manually: $PIP_CMD -r $INSTALL_DIR/requirements.txt"
    fi
else
    log_warn "requirements.txt not found. Installing basic dependencies..."
    sudo -u "$SERVICE_USER" $PIP_CMD bittensor python-dotenv requests 2>&1 | tee /tmp/pip_install.log || log_warn "Some dependencies may have failed to install"
fi

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
        fi
    fi
fi

log_info "Service will run as user: $SERVICE_USER"

# Update service file with correct paths and user
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|" /etc/systemd/system/stake-move.service

# Determine Python path
if [ "$SERVICE_USER" != "root" ]; then
    USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
    PYTHON_CMD="$USER_HOME/.local/bin/python3"
    if [ ! -f "$PYTHON_CMD" ]; then
        # Fallback to system python3
        PYTHON_CMD="python3"
    fi
    UPDATED_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$USER_HOME/.local/bin"
else
    PYTHON_CMD="python3"
    UPDATED_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
fi

# Update ExecStart to use Python script
sed -i "s|ExecStart=.*|ExecStart=$PYTHON_CMD $INSTALL_DIR/daily_stake_move.py|" /etc/systemd/system/stake-move.service
sed -i "s|^User=.*|User=$SERVICE_USER|" /etc/systemd/system/stake-move.service

# Update PATH to include Python user install location
if [ -n "$USER_HOME" ] && [ "$SERVICE_USER" != "root" ]; then
    # Check if PATH line exists, if not add it
    if ! grep -q "^Environment=\"PATH=" /etc/systemd/system/stake-move.service; then
        sed -i "/^\[Service\]/a Environment=\"PATH=$UPDATED_PATH\"" /etc/systemd/system/stake-move.service
    else
        sed -i "s|^Environment=\"PATH=.*|Environment=\"PATH=$UPDATED_PATH\"|" /etc/systemd/system/stake-move.service
    fi
    log_info "Updated PATH in service file to include $USER_HOME/.local/bin"
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

# Set up .env file
log_info ""
log_info "Setting up .env file..."

if [ -f "$ENV_FILE" ]; then
    log_info ".env file already exists at $ENV_FILE"
    read -p "Do you want to update it? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Keeping existing .env file"
        # Show masked password if it exists
        if grep -q "^WALLET_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
            PASSWORD_VALUE=$(grep "^WALLET_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2-)
            MASKED=$(mask_secret "$PASSWORD_VALUE")
            log_info "  Current password: $MASKED"
        fi
    else
        # Update existing .env file
        log_info "Updating .env file..."
        # Backup existing file
        cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
        
        # Prompt for wallet password
        read -sp "Enter wallet password: " WALLET_PASSWORD
        echo
        
        # Create new .env file
        cat > "$ENV_FILE" << EOF
# Wallet password for sn35 wallet (REQUIRED)
WALLET_PASSWORD=$WALLET_PASSWORD
EOF
        
        # Prompt for Telegram (optional)
        read -p "Do you want to configure Telegram notifications? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "Enter Telegram bot token: " TELEGRAM_BOT_TOKEN
            read -p "Enter Telegram chat ID: " TELEGRAM_CHAT_ID
            cat >> "$ENV_FILE" << EOF

# Telegram bot token (optional - for notifications)
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN

# Telegram chat ID (optional - for notifications)
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
EOF
        fi
        
        log_info ".env file updated"
    fi
else
    # Create new .env file
    log_info "Creating .env file at $ENV_FILE"
    
    # Prompt for wallet password (required)
    read -sp "Enter wallet password: " WALLET_PASSWORD
    echo
    
    if [ -z "$WALLET_PASSWORD" ]; then
        log_error "Wallet password cannot be empty!"
        exit 1
    fi
    
    # Create .env file
    cat > "$ENV_FILE" << EOF
# Wallet password for sn35 wallet (REQUIRED)
WALLET_PASSWORD=$WALLET_PASSWORD
EOF
    
    # Prompt for Telegram (optional)
    read -p "Do you want to configure Telegram notifications? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -p "Enter Telegram bot token: " TELEGRAM_BOT_TOKEN
        read -p "Enter Telegram chat ID: " TELEGRAM_CHAT_ID
        cat >> "$ENV_FILE" << EOF

# Telegram bot token (optional - for notifications)
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN

# Telegram chat ID (optional - for notifications)
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
EOF
        log_info "Telegram notifications configured"
    fi
    
    log_info ".env file created successfully"
fi

# Set proper permissions on .env file (always ensure correct ownership)
if [ -f "$ENV_FILE" ]; then
    log_info "Setting permissions on .env file..."
    chmod 600 "$ENV_FILE"
    chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
    log_info "✓ .env file permissions set to 600 ($SERVICE_USER:$SERVICE_USER)"
else
    log_error ".env file not found at $ENV_FILE"
    exit 1
fi

log_info ""
log_info "To update the .env file later, edit it directly:"
log_info "  sudo nano $ENV_FILE"

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

