import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ha_stubs  # noqa: F401  (debe importarse antes que la integración)
import asyncio, json, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from custom_components.weathernext import api as bq

fails = []
def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()

state = {"tokens": 0, "queries": [], "mode": "simple"}

async def token_handler(request):
    state["tokens"] += 1
    form = await request.post()
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert form["assertion"].count(".") == 2
    return web.json_response({"access_token": "tok-123", "expires_in": 3600})

def _row(*values):
    return {"f": [{"v": v} for v in values]}

SCHEMA = {"fields": [{"name": "a"}, {"name": "b"}]}

async def query_handler(request):
    if request.headers.get("Authorization") != "Bearer tok-123":
        return web.json_response({"error": {"message": "no auth"}}, status=401)
    body = await request.json()
    state["queries"].append(body)
    mode = state["mode"]
    if mode == "forbidden":
        return web.json_response({"error": {"message": "denied"}}, status=403)
    if mode == "notfound":
        return web.json_response({"error": {"message": "Not found: Table"}}, status=404)
    if mode == "badsql":
        return web.json_response({"error": {"message": "Syntax error"}}, status=400)
    if mode == "async":
        return web.json_response({"jobComplete": False,
                                  "jobReference": {"jobId": "job1", "location": "US"},
                                  "totalBytesProcessed": "42"})
    if mode == "paged":
        return web.json_response({"jobComplete": True, "schema": SCHEMA,
                                  "jobReference": {"jobId": "job2"},
                                  "rows": [_row("1", "x")], "pageToken": "p2",
                                  "totalBytesProcessed": "10"})
    return web.json_response({"jobComplete": True, "schema": SCHEMA,
                              "rows": [_row("1", "x"), _row("2", None)],
                              "totalBytesProcessed": "1234", "cacheHit": True})

async def results_handler(request):
    job = request.match_info["job"]
    if job == "job1":
        return web.json_response({"jobComplete": True, "schema": SCHEMA,
                                  "rows": [_row("9", "z")], "totalBytesProcessed": "42"})
    if request.query.get("pageToken") == "p2":
        return web.json_response({"jobComplete": True, "schema": SCHEMA,
                                  "rows": [_row("2", "y")]})
    return web.json_response({"jobComplete": True, "schema": SCHEMA, "rows": []})

async def main():
    async def bad_token(request):
        return web.json_response({"error": "invalid_grant",
                                  "error_description": "clave mal"}, status=400)

    app = web.Application()
    app.router.add_post("/token", token_handler)
    app.router.add_post("/token2", bad_token)
    app.router.add_post("/bq/projects/{p}/queries", query_handler)
    app.router.add_get("/bq/projects/{p}/queries/{job}", results_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"
    bq.BQ_ROOT = f"{base}/bq"
    bq.POLL_DELAY = 0.01

    sa = bq.parse_service_account(json.dumps({
        "client_email": "bot@p.iam.gserviceaccount.com",
        "private_key": PEM, "token_uri": f"{base}/token"}))

    async with aiohttp.ClientSession() as session:
        client = bq.BigQueryClient(session, sa, "proj", "US")

        rows, stats = await client.query(
            "SELECT 1", [bq.named_param("lat", "FLOAT64", 40.4)])
        check("filas decodificadas", rows == [{"a": "1", "b": "x"}, {"a": "2", "b": None}])
        check("bytes y cache", stats == {"total_bytes_processed": 1234, "cache_hit": True})
        body = state["queries"][-1]
        check("sql estandar", body["useLegacySql"] is False)
        check("parametros con nombre", body["parameterMode"] == "NAMED"
              and body["queryParameters"][0]["name"] == "lat")
        check("region enviada", body["location"] == "US")

        await client.query("SELECT 2")
        check("token cacheado", state["tokens"] == 1)

        state["mode"] = "async"
        rows, stats = await client.query("SELECT 3")
        check("polling de job", rows == [{"a": "9", "b": "z"}])

        state["mode"] = "paged"
        rows, _ = await client.query("SELECT 4")
        check("paginacion", rows == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])

        state["mode"] = "simple"
        _, stats = await client.query("SELECT 5", dry_run=True)
        check("dry run", state["queries"][-1]["dryRun"] is True)

        for mode, expected in [("forbidden", bq.BigQueryAuthError),
                               ("notfound", bq.BigQueryNotFoundError),
                               ("badsql", bq.BigQueryError)]:
            state["mode"] = mode
            try:
                await client.query("SELECT 6")
                check(f"error {mode}", False)
            except expected:
                check(f"error {mode}", True)
            except Exception as err:
                check(f"error {mode} (fue {type(err).__name__})", False)

    # Token rechazado por el servidor de OAuth.
    async with aiohttp.ClientSession() as session:
        sa2 = dict(sa, token_uri=f"{base}/token2")
        client2 = bq.BigQueryClient(session, sa2, "proj", "US")
        try:
            await client2.query("SELECT 1")
            check("token rechazado", False)
        except bq.BigQueryAuthError as err:
            check("token rechazado", "clave mal" in str(err))

    await runner.cleanup()
    print()
    print("FALLOS:", fails if fails else "ninguno")
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
