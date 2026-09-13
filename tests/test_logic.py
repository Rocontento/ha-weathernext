import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs  # noqa: F401  (debe importarse antes que la integración)
import sys, json, math
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from custom_components.weathernext import meteo
from custom_components.weathernext.coordinator import (
    parse_rows, build_table_id, QUERY_TEMPLATE,
)
from custom_components.weathernext import weather as wx
from custom_components.weathernext.api import (
    _decode_rows, named_param, parse_service_account, BigQueryClient, BigQueryAuthError,
)
from custom_components.weathernext import config_flow, sensor  # solo imports

fails = []
def check(label, cond):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        fails.append(label)

print("meteo")
check("kelvin", abs(meteo.kelvin_to_celsius(300.15) - 27.0) < 1e-9)
check("mm", meteo.metres_to_mm(0.0025) == 2.5)
check("hpa", meteo.pascal_to_hpa(101325) == 1013.25)
check("hr identica -> 100%", abs(meteo.relative_humidity(20, 20) - 100) < 1e-6)
check("hr 20/10 ~ 52%", 51 < meteo.relative_humidity(20, 10) < 53)
check("bearing norte", meteo.wind_bearing(0, -5) == 0.0)
check("bearing este", abs(meteo.wind_bearing(-5, 0) - 90.0) < 1e-9)
check("bearing calma", meteo.wind_bearing(0, 0) is None)
check("prob seco", meteo.precipitation_probability([0, 0, 0, 0, 0]) == 0)
check("prob 45%", meteo.precipitation_probability([0, 0, 0, 0.5, 2.0]) == 45)
check("prob alta", meteo.precipitation_probability([1, 2, 3, 4, 5]) == 95)
check("prob baja", meteo.precipitation_probability([0, 0, 0, 0, 0.05]) == 5)
check("prob incompleta", meteo.precipitation_probability([0, None, 0, 0, 1]) is None)
check("monotonia forzada", meteo.precipitation_probability([0, 0, 0.4, 0.2, 3.0]) is not None)

print("build_table_id")
check("valido", build_table_id("mi-proyecto-123", "weathernext", "weathernext_3_0_0_0p1deg")
      == "mi-proyecto-123.weathernext.weathernext_3_0_0_0p1deg")
for bad, part in [("mi proyecto", "project"), ("ab", "project")]:
    try:
        build_table_id(bad, "d", "t"); check(f"rechaza {bad!r}", False)
    except ValueError as e:
        check(f"rechaza {bad!r}", str(e) == "invalid_project")
try:
    build_table_id("proyecto-ok", "data set", "t"); check("rechaza dataset", False)
except ValueError as e:
    check("rechaza dataset", str(e) == "invalid_dataset")
try:
    build_table_id("proyecto-ok", "ds", "tabla; DROP"); check("rechaza inyeccion", False)
except ValueError as e:
    check("rechaza inyeccion", str(e) == "invalid_table")

print("api")
rows = _decode_rows([{"f": [{"v": "1"}, {"v": None}]}], ["a", "b"])
check("decode", rows == [{"a": "1", "b": None}])
check("decode fila corta", _decode_rows([{"f": [{"v": "1"}]}], ["a", "b"]) == [{"a": "1", "b": None}])
check("param", named_param("lat", "FLOAT64", 40.4)["parameterValue"]["value"] == "40.4")

# Cuenta de servicio real generada al vuelo, para probar la firma del JWT.
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
import base64
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
sa = {"client_email": "bot@proj.iam.gserviceaccount.com", "private_key": pem,
      "token_uri": "https://oauth2.googleapis.com/token"}
check("sa valida", parse_service_account(json.dumps(sa))["client_email"] == sa["client_email"])
escaped = json.dumps({**sa, "private_key": pem.replace("\n", "\\n")})
check("sa con \\n escapados", parse_service_account(escaped) is not None)
try:
    parse_service_account("{}"); check("sa incompleta", False)
except BigQueryAuthError:
    check("sa incompleta", True)
try:
    parse_service_account("no json"); check("sa no json", False)
except BigQueryAuthError:
    check("sa no json", True)

client = BigQueryClient(None, parse_service_account(json.dumps(sa)), "proj", "US")
jwt = client._sign_assertion()
head_b64, claims_b64, sig_b64 = jwt.split(".")
pad = lambda s: s + "=" * (-len(s) % 4)
claims = json.loads(base64.urlsafe_b64decode(pad(claims_b64)))
check("jwt claims", claims["iss"] == sa["client_email"]
      and claims["scope"] == "https://www.googleapis.com/auth/bigquery"
      and claims["exp"] - claims["iat"] == 3600)
