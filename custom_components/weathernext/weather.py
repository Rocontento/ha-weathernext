"""Entidad `weather` alimentada con la previsión de WeatherNext 3."""

from __future__ import annotations

from datetime import date

from homeassistant.components.weather import (
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER, MODEL
from .coordinator import ForecastHour, WeatherNextCoordinator

# Umbrales de precipitación (mm) para elegir icono. Los horarios son tasas por
# hora; los diarios, acumulados del día.
HOURLY_RAIN_MM = 0.2
HOURLY_POURING_MM = 2.5
DAILY_RAIN_MM = 1.0
DAILY_POURING_MM = 10.0

SNOW_MAX_C = 0.5
SLEET_MAX_C = 2.0

CLEAR_CLOUD_PCT = 20.0
PARTLY_CLOUD_PCT = 60.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Da de alta la entidad de tiempo."""
    coordinator: WeatherNextCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WeatherNextWeather(coordinator, entry)])


class WeatherNextWeather(CoordinatorEntity[WeatherNextCoordinator], WeatherEntity):
    """Tiempo actual y previsión horaria/diaria de WeatherNext 3."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_attribution = ATTRIBUTION
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(
        self, coordinator: WeatherNextCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url="https://deepmind.google/science/weathernext/",
        )

    # ------------------------------------------------------------ actualidad

    @property
    def _current(self) -> ForecastHour | None:
        """La hora de previsión que contiene el instante actual."""
        data = self.coordinator.data
        if not data or not data.hours:
            return None
        now = dt_util.utcnow()
        past = [hour for hour in data.hours if hour.time <= now]
        return past[-1] if past else data.hours[0]

    @property
    def available(self) -> bool:
        return super().available and self._current is not None

    @property
    def condition(self) -> str | None:
        current = self._current
        if current is None:
            return None
        return hourly_condition(current)

    @property
    def native_temperature(self) -> float | None:
        return getattr(self._current, "temperature", None)

    @property
    def native_dew_point(self) -> float | None:
        return getattr(self._current, "dewpoint", None)

    @property
    def humidity(self) -> float | None:
        return getattr(self._current, "humidity", None)

    @property
    def native_pressure(self) -> float | None:
        return getattr(self._current, "pressure", None)

    @property
    def native_wind_speed(self) -> float | None:
        return getattr(self._current, "wind_speed", None)

    @property
    def native_wind_gust_speed(self) -> float | None:
        return getattr(self._current, "wind_gust", None)

    @property
    def wind_bearing(self) -> float | None:
        return getattr(self._current, "wind_bearing", None)

    @property
    def cloud_coverage(self) -> float | None:
        return getattr(self._current, "cloud_coverage", None)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "forecast_run": data.init_time.isoformat() if data.init_time else None,
            "forecast_hours": len(data.hours),
            "precipitation_probability": getattr(
                self._current, "precipitation_probability", None
            ),
        }

    # ------------------------------------------------------------ previsión

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        data = self.coordinator.data
        if not data:
            return None
        now = dt_util.utcnow()
        return [
            _hourly_forecast(hour) for hour in data.hours if hour.time >= now
        ]

    async def async_forecast_daily(self) -> list[Forecast] | None:
        data = self.coordinator.data
        if not data:
            return None
        return _daily_forecast(data.hours)


def _hourly_forecast(hour: ForecastHour) -> Forecast:
    return Forecast(
        datetime=hour.time.isoformat(),
        condition=hourly_condition(hour),
        native_temperature=hour.temperature,
        native_dew_point=hour.dewpoint,
        humidity=hour.humidity,
        native_precipitation=hour.precipitation,
        precipitation_probability=hour.precipitation_probability,
        native_wind_speed=hour.wind_speed,
        native_wind_gust_speed=hour.wind_gust,
        wind_bearing=hour.wind_bearing,
        native_pressure=hour.pressure,
        cloud_coverage=hour.cloud_coverage,
    )


