# SLP — Skoomaholic Live Parser

Telegram-бот и EPG-парсер спортивных прямых трансляций. Проект собирает расписание из официальных источников, нормализует события, хранит их в SQLite, определяет фактический статус трансляции и показывает расписание/LIVE в Telegram.

## Что подключено

- Qazsport — `parsers/qazsport.py`
- Sport+ Qazaqstan — `parsers/sportplus.py`
- Telegram — aiogram 3
- внешняя проверка времени — `verifiers/web_search.py`
- SQLite-кэш событий — `services/event_store.py`
- сравнение расписаний и уведомления — `services/schedule_watch.py`

В текущем репозитории других EPG-адаптеров нет. Новый источник должен добавляться отдельным адаптером, а не специальной логикой внутри Telegram-бота.

## Главный принцип LIVE

SLP больше не использует один флаг для двух разных понятий.

`is_live_broadcast` означает только одно: **сам источник пометил передачу как прямую трансляцию**.

`live_now` — вычисляемое состояние. Оно истинно только когда:

```python
is_live_broadcast
and start_time <= now < resolved_end_time
```

Будущий матч с пометкой LIVE имеет статус `upcoming`, завершившийся — `finished`. Обычная EPG-передача во время эфира получает `on_air`, но не `live_now`.

Для окончания используется приоритет:

1. окончание, полученное от источника/адаптера;
2. начало следующей передачи того же канала;
3. ограниченная fallback-длительность по виду спорта.

Логика находится в `services/event_status.py`.

## Время

Основная timezone проекта:

```python
ZoneInfo("Asia/Almaty")
```

Runtime-поля `start_time`, `end_time` и `updated_at` — timezone-aware `datetime`. Ручные прибавления `+5 hours` не используются.

Строковые `date` / `time` пока сохраняются в контракте только для совместимости со старыми адаптерами и OpenSERP.

## SportEvent

Канонические поля события:

- `event_key` — стабильный ключ конкретного ТВ-эфира;
- `source`, `source_url`;
- `channel`;
- `raw_title` — исходное название;
- `normalized_title` / `title` — нормализованное отображаемое название;
- `sport`, `tournament`;
- `timezone`;
- `start_time`, `end_time`;
- `is_live_broadcast`;
- `updated_at`;
- `end_estimation_method`, `end_confidence`.

Контракт и валидация: `models.py`, `services/event_contract.py`.

## SQLite

По умолчанию события сохраняются в:

```text
./slp_events.sqlite3
```

Путь можно переопределить:

```bash
export SLP_DB_PATH=/absolute/path/slp_events.sqlite3
```

Оба слоя — refresh и Telegram — используют один модуль `services/event_store.py`, поэтому путь БД задаётся централизованно.

В таблице `sport_events` сохраняются источник, канал, исходное и нормализованное название, timezone-aware начало/окончание, LIVE-признак источника, URL, время обновления и ключ дедупликации.

## Горизонт расписания

`services/schedule_loader.py` всегда загружает минимум период от текущего дня до ближайшего понедельника включительно. Вчерашний день также проверяется для трансляций, переходящих через полночь.

После минимального горизонта SLP дополнительно проверяет опубликованные будущие даты и расширяет итоговый горизонт до самой дальней найденной LIVE-трансляции.

Размер discovery-окна:

```bash
export SLP_DISCOVERY_LOOKAHEAD_DAYS=14
```

Допустимый диапазон — 0–31 день.

## Диагностика `/live`

Каждый кандидат с `is_live_broadcast=True` логируется с причиной включения или исключения. Пример:

```text
LIVE candidate: channel=Qazsport title=... start=2026-09-11 14:00:00+05:00 end=2026-09-11 16:00:00+05:00 source_live=True now=2026-09-11 14:36:00+05:00 status=live_now included=True reason=live_now
```

Возможные причины исключения:

- `starts_in_future`
- `event_finished`
- `invalid_timezone`
- `source_not_live`

Telegram `/live` показывает только `live_now`.

## Структура

```text
main.py                       # точка входа
bot_app.py                    # Telegram handlers / runtime orchestration
models.py                     # SportEvent
parsers/
  qazsport.py
  sportplus.py
services/
  event_contract.py           # нормализация и валидация события
  event_status.py             # upcoming/live_now/on_air/finished
  event_store.py              # SQLite
  schedule_loader.py          # refresh + горизонт
  schedule_merge.py           # dedupe / sort / simulcast grouping
  schedule_watch.py           # diff + уведомления
  time_logic.py               # форматирование и compatibility API
verifiers/
  web_search.py
tests/
```

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="..."
python main.py
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
$env:BOT_TOKEN="..."
python main.py
```

## Тесты

```bash
python -m unittest discover -s tests -v
```

CI запускает тот же набор тестов на push и pull request.

## Правила для новых источников

Адаптер может выставлять `is_live_broadcast=True` только при наличии надёжного сигнала источника: LIVE badge, API/JSON field, CSS/event type или однозначная текстовая пометка прямого эфира.

Нельзя использовать правила вида:

```python
start_time >= now  # НЕ означает LIVE
sport_event        # НЕ означает LIVE
```

Если надёжного признака прямого эфира нет, событие остаётся обычной EPG-передачей с `is_live_broadcast=False`.
