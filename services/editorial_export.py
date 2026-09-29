"""Working 25-column OTT export, preserving previously edited template records.

Only supplied KZ translations are exported as approved. An unknown translation
is left blank for editorial review; Russian text is never silently copied.
"""
from __future__ import annotations

from copy import copy
from datetime import datetime, timedelta
import hashlib
import re
import unicodedata

from openpyxl import Workbook

from services.schedule_merge import normalize_match_text

HEADERS = (
    "Приоритет", "date", "time", "Вид спорта", "Турнир",
    "Событие / матч", "Канал", "Дата начала", "Дата окончания",
    "name_live_ru", "name_live_kz", "name_date_ru", "name_date_kz",
    "Доп. шлейфы", "ID эфира", "ID анонса", "sport",
    "name_rec_ru", "name_rec_kz", "team1_ru", "team1_kz",
    "team2_ru", "team2_kz", "subtitle_ru", "subtitle_kz",
)
MATCH_SPORTS = frozenset({
    "футбол", "хоккей", "баскетбол", "волейбол", "гандбол",
    "регби", "футзал", "американский футбол", "водное поло",
})
STAGE_RE = re.compile(
    r"^(?:квалификац(?:ия|ии)|гонка|спринт|практика\s*\d*|"
    r"свободная практика\s*\d*|финал|полуфинал(?:\s*\d+)?|"
    r"четвертьфинал(?:\s*\d+)?|1/\d+ финала|матч\s*\d+|"
    r"main card|prelims?|предварительн(?:ый|ые) кард)$", re.I
)
TRAILING_CARD_RE = re.compile(
    r"\s+[-–]\s+(?:main card|prelims?|предварительн(?:ый|ые) кард)\s*$",
    re.I,
)
SPORT_CODES = {
    "футбол": "FBL", "хоккей": "HKY", "баскетбол": "BSK",
    "волейбол": "VBL", "теннис": "TNS", "бокс": "BOX",
    "мма": "MMA", "снукер": "SNK", "мотоспорт": "MTR",
    "формула-1": "F1", "фигурное катание": "FIG",
    "дзюдо": "JDO", "футзал": "FTS",
}
CYRILLIC = {
    "А":"A","Б":"B","В":"V","Г":"G","Д":"D","Е":"E","Ё":"E","Ж":"ZH",
    "З":"Z","И":"I","Й":"Y","К":"K","Л":"L","М":"M","Н":"N",
    "О":"O","П":"P","Р":"R","С":"S","Т":"T","У":"U","Ф":"F",
    "Х":"H","Ц":"TS","Ч":"CH","Ш":"SH","Щ":"SCH","Ъ":"","Ы":"Y",
    "Ь":"","Э":"E","Ю":"YU","Я":"YA","Ә":"A","Ғ":"G","Қ":"K",
    "Ң":"N","Ө":"O","Ұ":"U","Ү":"U","Һ":"H","І":"I",
}
TRANSLATE = str.maketrans(CYRILLIC)


class InvalidTemplate(ValueError):
    pass


def validate_template(workbook: Workbook) -> None:
    if not workbook.worksheets:
        raise InvalidTemplate("Шаблон не содержит листов")
    sheet = workbook.worksheets[0]
    actual = tuple(sheet.cell(1, col).value for col in range(1, 26))
    if actual != HEADERS:
        raise InvalidTemplate(
            "Неверный шаблон. Требуются ровно 25 исходных заголовков "
            "в утверждённом порядке."
        )
    if any(int(str(rng.min_row)) <= sheet.max_row and rng.max_row >= 2
           for rng in sheet.merged_cells.ranges):
        raise InvalidTemplate("В основной области шаблона есть объединённые ячейки")
    if any(sheet.cell(row, col).data_type == "f"
           for row in range(2, sheet.max_row + 1)
           for col in range(1, 26)):
        raise InvalidTemplate(
            "В основных строках шаблона есть формулы. Для сохранения "
            "ссылок нужна отдельная редакторская настройка."
        )


def _key(event_date, sport, title) -> tuple[str, str, str]:
    return (str(event_date), normalize_match_text(str(sport)),
            normalize_match_text(str(title)))


def _existing_date(values: list) -> str:
    date_cell, start_cell = values[1], values[7]
    if isinstance(start_cell, datetime):
        return (start_cell + timedelta(minutes=10)).date().isoformat()
    if isinstance(date_cell, str):
        # A date without a year cannot establish identity. Do not fabricate it.
        match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", date_cell.strip())
        if match:
            day, month, year = map(int, match.groups())
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                pass
    return ""


def _existing_sort(values: list, original_index: int):
    start_cell = values[7]
    if isinstance(start_cell, datetime):
        return start_cell + timedelta(minutes=10)
    # Ambiguous old rows keep their original relative positions.
    return None


