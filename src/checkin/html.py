"""Tiny, dependency-free HTML helpers tailored to Moodle's markup."""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]*>", " ", fragment))).strip()


def attr(tag: str, name: str) -> str | None:
    """Read an attribute from an opening tag string like '<a class="x" href="...">'."""
    m = re.search(rf"""\s{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", tag, re.I)
    if not m:
        return None
    return _html.unescape(next(g for g in m.groups() if g is not None))


def has_attr(tag: str, name: str) -> bool:
    return re.search(rf"\s{name}(?=[\s=>/]|$)", tag, re.I) is not None


# ---------------------------------------------------------------------------
# Attendance table
# ---------------------------------------------------------------------------


@dataclass
class Link:
    href: str
    text: str


@dataclass
class SessionRow:
    date: str  # "Thu 5 Nov 2026"
    time: str  # "1:30PM - 5PM"
    start: int | None  # minutes since midnight
    end: int | None
    status: str  # "?", "Checked out", ...
    links: list[Link] = field(default_factory=list)


_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_DATE_TD = re.compile(r"<td\b[^>]*\bdatecol\b[^>]*>(.*?)</td>", re.I | re.S)
_STATUS_TD = re.compile(r"<td\b[^>]*\bstatuscol\b[^>]*>(.*?)</td>", re.I | re.S)
_NOBR = re.compile(r"<nobr>(.*?)</nobr>", re.I | re.S)
_A = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)


def parse_session_rows(page: str) -> list[SessionRow]:
    rows: list[SessionRow] = []
    for m in _TR.finditer(page):
        tr = m[1]
        date_cell = _DATE_TD.search(tr)
        if not date_cell:
            continue
        nobrs = [text_of(x) for x in _NOBR.findall(date_cell[1])]
        date = nobrs[0] if nobrs else text_of(date_cell[1])
        time = nobrs[1] if len(nobrs) > 1 else ""
        status_cell = _STATUS_TD.search(tr)
        links = [Link(attr(f"<a{a[0]}", "href") or "", text_of(a[1])) for a in _A.findall(tr)]
        start, end = parse_time_range(time)
        rows.append(
            SessionRow(date, time, start, end, text_of(status_cell[1]) if status_cell else "", [l for l in links if l.href])
        )
    return rows


def parse_clock(s: str) -> int | None:
    """'9AM', '12:30PM', '13:30', '1.30 pm' -> minutes since midnight"""
    m = re.fullmatch(r"(\d{1,2})(?:[:.h](\d{2}))?\s*([ap])?\.?\s*m?\.?", s.strip(), re.I)
    if not m:
        return None
    h, mi = int(m[1]), int(m[2] or 0)
    ap = (m[3] or "").lower()
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def parse_time_range(s: str) -> tuple[int | None, int | None]:
    parts = re.split(r"\s*[-–]\s*", s, maxsplit=1)
    a = parse_clock(parts[0]) if parts and parts[0] else None
    b = parse_clock(parts[1]) if len(parts) > 1 and parts[1] else None
    return a, b


def is_checkout_link(l: Link) -> bool:
    return bool(re.search(r"mod/attendance/attendance\.php\?", l.href)) and bool(re.search(r"[?&]action=checkout\b", l.href))


def is_checkin_link(l: Link) -> bool:
    return bool(re.search(r"mod/attendance/attendance\.php\?", l.href)) and not re.search(r"[?&]action=checkout\b", l.href)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def find_row(rows: list[SessionRow], date_label: str, half: str) -> SessionRow | None:
    """The morning (start < 12:00) or afternoon session for a Moodle date label."""
    same = [r for r in rows if _norm(r.date) == _norm(date_label)]
    for r in same:
        if r.start is not None and ((half == "am") == (r.start < 720)):
            return r
    if len(same) == 2 and all(r.start is None for r in same):
        return same[0] if half == "am" else same[1]
    return None


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


@dataclass
class Option:
    value: str
    text: str
    selected: bool


@dataclass
class SelectField:
    name: str
    options: list[Option]


@dataclass
class RadioField:
    name: str
    value: str
    label: str
    checked: bool


@dataclass
class ParsedForm:
    action: str
    method: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    selects: list[SelectField] = field(default_factory=list)
    radios: list[RadioField] = field(default_factory=list)
    submits: list[tuple[str, str]] = field(default_factory=list)


_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.I | re.S)
_LABEL = re.compile(r"<label\b([^>]*)>(.*?)</label>", re.I | re.S)
_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.I | re.S)
_OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.I | re.S)
_BUTTON = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.I | re.S)


def parse_forms(page: str) -> list[ParsedForm]:
    forms: list[ParsedForm] = []
    for m in _FORM.finditer(page):
        open_tag, body = f"<form{m[1]}", m[2]
        labels = {}
        for l_attrs, l_body in _LABEL.findall(body):
            for_id = attr(f"<label{l_attrs}", "for")
            if for_id:
                labels[for_id] = text_of(l_body)

        f = ParsedForm(action=attr(open_tag, "action") or "", method=(attr(open_tag, "method") or "get").lower())
        for tag in _INPUT.findall(body):
            name = attr(tag, "name")
            if not name or has_attr(tag, "disabled"):
                continue
            typ = (attr(tag, "type") or "text").lower()
            value = attr(tag, "value") or ""
            if typ == "submit":
                f.submits.append((name, value))
            elif typ == "radio":
                rid = attr(tag, "id")
                f.radios.append(RadioField(name, value, labels.get(rid or "", ""), has_attr(tag, "checked")))
            elif typ == "checkbox":
                if has_attr(tag, "checked"):
                    f.fields.append((name, value or "on"))
            elif typ in ("button", "reset", "image", "file"):
                pass
            else:
                f.fields.append((name, value))

        for s_attrs, s_body in _SELECT.findall(body):
            name = attr(f"<select{s_attrs}", "name")
            if not name:
                continue
            opts = []
            for o_attrs, o_body in _OPTION.findall(s_body):
                tag, text = f"<option{o_attrs}", text_of(o_body)
                v = attr(tag, "value")
                opts.append(Option(text if v is None else v, text, has_attr(tag, "selected")))
            f.selects.append(SelectField(name, opts))

        for b_attrs, b_body in _BUTTON.findall(body):
            tag = f"<button{b_attrs}"
            name = attr(tag, "name")
            if (attr(tag, "type") or "submit").lower() == "submit" and name:
                v = attr(tag, "value")
                f.submits.append((name, text_of(b_body) if v is None else v))
        forms.append(f)
    return forms


def _group_radios(radios: list[RadioField]) -> dict[str, list[RadioField]]:
    g: dict[str, list[RadioField]] = {}
    for r in radios:
        g.setdefault(r.name, []).append(r)
    return g


def describe_form(f: ParsedForm) -> str:
    parts = [f'select "{s.name}": [{" | ".join(o.text for o in s.options)}]' for s in f.selects]
    parts += [f'radio "{n}": [{" | ".join(r.label or r.value for r in rs)}]' for n, rs in _group_radios(f.radios).items()]
    if not parts:
        parts.append("no dropdown or radio buttons")
    parts.append(f"fields: {', '.join(k for k, _ in f.fields) or '-'}")
    parts.append(f"action: {f.action or '(same page)'}")
    return "; ".join(parts)


class FormMismatchError(Exception):
    pass


def _set(params: list[tuple[str, str]], key: str, value: str) -> None:
    params[:] = [(k, v) for k, v in params if k != key]
    params.append((key, value))


def build_submission(form: ParsedForm, wanted: str) -> tuple[list[tuple[str, str]], str]:
    """Keep hidden fields, pick the dropdown/radio option whose text contains `wanted`
    (case-insensitive), keep defaults elsewhere, add the submit button.
    Returns (params, chosen option text). Raises FormMismatchError if nothing matched."""
    params = list(form.fields)
    w = wanted.lower()
    chosen: str | None = None

    for s in form.selects:
        hit = next((o for o in s.options if w in o.text.lower()), None)
        pick = hit or next((o for o in s.options if o.selected), None) or (s.options[0] if s.options else None)
        if hit:
            chosen = hit.text
        if pick:
            _set(params, s.name, pick.value)

    for name, rs in _group_radios(form.radios).items():
        hit = next((r for r in rs if w in r.label.lower()), None)
        pick = hit or next((r for r in rs if r.checked), None)
        if hit:
            chosen = hit.label
        if pick:
            _set(params, name, pick.value)

    submit = next((s for s in form.submits if s[0] != "cancel"), None)
    if submit:
        _set(params, *submit)

    if not chosen:
        raise FormMismatchError(f'No option containing "{wanted}" in the check-in form. Found: {describe_form(form)}')
    return params, chosen


def is_login_page(page: str) -> bool:
    return bool(re.search(r'<body[^>]*\bid="page-login-index"', page, re.I) or re.search(r'name="logintoken"', page, re.I))
