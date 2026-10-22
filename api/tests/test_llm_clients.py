"""Tests de api/llm/clients.py.

Les tests unitaires ne sortent pas sur le réseau et n'ont besoin d'aucune clé.
Les tests d'intégration font de vrais appels (Gemini, Ollama), marqués `integration`.
"""

import os

import httpx
import pytest

from llm import clients

CLE_FACTICE = "test-fausse-cle-0123456789abcdef"


# --- Doublures ----------------------------------------------------------------

class _FauxUsage:
    total_input_tokens = 11
    total_output_tokens = 7


class _FausseInteraction:
    output_text = "  OK  "
    usage = _FauxUsage()


class _FausseInteractionSansUsage:
    output_text = "OK"


class _FauxClient:
    """Imite `genai.Client` : `client.interactions.create(**kwargs)`."""

    def __init__(self, interaction=None, erreur: Exception | None = None) -> None:
        self.interactions = self
        self.appels: list[dict] = []
        self._interaction = interaction
        self._erreur = erreur

    def create(self, **kwargs):
        self.appels.append(kwargs)
        if self._erreur is not None:
            raise self._erreur
        return self._interaction


# --- Tests unitaires (sans réseau, sans clé) ----------------------------------

def test_cle_absente_nomme_la_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(clients.GEMINI_API_KEY_VAR, raising=False)

    with pytest.raises(clients.MissingAPIKey) as erreur:
        clients.generate_gemini("bonjour")

    assert clients.GEMINI_API_KEY_VAR in str(erreur.value)


def test_requete_envoyee_avec_temperature_01(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clients.GEMINI_API_KEY_VAR, CLE_FACTICE)
    faux = _FauxClient(interaction=_FausseInteraction())
    monkeypatch.setattr(clients, "_gemini_client", lambda: faux)

    clients.generate_gemini("bonjour", system="tu es un analyste SOC")
    clients.generate_gemini("bonjour")

    assert len(faux.appels) == 2
    avec_system, sans_system = faux.appels
    assert avec_system["model"] == clients.GEMINI_MODEL
    assert avec_system["input"] == "bonjour"
    assert avec_system["generation_config"] == {"temperature": 0.1}
    assert avec_system["system_instruction"] == "tu es un analyste SOC"
    # Sans consigne explicite, les règles communes s'appliquent.
    assert sans_system["system_instruction"] == clients.DEFAULT_SYSTEM_PROMPT


def test_prompt_systeme_porte_les_quatre_blocs() -> None:
    prompt = clients.DEFAULT_SYSTEM_PROMPT

    for bloc in ("RÔLE", "CONTEXTE", "CONTRAINTES", "FORMAT DE SORTIE"):
        assert bloc in prompt
    assert "français" in prompt


def test_mappe_texte_et_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clients.GEMINI_API_KEY_VAR, CLE_FACTICE)
    monkeypatch.setattr(
        clients, "_gemini_client", lambda: _FauxClient(interaction=_FausseInteraction())
    )

    resultat = clients.generate_gemini("bonjour")

    assert resultat.text == "OK"  # espaces retirés
    assert resultat.provider == "gemini"
    assert resultat.model == clients.GEMINI_MODEL
    assert resultat.tokens_in == 11
    assert resultat.tokens_out == 7


def test_usage_absent_donne_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clients.GEMINI_API_KEY_VAR, CLE_FACTICE)
    monkeypatch.setattr(
        clients,
        "_gemini_client",
        lambda: _FauxClient(interaction=_FausseInteractionSansUsage()),
    )

    resultat = clients.generate_gemini("bonjour")

    assert resultat.tokens_in == 0
    assert resultat.tokens_out == 0


def test_erreur_gemini_ne_fuit_pas_la_cle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clients.GEMINI_API_KEY_VAR, CLE_FACTICE)
    erreur_sdk = RuntimeError(f"requête refusée pour la clé {CLE_FACTICE}")
    monkeypatch.setattr(
        clients, "_gemini_client", lambda: _FauxClient(erreur=erreur_sdk)
    )

    with pytest.raises(clients.LLMError) as erreur:
        clients.generate_gemini("bonjour")

    message = str(erreur.value)
    assert CLE_FACTICE not in message
    assert "***" in message
    assert "appel Gemini refusé" in message


