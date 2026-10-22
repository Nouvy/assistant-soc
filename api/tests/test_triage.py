"""Tests du routeur /triage — l'analyse est simulée, aucun appel modèle."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import analyse_tickets
from analyse_tickets import Rapport
from main import app
from routers import triage as routeur_triage

client = TestClient(app)


@pytest.fixture(autouse=True)
def _vider_les_travaux() -> Any:
    routeur_triage._JOBS.clear()
    yield
    routeur_triage._JOBS.clear()


def _rapport(provider: str = "ollama") -> Rapport:
    return Rapport(
        provider=provider,
        model="phi4-mini" if provider == "ollama" else "gemini-3.5-flash-lite",
        total=3,
        par_severite={"HIGH": 1, "MEDIUM": 1, "LOW": 1, "INCERTAIN": 0},
        echecs=0,
        duree_s=1.5,
        tokens_in=300,
        tokens_out=90,
        date="2026-10-22T12:00:00+02:00",
        details=[],
    )


def _demarrage_synchrone(
    monkeypatch: pytest.MonkeyPatch, appels: list[str], options: list[dict[str, Any]] | None = None
) -> None:
    """Remplace le fil d'exécution : le travail tourne dans la requête."""

    def faux_analyser(
        *, provider: str = "ollama", progression: Any = None, **options_appel: Any
    ) -> Rapport:
        appels.append(provider)
        if options is not None:
            options.append(options_appel)
        if progression:
            progression(0, 3)
            progression(3, 3)
        return _rapport(provider)

    monkeypatch.setattr(routeur_triage, "analyser_tickets", faux_analyser)
    monkeypatch.setattr(routeur_triage, "ecrire_rapport", lambda _: (
        analyse_tickets.RACINE / "api" / ".logs" / "triage-test.json"
    ))
    monkeypatch.setattr(
        routeur_triage,
        "_demarrer_en_fond",
        lambda job_id, provider: routeur_triage._executer(job_id, provider),
    )


def test_triage_lance_le_script_et_renvoie_les_comptes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appels: list[str] = []
    options: list[dict[str, Any]] = []
    _demarrage_synchrone(monkeypatch, appels, options)

    demarrage = client.post("/triage", json={"provider": "ollama"})

    assert demarrage.status_code == 202
    corps = demarrage.json()
    assert corps["provider"] == "ollama"
    assert corps["etat"] == "termine"
    assert corps["faits"] == 3
    assert corps["total"] == 3
    assert corps["rapport"]["par_severite"] == {
        "HIGH": 1,
        "MEDIUM": 1,
        "LOW": 1,
        "INCERTAIN": 0,
    }
    assert corps["rapport"]["analyses"] == 3
    assert corps["rapport"]["fichier"].endswith("triage-test.json")
    assert appels == ["ollama"]
    # Les boutons n'analysent que les 50 premiers tickets.
    assert options == [{"limite": analyse_tickets.LIMITE_DEFAUT}]
    assert analyse_tickets.LIMITE_DEFAUT == 50

    # Le suivi par identifiant renvoie le même état.
    suivi = client.get(f"/triage/{corps['job_id']}")
    assert suivi.status_code == 200
    assert suivi.json()["etat"] == "termine"


def test_triage_choisit_le_fournisseur_demande(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[str] = []
    _demarrage_synchrone(monkeypatch, appels)

    corps = client.post("/triage", json={"provider": "gemini"}).json()

    assert appels == ["gemini"]
    assert corps["rapport"]["provider"] == "gemini"
    assert corps["rapport"]["model"] == "gemini-3.5-flash-lite"


def test_triage_utilise_le_fournisseur_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[str] = []
    _demarrage_synchrone(monkeypatch, appels)

    corps = client.post("/triage", json={}).json()

    assert appels == [routeur_triage.DEFAULT_PROVIDER]
    assert corps["provider"] == routeur_triage.DEFAULT_PROVIDER


def test_triage_refuse_un_fournisseur_inconnu() -> None:
    assert client.post("/triage", json={"provider": "mistral"}).status_code == 422


def test_triage_refuse_un_second_travail_du_meme_modele() -> None:
    routeur_triage._JOBS["deja-la"] = routeur_triage.Job(provider="ollama")

    reponse = client.post("/triage", json={"provider": "ollama"})

    assert reponse.status_code == 409
    assert "ollama" in reponse.json()["detail"]


def test_triage_inconnu_renvoie_404() -> None:
    assert client.get("/triage/inconnu").status_code == 404


def test_triage_signale_un_echec_sans_crasher(monkeypatch: pytest.MonkeyPatch) -> None:
    def analyse_cassee(**_: Any) -> Rapport:
        raise RuntimeError("API indisponible sur http://127.0.0.1:8000")

    monkeypatch.setattr(routeur_triage, "analyser_tickets", analyse_cassee)
    monkeypatch.setattr(
        routeur_triage,
        "_demarrer_en_fond",
        lambda job_id, provider: routeur_triage._executer(job_id, provider),
    )

    corps = client.post("/triage", json={"provider": "ollama"}).json()

    assert corps["etat"] == "echec"
    assert "API indisponible" in corps["erreur"]
    assert corps["rapport"] is None
