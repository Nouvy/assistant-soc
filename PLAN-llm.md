# Plan — Couche `api/llm/clients.py` (client Gemini)

> Statut : **exécuté le 2026-10-22** — `api/llm/clients.py` en place, 5 tests unitaires et
> le test d'intégration réel verts. Deux précisions d'exécution au § 11.
> Doc de référence citée : <https://ai.google.dev/gemini-api/docs/get-started> (redirige vers
> `/gemini-api/docs/get-started`), consultée le 2026-10-22.
> Objectif : un module `api/llm/clients.py` qui branche le projet sur l'API Gemini, avec la
> température 0.1 et le modèle le moins coûteux en tokens.

---

## 1. Périmètre

### Dans ce lot

- `api/llm/clients.py` : **une** fonction d'appel Gemini, au contrat explicite.
- Un type de retour unique (`LLMResult`) contenant le texte **et** la consommation de tokens.
- Deux exceptions maison, qui ne peuvent pas laisser fuiter une clé.
- Dépendance : `google-genai` (SDK officiel Google).
- Tests : unitaires hors réseau + un test d'intégration réel (un seul appel Gemini).

### Hors périmètre

- **Aucun fournisseur local** : ni Ollama, ni llama.cpp, ni modèle téléchargé sur la machine.
- Les routeurs `/triage`, `/chat`, `/summarize` : ils viendront consommer `clients.py` plus tard.
- La couche RAG (`api/rag/`), les prompts métier, les schémas Pydantic de réponse.
- Le streaming, le multi-tour (`previous_interaction_id`), le function calling, les embeddings.
- Une clé supplémentaire : `GEMINI_API_KEY` est déjà dans le `.env` racine (nom vérifié, valeur
  jamais lue ni affichée).

---

## 2. Décision 1 — le modèle Gemini : `gemini-3.1-flash-lite`

> **Mise à jour du 2026-10-22 (décision du propriétaire)** : le modèle retenu est désormais
> **`gemini-3.5-flash-lite`** (`$0,30` entrée / `$2,50` sortie par million de tokens, free tier
> compris). La température 0.1 reste appliquée, 3.5 étant antérieur à la série 3.6+ qui l'ignore.
> L'arbitrage ci-dessous est conservé comme trace de la décision initiale.

Ta contrainte est double : **le moins de tokens possible** et **température 0.1**. Ces deux
contraintes pointent vers le même modèle.

| Modèle | Entrée / 1M | Sortie / 1M | Free tier | Verdict |
|---|---|---|---|---|
| `gemini-2.5-flash-lite` | $0,10 | $0,40 | oui | **écarté** : accès restreint aux comptes qui l'utilisaient déjà, Google demande d'utiliser 3.5 Flash-Lite ou 3.8 Flash pour tout nouveau projet |
| **`gemini-3.1-flash-lite`** | **$0,25** | **$1,50** | **oui** | **retenu** : le moins cher réellement disponible pour un nouveau projet |
| `gemini-3.5-flash-lite` | $0,30 | $2,50 | oui | plus cher en sortie |
| `gemini-3.6-flash` | $0,75 | $3,75 | oui | 3× le prix, et **ignore `temperature`** |
| `gemini-3.8-flash` | $0,75 | $3,75 | oui | idem |
| `gemini-4-argon` | $2,00 | $10,00 | non | hors sujet |

Deuxième argument, décisif : la doc d'aujourd'hui précise que **« Gemini 4 et les modèles Gemini
3.6+ ignorent `temperature`, `top_p` et `top_k` »** et renvoient des en-têtes d'avertissement. La
température 0.1 ne serait donc **pas appliquée** sur 3.6/3.8/4. Sur `gemini-3.1-flash-lite`, elle
l'est. Le modèle le moins cher est aussi le seul qui respecte ta consigne de température.

Fiche modèle (doc officielle) : 1 048 576 tokens d'entrée, 65 536 en sortie, text/image/video/audio/
PDF, sorties structurées, thinking, function calling — tous utiles pour `/triage` et `/summarize`
plus tard.

### Ce que « moins de tokens » veut dire, honnêtement

