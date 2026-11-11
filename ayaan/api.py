from contextlib import asynccontextmanager

from fastapi import FastAPI

from ayaan.settings import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    eureka_client = None
    if settings.eureka_enabled:
        from py_eureka_client import eureka_client as eureka

        eureka_client = eureka
        eureka.init(
            eureka_server=settings.eureka_url,
            app_name=settings.service_name,
            instance_host=settings.eureka_instance_host,
            instance_port=settings.eureka_instance_port,
        )
    try:
        yield
    finally:
        if eureka_client:
            eureka_client.stop()


app = FastAPI(title="D94 Ayaan", version="0.1.0", lifespan=lifespan)


@app.get("/actuator/health")
async def health():
    return {"status": "UP", "service": settings.service_name}
