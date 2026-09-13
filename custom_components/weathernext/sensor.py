"""Sensores auxiliares: estado del modelo y consumo de BigQuery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER, MODEL
from .coordinator import WeatherNextCoordinator


@dataclass(frozen=True, kw_only=True)
class WeatherNextSensorDescription(SensorEntityDescription):
    """Descripción de sensor con la función que extrae su valor."""

    value_fn: Callable[[WeatherNextCoordinator], float | int | datetime | None]


def _next_24h(coordinator: WeatherNextCoordinator):
    data = coordinator.data
    if not data:
        return []
    now = dt_util.utcnow()
    horizon = now + timedelta(hours=24)
    return [hour for hour in data.hours if now <= hour.time <= horizon]


def _precipitation_24h(coordinator: WeatherNextCoordinator) -> float | None:
    values = [
        hour.precipitation
        for hour in _next_24h(coordinator)
        if hour.precipitation is not None
    ]
    return round(sum(values), 2) if values else None


def _precipitation_probability_24h(coordinator: WeatherNextCoordinator) -> int | None:
    values = [
        hour.precipitation_probability
        for hour in _next_24h(coordinator)
        if hour.precipitation_probability is not None
    ]
    return max(values) if values else None


SENSORS: tuple[WeatherNextSensorDescription, ...] = (
    WeatherNextSensorDescription(
        key="precipitation_24h",
        translation_key="precipitation_24h",
        native_unit_of_measurement="mm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:weather-rainy",
        value_fn=_precipitation_24h,
    ),
    WeatherNextSensorDescription(
        key="precipitation_probability_24h",
        translation_key="precipitation_probability_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:weather-pouring",
        value_fn=_precipitation_probability_24h,
    ),
    WeatherNextSensorDescription(
        key="forecast_run",
        translation_key="forecast_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: (
            coordinator.data.init_time if coordinator.data else None
        ),
    ),
    WeatherNextSensorDescription(
        key="last_query_bytes",
        translation_key="last_query_bytes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: (
            coordinator.data.bytes_processed if coordinator.data else None
        ),
    ),
    WeatherNextSensorDescription(
        key="total_query_bytes",
        translation_key="total_query_bytes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.total_bytes_processed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Da de alta los sensores."""
    coordinator: WeatherNextCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WeatherNextSensor(coordinator, entry, description) for description in SENSORS
    )


class WeatherNextSensor(CoordinatorEntity[WeatherNextCoordinator], SensorEntity):
    """Sensor derivado de la previsión ya descargada."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: WeatherNextSensorDescription

    def __init__(
        self,
        coordinator: WeatherNextCoordinator,
        entry: ConfigEntry,
        description: WeatherNextSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def native_value(self) -> float | int | datetime | None:
        return self.entity_description.value_fn(self.coordinator)
