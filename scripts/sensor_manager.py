#!/usr/bin/env python3
"""
Менеджер сенсорів ESP32 для PowerBot.

Команди:
    python sensor_manager.py list                    # Список всіх сенсорів в БД
    python sensor_manager.py buildings               # Список будинків
    python sensor_manager.py info 1                  # Інфо для ESP32 (building_id=1)
    python sensor_manager.py delete <uuid>           # Видалити сенсор з БД
    python sensor_manager.py delete-all              # Видалити ВСІ сенсори з БД
    python sensor_manager.py token                   # Показати поточний токен
    python sensor_manager.py token --generate        # Згенерувати НОВИЙ токен (обережно!)
    python sensor_manager.py test <building_id>      # Надіслати тестовий heartbeat
"""
import secrets
import argparse
import sys
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# Визначаємо шляхи
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Будинки ЖК "Нова Англія"
BUILDINGS = {
    1: {"name": "Ньюкасл", "name_lat": "newcastle", "address": "24-в"},
    2: {"name": "Брістоль", "name_lat": "bristol", "address": "24-б"},
    3: {"name": "Ліверпуль", "name_lat": "liverpool", "address": "24-а"},
    4: {"name": "Ноттінгем", "name_lat": "nottingham", "address": "24-г"},
    5: {"name": "Манчестер", "name_lat": "manchester", "address": "26-г"},
    6: {"name": "Кембрідж", "name_lat": "cambridge", "address": "26"},
    7: {"name": "Брайтон", "name_lat": "brighton", "address": "26-в"},
    8: {"name": "Бермінгем", "name_lat": "birmingham", "address": "26-б"},
    9: {"name": "Віндзор", "name_lat": "windsor", "address": "26-д"},
    10: {"name": "Честер", "name_lat": "chester", "address": "28-д"},
    11: {"name": "Лондон", "name_lat": "london", "address": "28-е"},
    12: {"name": "Оксфорд", "name_lat": "oxford", "address": "28-б"},
    13: {"name": "Лінкольн", "name_lat": "lincoln", "address": "28-к"},
    14: {"name": "Престон", "name_lat": "preston", "address": "-"},
}


def get_env_path(env: str) -> Path:
    """Повертає шлях до .env файлу."""
    return PROJECT_ROOT / env / ".env"


def get_db_path(env: str) -> Path:
    """Повертає шлях до БД."""
    return PROJECT_ROOT / env / "state.db"


def read_env_token(env: str) -> str | None:
    """Читає SENSOR_API_KEY з .env файлу."""
    env_path = get_env_path(env)
    if not env_path.exists():
        return None
    
    with open(env_path, 'r') as f:
        for line in f:
            if line.startswith('SENSOR_API_KEY='):
                return line.strip().split('=', 1)[1]
    return None


def generate_sensor_uuid(building_id: int, sensor_num: int = 1) -> str:
    """Генерує UUID для сенсора."""
    building = BUILDINGS.get(building_id)
    if not building:
        raise ValueError(f"Будинок {building_id} не існує")
    return f"esp32-{building['name_lat']}-{sensor_num:03d}"


def generate_token(length: int = 32) -> str:
    """Генерує криптографічно безпечний токен."""
    return secrets.token_hex(length)


# ===== КОМАНДИ =====

def cmd_buildings(args):
    """Список будинків."""
    print("\n📋 Список будинків ЖК \"Нова Англія\":\n")
    print(f"{'ID':<4} {'Назва':<12} {'Адреса':<10} {'UUID сенсора':<25}")
    print("-" * 55)
    for bid, info in sorted(BUILDINGS.items()):
        uuid = f"esp32-{info['name_lat']}-001"
        print(f"{bid:<4} {info['name']:<12} {info['address']:<10} {uuid:<25}")
    print()


