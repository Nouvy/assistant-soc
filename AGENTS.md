# assistant-soc — instructions agents

## Secrets et clés API

**Toutes les clés API de ce projet vivent dans `.env` à la racine.** C'est le
propriétaire du projet qui remplit ce fichier au fil du projet ; les agents y
lisent les clés dont ils ont besoin et n'en créent pas d'autres ailleurs.

Règles :

- Lire une clé depuis `.env` (ou `os.environ` en runtime) — **jamais** la
  recopier en dur dans le code, un test, une fixture ou un fichier de config.
- **Ne jamais afficher, logguer, ni citer une valeur de clé** dans le chat, un
  rapport, un message d'erreur ou un commit. On peut nommer la variable
  (`GEMINI_API_KEY`), jamais sa valeur.
- Ne jamais modifier ni écraser une ligne existante de `.env` sans demande
  explicite. Pour ajouter une variable, ajouter une ligne, sans toucher aux
  autres.
- `.env` est ignoré par Git (`.gitignore`) et ne doit **jamais** être commité,
  poussé, ou copié dans un artefact de build / image Docker.
- Si une clé manque au moment de l'implémentation : implémenter le code qui la
  consomme, puis **demander au propriétaire de l'ajouter à `.env`** au lieu
  d'inventer une valeur ou de bloquer.
- Pour documenter une nouvelle variable, ajouter son **nom** (sans valeur) dans
  `.env.example`.

## Architecture du projet (cible)

Structure de référence — c'est le plan à respecter, pas une suggestion. Les
dossiers `api/`, `frontend/`, `data/` et `terraform/` existent déjà à la racine.

```
assistant-soc/
├── api/
│   ├── main.py            # app FastAPI
│   ├── routers/           # /triage, /chat, /summarize
│   ├── llm/               # clients cloud + local
│   ├── rag/               # chunking, embeddings, retrieve
│   └── pyproject.toml     # deps gérées par uv
├── frontend/              # React (jour 6)
├── data/
│   ├── tickets.json       # 500 tickets SOC fictifs
│   ├── runbooks/          # 30 runbooks markdown
│   └── cve.json           # 200 CVE 2024-2026
└── terraform/main.tf      # jour 3
```

Rôles :

- `api/` — backend FastAPI. Trois routeurs métier (`/triage`, `/chat`,
  `/summarize`), une couche `llm/` qui abstrait les fournisseurs cloud **et**
  local derrière une interface commune, une couche `rag/` pour le découpage,
  les embeddings et la recherche. Dépendances via **uv** (`pyproject.toml`),
  pas de `requirements.txt`.
- `frontend/` — interface React, prévue en dernier (jour 6).
- `data/` — jeu de données fictif et versionné servant à alimenter le RAG et
  les démos : tickets SOC, runbooks, CVE.
- `terraform/` — infrastructure as code, un `main.tf` (jour 3).

Ordre de construction indicatif : `api/` d'abord, puis `terraform/` (jour 3),
`frontend/` en dernier (jour 6). Ne pas créer de dossiers ou de fichiers
structurels hors de ce plan sans que le propriétaire le demande.

`docs-public/` est en plus du plan : c'est le dossier des documents destinés à
être publiés sur GitHub (schémas, captures, docs lisibles). Les visuels du
projet y vont, pas à la racine ni dans `data/`.

## Conventions

- Créer une variable documentée : `NOM_DE_LA_CLE=` dans `.env.example`, valeur
  réelle dans `.env`.
- Nommage : `UPPER_SNAKE_CASE`, suffixe `_API_KEY`, `_TOKEN`, `_SECRET`,
  `_URL` selon la nature.
