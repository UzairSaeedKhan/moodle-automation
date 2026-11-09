import pytest
from conftest import run
from fakes import FakeWorld

from checkin.moodle import Moodle, MoodleError

BASE = "https://moodle.test"


def logged_in(world: FakeWorld) -> Moodle:
    m = Moodle(BASE, world)
    run(m.login("me", "secret"))
    return m


def test_login_checkin_home_then_checkout():
    world = FakeWorld()
    m = logged_in(world)

    out = run(m.check_in("1491", "2026-11-05", "am", "home"))
    assert (out.kind, out.detail) == ("done", "At home · checked in")
    assert world.submitted[-1]["location"] == "athome"
    assert world.submitted[-1]["sesskey"] == "K"
    assert "cancel" not in world.submitted[-1]

    assert run(m.check_in("1491", "2026-11-05", "am", "home")).kind == "already"  # idempotent

    out = run(m.check_out("1491", "2026-11-05", "am"))
    assert (out.kind, out.detail) == ("done", "status: Checked out")
    assert run(m.check_out("1491", "2026-11-05", "am")).kind == "already"


def test_campus_option():
    world = FakeWorld()
    run(logged_in(world).check_in("1491", "2026-11-05", "pm", "campus"))
    assert world.submitted[-1]["location"] == "oncampus"
    assert world.state["pm"] == "in"


def test_not_open_and_no_session():
    world = FakeWorld(checkin_open={"am": True, "pm": False})
    m = logged_in(world)
    assert run(m.check_in("1491", "2026-11-05", "pm", "home")).kind == "not_open"
    assert run(m.check_out("1491", "2026-11-05", "pm")).kind == "not_open"
    assert run(m.check_in("1491", "2026-11-06", "am", "home")).kind == "no_session"


def test_wrong_password_is_permanent():
    with pytest.raises(MoodleError) as e:
        run(Moodle(BASE, FakeWorld(password="other")).login("me", "secret"))
    assert e.value.retryable is False


def test_session_cookie_survives_redirects():
    world = FakeWorld()
    m = logged_in(world)
    assert m.cookies["MoodleSession"] == "auth"


class _EvilRedirect:
    """A 'Moodle' that redirects to another site: nothing may be sent there."""

    def __init__(self):
        self.urls = []

    async def request(self, method, url, headers, body):
        from checkin.ports import HttpResponse

        self.urls.append(url)
        return HttpResponse(302, "", "https://evil.example/steal", ["MoodleSession=x; path=/"])


def test_never_sends_login_to_another_site():
    http = _EvilRedirect()
    with pytest.raises(MoodleError, match="another site"):
        run(Moodle(BASE, http).login("me", "secret"))
    assert all(u.startswith(BASE) for u in http.urls)


def test_requires_https():
    with pytest.raises(MoodleError, match="https"):
        Moodle("http://moodle.test", FakeWorld())
    Moodle("http://127.0.0.1:8790", FakeWorld())  # local e2e test server is fine
