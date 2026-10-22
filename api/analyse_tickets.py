"""Analyse en lot du jeu de tickets SOC via l'endpoint `/chat`.

Un `POST /chat` par ticket de `data/tickets.json`, puis agrégation des sévérités
renvoyées par le modèle. Le script ne parle jamais directement au modèle : il
passe par l'API du projet, donc par `api/llm/` (cf. AGENTS.md).

Deux usages :

    uv run python analyse_tickets.py --provider ollama
    uv run python analyse_tickets.py --provider gemini --limite 10 --concurrence 2

et, depuis le routeur `/triage`, la même fonction `analyser_tickets` avec un
callback de progression pour la page de test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from llm.clients import DEFAULT_PROVIDER

# api/analyse_tickets.py -> parents[0]=api, [1]=racine du dépôt.
API_DIR = Path(__file__).resolve().parent
RACINE = API_DIR.parent
TICKETS_PATH = RACINE / "data" / "tickets.json"
RAPPORTS_DIR = API_DIR / ".logs"

# Le script interroge l'API qui tourne : l'adresse est configurable, jamais devinée.
API_BASE_URL_VAR = "API_BASE_URL"
API_BASE_URL_DEFAULT = "http://127.0.0.1:8000"
# Un appel phi4-mini peut être lent au premier chargement du modèle.
TIMEOUT = 300.0

# Nombre de tickets traités par défaut (décision du propriétaire, 2026-10-22).
# Au-delà, une passe complète prend des dizaines de minutes : `--tous` reste
# disponible pour l'analyser en entier.
LIMITE_DEFAUT = 50

SEVERITES = ("HIGH", "MEDIUM", "LOW")
INCERTAIN = "INCERTAIN"

# Libellés acceptés en entrée, ramenés aux trois niveaux du prompt.
EQUIVALENCES = {
    "HIGH": "HIGH",
    "CRITIQUE": "HIGH",
    "ELEVE": "HIGH",
    "ELEVEE": "HIGH",
    "MEDIUM": "MEDIUM",
    "MOYEN": "MEDIUM",
    "MOYENNE": "MEDIUM",
    "LOW": "LOW",
    "FAIBLE": "LOW",
    "BAS": "LOW",
    "BASSE": "LOW",
}

_MOTIF_LIBELLE = re.compile(r"s[ée]v[ée]rit[ée]\s*[:=]?", re.IGNORECASE)
_MOTIF_MOT = re.compile(r"[A-Za-z\u00c0-\u017f]+")


# --- Lecture et mise en forme -------------------------------------------------

def charger_tickets(chemin: Path = TICKETS_PATH) -> list[dict[str, Any]]:
    """Charge le jeu de tickets ; refuse un fichier qui n'est pas une liste."""
    with chemin.open(encoding="utf-8") as fichier:
        donnees = json.load(fichier)
    if not isinstance(donnees, list):
        raise ValueError(f"{chemin.name} doit contenir une liste de tickets")
    for ticket in donnees:
        if not isinstance(ticket, dict) or not ticket.get("id"):
            raise ValueError(f"ticket sans identifiant dans {chemin.name}")
    return donnees


def message_ticket(ticket: dict[str, Any]) -> str:
    """Message envoyé à `/chat` : le ticket tel quel, en JSON lisible."""
    return "Analyse ce ticket SOC.\n\n" + json.dumps(
        ticket, ensure_ascii=False, indent=2
    )


# --- Lecture de la sévérité ---------------------------------------------------

def _normaliser(mot: str) -> str:
    sans_accent = unicodedata.normalize("NFD", mot)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return sans_accent.encode("ascii", "ignore").decode().upper()


def severite(reponse: str) -> str:
    """Extrait la sévérité de la réponse du modèle.

    On cherche l'étiquette « Sévérité : » puis le premier mot connu qui la suit
    (le prompt impose ce format, mais les modèles varient). Sans étiquette ou
    sans mot reconnu : `INCERTAIN`, jamais une valeur inventée.
    """
    libelle = _MOTIF_LIBELLE.search(reponse)
    fenetre = reponse[libelle.end() : libelle.end() + 80] if libelle else reponse[:200]
    for mot in _MOTIF_MOT.findall(fenetre):
        valeur = EQUIVALENCES.get(_normaliser(mot))
        if valeur:
            return valeur
    return INCERTAIN


