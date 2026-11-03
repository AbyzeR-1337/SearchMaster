#!/data/data/com.termux/files/usr/bin/bash
# Установка SearchMaster на Android через Termux.
#
#   curl -fsSL https://raw.githubusercontent.com/AbyzeR-1337/SearchMaster/main/termux-install.sh | bash
#
# Другая ветка:  SEARCHMASTER_BRANCH=имя-ветки bash termux-install.sh
set -e

REPO_URL="https://github.com/AbyzeR-1337/SearchMaster.git"
REPO_DIR="$HOME/SearchMaster"
BRANCH="${SEARCHMASTER_BRANCH:-}"

echo "==> Обновляю пакеты Termux и ставлю Python и Git"
pkg update -y
pkg install -y python git

echo "==> Ставлю зависимости Python (без lxml — на телефоне он не нужен)"
pip install --upgrade requests beautifulsoup4

echo "==> Загружаю SearchMaster в $REPO_DIR"
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
elif [ -n "$BRANCH" ]; then
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
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
  searchmaster --web          — открыть мобильный интерфейс в браузере телефона
  searchmaster "Hollow Knight" — поиск прямо в консоли

Совет: в браузере нажмите «Добавить на главный экран» — появится иконка приложения.
Чтобы отчёты и скачанные файлы были видны в «Файлах» телефона:
  termux-setup-storage
  searchmaster --web --out ~/storage/downloads/SearchMaster
EOF
