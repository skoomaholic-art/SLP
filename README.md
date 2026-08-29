вчера 2:06
До 1 сентября успеем хотя бы парсер сделать?

Да. Рабочий MVP-парсер к 1 сентября сделать реально, если сейчас жёстко зафиксировать объём и не лезть в Dashboard, CMS и дополнительную косметику.

Под «готовым парсером к 1 сентября» я бы зафиксировал такой результат:

Qazsport стабильно парсится;
корректные дата и UTC+5, включая переход через полночь;
LIVE / SOON / OVER считаются правильно;
спорт, турнир и событие нормально распознаются;
дубли удаляются;
интернет-проверка OpenSERP работает адекватно и не тормозит по минуте;
RU/KZ/EN алиасы работают;
ориентировочное окончание трансляции есть;
Telegram показывает нормальное расписание и LIVE;
данные уже имеют универсальный формат, чтобы дальше быстро подключать другие каналы.

Полностью подключить все 10+ источников к 1 сентября я бы не обещал. Это зависит от структуры каждого сайта. Но ядро парсера и Qazsport должны быть доведены до состояния, которое уже можно использовать в работе. Если источники окажутся простыми, за эти дни успеем добавить ещё несколько.

План до дедлайна такой:

28 августа: закрываем 70.1–70.5 — чистка, ошибки времени, ускорение OpenSERP, Extract, matching. В конце дня Qazsport должен стать стабильным эталонным парсером.

29 августа: закрываем нормализацию Qazsport, тесты перехода через полночь, LIVE и окончаний. Фиксируем единый формат события.

30 августа: подключаем следующий наиболее простой источник и тем самым проверяем, что архитектура действительно универсальная. Если идёт быстро — сразу третий.

31 августа: объединение источников, дедупликация, финальные ошибки, Telegram и при наличии времени XLSX.

1 сентября: рабочая версия, а не очередная переделка архитектуры.

То есть я бы поставил вероятность примерно 85–90%, что к 1 сентября у нас будет нормальный рабочий SLP Parser v1 при условии, что до этого момента мы замораживаем Sports Operations Dashboard и интеграцию с админкой.

А уже после 1 сентября начинаем превращать готовый парсер в Dashboard. Это намного разумнее, чем пытаться одновременно закончить парсер, подключить десять сайтов и CMS.

Тогда скорректируй наши планы, чтоб следующий шаг уже был в рамках нового скорректированного плана

Да. План теперь официально меняем под цель: к 1 сентября получить рабочий SLP Parser v1, а Sports Operations Dashboard и интеграцию с админкой временно ставим после релиза парсера.

До 1 сентября действует жёсткий freeze: не трогаем CMS, Dashboard, роли, кастомные изображения, сложную аналитику и другие необязательные функции. XLSX делаем только если не мешает основному дедлайну.

Скорректированный план
Шаг 70.1 — очистка и страховка проекта. 28 августа. Убираем дублирующиеся и устаревшие файлы, приводим зависимости в порядок, оставляем одну актуальную реализацию каждой функции. Добавляем минимальные автоматические проверки на критичные вещи: полночь, LIVE/SOON/OVER, Qazsport parsing. Цель: получить чистую базу, на которой дальше ничего не ломается случайно.
Шаг 70.2 — исправление времени и статусов. 28 августа. LIVE/SOON/OVER рассчитывается только по эфирному расписанию, а интернет-время используется исключительно для проверки точности. Исправляем переходы 27 августа 23:20 → 28 августа 01:30, последнее событие дня и fallback длительности. Это критический блок.
Шаг 70.3 — доводим Qazsport до эталонного парсера. 28–29 августа. Чиним 1\4 → 1/4, Қайрат → Кайрат, Дзюдо, Grand Slam, турниры, жеребьёвки, RU/KZ-названия, студийные передачи, дубли и другие реальные случаи из расписания. После этого Qazsport должен выдавать чистые структурированные события без ручных исправлений.
Шаг 70.4 — ускоряем OpenSERP. 29 августа. Bing + DuckDuckGo становятся основными движками, Baidu — fallback. Google/Yandex/Ecosia временно исключаем из рабочего цикла из-за блокировок и таймаутов. Убираем лишние повторные поиски. Цель: не ждать по минуте проверку каждого события.
Шаг 70.5 — нормальная интернет-проверка. 29 августа. Поиск → релевантные страницы → OpenSERP Extract → время из содержимого страницы. Разделяем MATCH / DRAW / GENERIC EVENT. Добавляем нормальные алиасы RU/KZ/EN. Внешнее время определяется независимо от времени Qazsport, а уже потом сравнивается с ним.
Шаг 70.6 — фиксируем универсальный формат SportEvent. 30 августа. Не строим большую архитектуру. Просто определяем единый контракт: дата, начало, окончание, спорт, турнир, событие, канал, источник, LIVE-флаг и служебные поля. Qazsport становится первым адаптером этого формата. Это необходимо перед вторым каналом.
Шаг 70.7 — подключаем второй источник. 30 августа. Берём один из наиболее простых реальных источников. Его задача не только дать события, но и доказать, что вся логика после парсера действительно универсальная: время, Telegram, проверка, дедупликация не переписываются под каждый сайт. Если идёт быстро, подключаем третий.
Шаг 70.8 — объединённое расписание SLP. 31 августа. События разных каналов собираются вместе, сортируются по UTC+5, удаляются дубли. Один матч на нескольких каналах понимается корректно. 📅 Расписание и 🔴 LIVE работают уже не как Qazsport-only.
Шаг 70.9 — финальная стабилизация Telegram. 31 августа. Исправляем визуальные промежутки, переходы между датами, подробный список, фоновые проверки, повторные нажатия и сообщения. Никаких новых функций — только устранение багов.
Шаг 71 — SLP Parser v1. 1 сентября. Финальный контрольный прогон на реальном расписании, исправление только критических ошибок, Git checkpoint и рабочая версия. Если к этому моменту остаётся запас времени, добавляем XLSX; если нет — XLSX становится первым пострелизным шагом.

После релиза порядок уже такой: XLSX → остальные спортивные источники → постоянный SQLite-кэш → Sports Operations Dashboard → read-only интеграция с OTT-админкой → reconciliation и автоматические предупреждения.

И главное: наш следующий шаг теперь однозначно Шаг 70.1. Никакого Extract, XLSX или нового канала до его закрытия. После 70.1 сразу идём в 70.2, потому что ошибки времени и LIVE важнее всего остального.

Когда переходим к шагу, четко фиксируй:
Номер шага
Этап разработки
Огибаемый результат 


Принято. С этого момента каждый новый шаг фиксирую строго в одном формате:

Номер шага: 70.1
Этап разработки: Очистка и страховка проекта
Ожидаемый результат: Чистая структура проекта без дублей и устаревших файлов, минимальный набор тестов на критичную логику, стабильная база для дальнейшей разработки.

И только после этого даю конкретные действия.

То есть перед каждым следующим этапом ты сразу видишь где мы находимся, что именно сейчас разрабатываем и какой результат должен быть на выходе.

Система уведомлений включена в тг бот? Если событие изменено?

Пока нет. В текущем Telegram-боте система уведомлений об изменении события ещё не реализована: есть загрузка расписания, кэш и фоновая интернет-проверка, но нет механизма «сохранить предыдущую версию → сравнить с новой → отправить уведомление». В main.py вообще нет обработчика уведомлений/alerts.

Но в Parser v1 её стоит включить, причём в простом виде. SLP периодически обновляет расписание и уведомляет в Telegram только о существенных изменениях:

⚠️ Изменение события

Андерлехт – Кайрат
Qazsport

Было: 23:20
Стало: 23:50

Изменено время начала.

Также уведомления нужны при появлении нового LIVE-события, отмене/исчезновении события, смене канала, изменении названия/участников и существенном изменении времени окончания.

Это не должно тормозить релиз: я бы добавил это после стабилизации парсинга и до финального Parser v1, без сложной системы ролей.

Ок

Принято. Следующий рабочий шаг будет уже в обновлённом плане с обязательной фиксацией:

Номер шага
Этап разработки
Ожидаемый результат

И уведомления об изменениях события включаю в обязательный объём Parser v1 до релиза.

сегодня 4:47
Го

Номер шага: 70.1
Этап разработки: Очистка и страховка проекта
Ожидаемый результат: Убираем старый прототип и лишние зависимости, оставляем только актуальное ядро SLP и добавляем базовые автотесты. После шага проект должен собираться без ошибок и быть готов к исправлению времени в 70.2.

Я перепроверил текущие файлы перед изменениями. main.py сейчас использует только актуальный parsers.qazsport и verifiers.web_search; старые models.py, events.py и time_utils.py к рабочему боту не подключены. events.py вообще содержит старые вручную забитые примеры через устаревший SportEvent. Текущий Qazsport самостоятельно реализует свою обработку времени и не импортирует time_utils.py. Также в requirements.txt остался ddgs, хотя текущий verifier работает через локальный OpenSERP и использует стандартные Python-модули.

1. Останови бота

Если в терминале сейчас бот работает и нет строки $, нажми:

Ctrl+C

Дождись строки с $.

2. Полностью заменяем requirements.txt

Открой requirements.txt → Ctrl+A → вставь ВЕСЬ код ниже → Ctrl+S:

aiogram==3.30.0
aiohttp==3.14.3
beautifulsoup4==4.15.0

Это теперь именно список прямых зависимостей SLP, а не дамп всей виртуальной среды.

3. Удаляем старый прототип

В командной строке Codespaces введи:

rm -f events.py models.py time_utils.py "web_search (1).py"

Это удалит старые events.py, models.py, time_utils.py и дубликат verifier. Актуальный web_search.py останется.

4. Создаём первый автоматический тест SLP

Слева в VS Code создай папку:

tests

В ней создай файл:

test_baseline.py

Вставь в него весь код:

import unittest

from parsers.qazsport import (
    build_url,
    clean_title,
    extract_sport_and_remainder,
    normalize_russian_text,
    parse_qazsport_title,
    time_to_minutes,
)


class QazsportBaselineTests(unittest.TestCase):

    def test_build_url_for_date(self):
        self.assertEqual(
            build_url("2026-08-29"),
            "https://qazsporttv.kz/ru/program/2026-08-29",
        )

    def test_time_to_minutes(self):
        self.assertEqual(
            time_to_minutes("23:50"),
            23 * 60 + 50,
        )

    def test_clean_live_title(self):
        self.assertEqual(
            clean_title(
                "11:55 LIVE Волейбол. Матч",
                "11:55",
            ),
            "Волейбол. Матч",
        )

    def test_extract_sport(self):
        raw_sport, sport, remainder = (
            extract_sport_and_remainder(
                "Футбол. QJ League Тараз - Тобол"
            )
        )

        self.assertEqual(
            raw_sport,
            "Футбол",
        )
        self.assertEqual(
            sport,
            "Футбол",
        )
        self.assertEqual(
            remainder,
            "QJ League Тараз - Тобол",
        )

    def test_kazakh_tournament_normalization(self):
        self.assertEqual(
            normalize_russian_text(
                "Азия чемпионаты (Әйелдер)"
            ),
            "Чемпионат Азии. Женщины",
        )

    def test_uefa_conference_draw(self):
        event = parse_qazsport_title(
            "Футбол. "
            "УЕФА Конференциялар Лигасы "
            "жалпы кезеңнің жеребе тарту рәсімі"
        )

        self.assertEqual(
            event["sport"],
            "Футбол",
        )
        self.assertEqual(
            event["tournament"],
            "Лига конференций УЕФА",
        )
        self.assertEqual(
            event["title"],
            "Жеребьёвка общего этапа",
        )


if __name__ == "__main__":
    unittest.main()

Сохрани: Ctrl+S.

Это не тестирует интернет и не ходит на Qazsport. Он за секунды проверяет базовые функции парсера. В текущем коде для этого уже есть отдельные функции нормализации, разбора спорта и жеребьёвок.

5. Один контрольный запуск

В командной строке Codespaces введи одной командой:

python -m unittest discover -s tests -v && python -m compileall -q main.py parsers verifiers && echo "STEP 70.1 OK"

Нужный результат в самом конце:

OK
STEP 70.1 OK

Если именно это получишь, Шаг 70.1 закрываем и сразу переходим к:

Номер шага: 70.2
Этап разработки: Исправление времени и LIVE/SOON/OVER
Ожидаемый результат: Интернет больше не сможет менять статус телетрансляции, переход через полночь будет корректным, а последнее событие дня перестанет висеть LIVE бесконечно.

переходим к следующему шагу
STEP 70.1 OK   вышло

