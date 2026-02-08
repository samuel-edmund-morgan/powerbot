#!/usr/bin/env python3
"""
migrate_db.py - Безпечна міграція схеми SQLite для PowerBot
=============================================================================

Скрипт порівнює цільову БД із еталонною схемою (schema.sql або SOURCE_DB)
та додає НОВІ таблиці/колонки, не видаляючи дані користувачів.

Використання:
    python migrate_db.py [--dry-run] [--verbose]

Параметри:
    --dry-run   - показує що буде змінено, без реального виконання
    --verbose   - детальний вивід

Що робить скрипт:
1. Порівнює схеми еталонної та цільової БД
2. Додає нові таблиці
3. Додає нові колонки до існуючих таблиць
4. Додає нові індекси
5. НЕ видаляє існуючі колонки/таблиці/індекси (безпечний режим)
6. Заповнює нові колонки дефолтними значеннями
7. Додає статичні дані без видалення існуючих записів (INSERT OR IGNORE)
   Пропускає kv/sensors/building_power_state, щоб не затирати прод-дані

ВАЖЛИВО:
- Перед запуском зупиніть контейнер бота
- Створюється автоматичний бекап перед міграцією
=============================================================================
"""

import sqlite3
import sys
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

# Шляхи (можна перевизначити через env)
BASE_DIR = Path(os.getenv("POWERBOT_BASE_DIR", "/home/powerbot/powerbot"))
TARGET_DB = Path(os.getenv("TARGET_DB") or os.getenv("DB_PATH", str(BASE_DIR / "state.db")))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(TARGET_DB.parent)))
SCHEMA_PATH = Path(os.getenv("SCHEMA_PATH", str(Path(__file__).with_name("schema.sql"))))
SOURCE_DB = Path(os.getenv("SOURCE_DB")) if os.getenv("SOURCE_DB") else None

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
    "business_owners",
    "business_subscriptions",
    "business_audit_log",
    "business_payment_events",
    "business_claim_tokens",
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
    def __init__(
        self,
        dry_run: bool = False,
        verbose: bool = False,
        source_db: Path | None = None,
        target_db: Path | None = None,
    ):
        self.dry_run = dry_run
        self.verbose = verbose
        self.changes: List[str] = []
        self.source_db = source_db
        self.target_db = target_db or TARGET_DB
    
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

    def get_index_info(self, db_path: Path) -> Dict[str, str]:
        """Отримує інформацію про користувацькі індекси (без autoindex)."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type='index'
              AND sql IS NOT NULL
              AND name NOT LIKE 'sqlite_autoindex_%'
            ORDER BY name
            """
        )
        rows = cursor.fetchall()
        conn.close()
        return {name: sql for name, sql in rows if name and sql}

    def get_create_index_statement(self, db_path: Path, index_name: str) -> str:
        """Отримує CREATE statement для індексу."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,)
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else ""
    
    def compare_schemas(self) -> Dict[str, Any]:
        """Порівнює схеми еталонної та цільової баз даних."""
        source_info = self.get_table_info(self.source_db)
        target_info = self.get_table_info(self.target_db)
        
        comparison = {
            "new_tables": [],
            "new_columns": {},
            "new_indexes": [],
            "modified_columns": {},
        }
        
        # Знаходимо нові таблиці
        for table in source_info:
            if table not in target_info:
                comparison["new_tables"].append(table)
        
        # Знаходимо нові/змінені колонки
        for table in source_info:
            if table in target_info:
                source_columns = {col["name"]: col for col in source_info[table]}
                target_columns = {col["name"]: col for col in target_info[table]}
                
                new_cols = []
                for col_name, col_info in source_columns.items():
                    if col_name not in target_columns:
                        new_cols.append(col_info)
                
                if new_cols:
                    comparison["new_columns"][table] = new_cols

        # Знаходимо нові індекси
        source_indexes = self.get_index_info(self.source_db)
        target_indexes = self.get_index_info(self.target_db)
        for index_name in source_indexes:
            if index_name not in target_indexes:
                comparison["new_indexes"].append(index_name)
        
        return comparison
    
    def create_backup(self) -> Path:
        """Створює бекап цільової бази даних."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"state_{timestamp}.db"
        
        if not self.dry_run:
            shutil.copy2(self.target_db, backup_path)
        
        return backup_path
    
    def add_table(self, table_name: str):
        """Додає нову таблицю з еталонної БД у цільову."""
        create_sql = self.get_create_statement(self.source_db, table_name)
        
        if self.verbose:
            print(f"    SQL: {create_sql[:100]}...")
        
        self.changes.append(f"Додано таблицю: {table_name}")
        
        if not self.dry_run:
            conn = sqlite3.connect(self.target_db)
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
            conn = sqlite3.connect(self.target_db)
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

    def add_index(self, index_name: str):
        """Додає новий індекс з еталонної БД у цільову."""
        create_sql = self.get_create_index_statement(self.source_db, index_name)
        if not create_sql:
            log_warning(f"Не знайдено SQL для індексу {index_name}")
            return

        if self.verbose:
            print(f"    SQL: {create_sql}")

        self.changes.append(f"Додано індекс: {index_name}")

        if not self.dry_run:
            conn = sqlite3.connect(self.target_db)
            cursor = conn.cursor()
            try:
                cursor.execute(create_sql)
                conn.commit()
            except sqlite3.OperationalError as e:
                if "already exists" in str(e).lower():
                    log_warning(f"Індекс {index_name} вже існує")
                else:
                    raise
            finally:
                conn.close()
    
    def migrate_static_data(self, table_name: str):
        """Мігрує статичні дані з еталонної БД у цільову без видалення існуючих записів."""
        if table_name in USER_DATA_TABLES:
            log_warning(f"Пропускаємо міграцію даних для {table_name} (захищена таблиця)")
            return
        
        conn_source = sqlite3.connect(self.source_db)
        conn_target = sqlite3.connect(self.target_db)
        
        cursor_source = conn_source.cursor()
        cursor_target = conn_target.cursor()
        
        # Отримуємо дані з еталонної БД
        cursor_source.execute(f"SELECT * FROM {table_name}")
        source_data = cursor_source.fetchall()
        
        # Отримуємо назви колонок
        cursor_source.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor_source.fetchall()]
        
        if source_data and not self.dry_run:
            # Додаємо нові записи, не перезаписуючи існуючі
            placeholders = ",".join(["?" for _ in columns])
            cols_str = ",".join(columns)
            cursor_target.executemany(
                f"INSERT OR IGNORE INTO {table_name} ({cols_str}) VALUES ({placeholders})",
                source_data
            )
            conn_target.commit()
        
        self.changes.append(f"Мігровано {len(source_data)} записів в {table_name}")
        
        conn_source.close()
        conn_target.close()
    
    def run(self):
        """Виконує міграцію."""
        print("")
        print("=" * 60)
        print("  МІГРАЦІЯ БАЗИ ДАНИХ (schema → target)")
        print("=" * 60)
        print("")
        
        if self.dry_run:
            print(f"{Colors.YELLOW}=== DRY RUN MODE ==={Colors.NC}")
            print("")
        
        # Перевірка файлів
        if not self.source_db.exists():
            log_error(f"Файл {self.source_db} не знайдено")
            return False
        
        if not self.target_db.exists():
            log_error(f"Файл {self.target_db} не знайдено")
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
        
        if (
            not comparison["new_tables"]
            and not comparison["new_columns"]
            and not comparison["new_indexes"]
        ):
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
        
        # Нові індекси
        if comparison["new_indexes"]:
            print("")
            print(f"{Colors.BLUE}5. Додавання нових індексів{Colors.NC}")
            for index_name in comparison["new_indexes"]:
                log_action(f"Додаю індекс: {index_name}")
                self.add_index(index_name)

        # Міграція статичних даних (places, general_services, buildings)
        print("")
        print(f"{Colors.BLUE}6. Міграція статичних даних{Colors.NC}")
        static_tables = ["general_services", "places", "buildings", "shelter_places"]
        source_info = self.get_table_info(self.source_db)
        for table in static_tables:
            if table in source_info:
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
        print("  1. Запустіть контейнер бота:")
        print("     docker compose up -d")
        print("")
        print("  2. При проблемах відкатіть з бекапу:")
        print(f"     cp {backup_path} {self.target_db}")
        
        return True


def prepare_schema_db(schema_path: str) -> Path:
    """Створює тимчасову БД з schema.sql для порівняння."""
    tmp_dir = Path(tempfile.gettempdir())
    tmp_db = tmp_dir / "powerbot_schema.db"
    if tmp_db.exists():
        tmp_db.unlink()
    conn = sqlite3.connect(tmp_db)
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()
    return tmp_db


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    source_db = SOURCE_DB
    if source_db is None:
        if not SCHEMA_PATH.exists():
            log_error(f"schema.sql не знайдено: {SCHEMA_PATH}")
            sys.exit(1)
        source_db = prepare_schema_db(str(SCHEMA_PATH))
    elif not source_db.exists():
        log_error(f"Файл {source_db} не знайдено")
        sys.exit(1)

    migrator = DatabaseMigrator(
        dry_run=dry_run,
        verbose=verbose,
        source_db=source_db,
        target_db=TARGET_DB,
    )
    success = migrator.run()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
