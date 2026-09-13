"""Constantes de la integración WeatherNext."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "weathernext"

ATTRIBUTION: Final = "Datos de previsión: WeatherNext 3 (Google DeepMind)"
MANUFACTURER: Final = "Google DeepMind"
MODEL: Final = "WeatherNext 3"

CONF_PROJECT_ID: Final = "project_id"
CONF_DATASET_ID: Final = "dataset_id"
CONF_TABLE_ID: Final = "table_id"
CONF_SERVICE_ACCOUNT: Final = "service_account_json"
CONF_BQ_LOCATION: Final = "bq_location"
CONF_LOOKBACK_HOURS: Final = "lookback_hours"
CONF_HORIZON_HOURS: Final = "horizon_hours"
CONF_UPDATE_INTERVAL: Final = "update_interval_minutes"

DEFAULT_TABLE_ID: Final = "weathernext_3_0_0_0p1deg"
DEFAULT_BQ_LOCATION: Final = "US"
DEFAULT_LOOKBACK_HOURS: Final = 18
DEFAULT_HORIZON_HOURS: Final = 240
DEFAULT_UPDATE_INTERVAL: Final = 60

MAX_HORIZON_HOURS: Final = 360

# Umbral (mm/h) a partir del cual se considera que "llueve" al calcular la
# probabilidad de precipitación a partir de los percentiles del ensemble.
PRECIP_THRESHOLD_MM: Final = 0.1
