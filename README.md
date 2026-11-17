# Banc d'essai de Noa : livrable d'examen

> **Dépôt réservé à l'évaluation. Ne pas diffuser, ne pas forker, ne pas réutiliser.**

Ce dépôt est le livrable du projet final de la certification **Data Engineer de Liora (ex-DataScientest)**, soutenu le 19 novembre 2026 par Thomas Cauhapé et Vincent.

## Pourquoi ce dépôt existe

Le banc d'essai a été développé dans un dépôt privé, aux côtés de Noa, une application qui appartient à son auteur et n'est pas diffusée. Ce dépôt en est un **extrait** :

- il contient le banc d'essai (orchestration, entrepôt, tableaux de bord, outils de notation) ;
- il ne contient **ni le code de Noa, ni ses prompts, ni aucune donnée d'utilisateur réel** ;
- il est publié **sans l'historique du dépôt d'origine**, pour qu'aucun fichier ni secret de cet historique n'en sorte. Le récit des étapes et la part de chacun sont décrits dans le rapport final.

## Ce que vous pouvez en faire

- le lire et l'exécuter pour évaluer le projet ;
- lancer la démonstration, qui tourne **sans Noa, sans clé d'API et à 0 $** (juges factices, notes rejouées).

## Ce que vous ne pouvez pas en faire

Le contenu est protégé : **tous droits réservés**. Sans accord écrit des auteurs, il est interdit de le copier, le diffuser, le publier, le modifier pour un autre usage ou l'utiliser à des fins commerciales. Voir `LICENSE`.

## 1. Démarrer la pile

### Prérequis
- Docker Engine et Docker Compose v2.24+ (Docker Desktop ou Docker Linux).
- Aucun environnement Python local n'est nécessaire : tous les services s'exécutent dans les conteneurs.

### Commandes

**Premier démarrage (construction de l'image) :**
```sh
docker compose up --build
```

**Démarrages suivants :**
```sh
docker compose up
```

*(Optionnel)* Personnaliser les ports de la machine hôte :
```sh
cp .env.example .env   # puis éditer BANC_*_PORT selon vos besoins
```
Par défaut, les ports locaux utilisés sont **18080** (Airflow), **13030** (Metabase) et **18081** (Rapports & Doc dbt).

---

## 2. État initial après démarrage

Au premier démarrage, la pile s'initialise automatiquement et se place dans un **état sain, vierge de données** :
- **Postgres :** bases et utilisateurs créés, schéma de données initialisé (tables vides).
- **Airflow :** prêt et accessible sans mot de passe, DAG `banc_chaine` actif et prêt à être déclenché.
- **Metabase :** compte administrateur initialisé, connexion à la base de données établie, sans carte ni tableau de bord pré-rempli.
- **Serveur de résultats (nginx) :** page d'accueil disponible.

Aucun dialogue, run ou incident n'existe encore. La génération des données intervient lors du déclenchement du DAG.

---

## 3. Lancer une campagne d'évaluation

1. Ouvrir l'interface Airflow : **http://127.0.0.1:18080/dags/banc_chaine**
2. Cliquer sur le bouton **Trigger DAG** (les paramètres par défaut lancent la campagne `S01` avec 5 tours et l'ensemble des personas).
3. Suivre l'exécution des 10 tâches séquentielles du pipeline :
   ```text
   preparer → personas → dialogues → controles → juges → charger → accord → rapport → metabase → verifier
   ```

---

## 4. Consulter les résultats

Une fois le DAG exécuté avec succès, les résultats sont accessibles sur les différentes interfaces :

| Outil | Adresse par défaut | Description |
|---|---|---|
| **Airflow** | http://127.0.0.1:18080/dags/banc_chaine | Suivi et statut des tâches d'orchestration |
| **Metabase** | http://127.0.0.1:13030 | Tableau de bord de simulation (`Simulations Noa`) |
| **Rapport HTML** | http://127.0.0.1:18081/S01/rapport_v0.html | Synthèse visuelle de la campagne (ici `S01`) |
| **Documentation dbt** | http://127.0.0.1:18081/dbt/ | Modèle de données en étoile et lignage |

**Identifiants Metabase :**
- Email : `demo@banc.local`
- Mot de passe : `demo-banc-noa-2026`

Mots de passe de démonstration, locaux uniquement : tous ceux qui figurent dans ce dépôt (README, `docker-compose.yml`, code et tests) ne protègent que cette pile locale et ne doivent jamais servir ailleurs.

Le tableau de bord Metabase créé par la tâche `metabase` affiche l'état d'exécution, la distribution des notes factices par axe d'évaluation, les incidents du scénario, ainsi que les mesures d'accord entre juges (alpha de Krippendorff et kappa quadratique).

---

## 5. Gestion de la pile (arrêt, relance, export)

- **Arrêter la pile :**
  ```sh
  docker compose stop
  ```
- **Arrêter et supprimer les conteneurs (données conservées) :**
  ```sh
  docker compose down
  ```
- **Réinitialiser complètement la pile (effacer toutes les données) :**
  ```sh
  docker compose down --volumes
  ```
- **Exporter les résultats sur la machine hôte :**
  ```sh
  docker compose cp airflow-scheduler:/opt/banc/runs ./resultats-runs
  ```

---

## 6. Paramètres de campagne (Trigger DAG)

Lors du déclenchement manuel dans Airflow, vous pouvez personnaliser les paramètres suivants :

| Paramètre | Défaut | Description |
|---|---|---|
| `campagne` | `S01` | Identifiant de la campagne (`S01` à `S999`) |
| `personas` | `claire,jeanpierre,lucas,mehdi,nadia` | Liste des personas synthétiques à jouer |
| `tours` | `5` | Nombre de tours de dialogue (1 à 5) |
| `bras` | `les_deux` | `sans_onboarding`, `avec_onboarding` ou `les_deux` |
| `paralleles` | `4` | Nombre de workers de simulation en parallèle (1 à 4) |
| `echelle_delais` | `0` | Facteur de délai de réponse simulé (`0` = rapide) |
| `plafond_noa_usd` | `1` | Plafond budgétaire alloué au scénario |
| `cout_estime_dialogue_usd` | `0.04` | Coût estimé par dialogue simulé |

---

## 7. Tests unitaires locaux (hors Docker)

Les tests statiques et unitaires s'exécutent avec Python 3.12 et [uv](https://docs.astral.sh/uv/) 0.12.9, la version qui a écrit `uv.lock` (`pyproject.toml` l'impose) :

```sh
uv sync --frozen          # environnement .venv, versions exactes de uv.lock
uv run poe check-all      # lint, format, mypy, tests avec couverture, pip-audit, bandit
uv run poe test           # ou une tâche seule : lint, format-check, mypy, security, bandit
uv run python -m infra.verifier_sources
```

Les mêmes contrôles tournent dans la CI GitHub Actions (`.github/workflows/ci.yml`) à chaque pull request et à chaque push sur `main` ; chacun y est bloquant.
