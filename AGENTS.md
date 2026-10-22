# assistant-soc — instructions agents

## Autorisation avant développement

**Aucun développement n'est lancé sans autorisation explicite du propriétaire.**

- Écrire du code, créer ou modifier des fichiers de code, installer des
dépendances, lancer un serveur ou exécuter un test qui modifie l'état du projet
: uniquement après un « vas-y », « exécute », « autorisé » ou équivalent.
- Un plan, un fichier de plan ou une proposition ne vaut pas autorisation. Une
  réponse à une question de conception (choix d'un chemin, d'une librairie, d'un
  port) ne vaut pas autorisation non plus.
- En attendant : lire, analyser, documenter, écrire des plans et répondre aux
  questions sont permis.
- Si le propriétaire interrompt un travail en cours, s'arrêter net et ne pas
  reprendre de sa propre initiative.

## Architecture du projet (référence)

Arborescence cible validée par le propriétaire. Le rôle indiqué à droite de
chaque fichier décrit **ce que le fichier doit devenir**, pas un état actuel :
les fichiers listés ci-dessous n'existent pas encore et aucune implémentation ne
doit être supposée acquise.

```
assistant-soc/
├── api/
│   ├── main.py            FastAPI app
│   ├── routers/           /triage, /chat, /summarize
│   ├── llm/               clients cloud + local
│   ├── rag/               chunking, embeddings, retrieve
│   └── pyproject.toml     deps gérées par uv
├── frontend/              React (jour 6)
├── data/
│   ├── tickets.json       500 tickets SOC fictifs
│   ├── runbooks/          30 runbooks markdown
│   └── cve.json           200 CVE 2024-2026
└── terraform/             main.tf (jour 3)
```

Règles qui en découlent :

- **Une seule application backend**, dans `api/` : point d'entrée `main.py`,
  routes découpées par domaine dans `api/routers/` (`/triage`, `/chat`,
  `/summarize`). Ne pas créer de second serveur ailleurs.
- **Les appels aux modèles passent tous par `api/llm/`** (clients cloud + local).
  Aucun appel LLM direct depuis un routeur ou le frontend : c'est là que les
  clés du `.env` sont lues.
- **La couche RAG vit dans `api/rag/`** : `chunking`, `embeddings`, `retrieve`.
- **Dépendances Python gérées par `uv`** via `api/pyproject.toml`. Pas de
  `requirements.txt`, pas de `pip install` à la main.
- **Les données de travail sont dans `data/`** : `tickets.json`, `cve.json` et
  `data/runbooks/` (markdown). Ce sont des jeux de données fictifs de démo —
  jamais des données client réelles, jamais de secret dedans.
- **Le frontend React vit dans `frontend/`** et ne fait que consommer l'API.
- **L'infrastructure vit dans `terraform/`** (`main.tf`). Aucune ressource cloud
  créée sans demande explicite.

## Secrets et clés API

**Toutes les clés API de ce projet vivent dans `.env` à la racine.** C'est le
propriétaire du projet qui remplit ce fichier au fil du projet ; les agents y
lisent les clés dont ils ont besoin et n'en créent pas d'autres ailleurs.

Décision du propriétaire (2026-10-22) : **un seul `.env`, à la racine du
dépôt.** Pas de `api/.env`, pas de `.env` dans un sous-dossier. Le code du
backend résout ce fichier par un chemin explicite vers le parent, jamais par une
recherche implicite dans le répertoire courant.

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

## Conventions

- Ne jamais démarrer un serveur, une installation ou une génération de fichiers
  sans autorisation (cf. « Autorisation avant développement »).
- Créer une variable documentée : `NOM_DE_LA_CLE=` dans `.env.example`, valeur
  réelle dans `.env`.
- Nommage : `UPPER_SNAKE_CASE`, suffixe `_API_KEY`, `_TOKEN`, `_SECRET`,
  `_URL` selon la nature.
