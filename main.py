import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from parsers.qazsport import get_qazsport_schedule
from verifiers.web_search import verify_event


BOT_TOKEN = os.getenv("BOT_TOKEN")

KZ_TIMEZONE = ZoneInfo("Asia/Almaty")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📅 Расписание",
                callback_data="schedule"
            ),
            InlineKeyboardButton(
                text="🔴 Сейчас LIVE",
                callback_data="live"
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌐 Проверка в интернете",
                callback_data="internet_check"
            ),
        ],
        [
            InlineKeyboardButton(
                text="👥 Команда",
                callback_data="team"
            ),
        ],
    ]
)


def format_date(date_text):
    months = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }

    date_value = datetime.strptime(
        date_text,
        "%Y-%m-%d"
    )

    return (
        f"{date_value.day} "
        f"{months[date_value.month]}"
    )


def get_event_datetimes(event):
    start_datetime = datetime.strptime(
        f"{event['date']} {event['time']}",
        "%Y-%m-%d %H:%M"
    ).replace(
        tzinfo=KZ_TIMEZONE
    )

    end_date = event.get(
        "estimated_broadcast_end_date"
    )

    end_time = event.get(
        "estimated_broadcast_end"
    )

    if not end_date or not end_time:
        return start_datetime, None

    end_datetime = datetime.strptime(
        f"{end_date} {end_time}",
        "%Y-%m-%d %H:%M"
    ).replace(
        tzinfo=KZ_TIMEZONE
    )

    return start_datetime, end_datetime


def get_event_status(event):
    start_datetime, end_datetime = (
        get_event_datetimes(event)
    )

    now = datetime.now(
        KZ_TIMEZONE
    )

    if now < start_datetime:
        return "upcoming"

    if (
        end_datetime is not None
        and start_datetime <= now < end_datetime
    ):
        return "live"

    if (
        end_datetime is not None
        and now >= end_datetime
    ):
        return "finished"

    return "unknown"


def get_event_status_text(event):
    status = get_event_status(event)

    if status == "live":
        return "🔴 ИДЁТ СЕЙЧАС"

    if status == "upcoming":
        return "🕒 Предстоит"

    if status == "finished":
        return "✅ Эфирный слот завершён"

    return "⚪ Статус не определён"


def is_event_live_now(event):
    return (
        get_event_status(event)
        == "live"
    )


def get_end_text(event):
    end_date = event.get(
        "estimated_broadcast_end_date"
    )

    end_time = event.get(
        "estimated_broadcast_end"
    )

    if not end_date or not end_time:
        return "не определено"

    if end_date == event["date"]:
        return end_time

    return (
        f"{format_date(end_date)}, "
        f"{end_time}"
    )


def format_qazsport_event(event):
    date_text = format_date(
        event["date"]
    )

    end_text = get_end_text(
        event
    )

    status_text = (
        get_event_status_text(
            event
        )
    )

    sport = event.get(
        "sport",
        ""
    )

    tournament = event.get(
        "tournament",
        ""
    )

    title = event.get(
        "title",
        event.get(
            "raw_title",
            ""
        )
    )

    if sport:
        sport_text = (
            f"{sport}. "
        )
    else:
        sport_text = ""

    if tournament:
        tournament_text = (
            f"{tournament}. "
        )
    else:
        tournament_text = ""

    return (
        f"{status_text}\n"
        f"{date_text}, {event['time']} – "
        f"{sport_text}"
        f"{tournament_text}"
        f"{title} | "
        f"{event['channel']}\n"
        f"↳ Ориентировочно до: "
        f"{end_text}"
    )


def remove_duplicates(events):
    result = []
    seen = set()

    for event in events:
        event_key = (
            event["date"],
            event["time"],
            event.get(
                "raw_title",
                event.get("title")
            ),
            event["channel"],
        )

        if event_key in seen:
            continue

        seen.add(
            event_key
        )

        result.append(
            event
        )

    return result


async def get_current_live_events():
    now = datetime.now(
        KZ_TIMEZONE
    )

    yesterday = (
        now.date()
        - timedelta(days=1)
    )

    # ВАЖНО:
    # Сегодня берём именно текущую страницу Qazsport.
    # Не передаём сегодняшнюю дату в URL.
    today_schedule = (
        await get_qazsport_schedule(
            include_current_live=True
        )
    )

    # Вчерашнюю страницу используем отдельно,
    # чтобы корректно ловить LIVE после полуночи.
    yesterday_schedule = (
        await get_qazsport_schedule(
            yesterday,
            include_current_live=True
        )
    )

    combined_schedule = (
        yesterday_schedule
        + today_schedule
    )

    combined_schedule = (
        remove_duplicates(
            combined_schedule
        )
    )

    current_live_events = []

    for event in combined_schedule:
        if not event.get(
            "is_live",
            False
        ):
            continue

        if not is_event_live_now(
            event
        ):
            continue

        current_live_events.append(
            event
        )

    return current_live_events


def build_verification_keyboard(
    live_events
):
    buttons = []

    for index, event in enumerate(
        live_events
    ):
        title = event.get(
            "title",
            event.get(
                "raw_title",
                ""
            )
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{event['time']} — "
                        f"{title}"
                    ),
                    callback_data=(
                        f"verify_qazsport_{index}"
                    )
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


@dp.message(
    Command("start")
)
async def start_command(
    message: Message
):
    await message.answer(
        "SLP [Skoomaholic Live Parser] "
        "запущен ✅\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard,
    )


@dp.callback_query(
    F.data == "schedule"
)
async def schedule_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = (
        await callback.message.answer(
            "⏳ Загружаю расписание "
            "Qazsport..."
        )
    )

    try:
        schedule = (
            await get_qazsport_schedule(
                include_current_live=True
            )
        )

        live_events = [
            event
            for event in schedule
            if event.get(
                "is_live",
                False
            )
        ]

        if not live_events:
            await (
                loading_message
                .edit_text(
                    "📅 Расписание Qazsport\n\n"
                    "LIVE-событий не найдено."
                )
            )

            return

        schedule_text = "\n\n".join(
            format_qazsport_event(
                event
            )
            for event in live_events
        )

        current_time = (
            datetime.now(
                KZ_TIMEZONE
            ).strftime(
                "%H:%M"
            )
        )

        await loading_message.edit_text(
            "📅 LIVE-расписание Qazsport\n"
            f"Текущее время: "
            f"{current_time} (UTC+5)\n\n"
            + schedule_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось загрузить "
            "расписание Qazsport."
        )


@dp.callback_query(
    F.data == "live"
)
async def live_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = (
        await callback.message.answer(
            "⏳ Проверяю, что идёт "
            "прямо сейчас..."
        )
    )

    try:
        current_live_events = (
            await get_current_live_events()
        )

        current_time = (
            datetime.now(
                KZ_TIMEZONE
            ).strftime(
                "%H:%M"
            )
        )

        if not current_live_events:
            await (
                loading_message
                .edit_text(
                    "🔴 Сейчас LIVE\n\n"
                    f"Текущее время: "
                    f"{current_time} "
                    f"(UTC+5)\n\n"
                    "На Qazsport сейчас "
                    "LIVE-событий нет."
                )
            )

            return

        live_text = "\n\n".join(
            format_qazsport_event(
                event
            )
            for event
            in current_live_events
        )

        await loading_message.edit_text(
            "🔴 Сейчас LIVE\n\n"
            f"Текущее время: "
            f"{current_time} "
            f"(UTC+5)\n\n"
            + live_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport LIVE:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось проверить "
            "текущие LIVE-события "
            "Qazsport."
        )


@dp.callback_query(
    F.data == "internet_check"
)
async def internet_check_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = (
        await callback.message.answer(
            "⏳ Загружаю реальные "
            "события Qazsport..."
        )
    )

    try:
        schedule = (
            await get_qazsport_schedule(
                include_current_live=True
            )
        )

        live_events = [
            event
            for event in schedule
            if event.get(
                "is_live",
                False
            )
        ]

        if not live_events:
            await (
                loading_message
                .edit_text(
                    "🌐 Проверка в интернете\n\n"
                    "Событий для проверки нет."
                )
            )

            return

        keyboard = (
            build_verification_keyboard(
                live_events
            )
        )

        await loading_message.edit_text(
            "🌐 Проверка в интернете\n\n"
            "Выберите реальное событие "
            "Qazsport для проверки:",
            reply_markup=keyboard,
        )

    except Exception as error:
        print(
            "Ошибка меню проверки:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось получить "
            "события для проверки."
        )


