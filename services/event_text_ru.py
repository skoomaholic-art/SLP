"""Russian editorial normalization for sports event labels.

Source payloads stay untouched. This module only normalizes the presentation
layer so every channel is shown in one Russian-language style.
"""
from __future__ import annotations

from functools import lru_cache
import re


_BOOKMAKER_RE = re.compile(
    r"(?iu)\b(?:"
    r"фонбет|fonbet|betboom|бетбум|betcity|бетсити|"
    r"winline|винлайн|parimatch|1xbet|olimpbet|олимпбет|"
    r"лига\s+ставок|букмекер(?:ская|ские|ский|ы)?|"
    r"ставки|bet|бет"
    r")\b[\s:._-]*"
)

_KZ_TRANSLIT = str.maketrans({
    "ә": "а", "Ә": "А",
    "ғ": "г", "Ғ": "Г",
    "қ": "к", "Қ": "К",
    "ң": "н", "Ң": "Н",
    "ө": "о", "Ө": "О",
    "ұ": "у", "Ұ": "У",
    "ү": "у", "Ү": "У",
    "і": "и", "І": "И",
    "һ": "х", "Һ": "Х",
})

_PHRASES = (
    (r"\bУЕФА\s+Ұлттар\s+Лигасы\b", "Лига наций УЕФА"),
    (r"\bUEFA\s+Nations\s+League\b", "Лига наций УЕФА"),
    (r"\bNations\s+League\b", "Лига наций"),
    (r"\bҰлттар\s+Лигасы\b", "Лига наций"),
    (r"\bUEFA\s+Champions\s+League\b", "Лига чемпионов УЕФА"),
    (r"\bChampions\s+League\b", "Лига чемпионов"),
    (r"\bUEFA\s+Europa\s+League\b", "Лига Европы УЕФА"),
    (r"\bEuropa\s+League\b", "Лига Европы"),
    (r"\bConference\s+League\b", "Лига конференций УЕФА"),
    (r"\bPremier\s+League\b", "Премьер-лига"),
    (r"\bWorld\s+Cup\b", "Чемпионат мира"),
    (r"\bAsian\s+Games\b", "Азиатские игры"),
    (r"\bЖазғы\s+Азия\s+Ойындары\b", "Летние Азиатские игры"),
    (r"\bАзия\s+Ойындары\b", "Азиатские игры"),
    (r"\bАуыр\s+атлетика\b", "Тяжёлая атлетика"),
    (r"\bЖеңіл\s+атлетика\b", "Лёгкая атлетика"),
    (r"\bК[өо]ркем\s+жүзу\b", "Артистическое плавание"),
    (r"\bК[өо]ркем\s+гимнастика\b", "Художественная гимнастика"),
    (r"\bСадақ\s+ату\b", "Стрельба из лука"),
    (r"\bСуға\s+секіру\b", "Прыжки в воду"),
    (r"\bКомандалық\s+жарыс\b", "Командные соревнования"),
    (r"\bЖекелей\s+жарыс\b", "Личные соревнования"),
    (r"\bӘйелдер\b", "Женщины"),
    (r"\b(?:Айелдер|Айелде|Әйелде)\b", "Женщины"),
    (r"\bЕрлер\b", "Мужчины"),
    (r"\bҚыздар\b", "Девушки"),
    (r"\bҰлдар\b", "Юноши"),
    (r"\bЖартылай\s+финал\b", "Полуфинал"),
    (r"\bШирек\s+финал\b", "Четвертьфинал"),
    (r"\bТоптық\s+кезең\b", "Групповой этап"),
    (r"\bФиналдық\s+кезең\b", "Финальный этап"),
    (r"\bТікелей\s+эфир\b", ""),
    (r"\bТікелей\s+трансляция\b", ""),
    (r"\bПрямая\s+трансляция\b", ""),
    (r"\bLIVE\b", ""),
    (r"\bFootball\b", "Футбол"),
    (r"\bSoccer\b", "Футбол"),
    (r"\bIce\s+Hockey\b", "Хоккей"),
    (r"\bHockey\b", "Хоккей"),
    (r"\bBasketball\b", "Баскетбол"),
    (r"\bVolleyball\b", "Волейбол"),
    (r"\bTennis\b", "Теннис"),
    (r"\bBoxing\b", "Бокс"),
    (r"\bWrestling\b", "Борьба"),
    (r"\bWeightlifting\b", "Тяжёлая атлетика"),
    (r"\bAthletics\b", "Лёгкая атлетика"),
    (r"\bSnooker\b", "Снукер"),
    (r"\bCycling\b", "Велоспорт"),
    (r"\bHandball\b", "Гандбол"),
    (r"\bJudo\b", "Дзюдо"),
    (r"\bFinal\b", "Финал"),
    (r"\bSemi[- ]?final\b", "Полуфинал"),
    (r"\bQuarter[- ]?final\b", "Четвертьфинал"),
    (r"\bGroup\s+stage\b", "Групповой этап"),
    (r"\bRound\s+of\s+16\b", "1/8 финала"),
)

