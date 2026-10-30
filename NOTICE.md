# AstroCheck — notice d'utilisation

Octobre 2026 — version avec analyse et classement

## À quoi sert AstroCheck

AstroCheck sert à passer en revue ses brutes FITS ou RAW Canon (CR2), ou les images d'une vidéo SER, une par une, à les classer de la meilleure à la plus mauvaise, à écarter les mauvaises et à préparer le reste pour le prétraitement. Il ne modifie jamais le contenu de vos fichiers : il les affiche, les déplace ou les assemble en vidéo SER.

Une séance typique se déroule en quatre temps :

1. Glisser le dossier de la nuit dans la fenêtre.
2. Faire défiler les images au clavier et rejeter celles qui sont ratées (nuages, filé, buée, passage de satellite, avion).
3. Ranger les rejetées dans un sous-dossier « rejetes ».
4. Lancer le prétraitement dans Siril ou PixInsight sur les images restantes, ou créer un SER pour AutoStakkert ou PIPP.

## Installation sous Linux

AstroCheck est un script Python. Il a besoin de PyQt6, numpy et astropy, à installer une seule fois avec le gestionnaire de paquets de votre distribution.

| Distribution | Commande |
| --- | --- |
| Debian 12, Ubuntu 22.04, Linux Mint 21 ou plus récents | `sudo apt install python3-pyqt6 python3-numpy python3-astropy` |
| Fedora | `sudo dnf install python3-pyqt6 python3-numpy python3-astropy` |
| Arch, Manjaro | `sudo pacman -S python-pyqt6 python-numpy python-astropy` |

Sur une distribution plus ancienne qui ne propose pas PyQt6, installez-le avec pip : `pip install --user PyQt6`.

Les versions précédentes utilisaient PyQt5. Si vous mettez à jour, installez simplement PyQt6 comme ci-dessus ; PyQt5 peut rester installé, il ne gêne pas.

Pour ouvrir les fichiers RAW Canon (CR2), installez en plus le petit programme **dcraw** : `sudo apt install dcraw` sous Debian, Ubuntu ou Mint, `sudo dnf install dcraw` sous Fedora, `sudo pacman -S dcraw` sous Arch. Si le module Python **rawpy** est installé, AstroCheck l'utilise à la place : il est un peu plus rapide, mais n'est pas proposé par toutes les distributions.

### Lancer le programme

Placez `astrocheck.py` et `astrocheck.svg` dans un même dossier, par exemple `~/Applications/astrocheck/`. Rendez le script exécutable puis lancez-le :

```bash
cd ~/Applications/astrocheck
chmod +x astrocheck.py
./astrocheck.py
```

Vous pouvez aussi lui donner directement des fichiers ou un dossier : `./astrocheck.py ~/Astro/2026-10-02/Lights/`.

### Ajouter AstroCheck au menu des applications

Le script `installer_astrocheck.sh` fourni avec l'icône copie le programme dans `~/.local/share/astrocheck/`, installe l'icône et crée l'entrée de menu. Lancez-le depuis le dossier qui contient les trois fichiers :

```bash
sh installer_astrocheck.sh
```

AstroCheck apparaît ensuite dans le menu, rubrique Graphisme ou Sciences selon le bureau. Il figure aussi dans le menu « Ouvrir avec » d'un clic droit sur un fichier FITS ou CR2. Si l'icône n'apparaît pas tout de suite, fermez puis rouvrez votre session.

Pour désinstaller : `sh installer_astrocheck.sh --retirer`.

La commande `astrocheck` est aussi ajoutée pour le terminal, par exemple `astrocheck ~/Images/Astro/IC1805`.

### Passer de fitsview à AstroCheck

AstroCheck est le nouveau nom de fitsview. Pour remplacer l'ancienne version :

```bash
sh desinstaller_fitsview.sh
sh installer_astrocheck.sh
```

