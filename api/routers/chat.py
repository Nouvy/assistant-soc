"""Routeur /chat — expose les clients LLM en HTTP.

Aucun appel modèle n'est écrit ici : tout passe par `llm.clients` (cf. AGENTS.md).
Le fournisseur est choisi par l'appelant (`gemini` ou `ollama`).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm.clients import DEFAULT_PROVIDER, LLMError, MissingAPIKey, generate

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    system: str | None = Field(default=None, max_length=4000)
    # Fournisseur inconnu : rejeté en 422 par la validation, avant tout appel.
    provider: Literal["gemini", "ollama"] = DEFAULT_PROVIDER


class ChatResponse(BaseModel):
    reply: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int


@router.post("/chat", response_model=ChatResponse)
def chat(requete: ChatRequest) -> ChatResponse:
    try:
        resultat = generate(
            requete.message, provider=requete.provider, system=requete.system
        )
    except MissingAPIKey as exc:
        # Configuration incomplète : le nom de la variable suffit, jamais sa valeur.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMError as exc:
        # Le fournisseur a refusé ou est indisponible (429, 503, Ollama arrêté).
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(
        reply=resultat.text,
        provider=resultat.provider,
        model=resultat.model,
        tokens_in=resultat.tokens_in,
        tokens_out=resultat.tokens_out,
    )
