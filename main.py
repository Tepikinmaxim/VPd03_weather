import argparse

from weather_app import run_cli


def _choose_interface() -> str | None:
    while True:
        print("\nВыберите интерфейс:")
        print("1 — консольный (CLI)")
        print("2 — графический (GUI)")
        print("0 — выход")
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if choice == "0":
            return None
        if choice == "1":
            return "cli"
        if choice == "2":
            return "gui"
        print("Неизвестный пункт меню. Введите 1, 2 или 0.")


def _run_gui() -> bool:
    try:
        from gui import run_gui
    except ImportError:
        print(
            "Графический интерфейс недоступен: не удалось загрузить tkinter. "
            "Запустите приложение с флагом --cli."
        )
        return False

    try:
        run_gui()
    except Exception:
        print(
            "Не удалось открыть графическое окно. "
            "Проверьте доступность Tkinter или запустите приложение с флагом --cli."
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Текущая погода OpenWeather: консольный и графический интерфейсы."
    )
    interface_group = parser.add_mutually_exclusive_group()
    interface_group.add_argument(
        "--cli",
        action="store_true",
        help="запустить консольный интерфейс",
    )
    interface_group.add_argument(
        "--gui",
        action="store_true",
        help="запустить графический интерфейс",
    )
    args = parser.parse_args(argv)

    if args.cli:
        interface = "cli"
    elif args.gui:
        interface = "gui"
    else:
        interface = _choose_interface()

    if interface is None:
        print("До свидания!")
        return 0
    if interface == "cli":
        run_cli()
        return 0
    return 0 if _run_gui() else 1


if __name__ == "__main__":
    raise SystemExit(main())
