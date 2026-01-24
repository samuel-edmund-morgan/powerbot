#!/bin/bash
# =============================================================================
# deploy_code.sh - Скрипт деплою коду з test/ в prod/
# =============================================================================
# 
# Використання: ./deploy_code.sh [--dry-run]
#   --dry-run  - показує що буде скопійовано, без реального виконання
#
# Цей скрипт копіює лише КОД з test/ в prod/
# НЕ копіює: .env, state.db, __pycache__
# =============================================================================

set -e

BASE_DIR="/home/powerbot/powerbot"
TEST_DIR="$BASE_DIR/test"
PROD_DIR="$BASE_DIR/prod"
BACKUP_DIR="$BASE_DIR/backups/code"

# Кольори для виводу
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

DRY_RUN=false

if [[ "$1" == "--dry-run" ]]; then
    DRY_RUN=true
    echo -e "${YELLOW}=== DRY RUN MODE ===${NC}"
fi

echo -e "${GREEN}=== Деплой коду з test/ в prod/ ===${NC}"
echo ""

# Перевірка директорій
if [[ ! -d "$TEST_DIR" ]]; then
    echo -e "${RED}ПОМИЛКА: Директорія test/ не існує${NC}"
    exit 1
fi

if [[ ! -d "$PROD_DIR" ]]; then
    echo -e "${RED}ПОМИЛКА: Директорія prod/ не існує${NC}"
    exit 1
fi

# Файли для копіювання (код)
CODE_FILES=(
    "main.py"
    "config.py"
    "database.py"
    "handlers.py"
    "services.py"
    "weather.py"
    "alerts.py"
    "api_server.py"
)

# Директорії для копіювання
CODE_DIRS=(
    "maps"
)

# Файли які НЕ копіюємо
EXCLUDE_PATTERNS=(
    ".env"
    "state.db"
    "__pycache__"
    "*.pyc"
    ".git"
)

# Створюємо backup директорію
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"

if [[ "$DRY_RUN" == false ]]; then
    mkdir -p "$BACKUP_PATH"
    echo -e "Бекап буде збережено в: ${YELLOW}$BACKUP_PATH${NC}"
fi

echo ""
echo "Файли для деплою:"
echo "=================="

# Копіювання файлів
for file in "${CODE_FILES[@]}"; do
    if [[ -f "$TEST_DIR/$file" ]]; then
        echo -e "  ${GREEN}✓${NC} $file"
        
        if [[ "$DRY_RUN" == false ]]; then
            # Бекап існуючого файлу
            if [[ -f "$PROD_DIR/$file" ]]; then
                cp "$PROD_DIR/$file" "$BACKUP_PATH/$file"
            fi
            # Копіювання нового файлу
            cp "$TEST_DIR/$file" "$PROD_DIR/$file"
        fi
    else
        echo -e "  ${YELLOW}⚠${NC} $file (не існує в test/)"
    fi
done

echo ""
echo "Директорії для деплою:"
echo "======================"

# Копіювання директорій
for dir in "${CODE_DIRS[@]}"; do
    if [[ -d "$TEST_DIR/$dir" ]]; then
        echo -e "  ${GREEN}✓${NC} $dir/"
        
        if [[ "$DRY_RUN" == false ]]; then
            # Бекап існуючої директорії
            if [[ -d "$PROD_DIR/$dir" ]]; then
                cp -r "$PROD_DIR/$dir" "$BACKUP_PATH/$dir"
            fi
            # Копіювання нової директорії
            rm -rf "$PROD_DIR/$dir"
            cp -r "$TEST_DIR/$dir" "$PROD_DIR/$dir"
        fi
    else
        echo -e "  ${YELLOW}⚠${NC} $dir/ (не існує в test/)"
    fi
done

echo ""
echo -e "${YELLOW}НЕ копіюються:${NC}"
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    echo "  - $pattern"
done

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}=== DRY RUN завершено. Зміни не внесені. ===${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}=== Код успішно задеплоєно! ===${NC}"
echo ""
echo "Наступні кроки:"
echo "  1. Перезапустіть prod бота:"
echo "     sudo systemctl restart bot-prod.service"
echo ""
echo "  2. Перевірте статус:"
echo "     sudo systemctl status bot-prod.service"
echo ""
echo "  3. При проблемах відкатіть з бекапу:"
echo "     cp -r $BACKUP_PATH/* $PROD_DIR/"
