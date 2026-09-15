# Bracket22 Mini — données réelles

## Démarrage recommandé

```sh
uv sync --frozen
./run-real.sh
```

Le script sélectionne Yahoo Finance et une base **bracket22-real.db** distincte de la démonstration. Le tableau de bord reste sur http://127.0.0.1:8000. Pour le passage quotidien : `MARKET_DATA_PROVIDER=yahoo uv run python -m bracket22.scheduler`. Docker Compose sélectionne également Yahoo, avec un nouveau volume PostgreSQL `postgres_real_data`.

Le connecteur `YahooProvider` utilise yfinance pour les actions/ETF, BTC-USD, ETH-USD et EURUSD=X (inversé pour USD→EUR). Aucun abonnement ni secret requis. Source communautaire non contractuelle, destinée ici à la recherche personnelle ; pas de flux temps réel ni de garantie de disponibilité. Documentation : [yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html), [calendriers boursiers](https://github.com/gerrymanoim/exchange_calendars).

- Uniquement les bougies journalières clôturées : calendrier XNYS, jours fériés et clôtures anticipées, marge de publication de 30 minutes ; crypto clôture à minuit UTC.
- Taux FX issu d'une journée UTC terminée ; date et source conservées dans le snapshot. Rejet au-delà de quatre jours calendaires d'ancienneté.
- Lots téléchargés conservés dans `market-cache/`, réutilisés pendant l'heure courante. Aucune substitution synthétique en cas de panne. Les lots antérieurs restent consultables, mais ne servent pas à masquer une panne.
- OHLC Yahoo avec `auto_adjust=False` : prix ajustés des splits, non ajustés des dividendes. Les splits publiés après une ouverture adaptent les unités paper et les rendements différés. Les dividendes ne sont pas crédités ; les statistiques sont des rendements de prix, pas des rendements totaux.
- Les mesures J+n partent de la clôture de référence sauvegardée : une analyse avant ouverture inclut la séance qui va commencer. Les snapshots passés restent immuables malgré les révisions possibles du fournisseur.
- Une base est liée à sa source au premier usage : le changement de source dans un journal existant est refusé. L'ancien fichier `bracket22.db` reste la démonstration synthétique ; ne pas le renommer en base réelle.
- Les agents restent déterministes et leurs scores/confiances heuristiques. Cette étape connecte les **données**, pas un LLM.

Configuration via variables d'environnement (le fichier `.env.example` est un exemple, pas chargé automatiquement par le lancement local). Pour revenir volontairement à la démonstration : `MARKET_DATA_PROVIDER=synthetic uv run uvicorn bracket22.app:app --port 8000`.

## Documentation initiale du mode synthétique

Les conventions ci-dessous sur les jours ouvrés simples et le FX 0,92 concernent uniquement ce mode ; les conventions réelles ci-dessus prévalent lorsque Yahoo est sélectionné.

# Bracket22 Mini

Desk de recherche personnel, **paper trading uniquement**. Capital initial : **100 000 EUR**. Aucun connecteur, identifiant ou chemin d'exécution d'ordre réel.

## Lancement local

Installer [uv](https://docs.astral.sh/uv/getting-started/installation/), puis dans ce dossier :

```sh
uv sync --frozen
uv run uvicorn bracket22.app:app --host 127.0.0.1 --port 8000
```

Ouvrir http://127.0.0.1:8000 (interface) ou http://127.0.0.1:8000/docs (API). Python 3.12 est téléchargé par uv si nécessaire. Le mode local utilise SQLite et des données synthétiques sans clé API. Lancer « Analyse quotidienne », ouvrir une fiche, puis approuver ou rejeter manuellement une proposition. Une analyse ne modifie jamais le portefeuille. Pour automatiser le passage quotidien à partir de 07:00 UTC, lancer dans un second terminal : `uv run python -m bracket22.scheduler` (le processus doit rester actif).

## PostgreSQL

```sh
docker compose up --build -d
```

Interface : http://127.0.0.1:8000. Compose démarre PostgreSQL, le backend et un processus quotidien séparé. Les données persistent dans le volume `postgres_data`. Autre possibilité : fournir `DATABASE_URL=postgresql+psycopg://...` à la commande uvicorn. Le schéma initial versionné est créé au démarrage ; aucune migration destructive automatique.

```sh
uv run python -m pytest -q
uv run ruff check bracket22 tests
# Tests identiques sur une base PostgreSQL de test dédiée :
TEST_DATABASE_URL=postgresql+psycopg://... uv run python -m pytest tests/test_postgres.py -q
```

## Architecture

`MarketDataProvider → DataEngine → Steffi / Desmond → Red Team → Houston → RiskEngine → validation humaine → PaperBroker`.

- `core.py` : univers, modèles Pydantic, provider abstrait, données reproductibles, outils scientifiques, règles de risque.
- `agents.py` : rôles séparés qui appellent les outils, sans calcul d'indicateurs dans les agents. Adaptateur de démonstration déterministe, aucun appel LLM. Les scores et confiances sont des heuristiques non calibrées, pas des probabilités validées.
- `storage.py` : snapshots/rapports append-only et journal d'événements chaîné SHA-256. Triggers SQL interdisant les modifications/suppressions ; PostgreSQL interdit aussi TRUNCATE. Les écritures métier sont sérialisées par verrou transactionnel PostgreSQL ou BEGIN IMMEDIATE SQLite.
- `service.py` : orchestration, idempotence quotidienne, évaluation différée, valorisation EUR et cycle des positions.
- `app.py` : API et interface minimale servie sur la même origine.
- `scheduler.py` : exécute une fois par date UTC, puis actualise les mesures différées. Reprise automatique après redémarrage grâce à l'idempotence du stockage.

La V1 sert une interface HTML/JavaScript légère depuis FastAPI. Next.js/React/Vercel ne sont pas nécessaires à ce stade ; l'API permet leur ajout ultérieur. Aucun déploiement public n'est effectué.

## Données et conventions

20 actifs : QQQ, SPY, AAPL, MSFT, NVDA, AMZN, META, GOOGL, AVGO, TSLA, AMD, NFLX, COST, PLTR, CRWD, BTC, ETH, GLD, TLT, IWM.

Les OHLCV sont **synthétiques**, ancrés au 01/01/2023, et ne changent pas quand la date d'analyse avance. L'historique ne contient jamais de date postérieure à la décision. Actions/ETF : lundi–vendredi, sans calendrier de jours fériés ; crypto : tous les jours. Aucune prétention à reproduire des cotations, ajustements de dividendes ou liquidité réelle. USD→EUR synthétique fixé à 0,92, explicitement conservé avec chaque décision et transaction. Remplacer `MarketDataProvider.history` et `fx_to_eur` pour une source réelle, y compris sa politique de clôture/fraîcheur et de calendrier. Le mode actuel doit rester nommé `synthetic` tant que ce travail n'est pas fait.

RSI de Wilder, SMA/EMA, ATR de Wilder, momentum, force relative au SPY, breakout, volumes, drawdown ; volatilité, Sharpe, Sortino, corrélation, régression OLS et régime. Backtest long SMA50>SMA200 sur observations espacées de 20 séances, retour à 20 séances, 20 points de base de coûts aller-retour ; aucun apprentissage sur les résultats futurs. Bootstrap à graine fixe et quantiles descriptifs, sans promesse prédictive. Les scénarios Houston proviennent des quantiles historiques calculés par les outils. Red Team signale notamment échantillon limité, signaux contradictoires, corrélation et données événementielles absentes.

Les rendements J+1/J+5/J+20/J+60 utilisent les **barres futures de l'actif** après la date de décision. Le benchmark SPY utilise la dernière clôture disponible à la date de mesure (notamment le week-end crypto). Les mesures immuables ne sont ajoutées qu'une fois arrivées à maturité, via l'analyse quotidienne ou `POST /performance/refresh`. Toutes les décisions sont mesurées, même rejetées, pour limiter le biais de sélection. Les statistiques des spécialistes portent sur leurs signaux longs ; celles de Red Team sur les propositions qu'il laisse continuer. La calibration porte sur une confiance heuristique et doit être interprétée comme diagnostic expérimental.

Le portefeuille est valorisé aux derniers prix connus et au FX provider ; toute donnée manquante/périmée empêche une approbation. Pas de levier ni de vente à découvert. Taille par défaut : 5 % de l'equity. Fractions d'unités autorisées ; simulation au prix de référence, sans frais/slippage d'exécution. Fermeture manuelle ; stop théorique et horizon sont enregistrés, sans déclencher une vente automatique.

## Risque et validation

Limites : position 5 %, secteur 25 %, crypto 10 %, 3 nouvelles positions par jour, score Houston ≥7, confiance ≥60 %, rejet des données manquantes ou périmées. La fraîcheur attend la dernière date ouvrée (tous les jours pour crypto) du calendrier synthétique. Une proposition n'est approuvable que le jour UTC de son analyse. Le moteur réévalue les expositions actuelles dans la transaction d'approbation ; les appels concurrents ne contournent pas les limites. Une décision humaine est définitive ; une nouvelle analyse le lendemain produit une nouvelle proposition.

Les ETF sont classés par poche (`Broad ETF`, `Growth ETF`, etc.), sans transparisation des sociétés sous-jacentes. Les limites sectorielles ne constituent donc pas un contrôle complet des concentrations économiques.

## API

GET `/assets`, `/market/{symbol}`, `/reports/latest`, `/reports/{symbol}`, `/opportunities`, `/risks`, `/portfolio`, `/performance`, `/agents/performance`, `/journal`, `/health`.

POST `/analysis/{symbol}`, `/analysis/run-daily`, `/performance/refresh`, `/proposals/{id}/review` (JSON `{"action":"approve"}` ou `{"action":"reject","reason":"..."}`), `/portfolio/{symbol}/close`.

Une analyse identique (symbole/date/version) renvoie le rapport existant. Les erreurs d'un actif dans le run quotidien sont isolées et remontées dans `errors`. Les refus de risque à l'approbation sont journalisés. Les requêtes sur un actif hors univers sont refusées.

## Exploitation

Application personnelle locale. Avant exposition réseau, définir `BRACKET22_API_TOKEN` (env) et utiliser HTTPS via un reverse proxy. L'interface demande le jeton et le conserve uniquement en mémoire ; l'API attend `Authorization: Bearer ...`. Pas de comptes multi-utilisateurs. Les secrets restent au backend. Sauvegarder PostgreSQL et exporter le journal pour disposer d'une preuve externe : un administrateur de base peut désactiver les triggers et recalculer la chaîne. L'immutabilité protège les opérations applicatives et SQL ordinaires, pas un administrateur hostile.

Sources techniques : [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [triggers PostgreSQL](https://www.postgresql.org/docs/current/trigger-definition.html).

## Reprise et suivi des traitements — 14 septembre 2026

Le tableau de bord affiche désormais le dernier traitement quotidien, le dernier succès et les erreurs. Ces informations sont également disponibles sur `GET /operations` et persistent dans le journal (`DAILY_RUN`). Une erreur de données ou de mesure empêche d'annoncer un succès global.

Un nouveau lancement conserve les analyses valides de la journée. Il retente les analyses dont les données étaient absentes ou périmées, avec un nouveau rapport lié à l'ancien par `supersedes`. Aucune ancienne décision n'est modifiée. Les mesures J+1/J+5/J+20/J+60 excluent ces rapports invalides et vérifient les séances manquantes avant de calculer les rendements.

Pour lancer le planificateur local, dans un second terminal à la racine du projet :

```sh
MARKET_DATA_PROVIDER=yahoo .venv/bin/python -m bracket22.scheduler
```

Il analyse après 07:00 UTC, retente les traitements incomplets après une heure et retrouve le dernier succès après redémarrage. Il faut conserver ce processus en fonctionnement, avec le Mac allumé et éveillé. Lancer une seule instance du planificateur ; Docker Compose contient déjà ce service. Le bouton d'analyse permet une relance manuelle immédiate. Aucun achat paper n'est automatique.

## Suivi des positions et sorties

Le portefeuille distingue gains réalisés à la clôture et gains latents, en EUR avec le change du provider. Chaque position présente son entrée, son dernier cours daté, sa valeur, son poids, sa durée en jours et séances, son stop et son horizon. Le prix d'entrée affiché et le stop sont ajustés des divisions d'actions ; le journal conserve les montants originaux.

Les alertes `stop_reached` et `horizon_reached` sont disponibles dans `GET /portfolio`, champ `positions[].exit_alerts`. Le stop est comparé à la dernière clôture disponible : aucun suivi intrajournalier, ordre stop ou exécution automatique. L'horizon compte les séances suivant le cours de référence de l'entrée (jours cotés pour actions, jours calendaires pour crypto). Les alertes sont l'état actuel, pas une notification envoyée ni un historique des franchissements.

Données absentes : cours et gain latent indisponibles, valorisation de secours au montant investi explicitement indicative. Données périmées : dernière valorisation datée, aucune alerte de sortie présentée comme actuelle. L'interface désactive la clôture tant que les données ne sont pas à jour ; le serveur les contrôle de nouveau lors de la demande.

## Agents LLM OpenAI

La configuration demandée est implémentée dans `bracket22/llm.py` :

| Agent | Modèle | Effort |
|---|---|---|
| Steffi | gpt-5.6-luna | max |
| Desmond | gpt-5.6-luna | max |
| Red Team | gpt-5.6-sol | high |
| Houston | gpt-5.6-sol | high |

Le backend charge `OPENAI_API_KEY` et `AGENT_MODE=llm` depuis `.env.local` à la racine du projet, ou depuis les variables d'environnement qui ont priorité. Le fichier est ignoré par Git et exclu du contexte Docker. Ne jamais y ajouter une clé dans un fichier suivi. En conteneur, injecter ces deux variables au démarrage. Sans mode explicite, les agents restent déterministes ; `AGENT_MODE=deterministic` permet d'y revenir.

Chaque rôle effectue un appel forcé à `research_evidence`, exécuté localement en Python, puis produit une réponse structurée validée par Pydantic. Les indicateurs, prix et stops affichés proviennent uniquement du moteur Python. Les scores et confiances des LLM sont des jugements non calibrés. Le veto Red Team et le moteur de risque déterministe s'appliquent ensuite ; les modèles n'ont aucun outil de transaction.

Chaque rapport journalise le modèle, l'effort, les identifiants de réponse et les consommations de tokens. Les réponses ne sont pas stockées côté API (`store=false`). Une analyse complète nécessite 8 appels (2 par rôle), bornés chacun à 8192 tokens de sortie incluant le raisonnement, avec un délai de 180 secondes par requête et sans répétition automatique du SDK. Les erreurs API sont expurgées et journalisées comme analyse rejetée. Les analyses et reprises LLM sont facturées par OpenAI ; un traitement de 20 actifs peut durer plusieurs minutes.

L'ancienne recherche déterministe reste dans le journal. Les rapports LLM ont une version distincte et ne réutilisent pas les décisions déterministes de la journée. Les statistiques des agents sont filtrées sur la version active. L'interface affiche combien de derniers rapports sont réellement issus des LLM et permet d'analyser un seul actif depuis son dossier. Le bouton quotidien lance tout l'univers ; la planification utilise la même configuration après redémarrage.

Documentation officielle : [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [appels d'outils](https://developers.openai.com/api/docs/guides/function-calling).

### Requêtes simultanées

Les transactions restent sérialisées pour préserver les limites de risque et éviter les décisions en double. Si SQLite reste occupé au-delà du délai d'attente, l'API renvoie désormais HTTP 409 et un message « Un traitement est déjà en cours ». Attendre la fin du traitement avant de relancer ; les rapports valides déjà produits sont réutilisés.

## Univers et rôle des agents — mise à jour

Univers actif de 20 actifs sans crypto : QQQ, SPY, AAPL, MSFT, NVDA, AMZN, META, GOOGL, AVGO, TSLA, AMD, NFLX, COST, PLTR, LRCX, SPCX, WMT, GLD, TLT, IWM.

BTC, ETH et CrowdStrike (CRWD) sont retirés du radar, des nouvelles analyses et du suivi des performances actif. Le journal historique reste inchangé et les anciennes propositions de ces actifs ne peuvent plus être approuvées. Le catalogue interne conserve leur définition pour lire les anciens événements.

SpaceX (SPCX), Walmart (WMT) et Lam Research (LRCX) sont ajoutés. Le contrôle réel a trouvé 63 séances pour SpaceX, contre 260 nécessaires : l'actif est suivi mais l'analyse complète est bloquée avant tout appel LLM. Aucun historique n'est inventé. WMT et LRCX disposent de 926 séances dans l'historique vérifié.

Le nouvel onglet « Rôle des agents » explique Steffi, Desmond, Red Team, Houston, les outils Python, le moteur de risque et la validation humaine. La performance reste accessible dans un onglet séparé. La navigation est aussi disponible sur mobile.

## Hébergement Render — configuration préparée, non déployée

`render.yaml` définit un service Docker Python (2 Go), une base PostgreSQL privée et une tâche planifiée à chaque heure de 07:00 à 23:00 UTC. Le planificateur ne recalcule pas une journée terminée avec succès ; il permet la reprise d'un traitement incomplet. Cette configuration crée des ressources payantes : vérifier le devis Render avant application.

Le site refuse de démarrer en mode hébergé sans jeton d'accès de 32 caractères minimum. Render génère `BRACKET22_API_TOKEN` ; le propriétaire le saisit dans le formulaire du desk. La clé OpenAI doit être configurée séparément comme secret du service web et de la tâche planifiée, jamais dans Git. Les URL PostgreSQL du fournisseur sont adaptées au pilote psycopg installé.

Étapes restantes : compte Render, dépôt privé connecté contenant le projet, validation du tarif, ajout des secrets, migration contrôlée du journal SQLite existant vers PostgreSQL, déploiement et vérification de l'URL HTTPS. Ne pas remplacer la base locale ni arrêter définitivement son planificateur avant validation du transfert ; l'arrêter après bascule évite des analyses facturées en double.

Le compte d'hébergement n'est pas encore créé. Aucun service distant, abonnement, téléversement de clé ou migration de données n'a été effectué pendant la préparation.
