"""Cliente asíncrono mínimo para la API REST de BigQuery.

Se autentica con una cuenta de servicio (flujo JWT-bearer) usando solo
`aiohttp` y `cryptography`, ambas dependencias que Home Assistant ya trae.
Así la integración no necesita instalar `google-cloud-bigquery` (que es
síncrona y arrastra grpc).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_LOGGER = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/bigquery"
BQ_ROOT = "https://bigquery.googleapis.com/bigquery/v2"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"

TOKEN_LIFETIME = 3600
TOKEN_EXPIRY_SKEW = 120
QUERY_TIMEOUT_MS = 60_000
POLL_ATTEMPTS = 10
POLL_DELAY = 3.0

REQUIRED_SA_KEYS = ("client_email", "private_key")


class BigQueryError(Exception):
    """Error genérico hablando con BigQuery."""


class BigQueryAuthError(BigQueryError):
    """Credenciales inválidas o sin permisos."""


class BigQueryNotFoundError(BigQueryError):
    """El proyecto, dataset o tabla no existe (o no es visible)."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def parse_service_account(raw: str) -> dict[str, Any]:
    """Valida y normaliza el JSON de la cuenta de servicio."""
    try:
        info = json.loads(raw)
    except (TypeError, ValueError) as err:
        raise BigQueryAuthError("El JSON de la cuenta de servicio no es válido") from err

    if not isinstance(info, dict):
        raise BigQueryAuthError("El JSON de la cuenta de servicio no es un objeto")

    missing = [key for key in REQUIRED_SA_KEYS if not info.get(key)]
    if missing:
        raise BigQueryAuthError(
            f"Faltan campos en la cuenta de servicio: {', '.join(missing)}"
        )

    # Al pegar el JSON en un formulario es fácil que los saltos de línea de la
    # clave lleguen escapados; los normalizamos para que `cryptography` la lea.
    private_key = info["private_key"]
    if "\\n" in private_key and "\n" not in private_key:
        info["private_key"] = private_key.replace("\\n", "\n")

    try:
        key = serialization.load_pem_private_key(
            info["private_key"].encode("utf-8"), password=None
        )
    except (ValueError, TypeError) as err:
        raise BigQueryAuthError("No se ha podido leer la clave privada (private_key)") from err

    if not isinstance(key, rsa.RSAPrivateKey):
        raise BigQueryAuthError("La clave privada de la cuenta de servicio no es RSA")

    return info


