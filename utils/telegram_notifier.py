#!/usr/bin/env python3
"""
Telegram notification handler for stake move automation.
Based on patterns from liquidity_flow_controller's SlackNotifier.
"""

import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Any
import requests


class TelegramNotifier:
    """Handles Telegram notifications for stake move automation with daily summaries"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.enabled = bool(bot_token and chat_id)
        
        self.vm_ip = self._get_vm_ip()
        self.vm_hostname = self._get_vm_hostname()
        self.git_branch = self._get_git_branch()

        # Daily summary tracking
        self.startup_time = datetime.now(timezone.utc)
        self.daily_summary_lock = threading.Lock()
        self.last_summary_date = None

        # Persistent metrics (survive restarts)
        self.metrics_file = "stake_move_lifetime_metrics.json"
        self.lifetime_metrics = self._load_lifetime_metrics()

        # Daily metrics (reset each day)
        self.daily_metrics = {
            "stake_moves_count": 0,
            "stake_moves_failed": 0,
            "total_stake_moved": 0.0,  # in TAO
        }

        # Start daily summary thread
        self._start_daily_summary_thread()

    def _get_vm_ip(self) -> str:
        """Get the VM's IP address"""
        try:
            response = requests.get('https://api.ipify.org', timeout=5)
            return response.text
        except Exception:
            try:
                hostname = socket.gethostname()
                return socket.gethostbyname(hostname)
            except Exception:
                return "Unknown IP"

    def _get_vm_hostname(self) -> str:
        """Get the VM's hostname"""
        try:
            return socket.gethostname()
        except Exception:
            return "Unknown Hostname"

    def _get_git_branch(self) -> str:
        """Get the current git branch"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True,
                text=True,
                check=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            branch = result.stdout.strip()
            if branch:
                return branch
            return "Unknown Branch"
        except Exception:
            return "Unknown Branch"

    def _load_lifetime_metrics(self) -> Dict[str, Any]:
        """Load persistent metrics from file"""
        try:
            metrics_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                self.metrics_file
            )
            if os.path.exists(metrics_path):
                with open(metrics_path, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        
        # Default metrics
        return {
            "total_lifetime_stake_moved": 0.0,
            "total_uptime_seconds": 0,
            "last_shutdown_time": None
        }

    def _save_lifetime_metrics(self):
        """Save persistent metrics to file"""
        try:
            # Update uptime
            current_session_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
            self.lifetime_metrics["total_uptime_seconds"] += current_session_uptime
            self.lifetime_metrics["last_shutdown_time"] = datetime.now(timezone.utc).isoformat()

            metrics_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                self.metrics_file
            )
            with open(metrics_path, 'w') as f:
                json.dump(self.lifetime_metrics, f)
        except Exception:
            pass

    def _start_daily_summary_thread(self):
        """Start the daily summary thread"""
        if not self.enabled:
            return

        def daily_summary_loop():
            while True:
                try:
                    now = datetime.now(timezone.utc)
                    # Calculate seconds until next midnight UTC
                    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    if next_midnight <= now:
                        next_midnight = next_midnight.replace(day=next_midnight.day + 1)

                    sleep_seconds = (next_midnight - now).total_seconds()
                    time.sleep(sleep_seconds)

                    # Send daily summary
                    self._send_daily_summary()

                except Exception:
                    time.sleep(3600)  # Sleep 1 hour on error

        summary_thread = threading.Thread(target=daily_summary_loop, daemon=True)
        summary_thread.start()

    def _get_uptime_str(self) -> str:
        """Get formatted uptime string"""
        current_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
        total_uptime = self.lifetime_metrics["total_uptime_seconds"] + current_uptime

        if total_uptime >= 86400:
            return f"{total_uptime / 86400:.1f} days"
        else:
            return f"{total_uptime / 3600:.1f} hours"

    def _send_daily_summary(self):
        """Send daily summary report"""
        with self.daily_summary_lock:
            try:
                # Calculate uptime
                uptime_str = self._get_uptime_str()

                # Calculate success rate
                total_attempts = self.daily_metrics["stake_moves_count"] + self.daily_metrics["stake_moves_failed"]
                if total_attempts > 0:
                    success_rate = (self.daily_metrics["stake_moves_count"] / total_attempts) * 100
                else:
                    success_rate = 0.0

                # Build message
                message = f"""📊 <b>Daily Summary Report</b>

Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
Script Uptime: {uptime_str}

