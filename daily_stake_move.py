#!/usr/bin/env python3
"""
Daily automated stake move operation.
Moves stake from a specific hotkey to RT21 using wallet sn35.
Runs at 8AM PST daily via systemd timer.
"""

import os
import re
import sys
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bittensor as bt
from bittensor.utils.balance import Balance
from bittensor_wallet import Wallet
from bittensor_wallet.errors import KeyFileError, PasswordError
from dotenv import load_dotenv

from utils.telegram_notifier import TelegramNotifier
from utils.sheets_logger import SheetsLogger

# Constants
ORIGIN_NETUID = 35
DEST_NETUID = 35
ORIGIN_HOTKEY = "5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx"
DEST_HOTKEY = "5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35"
WALLET_NAME = "sn35"
LOG_DIR = Path("/var/log/stake-move")
MINIMUM_STAKE_THRESHOLD = 0.001  # α — below this, nothing worth sweeping


class _BittensorErrorCapture(logging.Handler):
    """Temporary handler that captures bittensor log messages during move_stake."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord):
        msg = record.getMessage()
        # Strip Rich markup tags like [red], [/red], :cross_mark: etc.
        msg = re.sub(r':\w+:', '', msg)
        msg = re.sub(r'\[/?[a-zA-Z_0-9 ]+\]', '', msg).strip()
        if msg:
            self.records.append(msg)

    def best_error(self) -> str:
        """Return the most informative error line captured."""
        # Prefer lines that mention the actual subtensor error
        for msg in self.records:
            if any(kw in msg for kw in ('error', 'Error', 'Failed', 'failed', 'AmountToo', 'returned')):
                return msg
        return self.records[-1] if self.records else ""

# Load environment variables from .env file
# Try multiple locations: script directory, /opt/stake-move-automation, current directory
ENV_PATHS = [
    Path(__file__).parent / ".env",
    Path("/opt/stake-move-automation") / ".env",
    Path.cwd() / ".env",
]
env_loaded = False
for env_path in ENV_PATHS:
    if env_path.exists():
        try:
            load_dotenv(env_path)
            env_loaded = True
            break
        except PermissionError:
            # If we can't read this one, try the next
            continue
        except Exception as e:
            # Log but continue trying other paths
            print(f"Warning: Failed to load .env from {env_path}: {e}", file=sys.stderr)
            continue

# Setup logging
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
SUMMARY_LOG = LOG_DIR / "summary.log"

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def log(message: str):
    """Log a message with timestamp"""
    logger.info(message)


def log_summary(message: str):
    """Log a summary message"""
    logger.info(message)
    try:
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
        with open(SUMMARY_LOG, 'a') as f:
            f.write(f"[{current_time}] {message}\n")
    except Exception:
        pass


def get_telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """Get Telegram credentials from environment variables"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    return bot_token, chat_id


def init_sheets_logger() -> Optional[SheetsLogger]:
    """Initialize Google Sheets logger from environment variables."""
    sa_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    sheet_id = os.environ.get('GOOGLE_SHEET_ID')
    if not sa_json or not sheet_id:
        log("Google Sheets credentials not configured, skipping sheet logging")
        return None
    sheets = SheetsLogger(sa_json_path=sa_json, sheet_id=sheet_id)
    if sheets.connect():
        log("Google Sheets logging enabled")
        return sheets
    log("Warning: Google Sheets connection failed, continuing without sheet logging")
    return None


