import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv

# Завантажуємо .env з робочого каталогу (там де запускається скрипт)
env_path = Path.cwd() / ".env"
load_dotenv(env_path)


@dataclass
class Config:
    token: str
    home_ips: list[str]  # Список IP адрес для моніторингу (DEPRECATED - буде видалено)
    check_interval: int
    fails_to_down: int
    successes_to_up: int
    timeout_sec: int
    down_threshold: float  # Відсоток недоступних IP для оголошення DOWN (0.0-1.0)
    min_fail_hosts: int    # Мінімальна кількість недоступних IP для оголошення DOWN
    admin_ids: list[int]  # Список ID адміністраторів
    admin_tag: str  # Тег адміністратора для зворотного зв'язку
    bot_username: str  # Username бота для inline режиму та тегів
    bot_mode: str  # Режим роботи: "prod" або "test"
    # Телефони сервісної служби
    security_phone: str
    plumber_phone: str
    electrician_phone: str
    elevator_phones: str
    # API тривог
    alerts_api_key: str  # ukrainealarm.com
    alerts_in_ua_api_key: str  # alerts.in.ua (друге джерело)
    # API сервер для ESP32 сенсорів
    api_port: int  # Порт для HTTP API сервера
    sensor_api_key: str  # API ключ для сенсорів
    sensor_timeout: int  # Таймаут в секундах для визначення відключення


def parse_ips(env_value: str) -> list[str]:
    """Парсить IP адреси з рядка (через кому або пробіл)."""
    # Прибираємо лапки якщо є (для сумісності з systemd EnvironmentFile)
    env_value = env_value.strip().strip('"').strip("'")
    ips = [ip.strip() for ip in env_value.replace(",", " ").split()]
    return [ip for ip in ips if ip]


def parse_admin_ids(env_value: str) -> list[int]:
    """Парсить ID адміністраторів з рядка."""
    if not env_value:
        return []
    # Прибираємо лапки якщо є
    env_value = env_value.strip().strip('"').strip("'")
    ids = [id.strip() for id in env_value.replace(",", " ").split()]
    return [int(id) for id in ids if id.isdigit()]


CFG = Config(
    token=os.environ["BOT_TOKEN"],
    home_ips=parse_ips(os.getenv("HOME_IP", "")),  # DEPRECATED
    check_interval=int(os.getenv("CHECK_INTERVAL_SEC", "15")),
    fails_to_down=int(os.getenv("FAILS_TO_DECLARE_DOWN", "150")),
    successes_to_up=int(os.getenv("SUCCESSES_TO_DECLARE_UP", "1")),
    timeout_sec=int(os.getenv("TIMEOUT_SEC", "1")),
    down_threshold=float(os.getenv("DOWN_THRESHOLD", "0.6")),
    min_fail_hosts=int(os.getenv("MIN_FAIL_HOSTS", "10")),
    admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
    admin_tag=os.getenv("ADMIN_TAG", "").strip().strip('"').strip("'"),
    bot_username=os.getenv("BOT_USERNAME", "").strip().strip('"').strip("'"),
    bot_mode=os.getenv("BOT_MODE", "prod").strip().strip('"').strip("'"),
    security_phone=os.getenv("SECURITY_PHONE", "").strip().strip('"').strip("'"),
    plumber_phone=os.getenv("PLUMBER_PHONE", "").strip().strip('"').strip("'"),
    electrician_phone=os.getenv("ELECTRICIAN_PHONE", "").strip().strip('"').strip("'"),
    elevator_phones=os.getenv("ELEVATOR_PHONES", "").strip().strip('"').strip("'"),
    alerts_api_key=os.getenv("ALERTS_API_KEY", "").strip().strip('"').strip("'"),
    alerts_in_ua_api_key=os.getenv("ALERTS_IN_UA_API_KEY", "").strip().strip('"').strip("'"),
    # API сервер
    api_port=int(os.getenv("API_PORT", "8080")),
    sensor_api_key=os.getenv("SENSOR_API_KEY", "").strip().strip('"').strip("'"),
    sensor_timeout=int(os.getenv("SENSOR_TIMEOUT_SEC", "150")),
)

# Шлях до БД відносно робочого каталогу
DB_PATH = str(Path.cwd() / "state.db")
