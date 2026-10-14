"""Refusal accounting and quarantine staging for arrival-day ingestion.

The delivery of 2026-10-13 is 50 000 profiles and ~700 000 records in a shape nobody has seen. Two
failure modes are worse than an error:

* an error that names one row and nothing else, so an operator at 06:00 cannot tell whether the delivery
  is one bad row or fifty thousand;
* a load that rolls the whole corpus back because of one bad row.

This module is the accounting both fixes share. `RefusalLog` counts refusals by a stable machine code,
keeps the reason text, and keeps up to `MAX_EXAMPLES` example locators per reason with the offending
values -- which is exactly what the reconciliation report prints after a refusal. `QuarantineStore` is the
same structure with a JSONL sink, so a quarantine-mode load can keep every refused row (locator, raw row,
reason) and load the remainder.

Nothing here interprets a row: a `Refusal` is a fact about one row, and staging it is not a repair.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Enough for a human to recognise the shape of a repeated refusal without turning the report into the
# delivery. The COUNT is unbounded; only the examples are capped.
MAX_EXAMPLES = 5


@dataclass(frozen=True)
class Refusal:
    """One row (or one file-level failure) this load would not accept.

    `code` is the stable machine key: two rows refused for the same reason share it even when their
    messages differ (the message names the offending value). `raw` is kept only for quarantine staging;
    a dry-run refusal keeps the offending values, never the whole row.
    """

    code: str
    reason: str
    locator: str
    values: dict[str, Any] = field(default_factory=dict)
    record_id: str | None = None
    raw: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "locator": self.locator,
                "record_id": self.record_id, "values": self.values}

    @classmethod
    def from_error(cls, error: Exception, *, locator: str, record_id: str | None = None,
                   raw: dict[str, Any] | None = None) -> "Refusal":
        """Build a refusal from an exception that carries its own code/values when it knows them."""
        code = getattr(error, "code", None) or "shape"
        values = getattr(error, "values", None) or {}
        own_record_id = getattr(error, "record_id", None) or record_id
        return cls(code=code, reason=str(error), locator=locator, values=dict(values),
                   record_id=own_record_id, raw=raw)


class RefusalLog:
    """Counts refusals by reason and remembers bounded examples of each.

    With a `sink`, every refusal is also appended to a JSONL file as it happens, so a quarantine run has
    the rows on disk even if the process dies mid-load. The log itself is in-memory and small: one entry
    per distinct reason plus the examples.
    """

    def __init__(self, sink: Path | None = None):
        self._counts: OrderedDict[str, int] = OrderedDict()
        self._reasons: dict[str, str] = {}
        self._examples: dict[str, list[dict[str, Any]]] = {}
        self._total = 0
        self.sink = sink
        self._stream = None
        if sink is not None:
            sink.parent.mkdir(parents=True, exist_ok=True)
            self._stream = sink.open("a", encoding="utf-8")

    def record(self, refusal: Refusal) -> None:
        self._total += 1
        self._counts[refusal.code] = self._counts.get(refusal.code, 0) + 1
        self._reasons.setdefault(refusal.code, refusal.reason)
        examples = self._examples.setdefault(refusal.code, [])
        if len(examples) < MAX_EXAMPLES:
            examples.append(refusal.as_dict())
        if self._stream is not None:
            self._stream.write(json.dumps({
                "locator": refusal.locator, "reason_code": refusal.code,
                "reason": refusal.reason, "record_id": refusal.record_id,
                "values": refusal.values, "raw": refusal.raw,
            }, ensure_ascii=False) + "\n")
            self._stream.flush()

    def record_error(self, error: Exception, *, locator: str, record_id: str | None = None,
                     raw: dict[str, Any] | None = None) -> None:
        self.record(Refusal.from_error(error, locator=locator, record_id=record_id, raw=raw))

    def __len__(self) -> int:
        return self._total

    @property
    def total(self) -> int:
        return self._total

    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def as_dict(self) -> dict[str, Any]:
        """The report section a human reads: counts, and for each reason its examples."""
        return {
            "rows_refused": self._total,
            "reasons": [
                {"code": code, "count": count, "reason": self._reasons[code],
                 "examples": self._examples.get(code, [])}
                for code, count in self._counts.items()
            ],
        }

    def message(self, limit: int = 8) -> str:
        """One line per distinct reason, for the exception a shell prints on a refusal."""
        parts = [f"{code} x{count}: {self._reasons[code]}"
                 for code, count in list(self._counts.items())[:limit]]
        extra = len(self._counts) - limit
        if extra > 0:
            parts.append(f"(+{extra} more reasons)")
        return f"{self._total} row(s) refused; " + "; ".join(parts)

    def summary_text(self) -> str:
        lines = [f"  {code:24s} {count:6d}  {self._reasons[code]}"
                 for code, count in self._counts.items()]
        return "\n".join(lines)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "RefusalLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def write_summary(path: Path, *, loaded: int, quarantined: int, log: RefusalLog,
                  extra: dict[str, Any] | None = None) -> Path:
    """Write the machine-readable quarantine summary beside the quarantine JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "delivery-quarantine-summary",
        "loaded": loaded,
        "quarantined": quarantined,
        "reasons": log.as_dict()["reasons"],
    }
    if extra:
        document.update(extra)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
