import asyncio
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .calculations import calculate_percent

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = Path(os.getenv("STATE_FILE", "/data/state.json"))
ALERT_THRESHOLD_PERCENT = float(os.getenv("ALERT_THRESHOLD_PERCENT", "30"))
HA_WEBHOOK_URL = os.getenv("HA_WEBHOOK_URL", "").strip()
WEBHOOK_RETRY_INITIAL_SECONDS = 30
WEBHOOK_RETRY_MAX_SECONDS = 900
if not 0 <= ALERT_THRESHOLD_PERCENT <= 100:
    raise ValueError("ALERT_THRESHOLD_PERCENT must be between 0 and 100.")


class StateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    percent: float = Field(ge=0, le=100)
    mode: Literal["charging", "discharging", "idle"]
    capacity: float = Field(gt=0, le=10000)
    charge_hours: float = Field(gt=0, le=100)
    load_rate: float = Field(ge=0, le=100)
    charge_watts: float = Field(gt=0, le=10000)
    alert_threshold_percent: float = Field(
        default=ALERT_THRESHOLD_PERCENT,
        ge=0,
        le=100,
    )
    manual_mode_change: bool = Field(default=False, exclude=True)


class AlertEvent(BaseModel):
    event_id: str
    percent: float
    created_at_ms: int


class BatteryState(StateUpdate):
    updated_at_ms: int
    initialized: bool = False
    alert_armed: bool = True
    auto_charge_active: bool = False
    pending_alert: AlertEvent | None = None
    last_alert_sent_at_ms: int | None = None


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _default_state(alert_threshold_percent: float) -> BatteryState:
    return BatteryState(
        percent=100,
        mode="idle",
        capacity=192,
        charge_hours=2.5,
        load_rate=4,
        charge_watts=97,
        alert_threshold_percent=alert_threshold_percent,
        updated_at_ms=_now_ms(),
    )


class JsonStateStore:
    def __init__(
        self,
        state_file: Path = DEFAULT_STATE_FILE,
        webhook_url: str = HA_WEBHOOK_URL,
        alert_threshold_percent: float = ALERT_THRESHOLD_PERCENT,
    ) -> None:
        self.state_file = state_file
        self.webhook_url = webhook_url
        self._lock = asyncio.Lock()
        self._webhook_lock = asyncio.Lock()
        self._webhook_retry_delay_seconds = WEBHOOK_RETRY_INITIAL_SECONDS
        self._next_webhook_attempt_at = 0.0
        self._state = self._load(alert_threshold_percent)
        self.alert_threshold_percent = self._state.alert_threshold_percent

    def _load(self, default_threshold: float) -> BatteryState:
        try:
            content = self.state_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _default_state(default_threshold)
        try:
            return BatteryState.model_validate_json(content)
        except ValueError as error:
            raise RuntimeError(f"Invalid battery state file: {self.state_file}") from error

    def _persist(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_file.parent,
                prefix=f".{self.state_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                temporary_file.write(self._state.model_dump_json(indent=2))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _advance(self, now_ms: int) -> None:
        previous_percent = self._state.percent
        elapsed_hours = max(0, now_ms - self._state.updated_at_ms) / 3_600_000
        self._state.percent = calculate_percent(
            percent=self._state.percent,
            mode=self._state.mode,
            elapsed_hours=elapsed_hours,
            charge_hours=self._state.charge_hours,
            load_rate=self._state.load_rate,
        )
        self._state.updated_at_ms = now_ms

        if self._state.percent > self.alert_threshold_percent:
            self._state.alert_armed = True
        elif (
            self._state.mode == "discharging"
            and previous_percent > self.alert_threshold_percent
            and self._state.alert_armed
            and self._state.pending_alert is None
        ):
            self._queue_alert(now_ms)

    def _queue_alert(self, now_ms: int) -> None:
        self._state.alert_armed = False
        self._state.pending_alert = AlertEvent(
            event_id=str(uuid4()),
            percent=self._state.percent,
            created_at_ms=now_ms,
        )

    async def get_state(self) -> BatteryState:
        async with self._lock:
            self._advance(_now_ms())
            self._persist()
            return self._state.model_copy(deep=True)

    async def update_state(self, update: StateUpdate) -> BatteryState:
        async with self._lock:
            now_ms = _now_ms()
            self._advance(now_ms)
            previous_percent = self._state.percent
            threshold_changed = (
                self.alert_threshold_percent != update.alert_threshold_percent
            )
            update_values = update.model_dump(exclude={"manual_mode_change"})
            if self._state.auto_charge_active and not update.manual_mode_change:
                update_values["mode"] = "charging"
            elif update.manual_mode_change:
                self._state.auto_charge_active = False
            self._state = self._state.model_copy(
                update={**update_values, "initialized": True},
                deep=True,
            )
            self._state.updated_at_ms = now_ms
            self.alert_threshold_percent = update.alert_threshold_percent
            if self._state.percent > self.alert_threshold_percent:
                self._state.alert_armed = True
            elif (
                self._state.mode == "discharging"
                and (
                    previous_percent > self.alert_threshold_percent
                    or threshold_changed
                )
                and self._state.alert_armed
                and self._state.pending_alert is None
            ):
                self._queue_alert(now_ms)
            self._persist()
            should_send_alert = (
                self._state.pending_alert is not None and bool(self.webhook_url)
            )

        if should_send_alert:
            await self.check_alert()

        async with self._lock:
            return self._state.model_copy(deep=True)

    async def check_alert(self) -> None:
        async with self._lock:
            self._advance(_now_ms())
            self._persist()
            pending = self._state.pending_alert

        if pending is None:
            return
        if not self.webhook_url:
            return
        if time.monotonic() < self._next_webhook_attempt_at:
            return

        async with self._webhook_lock:
            if time.monotonic() < self._next_webhook_attempt_at:
                return

            try:
                await asyncio.to_thread(self._send_empty_webhook)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                logger.error("Home Assistant webhook failed; it will be retried: %s", error)
                self._next_webhook_attempt_at = (
                    time.monotonic() + self._webhook_retry_delay_seconds
                )
                self._webhook_retry_delay_seconds = min(
                    self._webhook_retry_delay_seconds * 2,
                    WEBHOOK_RETRY_MAX_SECONDS,
                )
                return
            self._next_webhook_attempt_at = 0.0
            self._webhook_retry_delay_seconds = WEBHOOK_RETRY_INITIAL_SECONDS

            async with self._lock:
                if (
                    self._state.pending_alert is not None
                    and self._state.pending_alert.event_id == pending.event_id
                ):
                    self._state.pending_alert = None
                    self._state.last_alert_sent_at_ms = _now_ms()
                    self._state.mode = "charging"
                    self._state.auto_charge_active = True
                    self._state.updated_at_ms = self._state.last_alert_sent_at_ms
                    self._persist()

    async def send_test_webhook(self) -> None:
        await asyncio.to_thread(self._send_test_webhook)

    def _send_test_webhook(self) -> None:
        self._send_empty_webhook()

    def _send_empty_webhook(self) -> None:
        request = urllib.request.Request(
            self.webhook_url,
            headers={"User-Agent": "BatteryMonitor/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise urllib.error.URLError(f"Unexpected HTTP status: {response.status}")
