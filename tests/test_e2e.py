import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs  # noqa: F401  (debe importarse antes que la integración)
import asyncio, json, math, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from custom_components.weathernext import api as bq
from custom_components.weathernext import coordinator as coord_mod
from custom_components.weathernext.coordinator import WeatherNextCoordinator
from custom_components.weathernext.weather import WeatherNextWeather
from custom_components.weathernext.sensor import SENSORS
from homeassistant.helpers.update_coordinator import UpdateFailed

fails = []
def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()

COLUMNS = ["init_time", "valid_time", "lead_hours", "temp_mean", "temp_p10",
           "temp_p90", "dewpoint_mean", "wind_mean", "wind_p90", "wind_u",
           "wind_v", "precip_mean", "precip_p10", "precip_p25", "precip_p50",
           "precip_p75", "precip_p90", "cloud_mean", "pressure_mean", "ssrd_mean"]

state = {"empty": False, "horizon": None}

def build_rows():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    init = now - timedelta(hours=2)
    rows = []
    for lead in range(1, 241):
        valid = init + timedelta(hours=lead)
        hour_of_day = valid.hour
        day = lead / 24
        temp = 288.15 + 6 * math.sin((hour_of_day - 6) / 24 * 2 * math.pi)
        precip = 0.002 if 40 < lead < 60 else 0.0
        ssrd = 1.5e6 if 7 <= hour_of_day <= 19 else 0.0
        rows.append({"f": [{"v": v} for v in [
            str(init.timestamp()), str(valid.timestamp()), str(lead),
            f"{temp}", f"{temp-2}", f"{temp+2}", f"{temp-5}",
            "4.5", "8.0", "-3.0", "-3.0",
            f"{precip}", "0.0", "0.0", f"{precip/2}", f"{precip}", f"{precip*2}",
            "0.35", "101300", f"{ssrd}"]]})
    return rows

async def token_handler(request):
    return web.json_response({"access_token": "tok", "expires_in": 3600})

async def query_handler(request):
    body = await request.json()
    params = {p["name"]: p["parameterValue"]["value"] for p in body["queryParameters"]}
    state["horizon"] = params.get("horizon")
    check("lat/lon pasan como parametros", params["lat"] == "40.4168" and params["lon"] == "-3.7038")
    rows = [] if state["empty"] else build_rows()
    return web.json_response({
        "jobComplete": True,
        "schema": {"fields": [{"name": c} for c in COLUMNS]},
        "rows": rows, "totalBytesProcessed": "5242880", "cacheHit": False})

class FakeEntry:
    entry_id = "abc123"
    title = "Madrid"
    options = {}
    def __init__(self, data):
        self.data = data

async def main():
    app = web.Application()
    app.router.add_post("/token", token_handler)
    app.router.add_post("/bq/projects/{p}/queries", query_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"
    bq.BQ_ROOT = f"{base}/bq"

    async with aiohttp.ClientSession() as session:
        ha_stubs.ac.async_get_clientsession = lambda hass: session
        coord_mod.async_get_clientsession = lambda hass: session

        entry = FakeEntry({
            "name": "Madrid", "latitude": 40.4168, "longitude": -3.7038,
            "project_id": "mi-proyecto-123", "dataset_id": "weathernext",
            "table_id": "weathernext_3_0_0_0p1deg", "bq_location": "US",
            "service_account_json": json.dumps({
                "client_email": "bot@p.iam.gserviceaccount.com",
                "private_key": PEM, "token_uri": f"{base}/token"}),
        })

        coordinator = WeatherNextCoordinator(None, entry)
        check("tabla compuesta", coordinator.table
              == "mi-proyecto-123.weathernext.weathernext_3_0_0_0p1deg")
        data = await coordinator._async_update_data()
        check("horizonte por defecto 240 h", state["horizon"] == "240")
        check("serie horaria completa", len(data.hours) == 240)
        check("init_time detectado", data.init_time is not None)
        check("bytes contabilizados", coordinator.total_bytes_processed == 5242880)

        coordinator.data = data
        entity = WeatherNextWeather(coordinator, entry)
        check("temperatura actual", entity.native_temperature is not None)
        check("presion en hPa", abs(entity.native_pressure - 1013.0) < 0.01)
        check("viento m/s", entity.native_wind_speed == 4.5)
        check("racha p90", entity.native_wind_gust_speed == 8.0)
        check("nubosidad %", entity.cloud_coverage == 35.0)
        check("rumbo noreste (u,v negativos -> viento del NE)", 44 < entity.wind_bearing < 46)
        check("condicion valida", entity.condition == "partlycloudy")
        check("humedad", 65 < entity.humidity < 80)
        check("atributos", entity.extra_state_attributes["forecast_hours"] == 240)
        check("disponible", entity.available)

        hourly = await entity.async_forecast_hourly()
        check("horaria solo futuro", len(hourly) > 200
              and all("T" in h["datetime"] for h in hourly))
        check("horaria con probabilidad",
              any(h["precipitation_probability"] for h in hourly))

        daily = await entity.async_forecast_daily()
        check("diaria ~10 dias", 9 <= len(daily) <= 11)
        check("diaria coherente",
              all(d["native_templow"] <= d["native_temperature"] for d in daily))
        check("dia lluvioso detectado",
              any(d["condition"] in ("rainy", "pouring") for d in daily))
        check("amplitud termica realista",
              all(0 < d["native_temperature"] - d["native_templow"] < 20 for d in daily))

        for description in SENSORS:
            value = description.value_fn(coordinator)
            check(f"sensor {description.key}", value is not None)

        state["empty"] = True
        try:
            await coordinator._async_update_data()
            check("sin datos -> UpdateFailed", False)
        except UpdateFailed:
            check("sin datos -> UpdateFailed", True)

    await runner.cleanup()
    print()
    print("FALLOS:", fails if fails else "ninguno")
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
