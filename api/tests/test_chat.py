"""Tests du routeur /chat — aucune requête réseau, le client est simulé."""

import pytest
from fastapi.testclient import TestClient

from llm.clients import LLMError, LLMResult, MissingAPIKey
from main import app
from routers import chat as routeur_chat

client = TestClient(app)


def _resultat(provider: str = "gemini") -> LLMResult:
    return LLMResult(
        text="Réponse simulée",
        provider=provider,
        model="gemini-3.5-flash-lite" if provider == "gemini" else "phi4-mini",
        tokens_in=12,
        tokens_out=5,
    )


def test_chat_renvoie_la_reponse_et_les_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[dict] = []

    def faux_generate(
        prompt: str, *, provider: str = "gemini", system: str | None = None
    ) -> LLMResult:
        appels.append({"prompt": prompt, "provider": provider, "system": system})
        return _resultat(provider)

    monkeypatch.setattr(routeur_chat, "generate", faux_generate)

    reponse = client.post(
        "/chat", json={"message": "bonjour", "system": "sois bref", "provider": "gemini"}
    )

    assert reponse.status_code == 200
    assert reponse.json() == {
        "reply": "Réponse simulée",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "tokens_in": 12,
        "tokens_out": 5,
    }
    assert appels == [{"prompt": "bonjour", "provider": "gemini", "system": "sois bref"}]


def test_chat_achemine_vers_le_modele_local(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[str] = []

    def faux_generate(
        prompt: str, *, provider: str = "gemini", system: str | None = None
    ) -> LLMResult:
        appels.append(provider)
        return _resultat(provider)

    monkeypatch.setattr(routeur_chat, "generate", faux_generate)

    reponse = client.post("/chat", json={"message": "bonjour", "provider": "ollama"})

    assert reponse.status_code == 200
    assert reponse.json()["provider"] == "ollama"
    assert reponse.json()["model"] == "phi4-mini"
    assert appels == ["ollama"]


def test_chat_utilise_le_fournisseur_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[str] = []

    def faux_generate(
        prompt: str, *, provider: str = "gemini", system: str | None = None
    ) -> LLMResult:
        appels.append(provider)
        return _resultat(provider)

    monkeypatch.setattr(routeur_chat, "generate", faux_generate)

    reponse = client.post("/chat", json={"message": "bonjour"})

    assert reponse.status_code == 200
    assert appels == [routeur_chat.DEFAULT_PROVIDER]


def test_chat_refuse_un_fournisseur_inconnu() -> None:
    reponse = client.post("/chat", json={"message": "bonjour", "provider": "mistral"})

    assert reponse.status_code == 422


def test_chat_refuse_un_message_vide() -> None:
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_chat_traduit_un_echec_fournisseur_en_502(monkeypatch: pytest.MonkeyPatch) -> None:
    def echec(
        prompt: str, *, provider: str = "gemini", system: str | None = None
    ) -> LLMResult:
        raise LLMError("appel Gemini refusé : 503 high demand")

    monkeypatch.setattr(routeur_chat, "generate", echec)

    reponse = client.post("/chat", json={"message": "bonjour"})

    assert reponse.status_code == 502
    assert "high demand" in reponse.json()["detail"]


def test_chat_signale_une_cle_absente_en_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def sans_cle(
        prompt: str, *, provider: str = "gemini", system: str | None = None
    ) -> LLMResult:
        raise MissingAPIKey("GEMINI_API_KEY absente du .env racine")

    monkeypatch.setattr(routeur_chat, "generate", sans_cle)

    reponse = client.post("/chat", json={"message": "bonjour", "provider": "gemini"})

    assert reponse.status_code == 503
    assert "GEMINI_API_KEY" in reponse.json()["detail"]


def test_page_formulaire_est_servie() -> None:
    reponse = client.get("/")

    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]
    assert 'id="formulaire"' in reponse.text
    # Les deux fournisseurs sont proposés dans le sélecteur.
    assert 'value="ollama"' in reponse.text
    assert 'value="gemini"' in reponse.text
