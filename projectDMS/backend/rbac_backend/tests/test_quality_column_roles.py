"""Column-role mapping - the prerequisite for any numeric check.

Companion document 3.3: a naive "last three numeric columns are qty x rate =
amount" rule produced 12 false mismatches on a correct document, because a
separate Nos column applied, two tables declared their own formulas, and an S/N
serial was read as a quantity. Roles must be established before anything is
checked, and an unestablished layout is NOT_CHECKABLE, not FAILED.
"""

from __future__ import annotations

from rbac_backend.services.extraction.quality.column_roles import (
    ColumnRole,
    is_checkable,
    map_column_roles,
)


def test_canonical_boq_header_maps_cleanly() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Unit", "Rate", "Amount"])

    assert roles == [
        ColumnRole.SERIAL,
        ColumnRole.DESCRIPTION,
        ColumnRole.QUANTITY,
        ColumnRole.UNIT,
        ColumnRole.RATE,
        ColumnRole.AMOUNT,
    ]


def test_nos_is_distinguished_from_quantity() -> None:
    # The measured page-5 case: 3 x 32.61 x 3,200 = 313,056. Reading Nos as Qty
    # produced a false mismatch.
    roles = map_column_roles(["Description", "Nos", "Qty", "Rate", "Amount"])

    assert roles[1] is ColumnRole.NOS
    assert roles[2] is ColumnRole.QUANTITY


def test_serial_column_is_never_a_quantity() -> None:
    for header in ("S/N", "Sr. No.", "SL", "Item No", "#"):
        assert map_column_roles([header])[0] is ColumnRole.SERIAL


def test_header_variants_are_recognised() -> None:
    assert map_column_roles(["Quantity"])[0] is ColumnRole.QUANTITY
    assert map_column_roles(["Unit Rate"])[0] is ColumnRole.RATE
    assert map_column_roles(["Total Amount (INR)"])[0] is ColumnRole.AMOUNT
    assert map_column_roles(["UOM"])[0] is ColumnRole.UNIT


def test_case_and_whitespace_are_tolerated() -> None:
    assert map_column_roles(["  AMOUNT  "])[0] is ColumnRole.AMOUNT
    assert map_column_roles(["rate"])[0] is ColumnRole.RATE


def test_multiline_header_cells_are_tolerated() -> None:
    # Table headers wrap; the newline must not defeat recognition.
    assert map_column_roles(["Unit\nRate"])[0] is ColumnRole.RATE


def test_unrecognised_headers_are_unknown_not_guessed() -> None:
    roles = map_column_roles(["Depth (mtr)", "Thickness (mtr)", "Volume (m3)"])

    assert all(role is ColumnRole.UNKNOWN for role in roles)


def test_table_with_unknown_roles_is_not_checkable() -> None:
    # Page 5's declared-formula table: Area A=[h*l], Volume [l*b*h]. Not a
    # qty x rate table at all - checking it produced 8 false mismatches.
    roles = map_column_roles(
        ["DW No", "Depth", "Thickness", "Length", "Area", "Volume"]
    )

    assert is_checkable(roles) is False


def test_table_with_qty_rate_amount_is_checkable() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Rate", "Amount"])

    assert is_checkable(roles) is True


def test_nos_alone_with_rate_and_amount_is_checkable() -> None:
    roles = map_column_roles(["Description", "Nos", "Rate", "Amount"])

    assert is_checkable(roles) is True


def test_missing_amount_is_not_checkable() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Rate"])

    assert is_checkable(roles) is False


def test_missing_rate_is_not_checkable() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Amount"])

    assert is_checkable(roles) is False


def test_amount_and_rate_without_any_quantity_is_not_checkable() -> None:
    roles = map_column_roles(["Description", "Rate", "Amount"])

    assert is_checkable(roles) is False


def test_empty_header_row_is_not_checkable() -> None:
    assert is_checkable(map_column_roles([])) is False
    assert is_checkable(map_column_roles(["", None])) is False  # type: ignore[list-item]


def test_serial_does_not_satisfy_the_quantity_requirement() -> None:
    # The measured p6/p8 false positive: S/N read as a quantity.
    roles = map_column_roles(["S/N", "Description", "Rate", "Amount"])

    assert is_checkable(roles) is False
