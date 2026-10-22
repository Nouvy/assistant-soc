# Plan — Initialisation du backend `api/` (FastAPI + uv)

> Statut : **exécuté le 2026-10-22** — les étapes 1 à 6 sont faites, les trois tests passent.
> Décision `.env` : tranchée, le fichier reste à la racine (section 2).
> Objectif : un serveur FastAPI qui démarre sur `http://127.0.0.1:8000` avec rechargement
> automatique, expose `GET /health`, et dont le bon fonctionnement est vérifié automatiquement.

---

## 1. Périmètre

### Dans ce lot

- Projet Python initialisé dans `api/` avec **uv** (`api/pyproject.toml`).
- Dépendances : `fastapi`, `uvicorn[standard]`, `python-dotenv`.
- Chargement des variables d'environnement depuis le **`.env` racine** (section 2).
- Un seul endpoint : `GET /health` → `{"status": "ok"}`.
- Serveur lancé sur le port **8000** avec `--reload`.
- Vérification automatisée du bon fonctionnement de `/health`.

### Hors périmètre (lots suivants)

- `/triage`, `/chat`, `/summarize` et tout `api/routers/` fonctionnel.
- `api/llm/` et `api/rag/` (les dossiers restent vides).
- Frontend, Terraform, données de `data/`.
- Toute authentification, base de données, CORS applicatif.

---

## 2. Décision actée : `.env` reste à la racine

**Décision du propriétaire : un seul `.env`, à la racine du dépôt.** Pas de `api/.env`.
Cette décision est enregistrée dans [AGENTS.md](AGENTS.md), section « Secrets et clés API ».

Conséquences concrètes :

- Le fichier existant `./.env` (qui contient déjà `GEMINI_API_KEY`) reste l'unique source des
  clés, à sa place, sans déplacement ni recopie.
- `api/main.py` **ne fait pas** un `load_dotenv()` implicite : il résout le chemin du parent,
  sinon un `uv run` lancé depuis `api/` chercherait un `.env` dans `api/` et n'en trouverait pas.

```python
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)
```

- Aucun `.env` ne doit apparaître dans un sous-dossier. Si un jour `python-dotenv` ou un autre
  outil en cherche un localement, c'est le chemin explicite ci-dessus qui fait foi.
- `.env` reste ignoré par Git ; `.env.example` à la racine reste la documentation des noms de
  variables.

Option écartée : `api/.env`, qui aurait dupliqué l'emplacement des secrets et contredit la règle
mémoire « toutes les clés à la racine ».

---

## 3. Précisions techniques

- **`python-env` n'est pas un paquet** : le paquet qui charge un `.env` est **`python-dotenv`**.
  Le plan part sur celui-là.
- **`uvicorn[standard]`** plutôt que `uvicorn` seul : l'extra installe `watchfiles`, sans lequel
  `--reload` ne détecte pas les modifications de fichiers de façon fiable.
- **`--reload` = développement uniquement.** Jamais en production.
- **Adresse d'écoute `127.0.0.1`** (et non `0.0.0.0`) : le serveur reste sur ta machine.
  Port 8000 vérifié libre avant de commencer.
- **Python 3.13**, déjà présent sur la machine (3.13.12 Homebrew) — aucun téléchargement d'interpréteur.

---

## 4. Étapes d'exécution

### Étape 1 — Initialiser le projet uv dans `api/`

État réel au 2026-10-22 : un `uv init` a déjà été exécuté avant l'arrêt de la session. Il a
produit `api/pyproject.toml`, `api/.python-version`, `api/README.md` (vide) et un paquet
`api/src/assistant_soc_api/` avec un point d'entrée `assistant-soc-api`, sans aucune dépendance.
Ce n'est pas la forme voulue (l'architecture du projet veut `api/main.py`, pas un paquet `src/`).
Deux façons de finir proprement, à choisir à l'exécution :

- **repartir d'un socle minimal** (`uv init --bare`, puis écrire un `pyproject.toml` explicite), ou
- **garder le fichier généré** et retirer ce qui ne sert pas : dossier `src/`, bloc
  `[project.scripts]` et `[build-system]`, `README.md` vide.

Le `.python-version` (`3.13`) et le nom `assistant-soc-api` sont conservés dans les deux cas.
Le `.gitkeep` d'`api/` devient inutile dès que le dossier a du contenu.

### Étape 2 — Dépendances

```bash
uv add fastapi "uvicorn[standard]" python-dotenv
uv add --dev pytest httpx
```

`pytest` et `httpx` servent uniquement aux tests (voir étape 5) et n'entrent pas dans les
dépendances d'exécution.

### Étape 3 — Code applicatif