def cmd_list(args):
    """Список сенсорів в БД."""
    db_path = get_db_path(args.env)
    
    if not db_path.exists():
        print(f"❌ БД не знайдено: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.uuid, s.building_id, b.name, s.last_heartbeat, s.is_active
        FROM sensors s
        LEFT JOIN buildings b ON s.building_id = b.id
        ORDER BY s.building_id, s.uuid
    """)
    
    sensors = cursor.fetchall()
    conn.close()
    
    if not sensors:
        print(f"\n📭 Сенсорів в БД ({args.env}) не знайдено\n")
        return
    
    print(f"\n📡 Сенсори в БД ({args.env}):\n")
    print(f"{'UUID':<25} {'Будинок':<15} {'Останній heartbeat':<22} {'Статус':<10}")
    print("-" * 75)
    
    for uuid, building_id, building_name, last_hb, is_active in sensors:
        building = building_name or BUILDINGS.get(building_id, {}).get("name", f"ID:{building_id}")
        status = "✅ Активний" if is_active else "❌ Неактивний"
        last_hb_str = last_hb[:19] if last_hb else "Ніколи"
        print(f"{uuid:<25} {building:<15} {last_hb_str:<22} {status:<10}")
    
    print(f"\nВсього: {len(sensors)} сенсор(ів)\n")


def cmd_info(args):
    """Інформація для налаштування ESP32."""
    building_id = args.building_id
    building = BUILDINGS.get(building_id)
    
    if not building:
        print(f"❌ Будинок з ID {building_id} не існує")
        cmd_buildings(args)
        return
    
    sensor_uuid = generate_sensor_uuid(building_id, args.sensor_num)
    token = read_env_token(args.env) or "НЕ НАЛАШТОВАНО"
    
    # API endpoint (через nginx на порт 80)
    api_host = "64.181.205.211"
    api_port = "80"
    endpoint = "/api/v1/heartbeat-test" if args.env == "test" else "/api/v1/heartbeat"
    
    print(f"""
🏠 Будинок: {building['name']} ({building['address']})
📍 Building ID: {building_id}
📡 Sensor UUID: {sensor_uuid}

═══════════════════════════════════════════════════════════════

⚙️ Налаштування для ESP32 (include/config.h):

   #define SERVER_IP       "{api_host}"
   #define SERVER_PORT     {api_port}
   #define API_KEY         "{token}"
   #define BUILDING_ID     {building_id}
   #define SENSOR_UUID     "{sensor_uuid}"

═══════════════════════════════════════════════════════════════

📤 Тестовий curl запит:

   curl -X POST http://{api_host}{endpoint} \\
     -H "Content-Type: application/json" \\
     -d '{{"api_key": "{token}", "building_id": {building_id}, "sensor_uuid": "{sensor_uuid}"}}'

═══════════════════════════════════════════════════════════════

📋 JSON для heartbeat:

   {{
     "api_key": "{token}",
     "building_id": {building_id},
     "sensor_uuid": "{sensor_uuid}"
   }}
""")


def cmd_delete(args):
    """Видалити сенсор з БД."""
    db_path = get_db_path(args.env)
    
    if not db_path.exists():
        print(f"❌ БД не знайдено: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Перевіряємо чи існує
    cursor.execute("SELECT uuid, building_id FROM sensors WHERE uuid = ?", (args.uuid,))
    sensor = cursor.fetchone()
    
    if not sensor:
        print(f"❌ Сенсор '{args.uuid}' не знайдено в БД")
        conn.close()
        return
    
    # Підтвердження
    if not args.force:
        building = BUILDINGS.get(sensor[1], {}).get("name", f"ID:{sensor[1]}")
        confirm = input(f"⚠️  Видалити сенсор '{args.uuid}' ({building})? [y/N]: ")
        if confirm.lower() != 'y':
            print("Скасовано")
            conn.close()
            return
    
    cursor.execute("DELETE FROM sensors WHERE uuid = ?", (args.uuid,))
    conn.commit()
    conn.close()
    
    print(f"✅ Сенсор '{args.uuid}' видалено")


def cmd_delete_all(args):
    """Видалити ВСІ сенсори з БД."""
    db_path = get_db_path(args.env)
    
    if not db_path.exists():
        print(f"❌ БД не знайдено: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM sensors")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("📭 Сенсорів немає")
        conn.close()
        return
    
    # Підтвердження
    if not args.force:
        confirm = input(f"⚠️  Видалити ВСІ {count} сенсор(ів)? [y/N]: ")
        if confirm.lower() != 'y':
            print("Скасовано")
            conn.close()
            return
    
    cursor.execute("DELETE FROM sensors")
    conn.commit()
    conn.close()
    
    print(f"✅ Видалено {count} сенсор(ів)")


def cmd_token(args):
    """Управління токеном."""
    if args.generate:
        new_token = generate_token(32)
        print(f"""
🔑 НОВИЙ API токен для сенсорів:

   SENSOR_API_KEY={new_token}

⚠️  УВАГА:
   1. Замініть SENSOR_API_KEY в .env файлі вручну
   2. Перезапустіть бота: sudo systemctl restart bot-{args.env}.service
   3. Оновіть токен у ВСІХ ESP32 пристроях!
   
   Старий токен перестане працювати!
""")
    else:
        # Показати поточний токен
        token_test = read_env_token("test")
        token_prod = read_env_token("prod")
        
        print(f"""
🔑 Поточні API токени для сенсорів:

   TEST:  {token_test or "НЕ НАЛАШТОВАНО"}
   PROD:  {token_prod or "НЕ НАЛАШТОВАНО"}

💡 Для генерації нового токена: python sensor_manager.py token --generate
""")


def cmd_test(args):
    """Надіслати тестовий heartbeat."""
    import urllib.request
    import json
    
    building_id = args.building_id
    building = BUILDINGS.get(building_id)
    
    if not building:
        print(f"❌ Будинок з ID {building_id} не існує")
        return
    
    token = read_env_token(args.env)
    if not token:
        print(f"❌ SENSOR_API_KEY не знайдено в {args.env}/.env")
        return
    
    sensor_uuid = generate_sensor_uuid(building_id, args.sensor_num)
    
    # Через nginx на порт 80 (локально для тестів)
    endpoint = "/api/v1/heartbeat-test" if args.env == "test" else "/api/v1/heartbeat"
    url = f"http://127.0.0.1:80{endpoint}"
    
    data = {
        "api_key": token,
        "building_id": building_id,
        "sensor_uuid": sensor_uuid
    }
    
    print(f"📤 Відправляю heartbeat на {url}...")
    print(f"   building_id: {building_id} ({building['name']})")
    print(f"   sensor_uuid: {sensor_uuid}")
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(f"\n✅ Успішно! Відповідь: {result}")
            
    except urllib.error.HTTPError as e:
        print(f"\n❌ HTTP помилка {e.code}: {e.read().decode('utf-8')}")
    except urllib.error.URLError as e:
        print(f"\n❌ Помилка з'єднання: {e.reason}")
    except Exception as e:
        print(f"\n❌ Помилка: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Менеджер сенсорів ESP32 для PowerBot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Приклади:
  %(prog)s buildings               Список будинків
  %(prog)s list                    Сенсори в БД (test)
  %(prog)s list --env prod         Сенсори в БД (prod)
  %(prog)s info 1                  Налаштування для Ньюкасла
  %(prog)s info 2 -n 2             Налаштування для 2-го сенсора Брістоля
  %(prog)s delete esp32-test-001   Видалити сенсор
  %(prog)s token                   Показати токени
  %(prog)s token --generate        Згенерувати новий токен
  %(prog)s test 1                  Тестовий heartbeat для Ньюкасла
"""
    )
    
    parser.add_argument(
        "--env", "-e",
        choices=["test", "prod"],
        default="test",
        help="Середовище (test/prod), за замовчуванням: test"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Команда")
    
    # buildings
    sub_buildings = subparsers.add_parser("buildings", help="Список будинків")
    sub_buildings.set_defaults(func=cmd_buildings)
    
    # list
    sub_list = subparsers.add_parser("list", help="Список сенсорів в БД")
    sub_list.set_defaults(func=cmd_list)
    
    # info
    sub_info = subparsers.add_parser("info", help="Інфо для налаштування ESP32")
    sub_info.add_argument("building_id", type=int, help="ID будинку (1-14)")
    sub_info.add_argument("-n", "--sensor-num", type=int, default=1, help="Номер сенсора (за замовчуванням 1)")
    sub_info.set_defaults(func=cmd_info)
    
    # delete
    sub_delete = subparsers.add_parser("delete", help="Видалити сенсор з БД")
    sub_delete.add_argument("uuid", help="UUID сенсора для видалення")
    sub_delete.add_argument("-f", "--force", action="store_true", help="Без підтвердження")
    sub_delete.set_defaults(func=cmd_delete)
    
    # delete-all
    sub_delete_all = subparsers.add_parser("delete-all", help="Видалити ВСІ сенсори")
    sub_delete_all.add_argument("-f", "--force", action="store_true", help="Без підтвердження")
    sub_delete_all.set_defaults(func=cmd_delete_all)
    
    # token
    sub_token = subparsers.add_parser("token", help="Управління токеном")
    sub_token.add_argument("--generate", "-g", action="store_true", help="Згенерувати новий токен")
    sub_token.set_defaults(func=cmd_token)
    
    # test
    sub_test = subparsers.add_parser("test", help="Тестовий heartbeat")
    sub_test.add_argument("building_id", type=int, help="ID будинку (1-14)")
    sub_test.add_argument("-n", "--sensor-num", type=int, default=1, help="Номер сенсора")
    sub_test.set_defaults(func=cmd_test)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    args.func(args)


if __name__ == "__main__":
    main()
