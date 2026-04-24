import pytest

from src.app.services.password_service import PasswordService


def test_hash_password_and_verify_round_trip() -> None:
    password = "StrongerPass123!"

    password_hash = PasswordService.hash_password(password)

    assert password_hash != password
    assert PasswordService.verify_password(password, password_hash) is True


def test_verify_password_returns_false_for_wrong_password() -> None:
    password_hash = PasswordService.hash_password("ValidPass123!")

    assert PasswordService.verify_password("wrong-password", password_hash) is False


def test_generate_and_verify_one_time_token() -> None:
    token = PasswordService.generate_one_time_token()
    token_hash = PasswordService.hash_token(token)

    assert token
    assert PasswordService.verify_token(token, token_hash) is True
    assert PasswordService.verify_token("wrong-token", token_hash) is False


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "        ",
        "password123",
    ],
)
def test_validate_password_strength_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValueError):
        PasswordService.validate_password_strength(password)


def test_validate_password_strength_accepts_reasonable_password() -> None:
    PasswordService.validate_password_strength("ReasonablePass123!")
