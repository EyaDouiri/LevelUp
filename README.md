# LevelUp

Assistant personnel multi-agents pour la recherche de stage de fin d'études
(actuariat / data science / assurance) et la préparation de candidatures ciblées.
Construit comme un **projet portfolio** : architecture agentique réelle,
budget gratuit maîtrisé, et reprise sans perte après interruption.

Une **équipe de 7 agents** inspecte les offres, analyse ton profil, prépare des
dossiers sourcés (CV + lettre adaptés par offre), les fait contrôler, puis te
laisse **décider de l'envoi** — rien ne part automatiquement.

## L'équipe

| Agent | Rôle | Résultat |
|---|---|---|
| **Orchestrateur** | Pilote la mission, conserve l'état, relance après erreur | Mission reprise au dernier checkpoint |
| **Éclaireur (scout)** | Recherche multi-sources (Tavily, Adzuna, Welcome to the Jungle), round-robin par destination | Découvertes persistées immédiatement |
| **Enquêteur (verifier)** | Consulte chaque fiche réelle, extrait le JSON-LD / le texte | Offre `jobposting`, `page_read` ou `snippet_only` |
| **Analyste** | Compare l'offre au **profil complet** (expériences, projets, compétences) | Score expliqué, critères bloquants, inconnues |
| **Rédacteur (writer)** | Rédige le mail ciblé et le plan de CV **à partir de preuves** | Dossier avec `evidence_ids` vérifiés |
| **Contrôleur (critic)** | Relecture indépendante : refuse toute invention de fait | Corrections renvoyées à la rédactrice |
| **Archiviste** | Génère les PDF (CV + lettre) et enregistre le dossier | Documents à relire et valider |

Chaque agent reçoit et renvoie un **contexte partagé validé** (schémas Pydantic).
Une affirmation du CV est toujours reliée à une expérience, un projet ou une
compétence présents dans `profile.json`. Une information absente reste inconnue.

## Parcours d'un dossier

```
Recherche → déduplication (URL canonique) → vérification de la fiche
→ analyse/classement → ton choix → rédaction + contrôle → PDF
→ ta relecture → validation → brouillon Gmail (destinataire vide, à compléter)
```

L'envoi final reste **manuel, depuis Gmail**. Le brouillon est créé avec les
pièces jointes (CV + lettre) ; tu vérifies, tu complètes le destinataire, tu envoies.

## Garanties de qualité

- **Aucune invention** : le critique bloque tout chiffre, expérience ou condition
  d'embauche absent du profil. Le score d'adéquation ne devient jamais une
  probabilité d'embauche.
- **Déduplication** : URLs canoniques (LinkedIn, Indeed, tracking supprimé),
  doublons ignorés même si la mission reprend.
- **Reprise sûre** : chaque recherche est persistée immédiatement ; une coupure
  (budget, réseau, arrêt) garde les découvertes et reprend au bon endroit.
- **Budget gratuit** : quotas journaliers/mensuels Tavily et Groq, enregistrés
  atomiquement. Une mission s'interrompt proprement à la limite.
- **Sécurité** : serveur local uniquement, pas d'envoi de mail dans le code,
  aucun scraping de domaine privé.

## Installation (5-10 min)

