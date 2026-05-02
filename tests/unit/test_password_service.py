import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from src.app.services.password_service import PasswordService


def test_hash_password_and_verify_round_trip() -> None:
    password = "StrongerPass123!"

    password_hash = PasswordService.hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$argon2id$")
    assert PasswordService.verify_password(password, password_hash) is True
    assert PasswordService.needs_rehash(password_hash) is False


def test_verify_password_returns_false_for_wrong_password() -> None:
    password_hash = PasswordService.hash_password("ValidPass123!")

    assert PasswordService.verify_password("wrong-password", password_hash) is False


def test_verify_password_rejects_invalid_hash() -> None:
    assert PasswordService.verify_password("ValidPass123!", "$2b$not-supported") is False
    assert PasswordService.needs_rehash("$2b$not-supported") is True


def test_needs_rehash_flags_weak_argon2_parameters() -> None:
    weak_hash = PasswordHasher(
        time_cost=1,
        memory_cost=8_192,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    ).hash("ValidPass123!")

    assert PasswordService.verify_password("ValidPass123!", weak_hash) is True
    assert PasswordService.needs_rehash(weak_hash) is True


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
        "alllowercase123!",
        "ALLUPPERCASE123!",
        "NoNumberSymbol!",
        "NoSymbol123",
        "ValidPass123!" * 30,
    ],
)
def test_validate_password_strength_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValueError):
        PasswordService.validate_password_strength(password)


def test_validate_password_strength_accepts_reasonable_password() -> None:
    PasswordService.validate_password_strength("ReasonablePass123!")