def _daily_forecast(hours: list[ForecastHour]) -> list[Forecast]:
    """Agrupa la serie horaria por día local y resume cada uno."""
    buckets: dict[date, list[ForecastHour]] = {}
    for hour in hours:
        buckets.setdefault(dt_util.as_local(hour.time).date(), []).append(hour)

    today = dt_util.as_local(dt_util.utcnow()).date()
    forecast: list[Forecast] = []

    for day in sorted(buckets):
        if day < today:
            continue
        entries = buckets[day]
        # Un día con muy pocas horas (el último del horizonte) daría máximas y
        # mínimas engañosas, así que se descarta.
        if day != today and len(entries) < 12:
            continue

        temperatures = [e.temperature for e in entries if e.temperature is not None]
        precipitations = [e.precipitation for e in entries if e.precipitation is not None]
        probabilities = [
            e.precipitation_probability
            for e in entries
            if e.precipitation_probability is not None
        ]
        winds = [e.wind_speed for e in entries if e.wind_speed is not None]
        gusts = [e.wind_gust for e in entries if e.wind_gust is not None]
        humidities = [e.humidity for e in entries if e.humidity is not None]
        pressures = [e.pressure for e in entries if e.pressure is not None]

        total_precipitation = sum(precipitations) if precipitations else None
        daytime = [e for e in entries if e.is_daytime] or entries
        clouds = [e.cloud_coverage for e in daytime if e.cloud_coverage is not None]

        start_of_day = dt_util.start_of_local_day(day)

        forecast.append(
            Forecast(
                datetime=start_of_day.isoformat(),
                condition=daily_condition(
                    total_precipitation,
                    sum(clouds) / len(clouds) if clouds else None,
                    min(temperatures) if temperatures else None,
                ),
                native_temperature=max(temperatures) if temperatures else None,
                native_templow=min(temperatures) if temperatures else None,
                native_precipitation=total_precipitation,
                precipitation_probability=max(probabilities) if probabilities else None,
                native_wind_speed=max(winds) if winds else None,
                native_wind_gust_speed=max(gusts) if gusts else None,
                wind_bearing=_dominant_bearing(entries),
                humidity=round(sum(humidities) / len(humidities)) if humidities else None,
                native_pressure=sum(pressures) / len(pressures) if pressures else None,
                cloud_coverage=sum(clouds) / len(clouds) if clouds else None,
            )
        )

    return forecast


def _dominant_bearing(entries: list[ForecastHour]) -> float | None:
    """Dirección del viento del día: la de la hora más ventosa."""
    candidates = [
        entry
        for entry in entries
        if entry.wind_bearing is not None and entry.wind_speed is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry.wind_speed).wind_bearing


def hourly_condition(hour: ForecastHour) -> str:
    """Icono para una hora concreta."""
    return _condition(
        hour.precipitation,
        hour.cloud_coverage,
        hour.temperature,
        hour.is_daytime,
        HOURLY_RAIN_MM,
        HOURLY_POURING_MM,
    )


def daily_condition(
    precipitation: float | None, cloud_coverage: float | None, temp_low: float | None
) -> str:
    """Icono resumen de un día completo."""
    return _condition(
        precipitation,
        cloud_coverage,
        temp_low,
        True,
        DAILY_RAIN_MM,
        DAILY_POURING_MM,
    )


def _condition(
    precipitation: float | None,
    cloud_coverage: float | None,
    temperature: float | None,
    is_daytime: bool,
    rain_threshold: float,
    pouring_threshold: float,
) -> str:
    if precipitation is not None and precipitation >= rain_threshold:
        if temperature is not None:
            if temperature <= SNOW_MAX_C:
                return ATTR_CONDITION_SNOWY
            if temperature <= SLEET_MAX_C:
                return ATTR_CONDITION_SNOWY_RAINY
        if precipitation >= pouring_threshold:
            return ATTR_CONDITION_POURING
        return ATTR_CONDITION_RAINY

    if cloud_coverage is None:
        return ATTR_CONDITION_SUNNY if is_daytime else ATTR_CONDITION_CLEAR_NIGHT
    if cloud_coverage < CLEAR_CLOUD_PCT:
        return ATTR_CONDITION_SUNNY if is_daytime else ATTR_CONDITION_CLEAR_NIGHT
    if cloud_coverage < PARTLY_CLOUD_PCT:
        return ATTR_CONDITION_PARTLYCLOUDY
    return ATTR_CONDITION_CLOUDY
