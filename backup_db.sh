#!/bin/bash
# =============================================================================
# backup_db.sh - Ручний бекап бази даних
# =============================================================================
# 
# Використання: ./backup_db.sh [prod|test]
#   prod  - бекап production бази (за замовчуванням)
#   test  - бекап test бази
#
# Бекапи зберігаються в /home/powerbot/powerbot/backups/db/
# =============================================================================

set -e

BASE_DIR="/home/powerbot/powerbot"
BACKUP_DIR="$BASE_DIR/backups/db"

# Кольори
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Визначаємо середовище
ENV="${1:-prod}"

if [[ "$ENV" != "prod" && "$ENV" != "test" ]]; then
    echo -e "${RED}Помилка: Невідоме середовище '$ENV'${NC}"
    echo "Використання: ./backup_db.sh [prod|test]"
    exit 1
fi

SOURCE_DB="$BASE_DIR/$ENV/state.db"

if [[ ! -f "$SOURCE_DB" ]]; then
    echo -e "${RED}Помилка: База даних не знайдена: $SOURCE_DB${NC}"
    exit 1
fi

# Створюємо директорію для бекапів
mkdir -p "$BACKUP_DIR"

# Генеруємо ім'я файлу
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/state_${ENV}_${TIMESTAMP}.db"

# Копіюємо базу
cp "$SOURCE_DB" "$BACKUP_FILE"

# Показуємо результат
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo -e "${GREEN}✓ Бекап створено:${NC} $BACKUP_FILE ($SIZE)"

# Показуємо кількість бекапів
COUNT=$(ls -1 "$BACKUP_DIR"/*.db 2>/dev/null | wc -l)
echo -e "${YELLOW}Всього бекапів: $COUNT${NC}"

# Попередження якщо багато бекапів
if [[ $COUNT -gt 20 ]]; then
    echo -e "${YELLOW}⚠ Рекомендується видалити старі бекапи${NC}"
    echo "  Найстаріші файли:"
    ls -1t "$BACKUP_DIR"/*.db | tail -5
fi
