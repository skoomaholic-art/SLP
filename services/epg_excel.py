"""Import genuine Setanta/QSport .xlsx EPGs without treating ordinary reruns as LIVE.

The importer is independent of Gmail transport: downloaded attachments and
an authorized manual upload use exactly the same verified parser.
No raw workbook bytes or email credentials are stored in git or SQLite.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from io import BytesIO
import json
from pathlib import PurePath
from zipfile import ZipFile, BadZipFile
import re
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook

from services.live_evidence import event_is_editorial_or_replay
from storage.database import SLPDatabase

KZ = ZoneInfo("Asia/Almaty")
MAX_WORKBOOK_BYTES = 6 * 1024 * 1024
MAX_SHEET_ROWS = 5000
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTH_PATTERN = "|".join(MONTHS)
DAY_RE = re.compile(r"\s*(\d{1,2})\s+(" + MONTH_PATTERN + r")(?:\s+(\d{4}))?\s*", re.I)
LIVE_RE = re.compile(r"^\s*LIVE\s*[\.:,\-]\s*", re.I)
SPORTS = {
    "футбол": "Футбол", "футзал": "Футзал", "баскетбол": "Баскетбол",
    "волейбол": "Волейбол", "пляжный волейбол": "Пляжный волейбол",
    "теннис": "Теннис", "хоккей": "Хоккей", "мма": "ММА",
    "mma": "ММА", "бокс": "Бокс", "дзюдо": "Дзюдо",
    "мотоспорт": "Мотоспорт", "формула 1": "Автоспорт",
    "формула-1": "Автоспорт", "снукер": "Снукер",
    "фигурное катание": "Фигурное катание",
    "киберспорт": "Киберспорт", "гандбол": "Гандбол",
    "регби": "Регби", "велоспорт": "Велоспорт",
    "легкая атлетика": "Лёгкая атлетика",
    "лёгкая атлетика": "Лёгкая атлетика",
    "женіл атлетика": "Лёгкая атлетика",
    "жеңіл атлетика": "Лёгкая атлетика",
    "гимнастика": "Гимнастика",
    "таэквондо": "Таэквондо", "карате": "Карате",
    "борьба": "Борьба", "күрес": "Борьба",
    "бильярд": "Бильярд", "гольф": "Гольф",
    "лыжные гонки": "Лыжный спорт", "биатлон": "Биатлон",
}
CHANNELS = {
    "setanta1": "SETANTA SPORTS 1",
    "setanta2": "SETANTA SPORTS 2",
    "setantakz": "SETANTA SPORTS KZ",
    "qleague": "Q LEAGUE",
    "qarena": "Q ARENA",
    "qfootball": "Q FOOTBALL",
}
SOURCE_KEY = {channel: "email_epg_" + key for key, channel in CHANNELS.items()}
# The two additional official supplier formats are intentionally separate
# from the six Setanta/QSport channels shown as mailbox coverage in the UI.
OFFICIAL_SOURCE_KEY = {
    "QAZSPORT HD": "email_epg_qazsport",
    "SPORT+ Qazaqstan": "email_epg_sportplus",
}


def source_key_for(channel: str) -> str:
    source = SOURCE_KEY.get(channel) or OFFICIAL_SOURCE_KEY.get(channel)
    if not source:
        raise InvalidEPG("Неизвестный телеканал поставщика")
    return source


def parse_supported_epg_channels(
    data: bytes, filename: str, *, today: date | None = None,
    context: str = "", confirmed_channel: str = "",
) -> tuple["ParsedEPG", ...]:
    """Route by the actual worksheet's channel label before email context.

    QAZSPORT and SPORT+ have provider-specific XLSX layouts and cannot be
    represented as a generic Setanta/QSport grid. Their first imports must
    always be approved by an editor in the Gmail flow.
    """
    from services.official_supplier_excel import (
        parse_official_epg, probe_official_channel,
    )

    official = probe_official_channel(data, filename)
    if official:
        if confirmed_channel and confirmed_channel != official:
            raise InvalidEPG("Подтверждённый канал противоречит заголовку Excel")
        other_filename_channels = _channel_matches(filename)
        if other_filename_channels and official not in other_filename_channels:
            raise InvalidEPG("Имя файла противоречит каналу внутри Excel")
        return (parse_official_epg(data, filename, today=today),)
    return parse_epg_xlsx_channels(
        data, filename, today=today, context=context,
        confirmed_channel=confirmed_channel,
    )



class InvalidEPG(ValueError):
    pass


@dataclass(frozen=True)
class ParsedEPG:
    channel: str
    filename: str
    content_hash: str
    scope_dates: tuple[str, ...]
    all_programmes: int
    events: tuple[dict[str, Any], ...]


def _channel_matches(text: str) -> set[str]:
    """Return only explicit channel identities, never provider-family guesses."""
    value = re.sub(r"\s+", " ", str(text or "").casefold())
    matches: set[str] = set()
    patterns = (
        (CHANNELS["setanta1"], (
            r"\bsetanta\s+sports\s*1\b",
            r"\bsetanta\s*1\s+kazakhstan\b",
        )),
        (CHANNELS["setanta2"], (
            r"\bsetanta\s+sports\s*2\b",
            r"\bsetanta\s*2\s+kazakhstan\b",
        )),
        (CHANNELS["setantakz"], (
            r"\bsetanta\s+qazaqstan\b",
            r"\bsetanta\s+kz\b",
            r"\bsetanta\s+sports\s+kz\b",
        )),
        (CHANNELS["qleague"], (
            r"\bq[\s_-]*league\b",
            r"\bq\s*sport[\s_-]*league\b",
            r"\bq\s*лига\b",
        )),
        (CHANNELS["qarena"], (
            r"\bq[\s_-]*arena\b",
            r"\bq\s*sport[\s_-]*arena\b",
            r"\bq\s*арена\b",
        )),
        (CHANNELS["qfootball"], (
            r"\bq[\s_-]*football\b",
            r"\bq\s*sport[\s_-]*football\b",
            r"\bq\s*футбол\b",
        )),
    )
    for channel, expressions in patterns:
        if any(re.search(expression, value, re.I) for expression in expressions):
            matches.add(channel)
    return matches


def _sheet_channels(sheet) -> set[str]:
    metadata = [str(sheet.title)]
    for row in sheet.iter_rows(
        min_row=1, max_row=min(sheet.max_row, 45),
        min_col=1, max_col=min(sheet.max_column, 8),
        values_only=True,
    ):
        for value in row:
            if isinstance(value, str) and value.strip():
                metadata.append(value[:300])
    return _channel_matches(" ".join(metadata))


def workbook_fingerprint(data: bytes, filename: str) -> str:
    """Hash workbook structure without retaining programme text.

    The fingerprint is deliberately based on sheet/layout signals rather than
    filenames or weekly fixture names. It may be used only after a user or a
    content-explicit import confirmed the corresponding channel.
    """
    if not data or len(data) > MAX_WORKBOOK_BYTES:
        raise InvalidEPG("Excel слишком большой или пуст")
    normalized = data
    if filename.casefold().endswith(".xls"):
        normalized = _legacy_xls_to_xlsx(data)
    try:
        workbook = load_workbook(BytesIO(normalized), read_only=True, data_only=True)
    except Exception as exc:
        raise InvalidEPG("Не удалось прочитать Excel для определения формата") from exc
    profiles = []
    try:
        if len(workbook.worksheets) > 20:
            raise InvalidEPG("Слишком много листов в Excel")
        current_year = datetime.now(KZ).year
        for sheet in workbook.worksheets:
            time_counts = [0] * 8
            date_counts = [0] * 8
            live_counts = [0] * 8
            text_counts = [0] * 8
            for row in sheet.iter_rows(
                min_row=1, max_row=min(sheet.max_row, 80),
                min_col=1, max_col=min(sheet.max_column, 8), values_only=True,
            ):
                for column, value in enumerate(row[:8]):
                    if _clock_minutes(value) is not None:
                        time_counts[column] += 1
                    elif isinstance(value, str) and _day_header(value, current_year):
                        date_counts[column] += 1
                    elif isinstance(value, str) and LIVE_RE.match(value):
                        live_counts[column] += 1
                    elif isinstance(value, str) and value.strip():
                        text_counts[column] += 1
            title_shape = re.sub(r"\d+", "#", " ".join(sheet.title.casefold().split()))
            profiles.append({
                "title": title_shape[:80],
                "columns": min(sheet.max_column, 32),
                "channel_markers": sorted(_sheet_channels(sheet)),
                "time_columns": [i + 1 for i, count in enumerate(time_counts) if count],
                "date_columns": [i + 1 for i, count in enumerate(date_counts) if count],
                "live_columns": [i + 1 for i, count in enumerate(live_counts) if count],
                "text_columns": [i + 1 for i, count in enumerate(text_counts) if count],
            })
    finally:
        workbook.close()
    canonical = json.dumps(profiles, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def detect_channel(filename: str, *, workbook=None, context: str = "") -> str:
    """Identify the station from independent evidence.

    Priority is workbook content, then the attachment filename, then e-mail
    context. A provider-family phrase such as "QSport" or "Setanta" alone is
    deliberately insufficient because one message can contain several grids.
    """
    evidence: list[tuple[str, set[str]]] = []
    file_matches = _channel_matches(filename)
    if file_matches:
        evidence.append(("имя файла", file_matches))

    if workbook is not None:
        content_matches: set[str] = set()
        for sheet in workbook.worksheets[:20]:
            content_matches.update(_sheet_channels(sheet))
        if content_matches:
            evidence.insert(0, ("содержимое Excel", content_matches))

    context_matches = _channel_matches(context)
    if context_matches:
        evidence.append(("контекст письма", context_matches))

    # Strongest source wins only when it identifies exactly one channel.
    for source, matches in evidence:
        if len(matches) == 1:
            candidate = next(iter(matches))
            # If a stronger workbook identity exists, a contradictory file
            # name/context must not silently relabel the attachment.
            stronger = evidence[0] if evidence else None
            if source != "содержимое Excel" and stronger and stronger[0] == "содержимое Excel":
                workbook_matches = stronger[1]
                if len(workbook_matches) == 1 and candidate not in workbook_matches:
                    raise InvalidEPG(
                        "Имя/контекст письма противоречит телеканалу внутри Excel"
                    )
            return candidate
        if len(matches) > 1 and source == "содержимое Excel":
            raise InvalidEPG(
                "В одном Excel обнаружено несколько телеканалов: " +
                ", ".join(sorted(matches))
            )
    raise InvalidEPG(
        "Не удалось однозначно определить телеканал по содержимому Excel, "
        "имени файла или контексту письма"
    )


def _year(filename: str, today: date) -> int:
    explicit = re.search(r"\b(20\d{2})\b", filename)
    if explicit:
        return int(explicit.group(1))
    # Example: 29.09.26 - 05.10.26 in a weekly supplier filename.
    short = re.search(r"\b\d{1,2}[.]\d{1,2}[.](\d{2})\b", filename)
    if short:
        return 2000 + int(short.group(1))
    return today.year


_FILENAME_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})[.](\d{1,2})[.](20\d{2}|\d{2})(?!\d)"
)


def _filename_week_window(filename: str) -> tuple[date, date] | None:
    """Trust only a concrete, short period printed in the supplier filename.

    Some actual weekly Setanta files contain a forgotten worksheet from an
    unrelated month. Such rows must not enter the active schedule. One-day
    margins admit the supplier's previous/next-day overlaps and night slots.
    """
    dates: list[date] = []
    for day, month, year in _FILENAME_DATE_RE.findall(filename):
        parsed_year = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            dates.append(date(parsed_year, int(month), int(day)))
        except ValueError:
            return None
        if len(dates) == 2:
            break
    if len(dates) != 2:
        return None
    start, end = dates
    return (start, end) if 0 <= (end - start).days <= 14 else None


def _cross_year_filename_window(filename: str) -> tuple[date, date] | None:
    """Disambiguate Dec-Jan headers only with an explicit weekly period."""
    window = _filename_week_window(filename)
    return window if window and window[0].year != window[1].year else None


def _day_header(value: Any, year: int) -> date | None:
    if not isinstance(value, str):
        return None
    match = DAY_RE.fullmatch(value.strip())
    if not match:
        return None
    day, month, explicit_year = match.groups()
    try:
        return date(int(explicit_year) if explicit_year else year,
                    MONTHS[month.casefold()], int(day))
    except ValueError:
        return None


def _clock_minutes(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, timedelta):
        return round(value.total_seconds() / 60)
    if isinstance(value, (int, float)):
        if not 0 <= value < 4:  # reject Excel calendar serials
            return None
        return round(float(value) * 24 * 60)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,2}):([0-5]\d)\s*", value)
        if match:
            hours = int(match.group(1))
            if hours < 48:
                return hours * 60 + int(match.group(2))
    return None


def _text_parts(raw: str) -> tuple[str, str, str] | None:
    if not LIVE_RE.match(raw):
        return None
    title = " ".join(LIVE_RE.sub("", raw, count=1).split()).strip(" .")
    if not title:
        return None

    # Supplier uses both "Теннис. ..." and "Формула 1: ...".
    match = re.match(r"^([^.:]+)\s*[.:]\s*(.+)$", title)
    if not match:
        return None
    prefix, rest = match.group(1).strip(), match.group(2).strip()
    sport = SPORTS.get(prefix.casefold())
    if not sport:
        return None

    event = {
        "raw_title": title, "title": title,
        "sport": sport, "tournament": rest.split(",")[0].strip(),
    }
    if event_is_editorial_or_replay(event):
        return None

    # Formula 1 supplier form:
    # "Формула 1: Гран-при Бахрейна - Квалификация"
    if prefix.casefold() in ("формула 1", "формула-1"):
        pieces = re.split(r"\s+[-–]\s+", rest)
        if len(pieces) >= 2:
            stage = pieces[-1].strip()
            grand_prix = " - ".join(x.strip() for x in pieces[:-1] if x.strip())
            return sport, "Формула-1. " + grand_prix, stage
        return sport, "Формула-1. " + rest, rest

    # Tennis commonly arrives as "ATP 250 Ханчжоу: Финал".
    if sport == "Теннис" and ":" in rest:
        tournament, stage = [x.strip() for x in rest.rsplit(":", 1)]
        if tournament and stage:
            return sport, tournament, stage

    # Combat cards keep the numbered event as the card identity rather than
    # pretending the two fighters are TEAM 1/TEAM 2 in the OTT template.
    if sport in ("ММА", "Бокс") and ":" in rest:
        card, fight = [x.strip() for x in rest.split(":", 1)]
        series = re.match(r"^[A-Za-zА-Яа-яЁё+]+", card)
        tournament = series.group(0) if series else card
        display = card + ". " + re.sub(r"\s+[-–]\s+(Main Card|Prelims?)$", r". \1", fight, flags=re.I)
        return sport, tournament, display

    # Team sports usually finish with the fixture after comma-separated
    # competition metadata.
    segments = [x.strip() for x in rest.split(",")]
    tail = segments[-1] if segments else rest
    if len(segments) >= 2 and re.search(r"\s+[-–]\s+", tail):
        title_for_event = re.sub(r"\s+[–]\s+", " - ", tail)
        tournament = ", ".join(segments[:-1]).strip()
    else:
        title_for_event = rest.strip()
        tournament = segments[0] if segments else ""
    return sport, tournament, title_for_event


def _read_programs(sheet, channel: str, filename: str, year: int) -> tuple[list[dict], set[str], int]:
    """Accept two audited layouts: Setanta1 A/B, others B/C."""
    starts: list[dict] = []
    seen_dates: set[str] = set()
    current_day: date | None = None
    previous_minutes: int | None = None
    rollover = 0
    programmes = 0
    year_window = _cross_year_filename_window(filename)
    week_window = _filename_week_window(filename)
    layout = ("A", "B") if channel == CHANNELS["setanta1"] else ("B", "C")
    t_idx = 0 if layout[0] == "A" else 1
    title_idx = 1 if layout[1] == "B" else 2
    for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, MAX_SHEET_ROWS),
                               min_col=1, max_col=5, values_only=True):
        title_cell = row[title_idx]
        for candidate in (row[1], row[2]):
            header = _day_header(candidate, year)
            if header:
                # Supplier day headers may omit the year. A weekly filename
                # spanning Dec-Jan disambiguates even a separate January sheet.
                # Without an explicit filename range, infer rollover only
                # from a chronological December -> January transition here.
                match = DAY_RE.fullmatch(candidate.strip()) if isinstance(candidate, str) else None
                if match and match.group(3) is None:
                    if year_window:
                        start, end = year_window
                        possible = []
                        for candidate_year in (start.year, end.year):
                            try:
                                dated = date(candidate_year, header.month, header.day)
                            except ValueError:
                                continue
                            if start - timedelta(days=1) <= dated <= end + timedelta(days=1):
                                possible.append(dated)
                        if len(possible) == 1:
                            header = possible[0]
                    elif (current_day is not None and current_day.month == 12
                          and header.month == 1 and header.year <= current_day.year):
                        header = header.replace(year=current_day.year + 1)
                if week_window and not (
                    week_window[0] - timedelta(days=1)
                    <= header <= week_window[1] + timedelta(days=1)
                ):
                    # A stale old-month sheet in a current weekly XLSX is
                    # not a current event. Suppress its entire day section.
                    current_day = None
                    previous_minutes = None
                    rollover = 0
                    continue
                year = header.year
                current_day = header
                seen_dates.add(header.isoformat())
                previous_minutes = None
                rollover = 0
                break
        if current_day is None or not isinstance(title_cell, str):
            continue
        minutes = _clock_minutes(row[t_idx])
        if minutes is None:
            continue
        # Supplier programmes run from 06:00 to next-day 05:59. The Excel
        # serial sometimes wraps back to 0.20 after a preceding 1.08 row.
        base_minutes = minutes % 1440
        if previous_minutes is not None and minutes < 1440:
            if previous_minutes >= 18 * 60 and base_minutes < 6 * 60:
                rollover = max(rollover, 1)
        day_offset = max(minutes // 1440, rollover)
        absolute_minutes = day_offset * 1440 + base_minutes
        if previous_minutes is not None and absolute_minutes < previous_minutes:
            # An out-of-order spreadsheet row is not trustworthy as a slot.
            continue
        previous_minutes = absolute_minutes
        programmes += 1
        start = datetime.combine(current_day, time.min, tzinfo=KZ) + timedelta(minutes=absolute_minutes)
        starts.append({"start": start, "raw": " ".join(title_cell.split()),
                       "duration": _clock_minutes(row[3]) if title_idx == 2 else None})
    return starts, seen_dates, programmes


def _legacy_xls_to_xlsx(data: bytes) -> bytes:
    """Read a bounded BIFF .xls safely, retaining times and channel evidence.

    This is a file-format adapter, not a claim that every supplier's
    worksheet layout is supported. Unrecognized layouts still fail closed.
    """
    try:
        import xlrd
    except ImportError as exc:
        raise InvalidEPG("Поддержка .xls не установлена") from exc
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
    except (ValueError, OSError, TypeError, xlrd.XLRDError) as exc:
        raise InvalidEPG("Повреждённый или неподдерживаемый файл .xls") from exc
    workbook = Workbook()
    workbook.remove(workbook.active)
    try:
        if book.nsheets < 1 or book.nsheets > 20:
            raise InvalidEPG("Недопустимое количество листов в .xls")
        cell_budget = 100_000
        for index in range(book.nsheets):
            old = book.sheet_by_index(index)
            if old.nrows > MAX_SHEET_ROWS or old.ncols > 32:
                raise InvalidEPG("Лист .xls превышает допустимый размер")
            cell_budget -= old.nrows * old.ncols
            if cell_budget < 0:
                raise InvalidEPG("Файл .xls содержит слишком много ячеек")
            sheet = workbook.create_sheet(title=old.name[:31] or f"Лист{index + 1}")
            for row in range(old.nrows):
                for col in range(old.ncols):
                    cell = old.cell(row, col)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK,
                                      xlrd.XL_CELL_ERROR):
                        continue
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            parsed = xlrd.xldate_as_datetime(cell.value, book.datemode)
                        except (ValueError, OverflowError) as exc:
                            raise InvalidEPG("Некорректная дата в .xls") from exc
                        value = parsed.time() if 0 <= cell.value < 1 else parsed
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(cell.value)
                    else:
                        value = cell.value
                    sheet.cell(row + 1, col + 1, value)
        result = BytesIO()
        workbook.save(result)
        if result.tell() > MAX_WORKBOOK_BYTES:
            raise InvalidEPG("Преобразованный .xls превышает 6 МБ")
        return result.getvalue()
    finally:
        workbook.close()
        book.release_resources()


def parse_epg_xlsx(
    data: bytes, filename: str, *, today: date | None = None,
    context: str = "", only_channel: str = ""
) -> ParsedEPG:
    # The supplied filename is untrusted, even after successful MIME parsing.
    filename = PurePath(filename.replace("\\", "/")).name
    extension = filename.casefold()
    if not (extension.endswith(".xlsx") or extension.endswith(".xls")):
        raise InvalidEPG("Поддерживаются только файлы .xlsx и .xls")
    if not data or len(data) > MAX_WORKBOOK_BYTES:
        raise InvalidEPG("Размер Excel превышает 6 МБ или файл пуст")
    if len(filename) > 180 or not filename.strip():
        raise InvalidEPG("Недопустимое имя вложения")
    raw_data = data
    if extension.endswith(".xls"):
        data = _legacy_xls_to_xlsx(data)
    try:
        with ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if (len(infos) > 512
                    or sum(item.file_size for item in infos) > 40 * 1024 * 1024
                    or any(item.file_size > 30 * 1024 * 1024 for item in infos)
                    or any(item.filename.casefold().endswith("vbaproject.bin")
                           for item in infos)):
                raise InvalidEPG("Подозрительное содержимое XLSX")
    except BadZipFile as exc:
        raise InvalidEPG("Повреждённый XLSX") from exc
    today = today or datetime.now(KZ).date()
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise InvalidEPG("Не удалось прочитать XLSX") from exc
    try:
        if only_channel:
            if only_channel not in CHANNELS.values():
                raise InvalidEPG("Неподдерживаемый телеканал XLSX")
            channel = only_channel
        else:
            channel = detect_channel(filename, workbook=workbook, context=context)
    except Exception:
        workbook.close()
        raise
    rows = []
    days: set[str] = set()
    count = 0
    try:
        year = _year(filename, today)
        for sheet in workbook.worksheets:
            if only_channel:
                identities = _sheet_channels(sheet)
                if identities and identities != {only_channel}:
                    continue
            slot_rows, sheet_days, programmes = _read_programs(sheet, channel, filename, year)
            days.update(sheet_days)
            count += programmes
            rows.extend(slot_rows)
    finally:
        workbook.close()
    if not days or count < 1:
        raise InvalidEPG("В файле не найдены датированные строки программы")
    # Sort without silently merging different programmes; next chronological
    # programme is an EPG end boundary, not proof of an event's actual end.
    rows.sort(key=lambda row: row["start"])
    events: list[dict] = []
    for index, item in enumerate(rows):
        parts = _text_parts(item["raw"])
        if parts is None:
            continue
        sport, tournament, title = parts
        start = item["start"]
        next_start = rows[index + 1]["start"] if index + 1 < len(rows) else None
        duration = item["duration"]
        end = None
        method = None
        if duration is not None and 1 <= duration <= 8 * 60:
            end = start + timedelta(minutes=duration)
            method = "provider_duration"
        elif next_start is not None and 0 < (next_start - start).total_seconds() <= 8 * 3600:
            end = next_start
            method = "next_program"
        record = {
            "source": SOURCE_KEY[channel], "source_url": "",
            "provider_source": "supplier_excel",
            "channel": channel, "date": start.date().isoformat(),
            "time": start.strftime("%H:%M"), "title": title,
            "raw_title": item["raw"], "sport": sport,
            "tournament": tournament, "is_sport_event": True,
            "is_live": True, "is_live_broadcast": True,
            "live_state": "live", "live_evidence_method": "provider_live_text",
            "live_evidence_value": "LIVE. in supplier XLSX",
            "source_timezone": "Asia/Almaty",
            "source_start_at": start.isoformat(),
        }
        if end is not None and method:
            record["estimated_broadcast_end_date"] = end.date().isoformat()
            record["estimated_broadcast_end"] = end.strftime("%H:%M")
            record["end_estimation_method"] = method
        events.append(record)
    # XLSX may repeat the same channel event on multiple sheets; preserve
    # different real start times and real channel identities.
    unique = {
        (e["date"], e["time"], e["channel"], e["title"].casefold()): e
        for e in events
    }
    return ParsedEPG(channel=channel, filename=filename,
                     content_hash=hashlib.sha256(channel.encode() + b"\0" + raw_data).hexdigest(),
                     scope_dates=tuple(sorted(days | {e["date"] for e in unique.values()})),
                     all_programmes=count,
                     events=tuple(unique.values()))


def parse_epg_xlsx_channels(
    data: bytes, filename: str, *, today: date | None = None,
    context: str = "", confirmed_channel: str = "",
) -> tuple[ParsedEPG, ...]:
    """Split an explicitly labelled multi-station workbook into channels.

    Never infer a station from adjacent sheets, titles alone, or a family name.
    Ambiguous sheets must be reviewed rather than silently misattributed.
    """
    if not filename.casefold().endswith((".xlsx", ".xls")):
        raise InvalidEPG("Поддерживаются только .xlsx и .xls")
    if not data or len(data) > MAX_WORKBOOK_BYTES:
        raise InvalidEPG("Excel слишком большой или пуст")
    # Read BIFF only once; both sheet identification and event parsing use
    # the same normalized workbook. Identity remains the ORIGINAL file hash.
    original_data = data
    if filename.casefold().endswith(".xls"):
        data = _legacy_xls_to_xlsx(data)
    virtual_filename = filename[:-4] + ".xlsx" if filename.casefold().endswith(".xls") else filename
    def from_original(item: ParsedEPG) -> ParsedEPG:
        return replace(
            item, filename=filename,
            content_hash=hashlib.sha256(
                item.channel.encode() + b"\0" + original_data
            ).hexdigest(),
        )
    try:
        with ZipFile(BytesIO(data)) as archive:
            info = archive.infolist()
            if (len(info) > 512 or
                    sum(item.file_size for item in info) > 40 * 1024 * 1024 or
                    any(item.file_size > 30 * 1024 * 1024 for item in info)):
                raise InvalidEPG("Подозрительный XLSX")
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        raise InvalidEPG("Повреждённый XLSX") from exc
    try:
        identities = [_sheet_channels(sheet) for sheet in workbook.worksheets]
        all_channels: set[str] = set().union(*identities) if identities else set()
        if confirmed_channel:
            if confirmed_channel not in CHANNELS.values():
                raise InvalidEPG("Справочник форматов содержит неизвестный канал")
            if all_channels and all_channels != {confirmed_channel}:
                raise InvalidEPG(
                    "Подтверждённый формат противоречит каналу внутри Excel"
                )
        if len(all_channels) == 1:
            # A named Setanta/Q channel next to an unlabelled but populated
            # regional sheet is NOT proof they belong to the same channel.
            # The former implementation imported both sheets under one
            # station, silently misattributing the second schedule.
            known_channel = next(iter(all_channels))
            year = _year(filename, today or datetime.now(KZ).date())
            for sheet, identity in zip(workbook.worksheets, identities):
                if identity:
                    continue
                _, _, programmes = _read_programs(
                    sheet, known_channel, filename, year,
                )
                if programmes:
                    raise InvalidEPG(
                        "В книге есть лист с программой без указания канала: "
                        + str(sheet.title)[:70] + ". Нужна проверка."
                    )
    finally:
        workbook.close()
    if confirmed_channel:
        return (from_original(parse_epg_xlsx(
            data, virtual_filename, today=today, context=context,
            only_channel=confirmed_channel,
        )),)
    if len(all_channels) <= 1:
        return (from_original(parse_epg_xlsx(data, virtual_filename, today=today, context=context)),)
    if not all(len(channels) == 1 for channels in identities):
        raise InvalidEPG(
            "В Excel несколько каналов, но не каждый лист однозначно размечен"
        )
    return tuple(
        from_original(parse_epg_xlsx(
            data, virtual_filename, today=today, context=context,
            only_channel=channel
        ))
        for channel in sorted(all_channels)
    )


def initialize_epg_imports(database: SLPDatabase) -> None:
    with database._connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS epg_imports (
                file_hash TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                filename TEXT NOT NULL,
                first_date TEXT NOT NULL,
                last_date TEXT NOT NULL,
                programme_count INTEGER NOT NULL,
                live_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_epg_imports_channel
                ON epg_imports(channel, imported_at DESC);
            CREATE TABLE IF NOT EXISTS epg_import_days (
                file_hash TEXT NOT NULL REFERENCES epg_imports(file_hash),
                scope_date TEXT NOT NULL,
                PRIMARY KEY(file_hash, scope_date)
            );
        """)


