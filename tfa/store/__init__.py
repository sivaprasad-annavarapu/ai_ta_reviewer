"""Persistence.

The analysis is a function of the filings; the register is the work people do
about it. This package keeps the second, and keeps enough of the first to prove
what the register was decided on.

Absent a database, every function here reports itself unavailable and the
analysis proceeds unchanged. That is deliberate: a system that cannot record
should say so on the screen, not stop, and not pretend.
"""
from .connection import Health, StoreError, health, migrate, redacted  # noqa: F401
from .matters import fingerprint, matter_key                            # noqa: F401
from .repository import (                                              # noqa: F401
    add_disposition, client_history, matter_detail, record_run, register,
    update_enquiry,
)
