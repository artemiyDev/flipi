import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataValidationError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramWebAppUser:
    id: int
    username: str | None
    full_name: str | None
    language_code: str | None


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int,
    now: int | None = None,
) -> TelegramWebAppUser:
    pairs = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    if not bot_token or len(fields) != len(pairs):
        raise InitDataValidationError("Invalid init data")

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataValidationError("Invalid init data")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataValidationError("Invalid init data")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as exc:
        raise InitDataValidationError("Invalid init data") from exc
    current_time = int(time.time()) if now is None else now
    if auth_date < current_time - max_age_seconds:
        raise InitDataValidationError("Expired init data")

    try:
        user = json.loads(fields["user"])
        telegram_id = int(user["id"])
        if not isinstance(user, dict):
            raise TypeError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InitDataValidationError("Invalid init data") from exc

    full_name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    )
    return TelegramWebAppUser(
        id=telegram_id,
        username=user.get("username"),
        full_name=full_name or None,
        language_code=user.get("language_code"),
    )