def imported_epg_status(database: SLPDatabase, *, first: date, last: date) -> list[dict]:
    """Report real per-day coverage across all accepted supplier files.

    A newer partial/correction file does not invalidate the remaining days
    from a previous accepted file. Per-channel provenance stays intact.
    """
    initialize_epg_imports(database)
    result = []
    with database._connect() as conn:
        for channel in CHANNELS.values():
            latest = conn.execute(
                "SELECT * FROM epg_imports WHERE channel=? "
                "ORDER BY imported_at DESC, rowid DESC LIMIT 1", (channel,)
            ).fetchone()
            if latest is None:
                result.append({"channel": channel, "status": "missing",
                               "coverage": [], "live_events": 0})
                continue
            days = [record[0] for record in conn.execute(
                "SELECT DISTINCT d.scope_date FROM epg_import_days d "
                "JOIN epg_imports i ON d.file_hash=i.file_hash "
                "WHERE i.channel=? ORDER BY d.scope_date", (channel,),
            ).fetchall()]
            covered = [day for day in days
                       if first.isoformat() <= day <= last.isoformat()]
            state = ("ready" if len(covered) == (last - first).days + 1 else
                     "partial" if covered else "outdated")
            result.append({
                "channel": channel, "status": state,
                "coverage": days,
                "live_events": sum(len(database.load_active_source_snapshot(
                    SOURCE_KEY[channel], day)) for day in covered),
                "filename": latest["filename"], "imported_at": latest["imported_at"],
            })
    return result