Le premier script retire le programme fitsview, son icône et son entrée de menu. Il ne touche ni à vos images, ni à vos exports, ni à vos dossiers « rejetes ». Il garde vos réglages : AstroCheck les reprend automatiquement au premier lancement (logiciel d'export, options, critères de mélange SHO). Pour les effacer aussi, utilisez `sh desinstaller_fitsview.sh --tout`.

## La fenêtre et l'ajout d'images

La fenêtre est divisée en trois colonnes, dont vous pouvez régler la largeur en tirant sur leurs séparations.

| Zone | Contenu |
| --- | --- |
| Gauche | La liste des images avec leurs mesures (score, FWHM, excentricité, étoiles, fond), le compteur d'images gardées et rejetées, un rappel des touches |
| Centre | L'image affichée, ajustée pour être visible |
| Droite | L'en-tête FITS complet de l'image affichée |
| Barre du haut | Le choix de l'étirement, la débayerisation, l'avance automatique, la lecture vidéo et l'analyse |
| Barre du bas | Un résumé : nom, dimensions, médiane du fond, bruit, objet, filtre, pose, gain, température, date, et les mesures une fois l'analyse faite |

### Ajouter des images

Le plus simple est de glisser des fichiers ou un dossier entier depuis votre gestionnaire de fichiers et de les déposer n'importe où sur la fenêtre. Vous pouvez aussi passer par **Fichier ▸ Ajouter des fichiers FITS, SER ou CR2** (Ctrl+O) ou **Fichier ▸ Ajouter un dossier** (Ctrl+D).

Quand vous ajoutez un dossier, AstroCheck prend les fichiers FITS, les vidéos SER et les RAW CR2 qu'il contient, y compris dans ses sous-dossiers (Light, Darks, Flats…). Vous pouvez donc déposer le dossier de toute la nuit d'un coup : la colonne Type distingue les brutes des darks, flats et offsets, et des **onglets** au-dessus de la liste (Toutes, Brutes, Darks, Flats, Offsets, Résultats) n'affichent qu'un type à la fois, avec le nombre d'images de chacun. AstroCheck ouvre d'abord l'onglet des brutes. Les dossiers « rejetes » et « traitement » créés par AstroCheck sont ignorés. Les extensions reconnues sont `.fit`, `.fits`, `.fts` (et leurs versions compressées `.gz`), `.ser` et `.cr2`.

La liste est triée par nom de fichier, ce qui correspond à l'ordre chronologique avec la plupart des logiciels d'acquisition. Un fichier déjà présent n'est jamais ajouté deux fois. Survolez un nom pour voir son chemin complet.

**Fichier ▸ Retirer la sélection de la liste** et **Fichier ▸ Vider la liste** enlèvent des noms de la liste sans toucher aux fichiers sur le disque.

## Afficher et ajuster les images

Cliquez sur un nom pour afficher l'image, puis utilisez les flèches haut et bas pour passer de l'une à l'autre. Les images voisines sont préparées à l'avance, le défilement reste donc fluide même avec un grand capteur.

### Choisir l'étirement

Une brute est presque noire à l'écran : le signal du ciel n'occupe qu'une petite partie de la dynamique. AstroCheck l'étire automatiquement pour la rendre lisible. L'étirement ne sert qu'à l'affichage, vos fichiers restent intacts.

| Étirement | Effet | Quand l'utiliser |
| --- | --- | --- |
| Auto-stretch (STF) | Fond de ciel amené à 25 % de gris, comme le STF de PixInsight | Par défaut, pour la plupart des images |
| Auto-stretch doux | Fond plus sombre (15 %), moins de bruit visible | Pour juger les étoiles et les cœurs brillants |
| Auto-stretch fort | Fond plus clair (40 %) | Pour repérer voiles nuageux, gradients et traînées faibles |
| Percentiles 0,5 – 99,8 % | Étalement linéaire entre deux seuils | Pour comparer des images entre elles sans effet de l'étirement |
| Linéaire min – max | Aucun étirement | Pour voir l'image telle qu'elle est, souvent très sombre |

Chaque image est étirée selon ses propres statistiques. Une image voilée par un nuage peut donc paraître presque normale : surveillez la valeur de la médiane dans la barre du bas, qui monte nettement quand le ciel s'éclaircit.

### Images couleur

Avec une caméra couleur, les brutes s'affichent en noir et blanc avec une fine trame en damier. Cochez **Débayeriser (couleur)** pour les voir en couleur. AstroCheck lit la matrice de Bayer dans le mot-clé BAYERPAT de l'en-tête ; sans ce mot-clé, l'image reste en noir et blanc. Chaque couleur est étirée séparément, ce qui neutralise la dominante verte habituelle.

### Zoomer et se déplacer

- **Molette de la souris** : zoomer ou dézoomer à l'endroit pointé.
- **Cliquer-glisser** : se déplacer dans l'image.
- **Double-clic** ou touche **F** : ajuster l'image à la fenêtre.
- Touche **1** : afficher à 100 %, un pixel de l'image pour un pixel de l'écran.

Le zoom est conservé quand vous changez d'image. Zoomez sur un coin, puis faites défiler la série : vous voyez tout de suite si les étoiles s'allongent ou se dédoublent.

### Retourner l'image

Les logiciels ne s'accordent pas sur le sens des lignes dans un fichier FITS. AstroCheck suit le mot-clé ROWORDER quand il est présent. Si une image apparaît à l'envers par rapport à Siril, appuyez sur **V** ou utilisez **Affichage ▸ Retourner verticalement**.

## Fichiers RAW Canon (CR2)

Les fichiers CR2 de vos reflex Canon s'ajoutent, s'affichent et se trient exactement comme des FITS. AstroCheck lit les données brutes du capteur, sans traitement : c'est ce qu'il faut pour juger une brute avant le prétraitement.

- **Couleur** : un CR2 est une image en mosaïque de Bayer. Cochez **Débayeriser (couleur)** pour la voir en couleur ; la matrice est lue dans le fichier.
- **Informations** : le temps de pose, la sensibilité ISO, le modèle de boîtier, la focale et la date sont repris des données EXIF. Ils s'affichent dans la barre du bas et dans la colonne de droite.
- **Rangement** : un CR2 rejeté se déplace dans « rejetes » comme un FITS.
- **Export** : les CR2 peuvent être assemblés en vidéo SER ou convertis en fichiers FITS 16 bits (menu Export), par exemple pour un logiciel qui ne lit pas les RAW.

La date EXIF est celle de l'horloge de l'appareil, souvent réglée à l'heure locale et non en temps universel. Vérifiez-la avant de vous en servir pour des mesures précises.

Le décodage d'un CR2 prend environ une demi-seconde. Le défilement reste fluide grâce au préchargement, mais la lecture vidéo d'une série de CR2 sera plus lente qu'avec des FITS.

## Vidéos SER

Une vidéo SER s'ajoute comme un fichier FITS, par glisser-déposer ou par le menu Fichier. Chacune de ses images apparaît alors dans la liste sous la forme `jupiter.ser  #0001`, `#0002`… et se trie exactement comme un FITS : affichage, étirement, débayerisation, zoom et rejet fonctionnent de la même façon.

Les dimensions, la profondeur (8 ou 16 bits), la matrice de Bayer et l'heure de chaque image sont lues dans le fichier. Elles s'affichent dans la colonne de droite, avec le numéro de l'image dans la vidéo.

### Lire la vidéo

Appuyez sur **P** ou cliquez sur **▶ Lire** dans la barre du haut pour faire défiler les images comme une vidéo. Le champ **Vitesse** règle la cadence, de 1 à 60 images par seconde. Appuyez de nouveau sur P pour mettre en pause.

- La lecture tourne en boucle sur la vidéo en cours ; avec des FITS, elle parcourt toute la liste.
- Les images rejetées sont sautées : vous voyez directement ce que donnera la vidéo après tri.
- Vous pouvez rejeter une image pendant la lecture avec Espace.
- Le zoom est conservé, ce qui permet de suivre la turbulence sur un détail de la planète.

La lecture suit l'ordre de la liste : si celle-ci est classée par score, utilisez d'abord **Analyse ▸ Classer par nom** pour revoir la vidéo dans l'ordre de prise de vue. Avec de très grandes images, la lecture peut être plus lente que la vitesse demandée : AstroCheck attend que chaque image soit prête plutôt que d'en sauter.

### Écarter des images d'un SER

Les images d'un SER ne sont pas des fichiers séparés : on ne peut pas les déplacer dans « rejetes ». Pour garder seulement les bonnes, exportez-les une fois le tri fini, en nouvelle vidéo SER ou vers un dossier de traitement (section Créer une vidéo SER). Le SER d'origine n'est jamais modifié.

## Trier les images

Pour rejeter l'image affichée, appuyez sur **Espace**, **Suppr** ou **X**. Son nom passe en rouge barré et AstroCheck affiche aussitôt l'image suivante. Appuyez de nouveau sur la même touche pour la récupérer.

Ce marquage n'agit pas encore sur le disque : tant que vous n'avez pas rangé ou supprimé les rejetées, vous pouvez changer d'avis autant de fois que nécessaire. Le compteur sous la liste indique en permanence le nombre d'images gardées et rejetées.

### Trier plusieurs images d'un coup

Sélectionnez plusieurs noms avec Ctrl+clic ou Maj+clic, puis appuyez sur Espace. Si au moins une image de la sélection est gardée, toutes sont rejetées ; si toutes étaient déjà rejetées, elles sont toutes récupérées. C'est pratique pour écarter d'un seul geste une série gâchée par un passage nuageux.

**Tri ▸ Garder toutes les images** annule tous les marquages.

### Avance automatique

La case **Passer à l'image suivante après marquage** est cochée par défaut. Il suffit alors de garder un doigt sur la flèche bas et l'autre sur Espace. Décochez-la si vous préférez avancer vous-même.

### Ranger ou supprimer les rejetées

Une fois le tri terminé, **Tri ▸ Déplacer les fichiers rejetés dans « rejetes »** (Ctrl+M) déplace chaque image rejetée dans un sous-dossier `rejetes` créé à côté d'elle. Rien n'est perdu : vous pouvez récupérer un fichier à la main plus tard. Les images déplacées disparaissent de la liste, et le dossier d'origine ne contient plus que les bonnes images, prêtes pour Siril ou PixInsight.

**Tri ▸ Supprimer définitivement les fichiers rejetés** efface les fichiers du disque, sans passer par la corbeille. AstroCheck demande une confirmation, avec « Non » proposé par défaut. Préférez le déplacement tant que vous n'êtes pas sûr de vous.

Ces deux commandes concernent les fichiers FITS et CR2. Les images rejetées issues d'une vidéo SER restent dans la liste ; écartez-les en exportant les images gardées.

## Analyser et classer les images

**Analyse ▸ Analyser et classer** (Ctrl+R), ou le bouton du même nom dans la barre du haut, mesure chaque image de la liste. AstroCheck classe ensuite les images de la meilleure, l'**image de référence**, à la plus mauvaise, et présélectionne les mauvaises. Comptez environ un quart de seconde par image ; une barre de progression permet d'arrêter, et les images déjà mesurées sont alors classées.

### Les mesures

Les mesures s'affichent dans des colonnes de la liste. Survolez un titre de colonne pour en avoir l'explication.

| Colonne | Ce qu'elle mesure | Ce qui la dégrade |
| --- | --- | --- |
| N° | Numéro de l'image dans la séquence de prise de vue (1 = la première), compté séparément pour chaque dossier et chaque type ; il ne change ni avec l'analyse, ni avec le tri, ni avec les rejets | — |
| Temp. | Température du capteur en °C, lue dans l'en-tête (CCD-TEMP, DET-TEMP…) ou dans le nom du fichier ; affichée aussi pour les darks et les flats | — |
| Haut. | Hauteur de l'objet au-dessus de l'horizon au moment de la pose, d'après RA, DEC, la date et le lieu d'observation ; affichée quand on la connaît | — |
| Score | Qualité globale, de 0 à 100 ; 100 = meilleure image de la série | Tout ce qui suit |
| FWHM | Largeur des étoiles à mi-hauteur, en pixels du capteur (plus petit = plus net) | Turbulence, mise au point, flou de bougé |
| Excent. | Forme des étoiles : 0 = rondes, proche de 1 = allongées | Suivi, vent, mise en station |
| Rot. | Rotation du champ par rapport à l'image de référence, en degrés (positif = sens inverse des aiguilles d'une montre à l'écran) ; affichée seulement si le champ tourne | Monture alt-azimutale, défaut de mise en station |
| Étoiles | Nombre d'étoiles détectées | Nuages, buée, voile |
| Fond | Niveau du fond de ciel | Lune, aube, voile, pollution lumineuse |
| Raison | Pourquoi l'image est rejetée ; « traînée » signale un satellite ou un avion | — |

Le score combine surtout la FWHM, puis le nombre d'étoiles, leur rondeur et le bruit. Chaque mesure est comparée à la médiane de la série : le score dit comment une image se situe par rapport à ses voisines de la même nuit, pas dans l'absolu.

L'image de référence est marquée d'une étoile ★ et écrite en gras. C'est une bonne candidate comme référence d'alignement dans Siril ou PixInsight. **Analyse ▸ Aller à l'image de référence** (Ctrl+Début) l'affiche à tout moment.

Pour une série planétaire (Lune, Soleil, planètes), il n'y a pas d'étoiles à mesurer. AstroCheck le détecte et classe alors les images par **netteté** : une colonne Netteté remplace FWHM, Excent. et Étoiles.

### Changer l'image de référence

L'image au meilleur score n'est pas toujours la meilleure référence. Avec une monture alt-azimutale, une image prise au milieu de la séance réduit la rotation maximale par rapport aux autres et agrandit la zone utile. Vous pouvez aussi préférer une image précise pour d'autres raisons.

- Affichez l'image voulue, puis **Analyse ▸ Définir l'image affichée comme référence** (Ctrl+Maj+R).
- Ou faites un clic droit sur une brute dans la liste, puis **Définir comme image de référence**. Le même menu permet de rejeter ou garder l'image.
- **Analyse ▸ Revenir à la référence automatique** redonne la main au meilleur score.

L'étoile ★ passe sur la nouvelle référence, qui garde son propre score. La colonne Rot., la zone utile et le graphique sont recalculés par rapport à elle. L'export pour Siril ou PixInsight et le résumé de la nuit utilisent aussi cette référence, et le résumé précise qu'elle a été choisie à la main.

Le Dwarf 3 aligne toujours sur la première image de la séquence. Pour empiler de la même façon, utilisez **Analyse ▸ Prendre la première image comme référence** (Ctrl+Maj+Début). Si la première image n'a pas pu être mesurée, AstroCheck prend la suivante et le signale dans la barre d'état.

La colonne **N°** sert aussi à retrouver n'importe quelle image par sa place dans la séquence : cliquez sur le titre N° pour classer la liste dans l'ordre de prise de vue, choisissez l'image voulue, puis Ctrl+Maj+R. Le message de fin d'analyse, le rapport Ctrl+U et le résumé de la nuit donnent le numéro de la référence, par exemple « n° 212 de la séquence ».

### Changer l'ordre

Après l'analyse, la liste est classée par score, la meilleure en haut. Cliquez sur le titre d'une colonne pour classer selon cette mesure, et cliquez de nouveau pour inverser l'ordre. **Analyse ▸ Classer par nom** revient à l'ordre de prise de vue, utile notamment avant de lire une vidéo.

Le classement par score se prête bien au tri à la main : en partant du bas de la liste, vous passez en revue les images les plus douteuses en premier.

### La présélection

À la fin de l'analyse, AstroCheck rejette d'office les images qui s'écartent nettement du reste de la série. Elles apparaissent en **orange barré**, pour les distinguer des images que vous rejetez vous-même, en rouge. Une fenêtre résume combien d'images sont écartées et pour quelles raisons ; la colonne **Raison** indique pour chaque image pourquoi elle est écartée : flou, étoiles allongées, peu d'étoiles, fond élevé, aucune étoile ou netteté. Elle affiche « à la main » pour vos propres rejets, et « gardée (…) » quand vous avez récupéré une image présélectionnée. Survolez la raison pour voir le détail chiffré, et cliquez sur le titre de la colonne pour regrouper les images par raison.

La présélection n'est qu'une proposition. Parcourez les images orange et appuyez sur Espace pour garder celles qui vous conviennent. Rien n'est déplacé sur le disque tant que vous n'utilisez pas Tri ▸ Déplacer les fichiers rejetés.

### Régler les critères

**Analyse ▸ Critères de présélection** (Ctrl+P) ouvre les réglages. Le nombre d'images qui seraient écartées s'affiche en direct pendant que vous modifiez les valeurs.

| Critère | Par défaut | Une image est écartée si… |
| --- | --- | --- |
| FWHM maximale | 1,30 × médiane | ses étoiles sont 30 % plus larges que la médiane de la série |
| Étoiles : écart maximal | 3 × dispersion | elle a nettement moins d'étoiles que ses voisines |
| Excentricité : écart maximal | 3 × dispersion | ses étoiles sont nettement plus allongées que d'habitude dans la série (jamais en dessous de 0,50) |
| Fond de ciel : écart maximal | 3 × dispersion | son fond est nettement plus haut que celui des autres |
| Netteté minimale (planétaire) | 70 % de la médiane | elle est nettement plus floue qu'une image typique |

La dispersion est la variation normale d'une image à l'autre pendant la nuit. Un écart de 3 × dispersion repère les images vraiment anormales : un voile, un passage nuageux, une rafale de vent, un décrochage du suivi. Baissez la valeur pour une présélection plus sévère, montez-la pour être plus tolérant.

Comme les seuils sont relatifs à la série, ils s'adaptent à votre matériel et aux conditions de la nuit. Le bouton **Restaurer les valeurs par défaut** les remet à zéro.

Les images que vous avez rejetées ou gardées à la main ne sont jamais modifiées par la présélection, même si vous changez les critères ensuite. **Analyse ▸ Annuler la présélection automatique** retire tous les rejets automatiques et laisse les vôtres.

### Enregistrer les mesures

**Analyse ▸ Enregistrer les mesures (CSV)** crée un tableau avec le rang, le score et toutes les mesures de chaque image, ainsi que son statut et les raisons de la présélection. Il s'ouvre dans LibreOffice Calc ou Excel. Si l'en-tête FITS indique la taille des pixels (XPIXSZ) et la focale (FOCALLEN), la FWHM y figure aussi en secondes d'arc.

## Graphique de la nuit

Après l'analyse, un graphique s'ouvre sous l'image. Il trace, pour chaque brute et à l'heure de sa prise de vue, la FWHM, l'excentricité, le nombre d'étoiles et le fond de ciel. **Affichage ▸ Graphique de la nuit**, la touche **G** ou le bouton **Graphique** l'affichent ou le masquent.

- Chaque point est une image : bleu si elle est gardée, orange si la présélection l'a écartée, rouge si vous l'avez rejetée.
- La ligne en tirets est la médiane de la série, la ligne pointillée orange le seuil de la présélection.
- Un trait vertical marque l'image affichée.
- Survolez un point pour lire sa valeur, cliquez dessus pour afficher l'image.
- Si le champ tourne (monture alt-azimutale), un panneau Rotation montre l'angle de chaque image par rapport à la référence.

Si le lieu d'observation est connu, un panneau **Hauteur** montre la hauteur de l'objet. Pour une série prise sur plusieurs nuits, chaque nuit a sa portion de graphique, séparée par un trait en tirets et marquée « nuit du … ».

Le graphique rend visible en un coup d'œil ce que les colonnes cachent : un passage nuageux (le fond monte, les étoiles baissent pendant quelques minutes), la turbulence qui varie, ou un défaut de suivi régulier (l'excentricité qui oscille avec la période de la vis sans fin). Pour un SER planétaire, il affiche la netteté et le fond.