La température ne change **pas** le nombre de tokens consommés : elle change la distribution des
choix. Ce qui pèse réellement sur la facture, c'est le **niveau de réflexion** et la **longueur de
sortie**. Le plan applique donc la température 0.1 demandée, sans la présenter comme une économie,
et laisse `max_output_tokens` hors périmètre pour l'instant (à calibrer quand les prompts métier
existeront). Si tu veux la vraie économie tout de suite, on ajoutera `thinking_level: "minimal"`
— voir la réserve au § 3.

---

## 3. Décision 2 — comment on se branche sur l'API Gemini

**SDK officiel `google-genai`** (version courante sur PyPI : **2.32.0**, `requires-python >=3.10`
— compatible avec notre 3.13), pas d'appel HTTP écrit à la main : l'authentification, les retries
et le typage sont gérés par Google.

**Surface d'appel : l'Interactions API**, celle que montre la page « Get started » que tu as
pointée. Elle remplace `generate_content` comme voie recommandée aujourd'hui.

```python
from google import genai

client = genai.Client(api_key=...)          # clé lue dans le .env racine
interaction = client.interactions.create(
    model="gemini-3.1-flash-lite",
    input=prompt,
    system_instruction=system,              # omis si None
    generation_config={"temperature": 0.1},
)
interaction.output_text                     # le texte, déjà concaténé
```

