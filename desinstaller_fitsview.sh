#!/bin/sh
# Désinstalle fitsview (et l'ancienne version « Tamis ») pour l'utilisateur
# courant. Vos images, vos exports et vos dossiers « rejetes » ne sont
# jamais touchés.
#
#   sh desinstaller_fitsview.sh         retire le programme, l'icône et
#                                       l'entrée de menu ; garde les réglages
#                                       (AstroCheck les reprend)
#   sh desinstaller_fitsview.sh --tout  retire aussi les réglages
set -e

APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

retire() {
    for f in "$@"; do
        if [ -e "$f" ] || [ -L "$f" ]; then
            rm -rf "$f"
            echo "  retiré : $f"
            TROUVE=1
        fi
    done
}

TROUVE=0
echo "Désinstallation de fitsview…"
for nom in fitsview tamis; do
    retire "$HOME/.local/share/$nom" \
           "$APPS/$nom.desktop" \
           "$ICONS/scalable/apps/$nom.svg" \
           "$ICONS/256x256/apps/$nom.png" \
           "$HOME/.local/bin/$nom"
done

if [ "$1" = "--tout" ]; then
    retire "$CONF/fitsview" "$CONF/tamis"
elif [ -d "$CONF/fitsview" ]; then
    echo "  conservé : $CONF/fitsview (vos réglages, repris par AstroCheck)"
fi

update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true

if [ "$TROUVE" = 1 ]; then
    echo "fitsview est désinstallé. Vos images n'ont pas été modifiées."
else
    echo "fitsview n'était pas installé : rien à faire."
fi
