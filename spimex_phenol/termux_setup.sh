#!/data/data/com.termux/files/usr/bin/bash
# Установка ежедневного сбора цен на фенол на Android (Termux).
# Запуск в Termux одной командой:
#   curl -fsSL https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/spimex_phenol/termux_setup.sh | bash
set -e

RAW="https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/spimex_phenol"
APP="$HOME/spimex"
RUN_AT_MIN="${RUN_AT_MIN:-47}"
RUN_AT_HOUR="${RUN_AT_HOUR:-7}"

echo "==> Устанавливаю Python и cron"
pkg update -y
pkg install -y python cronie curl
pip install --upgrade requests openpyxl xlrd

echo "==> Доступ к памяти телефона (подтвердите запрос разрешения)"
[ -d "$HOME/storage/shared" ] || termux-setup-storage
for _ in $(seq 1 30); do [ -d "$HOME/storage/shared" ] && break; sleep 1; done
OUT="$HOME/storage/shared/Download/SPIMEX"
[ -d "$HOME/storage/shared" ] || OUT="$APP/data"  # если разрешение не дали
mkdir -p "$APP" "$OUT"

echo "==> Создаю $APP/run.sh"
cat > "$APP/run.sh" <<RUN
#!/data/data/com.termux/files/usr/bin/bash
export PATH="$PREFIX/bin:\$PATH"
cd "$APP"
# подтягиваем свежую версию скрипта (исправления приходят сами)
curl -fsSL -o spimex_phenol.py.new "$RAW/spimex_phenol.py" && mv spimex_phenol.py.new spimex_phenol.py
# --days 7: если телефон был выключен, пропущенные дни догрузятся
python spimex_phenol.py --days "\${1:-7}" --csv "$OUT/phenol_prices.csv" >> "$APP/log.txt" 2>&1
code=\$?
if command -v termux-notification >/dev/null; then
  termux-notification --title "Фенол SPIMEX" --content "\$(grep 'новых строк' "$APP/log.txt" | tail -n 1 | cut -c25-)"
fi
exit \$code
RUN
chmod +x "$APP/run.sh"

echo "==> Расписание: каждый день в $RUN_AT_HOUR:$RUN_AT_MIN"
( crontab -l 2>/dev/null | grep -v "spimex/run.sh"; echo "$RUN_AT_MIN $RUN_AT_HOUR * * * $APP/run.sh" ) | crontab -
grep -q "pgrep crond" "$HOME/.bashrc" 2>/dev/null || \
  echo 'pgrep crond >/dev/null || crond   # сбор цен на фенол' >> "$HOME/.bashrc"
pgrep crond >/dev/null || crond
termux-wake-lock 2>/dev/null || true

echo "==> Первый запуск: история за 30 дней"
"$APP/run.sh" 30 || true
tail -n 15 "$APP/log.txt"
echo
echo "Готово. Таблица: $OUT/phenol_prices.xlsx (и .csv)"
echo "Запустить вручную: ~/spimex/run.sh    Лог: ~/spimex/log.txt"
