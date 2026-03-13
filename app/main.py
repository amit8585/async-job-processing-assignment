from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select, delete

from app.db import engine, Base
from app.api.routes import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Rate Limiter ──────────────────────────────────────────────────────────────

def get_user_identifier(request: Request) -> str:
    """Use user ID from JWT if available, otherwise fall back to IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            from jose import jwt
            SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey-change-in-production-please")
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=get_user_identifier, default_limits=["100/minute"])


# ─── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup, run cleanup task."""
    logger.info("Starting up — creating database tables...")

    # Import models so SQLAlchemy is aware of them before create_all
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables ready.")

    yield

    # Cleanup expired jobs on shutdown (or could be a periodic task)
    logger.info("Shutting down — cleaning up expired jobs...")
    try:
        from app.db import AsyncSessionLocal
        from app.models import Job
        async with AsyncSessionLocal() as session:
            now = datetime.utcnow()
            stmt = delete(Job).where(Job.expires_at < now)
            result = await session.execute(stmt)
            await session.commit()
            logger.info(f"Deleted {result.rowcount} expired jobs.")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")

    await engine.dispose()
    logger.info("Shutdown complete.")


# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Async Job Processing Service",
    description=(
        "A production-ready async job processing backend built with FastAPI, "
        "Celery, PostgreSQL, and Redis. Supports JWT authentication, rate limiting, "
        "pagination, and job expiration."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)

# Serve static files (frontend)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ─── Root Endpoint ─────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"], summary="Root / health check")
async def root():
    return {
        "service": "Async Job Processing Service",
        "status": "healthy",
        "version": "1.0.0",
        "docs": "/docs",
        "frontend": "/static/index.html",
    }


@app.get("/health", tags=["Health"], summary="Health check endpoint")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
