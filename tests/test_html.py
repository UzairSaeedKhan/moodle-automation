import pytest

from checkin.html import (
    FormMismatchError,
    build_submission,
    find_row,
    is_checkin_link,
    is_checkout_link,
    parse_clock,
    parse_forms,
    parse_session_rows,
)


@pytest.fixture
def rows(fixture_text):
    return parse_session_rows(fixture_text("attendance-view.html"))


@pytest.fixture
def form(fixture_text):
    return next(f for f in parse_forms(fixture_text("checkin-form.html")) if f.selects)


def test_reads_every_row_and_skips_header(rows):
    assert len(rows) == 4
    r = rows[0]
    assert (r.date, r.time, r.start, r.end, r.status) == ("Thu 5 Nov 2026", "9AM - 12:30PM", 540, 750, "Checked out")


def test_finds_morning_and_afternoon(rows):
    assert find_row(rows, "Thu 5 Nov 2026", "am").status == "Checked out"
    pm = find_row(rows, "Thu 5 Nov 2026", "pm")
    assert pm.start == 13 * 60 + 30
    assert len(pm.links) == 1
    assert is_checkout_link(pm.links[0]) and not is_checkin_link(pm.links[0])
    assert pm.links[0].href == "https://moodle.becode.org/mod/attendance/attendance.php?sessid=20390&sesskey=TESTKEY&action=checkout"


def test_future_absence_link_is_not_a_checkin(rows):
    fri = find_row(rows, "Fri 6 Nov 2026", "am")
    assert fri.status == "?"
    assert not any(is_checkin_link(l) for l in fri.links)


def test_no_row_for_days_without_sessions(rows):
    assert find_row(rows, "Sat 7 Nov 2026", "am") is None


@pytest.mark.parametrize(
    "text,minutes", [("9AM", 540), ("12:30PM", 750), ("1:30PM", 810), ("5PM", 1020), ("12AM", 0), ("13:30", 810)]
)
def test_parse_clock(text, minutes):
    assert parse_clock(text) == minutes


def test_parses_form(form):
    assert form.method == "post"
    assert ("sesskey", "TESTKEY") in form.fields
    assert ("sessid", "20392") in form.fields
    assert form.action == "https://moodle.becode.org/mod/attendance/attendance.php"
    assert [(o.value, o.text) for o in form.selects[0].options] == [("oncampus", "On campus"), ("athome", "At home")]


def test_picks_option_by_text_and_skips_cancel(form):
    params, chosen = build_submission(form, "campus")
    d = dict(params)
    assert chosen == "On campus"
    assert d["location"] == "oncampus"
    assert d["submitbutton"] == "Save changes"
    assert d["_qf__mod_attendance_form_studentlocation"] == "1"
    assert "cancel" not in d
    assert dict(build_submission(form, "home")[0])["location"] == "athome"


def test_real_checkin_row(fixture_text):
    rows = parse_session_rows(fixture_text("checkin-row.html"))
    pm = find_row(rows, "Mon 9 Nov 2026", "pm")
    checkin = [l for l in pm.links if is_checkin_link(l)]
    assert len(checkin) == 1 and not any(is_checkout_link(l) for l in pm.links)
    assert checkin[0].href == "https://moodle.becode.org/mod/attendance/attendance.php?sessid=20392&sesskey=TESTKEY"
    # "Report an absence" / "Report a future absence" are never mistaken for check-in.
    assert not any(is_checkin_link(l) for l in find_row(rows, "Tue 10 Nov 2026", "am").links)
    assert not any(is_checkin_link(l) for l in find_row(rows, "Mon 9 Nov 2026", "am").links)


def test_mismatch_explains_what_it_found(form):
    with pytest.raises(FormMismatchError, match=r"On campus \| At home"):
        build_submission(form, "library")


def test_radio_buttons_supported():
    html = """<form action="/mod/attendance/attendance.php" method="post">
      <input type="hidden" name="sesskey" value="k">
      <input type="radio" name="status" id="s1" value="1"><label for="s1">Present (on campus)</label>
      <input type="radio" name="status" id="s2" value="2"><label for="s2">Present (at home)</label>
      <input type="submit" name="submitbutton" value="Save changes">
    </form>"""
    params, chosen = build_submission(parse_forms(html)[0], "home")
    assert dict(params)["status"] == "2"
    assert chosen == "Present (at home)"
