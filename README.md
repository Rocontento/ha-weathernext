# WeatherNext 3 para Home Assistant

Integración personalizada que crea una entidad `weather` con la previsión de
**WeatherNext 3**, el modelo de predicción meteorológica de Google DeepMind,
leyendo directamente el dataset público de BigQuery.

- Temperatura, viento (media y racha p90), precipitación, probabilidad de
  precipitación, humedad, punto de rocío, nubosidad y presión.
- Previsión **horaria y diaria hasta 15 días** (360 h; por defecto 10 días).
- La probabilidad de precipitación se calcula a partir de los percentiles del
  ensemble de 64 miembros, no es una estimación inventada.
- Sin dependencias extra: habla con la API REST de BigQuery usando `aiohttp` y
  `cryptography`, que Home Assistant ya trae.

## Antes de empezar

El dato es gratuito, pero hay que pedir acceso y engancharlo a un proyecto de
Google Cloud. Son cuatro pasos y el primero tarda unos días.

### 1. Solicitar acceso a WeatherNext

Rellena el formulario de acceso enlazado en la
[guía oficial](https://developers.google.com/weathernext/guides/access-forecast).
Google indica un plazo aproximado de 5–7 días laborables. Una única aprobación
habilita BigQuery, Earth Engine y Cloud Storage a la vez, y no hace falta tener
un contrato de pago con Google Cloud.

### 2. Vincular el dataset en tu proyecto

Con el acceso concedido, en la consola de Google Cloud ve a **BigQuery →
Analytics Hub**, busca la publicación de WeatherNext y suscríbete. Al hacerlo se
crea un *linked dataset* en tu proyecto: **el nombre que le pongas ahí es el
`dataset_id` que pide la integración**. Apunta también su región (normalmente
`US`).

### 3. Crear una cuenta de servicio

En **IAM y administración → Cuentas de servicio**, crea una cuenta y dale:

| Rol | Dónde |
| --- | --- |
| `BigQuery Job User` (`roles/bigquery.jobUser`) | en el proyecto |
| `BigQuery Data Viewer` (`roles/bigquery.dataViewer`) | en el dataset vinculado |

Genera una clave en formato JSON y descárgala. Ese fichero completo es lo que se
pega en el formulario de la integración.

### 4. Comprobar que funciona (recomendado)

Antes de instalar nada, pega esto en la consola de BigQuery cambiando el
proyecto, el dataset y tus coordenadas. Si devuelve filas, la integración va a
funcionar; si no, el problema está en el acceso y no en Home Assistant.

```sql
SELECT
  t.init_time,
  f.time AS valid_time,
  f.temperature_2m_mean - 273.15 AS temp_c,
  f.wind_speed_10m_mean AS viento_ms,
  f.total_precipitation_1hr_mean * 1000 AS precip_mm
FROM `TU_PROYECTO.TU_DATASET.weathernext_3_0_0_0p1deg` AS t, t.forecast AS f
WHERE
  t.init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 18 HOUR)
  AND ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(-3.7038, 40.4168))
  AND f.hours <= 24
ORDER BY t.init_time DESC, f.time ASC;
```

Fíjate en el estimador de bytes que muestra la consola arriba a la derecha: es
lo que vas a consumir en cada actualización.

## Instalación

**Con HACS:** añade este repositorio como repositorio personalizado de tipo
*Integration*, instálalo y reinicia Home Assistant.

**A mano:** copia `custom_components/weathernext` dentro de tu carpeta
`config/custom_components/` y reinicia.

Después: **Ajustes → Dispositivos y servicios → Añadir integración → WeatherNext 3**.

## Configuración

| Campo | Qué es |
| --- | --- |
| Nombre | Etiqueta de la ubicación (p. ej. "Casa") |
| Latitud / Longitud | Por defecto, las de tu instancia |
| ID del proyecto | El proyecto de Google Cloud que factura las consultas |
| ID del dataset | El nombre del dataset vinculado desde Analytics Hub |
| Tabla | `weathernext_3_0_0_0p1deg` (malla ~10 km, todas las variables) |
| Región de BigQuery | La del dataset vinculado, normalmente `US` |
| JSON de la cuenta de servicio | El contenido completo del fichero de clave |

Puedes añadir varias ubicaciones repitiendo el alta; cada una consulta por
separado.

En **Configurar** (opciones) se ajustan luego el horizonte de previsión
(24–360 h), la frecuencia de consulta (15–720 min) y la ventana de pasadas.

### La tabla de 0,05°

Existe también `weathernext_3_0_0_0p05deg`, con malla de ~5 km entrenada contra
observaciones de estaciones reales, pero **solo trae temperatura y punto de
rocío**. No sirve para esta integración, que necesita viento, precipitación y
nubosidad. Si algún día quieres la temperatura más fina, lo suyo es añadirla como
sensor aparte.

## Entidades

| Entidad | Contenido |
| --- | --- |
| `weather.<nombre>` | Condiciones actuales + previsión horaria y diaria |
| `sensor.<nombre>_precipitacion_24_h` | Acumulado previsto en las próximas 24 h |
| `sensor.<nombre>_probabilidad_de_precipitacion_24_h` | Máxima probabilidad horaria en 24 h |
| `sensor.<nombre>_pasada_del_modelo` | Hora de inicialización de la pasada usada (diagnóstico) |
| `sensor.<nombre>_datos_leidos_en_la_ultima_consulta` | Bytes procesados (diagnóstico) |
| `sensor.<nombre>_datos_leidos_acumulados` | Bytes acumulados desde el reinicio (diagnóstico) |

La previsión horaria y diaria se consulta con el servicio
`weather.get_forecasts`, como en cualquier integración de tiempo:

```yaml
action: weather.get_forecasts
target:
  entity_id: weather.casa
data:
  type: daily
```

## Coste y cuota

El dato es gratis; lo que se factura es el procesamiento de BigQuery, con
**1 TiB gratuitos al mes**. La consulta está escrita para mantenerse muy por
debajo:

- Filtra por `init_time` en un rango corto, que es la columna de partición.
- Filtra el punto con `ST_INTERSECTS` sobre `geography_polygon`, que es la
  columna de clustering.
- Selecciona solo las 20 columnas que se usan, no `SELECT *`.

Aun así, vigila el sensor **Datos leídos acumulados** los primeros días. Si se
dispara, baja el horizonte de previsión o sube el intervalo de consulta: ambos
reducen proporcionalmente lo que se lee. Consultar más de una vez por hora no
aporta nada porque el modelo se actualiza cada hora.

## Cómo se combinan las pasadas del modelo

WeatherNext 3 publica dos tipos de pasada: unas cada hora que llegan a 48 h y
otras cada 6 h que llegan a 360 h. La integración pide todas las de la ventana
configurada (18 h por defecto) y, para cada instante, se queda con la de
inicialización más reciente que lo cubra. Así el corto plazo usa siempre el dato
más fresco y el largo plazo se completa con la última pasada larga disponible,
en una sola consulta.

## Limitaciones conocidas

- **El acceso no es inmediato**: hasta que aprueben el formulario, la
  integración no puede funcionar.
- **No hay condición de tormenta ni niebla**: el modelo no publica esas
  variables, así que el icono se deduce de nubosidad, precipitación y
  temperatura. La nieve se infiere de la temperatura a 2 m, no de un campo de
  tipo de precipitación.
- **Sin visibilidad ni índice UV.**
- La probabilidad de precipitación se interpola sobre cinco percentiles
  (p10–p90), así que se redondea a múltiplos del 5 % para no aparentar más
  precisión de la que hay.
- La resolución es de ~10 km: en zonas de montaña la temperatura será la de la
  celda de la malla, no la de tu valle concreto.

## Desarrollo

Los tests no necesitan Home Assistant instalado; ver [tests/README.md](tests/README.md).

---

Datos de previsión: WeatherNext 3 (Google DeepMind). Esta integración no está
afiliada a Google.
