import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

import app.api as api
from app.deps import get_db_session
from app.main import create_app
from bot.models import Event
from bot.services.optimizer import OptimizerUnavailableError

TEST_BOT_TOKEN = "test-bot-token"


def signed_init_data(telegram_id: int) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "optimizer-test",
        "user": json.dumps({"id": telegram_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def build_app(session_factory, monkeypatch):
    import app.deps as deps

    async def override_db_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(bot_token=TEST_BOT_TOKEN, auth_max_age_seconds=86400),
    )
    application = create_app()
    application.dependency_overrides[get_db_session] = override_db_session
    return application


def request(application, method: str, path: str, headers: dict[str, str], payload: dict | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, json=payload)

    return asyncio.run(send())


def test_optimizer_api_paths_detail_fields_and_event(session_factory, monkeypatch) -> None:
    application = build_app(session_factory, monkeypatch)
    owner_headers = {"X-Telegram-Init-Data": signed_init_data(20001)}
    foreign_headers = {"X-Telegram-Init-Data": signed_init_data(20002)}
    deck = request(application, "POST", "/api/decks", owner_headers, {"name": "Optimizer"}).json()
    deck_id = deck["id"]

    assert deck["fsrs_optimized_at"] is None
    assert deck["review_count"] == 0
    insufficient_response = request(application, "POST", f"/api/decks/{deck_id}/optimize", owner_headers, {})
    assert insufficient_response.status_code == 409
    assert insufficient_response.json()["detail"] == "Недостаточно истории (нужно ≥400 повторений, сейчас 0)"
    assert request(application, "POST", f"/api/decks/{deck_id}/optimize", foreign_headers, {}).status_code == 404

    async def unavailable(*_args) -> dict:
        raise OptimizerUnavailableError

    monkeypatch.setattr(api, "optimize_deck", unavailable)
    unavailable_response = request(application, "POST", f"/api/decks/{deck_id}/optimize", owner_headers, {})
    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["detail"] == "Оптимизатор недоступен на сервере"

    optimized_at = datetime.now(UTC)

    async def successful(_session, _user, optimized_deck) -> dict:
        optimized_deck.fsrs_optimized_at = optimized_at
        return {"review_count": 400, "optimized_at": optimized_at}

    monkeypatch.setattr(api, "optimize_deck", successful)
    successful_response = request(application, "POST", f"/api/decks/{deck_id}/optimize", owner_headers, {})
    assert successful_response.status_code == 200
    assert successful_response.json()["review_count"] == 400
    details = request(application, "GET", f"/api/decks/{deck_id}", owner_headers).json()
    assert details["fsrs_optimized_at"] == optimized_at.replace(tzinfo=None).isoformat()
    assert details["review_count"] == 0

    async def events() -> list[Event]:
        async with session_factory() as session:
            return list((await session.scalars(select(Event).where(Event.name == "fsrs_optimized"))).all())

    rows = asyncio.run(events())
    assert len(rows) == 1
    assert rows[0].props == {"deck_id": deck_id, "review_count": 400}