Le graphique est en heure locale : les dates des fichiers FITS et SER, enregistrées en temps universel, sont converties selon le fuseau de votre ordinateur. Sans date dans les fichiers, les images sont simplement numérotées dans l'ordre.

## Inspecter le champ : vue des coins

**Affichage ▸ Vue des coins** (touche **C**, ou le bouton **Coins**) remplace l'image par une mosaïque de neuf vignettes à 100 % : les quatre coins, les quatre bords et le centre, côte à côte. C'est le moyen le plus simple de comparer la forme des étoiles dans tout le champ. Le zoom et le défilement des images fonctionnent comme d'habitude.

Une fois l'analyse faite, chaque vignette indique la FWHM et l'excentricité des étoiles de cette zone, et la barre du bas donne un diagnostic.

| Diagnostic | Ce qu'il signifie | Que vérifier |
| --- | --- | --- |
| Champ homogène | Les étoiles sont semblables partout | Rien |
| Inclinaison probable | Un coin est nettement plus flou que le coin opposé | Le capteur n'est pas perpendiculaire à l'axe optique : bague d'inclinaison (tilt), serrage du porte-oculaire, adaptateurs |
| Courbure de champ | Les quatre coins sont plus flous que le centre | Absence de correcteur ou d'aplatisseur, ou tirage incorrect |
| Coma ou mauvais tirage | Les coins sont plus flous et leurs étoiles allongées | Distance entre correcteur et capteur (backfocus), collimation |

