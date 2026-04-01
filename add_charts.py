#!/usr/bin/env python3
"""
Adds charts to the SN35 Distribution Dashboard sheet.

Charts added:
  1. Daily Earnings — line chart (per-day swept α)
  2. Cumulative Balance — area chart (running total α)
  3. Distribution History — column chart (GTV vs PTN per distribution)

Run this whenever you want to refresh or re-add charts:
    python3 add_charts.py
"""

import gspread
from google.oauth2.service_account import Credentials
from pathlib import Path

SA_FILE = str(Path(__file__).parent / "google-sheets-sa.json")
SHEET_ID = "1_FvpOzJQRSR6x-5Q0fT7187-1yHlC37Ornb-j5hYqh0"

# Tab GIDs (from sheet metadata)
DASHBOARD_GID   = 1524296124
SWEEPS_GID      = 1397165976
DIST_GID        = 1137974863


def rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {
        "red":   int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue":  int(h[4:6], 16) / 255,
    }


def source_range(sheet_gid: int, start_row: int, end_row: int,
                 start_col: int, end_col: int) -> dict:
    return {
        "sheetId": sheet_gid,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_col,
        "endColumnIndex": end_col,
    }


def overlay_position(sheet_gid: int, row: int, col: int,
                     width: int, height: int) -> dict:
    return {
        "overlayPosition": {
            "anchorCell": {
                "sheetId": sheet_gid,
                "rowIndex": row,
                "columnIndex": col,
            },
            "widthPixels": width,
            "heightPixels": height,
            "offsetXPixels": 10,
            "offsetYPixels": 10,
        }
    }


def connect():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SA_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def delete_existing_charts(sh):
    """Remove all charts currently on the Dashboard to avoid duplicates."""
    dashboard = sh.worksheet("Dashboard")
    # Fetch spreadsheet metadata to find chart IDs
    meta = sh.fetch_sheet_metadata()
    sheets_meta = meta.get("sheets", [])
    requests = []
    for s in sheets_meta:
        if s["properties"]["sheetId"] == DASHBOARD_GID:
            for chart in s.get("charts", []):
                requests.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
    if requests:
        sh.batch_update({"requests": requests})
        print(f"  Deleted {len(requests)} existing chart(s).")
    else:
        print("  No existing charts to delete.")


def chart_daily_earnings() -> dict:
    """
    Line chart: Daily Earnings (α swept per day)
    Source: Daily Sweeps — col A (date, domain), col B (amount, series)
    Placed at Dashboard row 38, col 0
    """
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Daily Earnings (α swept per day)",
                    "titleTextFormat": {"bold": True, "fontSize": 13},
                    "basicChart": {
                        "chartType": "LINE",
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [
                            {
                                "position": "BOTTOM_AXIS",
                                "title": "Date",
                                "titleTextPosition": {"horizontalAlignment": "CENTER"},
                            },
                            {
                                "position": "LEFT_AXIS",
                                "title": "Amount (α)",
                                "titleTextPosition": {"horizontalAlignment": "CENTER"},
                            },
                        ],
                        "domains": [
                            {
                                "domain": {
                                    "sourceRange": {
                                        "sources": [source_range(SWEEPS_GID, 1, 1000, 0, 1)]
                                    }
                                }
                            }
                        ],
                        "series": [
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [source_range(SWEEPS_GID, 1, 1000, 1, 2)]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "color": rgb("#0f9d58"),
                                "lineStyle": {"width": 2, "type": "SOLID"},
                                "dataLabel": {
                                    "type": "DATA",
                                    "textFormat": {"fontSize": 8},
                                    "placement": "ABOVE",
                                },
                            }
                        ],
                        "headerCount": 1,
                        "interpolateNulls": True,
                    },
                    "backgroundColor": rgb("#f8fffe"),
                },
                "position": overlay_position(DASHBOARD_GID, row=38, col=0, width=620, height=340),
            }
        }
    }


