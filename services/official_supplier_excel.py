"""Conservative direct-LIVE import of two explicitly identified official EPGs.

These provider layouts were identified from owner-supplied workbook examples.
This parser does not fetch mail, invent a broadcast, or imply permission to
auto-approve corrections. Callers must preserve manual editorial approval.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from io import BytesIO
import hashlib
from pathlib import PurePath
import re
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from services.epg_excel import (
    InvalidEPG, MAX_WORKBOOK_BYTES, MAX_SHEET_ROWS,
    ParsedEPG, _clock_minutes,
)
from services.live_evidence import event_is_editorial_or_replay
from services.time_logic import KZ_TIMEZONE

OFFICIAL_SOURCES = {
    "QAZSPORT HD": "email_epg_qazsport",
    "SPORT+ Qazaqstan": "email_epg_sportplus",
}
DIRECT = re.compile(
    r"прямая\s+трансляция|тікелей\s+эфир|тiкелей\s+эфир",
    re.I,
)
NOT_A_MATCH = re.compile(
    r"студий|студия|студиялық|матч\s+қарсаңында|"
    r"футбол\s+плюс|pluste\s+bol|тележурнал|"
    r"обзор|шолу|новости|news|"
    r"бойцы\s+в\s+истории|лучшие\s+бои|үздік\s+жекпе",
    re.I,
)
KZ_MONTHS = {
    "қаңтар": 1, "ақпан": 2, "наурыз": 3, "сәуір": 4,
    "мамыр": 5, "маусым": 6, "шілде": 7, "тамыз": 8,
    "қыркүйек": 9, "қазан": 10, "қараша": 11, "желтоқсан": 12,
}
DAY = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(KZ_MONTHS) + r")\b",
    re.I,
)
SPORTS = (
    (r"футзал", "Футзал"),
    (r"футбол|кпл|қпл|суперлига\s+турц|ла\s+лига", "Футбол"),
    (r"хоккей|қхл|кхл", "Хоккей"),
    (r"кәсіпқой\s+бокс|профессиональный\s+бокс|бокс", "Бокс"),
    (r"мма|mma|alash\s+pride|naiza|aca\s*\d|pfl\s+\d", "ММА"),
    (r"теннис", "Теннис"),
    (r"фигурн|мәнерлеп", "Фигурное катание"),
    (r"күрес|борьба", "Борьба"),
    (r"көркем\s+жүзу|синхронн", "Артистическое плавание"),
    (r"суға\s+секіру|прыжки\s+в\s+воду", "Прыжки в воду"),
    (r"ауыр\s+атлетика|тяж[её]л[ая].*атлетика", "Тяжёлая атлетика"),
    (r"жеңіл\s+атлетика|л[её]гк[ая].*атлетика", "Лёгкая атлетика"),
    (r"нысана\s+көздеу|стенд\s+ату", "Стрельба"),
    (r"садақ\s+ату", "Стрельба из лука"),
    (r"джиу.?джитсу", "Джиу-джитсу"),
    (r"дзюдо", "Дзюдо"),
    (r"таеквандо|таэквондо", "Таэквондо"),
    (r"карате", "Карате"),
    (r"гимнастик", "Гимнастика"),
    (r"велоспорт", "Велоспорт"),
    (r"баскетбол", "Баскетбол"),
    (r"волейбол", "Волейбол"),
)
SPORT_RE = [(re.compile(pat, re.I), sport) for pat, sport in SPORTS]
TITLE_END = re.compile(
    r"(?:[. ]+)(?:ПРЯМАЯ\s+ТРАНСЛЯЦИЯ(?:\s+ИЗ\s+.+)?|"
    r"ТІКЕЛЕЙ\s+ЭФИР|ТIКЕЛЕЙ\s+ЭФИР)\s*$",
    re.I,
)


def _load_book(data: bytes, filename: str):
    if not filename.casefold().endswith(".xlsx"):
        raise InvalidEPG("Для QAZSPORT/SPORT+ нужен оригинальный XLSX")
    if not data or len(data) > MAX_WORKBOOK_BYTES:
        raise InvalidEPG("Excel пуст или превышает 6 МБ")
    try:
        with ZipFile(BytesIO(data)) as package:
            members = package.infolist()
            if (len(members) > 512
                    or sum(member.file_size for member in members) > 40 * 1024 * 1024
                    or any(member.file_size > 30 * 1024 * 1024
                           or member.filename.casefold().endswith("vbaproject.bin")
                           for member in members)):
                raise InvalidEPG("Подозрительное содержимое XLSX")
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        raise InvalidEPG("Повреждённый XLSX") from exc
    if len(book.worksheets) > 20:
        book.close()
        raise InvalidEPG("Слишком много листов")
    return book


def _identity(book) -> str:
    # Only explicit top-of-grid station identity is evidence of a provider.
    head = []
    for sheet in book.worksheets[:5]:
        for row in sheet.iter_rows(
            min_row=1, max_row=min(sheet.max_row, 10),
            min_col=1, max_col=min(sheet.max_column, 8),
            values_only=True,
        ):
            head.extend(str(v or "")[:300] for v in row if isinstance(v, str))
    identity = " ".join(head).casefold()
    is_qaz = bool(re.search(r"\bqazsport\b", identity))
    is_plus = bool(re.search(r"\bsport\s*(?:plus|\+)\s*qazaqstan\b", identity))
    if is_qaz and is_plus:
        raise InvalidEPG("В заголовке Excel противоречивые телеканалы")
    return "QAZSPORT HD" if is_qaz else "SPORT+ Qazaqstan" if is_plus else ""


def probe_official_channel(data: bytes, filename: str) -> str:
    if not filename.casefold().endswith(".xlsx"):
        return ""
    book = _load_book(data, filename)
    try:
        return _identity(book)
    finally:
        book.close()


def _sport(raw: str) -> str:
    return next((sport for rx, sport in SPORT_RE if rx.search(raw)), "")


def _verified_programme(raw: str, channel: str) -> tuple[str, str] | None:
    # A programme being in a "transmission" genre is never LIVE evidence.
    if not DIRECT.search(raw) or NOT_A_MATCH.search(raw):
        return None
    if channel == "QAZSPORT HD" and re.search(r"барыс|barys", raw, re.I):
        return None
    sport = _sport(raw)
    if not sport:
        return None
    cleaned = TITLE_END.sub("", raw).strip(" .,")
    if not cleaned or event_is_editorial_or_replay({
        "title": cleaned, "raw_title": raw, "sport": sport,
    }):
        return None
    return sport, cleaned


def _day_year(day: int, month: int, reference: date) -> date:
    # SPORT+ headings omit the year. Bound inference to the receipt week.
    options = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            target = date(year, month, day)
        except ValueError:
            continue
        if -21 <= (target - reference).days <= 25:
            options.append(target)
    if len(options) != 1:
        raise InvalidEPG("Год недели SPORT+ нельзя однозначно определить")
    return options[0]


def _base_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date() if value.year >= 2000 else None
    if isinstance(value, date):
        return value
    return None


def _make_event(channel: str, start: datetime, raw: str,
                duration: int | None, sport: str, title: str) -> dict:
    source = OFFICIAL_SOURCES[channel]
    item = {
        "source": source, "source_url": "",
        "provider_source": "supplier_excel",
        "channel": channel, "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"), "title": title,
        "raw_title": raw, "sport": sport,
        "tournament": "",
        "is_sport_event": True, "is_live": True,
        "is_live_broadcast": True, "live_state": "live",
        "live_evidence_method": "provider_live_text",
        "live_evidence_value": "explicit LIVE text in official XLSX",
        "source_timezone": "Asia/Almaty",
        "source_start_at": start.isoformat(),
    }
    if duration is not None and 1 <= duration <= 8 * 60:
        end = start + timedelta(minutes=duration)
        item.update({
            "estimated_broadcast_end_date": end.date().isoformat(),
            "estimated_broadcast_end": end.strftime("%H:%M"),
            "end_estimation_method": "provider_duration",
        })
    return item


def parse_official_epg(data: bytes, filename: str, *,
                       today: date | None = None) -> ParsedEPG:
    filename = PurePath(filename.replace("\\", "/")).name
    book = _load_book(data, filename)
    try:
        channel = _identity(book)
        if not channel:
            raise InvalidEPG("Нет заголовка QAZSPORT или SPORT PLUS QAZAQSTAN")
        reference = today or datetime.now(KZ_TIMEZONE).date()
        all_programmes = 0
        days: set[str] = set()
        events: dict[tuple[str, str, str], dict] = {}
        for sheet in book.worksheets:
            if sheet.max_row > MAX_SHEET_ROWS or sheet.max_column > 32:
                raise InvalidEPG("Лист превышает лимиты программы")
            if channel == "SPORT+ Qazaqstan":
                current_day = None
                previous = None
                rollover = 0
                for row in sheet.iter_rows(min_col=1, max_col=4, values_only=True):
                    _, clock, title, duration = (list(row) + [None] * 4)[:4]
                    if isinstance(title, str) and (match := DAY.search(title)):
                        current_day = _day_year(
                            int(match.group(1)),
                            KZ_MONTHS[match.group(2).casefold()],
                            reference,
                        )
                        days.add(current_day.isoformat())
                        previous = None
                        rollover = 0
                        continue
                    if current_day is None or not isinstance(title, str):
                        continue
                    minutes = _clock_minutes(clock)
                    if minutes is None or minutes < 0:
                        continue
                    if previous is not None and previous >= 18 * 60 and minutes % 1440 < 6 * 60:
                        rollover = max(rollover, 1)
                    previous = minutes % 1440
                    start = (
                        datetime.combine(current_day, time.min, tzinfo=KZ_TIMEZONE)
                        + timedelta(minutes=minutes % 1440, days=max(minutes // 1440, rollover))
                    )
                    all_programmes += 1
                    parsed = _verified_programme(title, channel)
                    if parsed:
                        sport, name = parsed
                        event = _make_event(channel, start, title,
                                            _clock_minutes(duration), sport, name)
                        key = (event["date"], event["time"], event["title"].casefold())
                        events[key] = event
                        days.add(event["date"])
            else:
                previous_start = None
                for row in sheet.iter_rows(min_col=1, max_col=8, values_only=True):
                    cells = (list(row) + [None] * 8)[:8]
                    day = _base_date(cells[0])
                    minutes = _clock_minutes(cells[1])
                    title = cells[2]
                    if day is None or minutes is None or minutes < 0 or not isinstance(title, str):
                        continue
                    start = (
                        datetime.combine(day, time.min, tzinfo=KZ_TIMEZONE)
                        + timedelta(minutes=minutes)
                    )
                    if (previous_start and start < previous_start
                            and day == previous_start.date() - timedelta(days=1)
                            and start.hour < 6):
                        start += timedelta(days=1)
                    elif (previous_start and start < previous_start
                          and day == previous_start.date()
                          and start.hour < 6 and previous_start.hour >= 18):
                        start += timedelta(days=1)
                    previous_start = start
                    days.add(start.date().isoformat())
                    all_programmes += 1
                    parsed = _verified_programme(title, channel)
                    if parsed:
                        sport, name = parsed
                        event = _make_event(channel, start, title,
                                            _clock_minutes(cells[3]), sport, name)
                        events[(event["date"], event["time"], event["title"].casefold())] = event
        if not days or all_programmes < 1:
            raise InvalidEPG("Не распознаны датированные строки программы")
        return ParsedEPG(
            channel=channel, filename=filename,
            content_hash=hashlib.sha256(channel.encode() + b"\0" + data).hexdigest(),
            scope_dates=tuple(sorted(days)),
            all_programmes=all_programmes,
            events=tuple(sorted(events.values(), key=lambda e: (e["date"], e["time"]))),
        )
    finally:
        book.close()