_NAMES = {
    "turkey": "Турция", "türkiye": "Турция", "туркия": "Турция",
    "түркия": "Турция", "италия": "Италия", "italy": "Италия",
    "kazakhstan": "Казахстан", "қазақстан": "Казахстан",
    "kyrgyzstan": "Кыргызстан", "қырғызстан": "Кыргызстан",
    "uzbekistan": "Узбекистан", "өзбекстан": "Узбекистан",
    "england": "Англия", "france": "Франция", "germany": "Германия",
    "spain": "Испания", "portugal": "Португалия",
    "netherlands": "Нидерланды", "belgium": "Бельгия",
    "switzerland": "Швейцария", "greece": "Греция",
    "poland": "Польша", "serbia": "Сербия", "croatia": "Хорватия",
    "ukraine": "Украина", "russia": "Россия",
    "argentina": "Аргентина", "brazil": "Бразилия",
    "usa": "США", "united states": "США",
    "south korea": "Южная Корея", "japan": "Япония", "china": "Китай",
    "manchester city": "Манчестер Сити",
    "manchester united": "Манчестер Юнайтед",
    "liverpool": "Ливерпуль", "arsenal": "Арсенал", "chelsea": "Челси",
    "tottenham": "Тоттенхэм", "newcastle": "Ньюкасл",
    "everton": "Эвертон", "brighton": "Брайтон",
    "bournemouth": "Борнмут", "brentford": "Брентфорд",
    "sunderland": "Сандерленд", "fulham": "Фулхэм",
    "nottingham forest": "Ноттингем Форест", "nottingham": "Ноттингем",
    "leeds": "Лидс", "crystal palace": "Кристал Пэлас",
    "aston villa": "Астон Вилла", "ipswich": "Ипсвич",
    "hull city": "Халл Сити", "hull": "Халл", "coventry": "Ковентри",
    "real madrid": "Реал Мадрид", "barcelona": "Барселона",
    "bayern": "Бавария", "borussia dortmund": "Боруссия Дортмунд",
    "inter": "Интер", "milan": "Милан", "juventus": "Ювентус",
    "napoli": "Наполи", "atletico": "Атлетико", "psg": "ПСЖ",
    "kairat": "Кайрат", "astana": "Астана", "barys": "Барыс",
    "hangzhou": "Ханчжоу",
}

_ACRONYMS = {
    "uefa": "УЕФА", "fifa": "ФИФА", "khl": "КХЛ", "nhl": "НХЛ",
    "nba": "НБА", "afc": "АФК", "ucl": "ЛЧ", "uclw": "ЖЛЧ",
    "wrc": "WRC", "ufc": "UFC", "atp": "ATP", "wta": "WTA",
    "f1": "F1", "mma": "ММА",
}

_SPORTS = {
    "футбол": "Футбол",
    "хоккей": "Хоккей",
    "баскетбол": "Баскетбол",
    "волейбол": "Волейбол",
    "теннис": "Теннис",
    "бокс": "Бокс",
    "мма": "ММА",
    "борьба": "Борьба",
    "тяжёлая атлетика": "Тяжёлая атлетика",
    "тяжелая атлетика": "Тяжёлая атлетика",
    "лёгкая атлетика": "Лёгкая атлетика",
    "легкая атлетика": "Лёгкая атлетика",
    "снукер": "Снукер",
    "велоспорт": "Велоспорт",
    "гандбол": "Гандбол",
    "дзюдо": "Дзюдо",
    "формула-1": "Формула-1",
    "автоспорт": "Автоспорт",
    "мотоспорт": "Мотоспорт",
}

_TOURNAMENT_HINT = re.compile(
    r"(?iu)\b(?:лига|чемпионат|кубок|игры|турнир|гран[- ]при|"
    r"премьер-лига|ATP|WTA|UFC|КХЛ|НХЛ|НБА|УЕФА|ФИФА|АФК)\b"
)

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

_PROTECTED_LATIN_PHRASES = (
    (re.compile(r"(?iu)\bAlash\s+Pride\b"), "ALASH PRIDE"),
)

_PROPER_CASE_WORDS = {
    "казахстан": "Казахстан",
    "казахстана": "Казахстана",
    "казахстане": "Казахстане",
    "азия": "Азия",
    "азии": "Азии",
    "европа": "Европа",
    "европы": "Европы",
    "уефа": "УЕФА",
    "фифа": "ФИФА",
    "кхл": "КХЛ",
    "нхл": "НХЛ",
    "нба": "НБА",
    "мма": "ММА",
}


def strip_bookmakers(value: str) -> str:
    text = _BOOKMAKER_RE.sub("", str(value or ""))
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.:;)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    return text.strip(" .,:;|_-")