def ensure_wallet_password_cached(wallet: "bt.wallet", password_value: Optional[str] = None) -> None:
    """Set wallet password in environment variables for keyfile unlocks."""
    if not password_value:
        return
    
    password = password_value.strip()
    if not password:
        return
    
    # Get keyfiles and set passwords in their expected environment variables
    for attr in ("coldkey_file", "hotkey_file"):
        try:
            file_obj = getattr(wallet, attr, None)
            if file_obj is None:
                continue
            
            # Check if encrypted
            try:
                if not file_obj.exists_on_device() or not file_obj.is_encrypted():
                    continue
            except Exception:
                continue
            
            # Get the environment variable name for this keyfile
            env_attr = getattr(file_obj, "env_var_name", None)
            if not env_attr:
                continue
            
            if callable(env_attr):
                try:
                    env_var = env_attr()
                except TypeError:
                    env_var = None
            else:
                env_var = env_attr
            
            if not env_var:
                continue
            
            env_var = str(env_var)
            
            # Set password in environment using keyfile's method if available
            try:
                file_obj.save_password_to_env(password)
            except AttributeError:
                # Fallback to direct environment variable setting
                os.environ[env_var] = password
            
            logger.debug(f"Set password for {attr} in environment variable {env_var}")
        except Exception as e:
            logger.debug(f"Could not set password for {attr}: {e}")
            continue


def unlock_wallet(wallet: "bt.wallet") -> None:
    """Unlock wallet with proper error handling"""
    logger.debug(f"Attempting to unlock coldkey for wallet {wallet}")
    try:
        wallet.unlock_coldkey()
        logger.debug("Coldkey unlocked successfully")
    except PasswordError as err:
        error_msg = f"Invalid coldkey password: {err}"
        logger.error(f"ERROR: {error_msg}")
        raise Exception(error_msg) from err
    except KeyFileError as err:
        error_msg = f"Coldkey file error: {err}"
        logger.error(f"ERROR: {error_msg}")
        raise Exception(error_msg) from err
    
    try:
        logger.debug(f"Attempting to unlock hotkey for wallet {wallet}")
        wallet.unlock_hotkey()
        logger.debug("Hotkey unlocked successfully")
    except PasswordError:
        logger.warning("Hotkey password is invalid; continuing since coldkey suffices for staking extrinsics")
    except KeyFileError:
        logger.warning("Hotkey file missing or unreadable; continuing since coldkey suffices for staking extrinsics")


def fetch_stake_amount(subtensor: bt.subtensor, coldkey_ss58: str, hotkey_ss58: str, netuid: int) -> Optional[Balance]:
    """Fetch stake amount for a hotkey"""
    try:
        stake = subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=hotkey_ss58,
            netuid=netuid,
        )
        return stake
    except Exception as e:
        logger.error(f"Failed to fetch stake for {hotkey_ss58}: {e}")
        return None