def imported_official_epg_status(database: SLPDatabase, *,
                                 first: date, last: date) -> dict[str, dict]:
    """Website channels may ALSO have approved independent supplier XLSX.

    Enrich their existing channel cards instead of inventing a 15th/16th
    channel or hiding website collection failures.
    """
    initialize_epg_imports(database)
    result = {}
    with database._connect() as conn:
        for channel, source in OFFICIAL_SOURCE_KEY.items():
            latest = conn.execute(
                "SELECT filename,imported_at FROM epg_imports WHERE channel=? "
                "ORDER BY imported_at DESC, rowid DESC LIMIT 1",
                (channel,),
            ).fetchone()
            if latest is None:
                continue
            coverage = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT days.scope_date FROM epg_import_days days "
                    "JOIN epg_imports imports ON days.file_hash=imports.file_hash "
                    "WHERE imports.channel=? AND days.scope_date BETWEEN ? AND ? "
                    "ORDER BY days.scope_date",
                    (channel, first.isoformat(), last.isoformat()),
                ).fetchall()
            ]
            result[channel] = {
                "filename": latest["filename"],
                "imported_at": latest["imported_at"],
                "coverage": coverage,
                "live_events": sum(
                    len(database.load_active_source_snapshot(source, day))
                    for day in coverage
                ),
            }
    return result

