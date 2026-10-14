from math import isfinite
from typing import Literal

Mode = Literal["charging", "discharging", "idle"]


def calculate_percent(
    percent: float,
    mode: Mode,
    elapsed_hours: float,
    charge_hours: float,
    load_rate: float,
) -> float:
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
