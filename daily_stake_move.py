#!/usr/bin/env python3
"""
Daily automated stake move operation.
Moves stake from a specific hotkey to RT21 using wallet sn35.
Runs at 8AM PST daily via systemd timer.
"""

import os
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
from google.cloud import secretmanager

from utils.telegram_notifier import TelegramNotifier

# Constants
ORIGIN_NETUID = 35
DEST_NETUID = 35
ORIGIN_HOTKEY = "5EsmkLf4VnpgNM31syMjAWsUrQdW2Yu5xzWbv6oDQydP9vVx"
DEST_HOTKEY = "5CATQqY6rA26Kkvm2abMTRtxnwyxigHZKxNJq86bUcpYsn35"
WALLET_NAME = "sn35"
SECRET_NAME = "stake-move-wallet-sn35-password"
TELEGRAM_BOT_TOKEN_SECRET = "stake-move-telegram-bot-token"
TELEGRAM_CHAT_ID_SECRET = "stake-move-telegram-chat-id"
LOG_DIR = Path("/var/log/stake-move")

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


def get_secret(secret_name: str, project_id: Optional[str] = None) -> str:
    """Fetch secret from GCP Secret Manager"""
    try:
        client = secretmanager.SecretManagerServiceClient()
        
        if project_id is None:
            # Try to get project ID from environment or gcloud config
            project_id = os.environ.get('GCP_PROJECT_ID')
            if not project_id:
                # Try to get from gcloud
                import subprocess
                result = subprocess.run(
                    ['gcloud', 'config', 'get-value', 'project'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    project_id = result.stdout.strip()
        
        if not project_id:
            raise ValueError("GCP_PROJECT_ID not set and could not determine from gcloud config")
        
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode('UTF-8')
    except Exception as e:
        logger.error(f"Failed to fetch secret {secret_name}: {e}")
        raise


def get_telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """Get Telegram credentials from Secret Manager"""
    try:
        bot_token = get_secret(TELEGRAM_BOT_TOKEN_SECRET)
        chat_id = get_secret(TELEGRAM_CHAT_ID_SECRET)
        return bot_token, chat_id
    except Exception:
        return None, None


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

    # Send start notification
    if telegram_notifier:
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
        start_msg = f"""🚀 <b>Daily Stake Move Started</b>

Date: {current_time}
Origin Hotkey: <code>{ORIGIN_HOTKEY}</code>
Destination Hotkey: <code>{DEST_HOTKEY}</code>
Wallet: {WALLET_NAME}"""
        telegram_notifier.send_message(start_msg)

    # Fetch password from GCP Secret Manager
    log("Fetching password from GCP Secret Manager...")
    try:
        password = get_secret(SECRET_NAME)
        log("Password retrieved successfully")
    except Exception as e:
        error_msg = f"Failed to fetch password from Secret Manager: {e}"
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
                telegram_msg = f"""❌ <b>Stake Move Failed</b>

Date: {current_time}
Error: {error_msg}"""
                telegram_notifier.send_message(telegram_msg)
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
        
        if origin_stake_before:
            log(f"Origin stake before move: {origin_stake_before.tao:.9f} α")
        if dest_stake_before:
            log(f"Destination stake before move: {dest_stake_before.tao:.9f} α")

        # Perform stake move
        log("Executing stake move operation...")
        try:
            success = subtensor.move_stake(
                wallet=wallet,
                origin_hotkey=ORIGIN_HOTKEY,
                origin_netuid=ORIGIN_NETUID,
                destination_hotkey=DEST_HOTKEY,
                destination_netuid=DEST_NETUID,
                move_all_stake=True,
            )
            
            if not success:
                raise Exception("move_stake returned False")
            
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
            
            # Send success notification
            if telegram_notifier:
                telegram_notifier.record_stake_move_success(amount_moved)
                current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                
                # Build success message with stake details
                dest_total_str = f"{dest_stake_after.tao:.9f} α" if dest_stake_after else "N/A (could not fetch)"
                origin_after_str = f"{origin_stake_after.tao:.9f} α" if origin_stake_after else "0.000000000 α"
                
                success_msg = f"""✅ <b>Stake Move Completed Successfully</b>

Date: {current_time}
Stake Moved: <b>{amount_moved:.9f} α</b>
Origin Stake After: {origin_after_str}
Destination Total: <b>{dest_total_str}</b>
Origin Hotkey: <code>{ORIGIN_HOTKEY}</code>
Destination Hotkey: <code>{DEST_HOTKEY}</code>"""
                telegram_notifier.send_message(success_msg)
                
                # Send log file
                try:
                    telegram_notifier.send_document(
                        str(LOG_FILE),
                        caption=f"Daily Stake Move Log - {current_date}"
                    )
                except Exception:
                    pass
            
            log("==========================================")
            log("Daily stake move operation completed")
            log("==========================================")
            
        except Exception as e:
            error_msg = f"Stake move operation failed: {e}"
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
            
    except Exception as e:
        error_msg = f"Failed to initialize bittensor components: {e}"
        log(f"ERROR: {error_msg}")
        log_summary(f"FAILED: {error_msg}")
        
        if telegram_notifier:
            current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
            telegram_msg = f"""❌ <b>Stake Move Failed</b>

Date: {current_time}
Error: {error_msg}"""
            telegram_notifier.send_message(telegram_msg)
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