def main():
    """Main execution function"""
    log("==========================================")
    log("Starting daily stake move operation")
    log("==========================================")
    log(f"Origin Netuid: {ORIGIN_NETUID}")
    log(f"Destination Netuid: {DEST_NETUID}")
    log(f"Origin Hotkey: {ORIGIN_HOTKEY}")
    log(f"Destination Hotkey: {DEST_HOTKEY}")
    log(f"Wallet: {WALLET_NAME}")

    # Initialize Telegram notifier
    telegram_notifier: Optional[TelegramNotifier] = None
    try:
        bot_token, chat_id = get_telegram_credentials()
        if bot_token and chat_id:
            telegram_notifier = TelegramNotifier(bot_token, chat_id)
            log("Telegram notifications enabled")
        else:
            log("Telegram credentials not configured, skipping notifications")
    except Exception as e:
        log(f"Warning: Failed to initialize Telegram notifier: {e}")

    # Initialize Google Sheets logger
    sheets_logger: Optional[SheetsLogger] = None
    try:
        sheets_logger = init_sheets_logger()
    except Exception as e:
        log(f"Warning: Failed to initialize Sheets logger: {e}")

    # Send start notification (minimal — success/fail message provides the detail)
    if telegram_notifier:
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
        telegram_notifier.send_message(
            f"⏳ <b>Daily Sweep Running</b>  —  {current_time}\n"
            f"Wallet: {WALLET_NAME}  |  Network: SN{ORIGIN_NETUID}"
        )

    # Get password from environment variable
    log("Reading password from environment variable...")
    password = os.environ.get('WALLET_PASSWORD')
    if not password:
        error_msg = "WALLET_PASSWORD environment variable not set. Please create a .env file with WALLET_PASSWORD=your_password"
        log(f"ERROR: {error_msg}")
        log_summary(f"FAILED: {error_msg}")
        
        if telegram_notifier:
            current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
            telegram_msg = f"""❌ <b>Stake Move Failed</b>

Date: {current_time}
Origin Hotkey: <code>{ORIGIN_HOTKEY}</code>
Destination Hotkey: <code>{DEST_HOTKEY}</code>
Error: {error_msg}

Please check the logs for more details."""
            telegram_notifier.send_message(telegram_msg)
            telegram_notifier.record_stake_move_failure()
        
        sys.exit(1)
    
    log("Password retrieved successfully")

    # Initialize bittensor components
    try:
        # Create wallet config using argparse and bt.config()
        parser = argparse.ArgumentParser()
        Wallet.add_args(parser)
        bt.subtensor.add_args(parser)
        bt.logging.add_args(parser)
        
        # Parse with wallet name and hotkey
        args = [
            '--wallet.name', WALLET_NAME,
            '--wallet.hotkey', 'default',  # Default hotkey name
        ]
        config = bt.config(parser, args=args)
        
        # Setup bittensor logging
        bt.logging(config=config)
        log("Bittensor logging configured")
        
        # Create wallet using bt.wallet factory function (not Wallet class directly)
        wallet = bt.wallet(config=config)
        log(f"Wallet created: {wallet}")
        
        # Set password in environment for wallet unlock
        # The ensure_wallet_password_cached function will set it in the correct env vars
        ensure_wallet_password_cached(wallet, password_value=password)
        
        # Unlock wallet with proper error handling
        try:
            unlock_wallet(wallet)
            log("Wallet unlocked successfully")
        except Exception as e:
            error_msg = f"Failed to unlock wallet: {e}"
            log(f"ERROR: {error_msg}")
            log_summary(f"FAILED: {error_msg}")
            
            if telegram_notifier:
                current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                telegram_notifier.send_message(
                    f"❌ <b>Sweep Failed — Wallet Unlock Error</b>\n\n"
                    f"⏱ {current_time}\n\n"
                    f"🔴 <b>Error</b>\n"
                    f"   <code>{error_msg}</code>\n\n"
                    f"Check the wallet password in <code>.env</code>."
                )
                telegram_notifier.record_stake_move_failure()

            sys.exit(1)

        # Create subtensor connection using config
        subtensor = bt.subtensor(config=config)
        log(f"Connected to subtensor network: {subtensor.network}")
        
        # Get coldkey address
        coldkey_ss58 = wallet.coldkeypub.ss58_address
        log(f"Coldkey: {coldkey_ss58}")

        # Fetch initial stake amounts
        log("Fetching initial stake amounts...")
        origin_stake_before = fetch_stake_amount(subtensor, coldkey_ss58, ORIGIN_HOTKEY, ORIGIN_NETUID)
        dest_stake_before = fetch_stake_amount(subtensor, coldkey_ss58, DEST_HOTKEY, DEST_NETUID)

        origin_tao = origin_stake_before.tao if origin_stake_before else 0.0
        dest_tao = dest_stake_before.tao if dest_stake_before else 0.0

        log(f"Origin stake: {origin_tao:.9f} α")
        log(f"Destination stake: {dest_tao:.9f} α")

        # ---------------------------------------------------------------
        # Pre-check: skip if nothing to sweep
        # ---------------------------------------------------------------
        if origin_tao < MINIMUM_STAKE_THRESHOLD:
            skip_msg = (
                f"Nothing to sweep — origin has {origin_tao:.9f} α "
                f"(below threshold of {MINIMUM_STAKE_THRESHOLD} α)"
            )
            log(skip_msg)
            log_summary(f"SKIPPED: {skip_msg}")

            if telegram_notifier:
                sheet_url = sheets_logger.sheet_url if sheets_logger else ""
                sheet_line = f'\n\n👉 <a href="{sheet_url}">View Sheet</a>' if sheet_url else ""
                telegram_notifier.send_message(
                    f"ℹ️ <b>Nothing to Sweep — {datetime.now(timezone.utc).strftime('%b %d, %Y')}</b>\n\n"
                    f"Origin stake:  <b>0.0000 α</b>  (not accumulated yet)\n"
                    f"Accumulated:   <b>{dest_tao:,.4f} α</b>  in destination\n\n"
                    f"The subnet hasn't emitted new stake to the origin hotkey yet. "
                    f"This is normal — try again tomorrow.{sheet_line}"
                )
            return  # clean exit — not an error

        # Perform stake move
        log("Executing stake move operation...")
        try:
            # Attach a temporary handler to capture bittensor's internal error output
            bt_capture = _BittensorErrorCapture()
            root_log = logging.getLogger()
            root_log.addHandler(bt_capture)
            try:
                success = subtensor.move_stake(
                    wallet=wallet,
                    origin_hotkey=ORIGIN_HOTKEY,
                    origin_netuid=ORIGIN_NETUID,
                    destination_hotkey=DEST_HOTKEY,
                    destination_netuid=DEST_NETUID,
                    move_all_stake=True,
                )
            finally:
                root_log.removeHandler(bt_capture)

            if not success:
                captured = bt_capture.best_error()
                detail = f" — {captured}" if captured else ""
                raise Exception(f"move_stake returned False{detail}")
            
            log("Stake move operation completed successfully")
            
            # Fetch final stake amounts
            log("Fetching final stake amounts...")
            origin_stake_after = fetch_stake_amount(subtensor, coldkey_ss58, ORIGIN_HOTKEY, ORIGIN_NETUID)
            dest_stake_after = fetch_stake_amount(subtensor, coldkey_ss58, DEST_HOTKEY, DEST_NETUID)
            
            # Calculate amount moved
            if origin_stake_before and origin_stake_after:
                amount_moved = origin_stake_before.tao - origin_stake_after.tao
            elif origin_stake_before:
                amount_moved = origin_stake_before.tao
            else:
                amount_moved = 0.0
            
            log(f"Stake moved: {amount_moved:.9f} α")
            if origin_stake_after:
                log(f"Origin stake after move: {origin_stake_after.tao:.9f} α")
            if dest_stake_after:
                log(f"Destination stake after move: {dest_stake_after.tao:.9f} α")
            
            # Log summary
            log_summary(f"SUCCESS: Stake moved from {ORIGIN_HOTKEY} to {DEST_HOTKEY}")
            log_summary(f"  Stake moved: {amount_moved:.9f} α")
            if dest_stake_after:
                log_summary(f"  Destination total: {dest_stake_after.tao:.9f} α")

            # -------------------------------------------------------
            # Google Sheets: log the sweep
            # -------------------------------------------------------
            if sheets_logger and amount_moved > 0:
                try:
                    sheets_logger.log_daily_sweep(
                        timestamp=datetime.now(timezone.utc),
                        amount=amount_moved,
                    )
                except Exception as e:
                    log(f"Warning: Failed to log sweep to sheets: {e}")

            # -------------------------------------------------------
            # Google Sheets: check if distribution is due today
            # -------------------------------------------------------
            if sheets_logger:
                try:
                    is_due, period_start, period_end = sheets_logger.check_distribution_due()
                    if is_due:
                        current_balance = sheets_logger.get_current_balance()
                        cfg = sheets_logger.config
                        gtv_share = cfg.get("gtv_share", 0.5)
                        ptn_share = cfg.get("ptn_share", 0.5)
                        gtv_amount = current_balance * gtv_share
                        ptn_amount = current_balance * ptn_share

                        # Log pending distribution row
                        sheets_logger.log_distribution_pending(
                            period_start=period_start,
                            period_end=period_end,
                            total_balance=current_balance,
                            gtv_amount=gtv_amount,
                            ptn_amount=ptn_amount,
                        )

                        # Send distribution alert via Telegram
                        if telegram_notifier:
                            telegram_notifier.send_distribution_alert(
                                period_start=period_start,
                                period_end=period_end,
                                total_balance=current_balance,
                                gtv_amount=gtv_amount,
                                ptn_amount=ptn_amount,
                                gtv_wallet=cfg.get("gtv_wallet", ""),
                                ptn_wallet=cfg.get("ptn_wallet", ""),
                                sheet_url=sheets_logger.sheet_url,
                            )
                        log(f"Distribution due: {current_balance:.4f} α → GTV {gtv_amount:.4f} | PTN {ptn_amount:.4f}")
                except Exception as e:
                    log(f"Warning: Distribution check failed: {e}")

            # -------------------------------------------------------
            # Google Sheets: check for overdue pending distributions (reminder)
            # -------------------------------------------------------
            if sheets_logger:
                try:
                    pending = sheets_logger.check_pending_reminder()
                    if pending and telegram_notifier:
                        telegram_notifier.send_distribution_reminder(
                            pending_rows=pending,
                            sheet_url=sheets_logger.sheet_url,
                        )
                except Exception as e:
                    log(f"Warning: Pending reminder check failed: {e}")

            # Send success notification
            if telegram_notifier:
                telegram_notifier.record_stake_move_success(amount_moved)

                # Fetch rich stats from sheet for the notification
                stats = {}
                if sheets_logger:
                    try:
                        stats = sheets_logger.get_sweep_stats(amount_moved)
                    except Exception as e:
                        log(f"Warning: Could not fetch sweep stats: {e}")

                if stats:
                    telegram_notifier.send_sweep_success(amount=amount_moved, stats=stats)
                else:
                    # Fallback to simple message if sheets not available
                    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                    dest_total_str = f"{dest_stake_after.tao:.4f} α" if dest_stake_after else "N/A"
                    telegram_notifier.send_message(
                        f"✅ <b>Daily Sweep — {current_time}</b>\n\n"
                        f"Swept: <b>{amount_moved:.4f} α</b>\n"
                        f"Destination Total: <b>{dest_total_str}</b>"
                    )
            
            log("==========================================")
            log("Daily stake move operation completed")
            log("==========================================")
            
        except Exception as e:
            error_msg = f"Stake move operation failed: {e}"
            log(f"ERROR: {error_msg}")
            log_summary(f"FAILED: {error_msg}")

            if telegram_notifier:
                current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                sheet_url = sheets_logger.sheet_url if sheets_logger else ""
                sheet_line = f'\n\n👉 <a href="{sheet_url}">View Sheet</a>' if sheet_url else ""
                telegram_notifier.send_message(
                    f"❌ <b>Sweep Failed — {datetime.now(timezone.utc).strftime('%b %d, %Y')}</b>\n\n"
                    f"⏱ {current_time}\n\n"
                    f"📉 <b>Stake at time of failure</b>\n"
                    f"   Origin:       <b>{origin_tao:,.9f} α</b>\n"
                    f"   Destination:  <b>{dest_tao:,.4f} α</b>\n\n"
                    f"🔴 <b>Error</b>\n"
                    f"   <code>{e}</code>\n\n"
                    f"Check <code>/var/log/stake-move/</code> for full details.{sheet_line}"
                )
                telegram_notifier.record_stake_move_failure()
            
            sys.exit(1)
            
    except Exception as e:
        error_msg = f"Failed to initialize bittensor components: {e}"
        log(f"ERROR: {error_msg}")
        log_summary(f"FAILED: {error_msg}")
        
        if telegram_notifier:
            current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
            telegram_notifier.send_message(
                f"❌ <b>Sweep Failed — Initialisation Error</b>\n\n"
                f"⏱ {current_time}\n\n"
                f"🔴 <b>Error</b>\n"
                f"   <code>{error_msg}</code>\n\n"
                f"Check <code>/var/log/stake-move/</code> for full details."
            )
            telegram_notifier.record_stake_move_failure()

        sys.exit(1)
    
    finally:
        # Cleanup
        if telegram_notifier:
            telegram_notifier.shutdown()
        
        # Clear password from environment
        os.environ.pop('MINER_WALLET_PASSWORD', None)


if __name__ == "__main__":
    main()

