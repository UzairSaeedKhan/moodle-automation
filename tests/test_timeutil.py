from datetime import datetime, timezone

import pytest

from checkin.timeutil import current_work_week_monday, local_now, moodle_date_label, parse_when


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "when,ymd,hhmm",
    [
        ("2026-11-05T08:00", "2026-11-05", "09:00"),  # winter, UTC+1
        ("2026-06-04T07:00", "2026-06-04", "09:00"),  # summer, UTC+2
        ("2026-11-05T23:30", "2026-11-06", "00:30"),  # past midnight
    ],
)
def test_local_now_brussels(when, ymd, hhmm):
    n = local_now(utc(when), "Europe/Brussels")
    assert (n.ymd, n.hhmm) == (ymd, hhmm)


def test_moodle_label():
    assert moodle_date_label("2026-11-05") == "Thu 5 Nov 2026"


def test_week_monday():
    assert current_work_week_monday("2026-11-05") == "2026-11-02"
    assert current_work_week_monday("2026-11-08") == "2026-11-09"  # Sunday -> next week


TODAY = "2026-11-05"  # Thursday


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ["2026-11-05"]),
        ("today", ["2026-11-05"]),
        ("tomorrow", ["2026-11-06"]),
        ("fri", ["2026-11-06"]),
        ("monday", ["2026-11-09"]),
        ("thu", ["2026-11-05"]),
        ("mon wed", ["2026-11-09", "2026-11-11"]),
        ("12/11", ["2026-11-12"]),
        ("7/11", []),  # Saturday
        ("12/11 - 16/11", ["2026-11-12", "2026-11-13", "2026-11-16"]),
        (
            "23/12-04/01",
            ["2026-12-23", "2026-12-24", "2026-12-25", "2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-04"],
        ),
        ("nextweek", ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]),
        ("week", ["2026-11-05", "2026-11-06"]),
        ("01/11", ["2027-11-01"]),  # past this year -> next year
        ("31/02", None),
        ("blah", None),
    ],
)
def test_parse_when(text, expected):
    assert parse_when(text, TODAY) == expected
