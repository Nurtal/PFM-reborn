# PFM-NG — Platform Manager, nouvelle génération

> Réécriture moderne de [Platform-Manager](https://github.com/bgo-bioimagerie/platformmanager), l'outil de gestion des plateformes technologiques (réservation d'équipements, prestations, facturation, clients…).
> Objectif : **mêmes fonctionnalités, stack moderne, rapide, jolie et sécurisée**, et surtout **ultra simple à déployer et très sobre** : un seul conteneur, un seul volume, aucune dépendance externe obligatoire.
>
> ```bash
> docker run -d -p 8000:8000 -v pfm-data:/data ghcr.io/<org>/pfm-ng
> ```

*(Le nom « PFM-NG » est provisoire.)*

---

## Table des matières

1. [Pourquoi une réécriture](#1-pourquoi-une-réécriture)
2. [Cibles du projet](#2-cibles-du-projet)
3. [Choix technologiques](#3-choix-technologiques)
4. [Architecture](#4-architecture)
5. [Périmètre fonctionnel : inventaire des modules](#5-périmètre-fonctionnel--inventaire-des-modules)
6. [Concepts métier clés](#6-concepts-métier-clés)
7. [Règles de sécurité](#7-règles-de-sécurité)
8. [Règles de développement](#8-règles-de-développement)
9. [UI / UX](#9-ui--ux)
10. [Démarrage rapide](#10-démarrage-rapide)
11. [Déploiement](#11-déploiement)
12. [Migration depuis l'ancien Platform-Manager](#12-migration-depuis-lancien-platform-manager)
13. [Feuille de route](#13-feuille-de-route)
14. [Licence](#14-licence)

---

## 1. Pourquoi une réécriture

Où en est l'application existante (état constaté en novembre 2026) :

| Problème | Détail |
|---|---|
| Runtime obsolète | Image `php:8.0.7-apache`. PHP 8.0 ne reçoit plus de correctifs de sécurité depuis novembre 2023. |
| Injections SQL | Une trentaine de requêtes sont construites par concaténation de variables (`"SELECT ... " . $var`). |
| Mots de passe | Un chemin de hachage en `md5()` existe encore (`CoreUser.php`). |
| Framework maison | Routeur, formulaires et sessions sont écrits à la main et n'ont jamais été audités. Les vues mélangent PHP et Twig. |
| Infra lourde | MySQL, RabbitMQ, InfluxDB, Grafana et Redis, plus deux workers : 7 services à maintenir. |
| Taille | Environ 93 000 lignes de PHP réparties en 17 modules. Maintenir ou auditer ce code coûte cher. |
| Maintenance | Le projet amont va bientôt cesser d'être maintenu. |

On ne corrige pas cet existant : on le **réécrit** sur un socle qui règle structurellement la plupart de ces problèmes (ORM, CSRF, sessions et authentification éprouvées).

---

## 2. Cibles du projet

### Fonctionnelles
- **Parité fonctionnelle** avec Platform-Manager pour tous les modules (cf. §5). Un élagage avec les utilisateurs viendra plus tard. On garde donc tout dans le périmètre, avec des priorités.
- **Multi-plateformes** : une seule instance héberge plusieurs plateformes (les « espaces »), chacune avec ses propres ressources, tarifs, clients et factures.
- **Reprise des données** de l'instance existante, via un script de migration MySQL → SQLite (ou PostgreSQL) rejouable.

### Non fonctionnelles (mesurables)

| Axe | Cible |
|---|---|
| **Performance** | Rendu serveur < 200 ms au p95 sur les pages courantes. Calendrier d'une semaine pour une ressource < 300 ms. Lighthouse Performance ≥ 90. |
| **Légèreté** | Moins de 50 ko de JS chargés par page hors calendrier. Aucun framework SPA. |
| **Sécurité** | OWASP ASVS niveau 2. Zéro vulnérabilité *high/critical* connue dans les dépendances et l'image Docker, vérifié en CI. En-têtes notés A+ sur securityheaders.com. |
| **Accessibilité** | WCAG 2.2 AA. Lighthouse Accessibility ≥ 95. Tout est navigable au clavier. |
| **Qualité** | Couverture de tests ≥ 80 % sur la logique métier (tarification, facturation, règles de réservation). CI verte obligatoire pour fusionner. |
| **Déploiement** | **1 conteneur, 1 volume (`/data`), 0 service externe obligatoire.** Installation avec un seul `docker run` ou `docker compose up -d`. Zéro configuration requise pour démarrer : la clé secrète est générée et stockée dans `/data` au premier lancement, les migrations s'appliquent automatiquement, et un assistant web de premier démarrage crée l'admin et le premier espace. Mise à jour = `pull` + redémarrage. |
| **Sobriété** | RAM au repos **< 150 Mo** pour l'ensemble, **< 256 Mo** en charge normale. Tourne sur **1 vCPU / 512 Mo**, ou sur un Raspberry Pi 4. Image **< 150 Mo** (compressée < 60 Mo). Démarrage à froid < 5 s. Images amd64 et arm64. |
| **Sauvegarde** | Sauvegarder = copier le dossier `/data` (base + fichiers). Restaurer = le remettre en place. |
| **i18n** | Français et anglais dès le départ, comme l'existant. |

---

## 3. Choix technologiques

Fil conducteur : **rendu côté serveur + interactivité légère**. C'est une appli de gestion faite de formulaires, de tableaux et d'un calendrier. Une SPA n'apporterait que de la complexité et de la surface d'attaque.

Second fil conducteur : **tout tient dans un processus**. Une plateforme, c'est quelques dizaines à quelques centaines d'utilisateurs, avec peut-être un millier de réservations par mois. SQLite et un seul serveur applicatif suffisent très largement. PostgreSQL reste une option pour les grosses instances, sans changer le code.

### Backend

| Brique | Choix | Pourquoi |
|---|---|---|
| Langage | **Python 3.13+** | Maintenable par l'équipe, écosystème riche (PDF, Excel, LDAP, OIDC). |
| Framework | **Django 5.2 LTS** (passage à la LTS suivante quand elle sort) | Authentification, permissions, ORM, migrations, CSRF, protection XSS des templates et admin intégrés. Correspond exactement au besoin. |
| Base de données | **SQLite** (mode WAL) par défaut, **PostgreSQL** en option via `DATABASE_URL` | Aucun service à faire tourner, un simple fichier dans `/data`, et largement assez rapide pour la charge visée. Les écritures se font en `transaction_mode=IMMEDIATE` (Django ≥ 5.1), ce qui sérialise la vérification anti-chevauchement des réservations. Sur PostgreSQL, on ajoute en plus une contrainte d'exclusion. Le code reste agnostique de la base. |
| Tâches asynchrones | **Huey** avec stockage SQLite (`/data/tasks.db`) | Envoi de mails, génération de PDF, relève IMAP du helpdesk, rappels, tâches planifiées. Un consumer léger (quelques Mo) tourne dans le même conteneur. Remplace RabbitMQ et Redis. |
| Gestion des dépendances | **uv** | Rapide, avec lockfile reproductible. |
| Serveur applicatif | **Granian** (serveur HTTP en Rust) | Très faible empreinte mémoire, rapide, sert aussi bien du WSGI que de l'ASGI. 1 processus × 4 threads par défaut (budget RAM), réglable. |
| Fichiers statiques | **WhiteNoise** (compression brotli/gzip, cache immuable) | Servis directement par l'appli : pas besoin de nginx pour fonctionner. |
| Supervision des processus | Petit entrypoint qui lance Granian et le consumer Huey et relaie les signaux (`tini` en PID 1) | Un seul conteneur, pas de s6 ni de supervisord. |

### Frontend

| Brique | Choix | Pourquoi |
|---|---|---|
| CSS | **Tailwind CSS v4** (CLI standalone, compilé à la construction de l'image, sans Node nulle part) | Moderne, cohérent, bundle CSS minuscule après purge. |
| Interactivité | **HTMX 2** + **Alpine.js** | Mises à jour partielles de page (filtres, modales, formulaires inline) sans écrire d'API JSON ni de SPA. |
| Composants UI | Composants maison avec **django-cotton**, inspirés de shadcn/ui | Interface jolie et homogène, réutilisable. |
| Calendrier | **FullCalendar** (cœur MIT) | Vues jour, semaine et mois par ressource, glisser-déposer. Remplace le calendrier fait main. |
| Graphiques | **Chart.js** ou **Apache ECharts** | Statistiques d'usage et de facturation. Remplace InfluxDB et Grafana pour l'usage métier. |
| Icônes | **Lucide** (SVG inline) | Léger et cohérent. |

### Bibliothèques métier

| Besoin | Lib |
|---|---|
| PDF (factures, devis) | **Typst** via le paquet Python `typst` : un seul wheel, aucune dépendance système, des PDF générés en quelques millisecondes et un très beau rendu typographique. Écarté : WeasyPrint, qui tire Pango/Cairo (~+80 Mo d'image). |
| Export Excel | **openpyxl** |
| Authentification | **django-allauth** (local + OIDC/SSO + MFA TOTP/WebAuthn) et **django-auth-ldap** |
| Anti brute-force | **django-axes** |
| CSP | **django-csp** (ou la CSP native de Django ≥ 6.0) |
| Permissions objet | **django-rules** (permissions par espace et par rôle) |
| Historique / audit | **django-simple-history** (traçabilité des factures et réservations) |
| CalDAV / iCal | Flux **iCal** en lecture seule par utilisateur et par ressource (remplace `Bookingcaldav`) |
| API (phase ultérieure) | **django-ninja** (OpenAPI auto) |

### Outillage

| Usage | Outil |
|---|---|
| Lint et format | **ruff** (lint + format), **djlint** (templates) |
| Typage | **mypy** + **django-stubs** |
| Tests | **pytest**, **pytest-django**, **factory-boy**, **Playwright** (E2E) |
| Hooks | **pre-commit** |
| CI | GitHub Actions (ou GitLab CI) : lint, tests, build de l'image, scan **Trivy**, **pip-audit** |
| Dépendances | **Renovate** ou Dependabot |
| Observabilité | Logs JSON (structlog), endpoint `/healthz`, **Sentry** optionnel |

### Infra / déploiement

| Brique | Choix |
|---|---|
| Conteneur | **Une seule image** multi-stage basée sur `python:3.13-slim`, multi-arch (amd64/arm64), utilisateur non-root, système de fichiers en lecture seule sauf `/data`. Bytecode précompilé, aucun outil de build dans l'image finale. |
| Reverse proxy / TLS | **Optionnel.** En général, l'établissement a déjà un reverse proxy : on se branche dessus. Sinon, un profil compose `tls` ajoute **Caddy**, qui gère HTTPS automatiquement via Let's Encrypt avec une config de 3 lignes. Un exemple de config **nginx** est aussi fourni. Les en-têtes de sécurité sont posés par Django lui-même, donc ils restent présents sans proxy. |
| Orchestration | `docker run` ou un `compose.yaml` d'un seul service |

Ce qui disparaît par rapport à l'existant : PHP, Apache, MySQL, RabbitMQ, Redis, InfluxDB, Grafana, et les 6 conteneurs en plus.

---

## 4. Architecture

```
  (reverse proxy de l'établissement, ou Caddy optionnel pour le TLS)
                      │
  ┌───────────────────▼──────────────── conteneur pfm-ng ──┐
  │  tini (PID 1)                                          │
  │   ├─ Granian ── Django + WhiteNoise   :8000            │
  │   └─ Huey consumer  (mails, PDF, IMAP, rappels, cron)  │
  └───────────────────┬────────────────────────────────────┘
                      │
              volume /data
               ├─ db.sqlite3      (ou PostgreSQL externe en option)
               ├─ tasks.db        (file Huey)
               ├─ media/          (uploads, PDF archivés)
               └─ secret_key      (générée au 1er démarrage)
```

**1 conteneur, 1 volume.** Toute la persistance tient dans `/data`.

### Arborescence du dépôt

```
pfm-ng/
├── README.md
├── pyproject.toml / uv.lock
├── compose.yaml                # prod : 1 service (+ profils optionnels tls / postgres)
├── compose.dev.yaml            # surcharges dev (hot reload, mailpit)
├── docker/
│   ├── Dockerfile
│   ├── entrypoint.sh           # init /data, migrate, lance granian + huey
│   ├── Caddyfile               # profil tls optionnel
│   └── nginx.example.conf      # pour qui préfère nginx
├── .env.example
├── config/                     # settings Django (base / dev / prod), urls, asgi
├── apps/
│   ├── core/                   # espaces, rôles, menu, réglages, audit
│   ├── accounts/               # utilisateurs, auth, LDAP/OIDC, MFA
│   ├── resources/              # ressources, catégories, zones, états, habilitations
│   ├── booking/                # réservations, règles, tarifs, forfaits, iCal
│   ├── clients/                # clients, organismes, grilles tarifaires
│   ├── services/               # prestations, commandes, projets, stock
│   ├── invoices/               # facturation transverse, visas, PDF
│   ├── quotes/                 # devis
│   ├── catalog/                # catalogue public
│   ├── documents/              # documents partagés
│   ├── helpdesk/               # tickets (email ↔ app)
│   ├── communication/          # actualités, tuiles (module « com »)
│   ├── mailer/                 # envoi de mails groupés
│   ├── statistics/             # tableaux de bord
│   ├── rating/                 # évaluations
│   ├── antibodies/             # base d'anticorps
│   └── notebook/               # carnet de notes (ex « bulletjournal »)
├── templates/                  # layout, composants (cotton)
├── static/src/                 # input Tailwind, JS minimal
├── legacy_import/              # scripts de migration depuis l'ancien PFM
└── tests/                      # E2E Playwright + fixtures transverses
```

**Règle :** chaque app Django contient `models.py`, `services.py` (logique métier pure, testable), `views.py` (fin), `forms.py`, `permissions.py`, `tasks.py`, `templates/<app>/` et `tests/`. **La logique métier ne vit jamais dans les vues ni dans les templates.**

---

## 5. Périmètre fonctionnel : inventaire des modules

Priorité : **P1** = usage principal (facturation et réservation d'équipements), **P2** = utile, **P3** = à confirmer lors de l'élagage avec les utilisateurs.

| Module legacy | App PFM-NG | Prio | Fonctionnalités à reprendre |
|---|---|---|---|
| `core` | `core` + `accounts` | **P1** | Espaces (plateformes) et accès, rôles par espace, utilisateurs, demandes d'adhésion, connexion locale + LDAP + OpenID, réglages par espace, menu et tuiles d'accueil, historique d'espace, templates PDF, fichiers. |
| `users` | `accounts` | **P1** | Profil, compte utilisateur, réglages personnels. |
| `resources` | `resources` | **P1** | Ressources (équipements et infrastructures), catégories, zones, états, types d'événements, responsables, fiches d'info et d'aide, **visas / habilitations** (formation requise avant de réserver). |
| `booking` | `booking` | **P1** | Calendrier jour, semaine et mois. Réservation, modification, annulation. Créneaux et horaires d'ouverture (scheduling). Restrictions (durée max, délai, quotas). Blocage de créneaux. Autorisations par ressource. Codes couleur. Quantités et suppléments. **Tarifs** (par grille client, majoration nuit et week-end). **Forfaits** (packages). Accessibilité (qui voit et qui réserve). Statistiques. Flux iCal (ex CalDAV). Préférences d'affichage utilisateur. |
| `clients` | `clients` | **P1** | Clients, organismes (sociétés, labos), **grilles tarifaires** (académique, industriel…), rattachement des utilisateurs aux comptes clients. |
| `invoices` | `invoices` | **P1** | Facturation transverse (réservations + prestations), facture globale par période et par client, liste et suivi, **visas** (validation), numérotation, génération PDF, export. |
| `services` | `services` | **P1** | Prestations et tarifs, commandes, **projets** (avec Gantt), facturation par commande ou par projet, solde, origines, achats, **stock** (armoires et étagères), visas, statistiques. |
| `quote` | `quotes` | P2 | Devis (PDF), conversion en commande ou facture. |
| `catalog` | `catalog` | P2 | Catalogue public des équipements et prestations. |
| `documents` | `documents` | P2 | Documents partagés par espace, avec droits. |
| `helpdesk` | `helpdesk` | P2 | Tickets créés depuis un email (IMAP) et suivis dans l'app. |
| `statistics` | `statistics` | P2 | Tableaux de bord d'usage et de facturation, export. |
| `mailer` | `mailer` | P2 | Mails groupés aux utilisateurs d'un espace. |
| `com` | `communication` | P3 | Actualités, tuiles d'accueil. |
| `rating` | `rating` | P3 | Évaluation des prestations et ressources. |
| `antibodies` | `antibodies` | P3 | Base d'anticorps (protocoles, espèces, organes, marquages…). Très spécifique, à confirmer. |
| `bulletjournal` | `notebook` | P3 | Notes et collections. À confirmer. |

> L'ancien code sert de **spécification fonctionnelle**, pas de modèle d'implémentation. Pour chaque module : lire les contrôleurs et modèles legacy, écrire les cas d'usage dans l'issue, puis implémenter proprement.

---

## 6. Concepts métier clés

- **Espace (`Space`)** : une plateforme (ex. imagerie, cytométrie). Toutes les données métier y sont rattachées par une clé étrangère `space`. **Chaque requête est filtrée par espace**, via un manager ou mixin commun, jamais à la main.
- **Rôles par espace** : visiteur, utilisateur, gestionnaire, administrateur d'espace. Un super-admin est global. On les implémente avec `django-rules` + un modèle `SpaceMembership(user, space, role)`.
- **Ressource** : un équipement ou une infrastructure réservable. Elle peut exiger une **habilitation** (`Authorization`) valide.
- **Réservation (`Booking`)** : champs `start` et `end` indexés. La création et la modification passent **uniquement** par `booking.services.reserve()`, qui vérifie le chevauchement et insère dans la même transaction `IMMEDIATE`. Sous SQLite, les écritures sont alors sérialisées, donc un double booking est impossible même en cas de concurrence. Sous PostgreSQL, une **contrainte d'exclusion** (`EXCLUDE USING gist`) est ajoutée en filet de sécurité. Un test de concurrence couvre les deux bases.
- **Tarification** : `prix = f(ressource ou prestation, grille tarifaire du client, période (jour / nuit / week-end), quantité, forfait)`. C'est **un seul service, pur et testé exhaustivement**. On ne calcule jamais de prix ailleurs.
- **Facture** : une fois émise, elle est **immuable**. Les lignes sont figées (copie des prix au moment de l'émission). Corriger une facture veut dire émettre un avoir. La numérotation est séquentielle par espace et sans trou.
- **Argent** : `DecimalField` uniquement, jamais de `float`. Devise configurable par espace.
- **Dates** : tout est stocké en UTC (`USE_TZ=True`) et affiché dans le fuseau de l'espace.

---

## 7. Règles de sécurité

Ces règles ne sont pas négociables.

1. **Pas de SQL brut.** On passe par l'ORM. Si `raw()` ou `cursor.execute()` est vraiment nécessaire, la requête est paramétrée et un commentaire justifie le choix en revue.
2. **Authentification** : hachage Argon2 (`argon2-cffi`, premier de `PASSWORD_HASHERS`). MFA disponible pour tous et obligatoire pour les admins. django-axes bloque après N échecs. Politique de mot de passe via les validateurs Django + vérification Have I Been Pwned optionnelle.
3. **Sessions et cookies** : `Secure`, `HttpOnly`, `SameSite=Lax`, expiration raisonnable, rotation à la connexion.
4. **CSRF** actif partout, y compris pour les requêtes HTMX (token injecté via `hx-headers`).
5. **Autorisation** : chaque vue déclare sa permission, et tout est refusé par défaut. Des tests vérifient l'isolation entre espaces : un utilisateur de l'espace A ne voit **rien** de l'espace B, même en devinant des IDs (IDOR). Les URLs publiques utilisent des UUID ou des identifiants courts non séquentiels.
6. **XSS** : auto-échappement des templates. `|safe` et `mark_safe` sont interdits sauf validation en revue. Le Markdown utilisateur est assaini (`nh3`).
7. **En-têtes** posés par Django lui-même, donc présents même sans reverse proxy : CSP stricte (pas de `unsafe-inline`, nonces pour Alpine), HSTS, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `frame-ancestors 'none'`.
8. **Uploads** : liste blanche d'extensions et de types MIME, taille max, stockage dans `/data/media` (jamais servi directement), téléchargement via une vue qui contrôle les droits (`FileResponse`), noms de fichiers régénérés.
9. **Secrets** : uniquement dans des variables d'environnement ou des secrets Docker. Jamais dans le dépôt. `.env` est dans `.gitignore`. `DEBUG=False` en prod, vérifié au démarrage.
10. **Dépendances et image** : lockfile, Renovate, `pip-audit` et `trivy image` en CI, qui bloquent sur *high/critical*. Image minimale, utilisateur non-root, `read_only: true`, `cap_drop: [ALL]`.
11. **Audit** : historique (qui, quoi, quand) sur les réservations, factures, tarifs et rôles.
12. **Données personnelles (RGPD)** : export et suppression des données d'un utilisateur, durée de conservation configurable, minimisation des données collectées.
13. Lancer `python manage.py check --deploy` en CI sur la config prod.

---

## 8. Règles de développement

### Code
- Python typé (annotations partout, `mypy` en mode strict sur `services.py`).
- `ruff format` + `ruff check` + `djlint`, appliqués par pre-commit.
- **Vues fines, services épais** : la logique métier va dans `services.py`, en fonctions pures autant que possible.
- Code, noms et commentaires en anglais. Interface traduite FR/EN via `gettext`.
- Pas de dépendance ajoutée sans justification dans la PR (maintenance, licence, sécurité).

### Tests
- Chaque règle métier (tarif, restriction de réservation, numérotation de facture) a des tests unitaires, y compris les cas limites : chevauchement minuit, changement d'heure, week-end, forfait épuisé.
- Chaque vue a un test de permission (accès autorisé, refusé, autre espace).
- Les parcours critiques sont testés en E2E Playwright : connexion, réserver une machine, facturer un mois, générer le PDF.

### Git
- Branche `main` protégée. Une PR par fonctionnalité, revue obligatoire, CI verte.
- [Conventional Commits](https://www.conventionalcommits.org/) (`feat(booking): ...`, `fix(invoices): ...`) et changelog généré.
- Versionnage SemVer, images taguées `vX.Y.Z` + `latest`.

### Budget de ressources (vérifié en CI)
- Un job CI démarre l'image, rejoue un scénario de charge (connexion, calendrier, facturation) et **échoue si** la RAM du conteneur dépasse 256 Mo, si l'image dépasse 150 Mo ou si le démarrage dépasse 5 s.
- Toute nouvelle dépendance indique son poids dans la PR. Les dépendances système (`apt`) sont à éviter.
- Pas de requêtes N+1 : `django-debug-toolbar` en dev et des tests `assertNumQueries` sur les vues listes et le calendrier.
- Pas de cache externe : le cache Django utilise la base ou la mémoire locale.

### Definition of Done
- [ ] Fonctionnalité équivalente au legacy (ou écart documenté)
- [ ] Tests unitaires + permissions, et E2E si parcours critique
- [ ] Traductions FR/EN
- [ ] Accessible au clavier, contraste conforme
- [ ] Migration de données legacy prise en charge pour ce module
- [ ] Documentation utilisateur mise à jour

---

## 9. UI / UX

- **Design system** : tokens Tailwind (couleurs, rayons, espacements) définis une fois dans `static/src/app.css` (`@theme`). Une couleur d'accent configurable par espace.
- **Mode sombre** dès le départ (`prefers-color-scheme` + bascule manuelle).
- **Mise en page** : barre latérale de navigation par espace, en-tête avec sélecteur d'espace, contenu responsive. L'interface est utilisable sur tablette, car on réserve parfois depuis la salle de manip.
- **Composants** : bouton, champ, select, modale, tiroir, tableau triable et paginé (HTMX), toasts, badges de statut, état vide, squelettes de chargement.
- **Tableaux** : tri, filtres et pagination côté serveur via HTMX, et export CSV/Excel partout où il y a une liste.
- **Calendrier** : FullCalendar avec vue ressources, couleurs par type ou statut, création par sélection de plage, dialogue de confirmation qui affiche **le prix estimé avant de valider**.
- **Retour utilisateur** : chaque action affiche un toast. Les erreurs de formulaire apparaissent inline, et les actions destructives demandent une confirmation.

---

## 10. Démarrage rapide

### Installer (production) en 30 secondes

```bash
docker run -d --name pfm --restart unless-stopped \
  -p 8000:8000 -v pfm-data:/data \
  -e PFM_URL=https://pfm.mon-labo.fr \
  ghcr.io/<org>/pfm-ng:latest
```

Ouvrir l'URL : l'**assistant de premier démarrage** demande d'abord le **jeton d'installation** affiché par `docker logs pfm` (il empêche quiconque d'autre de s'approprier une instance fraîche), puis le compte admin, le nom de la première plateforme et le serveur SMTP (avec un bouton de test). C'est tout.

Ou bien avec `compose.yaml` (un seul service) :

```yaml
services:
  pfm:
    image: ghcr.io/<org>/pfm-ng:latest
    restart: unless-stopped
    ports: ["8000:8000"]
    volumes: ["pfm-data:/data"]
    environment:
      PFM_URL: https://pfm.mon-labo.fr
volumes:
  pfm-data:
```

Options : `docker compose --profile tls up -d` ajoute Caddy (HTTPS automatique), `--profile postgres` ajoute un PostgreSQL pour les grosses instances.

### Configuration

**Une seule variable est recommandée : `PFM_URL`.** Tout le reste a une valeur par défaut raisonnable ou se règle dans l'interface d'admin (SMTP, LDAP, OIDC, IMAP du helpdesk, fuseau horaire, logo, couleurs). Les secrets saisis dans l'interface sont chiffrés en base avec une clé dérivée de `/data/secret_key`.

| Variable | Défaut | Rôle |
|---|---|---|
| `PFM_URL` | `http://localhost:8000` | URL publique (sert aussi pour ALLOWED_HOSTS, CSRF et les liens dans les mails) |
| `PFM_SECRET_KEY` | générée dans `/data/secret_key` | Clé Django |
| `DATABASE_URL` | `sqlite:////data/db.sqlite3` | Mettre `postgres://...` pour PostgreSQL |
| `PFM_WORKERS` | `1` | Nombre de processus Granian (~55 Mo chacun) |
| `PFM_THREADS` | `4` | Threads WSGI par processus |
| `PFM_BEHIND_PROXY` | `false` | Active `SECURE_PROXY_SSL_HEADER` / `X-Forwarded-*` |
| `EMAIL_URL` | vide (réglable dans l'UI) | `smtp+tls://user:pass@host:587` |
| `SENTRY_DSN` | vide | Optionnel |

### Développer

Prérequis : [uv](https://docs.astral.sh/uv/). Docker est facultatif en dev.

```bash
git clone <repo> pfm-ng && cd pfm-ng
uv sync
uv run manage.py migrate
uv run manage.py demo                 # données de démo (DEBUG uniquement)
uv run manage.py runserver            # http://localhost:8000
uv run tailwindcss -i static/src/app.css -o static/dist/app.css --watch
uv run manage.py run_huey             # tâches (ou HUEY_IMMEDIATE=1 pour tout exécuter en synchrone)
uv run pytest
```

Pour les mails en dev : `docker compose -f compose.dev.yaml up mailpit`, puis http://localhost:8025.

---

## 11. Déploiement

- **Mise à jour** : `docker pull ghcr.io/<org>/pfm-ng:latest && docker restart pfm` (ou `docker compose pull && docker compose up -d`). Les migrations s'appliquent au démarrage. Une **sauvegarde automatique de la base** est faite dans `/data/backups/` avant chaque migration.
- **Sauvegardes** : une tâche Huey planifiée fait chaque nuit un snapshot cohérent (`sqlite3 .backup`) dans `/data/backups/`, avec rotation sur 14 jours. Pour une copie externe, il suffit de synchroniser `/data/backups/` (rsync, restic, sauvegarde du serveur). Option : **Litestream** pour une réplication continue vers S3/MinIO. **Tester la restauration** (`pfm restore <fichier>`).
- **TLS** : derrière le reverse proxy de l'établissement (`PFM_BEHIND_PROXY=true`), ou avec le profil `tls` (Caddy). Un exemple nginx est dans `docker/nginx.example.conf`.
- **Healthcheck** intégré à l'image (`/healthz`). `docker ps` montre directement l'état.
- **CLI d'admin** dans le conteneur : `docker exec pfm pfm <commande>` (`createadmin`, `backup`, `restore`, `import-legacy`, `check`).
- **Limiter les ressources** (recommandé) : `--memory=384m --cpus=1`.
- **Quand passer à PostgreSQL ?** Au-delà de plusieurs dizaines de plateformes sur une même instance, ou de plusieurs instances applicatives derrière un load balancer. `pfm migrate-db --to postgres://...` transfère les données.

---

## 12. Migration depuis l'ancien Platform-Manager

- La commande `python manage.py import_legacy --mysql-url=... --space=<id|all>` lit directement la base MySQL legacy (en lecture seule) et peuple la base PFM-NG. Elle est accessible aussi via `docker exec pfm pfm import-legacy`. Le pilote MySQL n'est installé que dans une variante d'image `:legacy-import`, pour ne pas alourdir l'image principale.
- Elle est **idempotente et rejouable** : la correspondance des anciens IDs est conservée dans une table `LegacyIdMap`.
- Ordre d'import : espaces → utilisateurs → clients et grilles → ressources → habilitations → réservations → prestations et commandes → factures (importées **figées**, avec leur numéro d'origine) → le reste.
- **Mots de passe** : les anciens hachages ne sont pas repris. Les utilisateurs reçoivent un lien de réinitialisation, ou passent par LDAP/SSO.
- Un rapport de migration (comptes par table, anomalies) est produit pour chaque exécution.
- Bascule recommandée : exécution en parallèle sur un environnement de préprod, vérification croisée des totaux facturés par mois, puis bascule.

---

## 13. Feuille de route

| Jalon | Contenu |
|---|---|
| **M0 — Socle** | Squelette Django, image unique (Granian + Huey + SQLite), entrypoint et assistant de premier démarrage, CI (lint, tests, Trivy, budget de ressources), layout Tailwind, design system, authentification (locale + MFA), espaces et rôles. |
| **M1 — Ressources & réservation** | Ressources, catégories, habilitations, calendrier FullCalendar, règles et restrictions, contrainte anti-chevauchement, notifications mail, flux iCal. |
| **M2 — Clients & tarification** | Clients, organismes, grilles tarifaires, service de tarification (nuit, week-end, forfaits, suppléments). |
| **M3 — Facturation** | Facturation des réservations, factures globales, visas, PDF Typst, numérotation, avoirs, exports. |
| **M4 — Prestations** | Prestations, commandes, projets (Gantt), stock, facturation des prestations. |
| **M5 — Migration legacy** | Import MySQL complet, préprod en parallèle, recette avec la plateforme. |
| **M6 — Modules P2** | Devis, catalogue, documents, helpdesk, statistiques, mailer, LDAP/OIDC. |
| **M7 — Modules P3** | Selon le résultat de l'élagage avec les utilisateurs : com, rating, antibodies, notebook. |
| **M8 — API** | API REST (django-ninja) avec tokens par utilisateur. |

---

## 14. Licence

Platform-Manager est distribué sous **GNU AGPL v3**. PFM-NG en reprend la spécification fonctionnelle et vise une compatibilité de données. Il est donc également publié sous **AGPL-3.0**, ce qui garantit que les plateformes qui l'hébergent gardent l'accès au code source.