def _replace_names(text: str) -> str:
    for source, target in sorted(_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(
            r"(?iu)(?<![\w])" + re.escape(source) + r"(?![\w])",
            target,
            text,
        )
    return text


def _transliterate_word(word: str) -> str:
    if word.isupper() and re.fullmatch(r"[IVXLCDM]+", word):
        return word
    lower = word.casefold()
    if lower in _ACRONYMS:
        return _ACRONYMS[lower]
    if lower in _NAMES:
        return _NAMES[lower]

    specials = {
        "city": "сити", "united": "юнайтед", "sport": "спорт",
        "sports": "спорт", "prime": "прайм", "league": "лига",
    }
    if lower in specials:
        result = specials[lower]
    else:
        value = lower
        for source, target in (
            ("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"),
            ("ch", "ч"), ("sh", "ш"), ("ts", "ц"), ("ya", "я"),
            ("yu", "ю"), ("yo", "ё"), ("ye", "е"), ("ph", "ф"),
            ("th", "т"), ("ck", "к"), ("qu", "кв"), ("ee", "и"),
        ):
            value = value.replace(source, target)
        table = str.maketrans({
            "a": "а", "b": "б", "c": "к", "d": "д", "e": "е",
            "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
            "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
            "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
            "u": "у", "v": "в", "w": "в", "x": "кс", "y": "й",
            "z": "з",
        })
        result = value.translate(table)
    if word[:1].isupper() and not word.isupper():
        result = result[:1].upper() + result[1:]
    return result


def _transliterate_remaining_latin(text: str) -> str:
    protected: list[str] = []

    def hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"§{len(protected) - 1}§"

    for pattern, _canonical in _PROTECTED_LATIN_PHRASES:
        text = pattern.sub(hold, text)

    text = _LATIN_WORD_RE.sub(
        lambda match: _transliterate_word(match.group(0)),
        text,
    )

    for index, original in enumerate(protected):
        canonical = original
        for pattern, replacement in _PROTECTED_LATIN_PHRASES:
            if pattern.fullmatch(original):
                canonical = replacement
                break
        text = text.replace(f"§{index}§", canonical)
    return text


def _normalize_all_caps(text: str) -> str:
    letters = [char for char in text if char.isalpha()]
    cased = [char for char in letters if char.lower() != char.upper()]
    if len(cased) < 8 or not all(char.isupper() for char in cased):
        return text

    lowered = text.lower()

    def sentence(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2).upper()

    lowered = re.sub(
        r"(^|[.!?]\s+|\s-\s)([а-яё])",
        sentence,
        lowered,
        flags=re.I,
    )
    for source, replacement in _PROPER_CASE_WORDS.items():
        lowered = re.sub(
            r"(?iu)(?<![\w])" + re.escape(source) + r"(?![\w])",
            replacement,
            lowered,
        )
    return lowered


@lru_cache(maxsize=8192)
def to_russian_text(value: str) -> str:
    text = strip_bookmakers(value)
    if not text:
        return ""
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+-\s+", " - ", text)
    for pattern, replacement in _PHRASES:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = _replace_names(text)

    # Keep the category readable when a supplier writes "(Әйелдер 86 кг)".
    text = re.sub(
        r"(?iu)\((Женщины|Мужчины)\s+(\d+(?:[.,]\d+)?)\s*кг\)",
        lambda m: f"({m.group(1)}, {m.group(2).replace(',', '.')} кг)",
        text,
    )
    text = text.translate(_KZ_TRANSLIT)
    text = _transliterate_remaining_latin(text)
    text = _normalize_all_caps(text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.:;)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = text.strip(" .,:;|-")
    return text


def canonical_sport(value: str) -> str:
    text = to_russian_text(value)
    key = text.casefold().strip(" .")
    return _SPORTS.get(key, text)


def _strip_prefix(text: str, prefix: str) -> str:
    if not text or not prefix:
        return text
    escaped = re.escape(prefix)
    return re.sub(
        r"(?iu)^" + escaped + r"(?:\s*(?:[.:,]|\s+-\s+)\s*|\s+(?=\())",
        "",
        text,
        count=1,
    ).strip()


def normalize_event_fields(*, title: str, sport: str, tournament: str) -> dict[str, str]:
    result_sport = canonical_sport(sport)
    result_tournament = to_russian_text(tournament)
    result_title = to_russian_text(title)

    parts = [part.strip() for part in re.split(r"\.\s+", result_title) if part.strip()]
    if parts:
        first_sport = canonical_sport(parts[0])
        if first_sport in _SPORTS.values():
            if not result_sport:
                result_sport = first_sport
            parts.pop(0)

    if parts and _TOURNAMENT_HINT.search(parts[0]):
        if not result_tournament:
            result_tournament = parts[0]
        parts.pop(0)

    if parts:
        result_title = ". ".join(parts)

    result_title = _strip_prefix(result_title, result_sport)
    result_title = _strip_prefix(result_title, result_tournament)
    result_title = re.sub(
        r"^\(([^)]+)\)\.\s*",
        r"\1. ",
        result_title,
    ).strip()

    # A tournament can arrive as "УЕФА Лига наций" from one source and
    # "Лига наций УЕФА" from another. Use one stable order for matching.
    result_tournament = re.sub(
        r"(?iu)^УЕФА\s+Лига\s+наций$",
        "Лига наций УЕФА",
        result_tournament,
    )

    return {
        "title": strip_bookmakers(result_title),
        "sport": strip_bookmakers(result_sport),
        "tournament": strip_bookmakers(result_tournament),
    }