Номер шага: 70.2
Этап разработки: Исправление времени и статусов LIVE / SOON / OVER
Ожидаемый результат: Статус трансляции считается только по эфирному расписанию, переход через полночь работает корректно, последнее событие не остаётся LIVE бесконечно, а время начала/окончания отображается однозначно.

Я уже собрал полный комплект файлов и прогнал новые тесты: 7/7 успешно. Текущий main.py действительно смешивает эфирное и внешнее время: интернет-время может подменять начало, после чего от него рассчитывается LIVE/SOON/OVER. Также при отсутствии окончания функция фактически оставляет начавшееся событие LIVE навсегда.

1. Полностью заменяем main.py

Скачай готовый файл:

main.py — Step 70.2

В Codespaces открой текущий main.py → Ctrl+A → вставь весь код из нового файла → Ctrl+S.

Это именно полная замена, никаких отдельных функций вручную менять не нужно.

2. Создаём слой времени

В корне проекта создай папку:

services

В ней два файла.

time_logic.py

Открой/создай:

services/time_logic.py

Вставь туда весь файл → Ctrl+S.

Затем создай пустой:

services/__init__.py

Готовый файл:

services/init.py

Теперь вся критичная логика времени находится отдельно от Telegram.

3. Добавляем тесты времени

В уже существующей папке tests создай:

test_time_logic.py

Полный файл:

test_time_logic.py

Вставь весь код → Ctrl+S.

Твой старый test_baseline.py не удаляем.

Что конкретно исправлено

Теперь:

Эфир Qazsport
23:20 → 01:30
        ↓
29 августа 23:20
30 августа 01:30
        ↓
LIVE / SOON / OVER

А интернет существует отдельно:

Официальное время: 23:30
Эфир Qazsport:     23:20
Расхождение:       10 минут

Время совпадает

23:30 больше не может передвинуть статус трансляции.

Также если последнее событие Qazsport не имеет следующей программы, вместо:

23:20 → ?
LIVE навсегда

SLP временно использует типичную продолжительность спорта:

Футбол
23:20 → ≈01:50

И помечает в подробностях:

Окончание эфира:
≈ 30 августа 2026, 01:50
(оценка по виду спорта)

Когда Qazsport даёт следующую программу, по-прежнему используется именно её время — оно приоритетнее fallback. В самом парсере сейчас окончание действительно определяется по следующей передаче, а для последней записи остаётся None.

Заодно исправлено отображение перехода суток:

29 августа, 23:20–30 августа, 01:30

вместо неоднозначного:

23:20–30 августа, 01:30

И между событиями в кратком расписании теперь ровно одна пустая строка.

4. Один контрольный запуск

Если бот сейчас работает и нет $, нажми Ctrl+C и дождись $.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py parsers services verifiers && echo "STEP 70.2 OK"

Нужный финал:

OK
STEP 70.2 OK

Если это вышло, 70.2 закрываем.

Следом:

Номер шага: 70.3
Этап разработки: Qazsport как эталонный парсер
Ожидаемый результат: Исправляем Дзюдо / Grand Slam, 1\4, Қайрат / Кайрат, турниры, жеребьёвки и другие реальные названия так, чтобы Qazsport отдавал уже чистые структурированные данные без костылей в main.py.

main.py
Код
time_logic.py
Код
__init__.py
Код
test_time_logic.py
Код
Свернуть
какую то замену надо делать сейчас?
STEP70.2 вышло ОК

Номер шага: 70.3
Этап разработки: Qazsport как эталонный парсер
Ожидаемый результат: Qazsport сам нормализует Дзюдо / Grand Slam, 1\4 → 1/4, 1\2 → 1/2, Қайрат → Кайрат, турниры и жеребьёвки. В main.py больше не нужен костыль для исправления названий перед OpenSERP.

Раз STEP 70.2 OK вышло, файлы со скриншота повторно трогать не надо. Они уже относятся к закрытому шагу 70.2.

Сейчас в 70.3 заменяем только два файла и добавляем один тест.

Открой parsers/qazsport.py → Ctrl+A → вставь весь новый файл → Ctrl+S:

Скачать новый qazsport.py

Затем открой main.py → Ctrl+A → вставь весь новый файл → Ctrl+S:

Скачать новый main.py

В папке tests создай:

test_qazsport_normalization.py

и вставь туда весь файл:

Скачать test_qazsport_normalization.py

Я добавил проверки именно на наши реальные проблемы: 1\4, 1\2, Қайрат, Grand Slam / Дзюдо и жеребьёвку Лиги конференций.

После этого в командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py parsers services verifiers && echo "STEP 70.3 OK"

Нужный финал:

OK
STEP 70.3 OK

Если удобнее, весь комплект одним архивом:

SLP_STEP_70_3.zip

После STEP 70.3 OK сразу идём в 70.4 — ускорение OpenSERP.

qazsport.py
Код
main.py
Код
test_qazsport_normalization.py
Код
OK
STEP 70.3 OK

вышло

Номер шага: 70.4
Этап разработки: Ускорение OpenSERP
Ожидаемый результат: Основной поиск идёт только через Bing + DuckDuckGo, Baidu используется только как резерв. Google, Yandex и Ecosia временно исключены. Проверка больше не должна зависать из-за проблемных поисковиков на минуту и дольше.

STEP 70.3 OK означает, что предыдущий этап закрыт. Сейчас меняем только verifier и добавляем тест.

1. Полностью заменяем verifiers/web_search.py

Открой:

verifiers/web_search.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый web_search.py

Что изменено:

основные движки: Bing + DuckDuckGo;
Baidu только fallback;
Google / Yandex / Ecosia отключены;
лимит результатов снижен до 10;
убран бесполезный lang=EN;
общий timeout сокращён;
если событие уже найдено в результатах, но времени в сниппете нет, второй поиск больше не запускается зря — на следующем шаге 70.5 мы будем читать саму страницу через OpenSERP Extract;
старые иконки точности из verifier тоже убраны.

Это устраняет как раз текущую тяжёлую схему, где OpenSERP запускал сразу шесть движков, включая проблемные Google/Yandex/Ecosia. Раньше один запрос мог ждать до 120 секунд.

2. Добавляем тест

В папке tests создай:

test_openserp_speed.py

→ вставь весь файл → Ctrl+S.

Скачать test_openserp_speed.py

Я уже прогнал эти новые тесты отдельно: 5/5 OK.

3. Один контрольный запуск

Если бот запущен и строки $ нет, нажми Ctrl+C и дождись $.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py parsers services verifiers && echo "STEP 70.4 OK"

Нужный финал:

OK
STEP 70.4 OK

Весь комплект одним архивом:

SLP_STEP_70_4.zip

После этого переходим к самому важному блоку интернет-проверки:

Номер шага: 70.5
Этап разработки: OpenSERP Extract + корректный matching
Ожидаемый результат: SLP перестаёт пытаться угадывать время только по поисковому сниппету. Он находит релевантные страницы, читает их содержимое, извлекает время именно из контекста события и только потом сравнивает его с эфиром Qazsport.

web_search.py
Код
test_openserp_speed.py
Код
STEP 70.4 OK dsikj

Номер шага: 70.5
Этап разработки: OpenSERP Extract + корректный matching
Ожидаемый результат: SLP находит релевантные страницы события, читает их содержимое, определяет время из контекста события и только потом сравнивает его с эфирным временем Qazsport.

STEP 70.4 OK означает, что 70.4 закрыт.

Перед реализацией я проверил актуальную документацию OpenSERP. У него уже есть нативный POST /extract/batch, который принимает список URL и возвращает извлечённый page_content с metadata. То есть отдельный собственный web-scraper нам писать не нужно.

1. Полностью заменяем verifiers/web_search.py

Открой:

verifiers/web_search.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый web_search.py

Теперь логика такая:

Bing + DuckDuckGo
        ↓
релевантные страницы
        ↓
выбираем до 5 независимых источников
        ↓
OpenSERP Extract
        ↓
читаем содержимое страниц
        ↓
ищем время рядом с нужным событием
        ↓
консенсус источников
        ↓
только теперь сравниваем с Qazsport

OpenSERP позволяет batch-extraction до 20 URL; мы намеренно ограничиваемся пятью ради скорости. Если /extract/batch вдруг отсутствует в установленной версии, код автоматически использует обычный /extract, а не падает.

2. Добавляем тест

Создай:

tests/test_openserp_extract.py

Вставь весь файл → Ctrl+S.

Скачать test_openserp_extract.py

В нём проверяются наши ключевые случаи:

MATCH / DRAW / GENERIC;
Қайрат / Кайрат / Kairat;
Андерлехт / Anderlecht;
жеребьёвка не путается с жеребьёвкой другого турнира;
время выбирается по контексту страницы, а не потому что оно ближе к Qazsport;
используется нативный OpenSERP Extract.

Я прогнал весь текущий набор проекта вместе с новыми тестами: 30 тестов, все OK.

3. Контрольный запуск

Если бот сейчас запущен и строки $ нет, нажми Ctrl+C и дождись $.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py parsers services verifiers && python -c "from verifiers.web_search import openserp_extract_batch; x=openserp_extract_batch(['https://example.com']); assert x and 'page_content' in x[0]; print('OPENSERP EXTRACT OK')" && echo "STEP 70.5 OK"

Нужный финал:

OK
OPENSERP EXTRACT OK
STEP 70.5 OK

Весь комплект:

SLP_STEP_70_5.zip

Если получишь STEP 70.5 OK, следующий этап уже:

Номер шага: 70.6
Этап разработки: Универсальный формат SportEvent
Ожидаемый результат: Qazsport перестаёт быть особым случаем. Фиксируем единый формат события, через который затем подключим второй канал без переписывания Telegram, времени, OpenSERP и дедупликации.

web_search.py
Код
test_openserp_extract.py
Код
готово

Номер шага: 70.6
Этап разработки: Универсальный формат SportEvent
Ожидаемый результат: Все парсеры SLP начинают отдавать события в одном формате. Qazsport становится первым адаптером этого формата, а второй канал можно будет подключать без переписывания Telegram, времени, OpenSERP и основной дедупликации.

STEP 70.5 OK значит, что интернет-проверка закрыта и идём дальше.

На этом шаге меняем один текущий файл и добавляем три новых. main.py трогать не нужно.

1. Полностью заменяем parsers/qazsport.py

Открой:

parsers/qazsport.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый qazsport.py

Теперь Qazsport в конце своей работы обязан привести каждую запись к единому контракту SLP.

2. Создаём models.py

В корне проекта создай:

models.py

Вставь весь файл:

Скачать models.py

Это теперь не тот старый models.py, который мы удалили в 70.1. Новый файл описывает фактический формат, к которому мы уже пришли в процессе разработки.

Базовый SportEvent теперь выглядит концептуально так:

source
source_url
channel

date
time

sport
tournament
title

is_live
raw_title

estimated_broadcast_end_date
estimated_broadcast_end
end_estimation_method
end_confidence

Плюс разрешены служебные поля конкретного адаптера.

3. Создаём универсальный контракт адаптеров

В папке services создай:

event_contract.py

Вставь весь файл:

Скачать event_contract.py

Его задача простая:

Qazsport raw data
      ↓
build_sport_event()
      ↓
SportEvent
      ↓
Telegram
Time Logic
OpenSERP
Deduplication

Следующий канал пойдёт через тот же build_sport_event().

4. Добавляем тест контракта

Создай:

tests/test_event_contract.py

Вставь весь файл:

Скачать test_event_contract.py

Я прогнал весь комплект вместе с предыдущими тестами: 35 тестов, все OK.

5. Контрольный запуск

Если бот запущен и строки $ нет, нажми Ctrl+C и дождись $.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 OK"

Нужный финал:

OK
STEP 70.6 OK

Весь комплект также здесь:

SLP_STEP_70_6.zip

После этого идём в:

Номер шага: 70.7
Этап разработки: Подключение второго спортивного источника
Ожидаемый результат: Второй канал проходит через тот же SportEvent, время, OpenSERP и Telegram без отдельной логики в main.py. Это будет реальная проверка, что архитектура стала универсальной.

qazsport.py
Код
models.py
Код
event_contract.py
Код
Еще 1
maholic-live-parser (main) $ python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 OK" 
test_baseline (unittest.loader._FailedTest.test_baseline) ... ERROR 
test_event_contract (unittest.loader._FailedTest.test_event_contract) ... ERROR 
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok 
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok 
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok 
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok 
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok 
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok 
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok 
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok 
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok 
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok 
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok 
test_qazsport_normalization (unittest.loader._FailedTest.test_qazsport_normalization) ... ERROR 
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok 
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok 
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok 
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok 
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok 
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok 
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok 
 
