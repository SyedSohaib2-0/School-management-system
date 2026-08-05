"""Coming-session admission lifecycle for schema V22.

Pending admissions are registered during January-March for the academic
session beginning on 1 April. They keep their original admission date, carry a
private negative roll sentinel, generate no fee before activation, and are not
part of the ending session's promotion.
"""
from __future__ import annotations

from datetime import date, datetime
import re

from app.config import STATUS_ACTIVE, STATUS_COMING_SESSION
from app.database import get_connection
from app.normalization import normalize_person_name
from app.services import student_service

COMING_STATUS_PENDING = "PENDING"
COMING_STATUS_ACTIVATED = "ACTIVATED"
COMING_ADMISSION_MONTHS = frozenset({1, 2, 3})


def target_session_start_year(admission_year: int, admission_month: int) -> int:
    year = int(admission_year)
    month = int(admission_month)
    if month not in COMING_ADMISSION_MONTHS:
        raise ValueError(
            "Admission for Coming Session is available only for January, "
            "February and March admission dates."
        )
    return year


def session_start_date(target_year: int) -> date:
    return date(int(target_year), 4, 1)


def is_coming_session_option_available(
    admission_year: int,
    admission_month: int,
    admission_day: int,
    *,
    today: date | None = None,
) -> bool:
    try:
        admitted_on = date(
            int(admission_year), int(admission_month), int(admission_day)
        )
    except (TypeError, ValueError):
        return False
    if admitted_on.month not in COMING_ADMISSION_MONTHS:
        return False
    reference = today or date.today()
    return reference < session_start_date(admitted_on.year)


def pending_roll_sentinel(student_id: int) -> int:
    return -abs(int(student_id))


def _tracking_sort_value(raw_value) -> tuple[int, str]:
    text = str(raw_value or "").strip().upper()
    match = re.search(r"(\d+)$", text)
    return (int(match.group(1)) if match else 10**18, text)


def _class_reserved_capacity_with_cursor(
    cursor,
    class_id: int,
    *,
    exclude_student_id: int | None = None,
) -> dict:
    join_condition = "s.class_id=c.id"
    params: list[object] = []
    if exclude_student_id is not None:
        join_condition += " AND s.student_id<>?"
        params.append(int(exclude_student_id))
    query = f"""
        SELECT c.id,c.class_name,c.class_level,c.section,c.student_capacity,c.is_active,
               COUNT(CASE WHEN s.student_status='Active' OR (s.student_status='Coming Session' AND s.coming_session_status='PENDING') THEN 1 END)
                   AS reserved_strength
        FROM classes AS c
        LEFT JOIN students AS s ON {join_condition}
        WHERE c.id=?
        GROUP BY c.id
    """
    params.append(int(class_id))
    row = cursor.execute(query, tuple(params)).fetchone()
    if row is None:
        raise ValueError("The selected class could not be found.")
    if int(row["is_active"] or 0) != 1:
        raise ValueError(
            f"{row['class_name']} is Inactive and cannot receive a coming-session admission."
        )
    return dict(row)


def validate_reserved_capacity(
    cursor,
    class_id: int,
    *,
    exclude_student_id: int | None = None,
) -> dict:
    details = _class_reserved_capacity_with_cursor(
        cursor, int(class_id), exclude_student_id=exclude_student_id
    )
    if int(details["reserved_strength"] or 0) >= int(details["student_capacity"]):
        raise ValueError(
            f"{details['class_name']} has no coming-session seat available. "
            f"Capacity: {details['student_capacity']}; current and reserved "
            f"students: {details['reserved_strength']}."
        )
    return details


def _promotion_completed_for_class(cursor, class_id: int, target_year: int) -> bool:
    columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(promotion_batches)").fetchall()
    }
    if "target_session_start_year" in columns:
        row = cursor.execute(
            """
            SELECT 1 FROM promotion_batches
            WHERE source_class_id=? AND target_session_start_year=?
              AND is_reversed=0 LIMIT 1
            """,
            (int(class_id), int(target_year)),
        ).fetchone()
        return row is not None
    ending_session_start = f"{int(target_year) - 1:04d}-04-01"
    ending_session_end = f"{int(target_year):04d}-03-31"
    row = cursor.execute(
        """
        SELECT 1 FROM promotion_batches
        WHERE source_class_id=? AND is_reversed=0
          AND date(promotion_datetime) BETWEEN date(?) AND date(?)
        LIMIT 1
        """,
        (int(class_id), ending_session_start, ending_session_end),
    ).fetchone()
    return row is not None


