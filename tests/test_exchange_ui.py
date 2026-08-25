from app.ui_theme import exchange_picker


def test_exchange_picker_uses_compact_two_column_grid():
    names = ["lbank", "xt", "kucoin", "gateio", "mexc"]
    text, keyboard = exchange_picker(["lbank", "xt"], names)

    assert "Selected: <b>2</b> / 5" in text
    # Five exchanges become three exchange rows, not five single-button rows.
    assert len(keyboard.inline_keyboard) == 5
    assert len(keyboard.inline_keyboard[0]) == 2
    assert len(keyboard.inline_keyboard[1]) == 2
    assert len(keyboard.inline_keyboard[2]) == 1
    assert "✨ Done · 2 selected" == keyboard.inline_keyboard[-2][0].text


def test_exchange_picker_preserves_selection_marks():
    _, keyboard = exchange_picker(["lbank"], ["lbank", "xt"])
    assert keyboard.inline_keyboard[0][0].text.startswith("✅ lbank")
    assert keyboard.inline_keyboard[0][1].text.startswith("▫️ xt")
