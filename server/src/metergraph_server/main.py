import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, ingest, prices, usage


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate()
        loaded = prices.load_identity()
        app.state.catalog = loaded.snapshot
        app.state.catalog_doc = loaded.document
        app.state.catalog_version = loaded.version
        app.state.catalog_hash = loaded.content_hash
        yield
        db.close()

    app = FastAPI(title="Metergraph OSS", lifespan=lifespan)
    app.include_router(ingest.router)
    app.include_router(usage.router)

    @app.get("/healthz")
    def healthz():
        with db.pool().connection() as con:
            con.execute("select 1")
        return {
            "ok": True,
            "catalog_version": app.state.catalog_version,
            "catalog_hash": app.state.catalog_hash,
        }

    @app.get("/v1/config")
    def config(if_none_match: str | None = Header(default=None)):
        doc = {"routes": {}}
        etag = (
            '"'
            + hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:32]
            + '"'
        )
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(doc, headers={"ETag": etag, "Cache-Control": "max-age=300"})

    static_dir = Path(
        os.environ.get("MG_DASHBOARD_DIST")
        or Path(__file__).resolve().parents[3] / "dashboard" / "dist"
    )
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="dashboard")
    return app


app = create_app()