def _slug(value: str) -> str:
    value = str(value or "").upper().translate(TRANSLATE)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]+", "_", value)).strip("_")


def _participants(sport: str, title: str) -> tuple[str, str]:
    if sport.casefold() not in MATCH_SPORTS:
        return "", ""
    candidate = str(title or "").strip()
    # Combat listings often include a series prefix and a trailing card type:
    # "UFC 332: Силва - Ван - Main Card".
    if ":" in candidate and sport.casefold() in {"мма", "бокс"}:
        candidate = candidate.rsplit(":", 1)[1].strip()
    candidate = TRAILING_CARD_RE.sub("", candidate).strip()
    pair = re.split(r"\s+[-–]\s+", candidate, maxsplit=1)
    if len(pair) != 2:
        return "", ""
    left, right = pair[0].strip(), pair[1].strip()
    if not left or not right or STAGE_RE.fullmatch(right):
        return "", ""
    return left, right


def _editorial_fields(event: dict) -> tuple[str, str, str]:
    """Match the owner's approved 25-column workbook conventions.

    Team sports -> TEAM 1/TEAM 2 are participants.
    Individual disciplines -> TEAM 1 is the competition/card identity,
    TEAM 2 stays empty and the session/stage belongs to SUBTITLE.
    """
    sport = str(event.get("sport") or "").strip()
    title = str(event.get("title") or "").strip()
    tournament = str(event.get("tournament") or "").strip()
    explicit_team1 = str(event.get("team1_ru") or "").strip()
    explicit_team2 = str(event.get("team2_ru") or "").strip()
    explicit_subtitle = str(event.get("subtitle_ru") or "").strip()
    if explicit_team1:
        return explicit_team1, explicit_team2, (
            explicit_subtitle or ". ".join(x for x in (sport, tournament) if x)
        )

    team1, team2 = _participants(sport, title)
    if team1:
        return team1.upper(), team2.upper(), (
            explicit_subtitle or ". ".join(x for x in (sport, tournament) if x)
        )

    # Backward-compatible raw Formula-1 form from older parsers:
    # "Гран-при Италии - Квалификация".
    if not tournament and sport.casefold() in {"автоспорт", "формула-1"}:
        pieces = re.split(r"\s+[-–]\s+", title)
        if len(pieces) >= 2 and STAGE_RE.fullmatch(pieces[-1].strip()):
            gp = " - ".join(part.strip() for part in pieces[:-1] if part.strip())
            gp = re.sub(r"^Гран-при\s+", "ГП ", gp, flags=re.I)
            return gp.upper(), "", "Формула 1. " + pieces[-1].strip()

    if tournament:
        team_label = tournament
        subtitle_prefix = sport

        grand_prix = re.search(r"Гран-при\s+(.+)$", tournament, re.I)
        if grand_prix and sport.casefold() in {"автоспорт", "мотоспорт"}:
            team_label = "ГП " + grand_prix.group(1).strip()
            if "формула" in tournament.casefold():
                subtitle_prefix = "Формула 1"
            elif "motogp" in tournament.casefold():
                subtitle_prefix = "MotoGP"

        detail = title
        if sport.casefold() == "велоспорт" and "." in title:
            first, rest = [x.strip() for x in title.split(".", 1)]
            if first and rest and first.casefold() not in tournament.casefold():
                team_label = tournament + ". " + first
                detail = rest

        if normalize_match_text(title) == normalize_match_text(tournament):
            detail = ""

        subtitle = explicit_subtitle or ". ".join(
            x for x in (subtitle_prefix, detail) if x
        )
        return team_label.upper(), "", subtitle

    return title.upper(), "", explicit_subtitle or sport


def _excel_safe(text: str) -> str:
    value = str(text or "")
    # Preserve literal user/source strings instead of allowing spreadsheet
    # formula interpretation from an untrusted event title.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _new_row(event: dict) -> list:
    start = datetime.fromisoformat(event["start_at"])
    platform_start = datetime.fromisoformat(event["platform_start_at"])
    end = datetime.fromisoformat(event["end_at"])
    if start.tzinfo:
        start = start.replace(tzinfo=None)
    if platform_start.tzinfo:
        platform_start = platform_start.replace(tzinfo=None)
    if end.tzinfo:
        end = end.replace(tzinfo=None)

    title, sport, tournament = (
        str(event["title"]), str(event["sport"]), str(event.get("tournament") or "")
    )
    datecode = start.strftime("%d%m%y")
    code = SPORT_CODES.get(sport.casefold(), _slug(sport)[:4] or "SPT")
    suffix = _slug(title)[:52] or hashlib.sha1(title.encode()).hexdigest()[:12].upper()
    slug = f"{datecode}_{code}_{suffix}"
    # Avoid two events with the same textual title on one date sharing IDs
    # when their competition is different.
    if tournament:
        suffix_hash = hashlib.sha1(tournament.encode()).hexdigest()[:6].upper()
        slug += "_" + suffix_hash
    team1, team2, subtitle = _editorial_fields(event)
    return [
        None, start.strftime("%d.%m"), start.strftime("%H:%M"),
        _excel_safe(sport), _excel_safe(tournament), _excel_safe(title),
        _excel_safe(str(event["channel"])),
        platform_start, end,
        slug + "_LIVE_RU", slug + "_LIVE_KZ",
        slug + "_SOON_RU", slug + "_SOON_KZ",
        "", "", "", _excel_safe(" ".join(x for x in (sport, tournament) if x)),
        slug + "_ARCH_RU", slug + "_ARCH_KZ",
        _excel_safe(team1), _excel_safe(str(event.get("team1_kz") or "")),
        _excel_safe(team2), _excel_safe(str(event.get("team2_kz") or "")),
        _excel_safe(subtitle), _excel_safe(str(event.get("subtitle_kz") or "")),
    ]



