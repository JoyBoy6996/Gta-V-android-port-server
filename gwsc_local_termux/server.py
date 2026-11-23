import asyncio
import logging
import sys
from contextlib import asynccontextmanager, closing, suppress

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .capture import (
    AccessLogMiddleware,
    BodyLimitMiddleware,
    CaptureMiddleware,
    RequestIDMiddleware,
)
from .database import get_db, init_db
from .errors import error_payload
from .generate_certs import generate_ssl_certificates
from .logging_utils import install_redaction_filter
from .maintenance import maintenance_loop
from .multiplayer_relay import relay_stats, start_udp_relay, stop_udp_relay

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
install_redaction_filter(logging.getLogger())
log = logging.getLogger("gwsc.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s v%s (%s)", config.SERVER_NAME, config.SERVER_VERSION, config.ENVIRONMENT)
    init_db()
    if config.SSL_ENABLED:
        generate_ssl_certificates()

    transport = await start_udp_relay(config.RELAY_HOST, config.RELAY_PORT)
    app.state.relay_transport = transport
    maintenance_task = asyncio.create_task(maintenance_loop())
    app.state.maintenance_task = maintenance_task

    if config.CAPTURE_ENABLED:
        config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        log.warning("Development request capture is ENABLED at %s", config.CAPTURE_FILE)

    try:
        yield
    finally:
        log.info("Shutting down %s", config.SERVER_NAME)
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        stop_udp_relay()


app = FastAPI(
    title=config.SERVER_NAME,
    version=config.SERVER_VERSION,
    description=(
        "Private backend for the GTA V Android port. Not affiliated with "
        "Rockstar Games. Protocol compatibility is tracked in docs/protocol/."
    ),
    lifespan=lifespan,
    docs_url="/docs" if config.DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if config.DOCS_ENABLED else None,
)

# In Starlette the middleware added *last* is the outermost layer.  Add the
# innermost layer first so execution order is:
#   CORS -> RequestID -> AccessLog -> BodyLimit -> Capture -> app
# CORS stays outermost so error responses carry CORS headers; RequestID runs
# before AccessLog so every log line has a correlation id; BodyLimit runs
# before Capture so a body is read once at the real limit and only a bounded
# copy is stored for capture.
app.add_middleware(CaptureMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials="*" not in config.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-GWSC-Token",
        "X-Ticket",
        "ros-SessionTicket",
        "ros-Session-Ticket",
        "x-ros-sessionticket",
        "Idempotency-Key",
    ],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_payload(exc.detail)),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = jsonable_encoder(exc.errors())
    message = errors[0].get("msg", "Invalid request") if errors else "Invalid request"
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_request",
            "message": message,
            "detail": errors,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "-")
    log.exception("Unhandled error on %s %s request_id=%s", request.method, request.url.path, request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An internal server error occurred",
            "requestId": request_id,
        },
    )


@app.get("/")
async def root():
    return {
        "name": config.SERVER_NAME,
        "version": config.SERVER_VERSION,
        "status": "online",
        "private": True,
        "note": "Private backend for a modified client. Not affiliated with Rockstar Games.",
    }


@app.get("/health")
@app.get("/healthz")
async def health():
    database_ok = True
    try:
        with closing(get_db()) as conn:
            conn.execute("SELECT 1")
    except Exception:
        database_ok = False
        log.exception("Health check database failure")
    from .routes.events import event_bus

    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "relay": relay_stats(),
        "sseConnections": event_bus.total_connections(),
        "environment": config.ENVIRONMENT,
    }


# Route modules are imported after the app is created so their dependencies
# see the configured middleware and exception handlers.
from .routes.auth import router as auth_router  # noqa: E402
from .routes.events import router as events_router  # noqa: E402
from .routes.friends import router as friends_router  # noqa: E402
from .routes.game_launch import router as game_launch_router  # noqa: E402
from .routes.info import router as info_router  # noqa: E402
from .routes.lso import router as lso_router  # noqa: E402
from .routes.lso_cloud import router as lso_cloud_router  # noqa: E402
from .routes.lso_static import router as lso_static_router  # noqa: E402
from .routes.lso_save_migration import router as lso_save_migration_router  # noqa: E402
from .routes.lso_stats import router as lso_stats_router  # noqa: E402
from .routes.lso_ugc import router as lso_ugc_router  # noqa: E402
from .routes.messages import router as messages_router  # noqa: E402
from .routes.profile import router as profile_router  # noqa: E402
from .routes.relay import router as relay_router  # noqa: E402
from .routes.ros import router as ros_router  # noqa: E402
from .routes.sharkcards import router as sharkcards_router  # noqa: E402

app.include_router(info_router)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(friends_router)
app.include_router(messages_router)
app.include_router(sharkcards_router)
app.include_router(events_router)
app.include_router(relay_router)
app.include_router(game_launch_router)
# Exact LSO static, cloud and .asmx routes must precede ros.py's greedy aliases.
app.include_router(lso_static_router)
app.include_router(lso_cloud_router)
app.include_router(lso_router)
app.include_router(lso_save_migration_router)
app.include_router(lso_stats_router)
app.include_router(lso_ugc_router)
app.include_router(ros_router)


def run():
    ssl_kwargs = {}
    if config.SSL_ENABLED:
        generate_ssl_certificates()
        if config.SSL_CERT.exists() and config.SSL_KEY.exists():
            ssl_kwargs = {
                "ssl_certfile": str(config.SSL_CERT),
                "ssl_keyfile": str(config.SSL_KEY),
            }
            log.info("TLS enabled: %s", config.SSL_CERT)

    proto = "https" if ssl_kwargs else "http"
    log.info("==================================================")
    log.info(
        "%s v%s listening on %s://%s:%d",
        config.SERVER_NAME,
        config.SERVER_VERSION,
        proto,
        config.SERVER_HOST,
        config.SERVER_PORT,
    )
    log.info("UDP relay: %s:%d", config.RELAY_HOST, config.RELAY_PORT)
    log.info("==================================================")

    uvicorn.run(
        "gwsc_local_termux.server:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="info",
        reload=False,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    run()
