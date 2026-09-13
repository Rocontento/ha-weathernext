# Tests

No requieren instalar Home Assistant: `ha_stubs.py` sustituye los módulos de HA
que usa la integración por versiones mínimas, así que basta con `aiohttp`,
`cryptography` y `voluptuous`.

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp cryptography voluptuous
for t in tests/test_*.py; do .venv/bin/python "$t" || exit 1; done
```

- `test_logic.py` — conversiones meteorológicas, probabilidad de precipitación a
  partir de los percentiles, parseo de filas, mezcla de pasadas, agregado diario,
  mapeo de condiciones y validación de identificadores.
- `test_api_http.py` — capa HTTP contra un BigQuery falso: firma del JWT, caché
  del token, polling de jobs, paginación y mapeo de errores.
- `test_e2e.py` — coordinador y entidades completas contra ese servidor falso.
