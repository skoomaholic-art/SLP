from __future__ import annotations

from collections import Counter
from io import BytesIO

import xlsxwriter


COLUMNS = (
    ("№", "number", 6),
    ("Дата", "date", 12),
    ("Начало", "start", 9),
    ("Окончание", "end", 19),
    ("Статус", "status", 10),
    ("Проверка", "verification", 33),
    ("Вид спорта", "sport", 18),
    ("Турнир", "tournament", 28),
    ("Событие", "title", 42),
    ("Канал", "channel", 20),
    ("Источник", "source", 14),
    ("LIVE evidence", "live_evidence", 38),
    ("Внешнее время", "external_time", 22),
    ("Расхождение, мин", "difference_minutes", 16),
    ("Вес доверия", "trust_weight", 14),
    ("Уверенность", "confidence", 18),
    ("Источников", "source_count", 12),
    ("Релевантных результатов", "matching_results_count", 22),
    ("Источники проверки", "sources", 50),
)


def build_schedule_workbook(rows: list[dict]) -> bytes:
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    header = workbook.add_format({"bold": True, "border": 1, "text_wrap": True})
    cell = workbook.add_format({"valign": "top", "text_wrap": True, "border": 1})
    center = workbook.add_format({"valign": "top", "align": "center", "border": 1})
    warning = workbook.add_format(
        {"valign": "top", "text_wrap": True, "border": 1, "bold": True}
    )

    sheet = workbook.add_worksheet("Расписание")
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, max(len(rows), 1), len(COLUMNS) - 1)

    for col, (title, _, width) in enumerate(COLUMNS):
        sheet.write(0, col, title, header)
        sheet.set_column(col, col, width)

    for row_index, row in enumerate(rows, start=1):
        verification = str(row.get("verification", ""))
        rejected = "отброшено" in verification.casefold()
        for col, (_, key, _) in enumerate(COLUMNS):
            value = row.get(key, "")
            fmt = cell
            if rejected and key in {"verification", "external_time", "difference_minutes"}:
                fmt = warning
            elif key in {
                "number",
                "start",
                "status",
                "difference_minutes",
                "trust_weight",
                "source_count",
                "matching_results_count",
            }:
                fmt = center
            sheet.write(row_index, col, value if value is not None else "", fmt)

    summary = workbook.add_worksheet("Сводка")
    summary.set_column("A:A", 28)
    summary.set_column("B:B", 18)
    summary.write_row("A1", ["Показатель", "Значение"], header)

    statuses = Counter(str(row.get("status") or "") for row in rows)
    needs_check = sum(
        "провер" in str(row.get("verification") or "").casefold()
        or "отброшено" in str(row.get("verification") or "").casefold()
        for row in rows
    )
    metrics = [
        ("Всего событий", len(rows)),
        ("LIVE", statuses.get("LIVE", 0)),
        ("SOON", statuses.get("SOON", 0)),
        ("OVER", statuses.get("OVER", 0)),
        ("Нуждается в проверке", needs_check),
    ]
    for index, (name, value) in enumerate(metrics, start=1):
        summary.write(index, 0, name, cell)
        summary.write(index, 1, value, center)

    channels = Counter(str(row.get("channel") or "Канал не указан") for row in rows)
    start = len(metrics) + 3
    summary.write(start, 0, "Канал", header)
    summary.write(start, 1, "Событий", header)
    for offset, (channel, count) in enumerate(sorted(channels.items()), start=1):
        summary.write(start + offset, 0, channel, cell)
        summary.write(start + offset, 1, count, center)

    workbook.close()
    return output.getvalue()
