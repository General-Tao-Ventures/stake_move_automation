#!/usr/bin/env python3
"""
Google Sheets logger for SN35 stake move automation.

Handles:
- Logging each daily sweep to the 'Daily Sweeps' tab
- Checking whether today is a distribution Friday
- Logging a pending distribution row to the 'Distributions' tab
- Checking for overdue pending distributions (reminders)
- Reading current balance from the ledger
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


def _parse_float(value: str) -> float:
    """Parse a cell value that may contain currency symbols, commas, or 'α'."""
    if not value:
        return 0.0
    cleaned = str(value).replace("α", "").replace(",", "").replace("\xa0", "").strip()
    return float(cleaned) if cleaned else 0.0

# Sheet tab names
TAB_CONFIG = "Config"
TAB_SWEEPS = "Daily Sweeps"
TAB_DISTRIBUTIONS = "Distributions"

# Config key names (must match setup_sheets.py)
CONFIG_KEYS = {
    "starting_balance": "Starting_Balance",
    "opening_date": "Opening_Date",
    "cycle_days": "Cycle_Days",
    "first_dist_date": "First_Distribution_Date",
    "gtv_share": "GTV_Share",
    "ptn_share": "PTN_Share",
    "gtv_wallet": "GTV_Wallet",
    "ptn_wallet": "PTN_Wallet",
    "gtv_name": "GTV_Name",
    "ptn_name": "PTN_Name",
    "sheet_url": "Sheet_URL",
    "dashboard_url": "Dashboard_URL",
    "distributions_url": "Distributions_URL",
    "daily_sweeps_url": "Daily_Sweeps_URL",
}


class SheetsLogger:
    """Reads/writes SN35 distribution data to Google Sheets."""

    def __init__(self, sa_json_path: str, sheet_id: str):
        self._sa_json_path = sa_json_path
        self._sheet_id = sheet_id
        self._gc: Optional[gspread.Client] = None
        self._sh: Optional[gspread.Spreadsheet] = None
        self._config: dict = {}
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Authenticate and load config. Returns True on success."""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = Credentials.from_service_account_file(
                self._sa_json_path, scopes=scopes
            )
            self._gc = gspread.authorize(creds)
            self._sh = self._gc.open_by_key(self._sheet_id)
            self._load_config()
            self._connected = True
            logger.info(f"Google Sheets connected: {self._sh.title}")
            return True
        except Exception as e:
            logger.warning(f"Google Sheets connection failed: {e}")
            self._connected = False
            return False

    def _load_config(self):
        """Read all key-value pairs from the Config tab into self._config."""
        ws = self._sh.worksheet(TAB_CONFIG)
        rows = ws.get_all_values()
        raw = {row[0].strip(): row[1].strip() for row in rows if len(row) >= 2 and row[0].strip()}
        # Map to typed values
        try:
            self._config = {
                "starting_balance": float(raw.get(CONFIG_KEYS["starting_balance"], 0)),
                "opening_date": raw.get(CONFIG_KEYS["opening_date"], "2026-03-31"),
                "cycle_days": int(raw.get(CONFIG_KEYS["cycle_days"], 14)),
                "first_dist_date": raw.get(CONFIG_KEYS["first_dist_date"], "2026-04-10"),
                "gtv_share": float(raw.get(CONFIG_KEYS["gtv_share"], 0.5)),
                "ptn_share": float(raw.get(CONFIG_KEYS["ptn_share"], 0.5)),
                "gtv_wallet": raw.get(CONFIG_KEYS["gtv_wallet"], ""),
                "ptn_wallet": raw.get(CONFIG_KEYS["ptn_wallet"], ""),
                "gtv_name": raw.get(CONFIG_KEYS["gtv_name"], "GTV"),
                "ptn_name": raw.get(CONFIG_KEYS["ptn_name"], "PTN"),
                "sheet_url": raw.get(CONFIG_KEYS["sheet_url"], ""),
                "dashboard_url": raw.get(CONFIG_KEYS["dashboard_url"], ""),
                "distributions_url": raw.get(CONFIG_KEYS["distributions_url"], ""),
                "daily_sweeps_url": raw.get(CONFIG_KEYS["daily_sweeps_url"], ""),
            }
        except Exception as e:
            logger.warning(f"Config parse warning: {e}")

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def config(self) -> dict:
        return self._config

    @property
    def sheet_url(self) -> str:
        return self._config.get("sheet_url", "")

    # ------------------------------------------------------------------
    # Daily Sweeps
    # ------------------------------------------------------------------
    def log_daily_sweep(self, timestamp: datetime, amount: float, notes: str = "Auto-logged by script") -> bool:
        """
        Append one row to the Daily Sweeps tab.
        Cumulative total is calculated as the sum of all previous Amount values + this amount.
        Returns True on success.
        """
        if not self._connected:
            logger.warning("Sheets not connected — skipping log_daily_sweep")
            return False
        try:
            ws = self._sh.worksheet(TAB_SWEEPS)
            all_values = ws.get_all_values()

            # Build cumulative total by reading the last row's cumulative value
            # (more robust than re-summing all rows which can break on format changes)
            running_total = 0.0
            data_rows = [r for r in all_values[1:] if len(r) >= 3 and r[2]]
            if data_rows:
                running_total = _parse_float(data_rows[-1][2])
            running_total += amount

            date_str = timestamp.strftime("%Y-%m-%d")
            new_row = [date_str, amount, running_total, notes]
            ws.append_row(new_row, value_input_option="USER_ENTERED")
            logger.info(f"Logged sweep to sheet: {date_str} | {amount:.4f} α | cumulative: {running_total:.4f} α")
            return True
        except Exception as e:
            logger.warning(f"Failed to log daily sweep to sheets: {e}")
            return False

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------
    def get_current_balance(self) -> float:
        """
        Compute current balance:
          starting_balance + SUM(all sweeps) - SUM(completed distributions)
        """
        if not self._connected:
            return 0.0
        try:
            starting = self._config.get("starting_balance", 0.0)

            # Sum all sweep amounts (skip opening balance row)
            sweeps_ws = self._sh.worksheet(TAB_SWEEPS)
            sweep_rows = sweeps_ws.get_all_values()[1:]  # skip header
            total_sweeps = sum(
                _parse_float(r[1]) for r in sweep_rows
                if len(r) >= 2 and r[1] and (len(r) < 4 or r[3] != "Opening balance")
            )

            # Sum completed distributions
            dist_ws = self._sh.worksheet(TAB_DISTRIBUTIONS)
            dist_rows = dist_ws.get_all_values()[1:]  # skip header
            total_dist = sum(
                _parse_float(r[3]) for r in dist_rows
                if len(r) >= 7 and r[6].strip().lower() == "completed" and r[3]
            )

            balance = starting + total_sweeps - total_dist
            logger.info(f"Current balance: {starting:.4f} (start) + {total_sweeps:.4f} (sweeps) - {total_dist:.4f} (dist) = {balance:.4f} α")
            return balance
        except Exception as e:
            logger.warning(f"Failed to compute balance: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Distribution schedule
    # ------------------------------------------------------------------
    def _parse_date(self, s: str) -> date:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()

    def get_all_distribution_fridays(self) -> list[date]:
        """Return all distribution Fridays from first_dist_date onward (up to 5 years)."""
        cycle = self._config.get("cycle_days", 14)
        first = self._parse_date(self._config.get("first_dist_date", "2026-04-10"))
        fridays = []
        current = first
        limit = date.today() + timedelta(days=365 * 5)
        while current <= limit:
            fridays.append(current)
            current += timedelta(days=cycle)
        return fridays

    def check_distribution_due(self) -> tuple[bool, Optional[date], Optional[date]]:
        """
        Check if today is a distribution Friday.
        Returns (is_due, period_start, period_end).
        period_start = day after previous distribution (or opening_date for first).
        period_end   = today (the distribution Friday).
        """
        if not self._connected:
            return False, None, None
        try:
            today = date.today()
            fridays = self.get_all_distribution_fridays()

            if today not in fridays:
                return False, None, None

            idx = fridays.index(today)
            period_end = today
            if idx == 0:
                period_start = self._parse_date(self._config.get("opening_date", "2026-03-31"))
            else:
                period_start = fridays[idx - 1] + timedelta(days=1)

            logger.info(f"Distribution due today: {today} | period {period_start} → {period_end}")
            return True, period_start, period_end
        except Exception as e:
            logger.warning(f"check_distribution_due error: {e}")
            return False, None, None

    def days_until_next_distribution(self) -> int:
        """Return number of days until the next distribution Friday."""
        today = date.today()
        for d in self.get_all_distribution_fridays():
            if d >= today:
                return (d - today).days
        return -1

    # ------------------------------------------------------------------
    # Log pending distribution
    # ------------------------------------------------------------------
    def log_distribution_pending(
        self,
        period_start: date,
        period_end: date,
        total_balance: float,
        gtv_amount: float,
        ptn_amount: float,
    ) -> bool:
        """
        Append a Pending row to the Distributions tab.
        Returns True on success.
        """
        if not self._connected:
            return False
        try:
            ws = self._sh.worksheet(TAB_DISTRIBUTIONS)

            # Guard: don't log a duplicate Pending for the same date
            existing = ws.get_all_values()[1:]
            for row in existing:
                if row and row[0].strip() == str(period_end) and len(row) >= 7 and row[6].strip() == "Pending":
                    logger.info(f"Distribution Pending row for {period_end} already exists — skipping duplicate.")
                    return True

            new_row = [
                str(period_end),
                str(period_start),
                str(period_end),
                round(total_balance, 10),
                round(gtv_amount, 10),
                round(ptn_amount, 10),
                "Pending",
                "",  # GTV Tx Link
                "",  # PTN Tx Link
                "",  # Notes
            ]
            ws.append_row(new_row, value_input_option="USER_ENTERED")
            logger.info(
                f"Logged Pending distribution: {period_end} | "
                f"total={total_balance:.4f} α | GTV={gtv_amount:.4f} | PTN={ptn_amount:.4f}"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to log pending distribution: {e}")
            return False

    # ------------------------------------------------------------------
    # Rich sweep stats (for Telegram notification)
    # ------------------------------------------------------------------
    def get_sweep_stats(self, latest_amount: float) -> dict:
        """
        Return a dict of stats needed for the rich daily sweep notification.
        Computed after the sweep has already been logged to the sheet.

        Keys returned:
          current_balance, total_earned, period_day, cycle_days,
          next_dist_date, days_until_dist,
          avg_7d, avg_14d,
          projected_dist, gtv_projected, ptn_projected,
          dashboard_url, distributions_url, daily_sweeps_url
        """
        stats = {
            "current_balance": 0.0,
            "total_earned": 0.0,
            "period_day": 1,
            "cycle_days": self._config.get("cycle_days", 14),
            "next_dist_date": None,
            "days_until_dist": 0,
            "avg_7d": 0.0,
            "avg_14d": 0.0,
            "projected_dist": 0.0,
            "gtv_projected": 0.0,
            "ptn_projected": 0.0,
            "dashboard_url": self._config.get("dashboard_url", ""),
            "distributions_url": self._config.get("distributions_url", ""),
            "daily_sweeps_url": self._config.get("daily_sweeps_url", ""),
        }
        if not self._connected:
            return stats

        try:
            today = date.today()
            cycle = self._config.get("cycle_days", 14)
            gtv_share = self._config.get("gtv_share", 0.5)
            ptn_share = self._config.get("ptn_share", 0.5)

            # All sweep rows (skip header + opening balance)
            sweeps_ws = self._sh.worksheet(TAB_SWEEPS)
            sweep_rows = sweeps_ws.get_all_values()[1:]
            real_sweeps = [
                r for r in sweep_rows
                if len(r) >= 2 and r[1] and (len(r) < 4 or r[3] != "Opening balance")
            ]

            # Total earned from real sweeps
            total_earned = sum(_parse_float(r[1]) for r in real_sweeps if r[1])

            # Completed distributions
            dist_ws = self._sh.worksheet(TAB_DISTRIBUTIONS)
            dist_rows = dist_ws.get_all_values()[1:]
            total_dist = sum(
                _parse_float(r[3]) for r in dist_rows
                if len(r) >= 7 and r[6].strip().lower() == "completed" and r[3]
            )

            current_balance = self._config.get("starting_balance", 0.0) + total_earned - total_dist

            # 7-day and 14-day averages (sweep amounts only, by date)
            def avg_last_n_days(n: int) -> float:
                cutoff = today - timedelta(days=n)
                amounts = []
                for r in real_sweeps:
                    try:
                        row_date = datetime.strptime(r[0].strip(), "%Y-%m-%d").date()
                        if row_date >= cutoff:
                            amounts.append(_parse_float(r[1]))
                    except (ValueError, IndexError):
                        pass
                return sum(amounts) / len(amounts) if amounts else 0.0

            avg_7d = avg_last_n_days(7)
            avg_14d = avg_last_n_days(14)

            # Next distribution date and days remaining in period
            fridays = self.get_all_distribution_fridays()
            next_dist = None
            for f in fridays:
                if f >= today:
                    next_dist = f
                    break

            days_until = (next_dist - today).days if next_dist else 0
            # Period day = how far into the current 14-day window we are
            first = self._parse_date(self._config.get("first_dist_date", "2026-04-10"))
            period_day = ((today - first).days % cycle) + 1

            # Projected distribution = current balance + avg_14d * remaining days
            daily_avg = avg_14d if avg_14d > 0 else avg_7d
            projected = current_balance + (daily_avg * days_until)

            stats.update({
                "current_balance": round(current_balance, 4),
                "total_earned": round(total_earned, 4),
                "period_day": period_day,
                "next_dist_date": next_dist,
                "days_until_dist": days_until,
                "avg_7d": round(avg_7d, 4),
                "avg_14d": round(avg_14d, 4),
                "projected_dist": round(projected, 4),
                "gtv_projected": round(projected * gtv_share, 4),
                "ptn_projected": round(projected * ptn_share, 4),
            })
        except Exception as e:
            logger.warning(f"get_sweep_stats error: {e}")

        return stats

    # ------------------------------------------------------------------
    # Pending reminder check
    # ------------------------------------------------------------------
    def check_pending_reminder(self) -> list[dict]:
        """
        Return a list of distribution rows that are:
        - Status == Pending
        - Distribution Date < today (overdue)

        Each dict has keys: date, period_start, period_end, total, gtv, ptn,
                            has_tx_links (bool), gtv_link, ptn_link
        """
        if not self._connected:
            return []
        try:
            today = date.today()
            ws = self._sh.worksheet(TAB_DISTRIBUTIONS)
            rows = ws.get_all_values()[1:]  # skip header

            pending = []
            for row in rows:
                if len(row) < 7:
                    continue
                if row[6].strip().lower() != "pending":
                    continue
                try:
                    dist_date = self._parse_date(row[0])
                except ValueError:
                    continue
                if dist_date >= today:
                    continue  # not overdue yet

                gtv_link = row[7].strip() if len(row) > 7 else ""
                ptn_link = row[8].strip() if len(row) > 8 else ""
                has_tx_links = bool(gtv_link and ptn_link)

                pending.append({
                    "date": dist_date,
                    "period_start": row[1].strip(),
                    "period_end": row[2].strip(),
                    "total": row[3].strip(),
                    "gtv": row[4].strip(),
                    "ptn": row[5].strip(),
                    "has_tx_links": has_tx_links,
                    "gtv_link": gtv_link,
                    "ptn_link": ptn_link,
                })

            if pending:
                logger.info(f"Found {len(pending)} overdue pending distribution(s)")
            return pending
        except Exception as e:
            logger.warning(f"check_pending_reminder error: {e}")
            return []