Le message de fin d'analyse donne aussi ce diagnostic pour toute la série, calculé sur la médiane de toutes les brutes, ce qui est plus fiable qu'une seule image. Comptez au moins une centaine d'étoiles par image pour un résultat fiable.

## Rotation de champ et zone utile

Une monture alt-azimutale, comme celle du Dwarf 3 ou du Seestar, suit les étoiles sans tourner autour de l'axe du pôle : le champ pivote lentement pendant la séance. Les logiciels d'empilement corrigent cette rotation, mais deux effets restent : les bords de l'image empilée sont incomplets et doivent être rognés, et pendant chaque pose les étoiles des coins s'allongent en petits arcs.

### Mesurer la rotation

Pendant l'analyse, AstroCheck relève la position des étoiles les plus brillantes de chaque brute, puis retrouve le même motif d'étoiles dans l'image de référence. Il en déduit la rotation et le décalage de chaque image. La méthode ne dépend ni de l'ampleur de la rotation ni du décalage : une image trop tournée pour être comparée directement à la référence est reliée par ses voisines dans le temps.

- La colonne **Rot.** donne l'angle de chaque image par rapport à la référence. Survolez une ligne pour lire aussi la vitesse de rotation à ce moment, en degrés par heure.
- Le **graphique de la nuit** ajoute un panneau Rotation.
- Une image qui n'a pas pu être alignée (nuages, trop peu d'étoiles) affiche un tiret.

Avec une monture équatoriale bien mise en station, la rotation est nulle et la colonne reste masquée. Une petite rotation régulière peut signaler un défaut de mise en station.

### Le rapport

**Analyse ▸ Rotation de champ et zone utile** (Ctrl+U) ouvre un rapport complet :

- l'amplitude de la rotation sur la série, sa vitesse médiane et maximale, et l'heure où elle est la plus rapide ;
- le filé dans les coins pendant une pose, en pixels, comparé à la FWHM des étoiles, et la pose maximale conseillée pour que ce filé reste sous une demi-FWHM ;
- la zone couverte par toutes les images gardées, le plus grand rectangle utile, et combien de pixels rogner de chaque côté ;
- les images qui réduisent le plus la zone, avec le gain de surface si on les rejetait.

