"""Small session-state contract shared by shell-owned popovers and pages."""

from __future__ import annotations

from typing import Any, Final, Protocol

SHELL_POPOVER_EPOCH_STATE_KEY: Final = "shell_popover_epoch"


class ShellStateStore(Protocol):
    """Structural subset shared by live and test Streamlit session state."""

    def get(self, key: str | int, default: Any, /) -> Any: ...

    def __setitem__(self, key: str, value: Any) -> None: ...


def bump_shell_popover_epoch(state: ShellStateStore) -> int:
    """Invalidate browser-owned popover portals after durable visible changes."""

    raw_epoch = state.get(SHELL_POPOVER_EPOCH_STATE_KEY, 0)
    epoch = raw_epoch if isinstance(raw_epoch, int) else 0
    revised = epoch + 1
    state[SHELL_POPOVER_EPOCH_STATE_KEY] = revised
    return revised


__all__ = [
    "SHELL_POPOVER_EPOCH_STATE_KEY",
    "ShellStateStore",
    "bump_shell_popover_epoch",
]
