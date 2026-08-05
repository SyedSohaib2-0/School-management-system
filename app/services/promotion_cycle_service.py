"""Academic-session context for normal and overdue full-class promotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.config import SESSION_START_CALENDAR_MONTH, STATUS_ACTIVE

PROMOTION_MODE_NORMAL = "NORMAL"
PROMOTION_MODE_OVERDUE = "OVERDUE"
CYCLE_STATUS_PENDING = "PENDING"
CYCLE_STATUS_COMPLETED = "COMPLETED"
CYCLE_STATUS_SKIPPED_EMPTY = "SKIPPED_EMPTY"


@dataclass(frozen=True)
class PromotionContext:
    source_session_start_year: int
    target_session_start_year: int
    effective_date: date
    executed_at: datetime
    promotion_mode: str

    @property
    def effective_month(self) -> int:
        return int(self.effective_date.month)

    @property
    def effective_year(self) -> int:
        return int(self.effective_date.year)

    @property
    def source_session_label(self) -> str:
        return (
            f"{self.source_session_start_year}-"
            f"{self.source_session_start_year + 1}"
        )

    @property
    def target_session_label(self) -> str:
        return (
            f"{self.target_session_start_year}-"
            f"{self.target_session_start_year + 1}"
        )


def academic_session_start_year(reference: date | datetime) -> int:
    return (
        int(reference.year)
        if int(reference.month) >= int(SESSION_START_CALENDAR_MONTH)
        else int(reference.year) - 1
    )


def latest_due_source_session_start_year(reference: date | datetime) -> int:
    """Return the session ending in March of the reference calendar year.

    During January-March that is the normal promotion cycle; from April onward
    it is the oldest newly-overdue cycle. In both cases the source academic
    session starts in the previous calendar year.
    """
    return int(reference.year) - 1


def _period_session_start_year(month_number: int, calendar_year: int) -> int:
    return (
        int(calendar_year)
        if int(month_number) >= int(SESSION_START_CALENDAR_MONTH)
        else int(calendar_year) - 1
    )


def _latest_completed_source_session(cursor, class_id: int) -> int | None:
    columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(promotion_batches)").fetchall()
    }
    if "source_session_start_year" not in columns:
        row = cursor.execute(
            """
            SELECT MAX(CAST(strftime('%Y', promotion_datetime) AS INTEGER) - 1)
            FROM promotion_batches
            WHERE source_class_id=? AND is_reversed=0
              AND CAST(strftime('%m', promotion_datetime) AS INTEGER) BETWEEN 1 AND 3
            """,
            (int(class_id),),
        ).fetchone()
    else:
        row = cursor.execute(
            """
            SELECT MAX(source_session_start_year)
            FROM promotion_batches
            WHERE source_class_id=? AND is_reversed=0
            """,
            (int(class_id),),
        ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _student_entry_session_candidates(cursor, class_id: int) -> list[int]:
    """Best available lower bound for unresolved cycles in a legacy class."""
    rows = cursor.execute(
        """
        SELECT s.student_id,s.admission_month,s.admission_year,
               (
                 SELECT h.effective_month
                 FROM student_class_history h
                 WHERE h.student_id=s.student_id AND h.new_class_id=s.class_id
                 ORDER BY h.effective_year DESC,h.effective_month DESC,h.history_id DESC
                 LIMIT 1
               ) AS entry_month,
               (
                 SELECT h.effective_year
                 FROM student_class_history h
                 WHERE h.student_id=s.student_id AND h.new_class_id=s.class_id
                 ORDER BY h.effective_year DESC,h.effective_month DESC,h.history_id DESC
                 LIMIT 1
               ) AS entry_year
        FROM students s
        WHERE s.class_id=? AND s.student_status=?
        """,
        (int(class_id), STATUS_ACTIVE),
    ).fetchall()
    candidates: list[int] = []
    for row in rows:
        if row["entry_month"] is not None and row["entry_year"] is not None:
            candidates.append(
                _period_session_start_year(row["entry_month"], row["entry_year"])
            )
        else:
            candidates.append(
                _period_session_start_year(row["admission_month"], row["admission_year"])
            )
    return candidates


def resolve_promotion_context(
    cursor,
    class_id: int,
    *,
    as_of: datetime | None = None,
    require_active_students: bool = True,
) -> PromotionContext:
    """Resolve the oldest outstanding promotion session for one class.

    January-March remains the normal window. Outside that window, only an
    unresolved session can be processed and it is explicitly marked OVERDUE.
    """
    executed_at = as_of or datetime.now()
    class_id = int(class_id)
    class_row = cursor.execute(
        "SELECT id,class_name,is_active FROM classes WHERE id=?", (class_id,)
    ).fetchone()
    if class_row is None:
        raise ValueError("Class not found.")
    if int(class_row["is_active"] or 0) != 1:
        raise ValueError(
            f"{class_row['class_name']} is Inactive and cannot be promoted."
        )

    active_count = int(
        cursor.execute(
            "SELECT COUNT(*) FROM students WHERE class_id=? AND student_status=?",
            (class_id, STATUS_ACTIVE),
        ).fetchone()[0]
    )
    if require_active_students and active_count <= 0:
        raise ValueError(
            f"{class_row['class_name']} has no Active students to promote."
        )

    latest_due = latest_due_source_session_start_year(executed_at)
    latest_completed = _latest_completed_source_session(cursor, class_id)

    if latest_completed is not None:
        source_year = int(latest_completed) + 1
    else:
        entry_candidates = _student_entry_session_candidates(cursor, class_id)
        source_year = min(entry_candidates) if entry_candidates else latest_due

    # Never invent a future cycle. A class already processed through the latest
    # due session must wait for the next January-March window.
    if source_year > latest_due:
        raise ValueError(
            f"{class_row['class_name']} has no overdue promotion cycle. "
            "Its next normal promotion will be available during January-March."
        )

    target_year = source_year + 1
    mode = (
        PROMOTION_MODE_NORMAL
        if int(executed_at.month) in (1, 2, 3) and source_year == latest_due
        else PROMOTION_MODE_OVERDUE
    )
    return PromotionContext(
        source_session_start_year=source_year,
        target_session_start_year=target_year,
        effective_date=date(target_year, SESSION_START_CALENDAR_MONTH, 1),
        executed_at=executed_at,
        promotion_mode=mode,
    )


def ensure_cycle_row(cursor, class_id: int, context: PromotionContext, *, actor: str) -> int:
    now = context.executed_at.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO promotion_cycles(
            class_id,source_session_start_year,target_session_start_year,status,
            completed_batch_id,created_at,updated_at,updated_by
        ) VALUES(?,?,?,'PENDING',NULL,?,?,?)
        ON CONFLICT(class_id,source_session_start_year) DO UPDATE SET
            target_session_start_year=excluded.target_session_start_year,
            updated_at=excluded.updated_at,
            updated_by=excluded.updated_by
        """,
        (
            int(class_id),
            int(context.source_session_start_year),
            int(context.target_session_start_year),
            now,
            now,
            str(actor or "admin"),
        ),
    )
    row = cursor.execute(
        """
        SELECT cycle_id,status,completed_batch_id
        FROM promotion_cycles
        WHERE class_id=? AND source_session_start_year=?
        """,
        (int(class_id), int(context.source_session_start_year)),
    ).fetchone()
    if row is None:
        raise RuntimeError("Promotion cycle could not be created.")
    if str(row["status"]) == CYCLE_STATUS_COMPLETED:
        raise ValueError(
            "This class has already been promoted for the selected source session."
        )
    return int(row["cycle_id"])


def mark_cycle_completed(
    cursor,
    cycle_id: int,
    batch_id: int,
    *,
    actor: str,
    completed_at: datetime,
) -> None:
    stamp = completed_at.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        UPDATE promotion_cycles
        SET status='COMPLETED',completed_batch_id=?,completed_at=?,updated_at=?,updated_by=?
        WHERE cycle_id=? AND status='PENDING' AND completed_batch_id IS NULL
        """,
        (int(batch_id), stamp, stamp, str(actor or "admin"), int(cycle_id)),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(
            "Promotion cycle changed during execution. The complete promotion was rolled back."
        )


def class_cycle_completed(cursor, class_id: int, source_session_start_year: int) -> bool:
    row = cursor.execute(
        """
        SELECT 1
        FROM promotion_batches
        WHERE source_class_id=? AND source_session_start_year=? AND is_reversed=0
        LIMIT 1
        """,
        (int(class_id), int(source_session_start_year)),
    ).fetchone()
    return row is not None
