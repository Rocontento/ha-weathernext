"""Coordinador que descarga la previsión de WeatherNext desde BigQuery."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BigQueryAuthError,
    BigQueryClient,
    BigQueryError,
    BigQueryNotFoundError,
    named_param,
    parse_service_account,
)
from .const import (
    CONF_BQ_LOCATION,
    CONF_DATASET_ID,
    CONF_HORIZON_HOURS,
    CONF_LOOKBACK_HOURS,
    CONF_PROJECT_ID,
    CONF_SERVICE_ACCOUNT,
    CONF_TABLE_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_BQ_LOCATION,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_TABLE_ID,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .meteo import (
    kelvin_to_celsius,
    metres_to_mm,
    pascal_to_hpa,
    precipitation_probability,
    relative_humidity,
    wind_bearing,
)

_LOGGER = logging.getLogger(__name__)

_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.:]{4,}$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

QUERY_TEMPLATE = """
SELECT
  t.init_time AS init_time,
  f.time AS valid_time,
  f.hours AS lead_hours,
  f.temperature_2m_mean AS temp_mean,
  f.temperature_2m_p10 AS temp_p10,
  f.temperature_2m_p90 AS temp_p90,
  f.dewpoint_temperature_2m_mean AS dewpoint_mean,
  f.wind_speed_10m_mean AS wind_mean,
  f.wind_speed_10m_p90 AS wind_p90,
  f.u_component_of_wind_10m_mean AS wind_u,
  f.v_component_of_wind_10m_mean AS wind_v,
  f.total_precipitation_1hr_mean AS precip_mean,
  f.total_precipitation_1hr_p10 AS precip_p10,
  f.total_precipitation_1hr_p25 AS precip_p25,
  f.total_precipitation_1hr_p50 AS precip_p50,
  f.total_precipitation_1hr_p75 AS precip_p75,
  f.total_precipitation_1hr_p90 AS precip_p90,
  f.total_cloud_cover_mean AS cloud_mean,
  f.mean_sea_level_pressure_mean AS pressure_mean,
  f.surface_solar_radiation_downwards_1hr_mean AS ssrd_mean
FROM `{table}` AS t, t.forecast AS f
WHERE
  t.init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @lookback HOUR)
  AND ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(@lon, @lat))
  AND f.hours <= @horizon
ORDER BY t.init_time ASC, f.time ASC
"""


@dataclass(slots=True)
class ForecastHour:
    """Una hora de previsión ya convertida a unidades del SI habituales en HA."""

    time: datetime
    lead_hours: int
    init_time: datetime
    temperature: float | None = None
    temperature_p10: float | None = None
    temperature_p90: float | None = None
    dewpoint: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None
    wind_bearing: float | None = None
    precipitation: float | None = None
    precipitation_probability: int | None = None
    cloud_coverage: float | None = None
    pressure: float | None = None
    is_daytime: bool = True


@dataclass(slots=True)
class WeatherNextData:
    """Estado completo devuelto por el coordinador."""

    hours: list[ForecastHour] = field(default_factory=list)
    init_time: datetime | None = None
    bytes_processed: int = 0
    cache_hit: bool = False


class WeatherNextCoordinator(DataUpdateCoordinator[WeatherNextData]):
    """Consulta BigQuery cada X minutos y cachea la serie horaria completa."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        options = {**entry.data, **entry.options}
        self._options = options

        self.latitude: float = float(options[CONF_LATITUDE])
        self.longitude: float = float(options[CONF_LONGITUDE])
        self.table = build_table_id(
            options[CONF_PROJECT_ID],
            options[CONF_DATASET_ID],
            options.get(CONF_TABLE_ID, DEFAULT_TABLE_ID),
        )
        self.total_bytes_processed = 0

        self.client = BigQueryClient(
            async_get_clientsession(hass),
            parse_service_account(options[CONF_SERVICE_ACCOUNT]),
            options[CONF_PROJECT_ID],
            options.get(CONF_BQ_LOCATION, DEFAULT_BQ_LOCATION),
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=int(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
            ),
        )

    async def _async_update_data(self) -> WeatherNextData:
        try:
            rows, stats = await self.client.query(
                QUERY_TEMPLATE.format(table=self.table),
                [
                    named_param("lat", "FLOAT64", self.latitude),
                    named_param("lon", "FLOAT64", self.longitude),
                    named_param(
                        "lookback",
                        "INT64",
                        int(self._options.get(CONF_LOOKBACK_HOURS, DEFAULT_LOOKBACK_HOURS)),
                    ),
                    named_param(
                        "horizon",
                        "INT64",
                        int(self._options.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS)),
                    ),
                ],
            )
        except BigQueryAuthError as err:
            raise UpdateFailed(f"Credenciales rechazadas por BigQuery: {err}") from err
        except BigQueryNotFoundError as err:
            raise UpdateFailed(f"No se encuentra la tabla {self.table}: {err}") from err
        except BigQueryError as err:
            raise UpdateFailed(str(err)) from err

        self.total_bytes_processed += stats["total_bytes_processed"]
        _LOGGER.debug(
            "WeatherNext: %s filas, %s bytes procesados (caché: %s)",
            len(rows),
            stats["total_bytes_processed"],
            stats["cache_hit"],
        )

        hours = parse_rows(rows)
        if not hours:
            raise UpdateFailed(
                "BigQuery no ha devuelto previsión para esas coordenadas. Revisa el "
                "dataset y que la ubicación esté dentro de la malla del modelo."
            )

        return WeatherNextData(
            hours=hours,
            init_time=max(hour.init_time for hour in hours),
            bytes_processed=stats["total_bytes_processed"],
            cache_hit=stats["cache_hit"],
        )


