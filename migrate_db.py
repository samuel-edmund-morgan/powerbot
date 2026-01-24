#!/usr/bin/env python3
"""
migrate_db.py - Скрипт міграції бази даних з test/ в prod/
=============================================================================

Цей скрипт виконує ОБЕРЕЖНУ міграцію змін схеми бази даних з test в prod,
зберігаючи всі дані користувачів у production базі.

Використання:
    python migrate_db.py [--dry-run] [--verbose]
    
Параметри:
    --dry-run   - показує що буде змінено, без реального виконання
    --verbose   - детальний вивід

Що робить скрипт:
1. Порівнює схеми таблиць test і prod
2. Додає нові таблиці якщо вони є в test
3. Додає нові колонки до існуючих таблиць
4. НЕ видаляє існуючі колонки/таблиці (безпечний режим)
5. Заповнює нові колонки дефолтними значеннями
6. Додає статичні дані без видалення існуючих записів (INSERT OR IGNORE)
   Пропускає kv/sensors/building_power_state, щоб не затирати прод-дані

ВАЖЛИВО:
- Перед запуском зупиніть prod бота: sudo systemctl stop bot-prod.service
- Створюється автоматичний бекап перед міграцією
=============================================================================
"""

import sqlite3
import sys
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

# Шляхи
BASE_DIR = Path("/home/powerbot/powerbot")
TEST_DB = BASE_DIR / "test" / "state.db"
PROD_DB = BASE_DIR / "prod" / "state.db"
BACKUP_DIR = BASE_DIR / "backups" / "db"

# Таблиці, які НЕ можна перезаписувати (користувацькі/динамічні дані)
USER_DATA_TABLES = {
    "subscribers",
    "water_votes",
    "heating_votes",
    "place_likes",
    "events",
    "active_notifications",
    "last_bot_message",
    "kv",
    "sensors",
    "building_power_state",
}

# Дефолтні значення для нових колонок за типом
DEFAULT_VALUES = {
    "INTEGER": 0,
    "TEXT": None,
    "REAL": 0.0,
    "BLOB": None,
}


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def log_info(msg: str):
    print(f"{Colors.GREEN}✓{Colors.NC} {msg}")


def log_warning(msg: str):
    print(f"{Colors.YELLOW}⚠{Colors.NC} {msg}")


def log_error(msg: str):
    print(f"{Colors.RED}✗{Colors.NC} {msg}")


def log_action(msg: str):
    print(f"{Colors.BLUE}→{Colors.NC} {msg}")