def _translation_key(value: str) -> str:
    # Intentionally strict. "Спартак" must not pick a women's or reserve
    # team through aggressive fuzzy matching.
    return " ".join(str(value or "").casefold().split()).replace("–", "-")


def _reuse_approved_translations(rows: list[dict], event: dict) -> dict:
    """Reuse only unambiguous TEAM/SUBTITLE translations from user's XLSX.

    The workbook itself stays private in GCS. A conflict between two
    existing KZ spellings results in no automatic translation.
    """
    dictionary: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        cells = row["values"]
        for category, ru_col, kz_col in (
            ("team", 19, 20), ("team", 21, 22), ("subtitle", 23, 24),
        ):
            source_ru, source_kz = cells[ru_col], cells[kz_col]
            if not isinstance(source_ru, str) or not isinstance(source_kz, str):
                continue
            key = _translation_key(source_ru)
            translated = source_kz.strip()
            if key and translated:
                dictionary.setdefault((category, key), set()).add(translated)

    enriched = dict(event)
    team1, team2, original_subtitle = _editorial_fields(event)
    for ru_field, kz_field, category, fallback in (
        ("team1_ru", "team1_kz", "team", team1),
        ("team2_ru", "team2_kz", "team", team2),
        ("subtitle_ru", "subtitle_kz", "subtitle", original_subtitle),
    ):
        if str(enriched.get(kz_field) or "").strip():
            continue
        source_ru = str(enriched.get(ru_field) or fallback)
        candidates = dictionary.get((category, _translation_key(source_ru)), set())
        if len(candidates) == 1:
            enriched[kz_field] = next(iter(candidates))
    return enriched


def build_working_xlsx(workbook: Workbook, events: list[dict]) -> Workbook:
    """Sort current + existing events, preserving every existing editorial field.

    The helper modifies a freshly loaded workbook only; the persisted source
    template is not changed by an export. All other sheets remain untouched.
    """
    validate_template(workbook)
    sheet = workbook.worksheets[0]
    styles = [copy(sheet.cell(2, col)._style) for col in range(1, 26)]
    original = []
    known_keys = set()
    for row in range(2, sheet.max_row + 1):
        cells = [sheet.cell(row, col) for col in range(1, 26)]
        values = [c.value for c in cells]
        if not any(value is not None and str(value).strip() for value in values):
            continue
        event_date = _existing_date(values)
        if event_date:
            known_keys.add(_key(event_date, values[3], values[5]))
        original.append({
            "values": values,
            "styles": [copy(c._style) for c in cells],
            "sort": _existing_sort(values, row),
            "original_index": row,
        })

    next_index = sheet.max_row + 1
    for event in events:
        event_date = str(event.get("date") or "")
        identity = _key(event_date, event.get("sport"), event.get("title"))
        if identity in known_keys:
            continue
        known_keys.add(identity)
        original.append({
            "values": _new_row(_reuse_approved_translations(
                [row for row in original if row["original_index"] <= sheet.max_row],
                event,
            )),
            "styles": [copy(style) for style in styles],
            "sort": datetime.fromisoformat(event["start_at"]).replace(tzinfo=None),
            "original_index": next_index,
        })
        next_index += 1

    # Only use inferred chronology where the template's original start
    # cell is a real datetime. Unresolved rows preserve source order.
    original.sort(key=lambda row: (
        row["sort"] is None,
        row["sort"] or datetime.max,
        row["original_index"],
    ))
    for n, item in enumerate(original, start=2):
        for col, (value, style) in enumerate(zip(item["values"], item["styles"]), 1):
            cell = sheet.cell(n, col)
            cell.value = value
            cell._style = copy(style)
        # Owner's approved workbook uses 60000, 59990, 59980 ... (step 10).
        sheet.cell(n, 1).value = 60000 - (n-2)*10
    return workbook
