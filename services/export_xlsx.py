from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from services.time_logic import get_event_status, get_scheduled_datetimes


HEADERS = (
    "Дата",
    "Начало",
    "Окончание",
    "Статус",
    "Вид спорта",
    "Турнир",
    "Событие",
    "Канал",
    "Источник",
    "Исходное название",
    "URL источника",
    "LIVE",
)


def _title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "")


def build_schedule_xlsx(events: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Расписание"
    sheet.append(HEADERS)

    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for event in events:
        start, end = get_scheduled_datetimes(event)
        sheet.append(
            (
                start.strftime("%d.%m.%Y"),
                start.strftime("%H:%M"),
                end.strftime("%d.%m.%Y %H:%M"),
                get_event_status(event).upper(),
                str(event.get("sport") or ""),
                str(event.get("tournament") or ""),
                _title(event),
                str(event.get("channel") or ""),
                str(event.get("source") or ""),
                str(event.get("raw_title") or ""),
                str(event.get("source_url") or ""),
                "Да" if event.get("is_live", False) else "Нет",
            )
        )

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    widths = (12, 10, 20, 12, 18, 30, 44, 24, 16, 48, 48, 10)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    summary = workbook.create_sheet("Сводка")
    summary.append(("Показатель", "Значение"))
    for cell in summary[1]:
        cell.font = Font(bold=True)

    status_counts = {"live": 0, "upcoming": 0, "finished": 0}
    channels: dict[str, int] = {}
    sources: dict[str, int] = {}

    for event in events:
        status = get_event_status(event)
        status_counts[status] = status_counts.get(status, 0) + 1
        channel = str(event.get("channel") or "Не указан")
        source = str(event.get("source") or "Не указан")
        channels[channel] = channels.get(channel, 0) + 1
        sources[source] = sources.get(source, 0) + 1

    summary.append(("Всего событий", len(events)))
    summary.append(("LIVE", status_counts.get("live", 0)))
    summary.append(("SOON", status_counts.get("upcoming", 0)))
    summary.append(("OVER", status_counts.get("finished", 0)))
    summary.append(())
    summary.append(("Канал", "Событий"))
    for channel, count in sorted(channels.items()):
        summary.append((channel, count))
    summary.append(())
    summary.append(("Источник", "Событий"))
    for source, count in sorted(sources.items()):
        summary.append((source, count))

    summary.column_dimensions["A"].width = 32
    summary.column_dimensions["B"].width = 18

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
