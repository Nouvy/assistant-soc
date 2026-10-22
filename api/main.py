from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse

from routers.chat import router as chat_router
from routers.triage import router as triage_router

# Un seul `.env` pour tout le projet, à la racine du dépôt (cf. AGENTS.md).
# Chemin explicite : `uv run` s'exécute depuis `api/`, une recherche implicite
# ne trouverait rien.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

WEB_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="assistant-soc API", version="0.1.0")
app.include_router(chat_router)
app.include_router(triage_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Sonde de vie : ne dépend d'aucun service externe."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def formulaire() -> FileResponse:
    """Page de démonstration : un formulaire qui appelle `POST /chat`."""
    return FileResponse(WEB_DIR / "index.html")
