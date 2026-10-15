"""Assemble every deterministic check into one verdict per page.

Runs unconditionally, on native and OCR text alike. Companion 3.1 measured 9
corruptions in a PDF's *own* text layer, so "native means trustworthy" is false
and there is no page this gate may skip.

Only FAIL and INDETERMINATE escalate. NOT_CHECKABLE - "we could not identify
this structure well enough to verify it" - accepts and marks unverified,
because the alternative is the 12 paid model calls per correct document that
companion 3.3 measured.

NOT_CHECKABLE is also not PASS. A page carrying a numeric table whose
arithmetic was never verified stays NOT_CHECKABLE whatever else passed on it:
a consistent date column (real claim page 4) says nothing about the amounts.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from ..models import ExtractedPage, PageClass
from .column_roles import ColumnRole, is_checkable, map_column_roles
from .date_checks import check_date_column
from .models import CheckResult, NumericRepair, QualityVerdict, Verdict
from .numeric_checks import (
    check_row,
    check_subtotal,
    detect_split_digits,
    parse_amount,
    propose_repair,
)
from .reading_order import check_reading_order, check_text_density

logger = logging.getLogger(__name__)

Table = Sequence[Sequence[str]]

#: Checks that establish something about the *content*. A page whose only
#: passing check is "it has some text" has not been verified.
_SUBSTANTIVE = {"row_identity", "subtotal_identity", "date_convention"}

_MIN_DATE_VALUES = 2

#: How far down a table its header may sit. Real tables lead with blank, title
#: and spacer rows; the measured claim's headers sit at rows 0 to 4.
HEADER_SEARCH_ROWS = 6

#: The checks that verify a table's numbers. A date check does not.
_ARITHMETIC = ("row_identity", "subtotal_identity")

#: One per numeric table: how its header was chosen and whether any arithmetic
#: verified it. NOT_CHECKABLE here keeps the page from passing.
_TABLE_VERIFICATION = "table_verification"


class ExtractionQualityGate:
    def assess(
        self, page: ExtractedPage, *, tables: Optional[Sequence[Table]] = None
    ) -> QualityVerdict:
        checks: List[CheckResult] = [
            check_text_density(page.text, page.classification),
            check_reading_order(page.text, page.classification),
        ]
        repairs: List[NumericRepair] = []

        for position, table in enumerate(tables or []):
            table_checks, table_repairs = self._assess_table(
                table, page_number=page.number, position=position
            )
            checks.extend(table_checks)
            repairs.extend(table_repairs)

        verdict = self._aggregate(checks, page)
        reasons = [
            f"{check.name}: {check.detail}"
            for check in checks
            if check.verdict in {Verdict.FAIL, Verdict.INDETERMINATE}
        ]
        return QualityVerdict(
            verdict=verdict, checks=checks, repairs=repairs, reasons=reasons
        )

    def _assess_table(
        self, table: Table, *, page_number: int, position: int
    ) -> tuple[List[CheckResult], List[NumericRepair]]:
        checks: List[CheckResult] = []
        repairs: List[NumericRepair] = []

        rows = [list(row) for row in table]
        header_index = self._find_header(rows)
        # With no header found, row 0 stays the nominal header for the date
        # checks, as before, and every row counts as unverified content.
        start = 0 if header_index is None else header_index
        headers = rows[start] if rows else []
        roles = map_column_roles(headers)
        body = rows[start + 1 :]
        content = body if header_index is not None else rows

        if len(rows) >= 2:
            checks.extend(self._date_checks(headers, body))
            if header_index is None:
                checks.append(
                    CheckResult(
                        name="row_identity",
                        verdict=Verdict.NOT_CHECKABLE,
                        detail="column roles not established for this table",
                    )
                )
            else:
                repairs = self._check_arithmetic(
                    body, roles, checks, page_number=page_number
                )

        summary = self._table_verification(
            position, header_index, roles, content, checks
        )
        if summary is not None:
            checks.append(summary)
        return checks, repairs

    @staticmethod
    def _find_header(rows: Sequence[Sequence[str]]) -> Optional[int]:
        """Index of the first row whose roles make the table checkable.

        Searches only the leading ``HEADER_SEARCH_ROWS`` rows, and only past
        rows that carry no number: a blank, a title or a spacer can precede a
        header, data cannot. A header-shaped row below data would leave those
        data rows silently unchecked. None means no header - never a guess.
        """
        for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
            if is_checkable(map_column_roles(row)):
                return index
            if any(parse_amount(cell) is not None for cell in row):
                return None
        return None

    @staticmethod
    def _table_verification(
        position: int,
        header_index: Optional[int],
        roles: Sequence[ColumnRole],
        content: Sequence[Sequence[str]],
        table_checks: Sequence[CheckResult],
    ) -> Optional[CheckResult]:
        """Say whether this table's numbers were verified, and how.

        A table without numbers has no arithmetic to verify and gets no record.
        A numeric table is verified only when some arithmetic check passed on
        it; established roles alone verify nothing.
        """
        numeric = sum(
            1 for row in content for cell in row if parse_amount(cell) is not None
        )
        if numeric == 0:
            return None

        label = f"table {position + 1}"
        if header_index is None:
            return CheckResult(
                name=_TABLE_VERIFICATION,
                verdict=Verdict.NOT_CHECKABLE,
                detail=(
                    f"{label}: no header row with rate, amount and quantity or "
                    f"nos columns in the first {HEADER_SEARCH_ROWS} rows; "
                    f"{numeric} numeric cell(s) unverified"
                ),
            )

        named = ", ".join(
            role.value for role in roles if role is not ColumnRole.UNKNOWN
        )
        header = f"header row {header_index} ({named})"
        refs = [f"table[{position}].header.row[{header_index}]"]
        passed = {
            name: sum(
                1
                for check in table_checks
                if check.name == name and check.verdict is Verdict.PASS
            )
            for name in _ARITHMETIC
        }
        if sum(passed.values()):
            return CheckResult(
                name=_TABLE_VERIFICATION,
                verdict=Verdict.PASS,
                detail=(
                    f"{label}: {header}; {passed['row_identity']} row and "
                    f"{passed['subtotal_identity']} subtotal check(s) passed"
                ),
                evidence_refs=refs,
            )
        return CheckResult(
            name=_TABLE_VERIFICATION,
            verdict=Verdict.NOT_CHECKABLE,
            detail=(
                f"{label}: {header}, but no arithmetic check passed; "
                f"{numeric} numeric cell(s) unverified"
            ),
            evidence_refs=refs,
        )

    def _check_arithmetic(
        self,
        body: Sequence[Sequence[str]],
        roles: Sequence[ColumnRole],
        checks: List[CheckResult],
        *,
        page_number: int,
    ) -> List[NumericRepair]:
        repairs: List[NumericRepair] = []
        data_rows, stated_total = self._split_total_row(body, roles)
        for index, row in enumerate(data_rows):
            row_check = check_row(row, roles)
            checks.append(row_check)

            repair = self._maybe_repair(
                row,
                data_rows,
                roles,
                candidate_row_index=index,
                stated_total=stated_total,
                page_number=page_number,
            )
            if repair is not None:
                repairs.append(repair)

        if stated_total is not None:
            checks.append(
                check_subtotal(data_rows, stated_total, roles, candidate_row_index=0)
            )

        return repairs

    def _maybe_repair(
        self,
        row: Sequence[str],
        data_rows: Sequence[Sequence[str]],
        roles: Sequence[ColumnRole],
        *,
        candidate_row_index: int,
        stated_total: Optional[str],
        page_number: int,
    ) -> Optional[NumericRepair]:
        """Repair a corrupted amount only when two independent checks agree."""
        amount_index = self._index_of(roles, ColumnRole.AMOUNT)
        if amount_index is None or amount_index >= len(row):
            return None

        raw = row[amount_index]
        repaired_literal = detect_split_digits(raw)
        if repaired_literal is None or stated_total is None:
            return None

        repaired_row = list(row)
        repaired_row[amount_index] = repaired_literal
        repaired_rows = [list(item) for item in data_rows]
        repaired_rows[candidate_row_index] = repaired_row

        repair = propose_repair(
            raw,
            row_check=check_row(repaired_row, roles),
            subtotal_check=check_subtotal(
                repaired_rows,
                stated_total,
                roles,
                candidate_row_index=candidate_row_index,
            ),
        )
        if repair is not None:
            repair.page = page_number
        return repair

    @staticmethod
    def _index_of(roles: Sequence[ColumnRole], role: ColumnRole) -> Optional[int]:
        for index, column_role in enumerate(roles):
            if column_role is role:
                return index
        return None

    def _split_total_row(
        self, body: Sequence[Sequence[str]], roles: Sequence[ColumnRole]
    ) -> tuple[List[List[str]], Optional[str]]:
        """Separate data rows from a trailing stated-total row.

        A total row carries an amount but no operands - that is what makes it a
        total rather than another line item.
        """
        rows = [list(row) for row in body]
        if len(rows) < 2:
            return rows, None

        amount_index = self._index_of(roles, ColumnRole.AMOUNT)
        rate_index = self._index_of(roles, ColumnRole.RATE)
        if amount_index is None or rate_index is None:
            return rows, None

        last = rows[-1]
        has_amount = amount_index < len(last) and parse_amount(last[amount_index])
        has_rate = rate_index < len(last) and parse_amount(last[rate_index])
        if has_amount and not has_rate:
            return rows[:-1], last[amount_index]
        return rows, None

    def _date_checks(
        self, headers: Sequence[str], body: Sequence[Sequence[str]]
    ) -> List[CheckResult]:
        checks: List[CheckResult] = []
        for index in range(len(headers)):
            column = [row[index] for row in body if index < len(row)]
            parseable = [
                value
                for value in column
                if value and check_date_column([value]).verdict is not Verdict.NOT_CHECKABLE
            ]
            if len(parseable) >= _MIN_DATE_VALUES:
                checks.append(check_date_column(column))
        return checks

    @staticmethod
    def _aggregate(checks: Sequence[CheckResult], page: ExtractedPage) -> Verdict:
        if page.classification.page_class is PageClass.UNRENDERABLE:
            return Verdict.INDETERMINATE

        verdicts = [check.verdict for check in checks]
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.INDETERMINATE in verdicts:
            return Verdict.INDETERMINATE

        # After FAIL/INDETERMINATE, so a real failure still escalates.
        if any(
            check.name == _TABLE_VERIFICATION and check.verdict is Verdict.NOT_CHECKABLE
            for check in checks
        ):
            return Verdict.NOT_CHECKABLE

        substantive_pass = any(
            check.verdict is Verdict.PASS and check.name in _SUBSTANTIVE
            for check in checks
        )
        return Verdict.PASS if substantive_pass else Verdict.NOT_CHECKABLE