🔄 <b>Stake Moves</b>
Success: {self.daily_metrics['stake_moves_count']}
Failed: {self.daily_metrics['stake_moves_failed']}
Success Rate: {success_rate:.1f}%

💰 <b>Today's Stake Moved</b>
{self.daily_metrics['total_stake_moved']:.9f} α

📈 <b>Lifetime Stake Moved</b>
{self.lifetime_metrics['total_lifetime_stake_moved']:.9f} α

🖥️ <b>System Info</b>
Host: {self.vm_hostname}
IP: {self.vm_ip}
Branch: {self.git_branch}"""

                self.send_message(message)

                # Update lifetime metrics
                self.lifetime_metrics["total_lifetime_stake_moved"] += self.daily_metrics["total_stake_moved"]

                # Reset daily metrics after successful send
                self.daily_metrics = {
                    "stake_moves_count": 0,
                    "stake_moves_failed": 0,
                    "total_stake_moved": 0.0,
                }

            except Exception:
                pass

    def send_message(self, message: str, parse_mode: str = "HTML"):
        """Send a message to Telegram"""
        if not self.enabled:
            return

        try:
            response = requests.post(
                f"{self.api_url}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            response.raise_for_status()
        except Exception:
            pass  # Fail silently to avoid disrupting main flow

    def send_document(self, file_path: str, caption: str = ""):
        """Send a document/file to Telegram"""
        if not self.enabled:
            return

        try:
            with open(file_path, 'rb') as f:
                response = requests.post(
                    f"{self.api_url}/sendDocument",
                    data={
                        "chat_id": self.chat_id,
                        "caption": caption
                    },
                    files={"document": f},
                    timeout=30
                )
                response.raise_for_status()
        except Exception:
            pass  # Fail silently

    def send_sweep_success(self, amount: float, stats: dict):
        """
        Rich daily sweep notification with accounting context.

        stats keys (from SheetsLogger.get_sweep_stats()):
          current_balance, period_day, cycle_days,
          next_dist_date, days_until_dist,
          avg_7d, projected_dist, gtv_projected, ptn_projected,
          dashboard_url, distributions_url, daily_sweeps_url
        """
        if not self.enabled:
            return
        try:
            from datetime import date as _date

            def fmt(n: float) -> str:
                return f"{n:,.4f}"

            cycle = stats.get("cycle_days", 14)
            period_day = stats.get("period_day", 1)
            balance = stats.get("current_balance", 0.0)
            avg_7d = stats.get("avg_7d", 0.0)
            projected = stats.get("projected_dist", 0.0)
            gtv_proj = stats.get("gtv_projected", 0.0)
            ptn_proj = stats.get("ptn_projected", 0.0)
            days_until = stats.get("days_until_dist", 0)
            next_dist = stats.get("next_dist_date")
            dashboard_url = stats.get("dashboard_url", "")
            distributions_url = stats.get("distributions_url", "")
            daily_sweeps_url = stats.get("daily_sweeps_url", "")

            next_dist_str = next_dist.strftime("%b %d, %Y") if next_dist else "N/A"

            # Period progress bar (5 chars wide)
            filled = round((period_day / cycle) * 10)
            bar = "█" * filled + "░" * (10 - filled)

            # Projected line only shows if we have enough history
            proj_line = (
                f"   Projected dist:  <b>{fmt(projected)} α</b>\n"
                if avg_7d > 0 else ""
            )

            # Build deep links
            link_parts = []
            if dashboard_url:
                link_parts.append(f'<a href="{dashboard_url}">Dashboard</a>')
            if daily_sweeps_url:
                link_parts.append(f'<a href="{daily_sweeps_url}">Daily Sweeps</a>')
            if distributions_url:
                link_parts.append(f'<a href="{distributions_url}">Distributions</a>')
            links_line = "  ·  ".join(link_parts)

            message = (
                f"✅ <b>Daily Sweep — {datetime.now(timezone.utc).strftime('%b %d, %Y')}</b>\n"
                f"\n"
                f"💰 <b>Today's Earnings</b>\n"
                f"   <b>{fmt(amount)} α</b>  swept\n"
                f"\n"
                f"📊 <b>Period Progress</b>  (Day {period_day} of {cycle})\n"
                f"   {bar}\n"
                f"   Accumulated:  <b>{fmt(balance)} α</b>\n"
                f"   7-day avg:     {fmt(avg_7d)} α / day\n"
                f"{proj_line}"
                f"\n"
                f"🗓️ <b>Next Distribution</b>\n"
                f"   {next_dist_str}  ({days_until}d away)\n"
                f"   GTV: ~{fmt(gtv_proj)} α  |  PTN: ~{fmt(ptn_proj)} α\n"
                f"\n"
                f"👉 {links_line}"
            )
            self.send_message(message)
        except Exception:
            pass

    def send_distribution_alert(
        self,
        period_start,
        period_end,
        total_balance: float,
        gtv_amount: float,
        ptn_amount: float,
        gtv_wallet: str = "",
        ptn_wallet: str = "",
        sheet_url: str = "",
    ):
        """Send distribution day notification with amounts and sheet link."""
        if not self.enabled:
            return
        try:
            from datetime import date as _date
            def fmt_date(d) -> str:
                if isinstance(d, _date):
                    return d.strftime("%b %d, %Y")
                return str(d)

            period_days = ""
            try:
                from datetime import datetime as _dt
                if hasattr(period_start, "strftime"):
                    delta = (period_end - period_start).days + 1
                    period_days = f" ({delta} days)"
            except Exception:
                pass

            gtv_short = f"{gtv_wallet[:6]}...{gtv_wallet[-4:]}" if len(gtv_wallet) > 10 else gtv_wallet
            ptn_short = f"{ptn_wallet[:6]}...{ptn_wallet[-4:]}" if len(ptn_wallet) > 10 else ptn_wallet

            sheet_line = f'\n\n👉 <a href="{sheet_url}">View Sheet &amp; add tx links</a>' if sheet_url else ""

            message = (
                f"📅 <b>Distribution Day — {fmt_date(period_end)}</b>\n"
                f"\n"
                f"Period: {fmt_date(period_start)} → {fmt_date(period_end)}{period_days}\n"
                f"Current Balance: <b>{total_balance:,.4f} α</b>\n"
                f"\n"
                f"Split 50/50:\n"
                f"  GTV → <b>{gtv_amount:,.4f} α</b>\n"
                f"       <code>{gtv_wallet}</code>\n"
                f"  PTN → <b>{ptn_amount:,.4f} α</b>\n"
                f"       <code>{ptn_wallet}</code>\n"
                f"{sheet_line}\n"
                f"\nPlease complete the transfer and mark as Completed in the sheet."
            )
            self.send_message(message)
        except Exception:
            pass

    def send_distribution_reminder(self, pending_rows: list, sheet_url: str = ""):
        """Send a follow-up reminder for overdue pending distributions."""
        if not self.enabled or not pending_rows:
            return
        try:
            lines = ["⚠️ <b>Distribution Reminder</b>\n"]
            for row in pending_rows:
                dist_date = row.get("date", "?")
                gtv = row.get("gtv", "?")
                ptn = row.get("ptn", "?")
                has_links = row.get("has_tx_links", False)

                lines.append(f"Distribution Date: <b>{dist_date}</b>")
                lines.append(f"  GTV: {gtv} α  |  PTN: {ptn} α")
                if has_links:
                    lines.append("  ✅ Tx links found — please mark as <b>Completed</b> in the sheet.")
                else:
                    lines.append("  ❌ Still PENDING — transfer not recorded yet.")
                lines.append("")

            if sheet_url:
                lines.append(f'👉 <a href="{sheet_url}">Update sheet here</a>')

            self.send_message("\n".join(lines))
        except Exception:
            pass

    def record_stake_move_success(self, amount_tao: float):
        """Record a successful stake move"""
        with self.daily_summary_lock:
            self.daily_metrics["stake_moves_count"] += 1
            self.daily_metrics["total_stake_moved"] += amount_tao

    def record_stake_move_failure(self):
        """Record a failed stake move"""
        with self.daily_summary_lock:
            self.daily_metrics["stake_moves_failed"] += 1

    def shutdown(self):
        """Clean shutdown - save metrics"""
        try:
            self._save_lifetime_metrics()
        except Exception:
            pass

