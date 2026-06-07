#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_algo.sh — Lance l'algorithme de placement des oraux dans Docker.
#
# Ce script :
#   1. Arrête le conteneur app (pour éviter des requêtes pendant la réinit DB)
#   2. Démarre MariaDB et attend qu'elle soit prête
#   3. Exécute algo.py dans le conteneur Docker (même image, même réseau)
#   4. Redémarre le conteneur app
#   5. Affiche les papillons PDF générés
#
# Utilisation :
#   ./run_algo.sh                  # run normal
#   ./run_algo.sh --dry-run        # affiche les commandes sans les exécuter
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[•]${NC} $*"; }
ok()      { echo -e "${GREEN}[✔]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✘]${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}── $* ──${NC}"; }

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

run() {
    if $DRY_RUN; then
        echo -e "  ${YELLOW}(dry-run)${NC} $*"
    else
        "$@"
    fi
}

# ── Prérequis ─────────────────────────────────────────────────────────────────
command -v docker &>/dev/null || die "Docker n'est pas installé."
docker compose version &>/dev/null || die "Docker Compose v2 n'est pas disponible."

if [[ ! -f ".env" ]]; then
    warn "Fichier .env absent — les credentials DB pourraient manquer."
    warn "Copiez .env.example en .env et remplissez-le."
fi

# ── 1. Arrêt du conteneur app ─────────────────────────────────────────────────
header "Arrêt de l'application"
APP_WAS_RUNNING=false
if docker compose ps --status running app 2>/dev/null | grep -q "app"; then
    APP_WAS_RUNNING=true
    info "Arrêt du conteneur app..."
    run docker compose stop app
    ok "Conteneur app arrêté."
else
    info "Le conteneur app n'était pas en cours d'exécution."
fi

# ── 2. Démarrage de MariaDB ───────────────────────────────────────────────────
header "Démarrage de MariaDB"
info "Démarrage du service mariadb..."
run docker compose up -d mariadb

if ! $DRY_RUN; then
    info "Attente que MariaDB soit prête (max 60 s)..."
    RETRIES=30
    until docker compose exec -T mariadb healthcheck.sh --connect --innodb_initialized \
            > /dev/null 2>&1; do
        RETRIES=$((RETRIES - 1))
        if [[ $RETRIES -le 0 ]]; then
            echo ""
            die "MariaDB n'a pas démarré dans les temps. Logs :\n$(docker compose logs --tail=20 mariadb)"
        fi
        printf "."
        sleep 2
    done
    echo ""
fi
ok "MariaDB prête."

# ── 3. Exécution de algo.py ───────────────────────────────────────────────────
header "Exécution de algo.py"

# Le volume Docker nommé static_docs est root:root 755 sur les déploiements
# existants. On corrige les permissions une fois avant d'exécuter algo.py.
if ! $DRY_RUN; then
    docker compose run --rm --user root app \
        sh -c "mkdir -p /app/webserver/static/docs && chown 1000:1000 /app/webserver/static/docs" \
        2>/dev/null || true
fi

info "Lancement dans le conteneur Docker (image : secondoral-app)..."
echo ""

# --workdir /app  : algo.py est à la racine du projet (pas dans webserver/)
# L'environnement DB_HOST=mariadb est hérité du service app
# Le bind-mount .:/app fournit le code source
# --user "$(id -u):$(id -g)" : même UID que l'appelant → peut écrire dans data/ (log.txt, CSV)
run docker compose run --rm \
    --workdir /app \
    --user "$(id -u):$(id -g)" \
    -e DB_HOST=mariadb \
    app \
    python algo.py

echo ""
ok "algo.py terminé avec succès."

# ── 4. Redémarrage de l'application ──────────────────────────────────────────
header "Redémarrage de l'application"
if $APP_WAS_RUNNING; then
    info "Redémarrage du conteneur app..."
    run docker compose up -d app
    ok "Conteneur app redémarré."
else
    warn "Le conteneur app n'a pas été redémarré (il n'était pas lancé avant)."
    warn "Pour démarrer la stack complète : docker compose up -d"
fi

# ── 5. Résumé des fichiers générés ────────────────────────────────────────────
header "Fichiers générés"
if ! $DRY_RUN; then
    FOUND=false
    for f in \
        "$SCRIPT_DIR/papillons_examinateurs.pdf" \
        "$SCRIPT_DIR/papillons_candidats.pdf" \
        "$SCRIPT_DIR/papillons_loges.pdf"; do
        if [[ -f "$f" ]]; then
            SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
            echo -e "  ${GREEN}✔${NC} $(basename "$f")  (${SIZE})"
            FOUND=true
        fi
    done
    $FOUND || warn "Aucun papillon PDF trouvé dans $SCRIPT_DIR"
fi

echo ""
ok "Terminé."
