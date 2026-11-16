import asyncio
import logging
import os
import sys
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    SERVER_NAME, SERVER_VERSION, SERVER_HOST, SERVER_PORT,
    RELAY_HOST, RELAY_PORT, SSL_ENABLED, SSL_CERT, SSL_KEY, LOG_FILE
)
from .database import init_db
from .generate_certs import generate_ssl_certificates
from .multiplayer_relay import start_udp_relay

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
log = logging.getLogger("gwsc.server")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Starting up %s v%s...", SERVER_NAME, SERVER_VERSION)
    init_db()
    if SSL_ENABLED:
        generate_ssl_certificates()

    # Start UDP multiplayer relay
    relay_transport = await start_udp_relay(RELAY_HOST, RELAY_PORT)

    yield

    # Shutdown
    log.info("Shutting down %s...", SERVER_NAME)
    if relay_transport:
        relay_transport.close()

app = FastAPI(
    title=SERVER_NAME,
    version=SERVER_VERSION,
    description="Full Local & Cloud Server Replacement for GTA V / GWSC Android Multiplayer",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root status
@app.get("/")
async def root():
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "status": "online",
        "protocol": "GWSC + ROS compatible",
        "ports": {
            "http_or_https": SERVER_PORT,
            "udp_multiplayer_relay": RELAY_PORT
        }
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

# Import and mount route modules
from .routes.info import router as info_router
from .routes.auth import router as auth_router
from .routes.profile import router as profile_router
from .routes.friends import router as friends_router
from .routes.messages import router as messages_router
from .routes.sharkcards import router as sharkcards_router
from .routes.events import router as events_router
from .routes.ros import router as ros_router

app.include_router(info_router)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(friends_router)
app.include_router(messages_router)
app.include_router(sharkcards_router)
app.include_router(events_router)
app.include_router(ros_router)

def run():
    init_db()
    ssl_kwargs = {}
    if SSL_ENABLED:
        generate_ssl_certificates()
        if SSL_CERT.exists() and SSL_KEY.exists():
            ssl_kwargs = {
                "ssl_certfile": str(SSL_CERT),
                "ssl_keyfile": str(SSL_KEY)
            }
            log.info("SSL Enabled: %s, %s", SSL_CERT, SSL_KEY)

    proto = "https" if ssl_kwargs else "http"
    log.info("==================================================")
    log.info("%s v%s Starting (%s://0.0.0.0:%d)", SERVER_NAME, SERVER_VERSION, proto, SERVER_PORT)
    log.info("UDP Multiplayer Relay: %s:%d", RELAY_HOST, RELAY_PORT)
    log.info("==================================================")

    uvicorn.run(
        "gwsc_local_termux.server:app",
        host="0.0.0.0",
        port=SERVER_PORT,
        log_level="info",
        reload=False,
        **ssl_kwargs
    )

if __name__ == "__main__":
    run()
