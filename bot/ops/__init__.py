"""Human operations for the paper live loop.

Nothing in this package changes the strategy, accounting engine or broker.  It
wraps the existing ``bot.live`` state/engine so an operator can inspect, back up,
restore and alert on the same files the live loop uses.
"""

from .alerts import (  # noqa: F401
    AlertManager,
    AlertPayload,
    Delivery,
    build_manager,
)
from .backup import (  # noqa: F401
    BackupInfo,
    BackupVerification,
    RestoreResult,
    create_backup,
    restore_backup,
    verify_backup,
)
from .doctor import DoctorReport, run_doctor  # noqa: F401
from .status import collect_status, forward_gate, missed_session_payload  # noqa: F401

__all__ = [
    "AlertManager",
    "AlertPayload",
    "BackupInfo",
    "BackupVerification",
    "Delivery",
    "DoctorReport",
    "RestoreResult",
    "build_manager",
    "collect_status",
    "create_backup",
    "forward_gate",
    "missed_session_payload",
    "restore_backup",
    "run_doctor",
    "verify_backup",
]
