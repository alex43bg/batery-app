import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.state_store import JsonStateStore, StateUpdate


class JsonStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.store = JsonStateStore(
            state_file=self.state_file,
            webhook_url="",
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
        self.store.webhook_url = "https://home-assistant.example/api/webhook/test"
        update = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        with patch.object(self.store, "_send_empty_webhook") as send_webhook:
            asyncio.run(self.store.update_state(update))
            asyncio.run(self.store.check_alert())
            asyncio.run(self.store.check_alert())

        state = json.loads(self.state_file.read_text())
        self.assertEqual(send_webhook.call_count, 1)
        self.assertIsNone(state["pending_alert"])
        self.assertIsNotNone(state["last_alert_sent_at_ms"])
        self.assertEqual(state["mode"], "charging")
        self.assertTrue(state["auto_charge_active"])

    def test_failed_webhook_stays_pending_for_retry(self) -> None:
        self.store.webhook_url = "https://home-assistant.example/api/webhook/test"
        update = StateUpdate(
            percent=29,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
        )
        with (
            patch.object(
                self.store,
                "_send_empty_webhook",
                side_effect=urllib.error.URLError("Home Assistant unavailable"),
            ) as send_webhook,
            patch("app.state_store.time.monotonic", return_value=100),
        ):
            asyncio.run(self.store.update_state(update))
            asyncio.run(self.store.check_alert())
            asyncio.run(self.store.check_alert())

        state = json.loads(self.state_file.read_text())
        self.assertEqual(send_webhook.call_count, 1)
        self.assertIsNotNone(state["pending_alert"])
        self.assertIsNone(state["last_alert_sent_at_ms"])
        self.assertEqual(state["mode"], "discharging")

    def test_threshold_crossing_posts_empty_webhook_and_activates_charging(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        self.store.webhook_url = "https://home-assistant.example/api/webhook/test"

        with patch("app.state_store.urllib.request.urlopen", return_value=response) as send:
            state = asyncio.run(
                self.store.update_state(
                    StateUpdate(
                        percent=29,
                        mode="discharging",
                        capacity=192,
                        charge_hours=2.5,
                        load_rate=4,
                        charge_watts=97,
                        alert_threshold_percent=30,
                    )
                )
            )

        request = send.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertIsNone(request.data)
        self.assertEqual(state.mode, "charging")
        self.assertTrue(state.auto_charge_active)
        self.assertIsNone(state.pending_alert)

        stale_update = StateUpdate(
            percent=28,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
            alert_threshold_percent=30,
        )
        state = asyncio.run(self.store.update_state(stale_update))
        self.assertEqual(state.mode, "charging")

        manual_stop = StateUpdate(
            percent=28,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
            alert_threshold_percent=30,
            manual_mode_change=True,
        )
        state = asyncio.run(self.store.update_state(manual_stop))
        self.assertEqual(state.mode, "discharging")
        self.assertFalse(state.auto_charge_active)

    def test_manual_test_webhook_sends_current_battery_level(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        self.store.webhook_url = "https://home-assistant.example/api/webhook/test"

        with patch("app.state_store.urllib.request.urlopen", return_value=response) as send:
            asyncio.run(self.store.send_test_webhook())

        send.assert_called_once()
        request = send.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Content-type"))
        self.assertEqual(request.get_header("User-agent"), "BatteryMonitor/1.0")

    def test_custom_threshold_is_persisted_and_used_to_activate_charging(self) -> None:
        update = StateUpdate(
            percent=42,
            mode="discharging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
            alert_threshold_percent=45,
        )

        state = asyncio.run(self.store.update_state(update))

        self.assertEqual(state.alert_threshold_percent, 45)
        self.assertEqual(state.mode, "discharging")
        self.assertIsNotNone(state.pending_alert)
        restored = JsonStateStore(
            state_file=self.state_file,
            webhook_url="",
            alert_threshold_percent=30,
        )
        self.assertEqual(restored.alert_threshold_percent, 45)