Équivalent REST, pour référence (la même chose sans SDK) :

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gemini-3.1-flash-lite","input":"...","generation_config":{"temperature":0.1}}'
```

Trois points de vigilance, tous traités dans le code :

1. **La clé** : `GEMINI_API_KEY`, lue depuis `os.environ` après chargement du `.env` **racine** par
   chemin explicite (`parents[2]` depuis `api/llm/clients.py`). Aucune valeur en dur, aucune clé
   logguée, aucune clé dans un message d'erreur.
2. **Les tokens** : la réponse expose `usage.total_input_tokens` / `usage.total_output_tokens`
   (l'équivalent REST le confirme). Lecture défensive (`getattr(..., 0)`) : si le SDK renomme le
   champ, on renvoie 0 au lieu de planter. Ces deux compteurs sont ce qui permettra de vérifier
   « le modèle qui consomme le moins » sur de vrais appels.
3. **Réserve documentée** : la page modèle de `gemini-3.1-flash-lite` montre encore des exemples en
   `client.models.generate_content(...)`, et la table des niveaux de réflexion ne liste pas
   explicitement ce modèle (seule sa variante image y figure). **Règle de repli, décidée à
   l'avance** : si le test d'intégration renvoie une erreur 400/404 ou `AttributeError` sur
   `client.interactions`, on bascule la même fonction sur
   `client.models.generate_content(model=..., contents=..., config={"temperature": 0.1})`,
   qui est documenté pour ce modèle précis. La signature publique de `generate_gemini` ne change
   pas ; le plan est mis à jour avec le résultat constaté. Pour la même raison, `thinking_level`
   n'est **pas** envoyé dans ce lot.

---

## 4. Contrat d'interface

```python
generate_gemini(prompt: str, *, system: str | None = None) -> LLMResult
```

- Fonction **synchrone** : les endpoints FastAPI déclarés en `def` tournent déjà dans un
  threadpool ; pas d'`async` à propager pour un appel bloquant.
- `LLMResult` : `text`, `provider`, `model`, `tokens_in`, `tokens_out` (dataclass gelée).
- Erreurs : `LLMError` (échec d'appel) et `MissingAPIKey` (variable absente, qui **nomme** la
  variable sans jamais en donner la valeur).
- `system` est optionnel : les prompts métier n'existent pas encore.

**Un second fournisseur, plus tard** : une nouvelle fonction qui renvoie `LLMResult` suffit
(`generate_mistral()`, `generate_groq()`…). Pas de registre, pas de fabrique, pas d'abstraction
supplémentaire dans ce lot — on l'ajoutera le jour où il y aura vraiment deux implémentations.

---

## 5. Code de référence — `api/llm/clients.py`

```python
"""Client LLM — point d'entrée unique vers l'API Gemini.

Tous les appels modèles du projet passent par ce module (cf. AGENTS.md) : c'est ici,
et nulle part ailleurs, que la clé du `.env` racine est lue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Un seul `.env`, à la racine du dépôt.
# api/llm/clients.py -> parents[0]=api/llm, [1]=api, [2]=racine.
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

# --- Réglages -----------------------------------------------------------------

GEMINI_MODEL = "gemini-3.1-flash-lite"

# Réglage unique de génération (demande du propriétaire, 2026-10-22).
TEMPERATURE = 0.1


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
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise MissingAPIKey("GEMINI_API_KEY absente du .env racine")
    return key


def _gemini_client() -> Any:
    """Point de couture : remplacé par un faux client dans les tests unitaires."""
    from google import genai  # import tardif : le module reste importable sans le SDK

    return genai.Client(api_key=_gemini_api_key())


def generate_gemini(prompt: str, *, system: str | None = None) -> LLMResult:
    kwargs: dict[str, Any] = {
        "model": GEMINI_MODEL,
        "input": prompt,
        "generation_config": {"temperature": TEMPERATURE},
    }
    if system:
        kwargs["system_instruction"] = system

    client = _gemini_client()
    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — on traduit, on ne masque pas
        raise LLMError(f"appel Gemini refusé : {exc}") from exc

    usage = getattr(interaction, "usage", None)
    return LLMResult(
        text=(getattr(interaction, "output_text", "") or "").strip(),
        provider="gemini",
        model=GEMINI_MODEL,
        tokens_in=int(getattr(usage, "total_input_tokens", 0) or 0),
        tokens_out=int(getattr(usage, "total_output_tokens", 0) or 0),
    )
```

Deux choix explicités :

- Le `from google import genai` est **tardif** (dans `_gemini_client`) : `clients.py` reste
  importable et testable sans le SDK, et une erreur d'import ne casse pas `/health`.
- `_gemini_client()` existe uniquement pour donner aux tests unitaires une couture propre,
  sans dépendre du réseau ni d'une vraie clé.

---

## 6. Fichiers et dépendances

| Fichier | Action |
|---|---|
| `api/llm/clients.py` | **créé** (contenu du § 5) |
| `api/llm/.gitkeep` | supprimé (le dossier a désormais du contenu) |
| `api/pyproject.toml` | `google-genai` ajouté en dépendance d'exécution |
| `api/tests/test_llm_clients.py` | **créé** (§ 7) |
| `api/tests/conftest.py` | **créé** : déclare le marqueur `integration` |
| `api/main.py` | **inchangé** |
| `.env.example` | **inchangé** (aucune variable nouvelle) |

```bash
cd api
uv add google-genai
```

Aucune autre dépendance. `httpx` reste en dépendance de développement (il ne sert qu'à
`TestClient` et à ce type de test).

---

## 7. Tests

### Unitaires, sans réseau ni clé (par défaut)

| # | Test | Ce qui est prouvé |
|---|---|---|
| 1 | `test_cle_absente_nomme_la_variable` | `GEMINI_API_KEY` retirée de l'environnement → `MissingAPIKey` dont le message contient `GEMINI_API_KEY` et **aucune** valeur |
| 2 | `test_requete_envoyee_avec_temperature_01` | `_gemini_client` remplacé par un faux client : le corps passé à `interactions.create` porte `model="gemini-3.1-flash-lite"`, `generation_config.temperature == 0.1`, et le `system_instruction` seulement quand `system` est fourni |
| 3 | `test_mappe_texte_et_tokens` | un faux objet interaction renvoie `text`, `provider`, `model`, `tokens_in`/`tokens_out` correctement mappés |
| 4 | `test_usage_absent_donne_zero` | interaction sans `usage` → 0/0, aucune exception (robustesse au renommage SDK) |
| 5 | `test_erreur_gemini_ne_fuit_pas_la_cle` | avec une fausse clé `test-fausse-cle-…`, une exception du SDK ne laisse pas cette chaîne dans le message d'erreur |

### Test d'intégration, marqué `@pytest.mark.integration`

Exécuté à la demande (`uv run pytest -m integration -v`), **sauté** si la clé est absente.

| # | Test | Ce qui est prouvé |
|---|---|---|
| 6 | Gemini réel | la clé du `.env` racine authentifie, `gemini-3.1-flash-lite` répond, la température 0.1 est acceptée, `text` non vide, `tokens_out > 0` |

C'est un vrai appel payant mais minuscule (prompt court, sortie courte) : **< 0,001 $** avec
`gemini-3.1-flash-lite`, et gratuit sur le free tier.

### Vérification manuelle finale

```bash
cd api
uv run python -c "from llm.clients import generate_gemini; r = generate_gemini('Réponds uniquement: OK'); print(r.provider, r.model, r.tokens_in, r.tokens_out, repr(r.text))"
```

On lit les compteurs de tokens réels : c'est la mesure, pas une supposition, du « modèle qui
consomme le moins ».

---

## 8. Critères d'acceptation

1. `cd api && uv run python -c "from llm.clients import generate_gemini; print('ok')"` → `ok`
   (module importable sans clé ni réseau).
2. `uv run pytest -v` : les 5 tests unitaires passent, **sans réseau ni clé**.
3. `uv run pytest -m integration -v` : `1 passed` sur l'appel réel Gemini (ou `skipped` avec la
   raison si la clé manque).
4. Appel réel : `generate_gemini("...")` renvoie `provider='gemini'`,
   `model='gemini-3.1-flash-lite'`, `tokens_out > 0`, texte non vide.
5. Aucune clé en clair : ni dans le code, ni dans un test, ni dans les logs, ni dans `git status`.
6. `/health` et le test de contrat existant toujours verts — aucune régression.
7. `uv.lock` mis à jour par `uv add` (pas d'installation à la main).

---

## 9. Risques et réponses prévues

| Risque | Probabilité | Réponse |
|---|---|---|
| `client.interactions` absent/refusé sur `gemini-3.1-flash-lite` | moyenne | repli décidé d'avance sur `client.models.generate_content` (§ 3), signature publique inchangée |
| `temperature` ignorée par le modèle | faible | constaté à l'exécution ; 3.1 n'est pas dans la liste des modèles qui l'ignorent |
| Modèle `gemini-3.1-flash-lite` retiré/renommé | faible | stable depuis mai 2026 ; changer la constante `GEMINI_MODEL` suffit |
| Free tier Gemini saturé (429) | moyenne | `LLMError` explicite ; le test d'intégration reste exécutable plus tard |
| Clé recopiée par erreur dans un test | écarté | aucune clé littérale : `monkeypatch.setenv` avec une valeur factice |
| `google-genai` incompatible 3.13 | écarté | `requires-python >=3.10`, vérifié sur PyPI |

---

## 10. Estimation

| Étape | Durée |
|---|---|
| `uv add google-genai` + `clients.py` | 3 min |
| Tests unitaires (5) | 2–3 min |
| Test d'intégration réel + mesure des tokens | 2 min |
| Contrôles Git | 1 min |
| **Total** | **~8 min** |

---

## 11. Résultat d'exécution (2026-10-22)

- `google-genai` **2.32.0** installé ; `uv.lock` à jour.
- `client.interactions.create(model=..., input=..., generation_config=...)` **accepté** par le SDK
  et par l'API : le repli vers `models.generate_content` prévu au § 3 n'a pas été nécessaire.
- Appel réel : `tokens_in=7`, `tokens_out=1`, `total_thought_tokens=0`, `total_tokens=8`,
  texte `"OK"`. Les champs `total_input_tokens` / `total_output_tokens` sont bien ceux du SDK.
- Un refus transitoire **503 « high demand »** a été observé une fois, puis l'appel suivant a
  réussi : le message remonte proprement via `LLMError`, sans fuite de clé.

Deux ajouts par rapport au code de référence du § 5, tous deux nécessaires aux critères :

1. **`_sans_cle()`** : le test « la clé ne fuit pas » exige que le message d'erreur du SDK soit
   expurgé avant de remonter dans `LLMError`.
2. **`[tool.pytest.ini_options] addopts = "-m 'not integration'"`** dans `api/pyproject.toml` :
   sans cela, `uv run pytest` lancerait l'appel réseau réel, alors que le critère 2 impose des
   tests unitaires sans réseau. `uv run pytest -m integration -v` force l'inclusion.