def preview_parsed_epg(database: SLPDatabase, parsed: ParsedEPG) -> dict:
    """Compare one real supplier file to accepted rows without writing.

    A fixture disappearing from a later EPG is NOT a confirmed cancellation.
    A one-to-one changed kickoff is a candidate reschedule, not a source fact.
    Any ambiguous duplicate titles remain separate for editorial review.
    """
    from services.schedule_merge import normalize_match_text

    source = source_key_for(parsed.channel)
    existing: list[dict] = []
    for day in parsed.scope_dates:
        existing.extend(database.load_active_source_snapshot(source, day))

    def identity(event: dict) -> tuple:
        return (
            str(event.get("date") or ""),
            normalize_match_text(str(event.get("title") or "")),
            normalize_match_text(str(event.get("sport") or "")),
            normalize_match_text(str(event.get("tournament") or "")),
        )

    current_by_key: dict[tuple, list] = defaultdict(list)
    new_by_key: dict[tuple, list] = defaultdict(list)
    for event in existing:
        current_by_key[identity(event)].append(event)
    for event in parsed.events:
        new_by_key[identity(event)].append(event)
    changes = []
    counts = {"new": 0, "time_changed": 0, "missing_from_update": 0,
              "unchanged": 0, "ambiguous": 0}
    for key in sorted(set(current_by_key) | set(new_by_key)):
        before, after = current_by_key.get(key, []), new_by_key.get(key, [])
        old_times = sorted(str(e.get("time") or "") for e in before)
        new_times = sorted(str(e.get("time") or "") for e in after)
        if old_times == new_times:
            counts["unchanged"] += len(after)
            continue
        if len(before) == len(after) == 1:
            kind = "time_changed"
        elif before and after:
            kind = "ambiguous"
        elif before:
            kind = "missing_from_update"
        else:
            kind = "new"
        counts[kind] += max(len(before), len(after))
        if len(changes) < 100:
            changes.append({
                "kind": kind, "date": key[0],
                "title": after[0]["title"] if after else before[0]["title"],
                "before_times": old_times, "after_times": new_times,
                "note": (
                    "Исчезновение из EPG не подтверждает отмену"
                    if kind == "missing_from_update" else
                    "Нужна проверка: одинаковое событие встречается несколько раз"
                    if kind == "ambiguous" else
                    "Новое подтверждённое время, проверьте перенос"
                    if kind == "time_changed" else
                    "Новое подтверждённое LIVE событие"
                ),
            })
    return {
        "filename": parsed.filename,
        "channel": parsed.channel,
        "dates": list(parsed.scope_dates),
        "current_count": len(existing),
        "new_count": len(parsed.events),
        "counts": counts, "changes": changes,
        "changes_truncated": max(
            0, sum(counts[k] for k in counts if k != "unchanged") - len(changes)
        ),
    }