@dp.callback_query(
    F.data.startswith(
        "verify_qazsport_"
    )
)
async def verify_qazsport_callback(
    callback: CallbackQuery
):
    await callback.answer()

    try:
        event_index = int(
            callback.data.split(
                "_"
            )[-1]
        )

        schedule = (
            await get_qazsport_schedule(
                include_current_live=True
            )
        )

        live_events = [
            event
            for event in schedule
            if event.get(
                "is_live",
                False
            )
        ]

        if event_index >= len(
            live_events
        ):
            await callback.message.answer(
                "⚠️ Расписание уже "
                "изменилось.\n"
                "Откройте проверку заново."
            )

            return

        event = live_events[
            event_index
        ]

        title = event.get(
            "title",
            event.get(
                "raw_title",
                ""
            )
        )

        sport = event.get(
            "sport",
            ""
        )

        tournament = event.get(
            "tournament",
            ""
        )

        end_text = get_end_text(
            event
        )

        loading_message = (
            await callback.message.answer(
                "🌐 Интернет-проверка\n\n"
                f"🏟 {title}\n\n"
                "⏳ Ищу событие "
                "во внешних источниках..."
            )
        )

        verification = (
            await asyncio.to_thread(
                verify_event,
                event,
            )
        )

        if not verification[
            "found"
        ]:
            await loading_message.edit_text(
                "🌐 Интернет-проверка\n\n"
                f"🏟 {title}\n\n"
                f"🏅 Вид спорта: "
                f"{sport or 'не определён'}\n"
                f"🏆 Турнир: "
                f"{tournament or 'не определён'}\n"
                f"📅 Дата: "
                f"{format_date(event['date'])}\n"
                f"🕐 Начало эфира Qazsport: "
                f"{event['time']}\n"
                f"🏁 Ориентировочно до: "
                f"{end_text}\n"
                f"📺 Канал: "
                f"{event['channel']}\n\n"
                "❌ Не удалось определить "
                "время события по найденным "
                "внешним источникам.\n\n"
                f"🔎 Результатов поиска: "
                f"{verification['search_results_count']}\n"
                f"✅ Подходящих по событию: "
                f"{verification['matching_results_count']}"
            )

            return

        difference = verification.get(
            "difference_minutes"
        )

        if difference is None:
            difference_text = (
                "не определена"
            )
        else:
            difference_text = (
                f"{abs(difference)} мин."
            )

        time_status = (
            verification.get(
                "time_status",
                {},
            )
        )

        sources = verification.get(
            "sources",
            [],
        )

        if sources:
            sources_text = "\n".join(
                (
                    f"• "
                    f"{source['source_name']} — "
                    f"{source['time']} MSK"
                )
                for source
                in sources[:5]
            )
        else:
            sources_text = "• нет"

        await loading_message.edit_text(
            "🌐 Интернет-проверка\n\n"
            f"🏟 {title}\n\n"
            f"🏅 Вид спорта: "
            f"{sport or 'не определён'}\n"
            f"🏆 Турнир: "
            f"{tournament or 'не определён'}\n"
            f"📅 Дата Qazsport: "
            f"{format_date(event['date'])}\n"
            f"🕐 Начало эфира Qazsport: "
            f"{event['time']}\n"
            f"🏁 Ориентировочно до: "
            f"{end_text}\n"
            f"📺 Канал: "
            f"{event['channel']}\n\n"
            "🌍 Внешние источники\n"
            f"🕐 Время события: "
            f"{verification['external_time_kz']} "
            f"(UTC+5)\n"
            f"📅 Дата события: "
            f"{verification['external_date_kz']}\n"
            f"🔎 Найдено результатов: "
            f"{verification['search_results_count']}\n"
            f"🎯 Подходят к событию: "
            f"{verification['matching_results_count']}\n"
            f"📚 Независимых организаций: "
            f"{verification['source_count']}\n"
            f"⚖️ Вес доверия: "
            f"{verification['total_weight']}\n"
            f"📊 Уверенность: "
            f"{verification['confidence']['label']}\n"
            f"ℹ️ "
            f"{verification['confidence']['reason']}\n\n"
            f"⏱ Разница с эфиром: "
            f"{difference_text}\n"
            f"{time_status.get('label', '')}\n"
            f"{time_status.get('description', '')}\n\n"
            "Источники:\n"
            f"{sources_text}"
        )

    except Exception as error:
        print(
            "Ошибка карточки проверки:",
            repr(error)
        )

        await callback.message.answer(
            "❌ Не удалось открыть "
            "событие для проверки."
        )


@dp.callback_query(
    F.data == "status"
)
async def status_callback(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "📊 Статус SLP\n\n"
        "✅ Telegram-бот работает\n"
        "✅ Qazsport подключён\n"
        "✅ Реальное расписание "
        "загружается\n"
        "✅ LIVE-фильтрация работает\n"
        "✅ UTC+5 учитывается\n"
        "✅ Переход через 00:00 работает\n"
        "✅ Вчерашний LIVE после "
        "00:00 учитывается\n"
        "✅ Окончание эфирного "
        "слота рассчитывается\n"
        "✅ Русская нормализация "
        "Qazsport подключена\n"
        "✅ Турниры УЕФА "
        "распознаются\n"
        "✅ Интернет-сверка "
        "подключена\n\n"
        "Стадия: финальная проверка "
        "текущего этапа 🛠"
    )


@dp.callback_query(
    F.data == "team"
)
async def team_callback(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "👥 Команда\n\n"
        "Редактор\n"
        "Промо-продюсер\n"
        "Переводчик\n"
        "Дизайнер\n\n"
        "Роли пользователей "
        "пока не настроены."
    )


async def main():
    print(
        "SLP запущен. "
        "Ожидаю сообщения в Telegram..."
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )