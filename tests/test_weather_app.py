import json
import tempfile
import tkinter
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

import weather_app


def make_response(status_code: int, payload):
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = payload
    return response


class WeatherAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache_path = Path(self.temp_dir.name) / "weather_cache.json"

        self.cache_patch = patch.object(weather_app, "CACHE_FILE", self.cache_path)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

        self.old_api_key = weather_app.API_KEY
        weather_app.API_KEY = "test-key"
        self.addCleanup(self._restore_api_key)

        self.sleep_patch = patch("weather_app.time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def _restore_api_key(self) -> None:
        weather_app.API_KEY = self.old_api_key

    def weather_payload(self) -> dict:
        return {
            "name": "Москва",
            "main": {"temp": 12.4},
            "weather": [{"description": "пасмурно"}],
        }

    def test_get_coordinates_success(self) -> None:
        response = make_response(200, [{"lat": 55.75, "lon": 37.62}])
        with patch("weather_app.requests.get", return_value=response) as mock_get:
            result = weather_app.get_coordinates(" Москва ")

        self.assertEqual(result, (55.75, 37.62))
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs["params"]["q"], "Москва")
        self.assertEqual(kwargs["params"]["limit"], 1)
        self.assertEqual(kwargs["params"]["lang"], "ru")
        self.assertEqual(kwargs["params"]["appid"], "test-key")
        self.assertEqual(kwargs["timeout"], weather_app.REQUEST_TIMEOUT)

    def test_get_coordinates_empty_response(self) -> None:
        response = make_response(200, [])
        with patch("weather_app.requests.get", return_value=response):
            with self.assertRaises(weather_app.CityNotFoundError):
                weather_app.get_coordinates("Несуществующий город")

    def test_invalid_api_key_is_not_retried(self) -> None:
        response = make_response(401, {"message": "Invalid API key"})
        with patch("weather_app.requests.get", return_value=response) as mock_get:
            with self.assertRaises(weather_app.InvalidApiKeyError):
                weather_app.get_coordinates("Москва")

        self.assertEqual(mock_get.call_count, 1)
        self.mock_sleep.assert_not_called()

    def test_retry_on_429_uses_exponential_delays(self) -> None:
        responses = [
            make_response(429, {}),
            make_response(429, {}),
            make_response(200, [{"lat": 55.75, "lon": 37.62}]),
        ]
        with patch("weather_app.requests.get", side_effect=responses) as mock_get:
            result = weather_app.get_coordinates("Москва")

        self.assertEqual(result, (55.75, 37.62))
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(self.mock_sleep.call_args_list, [call(1), call(2)])

    def test_retry_on_server_error_then_success(self) -> None:
        responses = [
            make_response(503, {}),
            make_response(200, [{"lat": 55.75, "lon": 37.62}]),
        ]
        with patch("weather_app.requests.get", side_effect=responses):
            result = weather_app.get_coordinates("Москва")

        self.assertEqual(result, (55.75, 37.62))
        self.assertEqual(self.mock_sleep.call_args_list, [call(1)])

    def test_network_error_exhausts_retries(self) -> None:
        error = requests.ConnectionError("connection failed")
        with patch("weather_app.requests.get", side_effect=error) as mock_get:
            with self.assertRaises(weather_app.TemporaryWeatherError):
                weather_app.get_coordinates("Москва")

        self.assertEqual(mock_get.call_count, weather_app.MAX_RETRIES + 1)
        self.assertEqual(
            self.mock_sleep.call_args_list,
            [call(1), call(2), call(4)],
        )

    def test_get_weather_and_format_result(self) -> None:
        payload = self.weather_payload()
        response = make_response(200, payload)
        with patch("weather_app.requests.get", return_value=response) as mock_get:
            data = weather_app.get_weather_by_coordinates(55.75, 37.62)

        self.assertIs(data, payload)
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["lat"], 55.75)
        self.assertEqual(params["lon"], 37.62)
        self.assertEqual(params["units"], "metric")
        self.assertEqual(params["lang"], "ru")
        self.assertEqual(
            weather_app.format_weather("Москва", data),
            "Погода в Москва: 12.4°C, пасмурно",
        )

    def test_invalid_coordinates_do_not_send_request(self) -> None:
        with patch("weather_app.requests.get") as mock_get:
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_weather_by_coordinates(91.0, 37.62)

        mock_get.assert_not_called()

    def test_parse_coordinates(self) -> None:
        self.assertEqual(weather_app.parse_coordinates("55.75, 37.62"), (55.75, 37.62))
        with self.assertRaises(weather_app.WeatherError):
            weather_app.parse_coordinates("55.75")
        with self.assertRaises(weather_app.WeatherError):
            weather_app.parse_coordinates("100 37.62")
        with self.assertRaises(weather_app.WeatherError):
            weather_app.parse_coordinates("север 37.62")

    def test_parse_coordinates_with_decimal_comma_and_degree_sign(self) -> None:
        self.assertEqual(
            weather_app.parse_coordinates("43,585°, 39,723°"),
            (43.585, 39.723),
        )
        self.assertEqual(
            weather_app.parse_coordinates("55,75 37,62"),
            (55.75, 37.62),
        )
        self.assertEqual(
            weather_app.parse_coordinates("-43,585°; -39,723°"),
            (-43.585, -39.723),
        )
        self.assertEqual(
            weather_app.parse_coordinates("43.585° 39.723°"),
            (43.585, 39.723),
        )

    def test_get_clipboard_text(self) -> None:
        root = Mock()
        root.clipboard_get.return_value = " 43,585°, 39,723°\n"
        with patch("tkinter.Tk", return_value=root):
            text = weather_app.get_clipboard_text()

        self.assertEqual(text, "43,585°, 39,723°")
        root.withdraw.assert_called_once()
        root.destroy.assert_called_once()

    def test_get_clipboard_text_rejects_empty_clipboard(self) -> None:
        root = Mock()
        root.clipboard_get.side_effect = tkinter.TclError("empty")
        with patch("tkinter.Tk", return_value=root):
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_clipboard_text()

        root.destroy.assert_called_once()

    def test_format_weather_rejects_incomplete_data(self) -> None:
        with self.assertRaises(weather_app.WeatherError):
            weather_app.format_weather("Москва", {})
        with self.assertRaises(weather_app.WeatherError):
            weather_app.format_weather("Москва", {"main": {"temp": 10.0}})

    def test_city_from_weather_uses_fallback(self) -> None:
        payload = {"name": "Moscow"}
        self.assertEqual(weather_app.city_from_weather(payload, "координаты"), "Moscow")
        self.assertEqual(
            weather_app.city_from_weather({}, "координаты 55.75, 37.62"),
            "координаты 55.75, 37.62",
        )

    def test_cache_roundtrip(self) -> None:
        payload = self.weather_payload()
        weather_app.save_cache("Москва", 55.75, 37.62, payload)

        cache = weather_app.load_cache()
        self.assertIsNotNone(cache)
        assert cache is not None
        self.assertEqual(cache["city"], "Москва")
        self.assertEqual(cache["lat"], 55.75)
        self.assertEqual(cache["lon"], 37.62)
        self.assertEqual(cache["weather"], payload)
        self.assertIn("fetched_at", cache)

        age = weather_app.get_cache_age(cache)
        self.assertIsNotNone(age)
        assert age is not None
        self.assertLess(age, weather_app.CACHE_TTL)

        fresh_cache, reason = weather_app.load_fresh_cache()
        self.assertEqual(fresh_cache, cache)
        self.assertEqual(reason, "")

    def test_expired_cache_is_rejected(self) -> None:
        old_time = datetime.now(timezone.utc) - timedelta(hours=4)
        cache = {
            "city": "Москва",
            "lat": 55.75,
            "lon": 37.62,
            "fetched_at": old_time.isoformat(),
            "weather": self.weather_payload(),
        }
        self.cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        fresh_cache, reason = weather_app.load_fresh_cache()
        self.assertIsNone(fresh_cache)
        self.assertIn("устарел", reason)

    def test_corrupted_cache_is_ignored(self) -> None:
        self.cache_path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(weather_app.load_cache())

        fresh_cache, reason = weather_app.load_fresh_cache()
        self.assertIsNone(fresh_cache)
        self.assertIn("отсутствует или повреждён", reason)

    def test_non_utf8_cache_is_ignored(self) -> None:
        self.cache_path.write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(weather_app.load_cache())

        fresh_cache, reason = weather_app.load_fresh_cache()
        self.assertIsNone(fresh_cache)
        self.assertIn("отсутствует или повреждён", reason)


if __name__ == "__main__":
    unittest.main()