La vitesse de rotation dépend de la position de l'objet : elle est la plus forte quand il passe au méridien, haut dans le ciel, et faible quand il est bas vers l'est ou l'ouest. Si le rapport signale des étoiles allongées dans les coins, raccourcissez les poses pour les objets proches du zénith.

### Afficher la zone utile

**Affichage ▸ Zone utile après empilement** (touche **Z**, ou le bouton **Zone**) assombrit sur l'image ce qui sera perdu après empilement. Le contour orange entoure la zone couverte par toutes les images gardées ; le rectangle vert en tirets est le recadrage conseillé. La barre d'état donne ses dimensions.

La zone ne compte que les images gardées : rejetez une image très décalée et elle s'agrandit aussitôt. Elle est tracée sur chaque image à sa propre place, ce qui permet de voir comment chacune se situe par rapport au résultat final.

Le décalage entre images compte aussi : le dithering ou une dérive du suivi réduisent la zone utile, même sans rotation.

## Bilan de la séance

**Analyse ▸ Bilan de la séance** (Ctrl+B) rassemble ce qu'il faut savoir sur la série en cours. Le même bilan est écrit dans le résumé de la nuit à chaque export.

### Ce que coûte le tri

Le bilan compare les images gardées à l'ensemble de la série :

- le temps de pose perdu, en pourcentage et en minutes ;
- la perte de rapport signal/bruit, qui varie comme la racine du temps de pose : écarter 25 % des images coûte environ 13 % de signal/bruit ;
- la FWHM médiane avec toutes les images et avec les seules images gardées.

Écarter une image vaut la peine quand elle dégrade la finesse plus qu'elle n'apporte de signal. Le message de fin d'analyse donne aussi ce coût en une ligne.

### Plusieurs nuits

Ajoutez les dossiers de plusieurs nuits sur le même objet : AstroCheck les reconnaît d'après les dates (une image prise à 2 h du matin appartient à la nuit commencée la veille). Le bilan donne, pour chaque nuit, les heures de début et de fin, le nombre d'images gardées, le temps de pose, la FWHM, le fond, la température du capteur et la hauteur de l'objet, puis la meilleure nuit pour la finesse. Le graphique montre les nuits côte à côte, et l'export réunit toutes les images gardées dans un seul empilement.

### Mise au point et température