# --- Ollama (local, sans réseau grâce à la doublure) --------------------------

def test_ollama_envoie_systeme_temperature_et_modele(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envoyes: list[dict] = []

    def faux_post(payload: dict) -> dict:
        envoyes.append(payload)
        return {"message": {"content": "  OK  "}, "model": clients.OLLAMA_MODEL}

    monkeypatch.setattr(clients, "_ollama_post", faux_post)

    clients.generate_ollama("bonjour", system="tu es un analyste SOC")
    clients.generate_ollama("bonjour")

    assert len(envoyes) == 2
    avec_system, sans_system = envoyes
    assert avec_system["model"] == clients.OLLAMA_MODEL
    assert avec_system["stream"] is False
    assert avec_system["options"] == {"temperature": 0.1}
    assert avec_system["messages"] == [
        {"role": "system", "content": "tu es un analyste SOC"},
        {"role": "user", "content": "bonjour"},
    ]
    # Sans consigne explicite, les règles communes s'appliquent, comme chez Gemini.
    assert sans_system["messages"][0] == {
        "role": "system",
        "content": clients.DEFAULT_SYSTEM_PROMPT,
    }


def test_ollama_mappe_texte_et_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clients,
        "_ollama_post",
        lambda payload: {
            "message": {"content": "  Réponse locale  "},
            "model": "phi4-mini",
            "prompt_eval_count": 183,
            "eval_count": 64,
        },
    )

    resultat = clients.generate_ollama("bonjour")

    assert resultat.text == "Réponse locale"  # espaces retirés
    assert resultat.provider == "ollama"
    assert resultat.model == "phi4-mini"
    assert resultat.tokens_in == 183
    assert resultat.tokens_out == 64


def test_ollama_injoignable_donne_une_erreur_claire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def post_qui_echoue(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "post", post_qui_echoue)

    with pytest.raises(clients.LLMError) as erreur:
        clients.generate_ollama("bonjour")

    message = str(erreur.value)
    assert "appel Ollama impossible" in message
    assert clients.OLLAMA_HOST_DEFAULT in message


# --- Aiguillage ---------------------------------------------------------------

def test_generate_aiguille_vers_le_bon_fournisseur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appels: list[str] = []

    def faux(mark: str):
        def fonction(prompt: str, *, system: str | None = None) -> clients.LLMResult:
            appels.append(mark)
            return clients.LLMResult(mark, mark, mark, 0, 0)

        return fonction

    monkeypatch.setitem(clients.PROVIDERS, "gemini", faux("gemini"))
    monkeypatch.setitem(clients.PROVIDERS, "ollama", faux("ollama"))

    assert clients.generate("bonjour", provider="gemini").provider == "gemini"
    assert clients.generate("bonjour", provider="ollama").provider == "ollama"
    assert clients.generate("bonjour").provider == clients.DEFAULT_PROVIDER
    assert appels == ["gemini", "ollama", clients.DEFAULT_PROVIDER]


def test_generate_refuse_un_fournisseur_inconnu() -> None:
    with pytest.raises(ValueError, match="fournisseur inconnu"):
        clients.generate("bonjour", provider="mistral")


# --- Tests d'intégration (réseau) ---------------------------------------------

@pytest.mark.integration
def test_gemini_reel() -> None:
    if not os.getenv(clients.GEMINI_API_KEY_VAR):
        pytest.skip(f"{clients.GEMINI_API_KEY_VAR} absente du .env racine")

    resultat = clients.generate_gemini("Réponds uniquement par: OK")

    assert resultat.provider == "gemini"
    assert resultat.model == clients.GEMINI_MODEL
    assert resultat.text
    assert resultat.tokens_in > 0
    assert resultat.tokens_out > 0


@pytest.mark.integration
def test_ollama_reel() -> None:
    try:
        httpx.get(f"{clients._ollama_host()}/api/version", timeout=5)
    except httpx.HTTPError as exc:
        pytest.skip(f"Ollama injoignable ({exc})")

    resultat = clients.generate_ollama("Réponds uniquement par: OK")

    assert resultat.provider == "ollama"
    assert resultat.model.startswith(clients.OLLAMA_MODEL)
    assert resultat.text
    assert resultat.tokens_out > 0