====================================================================== 
ERROR: test_baseline (unittest.loader._FailedTest.test_baseline) 
---------------------------------------------------------------------- 
ImportError: Failed to import test module: test_baseline 
Traceback (most recent call last): 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 394, in _find_test_path 
    module = self._get_module_from_name(name) 
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 337, in _get_module_from_name 
    __import__(name) 
  File "/workspaces/skoomaholic-live-parser/tests/test_baseline.py", line 3, in <module> 
    from parsers.qazsport import ( 
  File "/workspaces/skoomaholic-live-parser/parsers/qazsport.py", line 8, in <module> 
    from services.event_contract import build_sport_event 
  File "/workspaces/skoomaholic-live-parser/services/event_contract.py", line 6, in <module> 
    from models import SportEvent 
ModuleNotFoundError: No module named 'models' 
 
 
====================================================================== 
ERROR: test_event_contract (unittest.loader._FailedTest.test_event_contract) 
---------------------------------------------------------------------- 
ImportError: Failed to import test module: test_event_contract 
Traceback (most recent call last): 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 394, in _find_test_path 
    module = self._get_module_from_name(name) 
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 337, in _get_module_from_name 
    __import__(name) 
  File "/workspaces/skoomaholic-live-parser/tests/test_event_contract.py", line 3, in <module> 
    from models import SportEvent 
ModuleNotFoundError: No module named 'models' 
 
 
====================================================================== 
ERROR: test_qazsport_normalization (unittest.loader._FailedTest.test_qazsport_normalization) 
---------------------------------------------------------------------- 
ImportError: Failed to import test module: test_qazsport_normalization 
Traceback (most recent call last): 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 394, in _find_test_path 
    module = self._get_module_from_name(name) 
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 337, in _get_module_from_name 
    __import__(name) 
  File "/workspaces/skoomaholic-live-parser/tests/test_qazsport_normalization.py", line 3, in <module> 
    from parsers.qazsport import ( 
  File "/workspaces/skoomaholic-live-parser/parsers/qazsport.py", line 8, in <module> 
    from services.event_contract import build_sport_event 
  File "/workspaces/skoomaholic-live-parser/services/event_contract.py", line 6, in <module> 
    from models import SportEvent 
ModuleNotFoundError: No module named 'models' 
 
 
---------------------------------------------------------------------- 
Ran 21 tests in 0.014s 
 
FAILED (errors=3) 
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ .

Проблема понятна: models.py не оказался в корне проекта. Поэтому всё, что импортирует SportEvent, падает.

Номер шага: 70.6 — исправление
Этап разработки: Универсальный формат SportEvent
Ожидаемый результат: models.py находится в корне проекта, импорт работает, все тесты снова проходят.

Ничего больше сейчас заменять не нужно.

1. Создай models.py именно в корне проекта

То есть рядом с:

main.py
requirements.txt
models.py
parsers/
services/
tests/
verifiers/

Открой новый models.py → вставь весь код → Ctrl+S:

from __future__ import annotations

from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Единый формат спортивного события внутри SLP."""

    source: str
    source_url: str
    channel: str
    date: str
    time: str
    sport: str
    tournament: str
    title: str
    is_live: bool
    raw_title: str

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]
2. Сразу повторяем проверку

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 OK"

Теперь ожидаю:

OK
STEP 70.6 OK

То есть ошибка не в логике 70.6, а просто в том, что один из новых файлов физически не был создан в репозитории.

models.py  где сейчас у меня лежит? не могу найти

Сейчас по логу в твоём Codespaces models.py в корне проекта вообще нет. Поэтому Python и пишет:

ModuleNotFoundError: No module named 'models'

Он должен лежать вот здесь:

/workspaces/skoomaholic-live-parser/models.py

То есть слева в VS Code на одном уровне с:

skoomaholic-live-parser/
├── main.py
├── models.py          ← ВОТ ЗДЕСЬ
├── requirements.txt
├── parsers/
├── services/
├── tests/
└── verifiers/

Файл, который я дал на шаге 70.6, был в скачиваемом комплекте, но сам в Codespaces автоматически не появился. Поэтому ты его сейчас и не находишь.

Создай слева в самом корне проекта новый файл models.py, не внутри services, parsers или tests. После этого вставим в него код, который я дал выше, и повторим тест.

Ran 35 tests in 0.019s

OK
Can't list 'models.py'
STEP 70.6 OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

результат

35 тестов прошли — это хорошо. Но строку

Can't list 'models.py'

игнорировать не стоит. Она означает, что compileall не видит models.py именно в корне проекта, хотя Python при тестах откуда-то модуль models успешно импортировал. Нужно убедиться, что он не подхватывается из другого места.

Шаг 70.6 пока не закрываем окончательно. Сделаем одну проверку.

В командной строке Codespaces введи:

pwd && ls -la models.py 2>&1 && python -c "import models; print('MODELS FILE:', models.__file__)"

Нормальный результат должен содержать примерно:

/workspaces/skoomaholic-live-parser
-rw-r--r-- ... models.py
MODELS FILE: /workspaces/skoomaholic-live-parser/models.py

Скинь мне весь результат этой одной команды. Если путь именно такой, сразу закрываем 70.6 и идём в 70.7 — подключение второго источника.

 source /workspaces/skoomaholic-live-parser/.venv/bin/activate
@skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $  source /workspaces/sk
oomaholic-live-parser/.venv/bin/activate
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ pwd && ls -la models.py 2>&1 && python -c "import models; print('MODELS FILE:', models.__file__)"
/workspaces/skoomaholic-live-parser
ls: cannot access 'models.py': No such file or directory
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 


Понял, причина теперь ясна: models.py в корне проекта реально отсутствует.

Предыдущая команда не дошла до python -c ..., потому что между командами стоял &&: ls models.py упал, и выполнение остановилось. Поэтому мы пока не знаем, откуда тесты смогли импортировать models.

Номер шага: 70.6 — исправление
Этап разработки: Возвращаем models.py в правильное место
Ожидаемый результат: SportEvent гарантированно импортируется именно из /workspaces/skoomaholic-live-parser/models.py.

Сейчас сделай только это.

В VS Code слева нажми правой кнопкой именно на корневую папку:

skoomaholic-live-parser

Выбери New File и назови:

models.py

Вставь в него весь код:

from __future__ import annotations

from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Единый формат спортивного события внутри SLP."""

    source: str
    source_url: str
    channel: str

    date: str
    time: str

    sport: str
    tournament: str
    title: str

    is_live: bool
    raw_title: str

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]

Нажми Ctrl+S.

После этого в командной строке Codespaces введи:

python -c "import models; print('MODELS FILE:', models.__file__)" && python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 REALLY OK"

Нужный финал:

MODELS FILE: /workspaces/skoomaholic-live-parser/models.py
...
Ran 35 tests
OK
STEP 70.6 REALLY OK

Вот после этого 70.6 закрываем без оговорок.

models.py  лежит в __pycache__

Да, вот откуда взялась путаница.

В __pycache__ лежит не нормальный models.py, а скомпилированная копия Python, обычно файл вроде:

models.cpython-312.pyc

Именно поэтому тесты могли пройти, хотя исходного models.py в корне уже не было: Python подхватил старый кэш.

Сейчас нужно сделать правильно:

Номер шага: 70.6 — финальное исправление
Этап разработки: Восстановление исходного models.py
Ожидаемый результат: В корне проекта есть настоящий models.py, а __pycache__ больше не маскирует его отсутствие.

Создай файл здесь:

/workspaces/skoomaholic-live-parser/models.py

То есть структура должна быть:

skoomaholic-live-parser/
├── main.py
├── models.py        ← НУЖЕН ВОТ ЭТОТ
├── requirements.txt
├── parsers/
├── services/
├── tests/
├── verifiers/
└── __pycache__/
    └── models.cpython-312.pyc

В новый models.py вставь весь код:

from __future__ import annotations

from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Единый формат спортивного события внутри SLP."""

    source: str
    source_url: str
    channel: str

    date: str
    time: str

    sport: str
    tournament: str
    title: str

    is_live: bool
    raw_title: str

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]

Ctrl+S.

После этого в командной строке Codespaces введи:

rm -rf __pycache__ && python -c "import models; print('MODELS FILE:', models.__file__)" && python -m unittest discover -s tests -v && echo "STEP 70.6 REALLY OK"

Нужный результат:

MODELS FILE: /workspaces/skoomaholic-live-parser/models.py
...
OK
STEP 70.6 REALLY OK

После этого уже без подвохов идём в 70.7.

Вижу проблему. models.py сейчас лежит внутри папки tests, а должен лежать в корне проекта рядом с main.py.

Сейчас у тебя:

skoomaholic-live-parser/
├── tests/
│   ├── models.py        ← НЕПРАВИЛЬНО
│   ├── test_baseline.py
│   └── ...
├── main.py
└── ...

Нужно получить:

skoomaholic-live-parser/
├── models.py            ← ПРАВИЛЬНО
├── main.py
├── tests/
│   ├── test_baseline.py
│   └── ...
└── ...

В командной строке Codespaces введи:

mv tests/models.py ./models.py && rm -rf __pycache__ tests/__pycache__ && python -c "import models; print('MODELS FILE:', models.__file__)" && python -m unittest discover -s tests -v && echo "STEP 70.6 REALLY OK"

Нужный результат:

MODELS FILE: /workspaces/skoomaholic-live-parser/models.py
...
Ran 35 tests
OK
STEP 70.6 REALLY OK

После этого 70.6 окончательно закрыт и переходим к 70.7 — второму источнику.

Проверь 

Визуально сейчас структура правильная.

models.py находится именно там, где должен:

skoomaholic-live-parser/
├── main.py
├── models.py        ← правильно
├── parsers/
├── services/
├── tests/
└── verifiers/

Предыдущая ошибка mv: cannot stat 'tests/models.py' возникла потому, что к моменту запуска команды файл уже был перенесён в корень вручную. Это не проблема.

Номер шага: 70.6 — финальная проверка
Этап разработки: Проверка SportEvent после переноса
Ожидаемый результат: Python импортирует именно корневой models.py, проходят 35 тестов и компиляция.

У тебя сейчас $ уже есть. В командной строке Codespaces введи:

rm -rf __pycache__ tests/__pycache__ && python -c "import models; print('MODELS FILE:', models.__file__)" && python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 REALLY OK"

В самом конце должно быть:

MODELS FILE: /workspaces/skoomaholic-live-parser/models.py
...
Ran 35 tests
OK
STEP 70.6 REALLY OK

Если это выходит, 70.6 окончательно закрыт и сразу начинаем 70.7 — второй реальный источник.

mv: cannot stat 'tests/models.py': No such file or directory
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ rm -rf __pycache__ tests/__pycache__ && python -c "import models; print('MODELS FILE:', models.__file__)" && python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.6 REALLY OK"
MODELS FILE: /workspaces/skoomaholic-live-parser/models.py
test_build_url_for_date (test_baseline.QazsportBaselineTests.test_build_url_for_date) ... ok
test_clean_live_title (test_baseline.QazsportBaselineTests.test_clean_live_title) ... ok
test_extract_sport (test_baseline.QazsportBaselineTests.test_extract_sport) ... ok
test_kazakh_tournament_normalization (test_baseline.QazsportBaselineTests.test_kazakh_tournament_normalization) ... ok
test_time_to_minutes (test_baseline.QazsportBaselineTests.test_time_to_minutes) ... ok
test_uefa_conference_draw (test_baseline.QazsportBaselineTests.test_uefa_conference_draw) ... ok
test_build_sport_event_creates_universal_contract (test_event_contract.EventContractTests.test_build_sport_event_creates_universal_contract) ... ok
test_contract_preserves_adapter_specific_fields (test_event_contract.EventContractTests.test_contract_preserves_adapter_specific_fields) ... ok
test_invalid_date_is_rejected (test_event_contract.EventContractTests.test_invalid_date_is_rejected) ... ok
test_model_is_typed_dict_compatible_at_runtime (test_event_contract.EventContractTests.test_model_is_typed_dict_compatible_at_runtime) ... ok
test_title_falls_back_to_raw_title (test_event_contract.EventContractTests.test_title_falls_back_to_raw_title) ... ok
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok
test_conference_draw_stays_structured (test_qazsport_normalization.QazsportNormalizationTests.test_conference_draw_stays_structured) ... ok
test_grand_slam_judo_with_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_with_sport_prefix) ... ok
test_grand_slam_judo_without_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_without_sport_prefix) ... ok
test_half_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_half_final_backslash_is_normalized) ... ok
test_kairat_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_kairat_is_normalized) ... ok
test_quarter_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_quarter_final_backslash_is_normalized) ... ok
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok

----------------------------------------------------------------------
Ran 35 tests in 0.022s

OK
STEP 70.6 REALLY OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

Номер шага: 70.7
Этап разработки: Подключение второго спортивного источника
Ожидаемый результат: SLP начинает одновременно получать LIVE-события из Qazsport + Sport+ Qazaqstan через единый SportEvent, без отдельной логики Telegram для каждого канала.

STEP 70.6 REALLY OK означает, что 70.6 окончательно закрыт.

Для второго источника беру Sport+ Qazaqstan: у него есть публичная программа передач и явные пометки прямых трансляций вроде ПРЯМАЯ ТРАНСЛЯЦИЯ / ТІКЕЛЕЙ ЭФИР. Сейчас на странице также видны реальные спортивные трансляции, так что источник подходит для проверки универсальной архитектуры.

1. Полностью заменяем main.py

Открой main.py → Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый main.py

Теперь бот загружает оба источника параллельно. Если один временно упал, второй продолжает работать.

2. Полностью заменяем parsers/qazsport.py

Здесь заодно исправляем недочёт прошлого шага: теперь Qazsport действительно возвращает события через build_sport_event().

Открой:

parsers/qazsport.py

→ Ctrl+A → вставь весь файл → Ctrl+S.

Скачать новый qazsport.py

3. Добавляем второй адаптер

Создай:

parsers/sportplus.py

Вставь весь файл:

Скачать sportplus.py

Он делает следующее:

Sport+ TV Guide
      ↓
находит дату
      ↓
разбирает все программы
      ↓
оставляет только ПРЯМАЯ ТРАНСЛЯЦИЯ / ТІКЕЛЕЙ ЭФИР
      ↓
отсекает прямые студийные передачи
      ↓
спорт / турнир / событие
      ↓
SportEvent

И окончание прямого эфира берётся по следующей программе, как у Qazsport.

4. Добавляем два теста

Создай:

tests/test_sportplus.py

Скачать test_sportplus.py

И:

tests/test_qazsport_contract.py

Скачать test_qazsport_contract.py

Я прогнал новый код локально: новая логика Sport+ и контракт проходят тесты, компиляция тоже проходит.

5. Реальная проверка Sport+

Если $ уже виден, в командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && python -m parsers.sportplus && echo "STEP 70.7 OK"

Теперь тестов должно стать примерно 41.

В конце:

OK
Sport+ LIVE-событий: ...
...
STEP 70.7 OK

Важно: строка Sport+ LIVE-событий: — это уже реальный запрос к sportplustv.kz, а не тестовая заглушка. Если сайт отдаёт текущие прямые трансляции, ниже появятся реальные события.

Весь комплект одним архивом:

SLP_STEP_70_7.zip

После STEP 70.7 OK переходим к 70.8 — объединённому расписанию SLP, где начнём правильно обрабатывать одно и то же событие на нескольких каналах и общий дедуп.

main.py
Код
qazsport.py
Код
sportplus.py
Код
Еще 2
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ python -m unit
test discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && python -m parsers.sportplus && echo "STEP 70.7 OK"
test_build_url_for_date (test_baseline.QazsportBaselineTests.test_build_url_for_date) ... ok
test_clean_live_title (test_baseline.QazsportBaselineTests.test_clean_live_title) ... ok
test_extract_sport (test_baseline.QazsportBaselineTests.test_extract_sport) ... ok
test_kazakh_tournament_normalization (test_baseline.QazsportBaselineTests.test_kazakh_tournament_normalization) ... ok
test_time_to_minutes (test_baseline.QazsportBaselineTests.test_time_to_minutes) ... ok
test_uefa_conference_draw (test_baseline.QazsportBaselineTests.test_uefa_conference_draw) ... ok
test_build_sport_event_creates_universal_contract (test_event_contract.EventContractTests.test_build_sport_event_creates_universal_contract) ... ok
test_contract_preserves_adapter_specific_fields (test_event_contract.EventContractTests.test_contract_preserves_adapter_specific_fields) ... ok
test_invalid_date_is_rejected (test_event_contract.EventContractTests.test_invalid_date_is_rejected) ... ok
test_model_is_typed_dict_compatible_at_runtime (test_event_contract.EventContractTests.test_model_is_typed_dict_compatible_at_runtime) ... ok
test_title_falls_back_to_raw_title (test_event_contract.EventContractTests.test_title_falls_back_to_raw_title) ... ok
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok
test_conference_draw_stays_structured (test_qazsport_normalization.QazsportNormalizationTests.test_conference_draw_stays_structured) ... ok
test_grand_slam_judo_with_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_with_sport_prefix) ... ok
test_grand_slam_judo_without_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_without_sport_prefix) ... ok
test_half_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_half_final_backslash_is_normalized) ... ok
test_kairat_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_kairat_is_normalized) ... ok
test_quarter_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_quarter_final_backslash_is_normalized) ... ok
test_live_marker_is_removed (test_sportplus.SportPlusParserTests.test_live_marker_is_removed) ... ok
test_midnight_program_belongs_to_next_calendar_date (test_sportplus.SportPlusParserTests.test_midnight_program_belongs_to_next_calendar_date) ... ok
test_next_program_sets_broadcast_end (test_sportplus.SportPlusParserTests.test_next_program_sets_broadcast_end) ... ok
test_selected_day_returns_only_sport_live_events (test_sportplus.SportPlusParserTests.test_selected_day_returns_only_sport_live_events) ... ok
test_title_is_structured (test_sportplus.SportPlusParserTests.test_title_is_structured) ... ok
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok

----------------------------------------------------------------------
Ran 40 tests in 0.041s

OK
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/workspaces/skoomaholic-live-parser/parsers/sportplus.py", line 474, in <module>
    asyncio.run(main())
  File "/home/codespace/.python/current/lib/python3.12/asyncio/runners.py", line 194, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/home/codespace/.python/current/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/codespace/.python/current/lib/python3.12/asyncio/base_events.py", line 684, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "/workspaces/skoomaholic-live-parser/parsers/sportplus.py", line 460, in main
    schedule = await get_sportplus_schedule()
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspaces/skoomaholic-live-parser/parsers/sportplus.py", line 453, in get_sportplus_schedule
    return parse_sportplus_html(
           ^^^^^^^^^^^^^^^^^^^^^
  File "/workspaces/skoomaholic-live-parser/parsers/sportplus.py", line 342, in parse_sportplus_html
    raise RuntimeError(
RuntimeError: Sport+ Qazaqstan: не удалось определить даты телепрограммы
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

Поймал конкретную причину. Тесты прошли, но реальный Sport+ упал на разборе дат. Сейчас сайт отдаёт заголовки дней не только в русском формате, а, например, ДБ 24.08, СС 25.08, Бейсенбі 27.08, ЖМ 28.08, СБ 29.08. Старый DAY_RE такие варианты не понимал.

Номер шага: 70.7 — исправление
Этап разработки: Адаптация Sport+ под реальную структуру сайта
Ожидаемый результат: Парсер понимает русские и казахские заголовки дат, не падает на реальной странице и корректно вытаскивает текущие LIVE-события Sport+.

Заодно я поправил ещё два момента: URL теперь используется напрямую sportplustv.kz/ru/tvguide без www, и MEDIA BASKET ALMATY распознаётся как баскетбол.

1. Полностью заменяем parsers/sportplus.py

Открой:

parsers/sportplus.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать исправленный sportplus.py

2. Полностью заменяем тест Sport+

Открой:

tests/test_sportplus.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый test_sportplus.py

Я добавил отдельный regression-тест именно на реальные заголовки:

ДБ 24.08
СС 25.08
СР 26.08
Бейсенбі 27.08
ЖМ 28.08
СБ 29.08
ЖБ 30.08

У меня новая версия проходит 8/8 тестов Sport+.

3. Запускаем одну проверку

У тебя $ уже есть. В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && python -m parsers.sportplus && echo "STEP 70.7 OK"

Теперь нам важен уже не только OK тестов. После них должно появиться что-то вроде:

Sport+ LIVE-событий: 3

1. 2026-08-29 09:50 | Футбол | ...
2. 2026-08-29 17:00 | Баскетбол | ...
3. 2026-08-29 20:00 | Баскетбол | ...

STEP 70.7 OK

Количество может отличаться в зависимости от актуального расписания сайта.

Полный исправленный комплект:

SLP_STEP_70_7_FIX.zip

Если теперь выходит STEP 70.7 OK, второй источник считаем реально подключённым и идём в 70.8 — единое расписание Qazsport + Sport+ и дедупликация между каналами.

sportplus.py
Код
test_sportplus.py
Код
RuntimeError: Sport+ Qazaqstan: не удалось определить даты телепрограммы
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ python -m unit
test discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && python -m parsers.sportplus && echo "STEP 70.7 OK"
test_build_url_for_date (test_baseline.QazsportBaselineTests.test_build_url_for_date) ... ok
test_clean_live_title (test_baseline.QazsportBaselineTests.test_clean_live_title) ... ok
test_extract_sport (test_baseline.QazsportBaselineTests.test_extract_sport) ... ok
test_kazakh_tournament_normalization (test_baseline.QazsportBaselineTests.test_kazakh_tournament_normalization) ... ok
test_time_to_minutes (test_baseline.QazsportBaselineTests.test_time_to_minutes) ... ok
test_uefa_conference_draw (test_baseline.QazsportBaselineTests.test_uefa_conference_draw) ... ok
test_build_sport_event_creates_universal_contract (test_event_contract.EventContractTests.test_build_sport_event_creates_universal_contract) ... ok
test_contract_preserves_adapter_specific_fields (test_event_contract.EventContractTests.test_contract_preserves_adapter_specific_fields) ... ok
test_invalid_date_is_rejected (test_event_contract.EventContractTests.test_invalid_date_is_rejected) ... ok
test_model_is_typed_dict_compatible_at_runtime (test_event_contract.EventContractTests.test_model_is_typed_dict_compatible_at_runtime) ... ok
test_title_falls_back_to_raw_title (test_event_contract.EventContractTests.test_title_falls_back_to_raw_title) ... ok
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok
test_conference_draw_stays_structured (test_qazsport_normalization.QazsportNormalizationTests.test_conference_draw_stays_structured) ... ok
test_grand_slam_judo_with_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_with_sport_prefix) ... ok
test_grand_slam_judo_without_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_without_sport_prefix) ... ok
test_half_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_half_final_backslash_is_normalized) ... ok
test_kairat_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_kairat_is_normalized) ... ok
test_quarter_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_quarter_final_backslash_is_normalized) ... ok
test_current_ru_url_does_not_use_www_redirect (test_sportplus.SportPlusParserTests.test_current_ru_url_does_not_use_www_redirect) ... ok
test_kazakh_weekday_headers_are_supported (test_sportplus.SportPlusParserTests.test_kazakh_weekday_headers_are_supported) ... ok
test_live_marker_is_removed (test_sportplus.SportPlusParserTests.test_live_marker_is_removed) ... ok
test_media_basket_is_recognized_as_basketball (test_sportplus.SportPlusParserTests.test_media_basket_is_recognized_as_basketball) ... ok
test_midnight_program_belongs_to_next_calendar_date (test_sportplus.SportPlusParserTests.test_midnight_program_belongs_to_next_calendar_date) ... ok
test_next_program_sets_broadcast_end (test_sportplus.SportPlusParserTests.test_next_program_sets_broadcast_end) ... ok
test_selected_day_returns_only_sport_live_events (test_sportplus.SportPlusParserTests.test_selected_day_returns_only_sport_live_events) ... ok
test_title_is_structured (test_sportplus.SportPlusParserTests.test_title_is_structured) ... ok
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok

----------------------------------------------------------------------
Ran 43 tests in 0.027s

OK
Sport+ LIVE-событий: 3

1. 2026-08-29 11:55 | Футбол | АСТАНА 2010 – ЖАС ҚЫРАН 2010 | Sport+ Qazaqstan
2. 2026-08-29 18:00 | Конный спорт | АТ ЖАРЫСЫ МАУСЫМЫ 2026. АЛМАТЫДАН | Sport+ Qazaqstan
3. 2026-08-29 23:25 | Футбол | ГАЛАТАСАРАЙ – ГЕЗТЕПЕ | Sport+ Qazaqstan
STEP 70.7 OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

результат

Да, это правильный результат: 43 теста прошли, реальный Sport+ вернул 3 LIVE-события, и появился STEP 70.7 OK.

Номер шага: 70.8
Этап разработки: Единое расписание SLP + межканальная дедупликация
Ожидаемый результат: Qazsport и Sport+ объединяются в один список, точные дубли удаляются, одинаковое событие на разных каналах не теряется, а в Telegram такие трансляции группируются вместе.

Сейчас меняем только main.py и добавляем один сервис + один тест.

Открой main.py → Ctrl+A → вставь весь новый файл → Ctrl+S:

Скачать новый main.py

Затем создай:

services/schedule_merge.py

и вставь весь файл:

Скачать schedule_merge.py

После этого создай:

tests/test_schedule_merge.py

и вставь весь файл:

Скачать test_schedule_merge.py

Что теперь будет происходить. Если два источника дадут одно и то же событие:

Qazsport
18:50 · Кайрат – Андерлехт

Sport+ Qazaqstan
19:00 · Кайрат – Андерлехт

SLP не удалит одну из трансляций. В Telegram они будут сгруппированы примерно так:

📡 Кайрат – Андерлехт
🟡 18:50–21:00 · Qazsport
   Время совпадает
🟡 19:00–21:00 · Sport+ Qazaqstan
   Время совпадает

При этом внутри системы это всё ещё две отдельные трансляции, поэтому у каждой сохраняются своё эфирное время, канал, LIVE/SOON/OVER и интернет-проверка. Это важно для будущей операционной логики.

Точные повторы одного и того же эфира, наоборот, удаляются.

Я прогнал новую merge-логику вместе с текущим проектом: тесты проходят.

Теперь у тебя $ уже есть. В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers && echo "STEP 70.8 OK"

У тебя было 43 теста, после добавления новых должно стать примерно 50.

Нужный финал:

OK
STEP 70.8 OK

Весь комплект:

SLP_STEP_70_8.zip

После этого перейдём к 70.9 — финальной стабилизации Telegram, и туда же я включу обязательную систему уведомлений об изменениях расписания, о которой мы говорили раньше.

main.py
Код
schedule_merge.py
Код
test_schedule_merge.py
Код
тут тестировать бот можно как то?

Да. Но эта вкладка в VS Code предназначена для автотестов кода, а не для полноценного кликанья по Telegram-боту.

У тебя сейчас два способа тестирования:

Автотесты — именно через эту вкладку с колбой. Твои test_*.py из папки tests можно запускать отсюда кнопками. Сейчас VS Code просто ещё не настроен на unittest, поэтому пишет «Тесты не найдены».
Сам Telegram-бот — запускаем в терминале:
python main.py

После строки:

SLP запущен. Ожидаю сообщения в Telegram...

открываешь @Skoomaholic_Live_Parser_Bot и уже руками проверяешь:
📅 Расписание, 🔴 LIVE, подробности, Sport+, Qazsport и т. д.

Если хочешь настроить именно эту панель, можешь прямо сейчас нажать Configure Python Tests → выбрать unittest → выбрать папку tests.

После этого здесь появится дерево примерно:

tests
├── test_baseline
├── test_event_contract
├── test_openserp_extract
├── test_openserp_speed
├── test_qazsport_normalization
├── test_schedule_merge
├── test_sportplus
└── test_time_logic

И можно будет запускать один конкретный тест кнопкой ▶ или сразу все.

Но для нашей разработки это необязательно: команда в терминале python -m unittest discover -s tests -v уже делает ровно то же самое и пока надёжнее.

А вот после STEP 70.8 OK я бы уже запустил python main.py и сделал живой smoke-test бота в Telegram перед 70.9.

ок. А как выгрузить весь проект?

Самый простой способ — выгрузить проект из Codespaces как ZIP.

В командной строке Codespaces введи:

cd /workspaces && zip -r skoomaholic-live-parser.zip skoomaholic-live-parser -x "skoomaholic-live-parser/.venv/*" "skoomaholic-live-parser/__pycache__/*" "skoomaholic-live-parser/**/__pycache__/*"

После этого появится файл:

/workspaces/skoomaholic-live-parser.zip

Дальше слева в проводнике VS Code открой папку /workspaces, найди skoomaholic-live-parser.zip, нажми по нему правой кнопкой и выбери Download.

Я специально исключил .venv и __pycache__, чтобы архив не был огромным и не тащил мусор. Весь код, тесты, requirements.txt, README.md, парсеры и сервисы останутся внутри.

Вставленный текст(20260829-015936).txt
Документ
файла не вижу что то 



Архив создался нормально. По логу видно, что zip успешно упаковал main.py, сервисы, парсеры, тесты и models.py.

Ты его не видишь потому, что он лежит на уровень выше проекта:

/workspaces/skoomaholic-live-parser.zip

а проводник VS Code у тебя открыт только на:

/workspaces/skoomaholic-live-parser/

Сделаем, чтобы архив появился прямо в проводнике. В командной строке Codespaces введи:

mv /workspaces/skoomaholic-live-parser.zip /workspaces/skoomaholic-live-parser/SLP_PROJECT.zip

После этого слева рядом с:

main.py
models.py
README.md
requirements.txt

должен появиться:

SLP_PROJECT.zip

Если сразу не появился, нажми значок ↻ Refresh в верхней части «Проводника».

Дальше правой кнопкой по SLP_PROJECT.zip → Download.

Кстати, текущий архив захватил ещё и .git. Это не ошибка, но архив получается тяжелее. После того как убедимся, что скачивание работает, могу дать одну команду для создания чистого резервного архива SLP только с нужными файлами проекта, без .git, .venv и кэша.

SLP_PROJECT.zip
ZIP-архив
можешь его проверить? есть ли там ошибки или лишнее что то?

Да. Я распаковал архив, прошёлся по структуре, прогнал тесты и компиляцию.

Общее состояние хорошее: все 50 тестов проходят, compileall проходит, синтаксических ошибок нет. Но я нашёл одну важную функциональную проблему и несколько вещей, которые лучше почистить до Parser v1.

Приоритет	Что нашёл	Что это значит
🔴 Важно	services/schedule_merge.py написан и протестирован, но вообще не подключён в main.py	Шаг 70.8 фактически ещё не завершён. Бот продолжает использовать старые remove_duplicates() и sort_events() из main.py, а group_simulcasts() и merge_source_schedules() не работают в реальном Telegram
🟠 Важно перед уведомлениями	get_event_id() включает время события	Если матч перенесли с 18:50 на 19:20, ID изменится. Для будущих уведомлений бот может воспринять это как «старое событие исчезло + новое появилось», а не «изменилось время»
🟠	Нет системы хранения предыдущего расписания	Уведомления об изменениях пока технически невозможны. После рестарта бот вообще ничего не помнит
🟡	main.py содержит дублирующую логику	remove_duplicates, sort_events, MONTHS, format_date, format_full_date частично дублируют уже созданные сервисы
🟡	main.py всё ещё пишет Текущий этап: Step 70.7	Просто устаревший текст после 70.8
🟡	Заголовок расписания жёстко содержит 2 канала	После третьего источника станет неправильным. Нужно вычислять количество каналов автоматически
🟡	Ошибки источников слишком тихие	_safe_source_load() при падении канала возвращает []. Пользователь может увидеть пустое расписание и не понять, что источник сломался
🟡	background_verify_and_refresh() содержит except Exception: pass	Некоторые ошибки Telegram сейчас полностью проглатываются
🟡	README.md практически пустой	На другом Codespace/ПК непонятно, как установить OpenSERP, задать BOT_TOKEN и запустить проект
🟢	requirements.txt теперь нормальный	Всего 3 прямые Python-зависимости. Это правильно
🟢	Секретов в архиве не нашёл	BOT_TOKEN берётся из environment, самого токена внутри проекта нет
Что лишнее в архиве

Главное лишнее — папка .git. Из-за неё в ZIP 241 запись, хотя реально рабочих файлов проекта около 20. Для личного полного backup это допустимо, но для отправки кому-либо .git лучше не класть: там вся история Git и адрес репозитория.

Ещё лишний:

logs.txt

Там около 900 строк старых логов OpenSERP, включая Google/Yandex/Ecosia до того, как мы их отключили. Для работы проекта он не нужен.

При этом .venv и __pycache__ в архив не попали — это хорошо.

Ещё одна небольшая проблема в merge

В schedule_merge.py:

Қайрат
Кайрат

нормализуются друг к другу.

Но:

Kairat

латиницей пока не станет автоматически тем же названием именно в merge-модуле. В интернет-verifier aliases для Kairat уже есть, а в межканальной дедупликации — нет. На разных источниках это потенциально даст две отдельные группы.

И ещё same_sporting_event() требует одинаковую календарную дату. Поэтому одинаковый матч:

Qazsport: 29 августа 23:55
Sport+:   30 августа 00:05

не будет сгруппирован, хотя разница всего 10 минут.

Главный вывод

Проект сейчас не развален. Напротив, база уже довольно аккуратная:

50/50 тестов проходят;
Qazsport и Sport+ реально подключены;
единый SportEvent есть;
время вынесено в сервис;
OpenSERP вынесен в verifier;
переход через полночь протестирован;
внешний интернет больше не управляет LIVE/SOON/OVER.

Но 70.8 я бы сейчас не считал закрытым, потому что мы написали хорошую merge-логику, протестировали её, но забыли подключить её к реальному main.py. Именно поэтому тесты всё равно зелёные: они тестируют schedule_merge.py отдельно, а не то, что Telegram реально его использует.

Поэтому следующий шаг я бы сделал не 70.9, а короткий:

Номер шага: 70.8.1
Этап разработки: Подключение готового merge-сервиса к реальному боту
Ожидаемый результат: Telegram действительно использует merge_source_schedules() и group_simulcasts(), количество каналов считается автоматически, старые remove_duplicates/sort_events удаляются, а отдельный integration-тест гарантирует, что такая ситуация больше не повторится.

После этого уже 70.9 с уведомлениями.

тогда какой следующий шаг?

Номер шага: 70.8.1
Этап разработки: Подключение merge-сервиса к реальному Telegram-боту
Ожидаемый результат: main.py реально использует merge_source_schedules() и group_simulcasts(), Qazsport + Sport+ корректно объединяются, одинаковые события на разных каналах группируются, старые дублирующие функции из main.py удаляются.

Именно его делаем следующим, потому что сейчас 70.8 протестирован как отдельный сервис, но ещё не доведён до реального Telegram-потока.

После него порядок такой:

70.9 — финальная стабилизация Telegram + система уведомлений об изменениях расписания.
71 — финальный SLP Parser v1 и контрольный релиз.

То есть сейчас не прыгаем к уведомлениям, сначала закрываем найденную дыру 70.8.1.

ну тогда приступаем, рассказывай что делать

Номер шага: 70.8.1
Этап разработки: Подключение merge-сервиса к реальному Telegram-боту
Ожидаемый результат: main.py реально использует объединение Qazsport + Sport+, одинаковые события на разных каналах группируются, количество каналов считается автоматически, а старые дублирующие функции удалены.

Я уже внёс правки в копию твоего текущего проекта и прогнал всё целиком: 57 тестов из 57 прошли, компиляция тоже без ошибок.

Сейчас тебе нужно заменить 3 файла и добавить 1 новый.

1. Полностью заменяем main.py

Открой:

main.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый main.py

Что изменилось:

подключены merge_source_schedules(), group_simulcasts(), unique_channels();
удалены старые remove_duplicates() и sort_events();
Qazsport и Sport+ теперь реально проходят через общий merge;
Telegram группирует одинаковую трансляцию с разных каналов;
вместо жёсткого 2 канала количество считается автоматически;
ошибки фонового обновления больше не проглатываются молча;
статус обновлён до Step 70.8.1.
2. Полностью заменяем services/schedule_merge.py

Открой:

services/schedule_merge.py

→ Ctrl+A → вставь весь файл → Ctrl+S.

Скачать новый schedule_merge.py

Заодно здесь исправлены две найденные ранее проблемы:

Қайрат
Кайрат
Kairat

теперь могут совпадать при сравнении.

И такой случай:

29 августа 23:55
30 августа 00:05

может быть распознан как одно спортивное событие, если это разные каналы и разница укладывается в допустимые 30 минут.

3. Полностью заменяем старый тест merge

Открой:

tests/test_schedule_merge.py

→ Ctrl+A → вставь весь файл → Ctrl+S.

Скачать новый test_schedule_merge.py

4. Добавляем integration-тест

Создай новый файл:

tests/test_main_merge_integration.py

Вставь весь файл:

Скачать test_main_merge_integration.py

Это важный тест. Он теперь специально проверяет, что мы снова не окажемся в ситуации:

сервис написан
тесты сервиса зелёные
но main.py его вообще не использует

Он проверяет, что main.py реально импортирует и вызывает merge-сервис.

5. Контрольный запуск

У тебя сейчас уже должен быть $.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"

Теперь должно быть:

Ran 57 tests
OK
STEP 70.8.1 OK

Если хочешь заменить всё из одного архива:

Скачать SLP_STEP_70_8_1.zip

После STEP 70.8.1 OK уже идём в:

Номер шага: 70.9
Этап разработки: Telegram stabilization + уведомления об изменениях событий
Ожидаемый результат: бот запоминает предыдущее расписание, обнаруживает перенос времени, изменение канала, появление/исчезновение LIVE-события и отправляет уведомление в Telegram.

main.py
Код
schedule_merge.py
Код
test_schedule_merge.py
Код
Еще 1
это что за текст?

Это нормально. VS Code говорит, что ты открыл не текстовый файл, а бинарный, скорее всего SLP_STEP_70_8_1.zip.

ZIP нельзя редактировать как обычный .py, поэтому и появляется:

«Файл не отображается в текстовом редакторе, так как является двоичным…»

Кнопку «Всё равно открыть» не нажимай.

Для шага 70.8.1 тебе нужны именно отдельные файлы:

main.py
services/schedule_merge.py
tests/test_schedule_merge.py
tests/test_main_merge_integration.py

Открывай соответствующий .py в проводнике VS Code и вставляй туда код.

Если ты скачал только SLP_STEP_70_8_1.zip и хочешь распаковать его прямо в Codespaces, скажи — дам одну команду, которая разложит файлы по нужным папкам автоматически.

(.venv) @skoomaholic-art ➜ /workspaces $ python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/home/codespace/.python/current/lib/python3.12/unittest/__main__.py", line 18, in <module>
    main(module=None)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 104, in __init__
    self.parseArgs(argv)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 130, in parseArgs
    self._do_discovery(argv[2:])
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 253, in _do_discovery
    self.createTests(from_discovery=True, Loader=Loader)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 160, in createTests
    self.test = loader.discover(self.start, self.pattern, self.top)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 307, in discover
    raise ImportError('Start directory is not importable: %r' % start_dir)
ImportError: Start directory is not importable: 'tests'
(.venv) @skoomaholic-art ➜ /workspaces $ ^C
(.venv) @skoomaholic-art ➜ /workspaces $ ^C
(.venv) @skoomaholic-art ➜ /workspaces $ 

Ошибка простая: ты запускаешь тесты из /workspaces, а папка tests находится внутри проекта.

Сейчас у тебя приглашение:

/workspaces $

Нужно сначала зайти в проект.

Номер шага: 70.8.1 — контрольный запуск
Этап разработки: Запуск тестов из правильной директории
Ожидаемый результат: 57 тестов проходят и появляется STEP 70.8.1 OK.

В командной строке Codespaces введи:

cd /workspaces/skoomaholic-live-parser && python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"

В конце должно быть примерно:

Ran 57 tests
OK
STEP 70.8.1 OK

То есть с кодом эта конкретная ошибка не связана, просто команда была запущена на один уровень выше проекта.

не понимаю как выйти в проект

Тебе не нужно «выходить» куда-то отдельно. Просто в терминале введи одну команду перехода в папку проекта:

cd /workspaces/skoomaholic-live-parser

Нажми Enter.

После этого строка должна измениться примерно на:

(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $

Вот это и значит, что ты находишься внутри проекта.

Дальше уже запускай тесты:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"

То есть сейчас первый шаг только один:

cd /workspaces/skoomaholic-live-parser
(.venv) @skoomaholic-art ➜ /workspaces $ python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/home/codespace/.python/current/lib/python3.12/unittest/__main__.py", line 18, in <module>
    main(module=None)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 104, in __init__
    self.parseArgs(argv)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 130, in parseArgs
    self._do_discovery(argv[2:])
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 253, in _do_discovery
    self.createTests(from_discovery=True, Loader=Loader)
  File "/home/codespace/.python/current/lib/python3.12/unittest/main.py", line 160, in createTests
    self.test = loader.discover(self.start, self.pattern, self.top)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/codespace/.python/current/lib/python3.12/unittest/loader.py", line 307, in discover
    raise ImportError('Start directory is not importable: %r' % start_dir)
ImportError: Start directory is not importable: 'tests'
(.venv) @skoomaholic-art ➜ /workspaces $ ^C
(.venv) @skoomaholic-art ➜ /workspaces $ ^C
(.venv) @skoomaholic-art ➜ /workspaces $ cd /workspaces/skoomaholic-live-parser && python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"
test_build_url_for_date (test_baseline.QazsportBaselineTests.test_build_url_for_date) ... ok
test_clean_live_title (test_baseline.QazsportBaselineTests.test_clean_live_title) ... ok
test_extract_sport (test_baseline.QazsportBaselineTests.test_extract_sport) ... ok
test_kazakh_tournament_normalization (test_baseline.QazsportBaselineTests.test_kazakh_tournament_normalization) ... ok
test_time_to_minutes (test_baseline.QazsportBaselineTests.test_time_to_minutes) ... ok
test_uefa_conference_draw (test_baseline.QazsportBaselineTests.test_uefa_conference_draw) ... ok
test_build_sport_event_creates_universal_contract (test_event_contract.EventContractTests.test_build_sport_event_creates_universal_contract) ... ok
test_contract_preserves_adapter_specific_fields (test_event_contract.EventContractTests.test_contract_preserves_adapter_specific_fields) ... ok
test_invalid_date_is_rejected (test_event_contract.EventContractTests.test_invalid_date_is_rejected) ... ok
test_model_is_typed_dict_compatible_at_runtime (test_event_contract.EventContractTests.test_model_is_typed_dict_compatible_at_runtime) ... ok
test_title_falls_back_to_raw_title (test_event_contract.EventContractTests.test_title_falls_back_to_raw_title) ... ok
test_channel_count_is_not_hardcoded (test_main_merge_integration.MainMergeIntegrationTests.test_channel_count_is_not_hardcoded) ... ok
test_load_schedule_uses_merge_source_schedules (test_main_merge_integration.MainMergeIntegrationTests.test_load_schedule_uses_merge_source_schedules) ... ok
test_main_imports_merge_service (test_main_merge_integration.MainMergeIntegrationTests.test_main_imports_merge_service) ... ok
test_old_duplicate_helpers_are_removed (test_main_merge_integration.MainMergeIntegrationTests.test_old_duplicate_helpers_are_removed) ... ok
test_telegram_views_use_simulcast_groups (test_main_merge_integration.MainMergeIntegrationTests.test_telegram_views_use_simulcast_groups) ... ok
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok
test_conference_draw_stays_structured (test_qazsport_normalization.QazsportNormalizationTests.test_conference_draw_stays_structured) ... ok
test_grand_slam_judo_with_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_with_sport_prefix) ... ok
test_grand_slam_judo_without_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_without_sport_prefix) ... ok
test_half_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_half_final_backslash_is_normalized) ... ok
test_kairat_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_kairat_is_normalized) ... ok
test_quarter_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_quarter_final_backslash_is_normalized) ... ok
test_cross_midnight_same_event_is_grouped (test_schedule_merge.ScheduleMergeTests.test_cross_midnight_same_event_is_grouped) ... ok
test_draws_from_different_tournaments_are_not_grouped (test_schedule_merge.ScheduleMergeTests.test_draws_from_different_tournaments_are_not_grouped) ... ok
test_kairat_latin_and_cyrillic_are_grouped (test_schedule_merge.ScheduleMergeTests.test_kairat_latin_and_cyrillic_are_grouped) ... ok
test_kazakh_letters_are_normalized_only_for_matching (test_schedule_merge.ScheduleMergeTests.test_kazakh_letters_are_normalized_only_for_matching) ... ok
test_same_channel_rebroadcast_is_not_simulcast (test_schedule_merge.ScheduleMergeTests.test_same_channel_rebroadcast_is_not_simulcast) ... ok
test_same_event_on_two_channels_is_grouped_for_display (test_schedule_merge.ScheduleMergeTests.test_same_event_on_two_channels_is_grouped_for_display) ... ok
test_same_event_on_two_channels_is_preserved (test_schedule_merge.ScheduleMergeTests.test_same_event_on_two_channels_is_preserved) ... ok
test_true_duplicate_broadcast_is_removed (test_schedule_merge.ScheduleMergeTests.test_true_duplicate_broadcast_is_removed) ... ok
test_unique_channels (test_schedule_merge.ScheduleMergeTests.test_unique_channels) ... ok
test_current_ru_url_does_not_use_www_redirect (test_sportplus.SportPlusParserTests.test_current_ru_url_does_not_use_www_redirect) ... ok
test_kazakh_weekday_headers_are_supported (test_sportplus.SportPlusParserTests.test_kazakh_weekday_headers_are_supported) ... ok
test_live_marker_is_removed (test_sportplus.SportPlusParserTests.test_live_marker_is_removed) ... ok
test_media_basket_is_recognized_as_basketball (test_sportplus.SportPlusParserTests.test_media_basket_is_recognized_as_basketball) ... ok
test_midnight_program_belongs_to_next_calendar_date (test_sportplus.SportPlusParserTests.test_midnight_program_belongs_to_next_calendar_date) ... ok
test_next_program_sets_broadcast_end (test_sportplus.SportPlusParserTests.test_next_program_sets_broadcast_end) ... ok
test_selected_day_returns_only_sport_live_events (test_sportplus.SportPlusParserTests.test_selected_day_returns_only_sport_live_events) ... ok
test_title_is_structured (test_sportplus.SportPlusParserTests.test_title_is_structured) ... ok
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok

----------------------------------------------------------------------
Ran 57 tests in 0.041s

OK
STEP 70.8.1 OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

Отлично. 70.8.1 закрыт полностью.

Главное подтверждение:

Ran 57 tests
OK
STEP 70.8.1 OK

То есть сейчас:

merge-сервис реально подключён к main.py;
Qazsport + Sport+ проходят через общий поток;
simulcast-группировка работает;
межканальный дедуп работает;
Kairat / Кайрат / Қайрат учитываются;
переход через полночь протестирован;
интеграционный тест защищает от ситуации, когда сервис написан, но не используется.

Перед следующим крупным шагом сделаем контрольную точку Git, чтобы можно было мгновенно откатиться.

В командной строке Codespaces введи:

git add . && git commit -m "Complete step 70.8.1 merge integration" && git push

После успешного push начинаем:

Номер шага: 70.9
Этап разработки: Финальная стабилизация Telegram + уведомления об изменениях расписания
Ожидаемый результат: SLP запоминает прошлое состояние расписания и сообщает, когда:

изменилось время события;
изменился канал;
появилось новое LIVE-событие;
событие исчезло;
существенно изменилось окончание эфира.

Причём перенос времени должен определяться именно как изменение существующего события, а не как «старое удалилось + новое появилось». Это как раз исправим в архитектуре ID событий.

Вставленный текст(20260829-021613).txt
Документ

Всё хорошо: 70.8.1 прошёл дважды подряд без ошибок — оба раза Ran 57 tests, OK, STEP 70.8.1 OK.

В самом конце у тебя сейчас набрано:

cd

Если оно ещё висит в строке и ты не нажал Enter — нажми Ctrl+C, чтобы очистить строку.

После этого делаем контрольную точку Git.

Номер шага: 70.8.1 — фиксация стабильной версии
Этап разработки: Сохранение рабочего состояния в Git
Ожидаемый результат: текущая версия с 57 зелёными тестами сохранена и отправлена в GitHub.

В командной строке Codespaces введи:

git add . && git commit -m "Complete step 70.8.1 merge integration" && git push

Пришли мне результат этой команды. После успешного push сразу начинаем 70.9 — уведомления об изменениях расписания и финальная стабилизация Telegram.

OK
STEP 70.8.1 OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.8.1 OK"
test_build_url_for_date (test_baseline.QazsportBaselineTests.test_build_url_for_date) ... ok
test_clean_live_title (test_baseline.QazsportBaselineTests.test_clean_live_title) ... ok
test_extract_sport (test_baseline.QazsportBaselineTests.test_extract_sport) ... ok
test_kazakh_tournament_normalization (test_baseline.QazsportBaselineTests.test_kazakh_tournament_normalization) ... ok
test_time_to_minutes (test_baseline.QazsportBaselineTests.test_time_to_minutes) ... ok
test_uefa_conference_draw (test_baseline.QazsportBaselineTests.test_uefa_conference_draw) ... ok
test_build_sport_event_creates_universal_contract (test_event_contract.EventContractTests.test_build_sport_event_creates_universal_contract) ... ok
test_contract_preserves_adapter_specific_fields (test_event_contract.EventContractTests.test_contract_preserves_adapter_specific_fields) ... ok
test_invalid_date_is_rejected (test_event_contract.EventContractTests.test_invalid_date_is_rejected) ... ok
test_model_is_typed_dict_compatible_at_runtime (test_event_contract.EventContractTests.test_model_is_typed_dict_compatible_at_runtime) ... ok
test_title_falls_back_to_raw_title (test_event_contract.EventContractTests.test_title_falls_back_to_raw_title) ... ok
test_channel_count_is_not_hardcoded (test_main_merge_integration.MainMergeIntegrationTests.test_channel_count_is_not_hardcoded) ... ok
test_load_schedule_uses_merge_source_schedules (test_main_merge_integration.MainMergeIntegrationTests.test_load_schedule_uses_merge_source_schedules) ... ok
test_main_imports_merge_service (test_main_merge_integration.MainMergeIntegrationTests.test_main_imports_merge_service) ... ok
test_old_duplicate_helpers_are_removed (test_main_merge_integration.MainMergeIntegrationTests.test_old_duplicate_helpers_are_removed) ... ok
test_telegram_views_use_simulcast_groups (test_main_merge_integration.MainMergeIntegrationTests.test_telegram_views_use_simulcast_groups) ... ok
test_batch_payload_uses_native_openserp_endpoint (test_openserp_extract.OpenSerpExtractTests.test_batch_payload_uses_native_openserp_endpoint) ... ok
test_draw_requires_correct_tournament (test_openserp_extract.OpenSerpExtractTests.test_draw_requires_correct_tournament) ... ok
test_event_kind_match_draw_generic (test_openserp_extract.OpenSerpExtractTests.test_event_kind_match_draw_generic) ... ok
test_kairat_aliases_cover_kazakh_russian_english (test_openserp_extract.OpenSerpExtractTests.test_kairat_aliases_cover_kazakh_russian_english) ... ok
test_match_works_with_english_names (test_openserp_extract.OpenSerpExtractTests.test_match_works_with_english_names) ... ok
test_page_context_beats_broadcast_nearest_time (test_openserp_extract.OpenSerpExtractTests.test_page_context_beats_broadcast_nearest_time) ... ok
test_baidu_is_fallback_only (test_openserp_speed.OpenSerpSpeedTests.test_baidu_is_fallback_only) ... ok
test_baidu_runs_only_when_primary_found_nothing (test_openserp_speed.OpenSerpSpeedTests.test_baidu_runs_only_when_primary_found_nothing) ... ok
test_no_second_search_when_event_pages_found (test_openserp_speed.OpenSerpSpeedTests.test_no_second_search_when_event_pages_found) ... ok
test_primary_engines_are_fast_pair (test_openserp_speed.OpenSerpSpeedTests.test_primary_engines_are_fast_pair) ... ok
test_time_status_has_no_accuracy_icons (test_openserp_speed.OpenSerpSpeedTests.test_time_status_has_no_accuracy_icons) ... ok
test_conference_draw_stays_structured (test_qazsport_normalization.QazsportNormalizationTests.test_conference_draw_stays_structured) ... ok
test_grand_slam_judo_with_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_with_sport_prefix) ... ok
test_grand_slam_judo_without_sport_prefix (test_qazsport_normalization.QazsportNormalizationTests.test_grand_slam_judo_without_sport_prefix) ... ok
test_half_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_half_final_backslash_is_normalized) ... ok
test_kairat_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_kairat_is_normalized) ... ok
test_quarter_final_backslash_is_normalized (test_qazsport_normalization.QazsportNormalizationTests.test_quarter_final_backslash_is_normalized) ... ok
test_cross_midnight_same_event_is_grouped (test_schedule_merge.ScheduleMergeTests.test_cross_midnight_same_event_is_grouped) ... ok
test_draws_from_different_tournaments_are_not_grouped (test_schedule_merge.ScheduleMergeTests.test_draws_from_different_tournaments_are_not_grouped) ... ok
test_kairat_latin_and_cyrillic_are_grouped (test_schedule_merge.ScheduleMergeTests.test_kairat_latin_and_cyrillic_are_grouped) ... ok
test_kazakh_letters_are_normalized_only_for_matching (test_schedule_merge.ScheduleMergeTests.test_kazakh_letters_are_normalized_only_for_matching) ... ok
test_same_channel_rebroadcast_is_not_simulcast (test_schedule_merge.ScheduleMergeTests.test_same_channel_rebroadcast_is_not_simulcast) ... ok
test_same_event_on_two_channels_is_grouped_for_display (test_schedule_merge.ScheduleMergeTests.test_same_event_on_two_channels_is_grouped_for_display) ... ok
test_same_event_on_two_channels_is_preserved (test_schedule_merge.ScheduleMergeTests.test_same_event_on_two_channels_is_preserved) ... ok
test_true_duplicate_broadcast_is_removed (test_schedule_merge.ScheduleMergeTests.test_true_duplicate_broadcast_is_removed) ... ok
test_unique_channels (test_schedule_merge.ScheduleMergeTests.test_unique_channels) ... ok
test_current_ru_url_does_not_use_www_redirect (test_sportplus.SportPlusParserTests.test_current_ru_url_does_not_use_www_redirect) ... ok
test_kazakh_weekday_headers_are_supported (test_sportplus.SportPlusParserTests.test_kazakh_weekday_headers_are_supported) ... ok
test_live_marker_is_removed (test_sportplus.SportPlusParserTests.test_live_marker_is_removed) ... ok
test_media_basket_is_recognized_as_basketball (test_sportplus.SportPlusParserTests.test_media_basket_is_recognized_as_basketball) ... ok
test_midnight_program_belongs_to_next_calendar_date (test_sportplus.SportPlusParserTests.test_midnight_program_belongs_to_next_calendar_date) ... ok
test_next_program_sets_broadcast_end (test_sportplus.SportPlusParserTests.test_next_program_sets_broadcast_end) ... ok
test_selected_day_returns_only_sport_live_events (test_sportplus.SportPlusParserTests.test_selected_day_returns_only_sport_live_events) ... ok
test_title_is_structured (test_sportplus.SportPlusParserTests.test_title_is_structured) ... ok
test_external_time_does_not_affect_status (test_time_logic.TimeLogicTests.test_external_time_does_not_affect_status) ... ok
test_finished_status (test_time_logic.TimeLogicTests.test_finished_status) ... ok
test_last_event_gets_fallback_end (test_time_logic.TimeLogicTests.test_last_event_gets_fallback_end) ... ok
test_live_status (test_time_logic.TimeLogicTests.test_live_status) ... ok
test_midnight_rollover_when_end_date_is_wrong (test_time_logic.TimeLogicTests.test_midnight_rollover_when_end_date_is_wrong) ... ok
test_same_day_schedule (test_time_logic.TimeLogicTests.test_same_day_schedule) ... ok
test_upcoming_status (test_time_logic.TimeLogicTests.test_upcoming_status) ... ok

----------------------------------------------------------------------
Ran 57 tests in 0.042s

OK
STEP 70.8.1 OK
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ cd /workspaces/skoomaholic-live-parser
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ ^C
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ (.

Да, всё в порядке: 70.8.1 снова прошёл успешно — 57/57 тестов зелёные.

У тебя в самом конце после $ остались лишние символы (.. Нажми Ctrl+C, чтобы очистить строку.

Дальше делаем контрольную точку Git.

Номер шага: 70.8.1 — фиксация стабильной версии
Этап разработки: Сохранение текущего рабочего состояния в GitHub
Ожидаемый результат: версия с 57 зелёными тестами сохранена в репозитории.

В командной строке Codespaces введи:

git add . && git commit -m "Complete step 70.8.1 merge integration" && git push

После успешного выполнения пришли результат. Затем сразу переходим к 70.9 — уведомления об изменениях расписания и финальная стабилизация Telegram-бота.

Вставленный текст(20260829-021800).txt
Документ

Готово. Git checkpoint прошёл успешно:

commit создан: 5d0d073
ветка main
git push завершился успешно
GitHub обновлён до новой версии 656589b..5d0d073.

Куча bash: syntax error выше не относится к коду: в терминал случайно попал старый вывод команд, и Bash попытался выполнить его как команды. До этого сами 57 тестов прошли успешно, поэтому проект не пострадал.

Нашёл только один момент: в commit случайно попали:

SLP_PROJECT.zip
logs.txt

Это лишнее для репозитория. Я включу их очистку и нормальный .gitignore в начало следующего шага, чтобы больше архивы и логи случайно не коммитились.

Теперь переходим дальше.

Номер шага: 70.9
Этап разработки: Уведомления об изменениях расписания + финальная стабилизация Telegram
Ожидаемый результат: бот будет запоминать состояние расписания и обнаруживать перенос времени, смену канала, появление/исчезновение события и существенное изменение окончания эфира, при этом обычное обновление страницы не будет спамить уведомлениями.

Следующим сообщением могу сразу дать полный комплект файлов и пошагово начать 70.9.

го

Номер шага: 70.9
Этап разработки: Уведомления об изменениях расписания + финальная стабилизация Telegram
Ожидаемый результат: бот каждые 3 минуты перепроверяет расписание, запоминает прошлое состояние и уведомляет только о реальных изменениях.

Я собрал этот шаг на базе текущей версии 70.8.1. Локально прогнал весь комплект: 75 тестов из 75 прошли, компиляция тоже прошла.

Что появится после 70.9:

🔔 Изменение расписания
Кайрат – Андерлехт
🕐 Qazsport: 29.08 18:50 → 29.08 19:20

или:

🔔 Изменение расписания
Кайрат – Андерлехт
📺 Канал: Qazsport → Sport+ Qazaqstan

Также бот увидит:

новое LIVE-событие;
исчезновение события до предполагаемого окончания;
перенос времени;
изменение канала;
изменение окончания эфира на 15 минут и больше.

При этом обычное завершение уже прошедшего события не считается удалением и не вызовет ложное уведомление.

1. Полностью заменяем main.py

Открой:

main.py

→ Ctrl+A → вставь весь новый файл → Ctrl+S.

Скачать новый main.py

В Telegram появится новая кнопка:

🔔 Уведомления

Нажатие включает или выключает подписку именно для твоего чата.

2. Создаём сервис мониторинга

Создай:

services/schedule_watch.py

и вставь весь файл:

Скачать schedule_watch.py

Он отвечает за:

текущее расписание
        ↓
снимок состояния
        ↓
сравнение с предыдущим
        ↓
определение типа изменения
        ↓
Telegram notification

Важный момент: стабильный ID события теперь для мониторинга не зависит от времени и канала.

Поэтому:

18:50 · Qazsport · Кайрат – Андерлехт

и позже:

19:20 · Qazsport · Кайрат – Андерлехт

распознаются как одно событие с изменившимся временем, а не как удаление старого и появление нового.

3. Добавляем тест мониторинга

Создай:

tests/test_schedule_watch.py

Скачать test_schedule_watch.py

Там проверяется в том числе:

перенос времени
смена канала
новое событие
удаление события
естественное завершение без ложного alert
изменение окончания >= 15 минут
simulcast
сохранение подписки
сохранение snapshot
4. Добавляем integration-тест

Создай:

tests/test_main_notifications_integration.py

Скачать test_main_notifications_integration.py

Он проверяет уже не только отдельный сервис, но и то, что main.py реально запускает мониторинг и использует его.

5. Полностью заменяем .gitignore

Открой:

.gitignore

→ Ctrl+A → вставь весь файл → Ctrl+S.

Скачать новый .gitignore

Теперь Git автоматически игнорирует:

slp_state.json
logs.txt
*.zip
.venv
__pycache__
.env

slp_state.json будет локальной памятью бота. Там хранятся подписчики и последний snapshot расписания.

6. Убираем уже закоммиченные ZIP и лог из Git

В командной строке Codespaces введи:

git rm --cached -f SLP_PROJECT.zip logs.txt

Файлы могут остаться у тебя локально, но Git больше их отслеживать не будет.

7. Контрольные тесты

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.9 TESTS OK"

Ожидаемый финал:

Ran 75 tests
OK
STEP 70.9 TESTS OK
8. После зелёных тестов запускаем уже сам бот
python main.py

В терминале должно появиться:

SLP запущен. Ожидаю сообщения в Telegram...
SLP notifications: проверка каждые 180 сек.
SLP notifications: базовый снимок расписания сохранён

После этого открываешь Telegram → @Skoomaholic_Live_Parser_Bot → /start.

Должна появиться кнопка:

🔔 Уведомления

Нажми её один раз. Бот должен ответить:

🔔 Уведомления включены.

SLP сообщит об изменении времени, канала,
существенном изменении окончания эфира,
появлении или исчезновении LIVE-события.

Первый запуск специально не рассылает все существующие события. Он сначала сохраняет базовый снимок и начинает сравнивать с ним следующие обновления.

Весь шаг одним архивом:

Скачать SLP_STEP_70_9.zip

Сначала сделай пункты 1–7 и пришли результат строки STEP 70.9 TESTS OK. После этого уже проведём живой smoke-test Telegram.

main.py
Код
schedule_watch.py
Код
test_schedule_watch.py
Код
Еще 1
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ git rm --cached -f SLP_PROJECT.zip logs.txt
rm 'SLP_PROJECT.zip'
rm 'logs.txt'
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ git rm --cached -f SLP_PROJECT.zip logs.txt
fatal: pathspec 'SLP_PROJECT.zip' did not match any files
(.venv) @skoomaholic-art ➜ /workspaces/skoomaholic-live-parser (main) $ 

Всё нормально. Первая команда сработала успешно:

rm 'SLP_PROJECT.zip'
rm 'logs.txt'

Это значит, что оба файла уже убраны из отслеживания Git.

Вторая команда упала только потому, что ты запустил её повторно:

fatal: pathspec 'SLP_PROJECT.zip' did not match any files

Это ожидаемо — Git уже не отслеживает этот файл.

Продолжаем шаг 70.9.

Номер шага: 70.9 — контрольные тесты
Этап разработки: Проверка уведомлений и общей сборки
Ожидаемый результат: 75 тестов проходят и появляется STEP 70.9 TESTS OK.

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 70.9 TESTS OK"

Пришли результат этой команды. Если всё зелёное, сразу запускаем живой Telegram smoke-test.

Вставленный текст(20260829-022914).txt
Документ

Отлично. 70.9 по тестам закрыт: 75/75 прошли, компиляция тоже успешна, есть STEP 70.9 TESTS OK.

Номер шага: 70.9 — живой smoke-test
Этап разработки: Проверка реального Telegram-бота
Ожидаемый результат: бот запускается, создаёт baseline расписания и даёт включить уведомления.

В командной строке Codespaces введи:

python main.py

Ждём строки примерно такого вида:

SLP запущен. Ожидаю сообщения в Telegram...
SLP notifications: проверка каждые 180 сек.
SLP notifications: базовый снимок расписания сохранён

После этого открой Telegram → @Skoomaholic_Live_Parser_Bot → /start.

Проверь три вещи:

📅 Расписание
🔴 LIVE
🔔 Уведомления

И нажми 🔔 Уведомления один раз. Бот должен подтвердить включение.

Потом пришли мне:

текст из терминала после запуска;
что ответил бот после /start;
что ответил после нажатия 🔔 Уведомления.

Если это работает, 70.9 закрываем полностью и переходим к шагу 71 — финальный Parser v1.

По Telegram-части 70.9 работает правильно.

На скриншоте подтверждены две ключевые вещи:

/start открывает меню без ошибок;
🔔 Уведомления включаются и бот подтверждает подписку.

То есть UI и механизм подписки уже живые.

Номер шага: 70.9 — финальный smoke-test
Этап разработки: Проверка рабочих данных
Ожидаемый результат: бот действительно показывает объединённое расписание Qazsport + Sport+ и корректно определяет текущий LIVE.

Теперь прямо в Telegram нажми по очереди:

📅 Расписание
🔴 LIVE
📊 Статус

Пришли мне скриншоты или текст ответов этих трёх кнопок. Если там всё корректно, 70.9 окончательно закрываем и переходим к шагу 71 — SLP Parser v1.

По этим трём экранам 70.9 можно закрывать. Живой smoke-test прошёл.

Что подтвердилось на реальном боте:

📅 Расписание загружается сразу из 2 каналов.
События расположены по времени.
Статусы рассчитываются корректно: в 07:41 все события начинаются позже, поэтому 🔴 LIVE нет, что совпадает с кнопкой LIVE.
Переход через полночь отображается правильно: 29 августа, 23:25–30 августа, 01:30.
🔔 Уведомления включены и подписчик сохранён.
📊 Статус видит Qazsport, Sport+ Qazaqstan, merge, UTC+5, OpenSERP и мониторинг.
Текущий этап отображается как Step 70.9.

Но перед финальным релизом вижу один слой, который надо обязательно дочистить: качество названий событий. Например:

Казахстан чемпионаты. Іріктеу Дзюдо
1/2 финал Волейбол
Grand slam

Это технически работает, но структура местами неправильная. В идеале должно быть ближе к:

Дзюдо. Чемпионат Казахстана. Отборочный этап
Волейбол. 1/2 финала
Дзюдо. Grand Slam

И второе: у всех событий сейчас:

Нет данных о времени

Это не ошибка статуса и не поломка OpenSERP. Это означает, что независимое подтверждение конкретного времени не найдено. Само эфирное расписание при этом используется нормально.

Следующий шаг

Номер шага: 71
Этап разработки: Финальная сборка SLP Parser v1
Ожидаемый результат: готовая первая версия парсера для реального использования.

В 71 я предлагаю сделать один финальный пакет без новых больших функций:

дочистить нормализацию спорт / турнир / событие;
добавить канал в подробную карточку там, где это нужно;
проверить Qazsport + Sport+ на реальных сегодняшних событиях;
проверить OpenSERP на нескольких событиях, где время точно есть в интернете;
финальный regression-тест всего проекта;
обновить README;
убрать остаточный мусор;
создать Git tag/release v1.0.0.

После этого Parser v1 замораживаем и уже начинаем подключать следующие каналы, не ломая ядро.

ok го
Так, а тут почему так?


Тут вижу две вещи, и обе объяснимы.

Главное: в меню пропала кнопка 🔔 Уведомления. При этом тест test_notification_button_and_callback_exist у тебя прошёл, значит в текущем main.py кнопка точно есть. Следовательно, в Telegram сейчас отвечает старый запущенный процесс бота, который был стартован до замены main.py. Python сам код на лету не перечитывает.

Номер шага: 70.9 — перезапуск актуальной версии
Этап разработки: Запуск нового main.py вместо старого процесса
Ожидаемый результат: после /start снова появится 🔔 Уведомления.

В терминале, где работает бот, нажми Ctrl+C. Дождись $, затем в командной строке Codespaces введи:

cd /workspaces/skoomaholic-live-parser && python main.py

После запуска снова отправь /start. Меню должно быть уже таким:

📅 Расписание      🔴 LIVE
🔔 Уведомления     📊 Статус
📥 Выгрузить

А строка сверху:

⚪ Время подтвердили: 0 источников

означает другое: OpenSERP не нашёл независимый источник, из которого смог уверенно извлечь время именно этого события. Это не означает, что эфирное время отсутствует — расписание канала всё равно остаётся основным временем для LIVE/SOON/OVER.

Сначала перезапусти бот. Если после нового /start кнопки 🔔 Уведомления всё равно нет, тогда уже проверим, какой именно main.py реально запускается.

почему на казахском а не на русском? и ты уверен что это лайв а не кечап (запись), уровень доверия 40 расхождение по времени сильное. То что свыше 30 мин. вообще не должно быть в списке, разве нет?

6-е событие нет уровня доверия, почему?

Номер шага: 71
Этап разработки: SLP Parser v1 — финальный Release Candidate
Ожидаемый результат: исправляем ровно те проблемы, которые ты сейчас нашёл, после чего проводим последний живой тест и фиксируем v1.0.0.

По твоим замечаниям я внёс четыре принципиальных изменения.

Во-первых, Sport+ теперь приводит служебные казахские формулировки к русскому интерфейсу. Например:

АТ ЖАРЫСЫ МАУСЫМЫ 2026. АЛМАТЫДАН

станет:

Сезон конных скачек 2026. Алматы

При этом названия команд вроде Жас Қыран не переводим самовольно.

Во-вторых, насчёт события №5 ты правильно обратил внимание на LIVE. Я проверил официальный Sport+ на сегодня: в 18:00 у них действительно стоит АТ СПОРТЫ. АТ ЖАРЫСЫ МАУСЫМЫ 2026. АЛМАТЫДАН ТІКЕЛЕЙ ЭФИР, то есть это прямой эфир, не запись.

Но теперь парсер дополнительно требует официальную LIVE-пометку и исключает событие, если одновременно встречаются признаки ПОВТОР / ЗАПИСЬ / REPLAY / АРХИВ.

В-третьих, правило интернет-проверки теперь такое:

0–10 мин   → Время совпадает
11–20 мин  → Требуется проверить
21–30 мин  → Есть расхождение
>30 мин    → внешнее время ОТБРАСЫВАЕТСЯ

То есть твои 249 минут больше не будут показываться как подтверждение с доверием 40.

Важно: из самого эфирного расписания событие не удаляем, если официальный канал пометил его LIVE. Удаляем именно ложное интернет-подтверждение. Иначе интернет-поиск получил бы право отменять официальный эфир канала, чего мы как раз не хотим.

В-четвёртых, ситуация как у Grand Slam, где время не найдено, теперь всегда будет показываться единообразно:

⚖️ Вес доверия: 0
📊 Уровень доверия: Не рассчитывается
📚 Время подтвердили: 0 независимых источников

То есть уровень доверия больше не будет просто исчезать.

Также поправил такие случаи Qazsport:

Казахстан чемпионаты. Іріктеу Дзюдо

→

Вид спорта: Дзюдо
Турнир: Чемпионат Казахстана
Событие: Отборочный этап

и:

1\2 финал Волейбол

→

Вид спорта: Волейбол
Событие: 1/2 финала

На обновлённой копии проекта я прогнал 80 тестов из 80, плюс compileall — всё прошло.

Что заменить

Открой каждый файл → Ctrl+A → вставь ВЕСЬ новый файл → Ctrl+S:

main.py

parsers/sportplus.py

parsers/qazsport.py

verifiers/web_search.py

Затем тесты:

tests/test_sportplus.py

tests/test_qazsport_normalization.py

tests/test_openserp_speed.py

tests/test_main_notifications_integration.py

И полностью заменяем:

README.md

Весь комплект одним архивом тоже есть:

Скачать SLP_STEP_71_PARSER_V1_RC.zip

После замены

У тебя проект должен быть открыт здесь:

/workspaces/skoomaholic-live-parser

В командной строке Codespaces введи:

python -m unittest discover -s tests -v && python -m compileall -q main.py models.py parsers services verifiers tests && echo "STEP 71 RC OK"

Ожидаю:

Ran 80 tests
OK
STEP 71 RC OK

После этого перезапустим python main.py и специально ещё раз посмотрим события №5 и №6 в Telegram. Если там всё выглядит правильно, только тогда ставим v1.0.0.

main.py
Код
sportplus.py
Код
qazsport.py
Код
Еще 6

Библиотека
/
README.md
SLP — Skoomaholic Live Parser

Telegram-бот для сбора и проверки спортивных LIVE-событий из телепрограмм.

Parser v1

Подключённые источники:

Qazsport
Sport+ Qazaqstan

Основные правила:

часовой пояс: Asia/Almaty (UTC+5);
статус LIVE / SOON / OVER определяется только эфирным расписанием канала;
интернет используется только для независимой проверки времени;
0–10 минут: Время совпадает;
11–20 минут: Событие требуется проверить;
21–30 минут: Есть расхождение по времени;
более 30 минут: внешнее время отбрасывается как нерелевантное и не участвует в доверии;
Sport+ считается LIVE только при официальной пометке прямого эфира; записи/повторы исключаются;
переход через полночь нормализуется автоматически;
одинаковое событие на разных каналах группируется как simulcast;
изменения расписания могут отправляться подписчикам Telegram.
Установка
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

Нужна переменная окружения:

export BOT_TOKEN="..."
Запуск
python main.py
Тесты
python -m unittest discover -s tests -v
python -m compileall -q main.py models.py parsers services verifiers tests
OpenSERP

SLP использует OpenSERP для независимой интернет-проверки времени. Основные поисковые движки: Bing и DuckDuckGo. Baidu используется только как fallback.

Поисковая выдача не считается независимым источником сама по себе: SLP ищет релевантные страницы события и извлекает время из содержимого страниц.

Runtime-файлы

slp_state.json, logs.txt, .env, ZIP-архивы, .venv и __pycache__ не должны попадать в Git.