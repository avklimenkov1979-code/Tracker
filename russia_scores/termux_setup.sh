#!/data/data/com.termux/files/usr/bin/bash
# Уведомления о счёте сборной России на Android (Termux + Termux:API).
# Запуск в Termux одной командой:
#   curl -fsSL https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/russia_scores/termux_setup.sh | bash
# С Telegram: TG_BOT_TOKEN=... TG_CHAT_ID=... перед bash.
set -e

RAW="https://raw.githubusercontent.com/avklimenkov1979-code/Tracker/main/russia_scores"
APP="$HOME/russia_scores"

echo "==> Устанавливаю Python, cron и Termux:API"
pkg update -y
pkg install -y python cronie curl termux-api
pip install --upgrade requests
mkdir -p "$APP"
curl -fsSL -o "$APP/russia_scores.py" "$RAW/russia_scores.py"

echo "==> Создаю $APP/run.sh"
cat > "$APP/run.sh" <<RUN
#!/data/data/com.termux/files/usr/bin/bash
export PATH="$PREFIX/bin:\$PATH"
export TG_BOT_TOKEN="${TG_BOT_TOKEN:-}" TG_CHAT_ID="${TG_CHAT_ID:-}"
cd "$APP"
# лог не даём разрастаться
[ -f log.txt ] && [ "\$(wc -c < log.txt)" -gt 1000000 ] && tail -n 2000 log.txt > log.tmp && mv log.tmp log.txt
python russia_scores.py --state "$APP/state.json" "\$@" >> "$APP/log.txt" 2>&1
RUN
chmod +x "$APP/run.sh"

echo "==> Расписание: проверка каждую минуту"
( crontab -l 2>/dev/null | grep -v "russia_scores/run.sh"; echo "* * * * * $APP/run.sh" ) | crontab -
grep -q "pgrep crond" "$HOME/.bashrc" 2>/dev/null || \
  echo 'pgrep crond >/dev/null || crond   # фоновые задачи' >> "$HOME/.bashrc"
pgrep crond >/dev/null || crond
termux-wake-lock 2>/dev/null || true

echo "==> Пробное уведомление"
"$APP/run.sh" --test-notify || true
tail -n 5 "$APP/log.txt"
echo
echo "Готово. Если уведомление не пришло — поставьте приложение Termux:API из F-Droid"
echo "и разрешите ему показывать уведомления."
echo "Проверка вручную: ~/russia_scores/run.sh    Лог: ~/russia_scores/log.txt"