Quand le capteur se refroidit pendant la nuit, le tube se contracte et la mise au point dérive. AstroCheck compare la FWHM à la température du capteur (au moins 10 images et 1,5 °C d'écart) et, si l'objet a changé de hauteur, retire l'effet de l'atmosphère. S'il trouve un lien net, il indique de combien la FWHM augmente par degré et conseille un intervalle pour refaire la mise au point, par exemple « tous les 2 °C ».

### Hauteur de l'objet et lieu d'observation

La hauteur de l'objet se calcule à partir de RA et DEC dans l'en-tête, de la date et du lieu. Le lieu vient de l'en-tête (SITELAT, SITELONG) ou, à défaut, de **Analyse ▸ Lieu d'observation** : indiquez-y une fois la latitude et la longitude de votre site (longitude positive à l'est de Greenwich).

- La colonne **Haut.** et le panneau Hauteur du graphique montrent la hauteur image par image.
- Le bilan signale les images prises sous 30°, où l'atmosphère épaissit les étoiles.
- Avec une monture alt-azimutale, il compare la rotation mesurée à la rotation attendue, et indique les heures où la rotation allonge les étoiles des coins pendant les poses, par exemple quand l'objet passe près du zénith.

### Traînées de satellites et d'avions

Pendant l'analyse, AstroCheck cherche les traînées rectilignes qui traversent l'image. Une image concernée affiche « traînée » dans la colonne Raison, mais n'est pas rejetée : la réjection des pixels à l'empilement (sigma clipping) efface en général ces traînées, d'autant mieux que les images sont nombreuses. Les aigrettes de diffraction des étoiles brillantes et les nébuleuses ne sont pas prises pour des traînées. Une traînée très faible ou très courte peut passer inaperçue.

### Journal des cibles pour AstroPlan

AstroCheck tient un **journal** de vos séances : pour chaque cible, chaque nuit et chaque série (caméra, filtre, pose, gain), le nombre de brutes, combien vous en avez gardé, le temps de pose gardé et la FWHM médiane. AstroPlan lit ce journal et affiche, cible par cible, le temps déjà accumulé face au temps conseillé, et le nombre de nuits qu'il reste à faire.

- Le journal est mis à jour à chaque **export**, et à la demande par **Analyse ▸ Mettre à jour le journal AstroPlan** (Ctrl+J).
- Il est enregistré dans `~/.config/astrocheck/journal.json`. Une nouvelle mise à jour remplace les entrées de la même nuit et de la même série : vous pouvez trier à nouveau sans créer de doublon.
- Le nom de la cible vient du mot-clé OBJECT de l'en-tête, sinon du nom du dossier du Dwarf (`DWARF_RAW_<cible>_EXP_…`) ou du fichier ASIAIR (`Light_<cible>_300.0s_…`).

Pour le voir dans AstroPlan, importez le fichier dans l'onglet **Journal**, ou copiez-le à côté de la page sur votre serveur :

```bash
scp ~/.config/astrocheck/journal.json serveur:/var/www/astroplan/site/journal.json
```

## Darks, flats et offsets

AstroCheck reconnaît le type de chaque image et l'affiche dans la colonne **Type** : brute, dark, flat, offset ou dark de flat. Il lit d'abord le mot-clé IMAGETYP de l'en-tête FITS, écrit par la plupart des logiciels d'acquisition. Sans ce mot-clé, notamment pour les CR2, il se fie au nom du fichier, puis à celui du dossier (Darks, Flats, Offsets, Bias…).

L'analyse, le classement, la présélection et le graphique ne concernent que les brutes. Vous pouvez rejeter un dark ou un flat à la main avec Espace, comme une brute.

### Vérifier les calibrations

**Analyse ▸ Vérifier les fichiers de calibration** (Ctrl+K) compare les calibrations gardées aux brutes :

- **darks** : même temps de pose, même gain, température à 2 °C près (écart réglable dans le contrôle des darks), même taille et même binning ;
- **flats** : même taille, même binning et même filtre que les brutes ;
- **darks de flat** : même temps de pose que les flats ;
- **offsets** : même gain et pose la plus courte possible.

Le rapport signale aussi ce qui manque, par exemple « pas de flats : vignetage et poussières ne seront pas corrigés », et prévient si les brutes mélangent plusieurs filtres ou temps de pose. Mieux vaut découvrir un dark à la mauvaise température ici qu'après une heure de traitement.

### Contrôle des darks

**Analyse ▸ Contrôle des darks** (Ctrl+Maj+K) vérifie les darks un par un. AstroCheck mesure le niveau et le bruit de chaque dark, puis affiche deux tableaux.

- **Brutes gardées et darks compatibles** : pour chaque groupe de brutes de même pose, même gain et même binning, la plage de température des brutes, le nombre de darks compatibles et leur plage de température, et la part des brutes qui ont un dark à la bonne température (**Couverture**).
- **Darks** : la pose, le gain, le binning, la température, le niveau médian et le bruit de chaque dark, avec un verdict.

| Verdict | Ce qu'il signifie |
| --- | --- |
| conforme | Le dark correspond aux brutes |
| pose différente | Aucune brute n'a ce temps de pose |
| gain différent | Le gain ne correspond à aucune brute de même pose |
| taille ou binning différent | Le dark ne couvre pas le même capteur ou le même binning |
| température hors plage | Le capteur était plus chaud ou plus froid que pendant les brutes, au-delà de l'écart toléré |
| anormalement clair | Le niveau est nettement au-dessus des autres darks : lumière parasite, capot mal fermé |

Réglez **l'écart de température toléré** en haut de la fenêtre : 2 °C par défaut, à garder serré pour une caméra refroidie, à élargir un peu pour un capteur non refroidi. AstroCheck retient ce réglage, qui sert aussi à la vérification des calibrations (Ctrl+K).

Sous les tableaux, des conseils indiquent les darks à refaire : pose, gain et plage de température à viser. Comptez 15 à 20 darks au moins pour un master dark propre. Le bouton **Rejeter les darks non conformes** les marque comme rejetés, avec la raison « dark non conforme » dans la liste. Ils ne seront ni exportés ni utilisés par le script Siril.

### Contrôle des flats

**Analyse ▸ Contrôle des flats** (Ctrl+Maj+F) mesure chaque flat et donne un verdict.

| Verdict | Ce qu'il signifie |
| --- | --- |
| conforme | Niveau correct, motif semblable aux autres flats |
| trop sombre, trop clair | Le niveau sort de 8 à 85 % de la dynamique ; visez 30 à 60 % |
| saturé | Plus de 0,5 % des pixels sont saturés |
| motif différent | Le vignetage ou les poussières ne ressemblent pas aux autres flats : autre filtre, poussière déplacée, caméra tournée entre les flats |
| niveau très différent | Le niveau s'écarte de plus de 30 % de celui des autres flats |
| filtre … | Le filtre ne correspond pas à celui des brutes |

La fenêtre indique aussi le vignetage mesuré (lumière perdue dans les coins) et rappelle d'en faire 20 à 30. Le bouton **Rejeter les flats non conformes** les marque comme rejetés, avec la raison « flat non conforme ».

## Dwarf 3

AstroCheck lit directement les dossiers du Dwarf 3 copiés sur l'ordinateur.

- **Brutes** : ajoutez le dossier `DWARF_RAW_<objet>_EXP_<pose>_GAIN_<gain>_<date>`. L'en-tête de chaque FITS donne la pose, le gain, la température du capteur (DET-TEMP) et la monture alt-azimutale (EQMODE = 0).
- **Darks** : ajoutez le dossier `DWARF_DARK`. AstroCheck reconnaît les darks à leur dossier et lit la pose, le gain et la température dans leur nom, par exemple `dark_exp_15.000000_gain_60_bin_1_38C`, quand l'en-tête ne les donne pas.
- La bibliothèque de darks empilés du Dwarf (`CALI_FRAME`, fichiers PNG) n'est pas lue : utilisez les darks individuels.

Le capteur du Dwarf 3 n'est pas refroidi : sa température suit celle de la nuit et varie pendant la séance. La colonne Temp. et le contrôle des darks (Ctrl+Maj+K) montrent si vos darks couvrent bien la plage de température des brutes. Faites-les de préférence juste après la séance, objectif fermé, avec la même pose et le même gain.

La monture alt-azimutale fait tourner le champ : voyez la section « Rotation de champ et zone utile » pour mesurer cette rotation, choisir une référence au milieu de la séance et prévoir le recadrage.

Le Dwarf 3 a deux caméras, TELE et WIDE. Quand la liste contient plusieurs **séries** de brutes, c'est-à-dire des caméras, filtres, poses ou gains différents, un choix **Série** apparaît au-dessus de la liste. L'analyse, la présélection, la référence, la rotation, le bilan et l'export portent sur la série choisie ; les brutes des autres séries sont masquées et restent intactes. Les calibrations (darks, flats, offsets) restent visibles quelle que soit la série.

## Créer une vidéo SER

Le menu **Export** assemble des images (FITS, CR2 ou images d'un autre SER) en un seul fichier SER, le format vidéo lu par Siril, AutoStakkert, PIPP ou SER Player. Il propose deux choix, qui diffèrent seulement par les images retenues.

| Commande | Images retenues | Usage courant |
| --- | --- | --- |
| Créer un SER avec toutes les images gardées (Ctrl+Maj+E) | Toute la liste, sauf les images rejetées | Après un tri, pour empiler tout le reste |
| Créer un SER avec les images sélectionnées (Ctrl+E) | Seulement les noms surlignés à la souris | Pour isoler une partie de la séance |

Attention : avec les images sélectionnées, c'est la sélection qui décide. Une image rejetée mais surlignée sera incluse.

AstroCheck propose d'enregistrer le fichier à côté des images, sous le nom du dossier. Une barre de progression permet d'annuler à tout moment ; le fichier n'apparaît qu'une fois la création terminée. La taille finale s'affiche à la fin.

### Ce que contient le SER

- **Les pixels bruts**, sans étirement ni débayerisation : l'étirement choisi à l'écran n'a aucun effet sur le SER.
- **La matrice de Bayer** (RGGB, GRBG…) reprise du mot-clé BAYERPAT, pour que le logiciel de traitement débayerise correctement. Les FITS déjà en couleur sont écrits en RGB.
- **La profondeur** : 8 bits si les FITS sont en 8 bits, 16 bits sinon. Les FITS en virgule flottante compris entre 0 et 1 sont ramenés sur l'échelle 0 – 65535.
- **Les dates de prise de vue** tirées de DATE-OBS, quand toutes les images en ont une.
- **Le télescope, l'instrument et l'observateur**, s'ils figurent dans l'en-tête de la première image.

Les images sont écrites dans l'ordre de la liste : si celle-ci est classée par score, la vidéo commencera par la meilleure image.

Toutes les images doivent avoir les mêmes dimensions. Si l'une diffère, par exemple à cause d'un binning différent, la création s'arrête et le fichier fautif est indiqué.

Un SER n'est pas compressé : il pèse à peu près la somme des FITS qu'il contient. Prévoyez la place sur le disque avant d'assembler une longue série.

### Exporter vers un dossier pour Siril ou PixInsight

**Export ▸ Exporter vers un dossier pour Siril / PixInsight** (Ctrl+Maj+S) rassemble les images retenues dans un dossier de travail, prêt pour le prétraitement. Choisissez d'abord votre logiciel, **PixInsight** ou **Siril** : les options s'adaptent, et AstroCheck retient votre choix et vos réglages pour la fois suivante. Une fenêtre propose les options suivantes.

| Option | Effet |
| --- | --- |
| Images à exporter | Toutes les images gardées, ou seulement celles surlignées dans la liste |
| Destination | Par défaut un dossier « traitement » à côté de vos images |
| Structure Siril | Range les brutes dans `traitement/lights`, et les calibrations dans darks, flats, biases et darkflats : la structure attendue par Siril (cochée par défaut) |
| Copier ou créer des liens | Les liens sont instantanés et ne prennent aucune place : idéal pour de grosses séries, tant que les originaux restent en place |
| Convertir les CR2 en FITS | Décochée, les CR2 sont gardés tels quels, car Siril et PixInsight les lisent directement |
| Renommer light_00001… | Numérote les fichiers en mettant l'image de référence en premier ; le reste suit l'ordre de prise de vue |
| Joindre mesures.csv | Ajoute le tableau des mesures dans le dossier, si l'analyse a été faite |

Les images sont exportées dans l'ordre de prise de vue, quel que soit le classement de la liste. Vos fichiers d'origine ne sont jamais modifiés, et rien n'est écrasé dans la destination : un fichier de même nom reçoit un suffixe `_1`. AstroCheck vérifie aussi la place libre sur le disque avant de copier.

La numérotation sert surtout avec Siril, qui aligne par défaut sur la première image de la séquence : votre meilleure image devient ainsi la référence sans réglage. PixInsight choisit sa référence lui-même, la numérotation y est donc inutile.

Avec la structure Siril, AstroCheck peut aussi écrire un **script de prétraitement** `traitement.ssf` à la racine du dossier. Il est adapté à ce que vous exportez : masters d'offset, de dark de flat, de flat et de dark s'ils sont présents, calibration des brutes (avec dématriçage pour un capteur couleur), alignement sur la première image, puis empilement. Le résultat est enregistré dans le dossier sous un nom comme `resultat_145x60s.fit`. Le script n'est pas écrit si les brutes mélangent plusieurs filtres. Le rapport de vérification des calibrations s'affiche aussi à la fin de l'export.

Pour pondérer l'empilement par la qualité mesurée par AstroCheck, cochez **Écrire le score de chaque brute dans les copies**. Le score, de 0 à 1, est écrit dans le mot-clé FITS `ACWEIGHT` des brutes copiées. Dans PixInsight, ImageIntegration peut s'en servir : *Image Weighting* ▸ *Weights* = *FITS keyword*, *Weight keyword* = `ACWEIGHT`. L'option n'existe qu'avec « Copier les fichiers » : un lien modifierait l'original, et AstroCheck ne touche jamais à vos fichiers. Les fichiers compressés `.gz` et les CR2 ne reçoivent pas de poids.

### Lancer Siril depuis AstroCheck

Avec Siril, cochez **Lancer Siril tout de suite et afficher le résultat** : AstroCheck exécute le script avec `siril-cli`, la version de Siril sans fenêtre, installée normalement ou en Flatpak. Une fenêtre affiche le journal de Siril en direct, et un bouton permet de l'arrêter. À la fin, l'image empilée s'ajoute à la liste avec le type **Résultat** et s'affiche aussitôt. **Traitement ▸ Lancer un script Siril** exécute de la même façon n'importe quel script `.ssf`, dans le dossier où il se trouve.

Les images de type Résultat sont reconnues aussi quand vous les ouvrez plus tard (mots-clés STACKCNT ou NCOMBINE). Elles sont exclues de l'analyse et du classement.

À la fin de chaque passage, le journal complet de Siril est enregistré dans le dossier de travail sous un nom comme `siril_20261014_133512.log`. Le bouton **Enregistrer le journal…** permet aussi de le sauvegarder ailleurs, par exemple pour le joindre à une demande d'aide.

### Filtres bi, tri et quadri-bande : HOO et SHO synthétique

Avec une caméra couleur derrière un filtre multibande (L-eXtreme, L-eNhance, ALP-T…), AstroCheck sépare les raies de l'image empilée :

- **Ha** : canal rouge ;
- **OIII** : moyenne des canaux vert et bleu, ramenée au niveau du Ha pour équilibrer les couleurs.

Il en tire les palettes suivantes, toutes linéaires, à étirer ensuite dans Siril ou PixInsight :

| Fichier | Composition |
| --- | --- |
| `…_Ha.fit`, `…_OIII.fit` | Les deux raies séparées, pour composer vous-même dans PixInsight |
| `…_HOO.fit` | R = Ha, V = OIII, B = OIII : la palette naturelle de ces filtres |
| `…_SHO_synthetique.fit` | R = Ha, V = mélange Ha et OIII (50 % par défaut), B = OIII |

Un capteur couleur ne peut pas séparer le SII du Ha : les deux tombent sur les pixels rouges. Le SHO obtenu imite les couleurs de la palette Hubble, mais ce n'est pas un vrai SHO, qui demande une caméra monochrome ou un filtre SII seul. Avec un filtre quadri-bande, le Hβ et le SII s'ajoutent simplement aux canaux OIII et Ha.

Deux façons de l'utiliser :

- à l'export vers Siril, choisissez **Après l'empilement : filtre multibande** ; les images composées s'ouvrent avec le résultat ;
- sur n'importe quel empilement couleur en FITS, y compris venant de PixInsight : **Traitement ▸ Composer en bande étroite**. Vous y choisissez les palettes et la part de Ha dans le vert du SHO.

### Le résumé de la nuit

Chaque export écrit un fichier `astrocheck_resume.txt` à garder avec vos données : nombre d'images par type, image de référence (avec son nom après export), diagnostic du champ, critères de présélection, liste des images écartées avec leurs raisons, et vérification des calibrations. Avec PixInsight, c'est là que vous retrouvez l'image à choisir comme référence dans WBPP (Registration Reference Image : manual).

À la fin, AstroCheck rappelle la marche à suivre et propose d'ouvrir le dossier.

- **Siril** : choisissez le dossier « traitement » comme répertoire de travail, vérifiez que vos darks, flats et offsets sont bien dans les sous-dossiers `darks`, `flats` et `biases`, puis tapez @traitement.ssf dans la ligne de commande de Siril (ou passez par le menu Scripts).
- **PixInsight** : dans WeightedBatchPreprocessing, ajoutez le contenu du dossier `lights` avec le bouton Add Files.

Chaque image issue d'une vidéo SER devient un fichier FITS nommé d'après la vidéo et son numéro, par exemple `jupiter_0042.fits`. Les pixels sont conservés tels quels, en 8 ou 16 bits, avec la matrice de Bayer, l'heure de prise de vue et le numéro de l'image dans l'en-tête. Si vous demandez la conversion, les CR2 deviennent des FITS 16 bits portant le même nom, avec la matrice de Bayer et les informations EXIF dans l'en-tête.

Cette commande sert aussi à extraire quelques images d'un SER, ou à le convertir entièrement en FITS pour un logiciel qui ne lit pas les vidéos.

## Raccourcis clavier

| Touche | Action |
| --- | --- |
| ↑ / ↓ | Image précédente / suivante |
| Début / Fin | Première / dernière image |
| Espace, Suppr ou X | Rejeter ou récupérer l'image (ou la sélection) |
| Ctrl+clic, Maj+clic | Sélectionner plusieurs images |
| P | Lire la liste comme une vidéo / mettre en pause |
| F ou double-clic | Ajuster l'image à la fenêtre |
| 1 | Zoom à 100 % |
| V | Retourner l'image verticalement |
| C | Vue des coins |
| G | Graphique de la nuit |
| Z | Afficher la zone utile après empilement |
| Ctrl+U | Rotation de champ et zone utile |
| Ctrl+Maj+R | Définir l'image affichée comme référence |
| Ctrl+Maj+Début | Prendre la première image comme référence (comme le Dwarf 3) |
| Ctrl+Maj+K | Contrôle des darks |
| Ctrl+B | Bilan de la séance |
| Ctrl+Maj+F | Contrôle des flats |
| Ctrl+J | Mettre à jour le journal AstroPlan |
| Molette | Zoomer / dézoomer |
| Ctrl+R | Analyser et classer les images |
| Ctrl+P | Critères de présélection |
| Ctrl+K | Vérifier les fichiers de calibration |
| Ctrl+Début | Aller à l'image de référence |
| Ctrl+O | Ajouter des fichiers FITS, SER ou CR2 |
| Ctrl+D | Ajouter un dossier |
| Ctrl+M | Déplacer les fichiers rejetés dans « rejetes » |
| Ctrl+E | Créer un SER avec les images sélectionnées |
| Ctrl+Maj+E | Créer un SER avec toutes les images gardées |
| Ctrl+Maj+S | Exporter vers un dossier pour Siril / PixInsight |
| Ctrl+Q | Quitter |

Les touches de tri et de navigation agissent sur la liste de gauche. Si elles semblent sans effet, cliquez une fois sur un nom de fichier pour redonner la main à la liste.

## Questions fréquentes

**Le programme ne se lance pas et parle de « ModuleNotFoundError ».**
Une dépendance manque. Relancez la commande d'installation de votre distribution (section Installation), puis réessayez.

**Mon dossier est déposé mais la liste reste vide.**
Vérifiez que les fichiers ont bien une extension `.fit`, `.fits`, `.fts`, `.ser` ou `.cr2`, et qu'ils sont directement dans le dossier déposé, pas dans un sous-dossier. Le message « Aucune nouvelle image trouvée » s'affiche alors en bas de la fenêtre.

**Une image affiche « Lecture impossible ».**
Le fichier est probablement incomplet, par exemple si l'acquisition a été interrompue pendant l'enregistrement. Le message exact apparaît dans la colonne de droite. Rejetez simplement ce fichier.

**Mes images couleur restent en noir et blanc malgré la case Débayeriser.**
L'en-tête ne contient pas de mot-clé BAYERPAT. Vérifiez dans la colonne de droite ; certains logiciels d'acquisition ne l'écrivent que si l'option correspondante est activée.

**Toutes mes images se ressemblent alors que la nuit a été nuageuse.**
L'auto-stretch compense les variations de fond. Lancez l'analyse (Ctrl+R) et regardez les colonnes Étoiles et Fond, ou passez en « Percentiles » pour comparer les images à l'œil.

**Où sont passées les images rejetées ?**
Dans un sous-dossier `rejetes`, à côté des images d'origine. Si deux fichiers portent le même nom, le second reçoit un suffixe `_1`, `_2`… : rien n'est écrasé.

**Siril affiche mon SER à l'envers.**
Le SER reprend les lignes dans l'ordre exact où elles sont stockées dans les FITS, pour ne pas fausser la matrice de Bayer. Le sens n'a aucune incidence sur l'alignement et l'empilement ; retournez l'image finale si besoin.

**La création du SER s'arrête sur « dimensions différentes ».**
Une des images n'a pas la même taille que la première, souvent à cause d'un binning ou d'un recadrage différent. Retirez-la de la sélection ou rejetez-la, puis relancez.

**Un SER apparaît en noir et blanc avec une trame en damier.**
C'est une vidéo de caméra couleur enregistrée en brut. Cochez **Débayeriser (couleur)** ; si rien ne change, la vidéo ne précise pas sa matrice de Bayer.

**L'ajout d'un très long SER prend quelques secondes.**
Chaque image devient une ligne de la liste : une vidéo de 20 000 images donne 20 000 lignes. Les images ne sont lues qu'au moment de les afficher ou de les analyser, la mémoire n'est donc pas un problème.

**Un CR2 affiche « installez le programme dcraw ».**
AstroCheck a besoin de dcraw ou du module rawpy pour décoder les RAW. Installez dcraw avec la commande de votre distribution (section Installation), puis rouvrez le fichier.

**Un CR2 affiche « Lecture impossible » alors que dcraw est installé.**
Le boîtier est peut-être plus récent que votre version de dcraw. Installez rawpy (`pip install --user --break-system-packages rawpy`), qui suit mieux les nouveaux modèles. Les CR3 des boîtiers Canon récents ne sont pas encore pris en charge.

**La présélection écarte trop d'images, ou pas assez.**
Ouvrez Analyse ▸ Critères de présélection (Ctrl+P) et ajustez les seuils en regardant le compteur en direct. Les critères comparent chaque image à la série : si toute la nuit a des étoiles un peu allongées, elles ne sont pas écartées pour autant, seules les images nettement pires le sont. Pour être plus sévère, baissez les écarts maximaux à 2 ou 2,5. Si plus de la moitié des images sont écartées, AstroCheck vous prévient.

**Une image voilée a pourtant un bon score.**
Un voile léger réduit peu la netteté des étoiles. Il fait en revanche monter la colonne Fond : classez par Fond (clic sur le titre) pour repérer les images voilées, ou baissez l'écart maximal du fond dans les critères.

**Les FWHM d'un appareil photo diffèrent de celles de Siril.**
Sur une image couleur, AstroCheck mesure les étoiles après avoir regroupé chaque carré de 4 pixels de la matrice de Bayer, puis convertit le résultat en pixels du capteur. Les valeurs restent comparables d'une image à l'autre, ce qui est l'essentiel pour le tri, mais peuvent différer un peu de celles d'autres logiciels.

**Siril lance le script mais n'exécute rien (« terminée avec succès » en quelques millisecondes).**
Siril 1.4 ignore un script qui contient des lettres accentuées, même dans les commentaires. Les scripts créés par AstroCheck depuis la version d'octobre 2026 sont entièrement sans accents. Pour un ancien script, supprimez les lignes de commentaire accentuées dans l'éditeur de script de Siril. Vérifiez aussi que le répertoire de travail de Siril est bien le dossier « traitement ».
