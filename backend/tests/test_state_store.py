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
            full_charge_discharge_delay_minutes=15,
        )
        with patch("app.state_store._now_ms", return_value=1_000):
            asyncio.run(self.store.update_state(update))

        restored = JsonStateStore(
            state_file=self.state_file,
            webhook_url="https://home-assistant.example/api/webhook/test",
            alert_threshold_percent=30,
        )

        with patch("app.state_store._now_ms", return_value=901_000):
            restored_state = asyncio.run(restored.get_state())

        self.assertAlmostEqual(restored_state.percent, 64, delta=0.01)
        self.assertEqual(restored_state.updated_at_ms, 901_000)
        self.assertEqual(restored_state.mode, "discharging")
        persisted = json.loads(self.state_file.read_text())
        self.assertEqual(persisted["mode"], "discharging")
        self.assertEqual(persisted["full_charge_discharge_delay_minutes"], 15)

    def test_update_without_delay_keeps_existing_delay_setting(self) -> None:
        asyncio.run(
            self.store.update_state(
                StateUpdate(
                    percent=65,
                    mode="discharging",
                    capacity=192,
                    charge_hours=2.5,
                    load_rate=4,
                    charge_watts=97,
                    full_charge_discharge_delay_minutes=15,
                )
            )
        )
        state = asyncio.run(
            self.store.update_state(
                StateUpdate(
                    percent=64,
                    mode="discharging",
                    capacity=192,
                    charge_hours=2.5,
                    load_rate=4,
                    charge_watts=97,
                )
            )
        )
        self.assertEqual(state.full_charge_discharge_delay_minutes, 15)

    def test_full_charge_switches_to_discharging_after_configured_delay(self) -> None:
        with patch("app.state_store._now_ms", return_value=1_000):
            state = asyncio.run(
                self.store.update_state(
                    StateUpdate(
                        percent=100,
                        mode="charging",
                        capacity=192,
                        charge_hours=2.5,
                        load_rate=4,
                        charge_watts=97,
                        full_charge_discharge_delay_minutes=10,
                    )
                )
            )
        self.assertEqual(state.mode, "charging")
        self.assertEqual(state.full_charge_reached_at_ms, 1_000)

        with patch("app.state_store._now_ms", return_value=600_999):
            state = asyncio.run(self.store.get_state())
        self.assertEqual(state.mode, "charging")

        with patch("app.state_store._now_ms", return_value=601_000):
            state = asyncio.run(self.store.get_state())
        self.assertEqual(state.mode, "discharging")
        self.assertTrue(state.auto_discharge_active)
        self.assertIsNone(state.full_charge_reached_at_ms)

    def test_full_charge_delay_starts_when_battery_reaches_100_percent(self) -> None:
        with patch("app.state_store._now_ms", return_value=1_000):
            state = asyncio.run(
                self.store.update_state(
                    StateUpdate(
                        percent=99,
                        mode="charging",
                        capacity=192,
                        charge_hours=1,
                        load_rate=0,
                        charge_watts=97,
                        full_charge_discharge_delay_minutes=1,
                    )
                )
            )

        with patch("app.state_store._now_ms", return_value=37_000):
            state = asyncio.run(self.store.get_state())
        self.assertEqual(state.mode, "charging")
        self.assertEqual(state.full_charge_reached_at_ms, 37_000)

        with patch("app.state_store._now_ms", return_value=96_999):
            state = asyncio.run(self.store.get_state())
        self.assertEqual(state.mode, "charging")

        with patch("app.state_store._now_ms", return_value=97_000):
            state = asyncio.run(self.store.get_state())
        self.assertEqual(state.mode, "discharging")

    def test_stale_charging_update_does_not_reverse_automatic_discharge(self) -> None:
        with patch("app.state_store._now_ms", return_value=1_000):
            state = asyncio.run(
                self.store.update_state(
                    StateUpdate(
                        percent=100,
                        mode="charging",
                        capacity=192,
                        charge_hours=2.5,
                        load_rate=4,
                        charge_watts=97,
                        full_charge_discharge_delay_minutes=0,
                    )
                )
            )
        self.assertEqual(state.mode, "discharging")
        self.assertTrue(state.auto_discharge_active)
        stale_charging_update = StateUpdate(
            percent=100,
            mode="charging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
            full_charge_discharge_delay_minutes=0,
        )
        with patch("app.state_store._now_ms", return_value=1_000):
            state = asyncio.run(self.store.update_state(stale_charging_update))
        self.assertEqual(state.mode, "discharging")

        manual_charging_override = StateUpdate(
            percent=100,
            mode="charging",
            capacity=192,
            charge_hours=2.5,
            load_rate=4,
            charge_watts=97,
            full_charge_discharge_delay_minutes=10,
            manual_mode_change=True,
        )
        with patch("app.state_store._now_ms", return_value=2_000):
            state = asyncio.run(self.store.update_state(manual_charging_override))
        self.assertEqual(state.mode, "charging")
        self.assertFalse(state.auto_discharge_active)

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