key.public_key().verify(base64.urlsafe_b64decode(pad(sig_b64)),
                        f"{head_b64}.{claims_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
check("firma jwt verificada", True)

print("parse_rows")
BASE = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)
def row(init, valid, **kw):
    data = {
        "init_time": str(init.timestamp()), "valid_time": str(valid.timestamp()),
        "lead_hours": str(int((valid - init).total_seconds() // 3600)),
        "temp_mean": "288.15", "temp_p10": "286.15", "temp_p90": "290.15",
        "dewpoint_mean": "283.15", "wind_mean": "5.0", "wind_p90": "9.0",
        "wind_u": "0", "wind_v": "-5", "precip_mean": "0.0",
        "precip_p10": "0", "precip_p25": "0", "precip_p50": "0",
        "precip_p75": "0", "precip_p90": "0", "cloud_mean": "0.1",
        "pressure_mean": "101325", "ssrd_mean": "1000000",
    }
    data.update({k: (None if v is None else str(v)) for k, v in kw.items()})
    return data

old_init = BASE
new_init = BASE + timedelta(hours=6)
raw = [row(old_init, old_init + timedelta(hours=h), temp_mean=280.15) for h in range(1, 13)]
raw += [row(new_init, new_init + timedelta(hours=h), temp_mean=300.15) for h in range(1, 7)]
hours = parse_rows(raw)
check("orden temporal", [h.time for h in hours] == sorted(h.time for h in hours))
check("sin duplicados", len({h.time for h in hours}) == len(hours))
overlap = [h for h in hours if h.time > new_init]
check("gana la pasada mas reciente", all(abs(h.temperature - 27.0) < 1e-6 for h in overlap))
tail = [h for h in hours if h.time <= new_init]
check("cola de la pasada larga", all(abs(h.temperature - 7.0) < 1e-6 for h in tail))
h0 = hours[0]
check("conversiones", abs(h0.pressure - 1013.25) < 1e-9 and h0.cloud_coverage == 10.0
      and h0.wind_bearing == 0.0 and h0.precipitation == 0.0)
check("humedad saturada (rocio > temp)", h0.humidity == 100.0)
h_ok = parse_rows([row(BASE, BASE + timedelta(hours=1), temp_mean=288.15, dewpoint_mean=283.15)])[0]
check("humedad 15/10 ~ 72%", 71 < h_ok.humidity < 74)
check("diurno por radiacion", h0.is_daytime is True)
check("nocturno sin radiacion", parse_rows([row(BASE, BASE + timedelta(hours=1), ssrd_mean=0)])[0].is_daytime is False)
check("filas corruptas ignoradas", parse_rows([{"valid_time": None, "init_time": None}]) == [])
check("nulos tolerados", parse_rows([row(BASE, BASE + timedelta(hours=1), temp_mean=None,
                                         precip_p50=None)])[0].temperature is None)

print("condiciones")
mk = lambda **kw: type("H", (), {"precipitation": 0.0, "cloud_coverage": 0.0,
                                 "temperature": 15.0, "is_daytime": True, **kw})()
check("soleado", wx.hourly_condition(mk()) == "sunny")
check("noche despejada", wx.hourly_condition(mk(is_daytime=False)) == "clear-night")
check("parcial", wx.hourly_condition(mk(cloud_coverage=40)) == "partlycloudy")
check("nublado", wx.hourly_condition(mk(cloud_coverage=90)) == "cloudy")
check("lluvia", wx.hourly_condition(mk(precipitation=1.0, cloud_coverage=90)) == "rainy")
check("diluvio", wx.hourly_condition(mk(precipitation=5.0)) == "pouring")
check("nieve", wx.hourly_condition(mk(precipitation=1.0, temperature=-2)) == "snowy")
check("aguanieve", wx.hourly_condition(mk(precipitation=1.0, temperature=1.5)) == "snowy-rainy")
check("diaria acumulado", wx.daily_condition(2.0, 90, 10) == "rainy")
check("diaria seca", wx.daily_condition(0.5, 10, 10) == "sunny")

print("agregado diario")
now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
raw = []
init = now - timedelta(hours=1)
for h in range(0, 96):
    valid = now + timedelta(hours=h)
    temp = 288.15 + 5 * math.sin(h / 24 * 2 * math.pi)
    raw.append(row(init, valid, temp_mean=temp, precip_mean=0.001,
                   precip_p50=0.0005, precip_p75=0.002, precip_p90=0.004,
                   wind_mean=3 + (h % 5)))
daily = wx._daily_forecast(parse_rows(raw))
check("dias generados", 3 <= len(daily) <= 5)
first = daily[0]
check("max >= min", first["native_temperature"] >= first["native_templow"])
check("precip acumulada", first["native_precipitation"] > 0)
check("prob presente", 0 <= first["precipitation_probability"] <= 100)
check("viento max", first["native_wind_speed"] >= 3)
check("fecha local a medianoche", first["datetime"].endswith(("+02:00", "+01:00"))
      and "T00:00:00" in first["datetime"])
check("dias ordenados", [d["datetime"] for d in daily] == sorted(d["datetime"] for d in daily))
check("sin dias truncados al final",
      all(v is not None for d in daily for v in (d["native_temperature"], d["native_templow"])))

print("consulta SQL")
sql = QUERY_TEMPLATE.format(table="p.d.t")
check("parametros presentes", all(p in sql for p in ("@lat", "@lon", "@lookback", "@horizon")))
check("usa clustering espacial", "ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(@lon, @lat))" in sql)
check("poda de particiones", "t.init_time >= TIMESTAMP_SUB" in sql)

print()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