def build_table_id(project_id: str, dataset_id: str, table_id: str) -> str:
    """Compone el identificador de tabla validando cada parte.

    Los identificadores no se pueden parametrizar en SQL, así que se validan
    aquí antes de interpolarlos en la consulta.
    """
    if not _PROJECT_RE.match(project_id):
        raise ValueError("invalid_project")
    if not _DATASET_RE.match(dataset_id):
        raise ValueError("invalid_dataset")
    if not _TABLE_RE.match(table_id):
        raise ValueError("invalid_table")
    return f"{project_id}.{dataset_id}.{table_id}"


def parse_rows(rows: list[dict[str, str | None]]) -> list[ForecastHour]:
    """Convierte las filas crudas en horas de previsión.

    La ventana de consulta abarca varias pasadas del modelo: las inicializadas
    cada hora solo llegan a 48 h y las de cada 6 h llegan a 360 h. Al recorrer
    las filas ordenadas por `init_time` ascendente y quedarnos con la última
    para cada instante, el corto plazo usa la pasada más reciente y el largo
    plazo se completa con la última pasada larga disponible.
    """
    by_time: dict[datetime, ForecastHour] = {}

    for row in rows:
        valid_time = _to_datetime(row.get("valid_time"))
        init_time = _to_datetime(row.get("init_time"))
        if valid_time is None or init_time is None:
            continue

        temperature = kelvin_to_celsius(_to_float(row.get("temp_mean")))
        dewpoint = kelvin_to_celsius(_to_float(row.get("dewpoint_mean")))
        cloud = _to_float(row.get("cloud_mean"))
        ssrd = _to_float(row.get("ssrd_mean"))

        by_time[valid_time] = ForecastHour(
            time=valid_time,
            lead_hours=_to_int(row.get("lead_hours")) or 0,
            init_time=init_time,
            temperature=temperature,
            temperature_p10=kelvin_to_celsius(_to_float(row.get("temp_p10"))),
            temperature_p90=kelvin_to_celsius(_to_float(row.get("temp_p90"))),
            dewpoint=dewpoint,
            humidity=relative_humidity(temperature, dewpoint),
            wind_speed=_to_float(row.get("wind_mean")),
            wind_gust=_to_float(row.get("wind_p90")),
            wind_bearing=wind_bearing(
                _to_float(row.get("wind_u")), _to_float(row.get("wind_v"))
            ),
            precipitation=metres_to_mm(_to_float(row.get("precip_mean"))),
            precipitation_probability=precipitation_probability(
                [
                    metres_to_mm(_to_float(row.get(key)))
                    for key in (
                        "precip_p10",
                        "precip_p25",
                        "precip_p50",
                        "precip_p75",
                        "precip_p90",
                    )
                ]
            ),
            cloud_coverage=None if cloud is None else max(0.0, min(100.0, cloud * 100.0)),
            pressure=pascal_to_hpa(_to_float(row.get("pressure_mean"))),
            # La radiación solar acumulada en la hora es un indicador directo y
            # fiable de si esa hora es diurna, sin necesidad de efemérides.
            is_daytime=bool(ssrd and ssrd > 0),
        )

    return [by_time[key] for key in sorted(by_time)]


def _to_float(value: str | float | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str | float | None) -> int | None:
    parsed = _to_float(value)
    return None if parsed is None else int(parsed)


def _to_datetime(value: str | float | None) -> datetime | None:
    """BigQuery devuelve los TIMESTAMP como segundos epoch en texto."""
    parsed = _to_float(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed, tz=timezone.utc)