def _assert_no_irreversible_activity(cursor, student_id: int) -> None:
    payment = cursor.execute(
        "SELECT 1 FROM fee_payments WHERE student_id=? LIMIT 1", (int(student_id),)
    ).fetchone()
    if payment is not None:
        raise ValueError(
            "This admission type cannot be changed because a payment record exists."
        )
    paid_or_waived = cursor.execute(
        """
        SELECT 1 FROM student_fee_records
        WHERE student_id=? AND status IN ('Paid','Waived') LIMIT 1
        """,
        (int(student_id),),
    ).fetchone()
    if paid_or_waived is not None:
        raise ValueError(
            "This admission type cannot be changed because a Paid or Waived fee exists."
        )
    promotion = cursor.execute(
        "SELECT 1 FROM promotion_history WHERE student_id=? AND is_reversed=0 LIMIT 1",
        (int(student_id),),
    ).fetchone()
    if promotion is not None:
        raise ValueError(
            "This admission type cannot be changed because promotion history exists."
        )
    graduation = cursor.execute(
        "SELECT 1 FROM graduation_records WHERE student_id=? AND is_reversed=0 LIMIT 1",
        (int(student_id),),
    ).fetchone()
    if graduation is not None:
        raise ValueError(
            "This admission type cannot be changed because graduation history exists."
        )


def _delete_pre_session_unpaid_fees(cursor, student_id: int, target_year: int) -> None:
    cursor.execute(
        """
        DELETE FROM student_fee_records
        WHERE student_id=? AND fee_type='Monthly' AND status='Unpaid'
          AND year IS NOT NULL AND month_number IS NOT NULL
          AND (year*12+month_number) < (?*12+4)
        """,
        (int(student_id), int(target_year)),
    )


def _insert_status_history(
    cursor,
    *,
    student_id: int,
    old_status: str,
    new_status: str,
    event_type: str,
    effective_datetime: str,
    reason: str,
    changed_by: str,
    class_id: int,
) -> None:
    cursor.execute(
        """
        INSERT INTO student_status_history(
            student_id,old_status,new_status,event_type,effective_datetime,
            reason,changed_by,created_at,related_class_id,related_event_id
        ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
        """,
        (
            int(student_id), str(old_status), str(new_status), str(event_type),
            str(effective_datetime), str(reason), str(changed_by or "admin"),
            str(effective_datetime), int(class_id),
        ),
    )


