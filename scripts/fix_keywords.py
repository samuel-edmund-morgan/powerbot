#!/usr/bin/env python3
"""
Скрипт для очистки дублікатів в колонці keywords таблиці places.

Використання:
    python fix_keywords.py test    # Виправити в test БД
    python fix_keywords.py prod    # Виправити в prod БД
    python fix_keywords.py test --dry-run  # Показати зміни без запису
"""
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent


def get_db_path(env: str) -> Path:
    """Повертає шлях до бази даних."""
    return PROJECT_ROOT / env / "state.db"


def clean_keywords(keywords: str) -> str:
    """
    Очищає keywords від дублікатів, зберігаючи порядок унікальних слів.
    
    Приклад:
        "кава,кафе,coffee adept kavy кав'ярня adept kavy кав'ярня adept kavy"
        ->
        "кава,кафе,coffee,adept,kavy,кав'ярня"
    """
    if not keywords:
        return ""
    
    # Розбиваємо по комах та пробілах
    # Спочатку замінюємо коми на пробіли для уніфікації
    normalized = keywords.replace(",", " ")
    
    # Розбиваємо на слова
    words = normalized.split()
    
    # Зберігаємо унікальні слова в порядку появи
    seen = set()
    unique_words = []
    
    for word in words:
        word_lower = word.lower().strip()
        if word_lower and word_lower not in seen:
            seen.add(word_lower)
            unique_words.append(word_lower)
    
    # З'єднуємо комами
    return ",".join(unique_words)


def fix_keywords(env: str, dry_run: bool = False):
    """Виправляє дублікати keywords в базі даних."""
    db_path = get_db_path(env)
    
    if not db_path.exists():
        print(f"❌ База даних не знайдена: {db_path}")
        sys.exit(1)
    
    print(f"📂 База даних: {db_path}")
    print(f"🔧 Режим: {'DRY-RUN (без змін)' if dry_run else 'ЗАПИС ЗМІН'}")
    print("-" * 60)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Отримуємо всі записи
    cursor.execute("SELECT id, name, keywords FROM places")
    rows = cursor.fetchall()
    
    changes = []
    
    for row_id, name, keywords in rows:
        if not keywords:
            continue
        
        cleaned = clean_keywords(keywords)
        
        # Перевіряємо чи змінилось
        if cleaned != keywords:
            old_len = len(keywords)
            new_len = len(cleaned)
            reduction = ((old_len - new_len) / old_len) * 100
            
            changes.append({
                'id': row_id,
                'name': name,
                'old': keywords,
                'new': cleaned,
                'old_len': old_len,
                'new_len': new_len,
                'reduction': reduction
            })
    
    if not changes:
        print("✅ Дублікатів не знайдено!")
        conn.close()
        return
    
    print(f"📊 Знайдено {len(changes)} записів з дублікатами:\n")
    
    total_saved = 0
    for change in changes:
        print(f"  [{change['id']}] {change['name']}")
        print(f"      До:    {change['old'][:80]}...")
        print(f"      Після: {change['new'][:80]}...")
        print(f"      Зменшення: {change['old_len']} → {change['new_len']} ({change['reduction']:.1f}%)")
        print()
        total_saved += change['old_len'] - change['new_len']
    
    print("-" * 60)
    print(f"📈 Загалом буде збережено: {total_saved} символів")
    
    if dry_run:
        print("\n⚠️  DRY-RUN режим - зміни НЕ записані")
    else:
        # Записуємо зміни
        for change in changes:
            cursor.execute(
                "UPDATE places SET keywords = ? WHERE id = ?",
                (change['new'], change['id'])
            )
        
        conn.commit()
        print(f"\n✅ Успішно оновлено {len(changes)} записів!")
    
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    env = sys.argv[1]
    if env not in ('test', 'prod'):
        print(f"❌ Невідоме середовище: {env}")
        print("   Використовуйте: test або prod")
        sys.exit(1)
    
    dry_run = '--dry-run' in sys.argv
    
    fix_keywords(env, dry_run)


if __name__ == "__main__":
    main()