# --- Résultat ----------------------------------------------------------------

@dataclass(frozen=True)
class Analyse:
    """Ce qu'on retient d'un ticket : sa sévérité et son coût."""

    ticket_id: str
    severite: str
    model: str
    tokens_in: int
    tokens_out: int
    erreur: str | None = None


@dataclass
class Rapport:
    """Agrégat d'une passe sur le jeu de tickets."""

    provider: str
    model: str
    total: int
    par_severite: dict[str, int]
    echecs: int
    duree_s: float
    tokens_in: int
    tokens_out: int
    date: str
    details: list[Analyse] = field(default_factory=list)

    def vers_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "date": self.date,
            "total": self.total,
            "analyses": self.total - self.echecs,
            "echecs": self.echecs,
            "duree_s": round(self.duree_s, 1),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "par_severite": dict(self.par_severite),
            "details": [
                {
                    "id": analyse.ticket_id,
                    "severite": analyse.severite,
                    "tokens_in": analyse.tokens_in,
                    "tokens_out": analyse.tokens_out,
                    "erreur": analyse.erreur,
                }
                for analyse in self.details
            ],
        }


# --- Appel d'un ticket --------------------------------------------------------

def _un_ticket(
    client: httpx.Client, base_url: str, provider: str, ticket: dict[str, Any]
) -> Analyse:
    identifiant = str(ticket.get("id"))
    try:
        reponse = client.post(
            f"{base_url}/chat",
            json={"message": message_ticket(ticket), "provider": provider},
        )
    except httpx.HTTPError as exc:
        return Analyse(identifiant, INCERTAIN, "", 0, 0, f"transport : {exc}")

    if reponse.status_code != 200:
        try:
            detail = str(reponse.json().get("detail"))
        except ValueError:
            detail = reponse.text[:200]
        return Analyse(
            identifiant,
            INCERTAIN,
            "",
            0,
            0,
            f"HTTP {reponse.status_code} : {detail}",
        )

    donnees = reponse.json()
    return Analyse(
        ticket_id=identifiant,
        severite=severite(str(donnees.get("reply") or "")),
        model=str(donnees.get("model") or ""),
        tokens_in=int(donnees.get("tokens_in") or 0),
        tokens_out=int(donnees.get("tokens_out") or 0),
    )


# --- Passe complète -----------------------------------------------------------

def base_url() -> str:
    return (os.getenv(API_BASE_URL_VAR) or API_BASE_URL_DEFAULT).rstrip("/")


def analyser_tickets(
    *,
    provider: str = DEFAULT_PROVIDER,
    url: str | None = None,
    tickets: list[dict[str, Any]] | None = None,
    limite: int | None = None,
    concurrence: int = 1,
    progression: Callable[[int, int], None] | None = None,
    client: httpx.Client | None = None,
) -> Rapport:
    """Un `POST /chat` par ticket, puis les comptes par sévérité.

    `progression(faits, total)` est appelée au départ puis après chaque ticket :
    c'est ce qui alimente la page de test sans bloquer la requête.
    """
    base = (url or base_url()).rstrip("/")
    lots = charger_tickets() if tickets is None else tickets
    if limite is not None:
        lots = lots[:limite]
    total = len(lots)
    if progression:
        progression(0, total)

    propre_client = client is None
    client_http = client or httpx.Client(timeout=TIMEOUT)
    faits = 0
    resultats: list[Analyse] = []
    debut = time.perf_counter()

    try:
        if concurrence <= 1:
            for ticket in lots:
                resultats.append(_un_ticket(client_http, base, provider, ticket))
                faits += 1
                if progression:
                    progression(faits, total)
        else:
            with ThreadPoolExecutor(max_workers=concurrence) as executeur:
                futurs = [
                    executeur.submit(_un_ticket, client_http, base, provider, ticket)
                    for ticket in lots
                ]
                for futur in futurs:
                    resultats.append(futur.result())
                    faits += 1
                    if progression:
                        progression(faits, total)
    finally:
        if propre_client:
            client_http.close()

    duree = time.perf_counter() - debut
    comptes = Counter(analyse.severite for analyse in resultats if not analyse.erreur)
    par_severite = {niveau: comptes.get(niveau, 0) for niveau in SEVERITES}
    par_severite[INCERTAIN] = comptes.get(INCERTAIN, 0)
    modele = next((a.model for a in resultats if a.model), provider)

    return Rapport(
        provider=provider,
        model=modele,
        total=total,
        par_severite=par_severite,
        echecs=sum(1 for analyse in resultats if analyse.erreur),
        duree_s=duree,
        tokens_in=sum(analyse.tokens_in for analyse in resultats),
        tokens_out=sum(analyse.tokens_out for analyse in resultats),
        date=datetime.now().astimezone().isoformat(timespec="seconds"),
        details=resultats,
    )


