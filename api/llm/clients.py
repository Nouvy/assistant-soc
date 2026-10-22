"""Clients LLM — point d'entrée unique vers les modèles.

Deux fournisseurs : Gemini (cloud, clé du `.env` racine) et Ollama (local).
Tous les appels modèles du projet passent par ce module (cf. AGENTS.md) : c'est ici,
et nulle part ailleurs, que la clé du `.env` est lue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from llm.prompts import SYSTEM_PROMPT_ANALYSTE_SOC

# Un seul `.env`, à la racine du dépôt.
# api/llm/clients.py -> parents[0]=api/llm, [1]=api, [2]=racine.
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

# --- Réglages -----------------------------------------------------------------

GEMINI_MODEL = "gemini-3.5-flash-lite"

# Modèle local servi par Ollama (installé sur la machine, aucun coût).
OLLAMA_MODEL = "phi4-mini"
OLLAMA_HOST_VAR = "OLLAMA_HOST"
OLLAMA_HOST_DEFAULT = "http://127.0.0.1:11434"
# Un 3,8 B sur CPU peut être lent au premier chargement : large, mais borné.
OLLAMA_TIMEOUT = 300.0

# Règles communes à tous les appels : chaque appelant les obtient par défaut.
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT_ANALYSTE_SOC

# Réglage unique de génération (demande du propriétaire, 2026-10-22).
TEMPERATURE = 0.1

# Nom de la variable, jamais sa valeur.
GEMINI_API_KEY_VAR = "GEMINI_API_KEY"


# --- Erreurs et résultat ------------------------------------------------------

class LLMError(RuntimeError):
    """Échec d'appel au fournisseur. Ne contient jamais de clé."""


class MissingAPIKey(LLMError):
    """Variable d'environnement absente : on nomme la variable, jamais sa valeur."""


@dataclass(frozen=True)
class LLMResult:
    """Ce que l'appel renvoie, sans dépendre des objets du SDK."""

    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int


# --- Gemini -------------------------------------------------------------------

def _gemini_api_key() -> str:
    key = os.getenv(GEMINI_API_KEY_VAR)
    if not key:
        raise MissingAPIKey(f"{GEMINI_API_KEY_VAR} absente du .env racine")
    return key


def _sans_cle(message: str) -> str:
    """Retire toute occurrence de la clé d'un message destiné à remonter."""
    key = os.getenv(GEMINI_API_KEY_VAR)
    return message.replace(key, "***") if key else message


def _gemini_client() -> Any:
    """Point de couture : remplacé par un faux client dans les tests unitaires."""
    from google import genai  # import tardif : le module reste importable sans le SDK

    return genai.Client(api_key=_gemini_api_key())


def generate_gemini(prompt: str, *, system: str | None = None) -> LLMResult:
    kwargs: dict[str, Any] = {
        "model": GEMINI_MODEL,
        "input": prompt,
        "system_instruction": system or DEFAULT_SYSTEM_PROMPT,
        "generation_config": {"temperature": TEMPERATURE},
    }

    client = _gemini_client()
    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — on traduit, on ne masque pas
        raise LLMError(f"appel Gemini refusé : {_sans_cle(str(exc))}") from exc

    usage = getattr(interaction, "usage", None)
    return LLMResult(
        text=(getattr(interaction, "output_text", "") or "").strip(),
        provider="gemini",
        model=GEMINI_MODEL,
        tokens_in=int(getattr(usage, "total_input_tokens", 0) or 0),
        tokens_out=int(getattr(usage, "total_output_tokens", 0) or 0),
    )


# --- Ollama (modèle local) ----------------------------------------------------

def _ollama_host() -> str:
    return (os.getenv(OLLAMA_HOST_VAR) or OLLAMA_HOST_DEFAULT).rstrip("/")


def _ollama_post(payload: dict[str, Any]) -> dict[str, Any]:
    """Point de couture réseau : remplacé par une doublure dans les tests."""
    import httpx  # import tardif : le module reste importable sans le client HTTP

    url = f"{_ollama_host()}/api/chat"
    try:
        reponse = httpx.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
        reponse.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(
            f"appel Ollama impossible sur {_ollama_host()} : {exc}"
        ) from exc
    return reponse.json()


def generate_ollama(prompt: str, *, system: str | None = None) -> LLMResult:
    """Même contrat que `generate_gemini`, servi par le modèle local."""
    donnees = _ollama_post(
        {
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": system or DEFAULT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": TEMPERATURE},
        }
    )

    message = donnees.get("message") or {}
    return LLMResult(
        text=(message.get("content") or "").strip(),
        provider="ollama",
        model=donnees.get("model") or OLLAMA_MODEL,
        tokens_in=int(donnees.get("prompt_eval_count") or 0),
        tokens_out=int(donnees.get("eval_count") or 0),
    )


# --- Choix du fournisseur -----------------------------------------------------

PROVIDERS: dict[str, Any] = {
    "gemini": generate_gemini,
    "ollama": generate_ollama,
}

# Le local par défaut : rapide, gratuit, et aucun appel ne sort de la machine.
DEFAULT_PROVIDER = "ollama"


def generate(
    prompt: str, *, provider: str = DEFAULT_PROVIDER, system: str | None = None
) -> LLMResult:
    """Aiguillage unique : les routeurs n'ont pas à connaître les fournisseurs."""
    try:
        fonction = PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"fournisseur inconnu : {provider}") from None
    return fonction(prompt, system=system)
