import pytest

from triage.redact import redact, redact_strict


@pytest.mark.parametrize(
    "text",
    [
        "card 4242 4242 4242 4242",
        "card 4242-4242-4242-4242",
        "5555555555554444",
        "cvv: 123",
        "my account number is 31926819",
        "sort code 40-47-84",
    ],
)
def test_payment_details_are_masked(text):
    cleaned, count = redact(text)

    assert count == 1
    assert not any(secret in cleaned for secret in ("4242", "5555", "123", "31926819", "40-47-84"))


@pytest.mark.parametrize(
    "text",
    ["Invoice 1234 5678 9012 3456 is overdue", "Call me on 07700 900123", "I paid £2,500 on 2026-11-02"],
)
def test_ordinary_numbers_are_left_alone(text):
    assert redact(text) == (text, 0)


SHOULD_HIDE = [
    "4242 4242 4242 4242 123",
    "5555555555554444 07/28",
    "paid 15 4242 4242 4242 4242",
    "4242.4242.4242.4242",
    "4242\n4242\n4242\n4242",
    "card4242424242424242",
    "4242424242424242123",
    "my card is 4242 4242 4242 4241",
]


@pytest.mark.parametrize("text", SHOULD_HIDE)
def test_the_strict_guard_hides_cards_the_first_guard_let_through(text):
    assert redact(text)[1] == 0

    cleaned, count = redact_strict(text)

    assert count >= 1
    assert "4242" not in cleaned and "5555" not in cleaned


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("4242 4242 4242 4242 123", "123"),
        ("cvv2 123", "123"),
        ("the three digits on the back are 987", "987"),
        ("expiry 08/28", "08/28"),
    ],
)
def test_the_strict_guard_hides_security_codes_and_expiry_dates(text, secret):
    assert secret not in redact_strict(text)[0]


@pytest.mark.parametrize(
    "text",
    [
        "Invoice 1234 5678 9012 3456 is overdue",
        "ring 07700 900123 or 07700 900124",
        "charged on 2026-11-02 2026-11-03 2026-10-03",
        "VAT number GB 123 4567 89",
    ],
)
def test_the_strict_guard_leaves_ordinary_numbers_alone(text):
    assert redact_strict(text) == (text, 0)
