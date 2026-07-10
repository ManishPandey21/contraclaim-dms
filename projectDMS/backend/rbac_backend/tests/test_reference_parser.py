import pytest

from rbac_backend.services.reference_parser import parse_legacy_reference_text


@pytest.mark.parametrize(
    ("raw", "expected_letter_no", "expected_date"),
    [
        (
            "UPMRC/CPM-1/KANPUR/KNPCC-06/2024-25/Vol-2/1 17/104 dtd. 05.09.2024",
            "UPMRC/CPM-1/KANPUR/KNPCC-06/2024-25/Vol-2/1 17/104",
            "05-09-2024",
        ),
        (
            "Engineer's letter no. Kanpur-LET-JVTI-TBM-00371-E01 - dated 06.09.2024",
            "Kanpur-LET-JVTI-TBM-00371-E01",
            "06-09-2024",
        ),
        (
            "Engineer\u2019s letter no. Kanpur-LET-JVTI-TBM-00395-E01 - dated 07.11.2024",
            "Kanpur-LET-JVTI-TBM-00395-E01",
            "07-11-2024",
        ),
        (
            "Contractor's letter no. AFC/PM/KNPCC-06/4905 dtd. 05.11.2025",
            "AFC/PM/KNPCC-06/4905",
            "05-11-2025",
        ),
    ],
)
def test_parse_legacy_reference_text_handles_prefixed_dated_references(
    raw: str,
    expected_letter_no: str,
    expected_date: str,
) -> None:
    parsed = parse_legacy_reference_text(raw)

    assert parsed == {
        "raw": raw,
        "letterNo": expected_letter_no,
        "letter_no": expected_letter_no,
        "date": expected_date,
    }
