# Цены на фенол с SPIMEX

Скрипт `spimex_phenol.py` каждое утро забирает свежий бюллетень итогов торгов
с [spimex.com](https://spimex.com), находит строки с фенолом и дописывает их в таблицу:

- `data/phenol_prices.csv` — UTF-8, разделитель `;`, открывается в Excel;
- `data/phenol_prices.xlsx` — та же таблица в формате Excel.

Колонки: дата торгов, код и наименование инструмента, базис поставки, объём (т и руб.),
изменение цены к предыдущему дню, минимальная, средняя, максимальная и рыночная цены,
лучшие цены покупки и продажи, количество договоров и имя файла бюллетеня.

## Запуск вручную

```bash
pip install -r spimex_phenol/requirements.txt
python spimex_phenol/spimex_phenol.py            # последний бюллетень
python spimex_phenol/spimex_phenol.py --days 60  # догрузить историю за 60 дней
python spimex_phenol/spimex_phenol.py --file bulletin.xls   # разобрать скачанный файл
```

Повторный запуск безопасен: уже обработанные бюллетени пропускаются, а строки
дедуплицируются по паре (дата, код инструмента).

Настройки:

- `--results-url URL` — страница «Итоги торгов» секции, где торгуется фенол
  (по умолчанию секция нефтепродуктов; флаг можно указать несколько раз);
- `--pattern REGEX` — какие инструменты брать (по умолчанию `фенол`, без «фенольной смолы»).

## Запуск на телефоне

SPIMEX не пускает зарубежные серверы (с GitHub Actions — `Connection refused`),
поэтому собирать данные нужно с российского IP — например, с телефона.

### Android (Termux)

1. Установите **Termux** из F-Droid (версия в Google Play устарела).
   По желанию — **Termux:API** оттуда же, чтобы получать уведомление после сбора.
2. Откройте Termux и выполните:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/spimex_phenol/termux_setup.sh | bash
   ```

   Разрешите доступ к файлам, когда появится запрос. Скрипт поставит Python,
   настроит запуск каждый день в 07:47 и сразу загрузит историю за 30 дней.
3. Таблица: **Загрузки → SPIMEX → `phenol_prices.xlsx`** — открывается в Excel,
   Google Таблицах или WPS.
4. Чтобы Android не убивал Termux: в настройках телефона отключите для Termux
   оптимизацию батареи. Если Termux закрыт смахиванием, расписание
   включится при следующем открытии Termux; пропущенные дни догрузятся сами.

Вручную: `~/spimex/run.sh`, лог: `~/spimex/log.txt`.
Другое время: `RUN_AT_HOUR=9 RUN_AT_MIN=5` перед `bash` в команде установки.

### iPhone (a-Shell + Команды)

1. Установите **a-Shell** из App Store и выполните в нём:

   ```bash
   pip install requests openpyxl xlrd
   curl -L -o spimex_phenol.py https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/spimex_phenol/spimex_phenol.py
   python3 spimex_phenol.py --days 30 --csv ~/Documents/SPIMEX/phenol_prices.csv
   ```

2. Приложение **Команды → Автоматизация → +  → Время суток** → 07:47, ежедневно,
   «Запускать сразу». Действие — **a-Shell → Execute Command**:

   ```bash
   python3 spimex_phenol.py --days 7 --csv ~/Documents/SPIMEX/phenol_prices.csv
   ```

3. Таблица: **Файлы → На iPhone → a-Shell → SPIMEX → `phenol_prices.xlsx`**.

### Свой компьютер или сервер в России

```cron
47 7 * * * cd /path/to/Tracker && python3 spimex_phenol/spimex_phenol.py --days 7 >> spimex_phenol.log 2>&1
```

Торги идут днём, поэтому утренний запуск забирает бюллетень за предыдущий торговый день.

## Тесты

```bash
pip install pytest xlwt
python -m pytest spimex_phenol/tests
```
