# Счёт сборной России — уведомления

`russia_scores.py` раз в минуту смотрит live-матчи на Sofascore и сообщает,
когда у сборной России (мужской, женской, молодёжных) меняется счёт:

- **⚽ футбол, футзал, 🏒 хоккей, хоккей с мячом, 🤾 гандбол, 🤽 водное поло,
  флорбол, хоккей на траве** — каждый гол/шайба: «🇷🇺 Россия забивает!» или
  «Соперник забивает»;
- **🏐 волейбол, пляжный волейбол** — каждая выигранная партия;
- **🏀 баскетбол, 🏉 регби** — смена четверти/тайма со счётом (каждое очко —
  слишком часто; включается ключом `--every-point`);
- во всех видах — начало матча и итоговый счёт.

Пример уведомления:

```
🇷🇺 Россия забивает!
⚽ Россия 1:0 Сербия
1-й тайм · Товарищеские матчи
```

Куда приходят уведомления:

- **Android** — системное уведомление со звуком (Termux + Termux:API);
- **Telegram** — если заданы переменные `TG_BOT_TOKEN` и `TG_CHAT_ID`
  (подходит и для iPhone);
- в консоль / лог — всегда.

## Android (Termux)

1. Установите из F-Droid **Termux** и **Termux:API**, разрешите Termux:API
   показывать уведомления.
2. В Termux:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/russia_scores/termux_setup.sh | bash
   ```

   Скрипт поставит Python, настроит проверку каждую минуту и пришлёт
   пробное уведомление.
3. Отключите для Termux оптимизацию батареи, иначе Android усыпит cron.

Вручную: `~/russia_scores/run.sh`, лог: `~/russia_scores/log.txt`.
Отключить: `crontab -e` и удалить строку с `russia_scores`.

## Telegram (Android, iPhone, компьютер)

1. Создайте бота у [@BotFather](https://t.me/BotFather) — он выдаст токен.
2. Напишите своему боту любое сообщение и откройте
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates` — в ответе `"chat":{"id":…}`
   это ваш `TG_CHAT_ID`.
3. Запускайте скрипт на любом устройстве, которое всегда включено:

   ```bash
   # Termux: токен запомнится в run.sh
   TG_BOT_TOKEN=123:abc TG_CHAT_ID=456 bash -c "$(curl -fsSL https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/russia_scores/termux_setup.sh)"
   ```

   ```cron
   # компьютер/сервер: crontab -e
   * * * * * TG_BOT_TOKEN=123:abc TG_CHAT_ID=456 python3 /path/to/Tracker/russia_scores/russia_scores.py --no-termux >> ~/russia_scores.log 2>&1
   ```

   Или без cron: `python3 russia_scores.py --watch 60`.

iPhone сам по себе не умеет запускать скрипт каждую минуту в фоне, поэтому
для него — Telegram-бот, а скрипт работает на Android-телефоне, компьютере
или сервере.

## Настройка

```bash
python russia_scores.py --test-notify                  # проверить уведомления
python russia_scores.py --sports football,ice-hockey   # только эти виды спорта
python russia_scores.py --every-point                  # каждое очко и в баскетболе
python russia_scores.py --team "Belarus\b" -v          # другая сборная (например, для проверки)
```

Состояние (счёт на прошлом запуске) хранится в `~/.russia_scores.json`
(`--state` — другой путь). Если Sofascore недоступен, матч не считается
завершённым — уведомление придёт при следующей удачной проверке.

Sofascore может не пускать запросы с зарубежных серверов (как и SPIMEX),
поэтому GitHub Actions для этого не годится — запускайте с телефона или
домашнего компьютера.

## Тесты

```bash
pip install pytest requests
python -m pytest russia_scores/tests
```