def save_pending_admission_edit(
    *,
    student_id: int,
    desired_coming_session: bool,
    class_id: int,
    student_name: str,
    gender: str,
    father_name: str,
    father_cnic: str,
    father_contact_number: str,
    whatsapp_number: str | None,
    current_address: str | None,
    permanent_address: str | None,
    school_leaving_certificate_submitted: bool,
    form_b_submitted: bool,
    birth_certificate_submitted: bool,
    date_of_birth: str,
    updated_by: str = "admin",
) -> dict:
    """Atomically edit/convert an Active or pending coming-session student."""
    connection = get_connection()
    cursor = connection.cursor()
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = cursor.execute(
            "SELECT * FROM students WHERE student_id=?", (int(student_id),)
        ).fetchone()
        if row is None:
            raise ValueError("Student not found.")
        existing_status = str(row["student_status"] or "").strip()
        if existing_status not in {STATUS_ACTIVE, STATUS_COMING_SESSION}:
            raise ValueError(
                "Only an Active or pending coming-session student can use this workflow."
            )
        target_year = target_session_start_year(row["admission_year"], row["admission_month"])
        session_has_started = now_dt.date() >= session_start_date(target_year)
        if session_has_started and not (
            existing_status == STATUS_COMING_SESSION
            and desired_coming_session
        ):
            raise ValueError(
                "The coming academic session has already started. The admission "
                "type can no longer be changed."
            )

        normalized_name = student_service.validate_person_name_length(student_name)
        normalized_father = student_service.validate_person_name_length(father_name)
        normalized_cnic = student_service.normalize_father_cnic(father_cnic)
        normalized_gender = str(gender or "").strip().title()
        if not student_service.validate_gender(normalized_gender):
            raise ValueError("Gender must be Male or Female.")
        duplicate = student_service._find_existing_student_identity_with_cursor(
            cursor,
            student_name=normalized_name,
            father_name=normalized_father,
            father_cnic=normalized_cnic,
            exclude_student_id=int(student_id),
        )
        if duplicate is not None:
            raise ValueError(
                "Another student record uses the same Student Name, Father Name and Father CNIC."
            )
        normalized_dob = student_service.validate_date_of_birth_before_admission(
            date_of_birth, datetime.fromisoformat(str(row["admission_date"]))
        )
        requested_class_id = int(class_id)
        student_service._validate_class_4_plus_gender_assignment_with_cursor(
            cursor,
            class_id=requested_class_id,
            gender=normalized_gender,
            student_status=(STATUS_COMING_SESSION if desired_coming_session else STATUS_ACTIVE),
            existing_class_id=int(row["class_id"]),
            existing_gender=row["gender"],
            allow_legacy_blank_route=False,
        )

        if desired_coming_session:
            validate_reserved_capacity(
                cursor, requested_class_id, exclude_student_id=int(student_id)
            )
            if existing_status == STATUS_ACTIVE:
                _assert_no_irreversible_activity(cursor, int(student_id))
                if _promotion_completed_for_class(cursor, int(row["class_id"]), target_year):
                    raise ValueError(
                        "This class has already completed promotion for the ending session."
                    )
                _delete_pre_session_unpaid_fees(cursor, int(student_id), target_year)
                new_roll = pending_roll_sentinel(int(student_id))
                _insert_status_history(
                    cursor,
                    student_id=int(student_id),
                    old_status=STATUS_ACTIVE,
                    new_status=STATUS_COMING_SESSION,
                    event_type="COMING_SESSION_CONVERSION",
                    effective_datetime=now,
                    reason="Normal admission converted to a coming-session admission before session start.",
                    changed_by=updated_by,
                    class_id=requested_class_id,
                )
            else:
                new_roll = pending_roll_sentinel(int(student_id))
            new_status = STATUS_COMING_SESSION
            coming_status = COMING_STATUS_PENDING
            coming_target = target_year
        else:
            if existing_status == STATUS_COMING_SESSION:
                _assert_no_irreversible_activity(cursor, int(student_id))
                if _promotion_completed_for_class(cursor, requested_class_id, target_year):
                    raise ValueError(
                        "This class has already completed promotion for the ending session. "
                        "The student cannot now be converted to a normal current-session admission."
                    )
                details = validate_reserved_capacity(
                    cursor, requested_class_id, exclude_student_id=int(student_id)
                )
                del details
                new_roll = student_service._next_transfer_roll_with_cursor(
                    cursor, class_id=requested_class_id, student_id=int(student_id)
                )
                _insert_status_history(
                    cursor,
                    student_id=int(student_id),
                    old_status=STATUS_COMING_SESSION,
                    new_status=STATUS_ACTIVE,
                    event_type="COMING_SESSION_CONVERSION",
                    effective_datetime=now,
                    reason="Coming-session admission converted to a normal admission before session start.",
                    changed_by=updated_by,
                    class_id=requested_class_id,
                )
                new_status = STATUS_ACTIVE
                coming_status = None
                coming_target = None
            else:
                raise ValueError("The student is already a normal Active admission.")

        cursor.execute(
            """
            UPDATE students SET class_id=?,student_name=?,gender=?,father_name=?,father_cnic=?,
                father_contact_number=?,whatsapp_number=?,current_address=?,permanent_address=?,
                school_leaving_certificate_submitted=?,form_b_submitted=?,birth_certificate_submitted=?,
                date_of_birth=?,student_status=?,roll_number=?,released_roll_number=NULL,
                target_session_start_year=?,coming_session_status=?,
                coming_session_effective_date=NULL,coming_session_activated_at=NULL,
                updated_at=?,updated_by=?
            WHERE student_id=?
            """,
            (
                requested_class_id, normalized_name, normalized_gender, normalized_father,
                normalized_cnic, father_contact_number, whatsapp_number or None,
                current_address or None, permanent_address or None,
                1 if school_leaving_certificate_submitted else 0,
                1 if form_b_submitted else 0,
                1 if birth_certificate_submitted else 0,
                normalized_dob, new_status, int(new_roll), coming_target,
                coming_status, now, str(updated_by or "admin"), int(student_id),
            ),
        )

        if existing_status == STATUS_COMING_SESSION and desired_coming_session and int(row["class_id"]) != requested_class_id:
            cursor.execute(
                """
                INSERT INTO student_class_history(
                    student_id,old_class_id,new_class_id,effective_month,effective_year,
                    change_type,change_datetime,changed_by
                ) VALUES(?,?,?,?,?,'MANUAL',?,?)
                """,
                (
                    int(student_id), int(row["class_id"]), requested_class_id,
                    int(row["admission_month"]), int(row["admission_year"]), now,
                    f"COMING_SESSION_CLASS_CORRECTION:{str(updated_by or 'admin')}",
                ),
            )

        from app.services import sibling_fee_service
        sibling_fee_service.recalculate_sibling_fees_after_student_change(
            student_id=int(student_id),
            old_father_cnic=row["father_cnic"],
            new_father_cnic=normalized_cnic,
            old_father_name=row["father_name"],
            new_father_name=normalized_father,
            effective_month=int(row["admission_month"]),
            effective_year=int(row["admission_year"]),
            connection=connection,
            changed_by=str(updated_by or "admin"),
        )

        if existing_status == STATUS_COMING_SESSION and not desired_coming_session:
            from app.services import fee_service
            fee_service.ensure_fee_records_generated(
                int(student_id), connection=connection, as_of=now_dt
            )

        connection.commit()
        updated = connection.execute(
            "SELECT * FROM students WHERE student_id=?", (int(student_id),)
        ).fetchone()
        return dict(updated)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _unresolved_active_students_for_activation(
    cursor,
    *,
    class_id: int,
    target_year: int,
):
    """Return Active rows not resolved for the target academic session."""
    effective_date = f"{int(target_year):04d}-04-01"
    return cursor.execute(
        """
        SELECT s.student_id,s.student_name,s.roll_number,s.admission_date
        FROM students s
        WHERE s.class_id=? AND s.student_status='Active'
          AND NOT EXISTS (
              SELECT 1
              FROM promotion_history ph
              JOIN promotion_batches pb ON pb.batch_id=ph.batch_id
              WHERE ph.student_id=s.student_id AND ph.is_reversed=0
                AND pb.is_reversed=0
                AND pb.target_session_start_year=?
                AND ph.new_status='Active'
                AND COALESCE(ph.new_class_id,ph.old_class_id)=s.class_id
          )
          AND date(s.admission_date)<date(?)
          AND NOT EXISTS (
              SELECT 1 FROM student_class_history h
              WHERE h.student_id=s.student_id AND h.new_class_id=s.class_id
                AND date(printf('%04d-%02d-01',h.effective_year,h.effective_month))>=date(?)
                AND h.change_type IN ('ADMISSION','MANUAL')
          )
        ORDER BY s.roll_number,s.student_id
        """,
        (int(class_id), int(target_year), effective_date, effective_date),
    ).fetchall()


