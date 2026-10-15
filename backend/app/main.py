import asyncio
import logging
import os
import secrets
import urllib.error
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .state_store import BatteryState, JsonStateStore, StateUpdate

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
api_token = os.getenv("API_TOKEN", "").strip()
if len(api_token) < 32:
    raise RuntimeError("API_TOKEN must be configured with at least 32 characters.")

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
state_store = JsonStateStore()
if not state_store.webhook_url:
    logger.warning("HA_WEBHOOK_URL is not configured; battery alerts will remain pending.")


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    scheme, separator, provided_token = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not separator
        or not secrets.compare_digest(provided_token, api_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _monitor_battery() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            await state_store.check_alert()
        except (OSError, ValueError) as error:
            logger.exception("Battery monitor could not update the persisted state: %s", error)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    monitor_task = asyncio.create_task(_monitor_battery())
    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Battery Monitor API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state", response_model=BatteryState, dependencies=[Depends(require_api_token)])
async def get_state() -> BatteryState:
    return await state_store.get_state()


@app.put(
    "/api/state",
    response_model=BatteryState,
    dependencies=[Depends(require_api_token)],
)
async def update_state(update: StateUpdate) -> BatteryState:
    return await state_store.update_state(update)


@app.post(
    "/api/webhook/test",
    dependencies=[Depends(require_api_token)],
)
async def send_test_webhook() -> dict[str, str]:
    if not state_store.webhook_url:
        raise HTTPException(
            status_code=503,
            detail="HA_WEBHOOK_URL is not configured.",
        )
    try:
        await state_store.send_test_webhook()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.error("Home Assistant test webhook failed: %s", error)
        raise HTTPException(
            status_code=502,
            detail="Home Assistant rejected or could not receive the test webhook.",
        ) from error
    return {"status": "sent"}
