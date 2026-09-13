"""Conversiones meteorológicas y derivadas del ensemble de WeatherNext."""

from __future__ import annotations

import math

from .const import PRECIP_THRESHOLD_MM

# Percentiles disponibles en las tablas de WeatherNext y su probabilidad
# acumulada asociada.
PERCENTILE_LEVELS: tuple[float, ...] = (0.10, 0.25, 0.50, 0.75, 0.90)

KELVIN_OFFSET = 273.15


def kelvin_to_celsius(value: float | None) -> float | None:
    """Kelvin -> grados Celsius."""
    return None if value is None else value - KELVIN_OFFSET


def metres_to_mm(value: float | None) -> float | None:
    """Metros de precipitación acumulada -> milímetros."""
    return None if value is None else value * 1000.0


def pascal_to_hpa(value: float | None) -> float | None:
    """Pascales -> hectopascales."""
    return None if value is None else value / 100.0


def relative_humidity(temp_c: float | None, dewpoint_c: float | None) -> float | None:
    """Humedad relativa (%) a partir de temperatura y punto de rocío (Magnus)."""
    if temp_c is None or dewpoint_c is None:
        return None
    numerator = math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = math.exp((17.625 * temp_c) / (243.04 + temp_c))
    return max(0.0, min(100.0, 100.0 * numerator / denominator))


def wind_bearing(u: float | None, v: float | None) -> float | None:
    """Dirección meteorológica (grados de donde viene el viento) desde u/v."""
    if u is None or v is None:
        return None
    if abs(u) < 1e-6 and abs(v) < 1e-6:
        return None
    return math.degrees(math.atan2(-u, -v)) % 360.0


def precipitation_probability(
    percentiles: list[float | None], threshold_mm: float = PRECIP_THRESHOLD_MM
) -> int | None:
    """Estima P(precipitación > umbral) interpolando la CDF del ensemble.

    `percentiles` son los valores en mm de p10, p25, p50, p75 y p90, en ese
    orden. Al ser una distribución muestreada en solo cinco puntos, el
    resultado se redondea a múltiplos de 5 para no aparentar más precisión de
    la que hay.
    """
    values = [value for value in percentiles if value is not None]
    if len(values) != len(PERCENTILE_LEVELS):
        return None

    # Los percentiles deben ser monótonos; pequeñas inversiones numéricas se
    # corrigen para que la interpolación tenga sentido.
    values = list(_monotonic(values))

    if values[-1] <= threshold_mm:
        # Ni siquiera el p90 supera el umbral.
        return 0 if values[-1] == 0.0 else 5
    if values[0] > threshold_mm:
        # Hasta el p10 llueve: la probabilidad es al menos del 90 %.
        return 95

    for index, value in enumerate(values):
        if value > threshold_mm:
            low_value = values[index - 1]
            low_level = PERCENTILE_LEVELS[index - 1]
            high_level = PERCENTILE_LEVELS[index]
            span = value - low_value
            if span <= 0:
                cdf = high_level
            else:
                cdf = low_level + (threshold_mm - low_value) / span * (
                    high_level - low_level
                )
            return _round_to_5((1.0 - cdf) * 100.0)

    return 0


def _monotonic(values: list[float]) -> list[float]:
    running = values[0]
    result = [running]
    for value in values[1:]:
        running = max(running, value)
        result.append(running)
    return result


def _round_to_5(value: float) -> int:
    return int(max(0.0, min(100.0, round(value / 5.0) * 5.0)))
