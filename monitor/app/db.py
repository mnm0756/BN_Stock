from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "settings_version": 4,
    "provider_mode": "auto",
    "refresh_seconds": 15,
    "total_capital": 1000.0,
    "perp_allocation": 0.5,
    "leverage": 1.0,
    "holding_days": 30,
    "min_annualized": 0.0,
    "execution_mode": "maker",
    "spot_fee_rate": 0.001,
    "spot_min_fee": 0.35,
    "perp_maker_fee": 0.0,
    "perp_taker_fee": 0.0004,
    "binance_maker_fee": 0.00005,
    "binance_taker_fee": 0.0004,
    "okx_maker_fee": 0.0003,
    "okx_taker_fee": 0.0009,
    "slippage_bps": 2.0,
    "extra_cost_bps": 0.0,
    "extra_fixed_fee": 0.0,
    "alert_annualized": 0.5,
    "watch_symbols": ["CXMTUSDT"],
}

LEGACY_STOCK_SYMBOLS = {
    "MUUSDT",
    "NOKUSDT",
    "ASTSUSDT",
    "NVOUSDT",
    "MSTRUSDT",
    "COINUSDT",
    "NVDAUSDT",
    "TSLAUSDT",
    "AAPLUSDT",
    "AMDUSDT",
    "PLTRUSDT",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS positions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
                "payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            row = conn.execute("SELECT payload FROM settings WHERE id = 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO settings (id, payload) VALUES (1, ?)",
                    (json.dumps(DEFAULT_SETTINGS),),
                )
            conn.commit()

    def get_settings(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM settings WHERE id = 1").fetchone()
        saved = json.loads(row["payload"]) if row else {}
        merged = {**DEFAULT_SETTINGS, **saved}
        watch_symbols = set(merged.get("watch_symbols") or [])
        if saved.get("settings_version", 1) < 4:
            merged["settings_version"] = DEFAULT_SETTINGS["settings_version"]
            merged["watch_symbols"] = DEFAULT_SETTINGS["watch_symbols"]
            for key in (
                "binance_maker_fee",
                "binance_taker_fee",
                "okx_maker_fee",
                "okx_taker_fee",
                "min_annualized",
                "alert_annualized",
            ):
                merged[key] = DEFAULT_SETTINGS[key]
        if (saved.get("settings_version", 1) < 3 and watch_symbols & LEGACY_STOCK_SYMBOLS):
            merged["watch_symbols"] = DEFAULT_SETTINGS["watch_symbols"]
        return merged

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = {**DEFAULT_SETTINGS, **settings, "settings_version": DEFAULT_SETTINGS["settings_version"]}
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE settings SET payload = ? WHERE id = 1",
                (json.dumps(merged),),
            )
            conn.commit()
        return merged

    def list_positions(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                **json.loads(row["payload"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def create_position(self, payload: dict[str, Any], now: str) -> dict[str, Any]:
        symbol = str(payload["symbol"]).upper().strip()
        payload = {**payload, "symbol": symbol}
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO positions (symbol, payload, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (symbol, json.dumps(payload), now, now),
            )
            conn.commit()
            position_id = cursor.lastrowid
        return {"id": position_id, **payload, "created_at": now, "updated_at": now}

    def update_position(self, position_id: int, payload: dict[str, Any], now: str) -> dict[str, Any] | None:
        symbol = str(payload["symbol"]).upper().strip()
        payload = {**payload, "symbol": symbol}
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT created_at FROM positions WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE positions SET symbol = ?, payload = ?, updated_at = ? WHERE id = ?",
                (symbol, json.dumps(payload), now, position_id),
            )
            conn.commit()
        return {"id": position_id, **payload, "created_at": row["created_at"], "updated_at": now}

    def delete_position(self, position_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            conn.commit()
        return cursor.rowcount > 0
