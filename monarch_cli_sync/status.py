from enum import Enum
from dataclasses import dataclass, field


class SyncStatus(str, Enum):
    OK = "ok"
    NO_CHANGES = "no_changes"
    PARTIAL = "partial"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


EXIT_CODES: dict[SyncStatus, int] = {
    SyncStatus.OK: 0,
    SyncStatus.NO_CHANGES: 0,
    SyncStatus.PARTIAL: 1,
    SyncStatus.AUTH_REQUIRED: 2,
    SyncStatus.RATE_LIMITED: 3,
    SyncStatus.ERROR: 4,
}


@dataclass
class SyncResult:
    status: SyncStatus
    orders_inspected: int = 0
    transactions_fetched: int = 0
    matched: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def summary_line(self) -> str:
        return (
            f"monarch-cli-sync: {self.status.value} | "
            f"matched={self.matched} updated={self.updated} "
            f"skipped={self.skipped} errors={len(self.errors)}"
        )
