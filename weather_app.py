import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")

GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/direct"
CURRENT_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAYS = (1, 2, 4)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
CACHE_TTL = timedelta(hours=3)
CACHE_FILE = Path(__file__).resolve().parent / "weather_cache.json"
COORDINATES_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*°?\s*(?:[,;]\s*|\s+)"
    r"([+-]?\d+(?:[.,]\d+)?)\s*°?\s*$"
)


class WeatherError(Exception):
    pass


class MissingApiKeyError(WeatherError):
    pass


class InvalidApiKeyError(WeatherError):
    pass


class CityNotFoundError(WeatherError):
    pass


class TemporaryWeatherError(WeatherError):
    pass


def has_api_key() -> bool:
    return bool((API_KEY or "").strip())


def _require_api_key() -> str:
    if not has_api_key():
        raise MissingApiKeyError(
            "API_KEY не найден. Добавьте ключ OpenWeather в файл .env: API_KEY=ваш_ключ"
        )
    return API_KEY.strip()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coordinate(value: Any, name: str, minimum: float, maximum: float) -> float:
    if not _is_number(value):
        raise WeatherError(f"{name} должна быть числом.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise WeatherError(f"{name} должна быть в диапазоне от {minimum:g} до {maximum:g}.")
    return result


def _validate_coordinates(lat: float, lon: float) -> tuple[float, float]:
    latitude = _coordinate(lat, "Широта", -90.0, 90.0)
    longitude = _coordinate(lon, "Долгота", -180.0, 180.0)
    return latitude, longitude


def parse_coordinates(text: str) -> tuple[float, float]:
    normalized = text.replace("º", "°").replace("˚", "°")
    match = COORDINATES_PATTERN.fullmatch(normalized)
    if match is None:
        raise WeatherError(
            "Введите две координаты: широту и долготу "
            "(например, 55.75 37.62 или 43,585°, 39,723°)."
        )
    lat = float(match.group(1).replace(",", "."))
    lon = float(match.group(2).replace(",", "."))
    return _validate_coordinates(lat, lon)


def get_clipboard_text() -> str:
    try:
        import tkinter as tk
    except ImportError:
        raise WeatherError(
            "Не удалось прочитать буфер обмена: tkinter недоступен."
        ) from None

    try:
        root = tk.Tk()
    except tk.TclError:
        raise WeatherError(
            "Не удалось открыть доступ к буферу обмена."
        ) from None

    root.withdraw()
    try:
        text = root.clipboard_get()
    except tk.TclError:
        raise WeatherError("Буфер обмена пуст или недоступен.") from None
    finally:
        root.destroy()

    clean_text = text.strip()
    if not clean_text:
        raise WeatherError("Буфер обмена пуст.")
    return clean_text


def _api_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str):
            return message.strip()
    return ""


def _request_json(url: str, params: dict[str, Any]) -> Any:
    attempts = MAX_RETRIES + 1
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise TemporaryWeatherError(
                f"Не удалось связаться с OpenWeather после {attempts} попыток. "
                "Проверьте подключение к интернету."
            ) from None

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAYS[attempt])
            continue

        if response.status_code == 401:
            raise InvalidApiKeyError(
                "OpenWeather отклонил API-ключ (HTTP 401). Проверьте API_KEY в файле .env."
            )

        if response.status_code in RETRYABLE_STATUS_CODES:
            if response.status_code == 429:
                raise TemporaryWeatherError(
                    f"OpenWeather временно ограничил частоту запросов (HTTP 429) "
                    f"после {attempts} попыток. Попробуйте позже."
                )
            raise TemporaryWeatherError(
                f"Сервис OpenWeather временно недоступен (HTTP {response.status_code}) "
                f"после {attempts} попыток. Попробуйте позже."
            )

        if response.status_code != 200:
            details = _api_message(response)
            suffix = f" {details}" if details else ""
            raise WeatherError(
                f"OpenWeather вернул ошибку HTTP {response.status_code}.{suffix}"
            )

        try:
            return response.json()
        except ValueError:
            raise WeatherError(
                "OpenWeather вернул ответ в неожиданном формате (не JSON)."
            ) from None

    raise TemporaryWeatherError("Не удалось выполнить запрос к OpenWeather.")


def get_coordinates(city: str) -> tuple[float, float]:
    clean_city = city.strip()
    if not clean_city:
        raise WeatherError("Название города не может быть пустым.")

    payload = _request_json(
        GEOCODING_URL,
        {
            "q": clean_city,
            "limit": 1,
            "lang": "ru",
            "appid": _require_api_key(),
        },
    )

    if not isinstance(payload, list) or not payload:
        raise CityNotFoundError(f"Город «{clean_city}» не найден. Проверьте название.")

    first_result = payload[0]
    if not isinstance(first_result, dict):
        raise WeatherError("Сервис геокодинга вернул данные в неожиданном формате.")

    return _validate_coordinates(first_result.get("lat"), first_result.get("lon"))


def get_weather_by_coordinates(lat: float, lon: float) -> dict:
    latitude, longitude = _validate_coordinates(lat, lon)
    payload = _request_json(
        CURRENT_WEATHER_URL,
        {
            "lat": latitude,
            "lon": longitude,
            "units": "metric",
            "lang": "ru",
            "appid": _require_api_key(),
        },
    )

    if not isinstance(payload, dict):
        raise WeatherError("Сервис погоды вернул данные в неожиданном формате.")
    return payload


def city_from_weather(data: dict, fallback: str) -> str:
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback


def format_weather(city: str, data: dict) -> str:
    clean_city = city.strip()
    if not clean_city:
        raise WeatherError("Не удалось определить город для вывода.")

    main = data.get("main")
    if not isinstance(main, dict) or not _is_number(main.get("temp")):
        raise WeatherError("OpenWeather не вернул температуру воздуха.")

    conditions = data.get("weather")
    if not isinstance(conditions, list) or not conditions or not isinstance(conditions[0], dict):
        raise WeatherError("OpenWeather не вернул описание погоды.")

    description = conditions[0].get("description")
    if not isinstance(description, str) or not description.strip():
        raise WeatherError("OpenWeather не вернул описание погоды.")

    temperature = float(main["temp"])
    return f"Погода в {clean_city}: {temperature:.1f}°C, {description.strip()}"


def _parse_fetched_at(cache: dict) -> datetime | None:
    value = cache.get("fetched_at")
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def save_cache(city: str, lat: float, lon: float, weather: dict) -> None:
    payload = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "weather": weather,
    }
    temp_path = CACHE_FILE.with_name(f"{CACHE_FILE.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(CACHE_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise WeatherError("Не удалось сохранить данные погоды в кэш.") from None


def load_cache() -> dict | None:
    try:
        raw = CACHE_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("city"), str) or not payload["city"].strip():
        return None
    if not isinstance(payload.get("weather"), dict):
        return None
    if not _is_number(payload.get("lat")) or not _is_number(payload.get("lon")):
        return None
    if _parse_fetched_at(payload) is None:
        return None
    return payload


def get_cache_age(cache: dict) -> timedelta | None:
    fetched_at = _parse_fetched_at(cache)
    if fetched_at is None:
        return None
    age = datetime.now(timezone.utc) - fetched_at
    if age < timedelta(0):
        return timedelta(0)
    return age


def format_age(age: timedelta) -> str:
    total_minutes = max(0, int(age.total_seconds() // 60))
    if total_minutes < 1:
        return "меньше минуты"
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def format_cache_time(cache: dict) -> str:
    fetched_at = _parse_fetched_at(cache)
    if fetched_at is None:
        return "время неизвестно"
    return fetched_at.astimezone().strftime("%d.%m.%Y %H:%M")


def load_fresh_cache() -> tuple[dict | None, str]:
    cache = load_cache()
    if cache is None:
        return None, "Кэш отсутствует или повреждён."
    age = get_cache_age(cache)
    if age is None:
        return None, "Не удалось определить время сохранения кэша."
    if age >= CACHE_TTL:
        return None, f"Кэш устарел: сохранён {format_age(age)} назад (лимит 3 ч)."
    return cache, ""


def _ask_yes_no(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes", "д", "да"}


def _offer_cache(error_message: str) -> None:
    cache, reason = load_fresh_cache()
    print(error_message)
    if cache is None:
        print(reason)
        return

    try:
        cached_line = format_weather(cache["city"], cache["weather"])
    except WeatherError:
        print("Данные в кэше повреждены.")
        return

    fetched_at = format_cache_time(cache)
    prompt = (
        f"В кэше есть погода для «{cache['city']}» от {fetched_at}. "
        "Вывести её? [y/N]: "
    )
    if _ask_yes_no(prompt):
        print(cached_line)
        print(f"Данные из кэша, сохранены {fetched_at}.")


def _run_city_mode() -> None:
    try:
        city = input("Введите название города: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not city:
        print("Название города не может быть пустым.")
        return

    try:
        lat, lon = get_coordinates(city)
        weather = get_weather_by_coordinates(lat, lon)
        line = format_weather(city, weather)
    except TemporaryWeatherError as error:
        _offer_cache(str(error))
        return
    except WeatherError as error:
        print(error)
        return

    print(line)
    try:
        save_cache(city, lat, lon, weather)
    except WeatherError as error:
        print(f"Предупреждение: {error}")


def _run_coordinates_mode() -> None:
    try:
        raw_coordinates = input(
            "Введите координаты, v — вставить из буфера обмена, 0 — назад "
            "(например, 43,585°, 39,723°): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if raw_coordinates == "0":
        return
    if raw_coordinates.lower() in {"v", "в"}:
        try:
            raw_coordinates = get_clipboard_text()
        except WeatherError as error:
            print(error)
            return
        print(f"Из буфера обмена: {raw_coordinates}")

    try:
        lat, lon = parse_coordinates(raw_coordinates)
    except WeatherError as error:
        print(error)
        return

    try:
        weather = get_weather_by_coordinates(lat, lon)
        fallback_city = f"координаты {lat:.4f}, {lon:.4f}"
        city = city_from_weather(weather, fallback_city)
        line = format_weather(city, weather)
    except TemporaryWeatherError as error:
        _offer_cache(str(error))
        return
    except WeatherError as error:
        print(error)
        return

    print(line)
    try:
        save_cache(city, lat, lon, weather)
    except WeatherError as error:
        print(f"Предупреждение: {error}")


def run_cli() -> None:
    if not has_api_key():
        print(
            "API_KEY не найден. Добавьте ключ OpenWeather в файл .env "
            "(API_KEY=ваш_ключ) и запустите приложение снова."
        )
        return

    while True:
        print("\nРежим ввода:")
        print("1 — по городу")
        print("2 — по координатам")
        print("0 — выход")

        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nДо свидания!")
            return

        if choice == "0":
            print("До свидания!")
            return
        if choice == "1":
            try:
                _run_city_mode()
            except Exception:
                print("Произошла непредвиденная ошибка. Попробуйте ещё раз.")
        elif choice == "2":
            try:
                _run_coordinates_mode()
            except Exception:
                print("Произошла непредвиденная ошибка. Попробуйте ещё раз.")
        else:
            print("Неизвестный пункт меню. Введите 1, 2 или 0.")


if __name__ == "__main__":
    run_cli()
