"""Flujo de configuración de la integración WeatherNext."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    MAX_HORIZON_HOURS,
)
from .coordinator import QUERY_TEMPLATE, build_table_id, parse_rows

_LOGGER = logging.getLogger(__name__)

# Al validar solo se piden unas pocas horas: basta para confirmar credenciales,
# permisos y cobertura del punto sin gastar cuota innecesariamente.
VALIDATION_HORIZON_HOURS = 6


class WeatherNextConfigFlow(ConfigFlow, domain=DOMAIN):
    """Alta de una ubicación."""

    VERSION = 1

    # Mensaje literal de Google del último fallo, para mostrarlo en el formulario.
    _error_detail = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            latitude = user_input[CONF_LATITUDE]
            longitude = user_input[CONF_LONGITUDE]
            await self.async_set_unique_id(f"{latitude:.3f},{longitude:.3f}")
            self._abort_if_unique_id_configured()

            errors = await self._async_validate(user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(user_input),
            errors=errors,
            description_placeholders={"error_detail": self._error_detail},
        )

    def _schema(self, user_input: dict[str, Any] | None) -> vol.Schema:
        current = user_input or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_NAME,
                    default=current.get(CONF_NAME, self.hass.config.location_name),
                ): str,
                vol.Required(
                    CONF_LATITUDE,
                    default=current.get(CONF_LATITUDE, self.hass.config.latitude),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-90, max=90, step="any", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_LONGITUDE,
                    default=current.get(CONF_LONGITUDE, self.hass.config.longitude),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-180,
                        max=180,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PROJECT_ID, default=current.get(CONF_PROJECT_ID, "")
                ): str,
                vol.Required(
                    CONF_DATASET_ID, default=current.get(CONF_DATASET_ID, "")
                ): str,
                vol.Required(
                    CONF_TABLE_ID, default=current.get(CONF_TABLE_ID, DEFAULT_TABLE_ID)
                ): str,
                vol.Required(
                    CONF_BQ_LOCATION,
                    default=current.get(CONF_BQ_LOCATION, DEFAULT_BQ_LOCATION),
                ): str,
                vol.Required(
                    CONF_SERVICE_ACCOUNT, default=current.get(CONF_SERVICE_ACCOUNT, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Comprueba credenciales, tabla y cobertura del punto."""
        try:
            service_account = parse_service_account(user_input[CONF_SERVICE_ACCOUNT])
        except BigQueryAuthError as err:
            _LOGGER.debug("Cuenta de servicio inválida: %s", err)
            return {CONF_SERVICE_ACCOUNT: "invalid_service_account"}

        try:
            table = build_table_id(
                user_input[CONF_PROJECT_ID],
                user_input[CONF_DATASET_ID],
                user_input[CONF_TABLE_ID],
            )
        except ValueError as err:
            return {"base": str(err)}

        client = BigQueryClient(
            async_get_clientsession(self.hass),
            service_account,
            user_input[CONF_PROJECT_ID],
            user_input[CONF_BQ_LOCATION],
        )

        try:
            rows, stats = await client.query(
                QUERY_TEMPLATE.format(table=table),
                [
                    named_param("lat", "FLOAT64", user_input[CONF_LATITUDE]),
                    named_param("lon", "FLOAT64", user_input[CONF_LONGITUDE]),
                    named_param("lookback", "INT64", DEFAULT_LOOKBACK_HOURS),
                    named_param("horizon", "INT64", VALIDATION_HORIZON_HOURS),
                ],
            )
        except BigQueryError as err:
            if isinstance(err, BigQueryAuthError):
                error = "invalid_auth"
            elif isinstance(err, BigQueryNotFoundError):
                error = "table_not_found"
            else:
                error = "cannot_connect"
            self._error_detail = str(err)
            _LOGGER.warning("Validación de BigQuery fallida (%s): %s", error, err)
            return {"base": error}

        if not parse_rows(rows):
            return {"base": "no_data"}

        _LOGGER.info(
            "WeatherNext validado: %s bytes procesados en la consulta de prueba",
            stats["total_bytes_processed"],
        )
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> WeatherNextOptionsFlow:
        return WeatherNextOptionsFlow()


class WeatherNextOptionsFlow(OptionsFlow):
    """Ajustes de horizonte, ventana de pasadas y frecuencia de consulta."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HORIZON_HOURS,
                        default=options.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=24,
                            max=MAX_HORIZON_HOURS,
                            step=24,
                            unit_of_measurement="h",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=options.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15,
                            max=720,
                            step=15,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_LOOKBACK_HOURS,
                        default=options.get(
                            CONF_LOOKBACK_HOURS, DEFAULT_LOOKBACK_HOURS
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=6,
                            max=48,
                            step=1,
                            unit_of_measurement="h",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )
