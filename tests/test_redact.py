import pytest

from triage.redact import redact


@pytest.mark.parametrize(
    "text",
    ["card 4242 4242 4242 4242", "card 4242-4242-4242-4242", "5555555555554444", "cvv: 123", "my account number is 0123456789"],
)
def test_payment_details_are_masked(text):
    cleaned, count = redact(text)

    assert count == 1
    assert not any(secret in cleaned for secret in ("4242", "5555", "123", "0123456789"))


@pytest.mark.parametrize(
    "text",
    ["Invoice 1234 5678 9012 3456 is overdue", "Call me on 07700 900123", "I paid £2,500 on 2026-11-02"],
)
def test_ordinary_numbers_are_left_alone(text):
    assert redact(text) == (text, 0)
