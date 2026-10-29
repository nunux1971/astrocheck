#!/bin/sh
# Installe AstroCheck pour l'utilisateur courant (sans droits administrateur).
#
#   sh installer_astrocheck.sh            installer ou mettre à jour
#   sh installer_astrocheck.sh --retirer  désinstaller AstroCheck
set -e

NOM=astrocheck
DEST="$HOME/.local/share/$NOM"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
BIN="$HOME/.local/bin"
HERE=$(cd "$(dirname "$0")" && pwd)

maj_caches() {
    update-desktop-database "$APPS" 2>/dev/null || true
    gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true
}

if [ "$1" = "--retirer" ]; then
    rm -rf "$DEST"
    rm -f "$APPS/$NOM.desktop" "$ICONS/scalable/apps/$NOM.svg" \
          "$ICONS/256x256/apps/$NOM.png" "$BIN/$NOM"
    maj_caches
    echo "AstroCheck a été désinstallé. Vos images n'ont pas été modifiées."
    exit 0
fi

for f in "$NOM.py" "$NOM.svg"; do
    if [ ! -f "$HERE/$f" ]; then
        echo "Fichier manquant : $f (placez-le à côté de ce script)" >&2
        exit 1
    fi
done

# --- dépendances --------------------------------------------------------
MANQUE=""
for mod in PyQt6 numpy astropy; do
    python3 -c "import $mod" 2>/dev/null || MANQUE="$MANQUE $mod"
done
if [ -n "$MANQUE" ]; then
    echo "Modules Python manquants :$MANQUE"
    echo "  Debian, Ubuntu, Mint : sudo apt install python3-pyqt6 python3-numpy python3-astropy"
    echo "  Fedora               : sudo dnf install python3-pyqt6 python3-numpy python3-astropy"
    echo "  Arch, Manjaro        : sudo pacman -S python-pyqt6 python-numpy python-astropy"
    echo "  Sinon (distribution ancienne) : pip install --user PyQt6"
    echo "AstroCheck est installé quand même ; installez ces modules avant de le lancer."
fi
if ! command -v dcraw >/dev/null 2>&1 && \
   ! python3 -c "import rawpy" 2>/dev/null; then
    echo "Info : ni dcraw ni rawpy, les fichiers CR2 ne pourront pas être lus."
fi

# --- installation -------------------------------------------------------
mkdir -p "$DEST" "$APPS" "$ICONS/scalable/apps" "$ICONS/256x256/apps" "$BIN"
cp "$HERE/$NOM.py" "$HERE/$NOM.svg" "$DEST/"
chmod +x "$DEST/$NOM.py"
cp "$HERE/$NOM.svg" "$ICONS/scalable/apps/$NOM.svg"
if [ -f "$HERE/$NOM.png" ]; then
    cp "$HERE/$NOM.png" "$DEST/"
    cp "$HERE/$NOM.png" "$ICONS/256x256/apps/$NOM.png"
fi
[ -f "$HERE/NOTICE.md" ] && cp "$HERE/NOTICE.md" "$DEST/"

# commande « astrocheck » pour le terminal
cat > "$BIN/$NOM" <<FIN
#!/bin/sh
exec python3 "$DEST/$NOM.py" "\$@"
FIN
chmod +x "$BIN/$NOM"

cat > "$APPS/$NOM.desktop" <<FIN
[Desktop Entry]
Type=Application
Name=AstroCheck
GenericName=Contrôle des nuits d'astrophotographie
Comment=Trier, analyser et préparer ses brutes FITS, SER et CR2
Exec=python3 "$DEST/$NOM.py" %F
Icon=$NOM
Terminal=false
Categories=Graphics;Science;Astronomy;Viewer;
MimeType=image/fits;application/fits;image/x-canon-cr2;
Keywords=FITS;astro;astrophotographie;SER;CR2;RAW;Siril;PixInsight;tri;
StartupWMClass=$NOM
FIN

maj_caches

echo "AstroCheck est installé :"
echo "  - menu des applications : « AstroCheck »"
echo "  - terminal : astrocheck   (ou astrocheck ~/Images/Astro/ma_nuit)"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "  (la commande astrocheck sera disponible après une reconnexion)" ;;
esac
if [ -e "$APPS/fitsview.desktop" ] || [ -d "$HOME/.local/share/fitsview" ]; then
    echo "fitsview est encore installé : lancez sh desinstaller_fitsview.sh pour le retirer."
fi
