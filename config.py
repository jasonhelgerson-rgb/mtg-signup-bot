"""Environment configuration and timezone-aware scheduling."""

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


def _integer(name: str, default: str, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer.") from None
    if value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be between 1 and {maximum}." if maximum else f"Set {name} to a positive Discord ID.")
    return value


@dataclass(frozen=True)
class Settings:
    channel_id: int
    token: str = field(default="", repr=False)
    guild_id: int | None = None
    database_path: Path = Path("./data/signups.db")
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Los_Angeles"))
    lock_time: time = time(15, 0)
    pod_size: int = 4
    days_ahead: int = 7

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path.cwd() / ".env", override=False)
        clock = os.getenv("LOCK_TIME", "15:00")
        if not re.fullmatch(r"\d{2}:\d{2}", clock):
            raise ValueError("LOCK_TIME must use 24-hour HH:MM, for example 15:00.")
        try:
            lock_time = time.fromisoformat(clock)
        except ValueError:
            raise ValueError("LOCK_TIME must be a valid 24-hour HH:MM time.") from None
        try:
            zone = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))
        except ZoneInfoNotFoundError:
            raise ValueError("TIMEZONE must be an IANA timezone such as America/Los_Angeles.") from None
        return cls(
            token=os.getenv("DISCORD_TOKEN", "").strip(),
            channel_id=_integer("SIGNUP_CHANNEL_ID", "0"),
            guild_id=_integer("GUILD_ID", "0") if os.getenv("GUILD_ID", "").strip() else None,
            database_path=Path(os.getenv("DATABASE_PATH", "./data/signups.db")).expanduser(),
            timezone=zone,
            lock_time=lock_time,
            pod_size=_integer("POD_SIZE", "4", 20),
            days_ahead=_integer("DAYS_AHEAD", "7", 31),
        )

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def lock_at(self, day: date) -> datetime:
        local = datetime.combine(day, self.lock_time, self.timezone)
        # Normalize a custom lock time in a spring-forward gap to the later valid time.
        return local.astimezone(timezone.utc).astimezone(self.timezone)

    def upcoming(self, now: datetime | None = None) -> list[date]:
        today = (now or self.now()).astimezone(self.timezone).date()
        return [today + timedelta(days=offset) for offset in range(self.days_ahead)]


def parse_day(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("That is not a valid calendar date. Use YYYY-MM-DD.") from None
