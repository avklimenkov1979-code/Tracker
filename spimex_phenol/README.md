# Цены на фенол с SPIMEX

С 28.09.2026 дневные итоги торгов на spimex.com закрыты (Указ Президента №686),
поэтому основной источник теперь — **национальный индикатор оптовых цен
нефтегазохимии по фенолу (код FEN)**: <https://spimex.com/indexes/petrochem/national/>.
Это среднемесячная цена в рублях за тонну; биржа обновляет её в течение месяца.

`phenol_index.py` открывает страницу индикаторов, берёт строки FEN и дописывает
в таблицу одну запись на каждую таблицу индикаторов в день:

- `phenol_index.csv` — UTF-8, разделитель `;`, открывается в Excel;
- `phenol_index.xlsx` — та же таблица в формате Excel.

Колонки: дата сбора, код, наименование, значение (руб./т), изменение (%),
название индикатора (с периодом и НДС), номер таблицы, страница.

```bash
pip install -r spimex_phenol/requirements.txt
python spimex_phenol/phenol_index.py --csv data/phenol_index.csv
python spimex_phenol/phenol_index.py --url https://spimex.com/indexes/petrochem/territorial/  # другие индикаторы
```

`spimex_phenol.py` — прежний сборщик из дневных бюллетеней; пока бюллетени
закрыты, он полезен только режимом диагностики `--inspect [--find ТЕКСТ]`.

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
   настроит запуск каждый день в 07:47 и сразу сделает первый сбор.
3. Таблица: **Загрузки → SPIMEX → `phenol_index.xlsx`** — открывается в Excel,
   Google Таблицах или WPS.
4. Чтобы Android не убивал Termux: в настройках телефона отключите для Termux
   оптимизацию батареи. Если Termux закрыт смахиванием, расписание
   включится при следующем открытии Termux.

Вручную: `~/spimex/run.sh`, лог: `~/spimex/log.txt`.
Другое время: `RUN_AT_HOUR=9 RUN_AT_MIN=5` перед `bash` в команде установки.

### iPhone (a-Shell + Команды)

1. Установите **a-Shell** из App Store и выполните в нём:

   ```bash
   pip install requests openpyxl
   curl -L -o ~/Documents/phenol_index.py https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/spimex_phenol/phenol_index.py
   python3 ~/Documents/phenol_index.py --csv ~/Documents/SPIMEX/phenol_index.csv
   ```

2. Приложение **Команды → Автоматизация → +  → Время суток** → 07:47, ежедневно,
   «Запускать сразу». Действие — **a-Shell → Execute Command**:

   ```bash
   python3 ~/Documents/phenol_index.py --csv ~/Documents/SPIMEX/phenol_index.csv
   ```

3. Таблица: **Файлы → На iPhone → a-Shell → SPIMEX → `phenol_index.xlsx`**.

### Свой компьютер или сервер в России

```cron
47 7 * * * cd /path/to/Tracker && python3 spimex_phenol/phenol_index.py >> phenol_index.log 2>&1
```



## Тесты

```bash
pip install pytest xlwt
python -m pytest spimex_phenol/tests
```
