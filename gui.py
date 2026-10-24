import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any

import weather_app


class WeatherGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.mode_var = tk.StringVar(value="city")

        self.root.title("Погода OpenWeather")
        self.root.minsize(520, 400)
        self._configure_styles()
        self._build_ui()
        self._update_mode()
        self._poll_events()

        if not weather_app.has_api_key():
            self.fetch_button.configure(state="disabled")
            self._set_status(
                "API_KEY не найден. Добавьте ключ OpenWeather в файл .env "
                "и перезапустите приложение.",
                "Error.TLabel",
            )

    def _configure_styles(self) -> None:
        base_font = tkfont.nametofont("TkDefaultFont").actual()
        family = base_font["family"]
        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=(family, 20, "bold"))
        style.configure("Result.TLabel", font=(family, 16, "bold"))
        style.configure("Error.TLabel", foreground="#b00020")
        style.configure("Status.TLabel", foreground="#555555")
        style.configure("Cache.TLabel", foreground="#8a5a00")

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=20)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        ttk.Label(
            container,
            text="Текущая погода",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        mode_frame = ttk.Frame(container)
        mode_frame.grid(row=1, column=0, sticky="w", pady=(16, 10))
        ttk.Radiobutton(
            mode_frame,
            text="По городу",
            variable=self.mode_var,
            value="city",
            command=self._update_mode,
        ).grid(row=0, column=0, padx=(0, 18))
        ttk.Radiobutton(
            mode_frame,
            text="По координатам",
            variable=self.mode_var,
            value="coordinates",
            command=self._update_mode,
        ).grid(row=0, column=1)

        self.city_frame = ttk.Frame(container)
        self.city_frame.grid(row=2, column=0, sticky="ew")
        self.city_frame.columnconfigure(0, weight=1)
        ttk.Label(self.city_frame, text="Город").grid(row=0, column=0, sticky="w")
        self.city_entry = ttk.Entry(self.city_frame)
        self.city_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self.coords_frame = ttk.Frame(container)
        self.coords_frame.grid(row=2, column=0, sticky="ew")
        self.coords_frame.columnconfigure(0, weight=1)
        ttk.Label(
            self.coords_frame,
            text="Координаты (широта, долгота)",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.coords_entry = ttk.Entry(self.coords_frame)
        self.coords_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.paste_button = ttk.Button(
            self.coords_frame,
            text="Вставить из буфера",
            command=self._paste_coordinates,
        )
        self.paste_button.grid(row=1, column=1, padx=(8, 0), pady=(4, 0))
        ttk.Label(
            self.coords_frame,
            text="Можно вводить: 43.585 39.723 или 43,585°, 39,723°",
            style="Status.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.fetch_button = ttk.Button(
            container,
            text="Узнать погоду",
            command=self._start_request,
        )
        self.fetch_button.grid(row=3, column=0, sticky="w", pady=(18, 14))

        self.result_label = ttk.Label(
            container,
            text="",
            style="Result.TLabel",
            wraplength=460,
            justify="left",
        )
        self.result_label.grid(row=4, column=0, sticky="w", pady=(0, 10))

        self.status_label = ttk.Label(
            container,
            text="",
            style="Status.TLabel",
            wraplength=460,
            justify="left",
        )
        self.status_label.grid(row=5, column=0, sticky="nw")

        self.root.bind("<Return>", lambda _event: self._start_request())
        self.city_entry.focus_set()

    def _update_mode(self) -> None:
        if self.mode_var.get() == "city":
            self.coords_frame.grid_remove()
            self.city_frame.grid()
            self.city_entry.focus_set()
        else:
            self.city_frame.grid_remove()
            self.coords_frame.grid()
            self.coords_entry.focus_set()

    def _set_status(self, text: str, style: str = "Status.TLabel") -> None:
        self.status_label.configure(text=text, style=style)

    def _paste_coordinates(self) -> None:
        try:
            clipboard_text = self.root.clipboard_get().strip()
        except tk.TclError:
            self._set_status(
                "Буфер обмена пуст или недоступен.",
                "Error.TLabel",
            )
            return

        self.coords_entry.delete(0, tk.END)
        self.coords_entry.insert(0, clipboard_text)
        self.coords_entry.focus_set()
        self.coords_entry.icursor(tk.END)

        try:
            lat, lon = weather_app.parse_coordinates(clipboard_text)
        except weather_app.WeatherError:
            self._set_status(
                "Текст вставлен, но координаты не распознаны. Проверьте формат.",
                "Error.TLabel",
            )
            return

        self._set_status(f"Координаты вставлены: {lat:.4f}, {lon:.4f}.")

    def _start_request(self) -> None:
        if self.busy or not weather_app.has_api_key():
            return

        if self.mode_var.get() == "city":
            city = self.city_entry.get().strip()
            if not city:
                self._set_status("Введите название города.", "Error.TLabel")
                self.city_entry.focus_set()
                return
            payload: dict[str, Any] = {"mode": "city", "city": city}
        else:
            raw_coordinates = self.coords_entry.get().strip()
            try:
                lat, lon = weather_app.parse_coordinates(raw_coordinates)
            except weather_app.WeatherError as error:
                self._set_status(str(error), "Error.TLabel")
                self.coords_entry.focus_set()
                return
            payload = {"mode": "coordinates", "lat": lat, "lon": lon}

        self.busy = True
        self.fetch_button.configure(state="disabled")
        self.result_label.configure(text="", style="Result.TLabel")
        self._set_status("Запрашиваю данные OpenWeather...")
        threading.Thread(
            target=self._fetch_worker,
            args=(payload,),
            daemon=True,
        ).start()

    def _fetch_worker(self, payload: dict[str, Any]) -> None:
        try:
            if payload["mode"] == "city":
                city = str(payload["city"])
                lat, lon = weather_app.get_coordinates(city)
                weather = weather_app.get_weather_by_coordinates(lat, lon)
            else:
                lat = float(payload["lat"])
                lon = float(payload["lon"])
                weather = weather_app.get_weather_by_coordinates(lat, lon)
                city = weather_app.city_from_weather(
                    weather, f"координаты {lat:.4f}, {lon:.4f}"
                )

            line = weather_app.format_weather(city, weather)
            received_at = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
            warning = ""
            try:
                weather_app.save_cache(city, lat, lon, weather)
            except weather_app.WeatherError as error:
                warning = str(error)

            self.events.put(
                (
                    "success",
                    {
                        "line": line,
                        "warning": warning,
                        "received_at": received_at,
                    },
                )
            )
        except weather_app.TemporaryWeatherError as error:
            self.events.put(("temporary_error", str(error)))
        except weather_app.WeatherError as error:
            self.events.put(("error", str(error)))
        except Exception:
            self.events.put(
                ("error", "Произошла непредвиденная ошибка при выполнении запроса.")
            )

    def _finish_request(self) -> None:
        self.busy = False
        state = "normal" if weather_app.has_api_key() else "disabled"
        self.fetch_button.configure(state=state)

    def _offer_cache(self, error_message: str) -> None:
        cache, reason = weather_app.load_fresh_cache()
        if cache is None:
            self._set_status(f"{error_message} {reason}", "Error.TLabel")
            return

        try:
            cached_line = weather_app.format_weather(
                cache["city"], cache["weather"]
            )
        except weather_app.WeatherError:
            self._set_status(f"{error_message} Данные в кэше повреждены.", "Error.TLabel")
            return

        fetched_at = weather_app.format_cache_time(cache)
        question = (
            f"{error_message}\n\n"
            f"В кэше есть погода для «{cache['city']}» от {fetched_at}. "
            "Вывести её?"
        )
        if messagebox.askyesno(
            "OpenWeather недоступен",
            question,
            parent=self.root,
        ):
            self.result_label.configure(text=cached_line, style="Result.TLabel")
            self._set_status(
                f"Данные из кэша, сохранены {fetched_at}.",
                "Cache.TLabel",
            )
        else:
            self._set_status(error_message, "Error.TLabel")

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            self._finish_request()
            if kind == "success":
                self.result_label.configure(
                    text=payload["line"], style="Result.TLabel"
                )
                received_at = payload["received_at"]
                if payload["warning"]:
                    self._set_status(
                        f"Данные получены: {received_at}. {payload['warning']}",
                        "Cache.TLabel",
                    )
                else:
                    self._set_status(
                        f"Данные получены: {received_at}.",
                        "Status.TLabel",
                    )
            elif kind == "temporary_error":
                self._offer_cache(payload)
            else:
                self._set_status(payload, "Error.TLabel")

        try:
            self.root.after(100, self._poll_events)
        except tk.TclError:
            pass


def run_gui() -> None:
    root = tk.Tk()
    WeatherGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
