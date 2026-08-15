from bot.keyboards import start_menu


def test_start_menu_includes_web_app_button_when_url_is_configured() -> None:
    menu = start_menu("https://example.test/app")

    button = menu.inline_keyboard[-1][0]

    assert button.text == "Открыть приложение"
    assert button.web_app is not None
    assert button.web_app.url == "https://example.test/app"


def test_start_menu_preserves_existing_menu_without_web_app_url() -> None:
    menu = start_menu("")

    assert all(button.web_app is None for row in menu.inline_keyboard for button in row)
