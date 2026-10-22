"""Tests du script d'analyse — aucun appel réseau, le client HTTP est simulé."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import analyse_tickets
from analyse_tickets import (
    INCERTAIN,
    Rapport,
    analyser_tickets,
    message_ticket,
    severite,
)

TICKETS = [
    {"id": "TKT-0001", "event": "KB manquante", "host": "SRV-FILE01"},
    {"id": "TKT-0002", "event": "PowerShell encodé", "host": "SRV-PRINT01"},
    {"id": "TKT-0003", "event": "Connexion inhabituelle", "host": "WS-014"},
]

HIGH = "Sévérité : HIGH\nExplication : brute force.\n1. Bloquer l'IP."
LOW = "**Sévérité :** LOW\nExplication : bruit de fond.\n1. Surveiller."


class _FausseReponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        texte: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = texte

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("pas de JSON")
        return self._payload


def _reussite(reply: str, model: str = "phi4-mini") -> _FausseReponse:
    return _FausseReponse(
        payload={
            "reply": reply,
            "provider": "ollama",
            "model": model,
            "tokens_in": 120,
            "tokens_out": 40,
        }
    )


class _FauxClient:
    """Client httpx minimal : rejoue une file de réponses, note les appels."""

    def __init__(self, reponses: list[Any]) -> None:
        self.reponses = list(reponses)
        self.appels: list[tuple[str, dict[str, Any]]] = []
        self.ferme = False

    def post(self, url: str, json: dict[str, Any] | None = None) -> _FausseReponse:
        self.appels.append((url, json or {}))
        suivant = self.reponses.pop(0)
        if isinstance(suivant, Exception):
            raise suivant
        return suivant

    def close(self) -> None:
        self.ferme = True


# --- Lecture de la sévérité ---------------------------------------------------

@pytest.mark.parametrize(
    ("reponse", "attendu"),
    [
        (HIGH, "HIGH"),
        ("Sévérité : MEDIUM", "MEDIUM"),
        ("Sévérité : ÉLEVÉE", "HIGH"),
        ("Sévérité : faible", "LOW"),
        ("Sévérité : critique", "HIGH"),
        ("Sévérité (estimation) : HIGH", "HIGH"),
        ("Je ne sais pas classer cette alerte.", INCERTAIN),
        ("", INCERTAIN),
    ],
)
def test_severite_lue_dans_la_reponse(reponse: str, attendu: str) -> None:
    assert severite(reponse) == attendu


def test_message_contient_le_ticket_complet() -> None:
    message = message_ticket(TICKETS[0])

    assert "TKT-0001" in message
    assert "SRV-FILE01" in message
    # La charge envoyée reste du JSON lisible par le modèle.
    charge = message.split("\n\n", 1)[1]
    assert json.loads(charge) == TICKETS[0]


# --- Appels par ticket --------------------------------------------------------

def test_un_appel_chat_par_ticket_et_comptes_par_severite() -> None:
    client = _FauxClient([_reussite(HIGH), _reussite(LOW), _reussite("Sévérité : MEDIUM")])
    etapes: list[tuple[int, int]] = []

    rapport = analyser_tickets(
        provider="ollama",
        url="http://api.test",
        tickets=TICKETS,
        client=client,
        progression=lambda faits, total: etapes.append((faits, total)),
    )

    assert len(client.appels) == 3
    assert all(url == "http://api.test/chat" for url, _ in client.appels)
    assert [charge["provider"] for _, charge in client.appels] == ["ollama"] * 3
    assert [charge["message"].split('"id": "')[1].split('"')[0] for _, charge in client.appels] == [
        "TKT-0001",
        "TKT-0002",
        "TKT-0003",
    ]

    assert rapport.total == 3
    assert rapport.par_severite == {"HIGH": 1, "MEDIUM": 1, "LOW": 1, INCERTAIN: 0}
    assert rapport.echecs == 0
    assert rapport.tokens_in == 360
    assert rapport.tokens_out == 120
    assert rapport.model == "phi4-mini"
    # Progression : le départ, puis un point par ticket.
    assert etapes == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_le_script_transmet_le_fournisseur_demande() -> None:
    client = _FauxClient([_reussite(HIGH, model="gemini-3.5-flash-lite")])

    rapport = analyser_tickets(
        provider="gemini", url="http://api.test", tickets=TICKETS[:1], client=client
    )

    assert client.appels[0][1]["provider"] == "gemini"
    assert rapport.provider == "gemini"
    assert rapport.model == "gemini-3.5-flash-lite"


def test_echec_http_compte_comme_echec_sans_casser_les_comptes() -> None:
    client = _FauxClient(
        [
            _reussite(HIGH),
            _FausseReponse(status_code=502, payload={"detail": "Ollama arrêté"}),
        ]
    )

    rapport = analyser_tickets(
        provider="ollama", url="http://api.test", tickets=TICKETS[:2], client=client
    )

    assert rapport.total == 2
    assert rapport.par_severite["HIGH"] == 1
    assert rapport.echecs == 1
    assert "502" in rapport.details[1].erreur
    assert "Ollama arrêté" in rapport.details[1].erreur


def test_panne_de_transport_ne_leve_pas() -> None:
    client = _FauxClient([httpx.ConnectError("connexion refusée")])

    rapport = analyser_tickets(
        provider="ollama", url="http://api.test", tickets=TICKETS[:1], client=client
    )

    assert rapport.echecs == 1
    assert "transport" in rapport.details[0].erreur
    assert rapport.par_severite["HIGH"] == 0


def test_limite_ne_traite_que_les_premiers() -> None:
    client = _FauxClient([_reussite(HIGH), _reussite(LOW)])

    rapport = analyser_tickets(
        provider="ollama",
        url="http://api.test",
        tickets=TICKETS,
        limite=2,
        client=client,
    )

    assert rapport.total == 2
    assert len(client.appels) == 2


def test_rapport_serialisable_et_ecrit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FauxClient([_reussite(HIGH)])
    rapport = analyser_tickets(
        provider="ollama", url="http://api.test", tickets=TICKETS[:1], client=client
    )
    monkeypatch.setattr(analyse_tickets, "RAPPORTS_DIR", tmp_path)

    chemin = analyse_tickets.ecrire_rapport(rapport)
    contenu = json.loads(chemin.read_text(encoding="utf-8"))

    assert chemin.parent == tmp_path
    assert chemin.name.startswith("triage-ollama-")
    assert contenu["par_severite"]["HIGH"] == 1
    assert contenu["details"][0]["id"] == "TKT-0001"
    # Les détails ne conservent aucune clé ni secret, seulement des compteurs.
    assert set(contenu["details"][0]) == {
        "id",
        "severite",
        "tokens_in",
        "tokens_out",
        "erreur",
    }


def test_chargement_refuse_un_fichier_qui_n_est_pas_une_liste(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chemin = tmp_path / "tickets.json"
    chemin.write_text(json.dumps({"tickets": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        analyse_tickets.charger_tickets(chemin)


def test_le_jeu_de_tickets_du_projet_est_lisible() -> None:
    tickets = analyse_tickets.charger_tickets()

    assert len(tickets) >= 1
    assert all(ticket.get("id") for ticket in tickets)
    # Aucun ticket ne doit dépasser la limite de 8000 caractères de `/chat`.
    assert max(len(message_ticket(ticket)) for ticket in tickets) < 8000


def test_rapport_vide_reste_coherent() -> None:
    rapport = Rapport(
        provider="ollama",
        model="phi4-mini",
        total=0,
        par_severite={"HIGH": 0, "MEDIUM": 0, "LOW": 0, INCERTAIN: 0},
        echecs=0,
        duree_s=0.0,
        tokens_in=0,
        tokens_out=0,
        date="2026-10-22T12:00:00+02:00",
    )

    assert rapport.vers_dict()["analyses"] == 0