def chart_cumulative_balance() -> dict:
    """
    Area chart: Cumulative Balance over time
    Source: Daily Sweeps — col A (date), col C (cumulative total)
    Placed at Dashboard row 38, col 4
    """
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Cumulative Accumulated Balance (α)",
                    "titleTextFormat": {"bold": True, "fontSize": 13},
                    "basicChart": {
                        "chartType": "AREA",
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [
                            {
                                "position": "BOTTOM_AXIS",
                                "title": "Date",
                            },
                            {
                                "position": "LEFT_AXIS",
                                "title": "Balance (α)",
                            },
                        ],
                        "domains": [
                            {
                                "domain": {
                                    "sourceRange": {
                                        "sources": [source_range(SWEEPS_GID, 1, 1000, 0, 1)]
                                    }
                                }
                            }
                        ],
                        "series": [
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [source_range(SWEEPS_GID, 1, 1000, 2, 3)]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "color": rgb("#1a73e8"),
                                "lineStyle": {"width": 2, "type": "SOLID"},
                            }
                        ],
                        "headerCount": 1,
                        "interpolateNulls": True,
                        "stackedType": "NOT_STACKED",
                    },
                    "backgroundColor": rgb("#f8fbff"),
                },
                "position": overlay_position(DASHBOARD_GID, row=38, col=4, width=620, height=340),
            }
        }
    }


def chart_distribution_history() -> dict:
    """
    Column chart: Distribution history — GTV vs PTN per event
    Source: Distributions — col A (date), col E (GTV amount), col F (PTN amount)
    Placed at Dashboard row 60, col 0 (spanning full width)
    """
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Distribution History — GTV vs PTN (α)",
                    "titleTextFormat": {"bold": True, "fontSize": 13},
                    "basicChart": {
                        "chartType": "COLUMN",
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [
                            {
                                "position": "BOTTOM_AXIS",
                                "title": "Distribution Date",
                            },
                            {
                                "position": "LEFT_AXIS",
                                "title": "Amount (α)",
                            },
                        ],
                        "domains": [
                            {
                                "domain": {
                                    "sourceRange": {
                                        "sources": [source_range(DIST_GID, 1, 1000, 0, 1)]
                                    }
                                }
                            }
                        ],
                        "series": [
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [source_range(DIST_GID, 1, 1000, 4, 5)]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "color": rgb("#e65100"),
                                "dataLabel": {
                                    "type": "DATA",
                                    "textFormat": {"fontSize": 8, "bold": True},
                                    "placement": "INSIDE_END",
                                },
                            },
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [source_range(DIST_GID, 1, 1000, 5, 6)]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "color": rgb("#00897b"),
                                "dataLabel": {
                                    "type": "DATA",
                                    "textFormat": {"fontSize": 8, "bold": True},
                                    "placement": "INSIDE_END",
                                },
                            },
                        ],
                        "headerCount": 1,
                        "stackedType": "NOT_STACKED",
                    },
                    "backgroundColor": rgb("#fffdf8"),
                },
                "position": overlay_position(DASHBOARD_GID, row=60, col=0, width=1260, height=340),
            }
        }
    }


def main():
    print("=" * 60)
    print("SN35 Dashboard — Adding Charts")
    print("=" * 60)

    sh = connect()
    print(f"Connected: {sh.title}\n")

    print("[1] Removing existing charts...")
    delete_existing_charts(sh)

    print("\n[2] Adding charts...")
    requests = [
        chart_daily_earnings(),
        chart_cumulative_balance(),
        chart_distribution_history(),
    ]
    sh.batch_update({"requests": requests})

    print("  Chart 1: Daily Earnings (line)         ✓")
    print("  Chart 2: Cumulative Balance (area)     ✓")
    print("  Chart 3: Distribution History (column) ✓")

    print("\n" + "=" * 60)
    print("Done! Open the Dashboard tab to see the charts.")
    print(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid={DASHBOARD_GID}#gid={DASHBOARD_GID}")
    print("=" * 60)


if __name__ == "__main__":
    main()
