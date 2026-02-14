import os
import re
import time
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv

# Завантажуємо .env з робочого каталогу (там де запускається скрипт)
env_path = Path.cwd() / ".env"
load_dotenv(env_path)


def _set_process_timezone() -> None:
    """Apply TZ from env so datetime.now() matches local time."""
    tz = os.getenv("BOT_TIMEZONE") or os.getenv("WEATHER_TIMEZONE") or "Europe/Kyiv"
    if tz:
        os.environ["TZ"] = tz
        if hasattr(time, "tzset"):
            try:
                time.tzset()
            except Exception:
                # Якщо tzset не підтримується - ігноруємо
                pass


_set_process_timezone()


@dataclass
class Config:
    token: str
    admin_ids: list[int]  # Список ID адміністраторів
    admin_tag: str  # Тег адміністратора для зворотного зв'язку
    bot_username: str  # Username бота для inline режиму та тегів
    # Погода
    weather_lat: float
    weather_lon: float
    weather_api_url: str
    weather_timezone: str
    # Телефони сервісної служби
    security_phone: str
    plumber_phone: str
    electrician_phone: str
    elevator_phones: str
    # API тривог
    alerts_api_key: str  # ukrainealarm.com
    alerts_in_ua_api_key: str  # alerts.in.ua (друге джерело)
    alerts_city_id_ukrainealarm: str
    alerts_city_uid_alerts_in_ua: str
    alerts_api_url: str
    alerts_in_ua_api_url: str
    alerts_in_ua_ratio: int
    # API сервер для ESP32 сенсорів
    api_port: int  # Порт для HTTP API сервера
    sensor_api_key: str  # API ключ для сенсорів
    sensor_timeout: int  # Таймаут в секундах для визначення відключення
    # Sensor aliases: treat heartbeat from one (building, section) as present for others.
    # Mapping: (src_building_id, src_section_id) -> [(dst_building_id, dst_section_id), ...]
    sensor_aliases: dict[tuple[int, int], list[tuple[int, int]]]
    # Web App
    web_app_enabled: bool
    web_app_url: str
    web_app_debug_user_id: int | None
    # Yasno planned outages
    yasno_enabled: bool
    yasno_region_id: int
    yasno_dso_id: int
    # Single-message mode
    single_message_mode: bool
    # Separate admin bot (control plane)
    admin_bot_api_key: str
    admin_bot_single_message_mode: bool
    # Business mode
    business_mode: bool
    business_bot_api_key: str
    business_bot_single_message_mode: bool
    business_payment_provider: str


def parse_admin_ids(env_value: str) -> list[int]:
    """Парсить ID адміністраторів з рядка."""
    if not env_value:
        return []
    # Прибираємо лапки якщо є
    env_value = env_value.strip().strip('"').strip("'")
    ids = [id.strip() for id in env_value.replace(",", " ").split()]
    return [int(id) for id in ids if id.isdigit()]


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Парсить булеве значення з env."""
    if value is None:
        return default
    value = value.strip().strip('"').strip("'").lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_int(value: str | None) -> int | None:
    """Парсить int з env."""
    if value is None:
        return None
    value = value.strip().strip('"').strip("'")
    if not value:
        return None
    return int(value)


def parse_sensor_aliases_from_env() -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Parse SENSOR_ALIAS_* env vars into a mapping.

    Env format:
      SENSOR_ALIAS_<SRC_BUILDING_ID>_<SRC_SECTION_ID>="DSTB:DSTS,DSTB:DSTS"

    Example:
      SENSOR_ALIAS_1_2="1:1,1:3,5:3"

    Notes:
    - Invalid entries are ignored.
    - Self-mapping is ignored.
    - Duplicates are removed preserving order.
    """
    mapping: dict[tuple[int, int], list[tuple[int, int]]] = {}
    valid_sections = {1, 2, 3}
    prefix = "SENSOR_ALIAS_"

    for key, raw_value in os.environ.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        parts = rest.split("_")
        if len(parts) != 2:
            continue
        try:
            src_building_id = int(parts[0])
            src_section_id = int(parts[1])
        except Exception:
            continue
        if src_building_id <= 0 or src_section_id not in valid_sections:
            continue

        value = (raw_value or "").strip().strip('"').strip("'")
        if not value:
            continue

        # Accept comma/space/semicolon-separated targets.
        tokens = re.split(r"[,\s;]+", value)
        targets: list[tuple[int, int]] = []
        for token in tokens:
            token = token.strip().strip('"').strip("'")
            if not token:
                continue
            if ":" in token:
                b, s = token.split(":", 1)
            elif "_" in token:
                b, s = token.split("_", 1)
            else:
                continue
            if not b.isdigit() or not s.isdigit():
                continue
            dst_building_id = int(b)
            dst_section_id = int(s)
            if dst_building_id <= 0 or dst_section_id not in valid_sections:
                continue
            if (dst_building_id, dst_section_id) == (src_building_id, src_section_id):
                continue
            targets.append((dst_building_id, dst_section_id))

        if not targets:
            continue

        # De-dup (preserve order).
        uniq: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for t in targets:
            if t in seen:
                continue
            seen.add(t)
            uniq.append(t)

        if uniq:
            mapping[(src_building_id, src_section_id)] = uniq

    return mapping


