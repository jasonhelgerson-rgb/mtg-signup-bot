"""Pure signup rules. Tuple order is the signup order, including flex priority."""

from dataclasses import dataclass, replace
from typing import Mapping


@dataclass(frozen=True)
class Night:
    seats: tuple[int, ...] = ()
    backups: tuple[int, ...] = ()
    flex: tuple[int, ...] = ()
    locked: bool = False


@dataclass(frozen=True)
class Change:
    nights: dict[str, Night]
    confirmation: str
    promoted: tuple[int, ...] = ()


def join(nights: Mapping[str, Night], day: str, user: int, pod_size: int) -> Change:
    night = nights[day]
    if night.locked:
        raise ValueError("This night is locked. Leave is still available.")
    if user in night.seats:
        return Change(dict(nights), f"You already have seat #{night.seats.index(user) + 1}.")
    if user in night.backups:
        return Change(dict(nights), f"You are already backup #{night.backups.index(user) + 1}.")
    night = replace(night, flex=tuple(player for player in night.flex if player != user))
    if len(night.seats) < pod_size:
        night = replace(night, seats=night.seats + (user,))
        confirmation = f"You have seat #{len(night.seats)} for {day}."
    else:
        night = replace(night, backups=night.backups + (user,))
        confirmation = f"The pod is full. You are backup #{len(night.backups)} for {day}."
    return Change({**nights, day: night}, confirmation)


def flex(nights: Mapping[str, Night], day: str, user: int) -> Change:
    night = nights[day]
    if night.locked:
        raise ValueError("This night is locked. Leave is still available.")
    if user in night.seats or user in night.backups:
        raise ValueError("You already joined this night. Leave first to switch to Flex.")
    if user in night.flex:
        return Change(dict(nights), "This night is already one of your flex options.")
    night = replace(night, flex=night.flex + (user,))
    return Change({**nights, day: night}, f"Added {day} to your flex options; no seat is held.")


def _clear_other_flex(nights: dict[str, Night], day: str, user: int) -> None:
    for other_day, night in list(nights.items()):
        if other_day != day and user in night.flex:
            nights[other_day] = replace(night, flex=tuple(p for p in night.flex if p != user))


def _fill(nights: dict[str, Night], day: str, pod_size: int, *, include_flex: bool) -> tuple[int, ...]:
    promoted = []
    night = nights[day]
    while len(night.seats) < pod_size:
        if night.backups:
            user, *remaining = night.backups
            night = replace(night, backups=tuple(remaining))
        elif include_flex and night.flex:
            user, *remaining = night.flex
            night = replace(night, flex=tuple(remaining))
            _clear_other_flex(nights, day, user)
        else:
            break
        night = replace(night, seats=night.seats + (user,))
        promoted.append(user)
    nights[day] = night
    return tuple(promoted)


def leave(nights: Mapping[str, Night], day: str, user: int, pod_size: int) -> Change:
    night = nights[day]
    was_seated = user in night.seats
    present = was_seated or user in night.backups or user in night.flex
    changed = dict(nights)
    changed[day] = replace(
        night,
        seats=tuple(p for p in night.seats if p != user),
        backups=tuple(p for p in night.backups if p != user),
        flex=tuple(p for p in night.flex if p != user),
    )
    promoted = _fill(changed, day, pod_size, include_flex=night.locked) if was_seated else ()
    return Change(changed, f"Removed you from {day}." if present else "You were not signed up for this night.", promoted)


def lock(nights: Mapping[str, Night], day: str, pod_size: int) -> Change:
    if nights[day].locked:
        return Change(dict(nights), "This night is already locked.")
    changed = dict(nights)
    changed[day] = replace(changed[day], locked=True)
    promoted = _fill(changed, day, pod_size, include_flex=True)
    return Change(changed, f"Locked {day}.", promoted)


def unlock(nights: Mapping[str, Night], day: str) -> Change:
    return Change({**nights, day: replace(nights[day], locked=False)}, f"Reopened {day}.")
