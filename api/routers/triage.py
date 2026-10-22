"""Routeur /triage — lance l'analyse du jeu de tickets et suit son avancement.

Le travail lui-même vit dans `analyse_tickets` (script CLI réutilisable) : ce
routeur ne fait que démarrer le script en tâche de fond et exposer sa
progression. Aucun appel modèle n'est écrit ici (cf. AGENTS.md).

500 tickets × un appel `/chat` durent plusieurs minutes : la requête POST rend
tout de suite un identifiant de travail, la page interroge ensuite `GET`.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from analyse_tickets import LIMITE_DEFAUT, RACINE, analyser_tickets, ecrire_rapport
from llm.clients import DEFAULT_PROVIDER

router = APIRouter(tags=["triage"])

# Un seul travail en cours par fournisseur : on ne lance pas deux fois la même
# passe sur le même modèle (Ollama les sérialiserait de toute façon).
_JOBS: dict[str, "Job"] = {}
_VERROU = threading.Lock()
_MAX_JOBS = 10


@dataclass
class Job:
    provider: str
    etat: Literal["en_cours", "termine", "echec"] = "en_cours"
    faits: int = 0
    total: int = 0
    rapport: dict[str, Any] | None = None
    erreur: str | None = None


class TriageRequest(BaseModel):
    # Fournisseur inconnu : rejeté en 422 avant tout appel.
    provider: Literal["gemini", "ollama"] = DEFAULT_PROVIDER


class TriageResponse(BaseModel):
    job_id: str
    provider: str
    etat: Literal["en_cours", "termine", "echec"]
    faits: int
    total: int
    rapport: dict[str, Any] | None = None
    erreur: str | None = None


def _instantane(job_id: str, job: Job) -> TriageResponse:
    return TriageResponse(
        job_id=job_id,
        provider=job.provider,
        etat=job.etat,
        faits=job.faits,
        total=job.total,
        rapport=job.rapport,
        erreur=job.erreur,
    )


def _executer(job_id: str, provider: str) -> None:
    """Corps de la tâche de fond : le script, avec un compteur partagé."""

    def progression(faits: int, total: int) -> None:
        with _VERROU:
            travail = _JOBS.get(job_id)
            if travail is not None:
                travail.faits = faits
                travail.total = total

    try:
        rapport = analyser_tickets(
            provider=provider, limite=LIMITE_DEFAUT, progression=progression
        )
    except Exception as exc:  # noqa: BLE001 — remonté tel quel à la page
        with _VERROU:
            travail = _JOBS.get(job_id)
            if travail is not None:
                travail.etat = "echec"
                travail.erreur = str(exc)
        return

    contenu = rapport.vers_dict()
    try:
        contenu["fichier"] = str(ecrire_rapport(rapport).relative_to(RACINE))
    except OSError:
        # Un disque en lecture seule ne doit pas invalider une analyse réussie.
        contenu["fichier"] = None

    with _VERROU:
        travail = _JOBS.get(job_id)
        if travail is not None:
            travail.etat = "termine"
            travail.rapport = contenu


def _demarrer_en_fond(job_id: str, provider: str) -> None:
    """Point de couture : les tests le remplacent par un appel synchrone."""
    threading.Thread(
        target=_executer, args=(job_id, provider), name=f"triage-{job_id}", daemon=True
    ).start()


@router.post("/triage", response_model=TriageResponse, status_code=202)
def lancer_triage(requete: TriageRequest) -> TriageResponse:
    with _VERROU:
        for travail in _JOBS.values():
            if travail.provider == requete.provider and travail.etat == "en_cours":
                raise HTTPException(
                    status_code=409,
                    detail=f"une analyse {requete.provider} est déjà en cours",
                )

        # On ne garde que les derniers travaux : le détail des 500 tickets pèse.
        for ancien in list(_JOBS)[:-_MAX_JOBS + 1]:
            _JOBS.pop(ancien, None)

        job_id = uuid.uuid4().hex[:12]
        job = Job(provider=requete.provider)
        _JOBS[job_id] = job

    _demarrer_en_fond(job_id, requete.provider)
    return _instantane(job_id, job)


@router.get("/triage/{job_id}", response_model=TriageResponse)
def etat_triage(job_id: str) -> TriageResponse:
    with _VERROU:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"travail inconnu : {job_id}")
        return _instantane(job_id, job)