CFG = Config(
    token=os.environ["BOT_TOKEN"],
    admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
    admin_tag=os.getenv("ADMIN_TAG", "").strip().strip('"').strip("'"),
    bot_username=os.getenv("BOT_USERNAME", "").strip().strip('"').strip("'"),
    weather_lat=float(os.getenv("WEATHER_LAT", "50.4501")),
    weather_lon=float(os.getenv("WEATHER_LON", "30.5234")),
    weather_api_url=os.getenv("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast").strip().strip('"').strip("'"),
    weather_timezone=os.getenv("WEATHER_TIMEZONE", "Europe/Kyiv").strip().strip('"').strip("'"),
    security_phone=os.getenv("SECURITY_PHONE", "").strip().strip('"').strip("'"),
    plumber_phone=os.getenv("PLUMBER_PHONE", "").strip().strip('"').strip("'"),
    electrician_phone=os.getenv("ELECTRICIAN_PHONE", "").strip().strip('"').strip("'"),
    elevator_phones=os.getenv("ELEVATOR_PHONES", "").strip().strip('"').strip("'"),
    alerts_api_key=os.getenv("ALERTS_API_KEY", "").strip().strip('"').strip("'"),
    alerts_in_ua_api_key=os.getenv("ALERTS_IN_UA_API_KEY", "").strip().strip('"').strip("'"),
    alerts_city_id_ukrainealarm=os.getenv("ALERTS_CITY_ID_UKRAINEALARM", "31").strip().strip('"').strip("'"),
    alerts_city_uid_alerts_in_ua=os.getenv("ALERTS_CITY_UID_ALERTS_IN_UA", "31").strip().strip('"').strip("'"),
    alerts_api_url=os.getenv("ALERTS_API_URL", "https://api.ukrainealarm.com/api/v3").strip().strip('"').strip("'"),
    alerts_in_ua_api_url=os.getenv("ALERTS_IN_UA_API_URL", "https://api.alerts.in.ua/v1").strip().strip('"').strip("'"),
    # За замовчуванням частіше ходимо в alerts.in.ua, щоб не ловити 401 у ukrainealarm
    alerts_in_ua_ratio=int(os.getenv("ALERTS_IN_UA_RATIO", "7")),
    # API сервер
    api_port=int(os.getenv("API_PORT", "8080")),
    sensor_api_key=os.getenv("SENSOR_API_KEY", "").strip().strip('"').strip("'"),
    sensor_timeout=int(os.getenv("SENSOR_TIMEOUT_SEC", "150")),
    sensor_aliases=parse_sensor_aliases_from_env(),
    web_app_enabled=parse_bool(os.getenv("WEB_APP", "0")),
    web_app_url=os.getenv("WEB_APP_URL", "").strip().strip('"').strip("'"),
    web_app_debug_user_id=parse_int(os.getenv("WEB_APP_DEBUG_USER_ID")),
    yasno_enabled=parse_bool(os.getenv("YASNO_ENABLED", "0")),
    yasno_region_id=int(os.getenv("YASNO_REGION_ID", "25")),
    yasno_dso_id=int(os.getenv("YASNO_DSO_ID", "902")),
    single_message_mode=parse_bool(os.getenv("SINGLE_MESSAGE_MODE", "0")),
    admin_bot_api_key=os.getenv("ADMIN_BOT_API_KEY", "").strip().strip('"').strip("'"),
    admin_bot_single_message_mode=parse_bool(os.getenv("ADMIN_BOT_SINGLE_MESSAGE_MODE", "1")),
    business_mode=parse_bool(os.getenv("BUSINESS_MODE", "0")),
    business_bot_api_key=os.getenv("BUSINESS_BOT_API_KEY", "").strip().strip('"').strip("'"),
    business_bot_single_message_mode=parse_bool(os.getenv("BUSINESS_BOT_SINGLE_MESSAGE_MODE", "1")),
    business_payment_provider=os.getenv("BUSINESS_PAYMENT_PROVIDER", "telegram_stars").strip().strip('"').strip("'").lower(),
)

# Шлях до БД: з env або відносно робочого каталогу
DB_PATH = os.getenv("DB_PATH", str(Path.cwd() / "state.db"))


def is_business_mode_enabled() -> bool:
    """Business mode is enabled only when flag is on."""
    return CFG.business_mode


def is_business_bot_enabled() -> bool:
    """Business bot process is enabled only with a non-empty token.

    Note: BUSINESS_MODE is a feature-flag for the *main (resident) bot UI/logic*.
    Business bot runtime can be enabled independently to allow "stealth" rollout
    (businessbot running while resident UI stays legacy with BUSINESS_MODE=0).
    """
    return bool(CFG.business_bot_api_key)


def is_admin_bot_enabled() -> bool:
    """Admin bot process is enabled only with non-empty token."""
    return bool(CFG.admin_bot_api_key)
