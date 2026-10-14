"""SQLite persistence; cross-night changes and notices commit together."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .allocation import Night


@dataclass(frozen=True)
class Record:
    day: str
    night: Night
    message_id: int | None
    created_at: datetime
    auto_lock_done: bool


@dataclass(frozen=True)
class Notice:
    content: str
    user_ids: tuple[int, ...] = ()


def _decode(row: sqlite3.Row) -> Record:
    state = json.loads(row["state"])
    return Record(
        day=row["day"],
        night=Night(tuple(state["seats"]), tuple(state["backups"]), tuple(state["flex"]), state["locked"]),
        message_id=row["message_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        auto_lock_done=bool(row["auto_lock_done"]),
    )


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS nights (
                day TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                message_id INTEGER,
                created_at TEXT NOT NULL,
                auto_lock_done INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                user_ids TEXT NOT NULL
            );
        """)

    def ensure(self, day: str) -> Record:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO nights(day, state, created_at) VALUES (?, ?, ?)",
                (day, json.dumps(asdict(Night())), datetime.now(timezone.utc).isoformat()),
            )
        return self.get(day)

    def get(self, day: str) -> Record:
        row = self.connection.execute("SELECT * FROM nights WHERE day = ?", (day,)).fetchone()
        if row is None:
            raise ValueError("No signup exists for that date. Ask an admin to use /signup post.")
        return _decode(row)

    def records(self) -> list[Record]:
        return [_decode(row) for row in self.connection.execute("SELECT * FROM nights ORDER BY day")]

    def states(self) -> dict[str, Night]:
        return {record.day: record.night for record in self.records()}

    def commit(self, states: Mapping[str, Night], notices: Sequence[Notice] = (), *, auto_done: Sequence[str] = ()) -> None:
        with self.connection:
            for day, night in states.items():
                self.connection.execute("UPDATE nights SET state = ? WHERE day = ?", (json.dumps(asdict(night)), day))
            for day in auto_done:
                self.connection.execute("UPDATE nights SET auto_lock_done = 1 WHERE day = ?", (day,))
            for notice in notices:
                self.connection.execute(
                    "INSERT INTO notices(content, user_ids) VALUES (?, ?)",
                    (notice.content, json.dumps(notice.user_ids)),
                )

    def set_message(self, day: str, message_id: int | None) -> None:
        with self.connection:
            self.connection.execute("UPDATE nights SET message_id = ? WHERE day = ?", (message_id, day))

    def pending_notices(self) -> list[tuple[int, Notice]]:
        return [(row["id"], Notice(row["content"], tuple(json.loads(row["user_ids"])))) for row in
                self.connection.execute("SELECT * FROM notices ORDER BY id")]

    def acknowledge(self, notice_id: int) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM notices WHERE id = ?", (notice_id,))

    def close(self) -> None:
        self.connection.close()
