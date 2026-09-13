"""Stubs mínimos de Home Assistant para poder probar la lógica sin instalarlo."""
import sys, types, enum
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Madrid")

def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

ha = _mod("homeassistant")
ha.__path__ = []

# --- config_entries
ce = _mod("homeassistant.config_entries")
class ConfigEntry: ...
class ConfigFlow:
    def __init_subclass__(cls, **kw): pass
class ConfigFlowResult(dict): ...
class OptionsFlow: ...
ce.ConfigEntry = ConfigEntry; ce.ConfigFlow = ConfigFlow
ce.ConfigFlowResult = ConfigFlowResult; ce.OptionsFlow = OptionsFlow

# --- const
const = _mod("homeassistant.const")
const.CONF_LATITUDE = "latitude"; const.CONF_LONGITUDE = "longitude"; const.CONF_NAME = "name"
const.PERCENTAGE = "%"
class Platform(str, enum.Enum):
    SENSOR = "sensor"; WEATHER = "weather"
class EntityCategory(str, enum.Enum):
    DIAGNOSTIC = "diagnostic"
class UnitOfInformation(str, enum.Enum):
    BYTES = "B"; MEGABYTES = "MB"; GIGABYTES = "GB"
class UnitOfPrecipitationDepth(str, enum.Enum):
    MILLIMETERS = "mm"
class UnitOfPressure(str, enum.Enum):
    HPA = "hPa"
class UnitOfSpeed(str, enum.Enum):
    METERS_PER_SECOND = "m/s"
class UnitOfTemperature(str, enum.Enum):
    CELSIUS = "°C"
for name, obj in [("Platform", Platform), ("EntityCategory", EntityCategory),
                  ("UnitOfInformation", UnitOfInformation),
                  ("UnitOfPrecipitationDepth", UnitOfPrecipitationDepth),
                  ("UnitOfPressure", UnitOfPressure), ("UnitOfSpeed", UnitOfSpeed),
                  ("UnitOfTemperature", UnitOfTemperature)]:
    setattr(const, name, obj)

# --- core / exceptions
core = _mod("homeassistant.core")
class HomeAssistant: ...
core.HomeAssistant = HomeAssistant
core.callback = lambda fn: fn
exc = _mod("homeassistant.exceptions")
class ConfigEntryAuthFailed(Exception): ...
class ConfigEntryNotReady(Exception): ...
exc.ConfigEntryAuthFailed = ConfigEntryAuthFailed
exc.ConfigEntryNotReady = ConfigEntryNotReady

# --- helpers
helpers = _mod("homeassistant.helpers"); helpers.__path__ = []
ac = _mod("homeassistant.helpers.aiohttp_client")
ac.async_get_clientsession = lambda hass: None

T = TypeVar("T")
uc = _mod("homeassistant.helpers.update_coordinator")
class UpdateFailed(Exception): ...
class DataUpdateCoordinator(Generic[T]):
    def __init__(self, *args, **kwargs):
        self.data = None
class CoordinatorEntity(Generic[T]):
    def __init__(self, coordinator):
        self.coordinator = coordinator
    @property
    def available(self): return True
uc.UpdateFailed = UpdateFailed
uc.DataUpdateCoordinator = DataUpdateCoordinator
uc.CoordinatorEntity = CoordinatorEntity

dr = _mod("homeassistant.helpers.device_registry")
dr.DeviceInfo = dict
ep = _mod("homeassistant.helpers.entity_platform")
ep.AddEntitiesCallback = Any

sel = _mod("homeassistant.helpers.selector")
class _Cfg:
    def __init__(self, **kw): self.kw = kw
class _Sel:
    def __init__(self, cfg=None): self.cfg = cfg
sel.NumberSelector = _Sel; sel.NumberSelectorConfig = _Cfg
sel.TextSelector = _Sel; sel.TextSelectorConfig = _Cfg
class NumberSelectorMode(str, enum.Enum):
    BOX = "box"; SLIDER = "slider"
sel.NumberSelectorMode = NumberSelectorMode

# --- components
comps = _mod("homeassistant.components"); comps.__path__ = []
weather = _mod("homeassistant.components.weather")
for cond in ["CLOUDY", "CLEAR_NIGHT", "PARTLYCLOUDY", "POURING", "RAINY",
             "SNOWY", "SNOWY_RAINY", "SUNNY"]:
    setattr(weather, f"ATTR_CONDITION_{cond}", cond.lower().replace("_", "-"))
weather.Forecast = dict
class WeatherEntity:
    _attr_name = None
weather.WeatherEntity = WeatherEntity
class WeatherEntityFeature(enum.IntFlag):
    FORECAST_DAILY = 1; FORECAST_HOURLY = 2
weather.WeatherEntityFeature = WeatherEntityFeature

sensor = _mod("homeassistant.components.sensor")
class SensorDeviceClass(str, enum.Enum):
    TIMESTAMP = "timestamp"; DATA_SIZE = "data_size"
class SensorStateClass(str, enum.Enum):
    MEASUREMENT = "measurement"; TOTAL_INCREASING = "total_increasing"
@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription:
    key: str
    translation_key: str | None = None
    device_class: Any = None
    state_class: Any = None
    native_unit_of_measurement: Any = None
    suggested_unit_of_measurement: Any = None
    suggested_display_precision: int | None = None
    entity_category: Any = None
    icon: str | None = None
class SensorEntity: ...
sensor.SensorDeviceClass = SensorDeviceClass
sensor.SensorStateClass = SensorStateClass
sensor.SensorEntityDescription = SensorEntityDescription
sensor.SensorEntity = SensorEntity

# --- util.dt
util = _mod("homeassistant.util"); util.__path__ = []
dt_mod = _mod("homeassistant.util.dt")
dt_mod.utcnow = lambda: datetime.now(timezone.utc)
dt_mod.as_local = lambda value: value.astimezone(LOCAL_TZ)
def start_of_local_day(value=None):
    if value is None:
        value = datetime.now(LOCAL_TZ).date()
    elif isinstance(value, datetime):
        value = value.date()
    return datetime.combine(value, dtime(), tzinfo=LOCAL_TZ)
dt_mod.start_of_local_day = start_of_local_day
util.dt = dt_mod