| Fichier | Rôle |
|---|---|
| `api/main.py` | charge le `.env`, crée `app = FastAPI(...)`, définit `GET /health` |
| `api/tests/test_health.py` | test de contrat de `/health` via `TestClient` |
| `api/tests/__init__.py` | rend `api/tests` importable par pytest |
| `.env.example` | ajout des **noms** `API_HOST=` et `API_PORT=` (aucune valeur) |

Contenu de référence pour `api/main.py` :

```python
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

app = FastAPI(title="assistant-soc API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Pourquoi `/health` reste dans `main.py`** : l'architecture du projet réserve `api/routers/`
aux domaines métier (`/triage`, `/chat`, `/summarize`). Un endpoint d'infrastructure n'y a pas
sa place. Aucun autre endpoint n'est ajouté.

### Étape 4 — Lancer le serveur

```bash
cd api
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- Exécution en tâche de fond, identifiant de processus enregistré.
- Sortie redirigée vers `api/.logs/uvicorn.log` (dossier ignoré par Git).
- Documentation interactive disponible sur `/docs`.

### Étape 5 — Vérification du bon fonctionnement de `/health`

Trois niveaux, du plus isolé au plus réel. Aucun ne remplace les autres.

| # | Test | Ce qui est prouvé | Commande |
|---|---|---|---|
| 1 | Unitaires | le contrat de la route, sans réseau | `uv run pytest -v` |
| 2 | Requête réelle | le serveur HTTP démarre, écoute et répond correctement | voir ci-dessous |
| 3 | Rechargement | `--reload` redémarre bien le worker sur modification | édition puis restauration d'une ligne |

Test 2 — on n'accepte pas un simple `200` : on exige le code, le corps exact et le type MIME.

```bash
curl -sS -o /tmp/health.json -w '%{http_code} %{content_type}\n' http://127.0.0.1:8000/health
cat /tmp/health.json   # attendu : {"status":"ok"}
```

Test 3 — on modifie une chaîne inoffensive dans `main.py`, on attend le message
`Reloading...` dans `api/.logs/uvicorn.log`, on re-teste `/health`, puis on restaure le fichier
et on vérifie une dernière fois.

### Étape 6 — Contrôles finaux

- `git status` : aucun `.venv`, `__pycache__`, `.env` ou `api/.logs/` ne doit apparaître.
- `git check-ignore api/.venv api/.logs` : doit répondre par les deux chemins.
- Vérifier que `.env` et `.env.example` restent cohérents avec les variables réellement lues.

---

## 5. Critères d'acceptation

Le lot est terminé lorsque **tous** ces points sont vrais :

1. `cd api && uv run python -c "import fastapi, dotenv, uvicorn"` réussit.
2. `uv run pytest -v` : 1 test, 1 succès.
3. `curl http://127.0.0.1:8000/health` renvoie HTTP `200`, `application/json`, `{"status":"ok"}`.
4. Le serveur tourne encore à la fin des tests (processus identifié, port 8000 en écoute).
5. Une modification de `main.py` provoque un rechargement visible dans les logs, et `/health`
   répond toujours après ce rechargement.
6. Aucun secret n'apparaît dans un fichier versionné ni dans les logs.

---

## 6. Risques et réponses prévues

| Risque | Probabilité | Réponse |
|---|---|---|
| `--reload` inopérant (watchfiles absent) | faible | l'extra `[standard]` l'installe ; test 3 le prouve |
| Port 8000 occupé par un autre processus | faible | port vérifié libre ; sinon je signale avant d'en changer |
| Portée 8000 non libérée après tests | moyenne | serveur laissé volontairement actif, processus identifiable |
| `.venv` créé dans `api/` et commité | faible | déjà couvert par `.gitignore`, vérifié en fin de lot |
| Confusion `.env` racine / `api/` | écarté | décision actée : un seul `.env`, à la racine |

---

## 7. Estimation

| Étape | Durée |
|---|---|
| 1–2 (init + dépendances) | 2–4 min |
| 3 (code + test) | 2 min |
| 4–5 (lancement + 3 tests) | 2–3 min |
| 6 (contrôles) | 1 min |
| **Total** | **~8 min** |

---

## 8. Ce qu'il me faut de toi

1. **L'autorisation de démarrer** — elle seule débloque les étapes 1 à 6. Conformément à
   [AGENTS.md](AGENTS.md), aucune ligne de code, aucune installation et aucun serveur ne sont
   lancés avant ce feu vert explicite.
2. **Le choix du socle** à l'étape 1 : `uv init --bare` + `pyproject.toml` écrit à la main
   *(recommandé, plus propre)*, ou conservation du `pyproject.toml` déjà généré après nettoyage
   du dossier `src/`. Si tu ne tranches pas, je pars sur la première option.

Le `.env` n'est plus une question ouverte : il reste à la racine.

Aucune clé supplémentaire n'est nécessaire : l'endpoint `/health` ne consomme aucune API
externe, et le chargement du `.env` ne dépend pas de son contenu.