class BigQueryClient:
    """Cliente muy reducido: solo autenticación y `jobs.query`."""

    def __init__(
        self,
        session: ClientSession,
        service_account: dict[str, Any],
        project_id: str,
        location: str | None = None,
    ) -> None:
        self._session = session
        self._sa = service_account
        self._project_id = project_id
        self._location = location or None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    def _sign_assertion(self) -> str:
        """Construye y firma el JWT. Es CPU-bound: se llama en un executor."""
        now = int(time.time())
        token_uri = self._sa.get("token_uri") or DEFAULT_TOKEN_URI
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self._sa["client_email"],
            "scope": SCOPE,
            "aud": token_uri,
            "iat": now,
            "exp": now + TOKEN_LIFETIME,
        }
        signing_input = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode("utf-8"))
            for part in (header, claims)
        ).encode("ascii")

        key = serialization.load_pem_private_key(
            self._sa["private_key"].encode("utf-8"), password=None
        )
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input.decode('ascii')}.{_b64url(signature)}"

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at - TOKEN_EXPIRY_SKEW:
                return self._token

            loop = asyncio.get_running_loop()
            assertion = await loop.run_in_executor(None, self._sign_assertion)
            token_uri = self._sa.get("token_uri") or DEFAULT_TOKEN_URI

            try:
                async with self._session.post(
                    token_uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                ) as resp:
                    payload = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise BigQueryAuthError(
                            f"No se ha podido obtener el token OAuth ({resp.status}): "
                            f"{payload.get('error_description') or payload.get('error')}"
                        )
            except ClientError as err:
                raise BigQueryError(f"Error de red pidiendo el token: {err}") from err

            self._token = payload["access_token"]
            self._token_expires_at = time.time() + float(
                payload.get("expires_in", TOKEN_LIFETIME)
            )
            return self._token

    # ----------------------------------------------------------------- query

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with self._session.request(
                method, url, headers=headers, **kwargs
            ) as resp:
                payload = await resp.json(content_type=None)
                if resp.status >= 400:
                    error = (payload or {}).get("error", {})
                    message = error.get("message", f"HTTP {resp.status}")
                    if resp.status in (401, 403):
                        raise BigQueryAuthError(message)
                    if resp.status == 404:
                        raise BigQueryNotFoundError(message)
                    raise BigQueryError(message)
                return payload
        except ClientError as err:
            raise BigQueryError(f"Error de red hablando con BigQuery: {err}") from err

    async def query(
        self,
        sql: str,
        parameters: list[dict[str, Any]] | None = None,
        *,
        dry_run: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Ejecuta SQL estándar y devuelve (filas, estadísticas)."""
        body: dict[str, Any] = {
            "query": sql,
            "useLegacySql": False,
            "timeoutMs": QUERY_TIMEOUT_MS,
            "parameterMode": "NAMED",
            "queryParameters": parameters or [],
        }
        if dry_run:
            body["dryRun"] = True
        if self._location:
            body["location"] = self._location

        payload = await self._request(
            "POST", f"{BQ_ROOT}/projects/{self._project_id}/queries", json=body
        )

        stats = {
            "total_bytes_processed": int(payload.get("totalBytesProcessed") or 0),
            "cache_hit": bool(payload.get("cacheHit")),
        }
        if dry_run:
            return [], stats

        job_ref = payload.get("jobReference", {})
        rows: list[dict[str, Any]] = []
        schema = payload.get("schema")
        complete = payload.get("jobComplete", False)

        for attempt in range(POLL_ATTEMPTS):
            if complete:
                break
            await asyncio.sleep(POLL_DELAY)
            payload = await self._get_results(job_ref)
            schema = payload.get("schema") or schema
            complete = payload.get("jobComplete", False)
            _LOGGER.debug("Esperando a que termine el job (intento %s)", attempt + 1)
        else:
            if not complete:
                raise BigQueryError("La consulta no ha terminado a tiempo")

        fields = [field["name"] for field in (schema or {}).get("fields", [])]
        rows.extend(_decode_rows(payload.get("rows"), fields))

        page_token = payload.get("pageToken")
        while page_token:
            payload = await self._get_results(job_ref, page_token=page_token)
            rows.extend(_decode_rows(payload.get("rows"), fields))
            page_token = payload.get("pageToken")

        return rows, stats

    async def _get_results(
        self, job_ref: dict[str, Any], page_token: str | None = None
    ) -> dict[str, Any]:
        job_id = job_ref.get("jobId")
        if not job_id:
            raise BigQueryError("BigQuery no ha devuelto un identificador de job")
        params: dict[str, Any] = {"timeoutMs": QUERY_TIMEOUT_MS}
        location = job_ref.get("location") or self._location
        if location:
            params["location"] = location
        if page_token:
            params["pageToken"] = page_token
        return await self._request(
            "GET",
            f"{BQ_ROOT}/projects/{self._project_id}/queries/{job_id}",
            params=params,
        )


def _decode_rows(
    raw_rows: list[dict[str, Any]] | None, fields: list[str]
) -> list[dict[str, Any]]:
    """Convierte el formato `{f: [{v: ...}]}` de BigQuery en dicts planos."""
    decoded: list[dict[str, Any]] = []
    for row in raw_rows or []:
        cells = row.get("f", [])
        decoded.append(
            {
                name: cells[index].get("v") if index < len(cells) else None
                for index, name in enumerate(fields)
            }
        )
    return decoded


def named_param(name: str, bq_type: str, value: Any) -> dict[str, Any]:
    """Construye un parámetro con nombre para `jobs.query`."""
    return {
        "name": name,
        "parameterType": {"type": bq_type},
        "parameterValue": {"value": str(value)},
    }
