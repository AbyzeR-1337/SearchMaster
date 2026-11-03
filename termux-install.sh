#!/data/data/com.termux/files/usr/bin/bash
# Установка SearchMaster на Android через Termux.
#
# Репозиторий приватный, поэтому обычный путь такой:
#   pkg install -y git gh python
#   gh auth login
#   gh repo clone AbyzeR-1337/SearchMaster ~/SearchMaster
#   bash ~/SearchMaster/termux-install.sh
#
# Скрипт можно запускать и отдельно: он сам скачает репозиторий
# (через gh, если вы вошли в GitHub, иначе через git).
# Другая ветка:  SEARCHMASTER_BRANCH=имя-ветки bash termux-install.sh
set -e

REPO="AbyzeR-1337/SearchMaster"
BRANCH="${SEARCHMASTER_BRANCH:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"

echo "==> Обновляю пакеты Termux и ставлю Python и Git"
pkg update -y
pkg install -y python git

echo "==> Ставлю зависимости Python (без lxml — на телефоне он не нужен)"
pip install --upgrade requests beautifulsoup4

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/searchmaster/__init__.py" ]; then
    REPO_DIR="$SCRIPT_DIR"            # скрипт запущен из уже скачанного репозитория
    echo "==> Использую репозиторий в $REPO_DIR"
else
    REPO_DIR="$HOME/SearchMaster"
    echo "==> Загружаю SearchMaster в $REPO_DIR"
    if [ -d "$REPO_DIR/.git" ]; then
        git -C "$REPO_DIR" pull --ff-only
    elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        gh repo clone "$REPO" "$REPO_DIR" -- --depth 1 ${BRANCH:+-b "$BRANCH"}
    elif ! git clone --depth 1 ${BRANCH:+-b "$BRANCH"} "https://github.com/$REPO.git" "$REPO_DIR"; then
        cat <<'EOF'

Не получилось скачать репозиторий: он приватный и нужен вход в GitHub.
Выполните и запустите установщик снова:
  pkg install -y gh
  gh auth login
  gh repo clone AbyzeR-1337/SearchMaster ~/SearchMaster
  bash ~/SearchMaster/termux-install.sh
EOF
        exit 1
    fi
fi

echo "==> Создаю команду searchmaster"
mkdir -p "$PREFIX/bin"
cat > "$PREFIX/bin/searchmaster" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
cd "$REPO_DIR" && exec python -m searchmaster "\$@"
EOF
chmod +x "$PREFIX/bin/searchmaster"

# Ярлык для виджета Termux:Widget (если приложение-виджет установлено)
mkdir -p "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/SearchMaster" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock 2>/dev/null || true
searchmaster --web
EOF
chmod +x "$HOME/.shortcuts/SearchMaster"

cat <<'EOF'

Готово!
  searchmaster --web           — открыть мобильный интерфейс в браузере телефона
  searchmaster "Hollow Knight" — поиск прямо в консоли
  cd ~/SearchMaster && git pull — обновить программу

Совет: в браузере нажмите «Добавить на главный экран» — появится иконка приложения.
Чтобы отчёты и скачанные файлы были видны в «Файлах» телефона:
  termux-setup-storage
  searchmaster --web --out ~/storage/downloads/SearchMaster
EOF
