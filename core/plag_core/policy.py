"""Permission levels and the policy gate every tool call passes through."""

from enum import IntEnum


class Level(IntEnum):
    READ = 0       # L0: inspect, search, read
    LOW = 1        # L1: open apps/sites, search in browser
    EXTERNAL = 2   # L2: send messages, post (needs confirmation)
    SENSITIVE = 3  # L3: delete, install, settings (confirmation + Windows Hello)
    FORBIDDEN = 4  # L4: payments, passwords, disabling security (never)


class Halted(Exception):
    """The kill switch is engaged."""


class NeedsApproval(Exception):
    """Action is valid but must be confirmed by the user (approval flow lands with L2 tools)."""


class Forbidden(Exception):
    """Action is never allowed."""


class Policy:
    def __init__(self) -> None:
        self.halted = False

    def check(self, level: Level, *, approved: bool = False) -> None:
        """`approved` is only ever set by the approvals flow, from the user's click or spoken "yes"."""
        if self.halted:
            raise Halted()
        if level >= Level.FORBIDDEN:
            raise Forbidden()
        if level >= Level.SENSITIVE:
            raise NeedsApproval()  # L3 needs Windows Hello as well; not built yet
        if level == Level.EXTERNAL and not approved:
            raise NeedsApproval()


policy = Policy()
