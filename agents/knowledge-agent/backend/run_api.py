from app.config import settings
from app.entrypoints.api.main import app
from app.logging import setup_logging


if __name__ == "__main__":
    import uvicorn

    setup_logging().info("starting_server host=%s port=%s", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port)
