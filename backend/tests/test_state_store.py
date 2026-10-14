import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.state_store import AlertEvent, JsonStateStore, StateUpdate


class JsonStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.store = JsonStateStore(
            state_file=self.state_file,
            webhook_url="https://home-assistant.example/api/webhook/test",
            alert_threshold_percent=30,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_state_survives_store_restart(self) -> None:
        update = StateUpdate(
            percent=65,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        asyncio.run(self.store.update_state(update))

        restored = JsonStateStore(
            state_file=self.state_file,
            webhook_url="https://home-assistant.example/api/webhook/test",
            alert_threshold_percent=30,
        )

        self.assertAlmostEqual(asyncio.run(restored.get_state()).percent, 65, delta=0.01)
        self.assertEqual(json.loads(self.state_file.read_text())["mode"], "discharging")

    def test_crossing_threshold_queues_one_persisted_alert(self) -> None:
        update = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        state = asyncio.run(self.store.update_state(update))

        self.assertIsNotNone(state.pending_alert)
        self.assertFalse(state.alert_armed)
        self.assertEqual(
            json.loads(self.state_file.read_text())["pending_alert"]["percent"],
            29,
        )

    def test_alert_rearms_after_level_is_above_threshold(self) -> None:
        low_state = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        asyncio.run(self.store.update_state(low_state))

        charging_state = StateUpdate(
            percent=40,
            mode="charging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        state = asyncio.run(self.store.update_state(charging_state))

        self.assertTrue(state.alert_armed)

    def test_successful_webhook_clears_pending_alert_once(self) -> None:
        update = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        asyncio.run(self.store.update_state(update))

        with patch.object(self.store, "_send_webhook") as send_webhook:
            asyncio.run(self.store.check_alert())
            asyncio.run(self.store.check_alert())

        state = json.loads(self.state_file.read_text())
        self.assertEqual(send_webhook.call_count, 1)
        self.assertIsNone(state["pending_alert"])
        self.assertIsNotNone(state["last_alert_sent_at_ms"])

    def test_failed_webhook_stays_pending_for_retry(self) -> None:
        update = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        asyncio.run(self.store.update_state(update))

        with patch.object(
            self.store,
            "_send_webhook",
            side_effect=urllib.error.URLError("Home Assistant unavailable"),
        ):
            asyncio.run(self.store.check_alert())

        state = json.loads(self.state_file.read_text())
        self.assertIsNotNone(state["pending_alert"])
        self.assertIsNone(state["last_alert_sent_at_ms"])

    def test_webhook_posts_threshold_event_payload(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        event = AlertEvent(event_id="event-1", percent=29.25, created_at_ms=123456)

        with patch("app.state_store.urllib.request.urlopen", return_value=response) as send:
            self.store._send_webhook(event)

        request = send.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            json.loads(request.data),
            {
                "event": "battery_below_threshold",
                "percent": 29.25,
                "threshold_percent": 30,
                "created_at_ms": 123456,
            },
        )
