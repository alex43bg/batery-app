"""Battery percentage and time-estimate calculations used by the monitor.

Run this file directly to print a sample discharge calculation:
    python battery_calculations.py
"""

from datetime import datetime, timedelta
from math import ceil, floor, isfinite
from typing import Optional

MODES = {"charging", "discharging", "idle"}


def calculate_percent(
    percent: float,
    mode: str,
    elapsed_hours: float,
    charge_hours: float,
    load_rate: float,
) -> float:
    """Project battery percentage using the monitor's charge/discharge rates."""
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    if not all(isfinite(value) for value in (percent, elapsed_hours, charge_hours, load_rate)):
        raise ValueError("Calculation values must be finite numbers.")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100.")
    if elapsed_hours < 0:
        raise ValueError("elapsed_hours cannot be negative.")
    if charge_hours <= 0:
        raise ValueError("charge_hours must be greater than zero.")
    if not 0 <= load_rate <= 100:
        raise ValueError("load_rate must be between 0 and 100.")

    charge_rate = 100 / charge_hours
    net_rate = (
        charge_rate - load_rate
        if mode == "charging"
        else -load_rate if mode == "discharging" else 0
    )
    return min(100, max(0, percent + net_rate * elapsed_hours))


def remaining_work_hours(
    percent: float,
    mode: str,
    charge_hours: float,
    load_rate: float,
) -> Optional[float]:
    """Return remaining hours in charging/discharging modes, or None if unknown."""
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    if not all(isfinite(value) for value in (percent, charge_hours, load_rate)):
        raise ValueError("Calculation values must be finite numbers.")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100.")
    if charge_hours <= 0:
        raise ValueError("charge_hours must be greater than zero.")
    if not 0 <= load_rate <= 100:
        raise ValueError("load_rate must be between 0 and 100.")

    if mode == "charging":
        net_rate = 100 / charge_hours - load_rate
        return (100 - percent) / net_rate if net_rate > 0 else None
    if mode == "discharging":
        return percent / load_rate if load_rate > 0 else None
    return None


def estimate_level_time(
    current_percent: float,
    target_percent: float,
    load_rate: float,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Estimate when a level is reached, assuming the battery is discharging.

    A target above the current level produces a timestamp in the past, matching
    the monitor's "Було" estimate for the last time the battery was at that level.
    """
    if not all(isfinite(value) for value in (current_percent, target_percent, load_rate)):
        raise ValueError("Calculation values must be finite numbers.")
    if not 0 <= current_percent <= 100 or not 0 <= target_percent <= 100:
        raise ValueError("Battery percentages must be between 0 and 100.")
    if load_rate < 0:
        raise ValueError("load_rate cannot be negative.")
    if load_rate == 0:
        return None
    reference_time = now or datetime.now().astimezone()
    return reference_time + timedelta(hours=(current_percent - target_percent) / load_rate)


def format_duration(hours: Optional[float]) -> str:
    """Format hours as the monitor does: rounded up to the next minute."""
    if hours is None or not isfinite(hours) or hours < 0:
        return "—"
    total_minutes = ceil(hours * 60)
    whole_hours = floor(total_minutes / 60)
    minutes = total_minutes % 60
    if whole_hours == 0:
        return f"{minutes} хв"
    if minutes == 0:
        return f"{whole_hours} год"
    return f"{whole_hours} год {minutes} хв"


def _print_sample() -> None:
    percent = calculate_percent(
        percent=40,
        mode="discharging",
        elapsed_hours=1.5,
        charge_hours=2.5,
        load_rate=4,
    )
    runtime = remaining_work_hours(percent, "discharging", 2.5, 4)
    now = datetime.now().astimezone()

    print(f"Заряд після 1 год 30 хв розряду: {percent:.1f}%")
    print(f"Орієнтовний залишок роботи: {format_duration(runtime)}")
    print("Прогноз рівнів при споживанні 4%/год:")
    for target in range(100, 0, -10):
        reached_at = estimate_level_time(percent, target, 4, now)
        time_text = reached_at.strftime("%d.%m.%Y %H:%M") if reached_at else "Невідомо"
        print(f"{target}% - {time_text}")


if __name__ == "__main__":
    _print_sample()
