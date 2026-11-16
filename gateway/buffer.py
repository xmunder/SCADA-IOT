"""Buffer persistente de salida (store-and-forward) respaldado en SQLite.

Cada fila es un mensaje ya serializado. Una fila solo se elimina cuando la nube
confirma la publicación (PUBACK), por lo que un corte de red o un reinicio del
proceso no provoca pérdida. El resultado es entrega al menos una vez.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class StoreAndForwardBuffer:
    def __init__(self, path: str | Path) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: cada sentencia es su propia transacción (autocommit).
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ms INTEGER NOT NULL,
                payload BLOB NOT NULL
            )
            """
        )

    def append(self, payload: bytes, created_ms: int) -> int:
        cursor = self._conn.execute(
            "INSERT INTO outbox (created_ms, payload) VALUES (?, ?)",
            (created_ms, payload),
        )
        return int(cursor.lastrowid)

    def peek(self, limit: int) -> list[tuple[int, bytes]]:
        """Devuelve hasta `limit` mensajes pendientes, en orden de llegada."""
        rows = self._conn.execute("SELECT id, payload FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [(int(row_id), bytes(payload)) for row_id, payload in rows]

    def ack(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._conn.execute(f"DELETE FROM outbox WHERE id IN ({placeholders})", ids)

    def pending(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