```bash
cd job-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Remplis `.env` :
1. **TAVILY_API_KEY** — compte gratuit https://tavily.com (1000 recherches/mois, souvent 1000/mois gratuit).
2. **GROQ_API_KEY** — compte gratuit https://console.groq.com. Le modèle par défaut `openai/gpt-oss-20b` reste gratuit.
3. **GMAIL_CREDENTIALS_PATH** — optionnel, voir section Gmail.

### Gmail (optionnel, une fois)

1. https://console.cloud.google.com/apis/credentials → activer Gmail API.
2. Créer un identifiant OAuth « Application de bureau », télécharger `credentials.json`.
3. Depuis l'interface : Profil → Connecter Gmail (la première connexion ouvre un navigateur).
4. `token.json` est stocké localement.

## Utilisation

Interface web :

```bash
.\start.ps1          # construit le frontend puis lance api.py sur http://127.0.0.1:8000
```

Sinon, uniquement le backend :

```bash
.\.venv\Scripts\python.exe api.py --port 8000
```

Interface : **Opportunités** (pipeline), **Équipe** (activité des agents, journal,
reprise des missions, consommation), **Entreprises** (cibles), **Profil & limites**.
Un bouton **« Waky Waky »** en bas à droite donne le point du jour à la voix
(voir section ci-dessous).

### Suivi Excel (tableau de travail)

L'Excel est une **vue de travail** synchronisée ; SQLite reste la source de vérité.
Chaque colonne Statut dispose d'une liste déroulante et les lignes sont colorées
uniquement par **mise en forme conditionnelle** (jamais peintes en dur). Un Statut
déjà choisi à la main n'est jamais écrasé à l'export ; les nouvelles offres sont
ajoutées en bas. `Rejeté` → `rejected` et `Clôturé` → `archived` sont réinjectés en
base et exclus des prochaines missions.

```bash
.\.venv\Scripts\python.exe export_excel.py export data\suivi_candidatures.xlsx   # (re)construire le tableau
.\.venv\Scripts\python.exe export_excel.py sync  data\suivi_candidatures.xlsx    # relire les Statuts modifiés
```

Dans l'interface, les mêmes actions sont disponibles via les routes `POST /api/excel/export`
et `POST /api/excel/sync`.

### Déclenchement vocal « waky waky »

Le point du jour se lance à la voix en français : dis **« waky waky »** (ou clique
sur le bouton micro). Le navigateur (Web Speech API — aucun service payant) écoute
en continu en `fr-FR`, puis appelle `GET /api/voice` : le serveur résume les vraies
données du registre (nombre d'offres, meilleures par adéquation, statut de la
dernière mission) en **3-4 phrases orales courtes** via Groq. La voix de réponse est
aussi en français. Sans Groq (quota ou clé absente), un récapitulatif minimal est
renvoyé pour ne jamais rester muet.

Ligne de commande (une mission sans interface) :

```bash
.\.venv\Scripts\python.exe main.py --max 6 --queries 8 --assess 8
```

Tests :

```bash
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend; npm run build; npm run lint
```

## Structure actuelle

```
LevelUp/
├── engine.py            # Orchestrateur durable (checkpoint, reprise, annulation)
├── contracts.py         # Schémas échangés par les agents (Pydantic, strict)
├── providers.py         # Adaptateurs Tavily/Groq/Adzuna/Jooble (budget, cache, retries bornés)
├── research.py          # Plan de recherche, extraction JSON-LD, vérification
├── browse_jobs.py       # Welcome to the Jungle (API) + Stage.fr (compat)
├── application_documents.py  # Génération PDF (CV/lettre) + package .eml
├── export_excel.py      # Suivi Excel : export (statuts préservés, couleurs conditionnelles) + sync
├── gmail_gateway.py     # OAuth Gmail + brouillon (jamais d'envoi)
├── storage.py           # SQLite transactionnel (applications, runs, events, usage)
├── server.py            # API locale (http://127.0.0.1:8000) + /api/voice + veille quotidienne
├── profile.json         # Ton profil (nom, expériences, projets, compétences, critères)
├── frontend/            # React/Vite : pipeline, équipe, entreprises, profil + bouton « Waky Waky »
├── data/jamil.sqlite3   # Registre
└── tests/               # 85 tests hors-ligne (offline, aucun appel payant)
```

**Sources actives** : Tavily, Adzuna et Welcome to the Jungle. **France Travail n'est
plus utilisé** dans le pipeline (étapes de recherche, plan, vérification, `.env` exempts).
Les 5 offres France Travail déjà enregistrées restent visibles dans l'historique ;
les nouvelles missions n'en collectent plus.

## Ce qui reste du premier prototype

`swarm.py`, `search_offers.py`, `tailor_application.py`, `dedup.py`,
`save_output.py`, `companies.py`, `gmail_draft.py`, `dashboard.py` : ancien
parcours modularisé, conservé pour référence. L'équipe actuelle se trouve dans
`engine.py` + `providers.py` + `research.py` + `application_documents.py`.

## Prochaines étapes possibles

- **Recherche de l'email RH** sur les pages carrières (contact_candidates est déjà collecté).
- **Relances** : enregistrer la date d'envoi, proposer une relance à J+7.
- **Préparation d'entretien** : générer les questions techniques à partir des projets.
- **Veille automatique** : déjà disponible (settings.auto_watch), couplée au serveur.
- **SaaS université** : multi-comptes, une BDD par utilisateur, quotas par compte.

## ⚠️ Notes

- Relis chaque email avant envoi. Le LLM peut mal interpréter une offre.
- Ne partage jamais `.env` ni `token.json` (clés API + accès Gmail).
- Surveille `Profil & limites` : les quotas t'évitent de dépasser le tier gratuit.