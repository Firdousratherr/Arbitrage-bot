from types import SimpleNamespace

import pytest

from arbitrage_terminal.bot.code_repair import _parse_repair_json
from arbitrage_terminal.bot.contact import contact_text


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


@pytest.mark.asyncio
async def test_contact_form_advances_through_name_email_phone_message():
    context = SimpleNamespace(user_data={'contact_stage': 'name', 'contact_form': {}})

    message = FakeMessage('Firdous')
    update = SimpleNamespace(effective_message=message)
    assert await contact_text(update, context) is True
    assert context.user_data['contact_stage'] == 'email'
    assert context.user_data['contact_form']['name'] == 'Firdous'

    message = FakeMessage('user@example.com')
    update.effective_message = message
    assert await contact_text(update, context) is True
    assert context.user_data['contact_stage'] == 'phone'

    message = FakeMessage('+919876543210')
    update.effective_message = message
    assert await contact_text(update, context) is True
    assert context.user_data['contact_stage'] == 'message'

    message = FakeMessage('Please fix the scanner.')
    update.effective_message = message
    assert await contact_text(update, context) is True
    assert context.user_data['contact_stage'] == 'confirm'
    assert context.user_data['contact_form']['message'] == 'Please fix the scanner.'
    assert message.replies


def test_ai_repair_json_parser_accepts_fenced_json():
    assert _parse_repair_json('```json\n{"files": []}\n```') == {'files': []}


def test_ai_repair_json_parser_rejects_malformed_json_without_changing_code():
    with pytest.raises(RuntimeError, match='invalid repair JSON'):
        _parse_repair_json('{"summary":"broken')