def activate_due_coming_session_admissions(
    *,
    as_of: datetime | None = None,
    changed_by: str = "automatic_session_activation",
) -> dict:
    """Activate every due pending admission class-by-class, without partial classes."""
    reference = as_of or datetime.now()
    probe = get_connection(automatic_backup=False)
    try:
        class_rows = probe.execute(
            """
            SELECT DISTINCT class_id,target_session_start_year
            FROM students
            WHERE student_status='Coming Session' AND coming_session_status='PENDING'
              AND target_session_start_year IS NOT NULL
            ORDER BY target_session_start_year,class_id
            """
        ).fetchall()
    finally:
        probe.close()

    activated = 0
    errors: list[str] = []
    for class_row in class_rows:
        target_year = int(class_row["target_session_start_year"])
        if reference.date() < session_start_date(target_year):
            continue
        class_id = int(class_row["class_id"])
        connection = get_connection()
        cursor = connection.cursor()
        try:
            connection.execute("BEGIN IMMEDIATE")
            class_info = cursor.execute(
                "SELECT * FROM classes WHERE id=?", (class_id,)
            ).fetchone()
            if class_info is None or int(class_info["is_active"] or 0) != 1:
                raise ValueError(f"Class ID {class_id} is missing or Inactive.")
            active = cursor.execute(
                "SELECT * FROM students WHERE class_id=? AND student_status='Active' ORDER BY roll_number,student_id",
                (class_id,),
            ).fetchall()
            pending = cursor.execute(
                """
                SELECT * FROM students
                WHERE class_id=? AND student_status='Coming Session'
                  AND coming_session_status='PENDING' AND target_session_start_year=?
                ORDER BY admission_date,tracking_id,student_id
                """,
                (class_id, target_year),
            ).fetchall()
            if not pending:
                connection.rollback()
                continue

            unresolved = _unresolved_active_students_for_activation(
                cursor,
                class_id=class_id,
                target_year=target_year,
            )
            if unresolved:
                preview = ", ".join(
                    f"{row['student_name']} (Roll {row['roll_number']})"
                    for row in unresolved[:5]
                )
                remaining = len(unresolved) - min(len(unresolved), 5)
                if remaining:
                    preview += f", and {remaining} more"
                raise ValueError(
                    f"{class_info['class_name']} coming-session activation is "
                    "blocked because the ending-session promotion is incomplete. "
                    f"Unresolved Active student(s): {preview}. Complete the "
                    "full-class promotion before activation."
                )

            total = len(active) + len(pending)
            if total > int(class_info["student_capacity"]):
                raise ValueError(
                    f"{class_info['class_name']} capacity is {class_info['student_capacity']} "
                    f"but activation requires {total} seats."
                )
            for student in pending:
                student_service._validate_class_4_plus_gender_assignment_with_cursor(
                    cursor,
                    class_id=class_id,
                    gender=student["gender"],
                    student_status=STATUS_ACTIVE,
                    existing_class_id=class_id,
                    existing_gender=student["gender"],
                    allow_legacy_blank_route=False,
                )

            class_level = str(class_info["class_level"] or "").strip().upper()
            if class_level == "PLAY GROUP":
                ordered = sorted(
                    list(active) + list(pending),
                    key=lambda r: (
                        str(r["admission_date"] or ""),
                        _tracking_sort_value(r["tracking_id"]),
                        int(r["student_id"]),
                    ),
                )
            else:
                # Nursery through Class 10 use the approved annual new-session
                # roll recalculation. Existing Active students (retained and
                # promoted) keep their relative order, pending coming-session
                # admissions are appended by admission date/Tracking ID, and
                # the complete final class receives a continuous 1..N roll
                # sequence. Class 10 is intentionally included.
                ordered = list(active) + sorted(
                    pending,
                    key=lambda r: (
                        str(r["admission_date"] or ""),
                        _tracking_sort_value(r["tracking_id"]),
                        int(r["student_id"]),
                    ),
                )

            max_roll = max([int(r["roll_number"]) for r in active] + [0])
            temporary_base = max_roll + len(ordered) + 1000
            for index, student in enumerate(active, start=1):
                cursor.execute(
                    "UPDATE students SET roll_number=? WHERE student_id=?",
                    (temporary_base + index, int(student["student_id"])),
                )

            effective_date = f"{target_year:04d}-04-01"
            effective_datetime = f"{effective_date} 00:00:00"
            pending_ids = {int(row["student_id"]) for row in pending}
            for new_roll, student in enumerate(ordered, start=1):
                sid = int(student["student_id"])
                if sid in pending_ids:
                    cursor.execute(
                        """
                        UPDATE students SET student_status='Active',roll_number=?,released_roll_number=NULL,
                            coming_session_status='ACTIVATED',coming_session_effective_date=?,
                            coming_session_activated_at=?,updated_at=?,updated_by=?
                        WHERE student_id=?
                        """,
                        (new_roll, effective_date, reference.strftime("%Y-%m-%d %H:%M:%S"),
                         reference.strftime("%Y-%m-%d %H:%M:%S"), str(changed_by), sid),
                    )
                    _insert_status_history(
                        cursor,
                        student_id=sid,
                        old_status=STATUS_COMING_SESSION,
                        new_status=STATUS_ACTIVE,
                        event_type="COMING_SESSION_ACTIVATION",
                        effective_datetime=effective_datetime,
                        reason="Coming-session admission activated at the April session boundary.",
                        changed_by=changed_by,
                        class_id=class_id,
                    )
                    cursor.execute(
                        """
                        INSERT INTO student_class_history(
                            student_id,old_class_id,new_class_id,effective_month,effective_year,
                            change_type,change_datetime,changed_by
                        ) VALUES(?,?,?,?,?,'MANUAL',?,?)
                        """,
                        (sid, class_id, class_id, 4, target_year, effective_datetime,
                         f"COMING_SESSION_ACTIVATION:{str(changed_by)}"),
                    )
                else:
                    cursor.execute(
                        "UPDATE students SET roll_number=?,updated_at=?,updated_by=? WHERE student_id=?",
                        (new_roll, reference.strftime("%Y-%m-%d %H:%M:%S"), str(changed_by), sid),
                    )

            from app.services import sibling_fee_service
            sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                student_ids=sorted(pending_ids),
                effective_month=4,
                effective_year=target_year,
                connection=connection,
                changed_by=str(changed_by),
            )
            connection.commit()
            activated += len(pending)
        except Exception as error:
            connection.rollback()
            errors.append(f"Class {class_id}: {type(error).__name__}: {error}")
        finally:
            connection.close()
    return {"activated_count": activated, "errors": errors, "success": not errors}