class DatabaseMigrator:
    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self.dry_run = dry_run
        self.verbose = verbose
        self.changes: List[str] = []
    
    def get_table_info(self, db_path: Path) -> Dict[str, List[Dict]]:
        """Отримує інформацію про всі таблиці в БД."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Отримуємо список таблиць
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        
        table_info = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            table_info[table] = [
                {
                    "cid": col[0],
                    "name": col[1],
                    "type": col[2],
                    "notnull": col[3],
                    "default": col[4],
                    "pk": col[5]
                }
                for col in columns
            ]
        
        conn.close()
        return table_info
    
    def get_create_statement(self, db_path: Path, table_name: str) -> str:
        """Отримує CREATE statement для таблиці."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else ""
    
    def compare_schemas(self) -> Dict[str, Any]:
        """Порівнює схеми test і prod баз даних."""
        test_info = self.get_table_info(TEST_DB)
        prod_info = self.get_table_info(PROD_DB)
        
        comparison = {
            "new_tables": [],
            "new_columns": {},
            "modified_columns": {},
        }
        
        # Знаходимо нові таблиці
        for table in test_info:
            if table not in prod_info:
                comparison["new_tables"].append(table)
        
        # Знаходимо нові/змінені колонки
        for table in test_info:
            if table in prod_info:
                test_columns = {col["name"]: col for col in test_info[table]}
                prod_columns = {col["name"]: col for col in prod_info[table]}
                
                new_cols = []
                for col_name, col_info in test_columns.items():
                    if col_name not in prod_columns:
                        new_cols.append(col_info)
                
                if new_cols:
                    comparison["new_columns"][table] = new_cols
        
        return comparison
    
    def create_backup(self) -> Path:
        """Створює бекап prod бази даних."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"state_{timestamp}.db"
        
        if not self.dry_run:
            shutil.copy2(PROD_DB, backup_path)
        
        return backup_path
    
    def add_table(self, table_name: str):
        """Додає нову таблицю з test в prod."""
        create_sql = self.get_create_statement(TEST_DB, table_name)
        
        if self.verbose:
            print(f"    SQL: {create_sql[:100]}...")
        
        self.changes.append(f"Додано таблицю: {table_name}")
        
        if not self.dry_run:
            conn = sqlite3.connect(PROD_DB)
            cursor = conn.cursor()
            cursor.execute(create_sql)
            conn.commit()
            conn.close()
    
    def add_column(self, table_name: str, column_info: Dict):
        """Додає нову колонку до існуючої таблиці."""
        col_name = column_info["name"]
        col_type = column_info["type"] or "TEXT"
        
        # Визначаємо дефолтне значення
        if column_info["default"] is not None:
            default = column_info["default"]
        elif column_info["notnull"]:
            # Якщо NOT NULL, потрібен дефолт
            default = DEFAULT_VALUES.get(col_type.upper(), "''")
        else:
            default = "NULL"
        
        # Формуємо SQL
        alter_sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
        if default != "NULL":
            alter_sql += f" DEFAULT {default}"
        
        if self.verbose:
            print(f"    SQL: {alter_sql}")
        
        self.changes.append(f"Додано колонку {table_name}.{col_name} ({col_type})")
        
        if not self.dry_run:
            conn = sqlite3.connect(PROD_DB)
            cursor = conn.cursor()
            try:
                cursor.execute(alter_sql)
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    log_warning(f"Колонка {col_name} вже існує в {table_name}")
                else:
                    raise
            finally:
                conn.close()
    
    def migrate_static_data(self, table_name: str):
        """Мігрує статичні дані з test в prod без видалення існуючих записів."""
        if table_name in USER_DATA_TABLES:
            log_warning(f"Пропускаємо міграцію даних для {table_name} (захищена таблиця)")
            return
        
        conn_test = sqlite3.connect(TEST_DB)
        conn_prod = sqlite3.connect(PROD_DB)
        
        cursor_test = conn_test.cursor()
        cursor_prod = conn_prod.cursor()
        
        # Отримуємо дані з test
        cursor_test.execute(f"SELECT * FROM {table_name}")
        test_data = cursor_test.fetchall()
        
        # Отримуємо назви колонок
        cursor_test.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor_test.fetchall()]
        
        if test_data and not self.dry_run:
            # Додаємо нові записи з test, не перезаписуючи існуючі
            placeholders = ",".join(["?" for _ in columns])
            cols_str = ",".join(columns)
            cursor_prod.executemany(
                f"INSERT OR IGNORE INTO {table_name} ({cols_str}) VALUES ({placeholders})",
                test_data
            )
            conn_prod.commit()
        
        self.changes.append(f"Мігровано {len(test_data)} записів в {table_name}")
        
        conn_test.close()
        conn_prod.close()
    
    def run(self):
        """Виконує міграцію."""
        print("")
        print("=" * 60)
        print("  МІГРАЦІЯ БАЗИ ДАНИХ test/ → prod/")
        print("=" * 60)
        print("")
        
        if self.dry_run:
            print(f"{Colors.YELLOW}=== DRY RUN MODE ==={Colors.NC}")
            print("")
        
        # Перевірка файлів
        if not TEST_DB.exists():
            log_error(f"Файл {TEST_DB} не знайдено")
            return False
        
        if not PROD_DB.exists():
            log_error(f"Файл {PROD_DB} не знайдено")
            return False
        
        # Бекап
        print(f"{Colors.BLUE}1. Створення бекапу{Colors.NC}")
        backup_path = self.create_backup()
        if not self.dry_run:
            log_info(f"Бекап створено: {backup_path}")
        else:
            log_action(f"Бекап буде створено: {backup_path}")
        print("")
        
        # Порівняння схем
        print(f"{Colors.BLUE}2. Порівняння схем баз даних{Colors.NC}")
        comparison = self.compare_schemas()
        
        if not comparison["new_tables"] and not comparison["new_columns"]:
            log_info("Схеми ідентичні, міграція не потрібна")
            return True
        
        # Нові таблиці
        if comparison["new_tables"]:
            print("")
            print(f"{Colors.BLUE}3. Додавання нових таблиць{Colors.NC}")
            for table in comparison["new_tables"]:
                log_action(f"Додаю таблицю: {table}")
                self.add_table(table)
        
        # Нові колонки
        if comparison["new_columns"]:
            print("")
            print(f"{Colors.BLUE}4. Додавання нових колонок{Colors.NC}")
            for table, columns in comparison["new_columns"].items():
                for col in columns:
                    log_action(f"Додаю колонку: {table}.{col['name']}")
                    self.add_column(table, col)
        
        # Міграція статичних даних (places, general_services, buildings)
        print("")
        print(f"{Colors.BLUE}5. Міграція статичних даних{Colors.NC}")
        static_tables = ["general_services", "places", "buildings"]
        for table in static_tables:
            test_info = self.get_table_info(TEST_DB)
            if table in test_info:
                log_action(f"Мігрую дані: {table}")
                self.migrate_static_data(table)
        
        # Підсумок
        print("")
        print("=" * 60)
        if self.dry_run:
            print(f"{Colors.YELLOW}DRY RUN завершено. Зміни НЕ внесені.{Colors.NC}")
        else:
            print(f"{Colors.GREEN}Міграція завершена успішно!{Colors.NC}")
        print("=" * 60)
        
        if self.changes:
            print("")
            print("Виконані зміни:")
            for change in self.changes:
                print(f"  • {change}")
        
        print("")
        print("Наступні кроки:")
        print("  1. Запустіть prod бота:")
        print("     sudo systemctl start bot-prod.service")
        print("")
        print("  2. При проблемах відкатіть з бекапу:")
        print(f"     cp {backup_path} {PROD_DB}")
        
        return True


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    migrator = DatabaseMigrator(dry_run=dry_run, verbose=verbose)
    success = migrator.run()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
