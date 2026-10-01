"""Registry mapping check ids to check instances."""

from __future__ import annotations

from collections.abc import Iterable

from forge_doctor.plugins.protocol import Check


class CheckRegistry:
    """Holds checks; supports category filtering and id-based ignores."""

    def __init__(self) -> None:
        self._checks: dict[str, Check] = {}

    def register(self, check: Check) -> Check:
        existing = self._checks.get(check.id)
        if existing is not None and existing is not check:
            msg = f"Duplicate check id: {check.id}"
            raise ValueError(msg)
        self._checks[check.id] = check
        return check

    def register_all(self, checks: Iterable[Check]) -> None:
        for check in checks:
            self.register(check)

    def get(self, check_id: str) -> Check | None:
        return self._checks.get(check_id)

    def categories(self) -> list[str]:
        return sorted({check.category for check in self._checks.values()})

    def all(self) -> list[Check]:
        return sorted(self._checks.values(), key=lambda check: check.id)

    def select(
        self,
        categories: Iterable[str] = (),
        ignore: Iterable[str] = (),
    ) -> list[Check]:
        wanted = set(categories)
        ignored = set(ignore)
        return [
            check
            for check in self.all()
            if check.id not in ignored and (not wanted or check.category in wanted)
        ]