def import_parsed_epg(database: SLPDatabase, parsed: ParsedEPG) -> dict:
    """Idempotent on attachment SHA; preserve other channels and unrelated days."""
    initialize_epg_imports(database)
    with database._connect() as conn:
        exists = conn.execute("SELECT 1 FROM epg_imports WHERE file_hash=?",
                              (parsed.content_hash,)).fetchone()
    if exists:
        return {"status": "already_imported", "channel": parsed.channel,
                "live_events": len(parsed.events), "dates": list(parsed.scope_dates)}
    by_day = defaultdict(list)
    for event in parsed.events:
        by_day[event["date"]].append(event)
    source = source_key_for(parsed.channel)
    # An empty date in an update is not evidence that every earlier LIVE
    # broadcast vanished. Preserve the last-good day and flag it for review.
    accepted_days: list[str] = []
    held_days: list[str] = []
    for day in parsed.scope_dates:
        day_events = by_day.get(day, [])
        if not day_events and database.load_active_source_snapshot(source, day):
            held_days.append(day)
            continue
        database.upsert_source_snapshot(
            run_id="epg:" + parsed.content_hash[:20],
            source=source, scope_date=day, events=day_events,
            preserve_editorial=True,
        )
        accepted_days.append(day)
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO epg_imports(file_hash,channel,filename,first_date,last_date,"
            "programme_count,live_count,imported_at) VALUES(?,?,?,?,?,?,?,?)",
            (parsed.content_hash, parsed.channel, parsed.filename,
             parsed.scope_dates[0], parsed.scope_dates[-1],
             parsed.all_programmes, len(parsed.events),
             datetime.now(KZ).isoformat()),
        )
        conn.executemany(
            "INSERT INTO epg_import_days(file_hash,scope_date) VALUES(?,?)",
            [(parsed.content_hash, day) for day in accepted_days],
        )
    return {"status": "partial_review" if held_days else "imported",
            "channel": parsed.channel, "live_events": len(parsed.events),
            "dates": list(accepted_days), "held_dates": held_days}