def ecrire_rapport(rapport: Rapport, dossier: Path | None = None) -> Path:
    """Écrit le détail dans `api/.logs/` (ignoré par Git) et renvoie le chemin."""
    dossier = dossier or RAPPORTS_DIR
    dossier.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    chemin = dossier / f"triage-{rapport.provider}-{horodatage}.json"
    chemin.write_text(
        json.dumps(rapport.vers_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return chemin


# --- Ligne de commande --------------------------------------------------------

def _resume(rapport: Rapport) -> str:
    lignes = [
        f"{rapport.provider} ({rapport.model}) — {rapport.total} tickets en "
        f"{rapport.duree_s:.1f} s",
        "",
    ]
    for niveau in SEVERITES:
        lignes.append(f"  {niveau:<9} {rapport.par_severite[niveau]:>4}")
    lignes.append(f"  {INCERTAIN:<9} {rapport.par_severite[INCERTAIN]:>4}")
    lignes.append(f"  échecs    {rapport.echecs:>4}")
    lignes.append("")
    lignes.append(f"  tokens    {rapport.tokens_in} entrée / {rapport.tokens_out} sortie")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Classe les tickets de data/tickets.json via POST /chat."
    )
    analyseur.add_argument(
        "--provider",
        choices=("ollama", "gemini"),
        default=DEFAULT_PROVIDER,
        help=f"modèle interrogé (défaut : {DEFAULT_PROVIDER})",
    )
    analyseur.add_argument(
        "--limite",
        type=int,
        default=LIMITE_DEFAUT,
        help=f"n'analyser que les N premiers tickets (défaut : {LIMITE_DEFAUT})",
    )
    analyseur.add_argument(
        "--tous", action="store_true", help="analyser tout le fichier, sans limite"
    )
    analyseur.add_argument(
        "--concurrence", type=int, default=1, help="appels simultanés (défaut : 1)"
    )
    analyseur.add_argument(
        "--url", default=None, help=f"API à interroger (défaut : {base_url()})"
    )
    analyseur.add_argument(
        "--sortie", type=Path, default=None, help="chemin du rapport JSON"
    )
    arguments = analyseur.parse_args(argv)

    limite = None if arguments.tous else arguments.limite
    if limite is not None and limite < 1:
        analyseur.error("--limite doit être supérieur à 0")
    if arguments.concurrence < 1:
        analyseur.error("--concurrence doit être supérieur à 0")

    def progression(faits: int, total: int) -> None:
        print(f"\r{faits}/{total} tickets", end="", file=sys.stderr, flush=True)

    rapport = analyser_tickets(
        provider=arguments.provider,
        url=arguments.url,
        limite=limite,
        concurrence=arguments.concurrence,
        progression=progression,
    )
    print("\r" + " " * 24 + "\r", end="", file=sys.stderr)

    if arguments.sortie is None:
        chemin = ecrire_rapport(rapport)
    else:
        chemin = arguments.sortie
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(
            json.dumps(rapport.vers_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(_resume(rapport))
    print(f"\nRapport : {chemin}")
    # Code de sortie non nul seulement si aucun ticket n'a pu être analysé.
    return 1 if rapport.total and rapport.echecs == rapport.total else 0


if __name__ == "__main__":
    raise SystemExit(main())