def cancel_pending_admission(
    student_id: int,
    *,
    cancelled_by: str = "admin",
) -> dict:
    """Permanently remove a never-activated coming-session admission."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = cursor.execute(
            "SELECT * FROM students WHERE student_id=?", (int(student_id),)
        ).fetchone()
        if row is None:
            raise ValueError("Student not found.")
        if str(row["student_status"] or "") != STATUS_COMING_SESSION or str(
            row["coming_session_status"] or ""
        ).upper() != COMING_STATUS_PENDING:
            raise ValueError("Only a pending coming-session admission can be cancelled permanently.")
        target_year = int(row["target_session_start_year"])
        if date.today() >= session_start_date(target_year):
            raise ValueError(
                "The target session has started. Use the normal Drop Student workflow instead."
            )
        _assert_no_irreversible_activity(cursor, int(student_id))
        receipt = cursor.execute(
            """
            SELECT 1 FROM fee_receipts AS r
            JOIN student_fee_records AS f ON f.id=r.student_fee_record_id
            WHERE f.student_id=? LIMIT 1
            """,
            (int(student_id),),
        ).fetchone()
        if receipt is not None:
            raise ValueError("A receipt exists for this student; permanent cancellation is blocked.")

        family_rows = cursor.execute(
            """
            SELECT student_id FROM students
            WHERE student_id<>? AND student_status='Active'
              AND NORMALIZE_PERSON_NAME(father_name)=NORMALIZE_PERSON_NAME(?)
              AND COALESCE(TRIM(father_cnic),'')=COALESCE(TRIM(?),'')
            """,
            (int(student_id), row["father_name"], row["father_cnic"]),
        ).fetchall()
        family_ids = [int(item[0]) for item in family_rows]

        cursor.execute("DELETE FROM sibling_fee_assignments WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM student_fee_override WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM student_fee_records WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM admission_number_history WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM student_status_history WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM student_class_history WHERE student_id=?", (int(student_id),))
        cursor.execute("DELETE FROM students WHERE student_id=?", (int(student_id),))
        if cursor.rowcount != 1:
            raise RuntimeError("The pending admission could not be removed completely.")

        if family_ids:
            from app.services import sibling_fee_service
            sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                student_ids=family_ids,
                effective_month=4,
                effective_year=target_year,
                connection=connection,
                changed_by=str(cancelled_by),
            )
        connection.commit()
        return {
            "student_id": int(student_id),
            "admission_number": row["admission_number"],
            "cancelled": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
