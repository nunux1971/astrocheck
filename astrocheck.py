#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AstroCheck — contrôle et tri des nuits d'astrophotographie
(anciennement fitsview).

- Glisser-déposer de fichiers FITS, de vidéos SER, de RAW Canon (CR2)
  ou de dossiers
- Liste des images ; une vidéo SER y apparaît image par image
- Auto-ajustement de l'histogramme (type STF de PixInsight)
- Débayerisation rapide (super-pixel) si la matrice de Bayer est connue
- Lecture de la liste comme une vidéo (SER ou série de FITS)
- Mesure de chaque image (FWHM, étoiles, excentricité, fond, netteté),
  classement de la meilleure (référence) à la plus mauvaise
- Présélection automatique des mauvaises images, critères réglables
- Marquage des images à rejeter (Espace, Suppr ou X)
- Graphique des mesures au fil de la nuit, vue des coins (inclinaison,
  courbure de champ)
- Reconnaissance des darks, flats et offsets, vérification de leur cohérence
  avec les brutes, export en structure Siril avec script de prétraitement
- Rangement des fichiers rejetés dans « rejetes » (ou suppression)
- Export des images choisies en vidéo SER, ou vers un dossier de
  traitement pour Siril ou PixInsight (copies ou liens)

Dépendances : python3, PyQt6, numpy, astropy
Pour les CR2 : le module Python rawpy, ou à défaut le programme dcraw
"""

import os
import sys
import shutil
import csv
import re
import struct
import unicodedata
import subprocess
import functools
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import numpy as np
from astropy.io import fits

from PyQt6.QtCore import (Qt, QObject, QRunnable, QThread, QThreadPool, QTimer,
                          QUrl, QPointF, QRectF, QSettings, QProcess,
                          pyqtSignal)
from PyQt6.QtGui import (QImage, QPixmap, QColor, QFont, QPainter, QKeySequence,
                         QIcon, QDesktopServices, QCursor, QPen, QPalette,
                         QPolygonF, QPainterPath, QAction)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSplitter,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFileDialog,
    QMessageBox, QPlainTextEdit, QWidget, QVBoxLayout, QLabel, QComboBox,
    QCheckBox, QAbstractItemView, QProgressDialog, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QDialog, QDialogButtonBox,
    QFormLayout, QDoubleSpinBox, QRadioButton, QLineEdit, QPushButton,
    QGroupBox, QHBoxLayout, QDockWidget, QToolTip, QTabBar, QMenu,
    QTableWidget, QTableWidgetItem,
)

APP_NAME = "AstroCheck"
APP_ID = "astrocheck"           # fichiers, icône, réglages
FITS_EXT = (".fit", ".fits", ".fts", ".fit.gz", ".fits.gz")
SER_EXT = (".ser",)
RAW_EXT = (".cr2",)
REJECT_DIR = "rejetes"
SUMMARY_NAME = "astrocheck_resume.txt"
CACHE_MAX = 12

# Chaque image de la liste est désignée par une « source » :
#   (chemin, -1) pour un fichier FITS, (chemin, n) pour l'image n d'un SER
USER_ROLE = Qt.ItemDataRole.UserRole.value
FOREGROUND_ROLE = Qt.ItemDataRole.ForegroundRole.value
ROLE_SRC = USER_ROLE
ROLE_REJ = USER_ROLE + 1
ROLE_AUTO = USER_ROLE + 2     # rejet décidé par la présélection
ROLE_MANUAL = USER_ROLE + 3   # l'utilisateur a tranché à la main
ROLE_SORT = USER_ROLE + 4     # valeur numérique de tri d'une colonne
ROLE_REASONS = USER_ROLE + 5  # raisons de la présélection
ROLE_META = USER_ROLE + 6     # type, pose, gain, température, date…
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def set_align(item, col, flag):
    """Alignement d'une cellule (les anciens PyQt6 veulent un entier)."""
    args = (flag,) if col is None else (col, flag)
    try:
        item.setTextAlignment(*args)
    except TypeError:
        item.setTextAlignment(*args[:-1], int(flag.value))

COLUMNS = ["Image", "N°", "Type", "Temp.", "Haut.", "Score", "FWHM",
           "Excent.", "Rot.", "Étoiles", "Fond", "Netteté", "Raison"]
(COL_NAME, COL_NUM, COL_TYPE, COL_TEMP, COL_ALT, COL_SCORE, COL_FWHM,
 COL_ECC, COL_ROT, COL_STARS, COL_BG, COL_SHARP, COL_REASON) = \
    range(len(COLUMNS))
LEFT_COLUMNS = (COL_NAME, COL_TYPE, COL_REASON)

# libellés courts de la colonne Raison
REASON_SHORT = {"dark": "dark non conforme", "flatbad": "flat non conforme", "fwhm": "flou", "ecc": "étoiles allongées",
                "stars": "peu d'étoiles", "bg": "fond élevé",
                "none": "aucune étoile", "sharp": "netteté"}

# nom affiché -> (type, paramètre a, paramètre b)
#   stf : a = écrêtage des ombres (en MAD), b = fond de ciel cible
#   pct : a, b = percentiles bas / haut
STRETCH_MODES = OrderedDict([
    ("Auto-stretch (STF)", ("stf", -2.8, 0.25)),
    ("Auto-stretch doux", ("stf", -3.5, 0.15)),
    ("Auto-stretch fort", ("stf", -2.0, 0.40)),
    ("Percentiles 0,5 – 99,8 %", ("pct", 0.5, 99.8)),
    ("Linéaire min – max", ("lin", None, None)),
])

BAYER_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


class Cancelled(Exception):
    pass


def is_fits(path):
    return path.lower().endswith(FITS_EXT)


def is_ser(path):
    return path.lower().endswith(SER_EXT)


def is_raw(path):
    return path.lower().endswith(RAW_EXT)


def is_supported(path):
    return is_fits(path) or is_ser(path) or is_raw(path)


def natural_key(name):
    """Clé de tri « naturelle » : img_2 avant img_10."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def src_name(src):
    path, frame = src
    name = os.path.basename(path)
    return name if frame < 0 else f"{name}  #{frame + 1}"


def to_ascii(text, n=68):
    return str(text).encode("ascii", "replace").decode("ascii")[:n]


def unique_path(path):
    if not os.path.exists(path) and not os.path.islink(path):
        return path
    stem, ext = split_ext(os.path.basename(path))
    base = os.path.join(os.path.dirname(path), stem)
    i = 1
    while os.path.exists(f"{base}_{i}{ext}") or os.path.islink(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


# ----------------------------------------------------------------------------
# Lecture FITS
# ----------------------------------------------------------------------------

def read_fits(path):
    """Renvoie (données float32 2D ou HxWx3, header, BITPIX d'origine)
    du premier HDU contenant une image."""
    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            if hdu.header.get("NAXIS", 0) < 2:
                continue
            header = hdu.header.copy()          # avant mise à l'échelle
            bitpix = int(header.get("BITPIX", 16))
            raw = hdu.data
            if raw is None or raw.ndim < 2:
                continue
            data = np.asarray(raw, dtype=np.float32)
            break
        else:
            raise ValueError("aucune image trouvée dans ce fichier")

    if data.ndim == 3:
        if data.shape[0] in (3, 4):          # cube (3, H, W)
            data = np.moveaxis(data[:3], 0, -1)
        elif data.shape[-1] in (3, 4):       # (H, W, 3)
            data = data[..., :3]
        else:                                # cube quelconque : 1er plan
            data = data[0]
    elif data.ndim > 3:
        data = data.reshape(-1, *data.shape[-2:])[0]

    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    return np.ascontiguousarray(data), header, bitpix


# ----------------------------------------------------------------------------
# Lecture SER
# ----------------------------------------------------------------------------

SER_HEADER = struct.Struct("<14s7i40s40s40s2q")      # 178 octets
SER_BAYER = {8: "RGGB", 9: "GRBG", 10: "GBRG", 11: "BGGR"}
SER_COLOR_IDS = {"MONO": 0, "RGGB": 8, "GRBG": 9, "GBRG": 10, "BGGR": 11,
                 "RGB": 100}
TICKS_EPOCH = datetime(1, 1, 1)


def _ser_text(b):
    return b.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def ticks_to_datetime(t):
    return TICKS_EPOCH + timedelta(microseconds=int(t) // 10)


def to_ticks(dt):
    """Format de date SER : intervalles de 100 ns depuis le 01/01/0001."""
    td = dt - TICKS_EPOCH
    return (td.days * 86400 + td.seconds) * 10_000_000 + td.microseconds * 10


class SerFile:
    """Accès en lecture aux images d'une vidéo SER (sans tout charger)."""

    def __init__(self, path):
        self.path = path
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(SER_HEADER.size)
        if len(raw) < SER_HEADER.size:
            raise ValueError("fichier SER trop court")
        (fid, _lu, color_id, big_endian, w, h, depth, count,
         observer, instrument, telescope, _local, utc) = SER_HEADER.unpack(raw)
        if not fid.startswith(b"LUCAM-RECORDER"):
            raise ValueError("ce n'est pas un fichier SER")
        if w <= 0 or h <= 0 or depth <= 0:
            raise ValueError("en-tête SER invalide")

        self.width, self.height, self.depth = w, h, depth
        self.color_id = color_id
        self.planes = 3 if color_id in (100, 101) else 1
        self.bpp = 1 if depth <= 8 else 2
        # Le drapeau « LittleEndian » est utilisé à l'envers par presque tous
        # les logiciels (Siril, PIPP…) : 0 = petit-boutiste, 1 = gros-boutiste
        self.dtype = np.dtype(np.uint8 if self.bpp == 1
                              else (">u2" if big_endian else "<u2"))
        self.frame_size = w * h * self.planes * self.bpp
        available = max(0, (size - SER_HEADER.size) // self.frame_size)
        self.count = min(count, available) if count > 0 else available
        if self.count == 0:
            raise ValueError("aucune image dans ce fichier SER")

        self.timestamps = None
        trailer = SER_HEADER.size + count * self.frame_size
        if count == self.count and size >= trailer + 8 * count:
            ts = np.fromfile(path, dtype="<i8", count=count, offset=trailer)
            if (ts > 0).all():
                self.timestamps = ts
        self.utc = utc
        self.observer = _ser_text(observer)
        self.instrument = _ser_text(instrument)
        self.telescope = _ser_text(telescope)

    def read_frame(self, i):
        n = self.width * self.height * self.planes
        a = np.fromfile(self.path, dtype=self.dtype, count=n,
                        offset=SER_HEADER.size + i * self.frame_size)
        if a.size != n:
            raise ValueError(f"image {i + 1} incomplète")
        if self.planes == 3:
            a = a.reshape(self.height, self.width, 3)
            if self.color_id == 101:                 # BGR -> RGB
                a = a[..., ::-1]
        else:
            a = a.reshape(self.height, self.width)
        return a

    def header(self, i):
        h = fits.Header()
        h["ROWORDER"] = ("TOP-DOWN", "Order of the rows in the image")
        bayer = SER_BAYER.get(self.color_id)
        if bayer:
            h["BAYERPAT"] = (bayer, "Bayer color pattern")
        if self.timestamps is not None:
            dt = ticks_to_datetime(self.timestamps[i])
            h["DATE-OBS"] = (dt.isoformat(timespec="microseconds"),
                             "UTC, from SER trailer")
        for key, val in (("INSTRUME", self.instrument),
                         ("TELESCOP", self.telescope),
                         ("OBSERVER", self.observer)):
            if val:
                h[key] = to_ascii(val)
        h["SERFILE"] = (to_ascii(os.path.basename(self.path)), "Source SER")
        h["SERFRAME"] = (i + 1, "Frame number in the SER file")
        h["SERCOUNT"] = (self.count, "Number of frames in the SER file")
        h["SERDEPTH"] = (self.depth, "Bits per pixel in the SER file")
        return h


@functools.lru_cache(maxsize=16)
def open_ser(path):
    return SerFile(path)


# ----------------------------------------------------------------------------
# Lecture RAW (Canon CR2)
# ----------------------------------------------------------------------------

def read_tiff_exif(path, limit=2 * 1024 * 1024):
    """Lit quelques champs EXIF d'un fichier RAW au format TIFF (CR2…).
    Renvoie un dict, éventuellement vide ; ne lève pas d'exception."""
    out = {}
    try:
        with open(path, "rb") as f:
            buf = f.read(limit)
        e = {b"II": "<", b"MM": ">"}.get(buf[:2])
        if e is None or struct.unpack_from(e + "H", buf, 2)[0] != 42:
            return out
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

        def entries(off):
            n = struct.unpack_from(e + "H", buf, off)[0]
            res = {}
            for k in range(min(n, 500)):
                tag, typ, cnt, raw = struct.unpack_from(e + "HHI4s", buf,
                                                        off + 2 + 12 * k)
                res[tag] = (typ, cnt, raw)
            return res

        def value(entry):
            typ, cnt, raw = entry
            size = sizes.get(typ, 1) * cnt
            if size > 4:
                pos = struct.unpack(e + "I", raw)[0]
                raw = buf[pos:pos + size]
                if len(raw) < size:
                    return None
            if typ == 2:
                return raw[:cnt].split(b"\0", 1)[0].decode("latin-1").strip()
            if typ == 3:
                return struct.unpack_from(e + "H", raw)[0]
            if typ == 4:
                return struct.unpack_from(e + "I", raw)[0]
            if typ in (5, 10):
                num, den = struct.unpack_from(e + ("II" if typ == 5 else "ii"),
                                              raw)
                return num / den if den else None
            return None

        ifd0 = entries(struct.unpack_from(e + "I", buf, 4)[0])
        for tag, key in ((0x010F, "make"), (0x0110, "model"),
                         (0x0132, "datetime")):
            if tag in ifd0:
                out[key] = value(ifd0[tag])
        if 0x8769 in ifd0:
            exif = entries(value(ifd0[0x8769]))
            for tag, key in ((0x829A, "exptime"), (0x8827, "iso"),
                             (0x9003, "datetime"), (0x920A, "focal")):
                if tag in exif:
                    v = value(exif[tag])
                    if v is not None:
                        out[key] = v
    except (OSError, struct.error, ValueError, TypeError):
        pass
    return out


def parse_pnm(buf):
    """Décode une image PGM/PPM binaire (sortie de dcraw)."""
    if buf[:2] not in (b"P5", b"P6"):
        raise ValueError("sortie de dcraw inattendue")
    vals, i = [], 2
    while len(vals) < 3:
        while buf[i:i + 1].isspace():
            i += 1
        if buf[i:i + 1] == b"#":
            i = buf.index(b"\n", i) + 1
            continue
        j = i
        while not buf[j:j + 1].isspace():
            j += 1
        vals.append(int(buf[i:j]))
        i = j
    i += 1
    w, h, maxval = vals
    planes = 3 if buf[:2] == b"P6" else 1
    dtype = np.dtype(">u2") if maxval > 255 else np.dtype(np.uint8)
    a = np.frombuffer(buf, dtype=dtype, count=w * h * planes, offset=i)
    a = a.reshape((h, w, 3) if planes == 3 else (h, w))
    return a.astype(np.uint16)


def decode_raw(path):
    """Données brutes du capteur (uint16, sans dématriçage) et matrice
    de Bayer de la zone visible. Utilise rawpy, sinon dcraw."""
    try:
        import rawpy
    except ImportError:
        rawpy = None

    if rawpy is not None:
        with rawpy.imread(path) as raw:
            data = np.array(raw.raw_image_visible, dtype=np.uint16)
            pattern = None
            rp = np.asarray(raw.raw_pattern)
            if rp.shape == (2, 2):
                desc = raw.color_desc.decode("ascii", "replace")
                p = "".join(desc[i] for i in rp.flatten())
                pattern = p if p in BAYER_PATTERNS else None
        return data, pattern

    exe = shutil.which("dcraw")
    if exe is None:
        raise ValueError("pour lire les fichiers CR2, installez le programme "
                         "dcraw (par ex. sudo apt install dcraw) ou le module "
                         "Python rawpy")
    env = dict(os.environ, LC_ALL="C")
    run = subprocess.run([exe, "-D", "-4", "-t", "0", "-c", path],
                         capture_output=True, env=env, timeout=180)
    if run.returncode != 0 or not run.stdout:
        msg = run.stderr.decode("utf-8", "replace").strip()
        raise ValueError(msg or "dcraw n'a pas pu lire ce fichier")
    data = parse_pnm(run.stdout)
    if data.ndim != 2:
        raise ValueError("ce RAW n'a pas de matrice de Bayer")

    pattern = None
    info = subprocess.run([exe, "-i", "-v", path], capture_output=True,
                          env=env, timeout=60)
    for line in info.stdout.decode("latin-1").splitlines():
        if line.startswith("Filter pattern:"):
            p = line.split(":", 1)[1].strip().replace("/", "")[:4].upper()
            pattern = p if p in BAYER_PATTERNS else None
    return data, pattern


def read_raw(path):
    data, pattern = decode_raw(path)
    meta = read_tiff_exif(path)

    h = fits.Header()
    h["ROWORDER"] = ("TOP-DOWN", "Order of the rows in the image")
    if pattern:
        h["BAYERPAT"] = (pattern, "Bayer color pattern")
    if isinstance(meta.get("exptime"), float):
        h["EXPTIME"] = (round(meta["exptime"], 6), "[s] Exposure time (EXIF)")
    if meta.get("iso"):
        h["ISOSPEED"] = (int(meta["iso"]), "ISO speed (EXIF)")
    if isinstance(meta.get("focal"), float) and meta["focal"] > 0:
        h["FOCALLEN"] = (round(meta["focal"], 1), "[mm] Focal length (EXIF)")
    model = meta.get("model") or ""
    make = meta.get("make") or ""
    camera = model if model.startswith(make) else f"{make} {model}".strip()
    if camera:
        h["INSTRUME"] = to_ascii(camera)
    try:
        dt = datetime.strptime(meta.get("datetime", ""), "%Y:%m:%d %H:%M:%S")
        h["DATE-OBS"] = (dt.isoformat(), "Camera clock (EXIF), time zone unknown")
    except ValueError:
        pass
    h["RAWFILE"] = (to_ascii(os.path.basename(path)), "Source RAW file")
    return data.astype(np.float32), h, 16


def read_source(src):
    """(données float32, header, BITPIX) pour un FITS ou une image de SER."""
    path, frame = src
    if frame < 0:
        return read_raw(path) if is_raw(path) else read_fits(path)
    ser = open_ser(path)
    raw = ser.read_frame(frame)
    return (raw.astype(np.float32), ser.header(frame),
            8 if ser.bpp == 1 else 16)


# ----------------------------------------------------------------------------
# Affichage (exécuté dans des threads de fond)
# ----------------------------------------------------------------------------

def subsample(a, target=500_000):
    step = max(1, int(np.sqrt(a.shape[0] * a.shape[1] / target)))
    return a[::step, ::step]


def debayer_superpixel(img, pattern):
    """Débayerisation super-pixel : rapide, image de moitié de taille."""
    h, w = img.shape
    img = img[: h - h % 2, : w - w % 2]
    quads = [img[0::2, 0::2], img[0::2, 1::2], img[1::2, 0::2], img[1::2, 1::2]]
    chans = {"R": [], "G": [], "B": []}
    for color, q in zip(pattern, quads):
        chans[color].append(q)
    r = chans["R"][0]
    g = (chans["G"][0] + chans["G"][1]) * 0.5
    b = chans["B"][0]
    return np.dstack([r, g, b])


def mtf(m, x):
    """Midtones Transfer Function (x dans [0,1], m dans ]0,1[)."""
    return ((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m)


def mtf_scalar(m, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return float(mtf(m, x))


def stretch(ch, params):
    """Étire un canal 2D, renvoie du float32 dans [0,1]."""
    kind, a, b = params
    sample = subsample(ch)

    if kind == "pct":
        lo, hi = (float(v) for v in np.percentile(sample, [a, b]))
    else:
        lo, hi = float(ch.min()), float(ch.max())
    if hi <= lo:
        return np.zeros(ch.shape, dtype=np.float32)

    scale = 1.0 / (hi - lo)
    x = (ch - lo) * scale
    np.clip(x, 0.0, 1.0, out=x)
    if kind != "stf":
        return x

    s = (sample - lo) * scale
    med = float(np.median(s))
    mad = float(np.median(np.abs(s - med))) * 1.4826
    c0 = min(max(med + a * mad, 0.0), 0.99)
    bg = (med - c0) / (1.0 - c0)
    # mb tel que mtf(mb, fond) = fond cible ; 0,5 = identité
    mb = mtf_scalar(b, bg) if bg > 0 else 0.5

    x = (x - c0) * (1.0 / (1.0 - c0))
    np.clip(x, 0.0, 1.0, out=x)
    return mtf(mb, x)


def header_summary(h):
    fields = [
        ("OBJECT", "Objet", ""), ("IMAGETYP", "Type", ""),
        ("FILTER", "Filtre", ""), ("EXPTIME", "Pose", " s"),
        ("EXPOSURE", "Pose", " s"), ("GAIN", "Gain", ""),
        ("ISOSPEED", "ISO", ""),
        ("CCD-TEMP", "Temp.", " °C"), ("DATE-OBS", "Date", ""),
    ]
    parts, seen = [], set()
    for key, label, unit in fields:
        if key in h and label not in seen:
            val = h[key]
            if isinstance(val, float):
                val = f"{val:g}"
            parts.append(f"{label} {str(val).strip()}{unit}")
            seen.add(label)
    return "   ".join(parts)


def process_source(src, params, debayer):
    data, header, _ = read_source(src)

    mono = data if data.ndim == 2 else data.mean(axis=2)
    s = subsample(mono)
    med = float(np.median(s))
    noise = float(np.median(np.abs(s - med))) * 1.4826

    bayer = str(header.get("BAYERPAT", "") or "").strip().upper()
    color = data.ndim == 3
    if debayer and not color and bayer in BAYER_PATTERNS:
        data = debayer_superpixel(data, bayer)
        color = True

    if color:
        out = np.dstack([stretch(data[..., c], params) for c in range(3)])
    else:
        out = stretch(data, params)

    img8 = (out * 255.0 + 0.5).astype(np.uint8)

    # Convention FITS : 1re ligne en bas, sauf si ROWORDER = TOP-DOWN
    if str(header.get("ROWORDER", "")).strip().upper() != "TOP-DOWN":
        img8 = img8[::-1]
    img8 = np.ascontiguousarray(img8)

    h0, w0 = mono.shape
    info = (f"{src_name(src)}   |   {w0}×{h0}"
            f"{'  couleur' if color else ''}   |   "
            f"médiane {med:.1f}   bruit {noise:.1f}   |   "
            f"{header_summary(header)}")
    return {
        "image": img8,
        "header": header.tostring(sep="\n", endcard=False, padding=False),
        "info": info,
    }


def numpy_to_qimage(a):
    h, w = a.shape[:2]
    if a.ndim == 2:
        img = QImage(a.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
    else:
        img = QImage(a.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()   # détache des données numpy


# ----------------------------------------------------------------------------
# Mesure de la qualité (étoiles, FWHM, excentricité, fond, netteté)
# ----------------------------------------------------------------------------

MAX_MEASURED = 400            # étoiles mesurées au plus par image
STAR_R = 7                    # demi-taille de la fenêtre de mesure

# Critères de présélection par défaut
# Les critères étoiles, excentricité et fond sont exprimés en « écarts » :
# une image est écartée si elle s'éloigne de la médiane de la série de plus
# de k fois la dispersion normale de la série (écart-type robuste).
PRESELECT_DEFAULTS = OrderedDict([
    ("fwhm", 1.30),     # FWHM max, en multiple de la médiane de la série
    ("stars", 3.0),     # étoiles : écart maximal sous la médiane (× dispersion)
    ("ecc", 3.0),       # excentricité : écart maximal au-dessus (× dispersion)
    ("bg", 3.0),        # fond : écart maximal au-dessus (× dispersion)
    ("sharp", 70.0),    # netteté min (planétaire), en % de la médiane
])
ECC_ALWAYS_OK = 0.50    # en dessous, les étoiles sont jugées assez rondes


def bin2(a):
    h, w = a.shape
    a = a[: h - h % 2, : w - w % 2]
    return (a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]) * 0.25


def box3(a):
    """Lissage 3 × 3 (moyenne)."""
    h, w = a.shape
    p = np.pad(a, 1, mode="edge")
    out = np.zeros_like(a)
    for dy in range(3):
        for dx in range(3):
            out += p[dy:dy + h, dx:dx + w]
    out *= 1.0 / 9.0
    return out


def local_maxima(a):
    h, w = a.shape
    p = np.pad(a, 1, mode="edge")
    m = np.ones(a.shape, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            if dy == 1 and dx == 1:
                continue
            nb = p[dy:dy + h, dx:dx + w]
            # égalité tranchée d'un seul côté : un plateau donne un maximum
            m &= (a > nb) if (dy, dx) < (1, 1) else (a >= nb)
    return m


def background_map(img, block=64):
    """Carte du fond de ciel : médiane par blocs (suit les gradients)."""
    h, w = img.shape
    block = max(8, min(block, h, w))
    hb, wb = h // block, w // block
    core = img[: hb * block, : wb * block].reshape(hb, block, wb, block)
    med = np.median(core.transpose(0, 2, 1, 3).reshape(hb, wb, -1), axis=2)
    # interpolation bilinéaire entre les centres des blocs
    def extend(c, size, vals):
        """Ajoute les bords, extrapolés linéairement, aux points de grille."""
        if len(c) < 2:
            return np.array([0.0, size - 1.0]), np.repeat(vals[..., :1], 2, -1)
        a = vals[..., :1] - (vals[..., 1:2] - vals[..., :1]) * c[0] / (c[1] - c[0])
        b = vals[..., -1:] + (vals[..., -1:] - vals[..., -2:-1]) * (
            (size - 1 - c[-1]) / (c[-1] - c[-2]))
        return (np.concatenate([[0.0], c, [size - 1.0]]),
                np.concatenate([a, vals, b], axis=-1))

    cy = (np.arange(hb) + 0.5) * block
    cx = (np.arange(wb) + 0.5) * block
    cx_e, med_x = extend(cx, w, med)
    cy_e, _ = extend(cy, h, med[:, 0])
    rows = np.empty((len(cy_e), w), dtype=np.float64)
    for i in range(hb):
        rows[i + 1] = np.interp(np.arange(w), cx_e, med_x[i])
    if hb >= 2:
        rows[0] = rows[1] - (rows[2] - rows[1]) * cy[0] / (cy[1] - cy[0])
        rows[-1] = rows[-2] + (rows[-2] - rows[-3]) * (
            (h - 1 - cy[-1]) / (cy[-1] - cy[-2]))
    else:
        rows[0] = rows[-1] = rows[1]
    cy = cy_e
    out = np.empty((h, w), dtype=np.float32)
    ys = np.arange(h)
    for j in range(0, w, 256):
        sl = rows[:, j:j + 256]
        for k in range(sl.shape[1]):
            out[:, j + k] = np.interp(ys, cy, sl[:, k])
    return out


def _dilate(m):
    p = np.pad(m, 1)
    return (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1]
            | p[1:-1, :-2] | p[1:-1, 2:])


def _region(win, level, r):
    """Région connexe au-dessus de level, partant du pic central."""
    mask = win > level
    c = win[r - 1:r + 2, r - 1:r + 2]
    cy, cx = np.unravel_index(np.argmax(c), (3, 3))
    comp = np.zeros_like(mask)
    comp[r - 1 + cy, r - 1 + cx] = True
    for _ in range(r):
        grown = _dilate(comp) & mask
        if (grown == comp).all():
            break
        comp = grown
    edge = (comp[0].any() or comp[-1].any()
            or comp[:, 0].any() or comp[:, -1].any())
    return comp, edge


def measure_star(win, noise):
    """FWHM (aire à mi-hauteur) et excentricité (moments) d'une étoile
    centrée dans la fenêtre win (fond déjà soustrait). None si douteuse."""
    r = win.shape[0] // 2
    peak = float(win[r - 1:r + 2, r - 1:r + 2].max())
    if peak < 10.0 * noise:
        return None
    half, edge = _region(win, peak * 0.5, r)
    if edge or half.sum() < 2:               # trop grosse, ou pixel chaud
        return None
    # FWHM par l'aire à mi-hauteur, affinée en comptant la fraction de
    # chaque pixel de bord qui dépasse la mi-hauteur
    ring = _dilate(half) & ~half
    v = win[ring]
    inside = win[half]
    frac_out = np.clip((v - 0.5 * peak) / (0.5 * peak) + 0.5, 0, 1).sum()
    frac_in = np.clip((inside - 0.5 * peak) / (0.5 * peak) + 0.5, 0, 1)
    area = float(half.sum() - (1 - frac_in).sum() * 0.5 + frac_out * 0.5)
    # excentricité par les moments sur une région plus large (20 %)
    comp, edge = _region(win, max(peak * 0.2, 3.0 * noise), r)
    if edge:
        comp = half
    yy, xx = np.nonzero(comp)
    wgt = win[yy, xx]
    s = float(wgt.sum())
    my, mx = (yy * wgt).sum() / s, (xx * wgt).sum() / s
    dy, dx = yy - my, xx - mx
    myy = float((wgt * dy * dy).sum() / s) + 1.0 / 12.0
    mxx = float((wgt * dx * dx).sum() / s) + 1.0 / 12.0
    mxy = float((wgt * dx * dy).sum() / s)
    tr, det = mxx + myy, mxx * myy - mxy * mxy
    disc = max(0.0, tr * tr / 4.0 - det)
    l1, l2 = tr / 2.0 + disc ** 0.5, max(tr / 2.0 - disc ** 0.5, 1e-9)
    ecc = (max(0.0, 1.0 - l2 / l1)) ** 0.5
    fwhm = 2.0 * (max(area, 1.0) / np.pi) ** 0.5
    return fwhm, ecc, my, mx


def iq_mean(values):
    """Moyenne des valeurs comprises entre les quartiles (robuste et
    plus fine qu'une médiane de valeurs mesurées au pixel près)."""
    v = np.sort(np.asarray(values, dtype=np.float64))
    n = len(v)
    lo, hi = n // 4, n - n // 4
    return float(v[lo:hi].mean()) if hi > lo else float(v.mean())


def pixel_scale(header):
    """Échantillonnage en secondes d'arc par pixel, si l'en-tête le permet."""
    try:
        pix = float(header.get("XPIXSZ") or header.get("PIXSIZE1") or 0)
        foc = float(header.get("FOCALLEN") or 0)
    except (TypeError, ValueError):
        return None
    if pix <= 0 or foc <= 0:
        return None
    return 206.265 * pix / foc      # XPIXSZ inclut déjà le binning (norme)


def box_mean(a, k):
    """Moyenne glissante k × k (k impair), bords compris."""
    r = k // 2
    p = np.pad(a, r, mode="edge").astype(np.float64)
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return (s / (k * k)).astype(np.float32)


def detect_trail(resid, noise):
    """Cherche une traînée rectiligne (satellite, avion, météore).
    resid : image moins le fond ; noise : bruit par pixel.
    Renvoie {"angle", "length", "line": (x0, y0, x1, y1)} en pixels de
    resid, ou None."""
    h, w = resid.shape
    f = max(1, int(np.ceil(max(h, w) / 900.0)))
    hh, ww = h // f, w // f
    d = resid[:hh * f, :ww * f].reshape(hh, f, ww, f).mean(axis=(1, 3))
    sig = noise / f
    hp = d - box_mean(d, 15)                 # retire nébuleuses et halos
    mask = hp > 3.0 * sig
    # les étoiles : petits amas très brillants, on les efface largement
    bright = hp > 25.0 * sig
    if bright.any():
        grow = box_mean(bright.astype(np.float32), 7) > 0
        mask &= ~grow
    ys, xs = np.nonzero(mask)
    if len(ys) < 30:
        return None
    rng = np.random.default_rng(0)
    if len(ys) > 40000:
        pick = rng.choice(len(ys), 40000, replace=False)
        ys, xs = ys[pick], xs[pick]
    thetas = np.radians(np.arange(0, 180, 1.0))
    diag = int(np.hypot(hh, ww)) + 2
    nr = diag  # 2 px par case de rho, de -diag à +diag
    ct, st = np.cos(thetas), np.sin(thetas)
    rho = (xs[:, None] * ct[None, :] + ys[:, None] * st[None, :])
    ri = np.round((rho + diag) / 2.0).astype(np.int64)
    idx = ri + np.arange(len(thetas))[None, :] * (nr + 1)
    acc = np.bincount(idx.ravel(), minlength=len(thetas) * (nr + 1))
    acc = acc.reshape(len(thetas), nr + 1)
    best = None
    min_len = max(40.0, 0.2 * min(hh, ww))
    flat = np.argsort(acc.ravel())[::-1][:12]
    for k in flat:
        ti, rj = divmod(int(k), nr + 1)
        if acc[ti, rj] < min_len * 0.5:
            break
        th = thetas[ti]
        r0 = rj * 2.0 - diag
        c, s_ = np.cos(th), np.sin(th)
        # affinage : droite des moindres carrés sur les points proches
        for tol in (4.0, 2.0, 1.5):
            dist = xs * c + ys * s_ - r0
            near = np.abs(dist) < tol
            if near.sum() < 10:
                break
            px0, py0 = xs[near].mean(), ys[near].mean()
            cov = np.cov(np.vstack([xs[near] - px0, ys[near] - py0]))
            evals, evecs = np.linalg.eigh(cov)
            nx, ny = evecs[:, 0]             # normale = plus petit axe
            c, s_ = nx, ny
            r0 = px0 * c + py0 * s_
        x_c, y_c = r0 * c, r0 * s_
        dx, dy = -s_, c
        t = np.arange(-diag, diag, 1.0)
        px_, py_ = x_c + t * dx, y_c + t * dy
        inside = (px_ >= 4) & (px_ < ww - 4) & (py_ >= 4) & (py_ < hh - 4)
        if inside.sum() < min_len:
            continue
        t, px_, py_ = t[inside], px_[inside], py_[inside]
        on = np.max([hp[np.round(py_ + o * s_).astype(int).clip(0, hh - 1),
                        np.round(px_ + o * c).astype(int).clip(0, ww - 1)]
                     for o in (-1, 0, 1)], axis=0)
        # contraste avec deux bandes parallèles à 4 px de part et d'autre
        offs = []
        for o in (-4, 4):
            qx = np.round(px_ + o * c).astype(int).clip(0, ww - 1)
            qy = np.round(py_ + o * s_).astype(int).clip(0, hh - 1)
            offs.append(hp[qy, qx])
        side = np.maximum(offs[0], offs[1])
        lit = (on > 2.5 * sig) & (on - side > 2.0 * sig)
        # plus long tronçon allumé, trous de 3 px tolérés
        run, best_run, gap, start, best_span = 0, 0, 0, 0, (0, 0)
        for i, v in enumerate(lit):
            if v:
                if run == 0:
                    start = i
                run, gap = i - start + 1, 0
                if run > best_run:
                    best_run, best_span = run, (start, i)
            elif run:
                gap += 1
                if gap > 3:
                    run, gap = 0, 0
        if best_run < min_len:
            continue
        a, b = best_span
        frac = lit[a:b + 1].mean()
        if frac < 0.6:
            continue
        # aigrette de diffraction : la ligne passe par le cœur d'une étoile
        # brillante situé vers son milieu
        core = np.zeros(len(px_), bool)
        for o in (-2, -1, 0, 1, 2):
            core |= bright[np.round(py_ + o * s_).astype(int).clip(0, hh - 1),
                           np.round(px_ + o * c).astype(int).clip(0, ww - 1)]
        margin = int(0.05 * (b - a)) + 3
        lo_, hi_ = max(0, a - margin), min(len(core) - 1, b + margin)
        hits = np.nonzero(core[lo_:hi_ + 1])[0] + lo_
        spike = False
        if len(hits):
            # un groupe de points brillants consécutifs = une étoile
            groups = np.split(hits, np.nonzero(np.diff(hits) > 2)[0] + 1)
            idx_ = np.arange(a, b + 1)
            seg = on[a:b + 1]
            for g in groups:
                dist = np.abs(idx_ - g.mean())
                dmax = max(dist.max(), 1)
                near_ = seg[dist <= 0.2 * dmax]
                far_ = seg[dist >= 0.6 * dmax]
                if len(near_) and len(far_) and \
                        np.median(near_) > 2.0 * max(np.median(far_), sig):
                    spike = True         # s'éteint en s'éloignant du cœur
                    break
        if spike:
            continue
        if best is None or best_run > best[0]:
            best = (best_run, (px_[a] * f, py_[a] * f, px_[b] * f, py_[b] * f))
    if best is None:
        return None
    x0, y0, x1, y1 = best[1]
    ang = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180.0)
    return {"angle": ang, "length": float(np.hypot(x1 - x0, y1 - y0)),
            "line": (float(x0), float(y0), float(x1), float(y1))}


def measure_data(data, header):
    """Mesure une image (float32 2D ou HxWx3). Renvoie un dict."""
    scale = 1
    if data.ndim == 3:
        img = data.mean(axis=2)
    else:
        bayer = str(header.get("BAYERPAT", "") or "").strip().upper()
        if bayer in BAYER_PATTERNS:          # super-pixel : supprime la trame
            img, scale = bin2(data), 2
        else:
            img = data
    while img.shape[0] * img.shape[1] > 8_000_000:
        img, scale = bin2(img), scale * 2
    img = np.ascontiguousarray(img, dtype=np.float32)
    h, w = img.shape

    bgmap = background_map(img)
    resid_full = img - bgmap
    s = subsample(resid_full)
    noise = float(np.median(np.abs(s - np.median(s)))) * 1.4826
    noise = max(noise, 1e-3)
    bg = float(np.median(subsample(img)))

    sm = box3(resid_full)
    sm_noise = noise / 3.0
    R = STAR_R
    cand = local_maxima(sm) & (sm > 5.0 * sm_noise)
    cand[:R + 1] = cand[-R - 1:] = False
    cand[:, :R + 1] = cand[:, -R - 1:] = False
    ys, xs = np.nonzero(cand)

    # écarte les pixels chauds : une étoile a des voisins lumineux
    if len(ys):
        center = resid_full[ys, xs]
        nb = (resid_full[ys - 1, xs] + resid_full[ys + 1, xs]
              + resid_full[ys, xs - 1] + resid_full[ys, xs + 1]) * 0.25
        keep = nb > 0.15 * np.maximum(center, 1e-6)
        ys, xs = ys[keep], xs[keep]
    n_stars = int(len(ys))

    fwhms, eccs, cells, stars_xy = [], [], set(), []
    zones = [[[] for _ in range(3)] for _ in range(3)]   # champ en 3 × 3
    if n_stars:
        order = np.argsort(-sm[ys, xs])
        sat = float(img.max()) * 0.97
        for k in order:
            y, x = int(ys[k]), int(xs[k])
            if img[y - 1:y + 2, x - 1:x + 2].max() >= sat:
                continue                     # étoile saturée
            m = measure_star(resid_full[y - R:y + R + 1, x - R:x + R + 1],
                             noise)
            if m is not None:
                cells.add((4 * y // h, 4 * x // w))
                zones[3 * y // h][3 * x // w].append(m)
                if len(stars_xy) < 80:       # positions pour l'alignement
                    stars_xy.append(((x - R + m[3] + 0.5) * scale - 0.5,
                                     (y - R + m[2] + 0.5) * scale - 0.5))
                fwhms.append(m[0])
                eccs.append(m[1])
                if len(fwhms) >= MAX_MEASURED:
                    break

    # netteté (utile en planétaire) : énergie du laplacien, normalisée
    lap = (4.0 * sm[1:-1, 1:-1] - sm[:-2, 1:-1] - sm[2:, 1:-1]
           - sm[1:-1, :-2] - sm[1:-1, 2:])
    signal = float(np.percentile(subsample(sm), 99.5))
    sharp = float(np.mean(lap * lap)) / max(signal, noise) ** 2

    try:
        trail = detect_trail(resid_full, noise)
    except Exception:   # noqa: BLE001  (la mesure principale reste valable)
        trail = None
    if trail:
        x0, y0, x1, y1 = trail["line"]
        trail = {"angle": trail["angle"], "length": trail["length"] * scale,
                 "line": tuple(v * scale for v in (x0, y0, x1, y1))}

    enough = len(fwhms) >= 3
    grid = [[(iq_mean([z[0] for z in zone]) * scale,
              iq_mean([z[1] for z in zone]), len(zone))
             if len(zone) >= 3 else None for zone in row] for row in zones]
    return {
        "grid": grid,                       # (FWHM, excentricité, n) par zone
        "trail": trail,                     # traînée de satellite ou d'avion
        # étoiles les plus brillantes (x, y) en pixels du fichier
        "stars_xy": np.array(stars_xy, dtype=np.float32).reshape(-1, 2),
        "shape": (int(data.shape[1]), int(data.shape[0])),     # (w, h)
        "fwhm": iq_mean(fwhms) * scale if enough else None,
        "ecc": iq_mean(eccs) if enough else None,
        "stars": n_stars,
        "measured": len(fwhms),
        "coverage": len(cells) / 16.0,      # étoiles réparties dans le champ
        "bg": bg,
        "noise": noise,
        "sharp": sharp,
        "arcsec": pixel_scale(header),
        # sens d'affichage : convention FITS (1re ligne en bas) sauf TOP-DOWN
        "rowflip": str(header.get("ROWORDER", "")).strip().upper()
                   != "TOP-DOWN",
    }


def measure_source(src):
    data, header, _ = read_source(src)
    return measure_data(data, header)


def _median(values):
    v = [x for x in values if x is not None]
    return float(np.median(v)) if v else None


def series_mode(measures):
    """'stars' (ciel profond) ou 'planet' selon que les images contiennent
    assez d'étoiles mesurables."""
    ms = [m for m in measures.values() if m]
    if not ms:
        return "stars"
    stellar = sum(1 for m in ms if m["fwhm"] and m["measured"] >= 10
                  and m.get("coverage", 1.0) >= 0.5)
    return "stars" if stellar >= len(ms) / 2.0 else "planet"


def compute_scores(measures):
    """Score de 0 à 100 pour chaque image (100 = image de référence)."""
    ms = {k: m for k, m in measures.items() if m}
    if not ms:
        return {}
    raw = {}
    if series_mode(ms) == "stars":
        med_f = _median(m["fwhm"] for m in ms.values())
        med_s = max(1.0, _median(m["stars"] for m in ms.values()))
        med_e = min(0.95, _median(m["ecc"] for m in ms.values()))
        med_n = _median(m["noise"] for m in ms.values())
        for k, m in ms.items():
            if not m["fwhm"]:
                raw[k] = 0.0
                continue
            raw[k] = (0.45 * min(2.0, (med_f / m["fwhm"]) ** 2)
                      + 0.25 * min(2.0, m["stars"] / med_s)
                      + 0.15 * min(2.0, (1.0 - m["ecc"]) / (1.0 - med_e))
                      + 0.15 * min(2.0, med_n / m["noise"]))
    else:
        med_sh = _median(m["sharp"] for m in ms.values()) or 1.0
        for k, m in ms.items():
            raw[k] = m["sharp"] / med_sh
    best = max(raw.values())
    return {k: (100.0 * v / best if best > 0 else 0.0) for k, v in raw.items()}


def pct(x, sign=False):
    """Pourcentage à la française : 25 %, +3 %."""
    v = round(100 * x)
    return (f"{v:+d}" if sign else f"{v:d}") + "\u202f%"


def fr(x, nd=1):
    return f"{x:.{nd}f}".replace(".", ",")


def reason_kind(reason):
    """Catégorie d'une raison de présélection (pour le résumé)."""
    if reason.startswith("FWHM"):
        return "fwhm"
    if "allongées" in reason:
        return "ecc"
    if reason.startswith("fond"):
        return "bg"
    if reason.startswith("aucune"):
        return "none"
    if reason.startswith("netteté"):
        return "sharp"
    if reason.startswith("dark"):
        return "dark"
    if reason.startswith("flat :"):
        return "flatbad"
    return "stars"


def preselect_limits(measures, crit):
    """Médianes et seuils de la présélection pour une série.
    Renvoie {"mode": ..., mesure: (médiane, seuil, sens)} ; sens = +1 si
    l'image est écartée au-dessus du seuil, -1 en dessous."""
    ms = [m for m in measures.values() if m]
    if not ms:
        return {}

    def spread(values, floor):
        v = np.array([x for x in values if x is not None], dtype=float)
        if not len(v):
            return None, floor
        med = float(np.median(v))
        return med, max(1.4826 * float(np.median(np.abs(v - med))), floor)

    if series_mode(measures) == "stars":
        med_f = _median(m["fwhm"] for m in ms)
        med_n = _median(m["noise"] for m in ms) or 1.0
        med_s, sd_s = spread((m["stars"] for m in ms), 0)
        sd_s = max(sd_s, 0.03 * med_s)
        med_e, sd_e = spread((m["ecc"] for m in ms), 0.03)
        med_b, sd_b = spread((m["bg"] for m in ms), 0.5 * med_n)
        return {
            "mode": "stars",
            "fwhm": (med_f, crit["fwhm"] * med_f if med_f else None, +1),
            "ecc": (med_e, max(ECC_ALWAYS_OK, med_e + crit["ecc"] * sd_e)
                    if med_e is not None else None, +1),
            "stars": (med_s, med_s - crit["stars"] * sd_s, -1),
            "bg": (med_b, med_b + crit["bg"] * sd_b, +1),
        }
    med_sh = _median(m["sharp"] for m in ms) or 1.0
    med_b, sd_b = spread((m["bg"] for m in ms), 1e-6)
    return {
        "mode": "planet",
        "sharp": (med_sh, crit["sharp"] / 100.0 * med_sh, -1),
        "bg": (med_b, None, +1),
    }


def preselect(measures, crit):
    """Renvoie {source: [raisons]} pour les images jugées mauvaises."""
    ms = {k: m for k, m in measures.items() if m}
    out = {}
    if not ms:
        return out

    def add(k, reason):
        out.setdefault(k, []).append(reason)

    lim = preselect_limits(ms, crit)
    if lim["mode"] == "stars":
        med_f = lim["fwhm"][0]
        med_e, lim_e = lim["ecc"][0], (lim["ecc"][1] or 1.0)
        med_s, lim_s = lim["stars"][:2]
        med_b, lim_b = lim["bg"][:2]
        for k, m in ms.items():
            if not m["fwhm"]:
                add(k, "aucune étoile mesurable")
            else:
                if m["fwhm"] > crit["fwhm"] * med_f:
                    add(k, f"FWHM {fr(m['fwhm'])} px (médiane {fr(med_f)})")
                if m["ecc"] > lim_e:
                    add(k, f"étoiles allongées : excentricité "
                           f"{fr(m['ecc'], 2)} (médiane {fr(med_e, 2)})")
            if m["stars"] < lim_s:
                add(k, f"{m['stars']} étoiles (médiane {int(med_s)})")
            if m["bg"] > lim_b:
                add(k, f"fond élevé : {fr(m['bg'], 0)} (médiane {fr(med_b, 0)})")
    else:
        med_sh = _median(m["sharp"] for m in ms.values()) or 1.0
        for k, m in ms.items():
            pct = 100.0 * m["sharp"] / med_sh
            if pct < crit["sharp"]:
                add(k, f"netteté {fr(pct, 0)} % de la médiane")
    return out


# ----------------------------------------------------------------------------
# Inspection du champ (coins, inclinaison du capteur, courbure)
# ----------------------------------------------------------------------------

ZONE_NAMES = [["haut gauche", "haut", "haut droit"],
              ["gauche", "centre", "droite"],
              ["bas gauche", "bas", "bas droit"]]


def orient_grid(grid, flip_rows):
    """Remet la grille dans le sens de l'affichage."""
    if grid is None:
        return None
    return grid[::-1] if flip_rows else grid


def median_grid(grids):
    """Grille médiane d'une série (plus fiable qu'une seule image)."""
    grids = [g for g in grids if g]
    if not grids:
        return None
    out = []
    for r in range(3):
        row = []
        for c in range(3):
            cells = [g[r][c] for g in grids if g[r][c]]
            if len(cells) * 2 < len(grids) or not cells:
                row.append(None)
            else:
                row.append((float(np.median([x[0] for x in cells])),
                            float(np.median([x[1] for x in cells])),
                            int(np.median([x[2] for x in cells]))))
        out.append(row)
    return out


def field_summary(grid):
    """Diagnostic du champ à partir de la grille 3 × 3 (orientée comme
    l'affichage). Renvoie (phrase courte, détail)."""
    if not grid or not grid[1][1]:
        return "", ""
    center = grid[1][1][0]
    corners = {ZONE_NAMES[r][c]: grid[r][c] for r, c in
               ((0, 0), (0, 2), (2, 0), (2, 2)) if grid[r][c]}
    if len(corners) < 3:
        return "pas assez d'étoiles dans les coins", ""
    cf = {k: v[0] for k, v in corners.items()}
    worst = max(cf, key=cf.get)
    best = min(cf, key=cf.get)
    spread = (cf[worst] - cf[best]) / cf[best]
    mean_c = sum(cf.values()) / len(cf)
    edge = (mean_c - center) / center
    ecc_c = sum(v[1] for v in corners.values()) / len(corners)
    detail = (f"FWHM centre {fr(center, 2)} px ; coins : " + ", ".join(
        f"{k} {fr(v, 2)}" for k, v in cf.items()) +
        f" ; écart entre coins {spread * 100:.0f} %")
    if spread > 0.15:
        msg = (f"inclinaison probable : coin {worst} {spread * 100:.0f} % "
               f"plus flou que le coin {best}")
    elif edge > 0.15 and ecc_c > grid[1][1][1] + 0.1:
        msg = (f"coma ou mauvais tirage : coins {edge * 100:.0f} % plus "
               "flous et étoiles allongées")
    elif edge > 0.15:
        msg = f"courbure de champ : coins {edge * 100:.0f} % plus flous"
    else:
        msg = "champ homogène"
    return msg, detail


def corner_mosaic(img, gap=6):
    """Mosaïque 3 × 3 : coins, bords et centre de l'image, à 100 %.
    Renvoie (mosaïque, taille d'une vignette)."""
    h, w = img.shape[:2]
    c = max(32, min(h, w) // 4)
    c = min(c, h, w)
    ys = [0, (h - c) // 2, h - c]
    xs = [0, (w - c) // 2, w - c]
    shape = (3 * c + 2 * gap, 3 * c + 2 * gap) + img.shape[2:]
    out = np.full(shape, 40, dtype=np.uint8)
    for r, y in enumerate(ys):
        for k, x in enumerate(xs):
            oy, ox = r * (c + gap), k * (c + gap)
            out[oy:oy + c, ox:ox + c] = img[y:y + c, x:x + c]
    return np.ascontiguousarray(out), c


# ----------------------------------------------------------------------------
# Types d'images et fichiers de calibration
# ----------------------------------------------------------------------------

FRAME_TYPES = OrderedDict([("light", "Brute"), ("dark", "Dark"),
                           ("flat", "Flat"), ("darkflat", "Dark de flat"),
                           ("bias", "Offset"), ("master", "Résultat")])
SIRIL_DIRS = {"light": "lights", "dark": "darks", "flat": "flats",
              "darkflat": "darkflats", "bias": "biases", "master": "resultats"}
SKIP_DIRS = {"rejetes", "traitement", "process", "masters", "@eadir"}


def type_from_text(text):
    t = re.sub(r"[^a-z]", " ", str(text).lower())
    words = t.split()
    joined = "".join(words)
    if any(w in ("bias", "biases", "offset", "offsets", "zero")
           for w in words) or "bias" in joined or "offset" in joined:
        return "bias"
    if "darkflat" in joined or "flatdark" in joined or "dflat" in words:
        return "darkflat"
    if "flat" in joined:
        return "flat"
    if "dark" in joined:
        return "dark"
    if any(w in ("light", "lights", "object", "science", "brute", "brutes")
           for w in words):
        return "light"
    return None


def detect_type(imagetyp, path):
    """Type d'image : d'après IMAGETYP, sinon le nom du fichier, puis celui
    des dossiers parents."""
    if imagetyp:
        t = type_from_text(imagetyp)
        if t:
            return t
    t = type_from_text(os.path.basename(path))
    if t:
        return t
    parent = os.path.dirname(path)
    for _ in range(2):
        t = type_from_text(os.path.basename(parent))
        if t:
            return t
        parent = os.path.dirname(parent)
    return "light"


# ----------------------------------------------------------------------------
# Position de l'objet dans le ciel
# ----------------------------------------------------------------------------

def parse_angle(v, hours=False):
    """Angle en degrés depuis un nombre ou une chaîne sexagésimale
    (« 22 47 21.5 », « +58:07:12 »). hours : la chaîne est en heures."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    txt = str(v).strip()
    try:
        return float(txt)
    except ValueError:
        pass
    parts = re.findall(r"[-+]?\d+(?:\.\d+)?", txt.replace(",", "."))
    if not parts:
        return None
    sign = -1.0 if txt.lstrip().startswith("-") else 1.0
    vals = [abs(float(x)) for x in parts[:3]]
    deg = vals[0] + (vals[1] / 60 if len(vals) > 1 else 0) \
        + (vals[2] / 3600 if len(vals) > 2 else 0)
    return sign * deg * (15.0 if hours else 1.0)


def alt_az(ra, dec, lat, lon, utc):
    """Hauteur et azimut (degrés, azimut compté du nord vers l'est) d'un
    objet (ra, dec en degrés) vu depuis (lat, lon est positive) à la date
    UTC donnée."""
    jd = (utc - datetime(2000, 1, 1, 12)).total_seconds() / 86400.0
    gmst = (280.46061837 + 360.98564736629 * jd) % 360.0
    ha = np.radians((gmst + lon - ra) % 360.0)
    d, f = np.radians(dec), np.radians(lat)
    sin_alt = np.sin(f) * np.sin(d) + np.cos(f) * np.cos(d) * np.cos(ha)
    alt = np.arcsin(np.clip(sin_alt, -1, 1))
    az = np.arctan2(-np.sin(ha) * np.cos(d),
                    np.cos(f) * np.sin(d) - np.sin(f) * np.cos(d) * np.cos(ha))
    return float(np.degrees(alt)), float(np.degrees(az) % 360.0)


SIDEREAL_DEG_H = 15.041           # rotation de la Terre, degrés par heure


def rotation_rate_theory(lat, alt, az):
    """Vitesse de rotation du champ (°/h) pour une monture alt-azimutale."""
    ca = np.cos(np.radians(alt))
    if ca < 0.02:
        return None                        # au zénith : indéfinie
    return float(SIDEREAL_DEG_H * np.cos(np.radians(lat))
                 * np.cos(np.radians(az)) / ca)



# ----------------------------------------------------------------------------
# Bilan de la séance
# ----------------------------------------------------------------------------

def hm(seconds):
    """Durée lisible : 2 h 05 min, 12 min, 45 s."""
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return f"{seconds} s"
    h, mn = divmod(seconds // 60, 60)
    return f"{h} h {mn:02d} min" if h else f"{mn} min"


def night_of(date):
    """Nuit d'observation : une image de 2 h du matin appartient à la nuit
    commencée la veille."""
    return (date - timedelta(hours=12)).date() if date else None


def rejection_cost(rows):
    """Ce que coûte le tri : temps de pose perdu, perte de rapport
    signal/bruit et gain en finesse. rows : dicts avec exp, fwhm, kept."""
    tot = sum(r.get("exp") or 0 for r in rows)
    kept = sum(r.get("exp") or 0 for r in rows if r["kept"])
    n, nk = len(rows), sum(1 for r in rows if r["kept"])
    out = {"n": n, "kept": nk, "exp_total": tot, "exp_kept": kept}
    if tot > 0:
        out["time_lost"] = 1.0 - kept / tot
        out["snr_loss"] = 1.0 - float(np.sqrt(kept / tot))
    elif n:
        out["time_lost"] = 1.0 - nk / n
        out["snr_loss"] = 1.0 - float(np.sqrt(nk / n))
    f_all = _median([r.get("fwhm") for r in rows])
    f_kept = _median([r.get("fwhm") for r in rows if r["kept"]])
    if f_all and f_kept:
        out["fwhm_all"], out["fwhm_kept"] = f_all, f_kept
        out["fwhm_gain"] = 1.0 - f_kept / f_all
    return out


def focus_drift(rows):
    """Lien entre la FWHM et la température du capteur (dérive de mise au
    point), corrigé de la hauteur de l'objet quand on la connaît.
    Renvoie None si les données ne suffisent pas."""
    pts = [r for r in rows if r["kept"] and r.get("fwhm")
           and r.get("temp") is not None]
    if len(pts) < 10:
        return None
    t = np.array([r["temp"] for r in pts], float)
    f = np.array([r["fwhm"] for r in pts], float)
    if t.max() - t.min() < 1.5:
        return {"range": float(t.max() - t.min()), "n": len(pts),
                "enough": False}
    cols = [np.ones_like(t), t]
    use_alt = all(r.get("alt") and r["alt"] > 5 for r in pts)
    if use_alt:
        airmass = np.array([1.0 / np.sin(np.radians(r["alt"])) for r in pts])
        if airmass.max() - airmass.min() > 0.05:
            cols.append(airmass)
        else:
            use_alt = False
    A = np.vstack(cols).T
    coef, *_ = np.linalg.lstsq(A, f, rcond=None)
    pred = A @ coef
    ss_res = float(((f - pred) ** 2).sum())
    ss_tot = float(((f - f.mean()) ** 2).sum()) or 1e-12
    r_t = float(np.corrcoef(t, f)[0, 1]) if t.std() > 0 and f.std() > 0 \
        else 0.0
    med = float(np.median(f))
    slope = float(coef[1]) / med           # fraction de FWHM par °C
    out = {"n": len(pts), "enough": True, "range": float(t.max() - t.min()),
           "t_min": float(t.min()), "t_max": float(t.max()),
           "slope": slope, "r": r_t, "r2": 1.0 - ss_res / ss_tot,
           "with_alt": use_alt}
    # significatif : corrélation nette et effet de plus de 1 % par degré
    out["significant"] = abs(r_t) >= 0.5 and abs(slope) >= 0.01
    if out["significant"] and slope < 0:
        out["refocus_every"] = 0.05 / -slope   # °C pour perdre 5 % de FWHM
    return out


def nights_summary(rows):
    """Statistiques par nuit."""
    by = OrderedDict()
    for r in sorted(rows, key=lambda r: (r.get("date") is None,
                                         r.get("date") or datetime.min)):
        by.setdefault(night_of(r.get("date")), []).append(r)
    out = []
    for night, rs in by.items():
        kept = [r for r in rs if r["kept"]]
        temps = [r["temp"] for r in rs if r.get("temp") is not None]
        alts = [r["alt"] for r in rs if r.get("alt") is not None]
        dates = [r["date"] for r in rs if r.get("date")]
        out.append({
            "night": night, "n": len(rs), "kept": len(kept),
            "exp_kept": sum(r.get("exp") or 0 for r in kept),
            "fwhm": _median([r.get("fwhm") for r in kept]),
            "bg": _median([r.get("bg") for r in kept]),
            "temps": (min(temps), max(temps)) if temps else None,
            "alts": (min(alts), max(alts)) if alts else None,
            "span": (min(dates), max(dates)) if dates else None,
            "trails": sum(1 for r in rs if r.get("trail"))})
    return out


def slow_exposure_windows(rows, lat, fwhm, halfdiag, exptime):
    """Moments où la rotation théorique allonge les étoiles des coins de
    plus d'une demi-FWHM pendant une pose. Renvoie [(début, fin, max °/h)]."""
    if not (fwhm and halfdiag and exptime):
        return []
    bad = []
    for r in sorted((r for r in rows if r.get("alt") is not None
                     and r.get("date")), key=lambda r: r["date"]):
        rate = rotation_rate_theory(lat, r["alt"], r["az"])
        if rate is None:
            continue
        trail = np.radians(abs(rate)) / 3600.0 * exptime * halfdiag
        bad.append((r["date"], trail > 0.5 * fwhm, abs(rate)))
    out, cur = [], None
    for date, flag, rate in bad:
        if flag:
            cur = [date, date, rate] if cur is None else \
                [cur[0], date, max(cur[2], rate)]
        elif cur is not None:
            out.append(tuple(cur))
            cur = None
    if cur is not None:
        out.append(tuple(cur))
    return out


def utc_to_local(dt):
    """Date UTC (sans fuseau) -> heure locale de l'ordinateur."""
    if dt is None:
        return None
    try:
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return dt


def _num(h, *keys):
    for k in keys:
        v = h.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def read_meta(path):
    """Informations utiles d'un fichier FITS ou CR2, lues rapidement."""
    meta = {"type": "light", "exptime": None, "gain": None, "temp": None,
            "binning": None, "filter": "", "size": None, "bayer": "",
            "date": None}
    try:
        if is_raw(path):
            ex = read_tiff_exif(path)
            meta["exptime"] = ex.get("exptime")
            meta["gain"] = float(ex["iso"]) if ex.get("iso") else None
            meta["bayer"] = "CR2"
            try:
                meta["date"] = datetime.strptime(ex.get("datetime", ""),
                                                 "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
            meta["type"] = detect_type("", path)
            return meta
        h = fits.getheader(path, 0)
        if int(h.get("NAXIS", 0) or 0) < 2:
            try:
                h1 = fits.getheader(path, 1)
                h1.update({k: v for k, v in h.items()
                           if k not in h1 and k not in ("SIMPLE", "EXTEND")})
                h = h1
            except (OSError, IndexError):
                pass
        meta["type"] = detect_type(h.get("IMAGETYP") or h.get("FRAME") or
                                   h.get("FRAMETYP") or "", path)
        if (_num(h, "STACKCNT") or 0) > 1 or (_num(h, "NCOMBINE") or 0) > 1:
            meta["type"] = "master"          # image déjà empilée
        meta["exptime"] = _num(h, "EXPTIME", "EXPOSURE")
        meta["gain"] = _num(h, "GAIN", "ISOSPEED", "ISO")
        meta["temp"] = _num(h, *TEMP_KEYS)
        meta["eqmode"] = _num(h, "EQMODE")
        xb, yb = _num(h, "XBINNING"), _num(h, "YBINNING")
        meta["binning"] = f"{int(xb or 1)}x{int(yb or xb or 1)}"
        meta["filter"] = str(h.get("FILTER", "") or "").strip()
        n1, n2 = h.get("NAXIS1"), h.get("NAXIS2")
        if n1 and n2:
            meta["size"] = (int(n1), int(n2))
        meta["bayer"] = str(h.get("BAYERPAT", "") or "").strip().upper()
        utc = parse_date_obs(h)
        meta["date_utc"] = utc
        meta["date"] = utc_to_local(utc)                  # DATE-OBS est en UTC
        meta["camera"] = str(h.get("CAMERA", "") or "").strip()
        meta["instrument"] = str(h.get("INSTRUME", "") or "").strip()
        meta["object"] = str(h.get("OBJECT", "") or "").strip()
        meta["ra"] = parse_angle(h.get("RA"))
        if meta["ra"] is None:
            meta["ra"] = parse_angle(h.get("OBJCTRA"), hours=True)
        meta["dec"] = parse_angle(h.get("DEC"))
        if meta["dec"] is None:
            meta["dec"] = parse_angle(h.get("OBJCTDEC"))
        meta["site_lat"] = parse_angle(h.get("SITELAT") or h.get("OBSLAT")
                                       or h.get("LAT-OBS"))
        meta["site_lon"] = parse_angle(h.get("SITELONG") or h.get("OBSLONG")
                                       or h.get("LONG-OBS"))
    except Exception:   # noqa: BLE001  (fichier abîmé : on garde le défaut)
        meta["type"] = detect_type("", path)
    # ce que l'en-tête ne dit pas, le nom du fichier ou du dossier le dit
    # parfois (Dwarf : dark_exp_15.000000_gain_60_bin_1_38C, DWARF_RAW_…_EXP_15
    # _GAIN_60_…, NGC 7380_15s120_…)
    hint = name_hints(path)
    for key in ("temp", "exptime", "gain"):
        if meta.get(key) is None and hint.get(key) is not None:
            meta[key] = hint[key]
    return meta


TEMP_KEYS = ("CCD-TEMP", "CCD_TEMP", "DET-TEMP", "SENSOR-TEMP", "SENSTEMP",
             "TEMPERAT", "CAMTEMP", "SET-TEMP")


def name_hints(path):
    """Température, pose et gain lus dans le nom du fichier ou du dossier."""
    out = {}
    name = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    m = re.search(r"_(-?\d{1,2}(?:\.\d+)?)C(?=[_.\s]|$)", name)
    if m:
        out["temp"] = float(m.group(1))
    for text in (name, parent):
        if "exptime" not in out:
            m = (re.search(r"exp_(\d+(?:\.\d+)?)", text, re.I)
                 or re.search(r"_(\d+(?:\.\d+)?)s(\d+)_", text))
            if m:
                out["exptime"] = float(m.group(1))
        if "gain" not in out:
            m = (re.search(r"gain_(\d+)", text, re.I)
                 or re.search(r"_\d+(?:\.\d+)?s(\d+)_", text))
            if m:
                out["gain"] = float(m.group(1))
    return out


def type_count(t, n):
    """« 3 darks », « 1 offset », « 2 darks de flat »."""
    words = FRAME_TYPES[t].lower().split(" ", 1)
    if n > 1:
        words[0] += "s"
    return f"{n} " + " ".join(words)


def _vals(metas, key):
    return [m[key] for m in metas if m.get(key) not in (None, "")]


def check_calibration(metas, temp_tol=2.0):
    """Vérifie la cohérence entre brutes et calibrations.
    metas : liste de dicts read_meta (images gardées). Renvoie (résumé,
    [(niveau, texte)]) avec niveau « ok », « info » ou « attention »."""
    by = {t: [m for m in metas if m["type"] == t] for t in FRAME_TYPES}
    summary = ", ".join(type_count(t, len(v)) for t, v in by.items() if v)
    out = []
    lights = by["light"]
    if not lights:
        return summary, [("attention", "aucune image brute parmi les images "
                                       "gardées")]

    def uniq(ms, key):
        return sorted(set(_vals(ms, key)), key=str)

    def same(a, b, tol=0.0):
        return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9) + 1e-9

    le, lg = uniq(lights, "exptime"), uniq(lights, "gain")
    lb, ls, lf = uniq(lights, "binning"), uniq(lights, "size"), \
        uniq(lights, "filter")
    lt = _vals(lights, "temp")
    if len(le) > 1:
        out.append(("info", "brutes de plusieurs temps de pose : " +
                    ", ".join(f"{fr(x, 0)} s" for x in le)))
    if len(lf) > 1:
        out.append(("attention", "brutes prises avec plusieurs filtres (" +
                    ", ".join(lf) + ") : à traiter séparément"))
    if len(ls) > 1 or len(lb) > 1:
        out.append(("attention", "brutes de tailles ou binnings différents"))

    def check_geometry(name, ms):
        if lb and uniq(ms, "binning") and uniq(ms, "binning") != lb:
            out.append(("attention", f"{name} : binning "
                        f"{', '.join(uniq(ms, 'binning'))} au lieu de "
                        f"{', '.join(lb)}"))
        if ls and uniq(ms, "size") and uniq(ms, "size") != ls:
            out.append(("attention", f"{name} : taille différente des brutes"))

    darks = by["dark"]
    if darks:
        check_geometry("darks", darks)
        de = uniq(darks, "exptime")
        if le and de and not any(same(d, l, 0.02) for d in de for l in le):
            out.append(("attention", "darks de " + ", ".join(
                f"{fr(x, 0)} s" for x in de) + " pour des brutes de " +
                ", ".join(f"{fr(x, 0)} s" for x in le)))
        dg = uniq(darks, "gain")
        if lg and dg and set(dg) != set(lg):
            out.append(("attention", f"gain des darks ({', '.join(fr(x, 0) for x in dg)}) "
                        f"différent des brutes ({', '.join(fr(x, 0) for x in lg)})"))
        dt = _vals(darks, "temp")
        if lt and dt:
            diff = abs(float(np.median(dt)) - float(np.median(lt)))
            if diff > temp_tol:
                out.append(("attention", f"darks à {fr(float(np.median(dt)), 1)} °C, "
                            f"brutes à {fr(float(np.median(lt)), 1)} °C "
                            f"(écart {fr(diff, 1)} °C)"))
        if not any(o[0] == "attention" and o[1].startswith(("darks", "gain des darks"))
                   for o in out):
            out.append(("ok", f"darks cohérents avec les brutes ({len(darks)})"))
    else:
        out.append(("info", "pas de darks : le signal thermique et les "
                            "pixels chauds ne seront pas retirés"))

    flats = by["flat"]
    if flats:
        check_geometry("flats", flats)
        ff = uniq(flats, "filter")
        if lf and ff and not set(lf) <= set(ff):
            out.append(("attention", f"flats pris avec le filtre {', '.join(ff)}, "
                        f"brutes avec {', '.join(lf)}"))
        if not by["bias"] and not by["darkflat"]:
            out.append(("info", "flats sans offsets ni darks de flat : leur "
                                "calibration sera approximative"))
        out.append(("ok", f"{len(flats)} flats"))
    else:
        out.append(("info", "pas de flats : vignetage et poussières ne seront "
                            "pas corrigés"))

    dfl = by["darkflat"]
    if dfl and flats:
        fe, de = uniq(flats, "exptime"), uniq(dfl, "exptime")
        if fe and de and not any(same(a, b, 0.05) for a in fe for b in de):
            out.append(("attention", "les darks de flat n'ont pas le même "
                                     "temps de pose que les flats"))

    biases = by["bias"]
    if biases:
        check_geometry("offsets", biases)
        bg = uniq(biases, "gain")
        if lg and bg and set(bg) != set(lg):
            out.append(("attention", "gain des offsets différent des brutes"))
        be = uniq(biases, "exptime")
        if be and max(be) > 1.0:
            out.append(("attention", f"offsets de {fr(max(be), 1)} s : un offset "
                                     "doit être une pose la plus courte possible"))
    return summary, out


DARK_MIN_COUNT = 15         # en dessous, le bruit du master dark se voit


def _fs(x):
    """Temps de pose lisible : 15 s, 0,5 s."""
    if x is None:
        return "?"
    return fr(x, 0) if abs(x - round(x)) < 1e-6 else fr(x, 2).rstrip("0")


def dark_stats(data):
    """Médiane et bruit (écart robuste) d'un dark."""
    a = subsample(np.asarray(data, dtype=np.float32), 400_000)
    med = float(np.median(a))
    noise = float(1.4826 * np.median(np.abs(a - med)))
    return med, noise


def _same(a, b, rel=0.02):
    if a is None or b is None:
        return True              # information absente : on ne juge pas
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-9) + 1e-9


FLAT_MIN_COUNT = 20


def flat_stats(data, bitpix):
    """Niveau, saturation, vignetage et motif réduit d'un flat."""
    a = np.asarray(data, dtype=np.float32)
    if a.ndim == 3:
        a = a.mean(axis=0 if a.shape[0] == 3 else 2)
    full = 255.0 if bitpix == 8 else 65535.0
    s = subsample(a, 400_000)
    med = float(np.median(s))
    sat = float(np.mean(s >= 0.98 * full))
    h, w = a.shape
    # motif réduit 24 × 36 (moyennes de blocs), normalisé
    bh, bw = max(1, h // 24), max(1, w // 36)
    small = a[:bh * 24, :bw * 36].reshape(24, bh, 36, bw).mean(axis=(1, 3))
    norm = small / max(float(np.median(small)), 1e-6)
    centre = float(norm[9:15, 14:22].mean())
    corners = float(np.mean([norm[:3, :3].mean(), norm[:3, -3:].mean(),
                             norm[-3:, :3].mean(), norm[-3:, -3:].mean()]))
    return {"median": med, "level": med / full, "sat": sat,
            "vignetting": 1.0 - corners / max(centre, 1e-6), "pattern": norm}


def flat_control(flats, stats, lights=()):
    """Verdict pour chaque flat. flats : [(source, meta)] ;
    stats : {source: flat_stats} ; lights : [(source, meta)] des brutes.
    Renvoie (verdicts {source: (niveau, texte)}, conseils)."""
    verdicts, advice = {}, []
    measured = [s for s, _ in flats if s in stats]
    # motif de référence : médiane des flats de même taille
    shapes = {}
    for s in measured:
        shapes.setdefault(stats[s]["pattern"].shape, []).append(s)
    odd = {}
    for srcs in shapes.values():
        if len(srcs) < 3:
            continue
        ref = np.median([stats[s]["pattern"] for s in srcs], axis=0)
        diffs = {s: float(np.sqrt(np.mean((stats[s]["pattern"] - ref) ** 2)))
                 for s in srcs}
        med_d = float(np.median(list(diffs.values())))
        spread = float(1.4826 * np.median([abs(d - med_d)
                                           for d in diffs.values()]))
        lim = max(med_d + 6 * spread, 0.01)
        for s, d in diffs.items():
            if d > lim:
                odd[s] = d
    levels = [stats[s]["median"] for s in measured]
    med_level = float(np.median(levels)) if levels else None
    lf = {m.get("filter") for _, m in lights if m.get("filter")}
    for s, m in flats:
        st = stats.get(s)
        if st is None:
            verdicts[s] = ("info", "non mesuré")
            continue
        if lf and m.get("filter") and m["filter"] not in lf:
            verdicts[s] = ("bad", f"filtre {m['filter']} (brutes : "
                                  f"{', '.join(sorted(lf))})")
        elif st["sat"] > 0.005:
            verdicts[s] = ("bad", f"saturé ({fr(100 * st['sat'], 1)} % des "
                                  f"pixels)")
        elif st["level"] > 0.85:
            verdicts[s] = ("bad", f"trop clair ({pct(st['level'])} de la "
                                  f"dynamique)")
        elif st["level"] < 0.08:
            verdicts[s] = ("bad", f"trop sombre ({pct(st['level'])} de la "
                                  f"dynamique)")
        elif s in odd:
            verdicts[s] = ("bad", "motif différent des autres flats "
                                  "(filtre, poussière déplacée, caméra "
                                  "tournée ?)")
        elif med_level and abs(st["median"] - med_level) > 0.3 * med_level:
            verdicts[s] = ("warn", f"niveau {st['median']:.0f} très différent"
                                   f" des autres ({med_level:.0f})")
        elif st["level"] < 0.2 or st["level"] > 0.7:
            verdicts[s] = ("warn", f"niveau {pct(st['level'])} : visez 30 à "
                                   f"60 % de la dynamique")
        else:
            verdicts[s] = ("ok", "conforme")
    n_ok = sum(1 for v in verdicts.values() if v[0] in ("ok", "warn"))
    if flats and n_ok < FLAT_MIN_COUNT:
        advice.append(f"{n_ok} flat(s) utilisables : visez "
                      f"{FLAT_MIN_COUNT} à 30 flats pour un master propre.")
    if any(v[0] == "bad" for v in verdicts.values()):
        advice.append("Les flats non conformes peuvent être rejetés : ils "
                      "fausseraient la correction du vignetage et des "
                      "poussières.")
    vig = [stats[s]["vignetting"] for s in measured]
    if vig:
        advice.append(f"Vignetage mesuré : les coins reçoivent "
                      f"{pct(float(np.median(vig)))} de lumière en moins que "
                      f"le centre.")
    return verdicts, advice


def dark_control(lights, darks, tol=2.0, stats=None):
    """Contrôle des darks face aux brutes.
    lights, darks : [(source, meta)] ; stats : {source: (médiane, bruit)}.
    Renvoie (groupes, verdicts des darks, conseils)."""
    stats = stats or {}
    # 1. verdict de chaque dark
    verdicts = {}
    for src, d in darks:
        same_exp = [m for _, m in lights if _same(d.get("exptime"),
                                                  m.get("exptime"))]
        same_gain = [m for m in same_exp if _same(d.get("gain"),
                                                  m.get("gain"), 0.0)]
        same_geo = [m for m in same_gain
                    if (not d.get("binning") or not m.get("binning")
                        or d["binning"] == m["binning"])
                    and (not d.get("size") or not m.get("size")
                         or d["size"] == m["size"])]
        if not lights:
            v = ("info", "pas de brutes à comparer")
        elif not same_exp:
            v = ("bad", f"pose différente ({_fs(d.get('exptime'))} s)")
        elif not same_gain:
            v = ("bad", f"gain différent ({fr(d.get('gain'), 0)})")
        elif not same_geo:
            v = ("bad", "taille ou binning différent")
        else:
            lt = _vals(same_geo, "temp")
            t = d.get("temp")
            if t is not None and lt and not (min(lt) - tol <= t
                                             <= max(lt) + tol):
                v = ("bad", f"température hors plage ({fr(t, 1)} °C pour "
                            f"des brutes de {fr(min(lt), 1)} à "
                            f"{fr(max(lt), 1)} °C)")
            else:
                v = ("ok", "conforme")
        verdicts[src] = v
    # 2. dark anormalement clair (fuite de lumière, capot oublié…)
    groups_d = {}
    for src, d in darks:
        if src in stats:
            groups_d.setdefault((d.get("exptime"), d.get("gain")),
                                []).append(src)
    for srcs in groups_d.values():
        if len(srcs) < 3:
            continue
        meds = np.array([stats[s][0] for s in srcs])
        ref = float(np.median(meds))
        spread = float(1.4826 * np.median(np.abs(meds - ref)))
        noise = float(np.median([stats[s][1] for s in srcs]))
        lim = max(5 * spread, 0.5 * noise, 0.01 * abs(ref), 1.0)
        for s, m in zip(srcs, meds):
            if m - ref > lim and verdicts[s][0] != "bad":
                verdicts[s] = ("bad", f"anormalement clair (médiane "
                                      f"{m:.0f} au lieu de {ref:.0f} : "
                                      f"lumière parasite ?)")
    # 3. couverture de chaque groupe de brutes
    keys = {}
    for src, m in lights:
        keys.setdefault((m.get("exptime"), m.get("gain"),
                         m.get("binning") or ""), []).append(m)
    groups = []
    good = [d for s, d in darks if verdicts.get(s, ("",))[0] == "ok"]
    for (exp, gain, binning), ms in sorted(keys.items(), key=str):
        comp = [d for d in good if _same(d.get("exptime"), exp)
                and _same(d.get("gain"), gain, 0.0)
                and (not d.get("binning") or not binning
                     or d["binning"] == binning)]
        lt, dt = _vals(ms, "temp"), _vals(comp, "temp")
        if lt and dt:
            covered = sum(1 for t in lt if any(abs(t - x) <= tol
                                               for x in dt))
            cover = covered / len(lt)
        else:
            cover = None
        if not comp:
            verdict = ("bad", "aucun dark compatible")
        elif cover is not None and cover < 0.999:
            verdict = ("warn", f"températures couvertes à {pct(cover)}")
        elif len(comp) < DARK_MIN_COUNT:
            verdict = ("warn", f"peu de darks ({len(comp)}) : visez "
                               f"{DARK_MIN_COUNT} à 20 ou plus")
        else:
            verdict = ("ok", "couvert")
        groups.append({"exptime": exp, "gain": gain, "binning": binning,
                       "n": len(ms), "temps": (min(lt), max(lt)) if lt
                       else None, "darks": len(comp),
                       "dark_temps": (min(dt), max(dt)) if dt else None,
                       "cover": cover, "verdict": verdict})
    # 4. conseils
    advice = []
    for g in groups:
        if g["verdict"][0] == "ok":
            continue
        what = (f"Brutes de {_fs(g['exptime'])} s, gain "
                f"{fr(g['gain'], 0)}")
        todo = ("ajoutez des darks" if g["darks"] else "faites des darks")
        if g["temps"]:
            lo, hi = g["temps"]
            rng = (f"{fr(lo, 1)} °C" if hi - lo < 0.05 else
                   f"de {fr(lo, 1)} à {fr(hi, 1)} °C")
            advice.append(f"{what} : {todo} de même pose et même gain, "
                          f"capteur entre {fr(lo - tol, 1)} et "
                          f"{fr(hi + tol, 1)} °C (brutes {rng}) ; "
                          f"visez {DARK_MIN_COUNT} à 20 darks ou plus.")
        else:
            advice.append(f"{what} : {todo} de même pose et même gain, à la "
                          f"même température que les brutes ; visez "
                          f"{DARK_MIN_COUNT} à 20 darks ou plus.")
    if any(v[0] == "bad" for v in verdicts.values()):
        advice.append("Les darks non conformes peuvent être rejetés : ils "
                      "dégraderaient le master dark.")
    return groups, verdicts, advice


def is_cfa(metas):
    return any(m["type"] == "light" and m.get("bayer") for m in metas)


def siril_script(types, cfa):
    """Script de prétraitement Siril adapté aux calibrations disponibles.
    types : ensemble des types présents ; cfa : capteur couleur."""
    has = types.__contains__
    # Siril 1.4 n'exécute rien si le script contient des caractères non
    # ASCII (accents) : tout le texte reste en ASCII, comme les scripts
    # officiels.
    L = ["############################################",
         "# Script de pretraitement genere par AstroCheck",
         "# Lancer dans Siril avec ce dossier comme",
         "# repertoire de travail (voir la notice).",
         "############################################",
         "",
         "requires 1.2.0",
         ""]
    if has("bias"):
        L += ["# Offset maitre", "cd biases", "convert bias -out=../process",
              "cd ../process", "stack bias rej 3 3 -nonorm", "cd ..", ""]
    if has("darkflat") and has("flat"):
        L += ["# Dark de flat maitre", "cd darkflats",
              "convert darkflat -out=../process", "cd ../process",
              "stack darkflat rej 3 3 -nonorm", "cd ..", ""]
    if has("flat"):
        L += ["# Flat maitre", "cd flats", "convert flat -out=../process",
              "cd ../process"]
        if has("darkflat"):
            L += ["calibrate flat -bias=darkflat_stacked",
                  "stack pp_flat rej 3 3 -norm=mul"]
        elif has("bias"):
            L += ["calibrate flat -bias=bias_stacked",
                  "stack pp_flat rej 3 3 -norm=mul"]
        else:
            L += ["stack flat rej 3 3 -norm=mul -out=pp_flat_stacked"]
        L += ["cd ..", ""]
    if has("dark"):
        L += ["# Dark maitre", "cd darks", "convert dark -out=../process",
              "cd ../process", "stack dark rej 3 3 -nonorm", "cd ..", ""]

    opts = []
    if has("dark"):
        opts += ["-dark=dark_stacked", "-cc=dark"]
    elif has("bias"):
        opts.append("-bias=bias_stacked")
    if has("flat"):
        opts.append("-flat=pp_flat_stacked")
    if cfa:
        opts += ["-cfa", "-equalize_cfa", "-debayer"] if opts else ["-debayer"]
    L += ["# Brutes : calibration", "cd lights",
          "convert light -out=../process", "cd ../process"]
    if opts:
        L += ["calibrate light " + " ".join(opts)]
        seq = "pp_light"
    else:
        seq = "light"
    L += ["",
          "# Alignement : la reference est la premiere image",
          "# (light_00001 si vous avez numerote a l'export)",
          f"register {seq}",
          "",
          "# Empilement",
          f"stack r_{seq} rej 3 3 -norm=addscale -output_norm"
          + (" -rgb_equal" if cfa else "") + " -out=resultat",
          "",
          "load resultat",
          "mirrorx -bottomup",
          "save ../resultat_$STACKCNT:%d$x$EXPTIME:%d$s",
          "cd ..",
          "close",
          ""]
    text = "\n".join(L)
    # garde-fou : aucun caractère non ASCII ne doit subsister
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore")
    return text.decode("ascii")


# ----------------------------------------------------------------------------
# Rotation de champ et zone utile après empilement
# ----------------------------------------------------------------------------

MATCH_STARS = 60              # étoiles les plus brillantes comparées


def _pairs(xy):
    """Distances et angles (modulo 180°) de toutes les paires d'étoiles."""
    n = len(xy)
    i, j = np.triu_indices(n, 1)
    d = xy[j] - xy[i]
    dist = np.hypot(d[:, 0], d[:, 1])
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0
    return dist, ang


def _rot(xy, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return xy @ np.array([[c, s], [-s, c]])


def match_transform(ref_xy, img_xy, tol=1.5):
    """Rotation et décalage qui amènent les étoiles de l'image sur celles de
    la référence : p_ref = R(angle) · p_img + (dx, dy).
    Renvoie {"angle", "dx", "dy", "n"} ou None si le motif n'est pas trouvé.
    Indépendant du sens de rotation et du décalage, robuste aux étoiles
    manquantes ou en plus."""
    ref = np.asarray(ref_xy, dtype=np.float64)[:MATCH_STARS]
    img = np.asarray(img_xy, dtype=np.float64)[:MATCH_STARS]
    if len(ref) < 6 or len(img) < 6:
        return None
    dr, ar = _pairs(ref)
    di, ai = _pairs(img)
    # paires de longueur égale (la rotation conserve les distances)
    keep_r = dr > 20 * tol
    dr, ar = dr[keep_r], ar[keep_r]
    order = np.argsort(dr)
    dr, ar = dr[order], ar[order]
    lo = np.searchsorted(dr, di - tol)
    hi = np.searchsorted(dr, di + tol)
    diffs = []
    for k in np.nonzero(hi > lo)[0]:
        diffs.append((ar[lo[k]:hi[k]] - ai[k]) % 180.0)
    if not diffs:
        return None
    diffs = np.concatenate(diffs)
    hist, edges = np.histogram(diffs, bins=360, range=(0, 180))
    hist = hist + np.roll(hist, 1) + np.roll(hist, -1)      # lissage circulaire
    peak = int(np.argmax(hist))
    centre = (edges[peak] + edges[peak + 1]) / 2.0
    # moyenne circulaire (sur 180°) des valeurs proches du pic
    near = ((diffs - centre + 90.0) % 180.0 - 90.0)
    near = diffs[np.abs(near) < 1.0]
    dbl = np.radians(2.0 * near)
    phi0 = (np.degrees(np.arctan2(np.sin(dbl).mean(), np.cos(dbl).mean()))
            / 2.0) % 180.0

    best = None
    for phi in (phi0, phi0 + 180.0):
        rot = _rot(img, phi)
        vec = (ref[:, None, :] - rot[None, :, :]).reshape(-1, 2)
        # regroupement des décalages candidats
        q = np.round(vec / (4 * tol)).astype(np.int64)
        keys, counts = np.unique(q, axis=0, return_counts=True)
        top = keys[np.argmax(counts)]
        sel = np.all(np.abs(q - top) <= 1, axis=1)
        t = np.median(vec[sel], axis=0)
        score = int(sel.sum())
        if best is None or score > best[0]:
            best = (score, phi, t)
    _score, phi, t = best

    # affinage : moindres carrés sur les étoiles appariées
    for radius in (8 * tol, 4 * tol, 2 * tol, 2 * tol):
        rot = _rot(img, phi) + t
        d = np.hypot(*(ref[:, None, :] - rot[None, :, :]).transpose(2, 0, 1))
        j = np.argmin(d, axis=1)
        ok = d[np.arange(len(ref)), j] < radius
        if ok.sum() < 6:
            return None
        a, b = img[j[ok]], ref[ok]
        ac, bc = a - a.mean(0), b - b.mean(0)
        cross = (ac[:, 0] * bc[:, 1] - ac[:, 1] * bc[:, 0]).sum()
        dot = (ac * bc).sum()
        phi = float(np.degrees(np.arctan2(cross, dot)))
        t = b.mean(0) - _rot(a.mean(0)[None, :], phi)[0]
    angle = (phi + 180.0) % 360.0 - 180.0
    return {"angle": angle, "dx": float(t[0]), "dy": float(t[1]),
            "n": int(ok.sum())}


def frame_polygon(w, h, tr=None):
    """Contour d'une image (w × h) placé dans le repère de la référence."""
    pts = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    if tr:
        pts = _rot(pts, tr["angle"]) + [tr["dx"], tr["dy"]]
    return pts


def clip_convex(poly, clip):
    """Intersection de deux polygones convexes (Sutherland–Hodgman)."""
    def area2(p):
        return np.sum(p[:, 0] * np.roll(p[:, 1], -1)
                      - np.roll(p[:, 0], -1) * p[:, 1])
    if area2(clip) < 0:
        clip = clip[::-1]
    out = [tuple(p) for p in poly]
    for k in range(len(clip)):
        a, b = clip[k], clip[(k + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break

        def inside(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-9

        def cut(p, q):
            x1, y1, x2, y2 = p[0], p[1], q[0], q[1]
            x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
            den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(den) < 1e-12:
                return q
            u = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
            return (x1 + u * (x2 - x1), y1 + u * (y2 - y1))
        prev = inp[-1]
        for cur in inp:
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
            prev = cur
    return np.array(out, dtype=np.float64) if out else np.zeros((0, 2))


def polygon_area(p):
    if len(p) < 3:
        return 0.0
    return 0.5 * abs(np.sum(p[:, 0] * np.roll(p[:, 1], -1)
                            - np.roll(p[:, 0], -1) * p[:, 1]))


def largest_rectangle(poly, w, h, grid=480):
    """Plus grand rectangle aux bords droits contenu dans le polygone
    convexe (repère de la référence). Renvoie (x0, y0, x1, y1)."""
    if len(poly) < 3:
        return None
    step = max(w, h) / float(grid)
    nx, ny = int(np.ceil(w / step)), int(np.ceil(h / step))
    xs = (np.arange(nx) + 0.5) * step
    ys = (np.arange(ny) + 0.5) * step
    X, Y = np.meshgrid(xs, ys)
    inside = np.ones_like(X, dtype=bool)
    p = poly if (np.sum(poly[:, 0] * np.roll(poly[:, 1], -1)
                        - np.roll(poly[:, 0], -1) * poly[:, 1]) > 0) \
        else poly[::-1]
    for k in range(len(p)):
        a, b = p[k], p[(k + 1) % len(p)]
        inside &= ((b[0] - a[0]) * (Y - a[1]) - (b[1] - a[1]) * (X - a[0])) >= 0
    # plus grand rectangle dans une matrice binaire (histogrammes)
    heights = np.zeros(nx, dtype=np.int64)
    best = (0, None)
    for r in range(ny):
        heights = np.where(inside[r], heights + 1, 0)
        stack = []
        for c in range(nx + 1):
            hgt = heights[c] if c < nx else 0
            start = c
            while stack and stack[-1][1] >= hgt:
                s0, sh = stack.pop()
                area = sh * (c - s0)
                if area > best[0]:
                    best = (area, (s0, r - sh + 1, c, r + 1))
                start = s0
            stack.append((start, hgt))
    if best[1] is None:
        return None
    c0, r0, c1, r1 = best[1]
    return (c0 * step, r0 * step, min(c1 * step, w), min(r1 * step, h))


def usable_area(w, h, transforms):
    """Zone couverte par toutes les images une fois alignées sur la
    référence. transforms : {source: transformation ou None (référence)}.
    Renvoie (polygone commun, rectangle, fraction de surface)."""
    poly = frame_polygon(w, h)
    for tr in transforms.values():
        if tr:
            poly = clip_convex(poly, frame_polygon(w, h, tr))
            if len(poly) < 3:
                return poly, None, 0.0
    rect = largest_rectangle(poly, w, h)
    return poly, rect, polygon_area(poly) / float(w * h)


def rotation_rates(entries):
    """Vitesse de rotation (°/min) de chaque image, d'après ses voisines
    dans le temps. entries : [(source, date, angle)] triables par date."""
    pts = sorted((e for e in entries if e[1] is not None and e[2] is not None),
                 key=lambda e: e[1])
    rates = {}
    for k, (src, date, ang) in enumerate(pts):
        a, b = pts[max(0, k - 2)], pts[min(len(pts) - 1, k + 2)]
        dt = (b[1] - a[1]).total_seconds() / 60.0
        if dt > 0:
            rates[src] = (b[2] - a[2]) / dt
    return rates


def compose(a, b):
    """Transformation « b puis a » (b : image → intermédiaire,
    a : intermédiaire → référence)."""
    t = _rot(np.array([[b["dx"], b["dy"]]]), a["angle"])[0] + [a["dx"], a["dy"]]
    ang = (a["angle"] + b["angle"] + 180.0) % 360.0 - 180.0
    return {"angle": ang, "dx": float(t[0]), "dy": float(t[1]),
            "n": min(a["n"], b["n"])}


def align_series(stars, order, ref):
    """Transformation de chaque image vers la référence.
    stars : {source: positions des étoiles} ; order : sources dans l'ordre
    de prise de vue. Une image qui ne s'apparie pas directement avec la
    référence (forte rotation) est reliée par ses voisines dans le temps."""
    out = {ref: {"angle": 0.0, "dx": 0.0, "dy": 0.0, "n": len(stars[ref])}}
    order = [s for s in order if s in stars]
    if ref not in order:
        return out
    k0 = order.index(ref)
    for direction in (+1, -1):
        prev = ref
        k = k0 + direction
        while 0 <= k < len(order):
            src = order[k]
            tr = match_transform(stars[ref], stars[src])
            if tr is None and prev in out:
                step = match_transform(stars[prev], stars[src])
                if step is not None:
                    tr = compose(out[prev], step)
            if tr is not None:
                out[src] = tr
                prev = src
            k += direction
    return out


# ----------------------------------------------------------------------------
# Bande étroite : Ha et OIII à partir d'un empilement couleur
# ----------------------------------------------------------------------------

NARROW_MODES = OrderedDict([
    ("rgb", "Image couleur classique (RVB)"),
    ("hoo", "Filtre multibande : Ha, OIII et HOO"),
    ("sho", "Filtre multibande : Ha, OIII, HOO et SHO synthétique"),
])
STRUCT_KEYS = {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3",
               "EXTEND", "BZERO", "BSCALE", "DATAMIN", "DATAMAX"}


def match_level(src, ref):
    """Ramène src au niveau de ref (même médiane et même dispersion),
    comme le fait le script officiel de Siril pour l'OIII."""
    s, r = subsample(src), subsample(ref)
    ms, mr = float(np.median(s)), float(np.median(r))
    ds = float(np.median(np.abs(s - ms))) or 1e-12
    dr = float(np.median(np.abs(r - mr)))
    return (src - ms) * (dr / ds) + mr


def narrowband_compose(path, palettes=("hoo",), green_ha=0.5):
    """Sépare Ha (canal rouge) et OIII (moyenne vert + bleu) d'un empilement
    couleur pris avec un filtre bi, tri ou quadri-bande, puis compose les
    palettes demandées. Les images restent linéaires. Renvoie les fichiers
    écrits."""
    data, header, _ = read_fits(path)
    if data.ndim != 3:
        raise ValueError("il faut une image couleur (3 canaux)")
    ha = data[..., 0].astype(np.float32)
    oiii = (0.5 * (data[..., 1] + data[..., 2])).astype(np.float32)
    oiii = np.maximum(match_level(oiii, ha), 0).astype(np.float32)

    h = fits.Header()
    for card in header.cards:
        if card.keyword not in STRUCT_KEYS and card.keyword:
            h.append(card)
    stem, _ext = split_ext(path)
    written = []

    def save(suffix, arr, history):
        hh = h.copy()
        hh["HISTORY"] = "AstroCheck : " + history
        out = unique_path(f"{stem}_{suffix}.fit")
        fits.PrimaryHDU(data=np.ascontiguousarray(arr, dtype=np.float32),
                        header=hh).writeto(out)
        written.append(out)

    save("Ha", ha, "Ha = canal rouge")
    save("OIII", oiii, "OIII = (vert + bleu) / 2, niveau aligne sur Ha")
    if "hoo" in palettes:
        save("HOO", np.stack([ha, oiii, oiii]), "HOO : R = Ha, V = B = OIII")
    if "sho" in palettes:
        g = green_ha * ha + (1.0 - green_ha) * oiii
        save("SHO_synthetique", np.stack([ha, g, oiii]),
             f"SHO synthetique : R = Ha, V = {green_ha:.2f} Ha + "
             f"{1 - green_ha:.2f} OIII, B = OIII")
    return written


# ----------------------------------------------------------------------------
# Export SER et FITS
# ----------------------------------------------------------------------------

def parse_date_obs(h):
    """DATE-OBS (UTC) -> datetime naïf, ou None."""
    s = str(h.get("DATE-OBS", "") or "").strip().rstrip("Z")
    if not s:
        return None
    if "T" not in s and "TIME-OBS" in h:
        s += "T" + str(h["TIME-OBS"]).strip()
    if "." in s:
        main, frac = s.split(".", 1)
        n = 0
        while n < len(frac) and frac[n].isdigit():
            n += 1
        digits, rest = frac[:n][:6], frac[n:]       # rest = fuseau éventuel
        s = f"{main}.{digits.ljust(6, '0')}{rest}" if digits else main + rest
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_ser_pixels(data, depth, bitpix):
    if depth == 8:
        return np.clip(np.rint(data), 0, 255).astype(np.uint8)
    if bitpix < 0 and data.max() <= 1.0:      # FITS flottant normalisé 0–1
        data = data * 65535.0
    return np.clip(np.rint(data), 0, 65535).astype("<u2")


def write_ser(out_path, srcs, progress=None):
    """Écrit une vidéo SER (v3) à partir d'images de même taille.

    Les pixels sont écrits tels qu'ils sont stockés (pas de débayerisation,
    pas de retournement) ; la matrice de Bayer est reprise de BAYERPAT.
    Renvoie (nombre d'images, taille en octets).
    """
    data, header, bitpix = read_source(srcs[0])
    shape = data.shape
    h, w = shape[:2]
    depth = 8 if bitpix == 8 else 16
    if data.ndim == 3:
        color_id = SER_COLOR_IDS["RGB"]
    else:
        bayer = str(header.get("BAYERPAT", "") or "").strip().upper()
        color_id = SER_COLOR_IDS.get(bayer, 0)

    def text40(key):
        val = str(header.get(key, "") or "").strip()
        return val.encode("latin-1", "replace")[:40].ljust(40, b"\0")

    tmp = out_path + ".part"
    dates = []
    try:
        with open(tmp, "wb") as f:
            f.write(b"\0" * SER_HEADER.size)      # en-tête écrit à la fin
            for i, src in enumerate(srcs):
                if progress and not progress(i, src):
                    raise Cancelled()
                if i > 0:
                    data, header_i, bitpix = read_source(src)
                else:
                    header_i = header
                if data.shape != shape:
                    raise ValueError(
                        f"{src_name(src)} : dimensions {data.shape} "
                        f"différentes de la 1re image {shape}")
                f.write(to_ser_pixels(data, depth, bitpix).tobytes())
                dates.append(parse_date_obs(header_i))

            utc = local = 0
            if dates and all(d is not None for d in dates):
                f.write(np.array([to_ticks(d) for d in dates],
                                 dtype="<i8").tobytes())
                utc = to_ticks(dates[0])
                local = to_ticks(dates[0].replace(tzinfo=timezone.utc)
                                 .astimezone().replace(tzinfo=None))

            # LittleEndian = 0 : données en petit-boutiste (convention de
            # fait suivie par Siril, PIPP, AutoStakkert, SER Player…)
            hdr = SER_HEADER.pack(
                b"LUCAM-RECORDER", 0, color_id, 0, w, h, depth, len(srcs),
                text40("OBSERVER"), text40("INSTRUME"), text40("TELESCOP"),
                local, utc)
            f.seek(0)
            f.write(hdr)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        open_ser.cache_clear()          # au cas où un SER aurait été remplacé
    return len(srcs), os.path.getsize(out_path)


def split_ext(name):
    """Sépare l'extension, en gardant les doubles extensions (.fits.gz)."""
    low = name.lower()
    for ext in (".fits.gz", ".fit.gz"):
        if low.endswith(ext):
            return name[:-len(ext)], name[-len(ext):]
    return os.path.splitext(name)


def export_order(srcs, ref=None, numbered=False):
    """Ordre d'export : chronologique (nom, puis numéro d'image) ; avec la
    numérotation, l'image de référence passe en premier (Siril aligne par
    défaut sur la première image de la séquence)."""
    order = sorted(srcs, key=lambda s: (os.path.basename(s[0]), s[0], s[1]))
    if numbered and ref in order:
        order.remove(ref)
        order.insert(0, ref)
    return order


def export_files(out_dir, srcs, progress=None, link=False, raw_to_fits=False,
                 numbered=False, prefix="light"):
    """Place les images dans out_dir pour Siril ou PixInsight.

    - FITS (et CR2 gardés tels quels) : copie, ou lien symbolique si link
    - CR2 avec raw_to_fits : converti en FITS 16 bits (mosaïque brute)
    - image d'un SER : écrite en FITS (pixels bruts, 8 ou 16 bits)
    Avec numbered, les fichiers sont renommés prefix_00001, prefix_00002…
    dans l'ordre de srcs. Renvoie la liste des fichiers créés.
    """
    os.makedirs(out_dir, exist_ok=True)
    digits = max(5, len(str(len(srcs))))
    created = []
    for i, src in enumerate(srcs):
        if progress and not progress(i, src):
            raise Cancelled()
        path, frame = src
        if frame >= 0:
            stem = os.path.splitext(os.path.basename(path))[0]
            stem = f"{stem}_{frame + 1:0{max(4, digits - 1)}d}"
            ext = ".fits"
        else:
            stem, ext = split_ext(os.path.basename(path))
            if is_raw(path) and raw_to_fits:
                ext = ".fits"
        if numbered:
            stem = f"{prefix}_{i + 1:0{digits}d}"
        dst = unique_path(os.path.join(out_dir, stem + ext))

        if frame < 0 and is_raw(path) and raw_to_fits:
            data, header, _ = read_raw(path)
            fits.PrimaryHDU(data=data.astype(np.uint16),
                            header=header).writeto(dst)
        elif frame < 0:
            if link:
                os.symlink(os.path.abspath(path), dst)
            else:
                shutil.copy2(path, dst)
        else:
            ser = open_ser(path)
            raw = ser.read_frame(frame)
            raw = raw.astype(np.uint8 if ser.bpp == 1 else np.uint16)
            if raw.ndim == 3:
                raw = np.moveaxis(raw, -1, 0)        # FITS : (3, H, W)
            fits.PrimaryHDU(data=np.ascontiguousarray(raw),
                            header=ser.header(frame)).writeto(dst)
        created.append(dst)
    return created


WEIGHT_KEY = "ACWEIGHT"


def write_weight(path, weight):
    """Écrit le poids AstroCheck (0 à 1) dans l'en-tête d'une COPIE FITS.
    Jamais dans un lien (cela modifierait l'original) ni un .gz."""
    if os.path.islink(path) or \
            not path.lower().endswith((".fit", ".fits", ".fts")):
        return False
    try:
        with fits.open(path, mode="update", memmap=False,
                       do_not_scale_image_data=True) as hdul:
            hdul[0].header[WEIGHT_KEY] = (round(float(weight), 4),
                                          "AstroCheck quality weight (0-1)")
        return True
    except Exception:   # noqa: BLE001
        return False


def export_size(srcs, link=False, raw_to_fits=False):
    """Estimation de la place disque nécessaire (octets)."""
    total = 0
    for path, frame in srcs:
        try:
            if frame >= 0:
                total += open_ser(path).frame_size + 5760
            elif link and not (is_raw(path) and raw_to_fits):
                continue
            elif is_raw(path) and raw_to_fits:
                total += os.path.getsize(path) * 4     # 16 bits non compressé
            else:
                total += os.path.getsize(path)
        except (OSError, ValueError):
            pass
    return total


# ----------------------------------------------------------------------------
# Chargement en arrière-plan
# ----------------------------------------------------------------------------

class LoaderSignals(QObject):
    done = pyqtSignal(object, object, object)   # clé, résultat, erreur


class LoadJob(QRunnable):
    def __init__(self, key, params, signals):
        super().__init__()
        self.key, self.params, self.signals = key, params, signals

    def run(self):
        src, _mode, debayer = self.key
        try:
            res = process_source(src, self.params, debayer)
            self.signals.done.emit(self.key, res, None)
        except Exception as e:   # noqa: BLE001
            self.signals.done.emit(self.key, None, str(e))


class AnalyzeSignals(QObject):
    done = pyqtSignal(object, object, object)   # source, mesures, erreur


class AnalyzeJob(QRunnable):
    def __init__(self, src, signals):
        super().__init__()
        self.src, self.signals = src, signals

    def run(self):
        try:
            self.signals.done.emit(self.src, measure_source(self.src), None)
        except Exception as e:   # noqa: BLE001
            self.signals.done.emit(self.src, None, str(e))


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------

class ImageView(QGraphicsView):
    """Vue avec zoom molette, déplacement à la souris, double-clic = ajuster."""

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self.pix)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(24, 24, 26))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.fit_mode = True

    def set_image(self, qimg):
        self.pix.setPixmap(QPixmap.fromImage(qimg))
        self._scene.setSceneRect(self.pix.boundingRect())
        if self.fit_mode:
            self.fit()

    def clear(self):
        self.pix.setPixmap(QPixmap())

    def fit(self):
        self.fit_mode = True
        if not self.pix.pixmap().isNull():
            self.fitInView(self.pix, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_100(self):
        self.fit_mode = False
        self.resetTransform()

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.fit_mode = False
        self.scale(factor, factor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode:
            self.fit()

    def mouseDoubleClickEvent(self, e):
        self.fit()


class ImageItem(QTreeWidgetItem):
    """Ligne de la liste ; le tri suit la valeur numérique des colonnes de
    mesure, les images non mesurées restant toujours en bas."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        if col == COL_NAME:
            return self.text(0) < other.text(0)
        a, b = self.data(col, ROLE_SORT), other.data(col, ROLE_SORT)
        if a is None or b is None:
            if a is None and b is None:
                return self.text(0) < other.text(0)
            descending = tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            # « vide » doit finir en bas quel que soit le sens du tri
            return (a is None) == descending
        if a == b:
            return self.text(0) < other.text(0)
        return a < b


class NightGraph(QWidget):
    """Évolution des mesures des brutes au fil de la nuit.
    Un clic sur un point affiche l'image correspondante."""
    pointClicked = pyqtSignal(object)

    STATE_COLORS = {"kept": "#4a90d9", "auto": "#e08a3c", "manual": "#e05555"}

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self.points = []        # (source, x, {mesure: valeur}, état, nom)
        self.panels = []        # (clé, titre, décimales)
        self.limits = {}        # clé -> (médiane, seuil)
        self.time_axis = False
        self.t0 = None
        self.current = None
        self._hits = []

    def set_data(self, entries, limits, planet, rot=None, alt=None):
        """entries : [(source, date ou None, mesures, état, nom)] ;
        rot : {source: rotation du champ en degrés}."""
        rot, alt = rot or {}, alt or {}
        if planet:
            self.panels = [("sharp", "Netteté (%)", 0), ("bg", "Fond", 0)]
        else:
            self.panels = [("fwhm", "FWHM (px)", 2), ("ecc", "Excent.", 2),
                           ("stars", "Étoiles", 0), ("bg", "Fond", 0)]
            if rot:
                self.panels.append(("rot", "Rotation (°)", 1))
        if alt:
            self.panels.append(("alt", "Hauteur (°)", 0))
        self.limits = {}
        for k, (med, lim, _sense) in ((k, v) for k, v in limits.items()
                                      if k != "mode"):
            self.limits[k] = (med, lim)
        med_sh = limits.get("sharp", (None,))[0] if planet else None
        self.time_axis = bool(entries) and all(e[1] for e in entries)
        self.segments = []      # nuits : (x début, x fin, date de début)
        self.seg_of = {}
        if self.time_axis:
            entries = sorted(entries, key=lambda e: e[1])
            self.t0 = entries[0][1]
            # une coupure de plus de 3 h sépare deux nuits
            starts = [0] + [k for k in range(1, len(entries))
                            if (entries[k][1] - entries[k - 1][1])
                            > timedelta(hours=3)]
            bounds = list(zip(starts, starts[1:] + [len(entries)]))
            lengths = [(entries[b - 1][1] - entries[a][1]).total_seconds()
                       / 60.0 for a, b in bounds]
            gap = max(10.0, 0.04 * sum(lengths))
            off = 0.0
            for (a, b), ln in zip(bounds, lengths):
                self.segments.append((off, off + ln, entries[a][1]))
                for e in entries[a:b]:
                    self.seg_of[e[0]] = len(self.segments) - 1
                off += ln + gap
        else:
            entries = sorted(entries, key=lambda e: (e[4], e[0]))
        self.points = []
        for i, (src, date, m, state, name) in enumerate(entries):
            if self.time_axis:
                x0_, _x1, t_start = self.segments[self.seg_of[src]]
                x = x0_ + (date - t_start).total_seconds() / 60.0
            else:
                x = float(i + 1)
            vals = {k: m.get(k) for k in ("fwhm", "ecc", "stars", "bg")}
            vals["rot"] = rot.get(src)
            vals["alt"] = alt.get(src)
            if planet and med_sh:
                vals["sharp"] = 100.0 * m["sharp"] / med_sh
            self.points.append((src, x, vals, state, name))
        if planet and med_sh and "sharp" in self.limits:
            med, lim = self.limits["sharp"]
            self.limits["sharp"] = (100.0, 100.0 * lim / med_sh)
        self.update()

    def set_current(self, src):
        self.current = src
        self.update()

    def _xfmt(self, x):
        if not self.time_axis:
            return f"{x:.0f}"
        t = self.t0 + timedelta(minutes=x)
        return t.strftime("%H:%M")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        bg, fg = pal.color(QPalette.ColorRole.Base), pal.color(QPalette.ColorRole.Text)
        mid = QColor(fg)
        mid.setAlpha(60)
        p.fillRect(self.rect(), bg)
        self._hits = []
        if not self.points:
            p.setPen(fg)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Lancez l'analyse (Ctrl+R) pour voir l'évolution des "
                       "mesures pendant la nuit.")
            return
        W, H = self.width(), self.height()
        L, R, T, B = 70, 14, 22, 22
        xs = [pt[1] for pt in self.points]
        x0, x1 = min(xs), max(xs)
        if x1 <= x0:
            x0, x1 = x0 - 1, x1 + 1
        span = x1 - x0
        x0, x1 = x0 - 0.02 * span, x1 + 0.02 * span

        def px(x):
            return L + (x - x0) / (x1 - x0) * (W - L - R)

        n = len(self.panels)
        ph = (H - T - B) / max(n, 1)
        small = p.font()
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        p.setFont(small)
        cur_x = None
        for i, (key, title, dec) in enumerate(self.panels):
            top, bottom = T + i * ph + 4, T + (i + 1) * ph - 4
            vals = [pt[2].get(key) for pt in self.points
                    if pt[2].get(key) is not None]
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            med, lim = self.limits.get(key, (None, None))
            rng = (hi - lo) or abs(hi) * 0.05 or 1.0
            if lim is not None and lo - 0.6 * rng <= lim <= hi + 0.6 * rng:
                lo, hi = min(lo, lim), max(hi, lim)
            rng = (hi - lo) or 1.0
            lo, hi = lo - 0.08 * rng, hi + 0.08 * rng

            def py(v, lo=lo, hi=hi, top=top, bottom=bottom):
                return bottom - (v - lo) / (hi - lo) * (bottom - top)

            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(mid, 1))
            p.drawRect(QRectF(L, top, W - L - R, bottom - top))
            line_h = p.fontMetrics().height()
            p.setPen(fg)
            p.drawText(QRectF(2, top, L - 8, line_h), Qt.AlignmentFlag.AlignRight, title)
            if bottom - top >= 3 * line_h:        # place pour mini et maxi
                p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 150))
                p.drawText(QRectF(2, top + line_h, L - 8, line_h),
                           Qt.AlignmentFlag.AlignRight, fr(hi, dec))
                p.drawText(QRectF(2, bottom - line_h, L - 8, line_h),
                           Qt.AlignmentFlag.AlignRight, fr(lo, dec))
            if med is not None and lo <= med <= hi:
                pen = QPen(QColor(fg.red(), fg.green(), fg.blue(), 110), 1,
                           Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(L, py(med)), QPointF(W - R, py(med)))
            if lim is not None and lo <= lim <= hi:
                pen = QPen(QColor("#e08a3c"), 1, Qt.PenStyle.DotLine)
                p.setPen(pen)
                p.drawLine(QPointF(L, py(lim)), QPointF(W - R, py(lim)))
            # tracé
            line = QPen(QColor(fg.red(), fg.green(), fg.blue(), 45), 1)
            p.setPen(line)
            prev, prev_seg = None, None
            for pt in self.points:
                v = pt[2].get(key)
                seg = self.seg_of.get(pt[0])
                if v is None or seg != prev_seg:
                    prev = None
                prev_seg = seg
                if v is None:
                    continue
                cur = QPointF(px(pt[1]), py(v))
                if prev is not None:
                    p.drawLine(prev, cur)
                prev = cur
            p.setPen(Qt.PenStyle.NoPen)
            for src, x, vals_, state, name in self.points:
                v = vals_.get(key)
                if v is None:
                    continue
                c = QPointF(px(x), py(v))
                p.setBrush(QColor(self.STATE_COLORS[state]))
                r = 2.6 if state == "kept" else 3.4
                p.drawEllipse(c, r, r)
                self._hits.append((c, src, f"{name}\n{title} : {fr(v, dec)}"))
                if src == self.current:
                    cur_x = c.x()
        # image affichée
        if cur_x is not None:
            p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 140), 1))
            p.drawLine(QPointF(cur_x, T), QPointF(cur_x, H - B))
        # axe horizontal
        p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 170))
        if self.time_axis and len(self.segments) > 1:
            total = x1 - x0
            for k, (sa, sb, t_start) in enumerate(self.segments):
                share = max(1, int(8 * (sb - sa + 1) / total))
                step = next((s_ for s_ in (5, 10, 15, 30, 60, 120, 240)
                             if (sb - sa) / s_ <= share), 480)
                day0 = t_start.replace(hour=0, minute=0, second=0,
                                       microsecond=0)
                off = (t_start - day0).total_seconds() / 60.0 - sa
                a = -(-(sa + off) // step) * step
                p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 170))
                while a - off <= sb:
                    t = day0 + timedelta(minutes=a)
                    p.drawText(QRectF(px(a - off) - 30, H - B + 4, 60, 16),
                               Qt.AlignmentFlag.AlignHCenter,
                               t.strftime("%H:%M"))
                    a += step
                night = (t_start - timedelta(hours=12)).strftime("%d/%m")
                p.drawText(QRectF(px(sa), T - 16, 160, 14),
                           Qt.AlignmentFlag.AlignLeft,
                           f"nuit du {night}")
                if k:
                    p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 90),
                                  1, Qt.PenStyle.DashLine))
                    xs_ = (px(self.segments[k - 1][1]) + px(sa)) / 2
                    p.drawLine(QPointF(xs_, T), QPointF(xs_, H - B))
            p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 170))
        elif self.time_axis:
            total = x1 - x0
            step = next((s_ for s_ in (5, 10, 15, 30, 60, 120, 240)
                         if total / s_ <= 8), 480)
            day0 = self.t0.replace(hour=0, minute=0, second=0, microsecond=0)
            off = (self.t0 - day0).total_seconds() / 60.0
            a = -(-(x0 + off) // step) * step       # première heure ronde
            while a - off <= x1:
                t = day0 + timedelta(minutes=a)
                p.drawText(QRectF(px(a - off) - 30, H - B + 4, 60, 16),
                           Qt.AlignmentFlag.AlignHCenter, t.strftime("%H:%M"))
                a += step
        else:
            n_pts = len(self.points)
            step = max(1, round(n_pts / 8))
            for k in range(1, n_pts + 1, step):
                p.drawText(QRectF(px(k) - 30, H - B + 4, 60, 16),
                           Qt.AlignmentFlag.AlignHCenter, f"n° {k}")
        # légende, en haut à droite
        fm = p.fontMetrics()
        items = (("kept", "gardée"), ("auto", "présélection"),
                 ("manual", "rejet manuel"))
        x = W - R - sum(fm.horizontalAdvance(lbl) + 26 for _, lbl in items)
        if self.time_axis and len(self.segments) < 2:
            p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 150))
            p.drawText(QRectF(L, 3, 200, T - 4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "heure locale")
        for state, label in items:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.STATE_COLORS[state]))
            p.drawEllipse(QPointF(x + 4, T / 2 + 1), 3.4, 3.4)
            p.setPen(fg)
            p.drawText(QRectF(x + 11, 3, fm.horizontalAdvance(label) + 10, T - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            x += fm.horizontalAdvance(label) + 26

    def _nearest(self, pos):
        best, bd = None, 81.0
        for c, src, text in self._hits:
            d = (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2
            if d < bd:
                best, bd = (src, text), d
        return best

    def mouseMoveEvent(self, e):
        hit = self._nearest(e.position().toPoint())
        if hit:
            QToolTip.showText(e.globalPosition().toPoint(), hit[1], self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, e):
        hit = self._nearest(e.position().toPoint())
        if hit:
            self.pointClicked.emit(hit[0])


class FileList(QTreeWidget):
    """Liste des images, avec une interface proche de QListWidget."""
    toggleRequested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setColumnCount(len(COLUMNS))
        self.setHeaderLabels(COLUMNS)
        self.setColumnHidden(COL_SHARP, True)    # affichée en planétaire
        self.setColumnHidden(COL_ROT, True)      # affichée si le champ tourne
        self.setColumnHidden(COL_ALT, True)      # affichée si on la connaît
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)        # rapide avec un long SER
        self.setAllColumnsShowFocus(True)
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            if c not in LEFT_COLUMNS:
                set_align(self.headerItem(), c, ALIGN_RIGHT)
        for c, w in ((COL_NUM, 50), (COL_TYPE, 70), (COL_TEMP, 54),
                     (COL_ALT, 50), (COL_SCORE, 58),
                     (COL_FWHM, 52), (COL_ECC, 56), (COL_ROT, 56),
                     (COL_STARS, 58), (COL_BG, 62), (COL_SHARP, 62),
                     (COL_REASON, 150)):
            self.setColumnWidth(c, w)
        tips = {COL_TYPE: "Brute, dark, flat, offset ou dark de flat, "
                          "d'après l'en-tête FITS ou le nom du fichier",
                COL_NUM: "Numéro de l'image dans la séquence de prise de vue "
                         "(1 = la première), par dossier et par type ; ne "
                         "change ni avec l'analyse ni avec le tri",
                COL_TEMP: "Température du capteur en °C (en-tête FITS : "
                          "CCD-TEMP, DET-TEMP…, ou nom du fichier)",
                COL_ALT: "Hauteur de l'objet au-dessus de l'horizon, en "
                         "degrés, au moment de la pose (d'après RA, DEC, la "
                         "date et le lieu d'observation)",
                COL_ROT: "Rotation du champ par rapport à l'image de "
                         "référence, en degrés (monture alt-azimutale)",
                COL_SCORE: "Qualité globale des brutes, 100 = image de "
                           "référence",
                COL_FWHM: "Largeur à mi-hauteur des étoiles, en pixels du "
                          "capteur (plus petit = plus net)",
                COL_ECC: "Excentricité des étoiles : 0 = rondes, "
                         "proche de 1 = allongées (filé, vent, suivi)",
                COL_STARS: "Nombre d'étoiles détectées (baisse avec nuages, "
                           "buée, voile)",
                COL_BG: "Niveau du fond de ciel (monte avec Lune, aube, "
                        "voile, pollution)",
                COL_SHARP: "Netteté relative, utile en planétaire "
                           "(100 = la plus nette)",
                COL_REASON: "Pourquoi l'image est rejetée : par la "
                            "présélection ou à la main"}
        for c, t in tips.items():
            self.headerItem().setToolTip(c, t)
        self.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.setSortingEnabled(True)

    # interface façon QListWidget
    def count(self):
        return self.topLevelItemCount()

    def item(self, row):
        return self.topLevelItem(row)

    def row(self, item):
        return self.indexOfTopLevelItem(item)

    def takeItem(self, row):
        return self.takeTopLevelItem(row)

    def addItem(self, item):
        self.addTopLevelItem(item)

    def currentRow(self):
        it = self.currentItem()
        return self.indexOfTopLevelItem(it) if it is not None else -1

    def setCurrentRow(self, row):
        it = self.topLevelItem(row)
        if it is not None:
            self.setCurrentItem(it)
            self.scrollToItem(it)

    def keyPressEvent(self, e):
        if (e.key() in (Qt.Key.Key_Space.value, Qt.Key.Key_Delete.value,
                        Qt.Key.Key_X.value)
                and e.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.toggleRequested.emit()
            return
        super().keyPressEvent(e)


SETTINGS = None


def settings():
    """Réglages mémorisés entre deux lancements (~/.config/astrocheck).
    Au premier lancement, les réglages de fitsview sont repris."""
    global SETTINGS
    if SETTINGS is None:
        SETTINGS = QSettings(APP_ID, APP_ID)
        if not SETTINGS.allKeys():
            old = QSettings("fitsview", "fitsview")
            for key in old.allKeys():
                SETTINGS.setValue(key, old.value(key))
    return SETTINGS


def setting_bool(key, default):
    v = settings().value(key, default)
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v)


_SIRIL_CMD = None


def find_siril():
    """Commande pour lancer siril-cli : installé normalement ou en Flatpak.
    Renvoie une liste (programme et arguments) ou None."""
    global _SIRIL_CMD
    if _SIRIL_CMD is not None:
        return _SIRIL_CMD or None
    cmd = []
    exe = shutil.which("siril-cli")
    if exe:
        cmd = [exe]
    elif shutil.which("flatpak"):
        try:
            ok = subprocess.run(["flatpak", "info", "org.siril.Siril"],
                                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            cmd = ["flatpak", "run", "--command=siril-cli", "org.siril.Siril"]
    _SIRIL_CMD = cmd
    return cmd or None


class ExportDialog(QDialog):
    """Options de l'export vers un dossier de traitement."""

    def __init__(self, parent, n_kept, n_sel, folder, has_raw, has_ser,
                 has_measures, has_ref, kept_summary=""):
        super().__init__(parent)
        self.setWindowTitle("Exporter vers un dossier de traitement")
        self.setMinimumWidth(540)
        self.has_raw, self.has_measures, self.has_ref = \
            has_raw, has_measures, has_ref
        self.siril_cmd = find_siril()
        lay = QVBoxLayout(self)

        box = QGroupBox("Logiciel de traitement")
        bl = QHBoxLayout(box)
        self.rb_pi = QRadioButton("PixInsight")
        self.rb_siril = QRadioButton("Siril")
        bl.addWidget(self.rb_pi)
        bl.addWidget(self.rb_siril)
        bl.addStretch(1)
        lay.addWidget(box)

        box = QGroupBox("Images à exporter")
        bl = QVBoxLayout(box)
        self.rb_kept = QRadioButton(
            f"Toutes les images gardées ({kept_summary or n_kept})")
        self.rb_sel = QRadioButton(
            f"Seulement les images surlignées dans la liste ({n_sel})")
        bl.addWidget(self.rb_kept)
        bl.addWidget(self.rb_sel)
        self.rb_sel.setEnabled(n_sel > 0)
        (self.rb_sel if n_sel > 1 else self.rb_kept).setChecked(True)
        lay.addWidget(box)

        box = QGroupBox("Destination")
        bl = QVBoxLayout(box)
        row = QHBoxLayout()
        self.dest = QLineEdit(folder)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.dest)
        row.addWidget(browse)
        bl.addLayout(row)
        self.cb_lights = QCheckBox("Ranger par type : sous-dossiers lights, "
                                   "darks, flats, biases")
        bl.addWidget(self.cb_lights)
        lay.addWidget(box)

        box = QGroupBox("Fichiers")
        bl = QVBoxLayout(box)
        self.rb_copy = QRadioButton("Copier les fichiers")
        self.rb_link = QRadioButton("Créer des liens vers les originaux "
                                    "(instantané, ne prend pas de place)")
        bl.addWidget(self.rb_copy)
        bl.addWidget(self.rb_link)
        self.cb_raw = QCheckBox("Convertir les CR2 en FITS 16 bits")
        self.cb_raw.setToolTip("Décoché, les CR2 sont gardés tels quels : "
                               "Siril et PixInsight les lisent directement.")
        self.cb_raw.setVisible(has_raw)
        bl.addWidget(self.cb_raw)
        self.cb_num = QCheckBox("Renommer les brutes light_00001, "
                                "light_00002… avec l'image de référence en "
                                "premier")
        self.cb_num.setToolTip(
            "Siril aligne par défaut sur la première image de la séquence.\n"
            "Les autres images restent dans l'ordre de prise de vue.")
        self.cb_num.setEnabled(has_ref)
        bl.addWidget(self.cb_num)
        self.cb_weight = QCheckBox(
            f"Écrire le score de chaque brute dans les copies "
            f"(mot-clé {WEIGHT_KEY}, de 0 à 1)")
        self.cb_weight.setToolTip(
            "Pour pondérer l'empilement par la qualité mesurée par AstroCheck.\n"
            "PixInsight, ImageIntegration : Weights = FITS keyword, "
            f"Weight keyword = {WEIGHT_KEY}.\n"
            "Seulement avec « Copier les fichiers » : les originaux ne sont "
            "jamais modifiés.")
        self.cb_weight.setVisible(has_measures)
        bl.addWidget(self.cb_weight)
        self.cb_csv = QCheckBox("Joindre le tableau des mesures (mesures.csv)")
        self.cb_csv.setVisible(has_measures)
        bl.addWidget(self.cb_csv)
        if has_ser:
            note = QLabel("Les images issues d'une vidéo SER sont toujours "
                          "écrites en fichiers FITS.")
            note.setStyleSheet("color: gray;")
            note.setWordWrap(True)
            bl.addWidget(note)
        lay.addWidget(box)

        self.siril_box = QGroupBox("Siril")
        bl = QVBoxLayout(self.siril_box)
        self.cb_script = QCheckBox("Écrire le script de prétraitement "
                                   "(traitement.ssf)")
        bl.addWidget(self.cb_script)
        self.cb_run = QCheckBox("Lancer Siril tout de suite et afficher le "
                                "résultat dans AstroCheck")
        if self.siril_cmd:
            self.cb_run.setToolTip("Commande : " + " ".join(self.siril_cmd))
        else:
            self.cb_run.setEnabled(False)
            self.cb_run.setToolTip("siril-cli introuvable (ni installé, ni en "
                                   "Flatpak)")
        bl.addWidget(self.cb_run)
        row = QHBoxLayout()
        row.addWidget(QLabel("Après l'empilement :"))
        self.narrow = QComboBox()
        for k, label in NARROW_MODES.items():
            self.narrow.addItem(label, k)
        self.narrow.setToolTip(
            "Avec un filtre bi, tri ou quadri-bande sur une caméra couleur, "
            "AstroCheck sépare Ha (rouge) et OIII (vert + bleu) dans l'image "
            "empilée et compose les palettes choisies.")
        row.addWidget(self.narrow, 1)
        bl.addLayout(row)
        self.cb_script.toggled.connect(self._sync)
        self.cb_lights.toggled.connect(self._sync)
        self.cb_run.toggled.connect(self._sync)
        lay.addWidget(self.siril_box)

        self.pi_note = QLabel(
            "PixInsight : dans WeightedBatchPreprocessing, « Add Directory » "
            "sur le dossier exporté. Le fichier astrocheck_resume.txt indique "
            "l'image de référence à choisir si vous la fixez à la main.")
        self.pi_note.setWordWrap(True)
        self.pi_note.setStyleSheet("color: gray;")
        lay.addWidget(self.pi_note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Exporter")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # réglages mémorisés
        target = settings().value("export/target", "pixinsight")
        (self.rb_siril if target == "siril" else self.rb_pi).setChecked(True)
        self.cb_lights.setChecked(setting_bool("export/structure", True))
        (self.rb_link if setting_bool("export/link", False)
         else self.rb_copy).setChecked(True)
        self.cb_raw.setChecked(setting_bool("export/raw_to_fits", False))
        self.cb_csv.setChecked(setting_bool("export/csv", True))
        self.cb_weight.setChecked(setting_bool("export/weight", False))
        self.rb_link.toggled.connect(self._sync)
        self.cb_script.setChecked(setting_bool("export/script", True))
        self.cb_run.setChecked(setting_bool("export/run_siril", False)
                               and bool(self.siril_cmd))
        mode = settings().value("export/narrow", "rgb")
        self.narrow.setCurrentIndex(max(0, list(NARROW_MODES).index(mode)
                                        if mode in NARROW_MODES else 0))
        self.rb_pi.toggled.connect(self.target_changed)
        self.target_changed()

    def target_changed(self, *_):
        siril = self.rb_siril.isChecked()
        self.siril_box.setVisible(siril)
        self.pi_note.setVisible(not siril)
        key = "export/numbered_" + ("siril" if siril else "pixinsight")
        self.cb_num.setChecked(setting_bool(key, siril) and self.has_ref)
        self._sync()
        self.adjustSize()

    def _sync(self, *_):
        self.cb_weight.setEnabled(not self.rb_link.isChecked())
        structure = self.cb_lights.isChecked()
        self.cb_script.setEnabled(structure)
        self.cb_run.setEnabled(bool(self.siril_cmd) and structure
                               and self.cb_script.isChecked())
        self.narrow.setEnabled(self.cb_run.isEnabled()
                               and self.cb_run.isChecked())

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de destination",
                                             self.dest.text())
        if d:
            self.dest.setText(d)

    def options(self):
        siril = self.rb_siril.isChecked()
        o = {
            "target": "siril" if siril else "pixinsight",
            "selected_only": self.rb_sel.isChecked(),
            "dest": os.path.expanduser(self.dest.text().strip()),
            "lights": self.cb_lights.isChecked(),
            "link": self.rb_link.isChecked(),
            "raw_to_fits": self.cb_raw.isChecked() and self.has_raw,
            "numbered": self.cb_num.isChecked() and self.has_ref,
            "csv": self.cb_csv.isChecked() and self.has_measures,
            "weight": self.cb_weight.isChecked() and self.has_measures
            and not self.rb_link.isChecked(),
            "script": siril and self.cb_script.isChecked()
            and self.cb_lights.isChecked(),
            "run": siril and self.cb_run.isEnabled() and self.cb_run.isChecked(),
            "narrow": self.narrow.currentData() or "rgb",
        }
        st = settings()
        st.setValue("export/target", o["target"])
        st.setValue("export/structure", o["lights"])
        st.setValue("export/link", o["link"])
        st.setValue("export/raw_to_fits", self.cb_raw.isChecked())
        st.setValue("export/csv", self.cb_csv.isChecked())
        st.setValue("export/weight", self.cb_weight.isChecked())
        st.setValue("export/script", self.cb_script.isChecked())
        st.setValue("export/run_siril", self.cb_run.isChecked())
        st.setValue("export/narrow", o["narrow"])
        st.setValue("export/numbered_" + o["target"], self.cb_num.isChecked())
        return o


class SirilRunDialog(QDialog):
    """Exécute un script avec siril-cli et affiche sa sortie en direct."""
    resultsReady = pyqtSignal(object)

    def __init__(self, parent, workdir, script):
        super().__init__(parent)
        self.setWindowTitle("Siril — " + os.path.basename(script))
        self.resize(820, 520)
        self.workdir, self.script = workdir, script
        self.started = datetime.now().timestamp()
        lay = QVBoxLayout(self)
        self.status = QLabel("Démarrage de Siril…")
        lay.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(9)
        self.log.setFont(mono)
        lay.addWidget(self.log)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Enregistrer le journal…")
        self.btn_save.clicked.connect(self.save_log_as)
        self.btn_stop = QPushButton("Arrêter")
        self.btn_close = QPushButton("Fermer")
        self.btn_close.setEnabled(False)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_close.clicked.connect(self.accept)

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.finished.connect(self.done_running)
        self.proc.errorOccurred.connect(self.failed)
        cmd = find_siril() or ["siril-cli"]
        args = cmd[1:] + ["-d", workdir, "-s", script]
        self.log.appendPlainText("$ " + " ".join(
            [cmd[0]] + [f'"{a}"' if " " in a else a for a in args]) + "\n")
        self.proc.setWorkingDirectory(workdir)
        self.proc.start(cmd[0], args)

    def read_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(
            "utf-8", "replace")
        for line in text.splitlines():
            line = line.rstrip()
            if not line:
                continue
            self.log.appendPlainText(line)
            low = line.lower()
            if "running command" in low or "exécution de la commande" in low:
                self.status.setText(line.split(":", 1)[-1].strip()[:90])

    def auto_log_path(self):
        stamp = datetime.fromtimestamp(self.started).strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.workdir, f"siril_{stamp}.log")

    def write_log(self, path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log.toPlainText() + "\n")
            return True
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Journal non enregistré :\n{e}")
            return False

    def save_log_as(self):
        out, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le journal de Siril", self.auto_log_path(),
            "Journal (*.log *.txt)")
        if out and self.write_log(out):
            self.status.setText(f"Journal enregistré : {out}")

    def find_results(self):
        """Images produites par ce passage du script (dossier de travail)."""
        out = []
        try:
            names = os.listdir(self.workdir)
        except OSError:
            return out
        for n in names:
            full = os.path.join(self.workdir, n)
            if (os.path.isfile(full) and is_fits(n)
                    and n.lower().startswith(("resultat", "result"))
                    and os.path.getmtime(full) >= self.started - 1):
                out.append(full)
        return out

    def stop(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self.log.appendPlainText("\n— arrêté par l'utilisateur —")

    def failed(self, err):
        if err == QProcess.ProcessError.FailedToStart:
            self.status.setText("Impossible de lancer siril-cli.")
            self.btn_stop.setEnabled(False)
            self.btn_close.setEnabled(True)

    def done_running(self, code, _status):
        self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.log.appendPlainText(f"\n— fin de Siril, code {code} —")
        log_path = self.auto_log_path()
        saved = self.write_log(log_path)
        results = sorted(self.find_results())
        note = (f"  Journal : {os.path.basename(log_path)}" if saved else "")
        if code == 0 and results:
            self.status.setText(f"Terminé : {len(results)} image(s) produite(s), "
                                "ouverte(s) dans AstroCheck." + note)
            self.resultsReady.emit(results)
        elif code == 0:
            self.status.setText("Siril a terminé, mais aucun fichier résultat "
                                "n'a été trouvé. Consultez le journal." + note)
        else:
            self.status.setText(f"Siril s'est arrêté avec une erreur "
                                f"(code {code}). Les dernières lignes du "
                                "journal indiquent pourquoi." + note)

    def reject(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            if QMessageBox.question(self, APP_NAME, "Siril travaille encore. "
                                    "L'arrêter ?") != QMessageBox.StandardButton.Yes:
                return
            self.proc.kill()
            self.proc.waitForFinished(3000)
        super().reject()


class PreselectDialog(QDialog):
    """Réglage des critères, avec le nombre d'images écartées en direct."""

    def __init__(self, parent, criteria, measures):
        super().__init__(parent)
        self.setWindowTitle("Critères de présélection")
        self.measures = measures
        self.planet = series_mode(measures) == "planet"
        form = QFormLayout(self)

        intro = QLabel(
            "Série planétaire : les images sont jugées sur leur netteté."
            if self.planet else
            "Les seuils sont relatifs à la série : une image est écartée "
            "quand elle s'éloigne nettement de ses voisines. Plus la valeur "
            "est petite, plus la présélection est sévère.")
        intro.setWordWrap(True)
        form.addRow(intro)

        def spin(lo, hi, step, dec, val, suffix=""):
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setDecimals(dec)
            sb.setValue(val)
            sb.setSuffix(suffix)
            sb.valueChanged.connect(self.update_preview)
            return sb

        self.sb = {
            "fwhm": spin(1.0, 5.0, 0.05, 2, criteria["fwhm"], " × médiane"),
            "stars": spin(0.5, 50, 0.5, 1, criteria["stars"], " × dispersion"),
            "ecc": spin(0.5, 50, 0.5, 1, criteria["ecc"], " × dispersion"),
            "bg": spin(0.5, 50, 0.5, 1, criteria["bg"], " × dispersion"),
            "sharp": spin(0, 100, 5, 0, criteria["sharp"], " % de la médiane"),
        }
        labels = {"fwhm": "FWHM maximale",
                  "stars": "Étoiles : écart maximal (en moins)",
                  "ecc": "Excentricité : écart maximal",
                  "bg": "Fond de ciel : écart maximal",
                  "sharp": "Netteté minimale"}
        keys = ["sharp"] if self.planet else ["fwhm", "stars", "ecc", "bg"]
        for k in keys:
            form.addRow(labels[k], self.sb[k])

        self.preview = QLabel()
        self.preview.setStyleSheet("font-weight: bold;")
        form.addRow(self.preview)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                | QDialogButtonBox.StandardButton.RestoreDefaults)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.restore)
        form.addRow(btns)
        note = QLabel("Les images que vous avez rejetées ou gardées à la "
                      "main ne sont pas modifiées.")
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        form.addRow(note)
        self.update_preview()

    def values(self):
        return OrderedDict((k, sb.value()) for k, sb in self.sb.items())

    def restore(self):
        for k, sb in self.sb.items():
            sb.setValue(PRESELECT_DEFAULTS[k])

    def update_preview(self, *_):
        n_bad = len(preselect(self.measures, self.values()))
        n = len(self.measures)
        self.preview.setText(f"→ {n_bad} image(s) sur {n} seraient écartées")


# ----------------------------------------------------------------------------
# Fenêtre principale
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(4, QThread.idealThreadCount())))
        self.signals = LoaderSignals()
        self.signals.done.connect(self.on_loaded)
        self.cache = OrderedDict()
        self.pending = set()
        self.current_src = None
        self.shown_key = None            # clé de l'image actuellement affichée

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.play_step)

        self.measures = {}               # source -> mesures (ou None)
        self.scores = {}
        self.planet = False
        self.ref = None
        self.criteria = OrderedDict(PRESELECT_DEFAULTS)
        self.analysis_pool = QThreadPool(self)
        self.analysis_pool.setMaxThreadCount(
            max(1, min(4, QThread.idealThreadCount() - 1)))
        self.asignals = AnalyzeSignals()
        self.asignals.done.connect(self.on_measured)
        self.analysis_todo = 0
        self.analysis_done = 0
        self.analysis_errors = []
        self.analysis_prog = None
        self._item_index = {}
        self.ref_manual = None       # référence choisie à la main
        self.transforms = {}         # source -> rotation/décalage vers la réf.
        self.rot_rates = {}          # source -> vitesse de rotation (°/min)
        self.zone = None             # zone utile (calculée à la demande)
        self.zone_dirty = True
        self._geom_key = None
        self._dark_stats = {}        # source -> (médiane, bruit) des darks
        self._flat_stats = {}        # source -> mesures des flats

        self._build_ui()
        self._build_menus()
        self.update_count()
        self.statusBar().showMessage(
            "Glissez-déposez des fichiers FITS, SER ou CR2, ou un dossier, "
            "ou utilisez Fichier ▸ Ajouter des fichiers")

    # --- interface -----------------------------------------------------------

    def _build_ui(self):
        self.list = FileList()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAcceptDrops(False)
        self.list.viewport().setAcceptDrops(False)
        self.list.currentItemChanged.connect(self.on_current_changed)
        self.list.toggleRequested.connect(self.toggle_reject)
        self.list.customContextMenuRequested.connect(self.list_menu)

        hint = QLabel("Espace / Suppr / X : rejeter ou garder\n"
                      "↑ ↓ : image précédente / suivante\n"
                      "P : lire / mettre en pause\n"
                      "Ctrl+R : analyser et classer\n"
                      "C : vue des coins   G : graphique")
        hint.setStyleSheet("color: gray;")
        self.count_label = QLabel()

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.setSpacing(0)
        self.series_label_w = QLabel("Série :")
        self.series_box = QComboBox()
        self.series_box.setToolTip(
            "Plusieurs séries de brutes (caméra TELE ou WIDE du Dwarf, "
            "filtre, pose, gain) : l'analyse, la présélection, la référence "
            "et l'export portent sur la série choisie")
        self.series_box.currentIndexChanged.connect(self.on_series_selected)
        self.series_box.hide()
        self.series_label_w.hide()
        self.series_keys = []
        self.active_series = None
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 0, 0, 4)
        srow.addWidget(self.series_label_w)
        srow.addWidget(self.series_box, 1)
        lv.addLayout(srow)
        self.type_tabs = QTabBar()
        self.type_tabs.setExpanding(False)
        self.type_tabs.setDrawBase(False)
        self.type_tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.type_tabs.currentChanged.connect(self.apply_type_filter)
        self.type_tabs.hide()
        self.tab_keys = []
        lv.addWidget(self.type_tabs)
        lv.addWidget(self.list)
        lv.addWidget(self.count_label)
        lv.addWidget(hint)

        self.view = ImageView()

        self.header_view = QPlainTextEdit()
        self.header_view.setReadOnly(True)
        self.header_view.setAcceptDrops(False)
        self.header_view.viewport().setAcceptDrops(False)
        self.header_view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.header_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(9)
        self.header_view.setFont(mono)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.addWidget(self.header_view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([660, 520, 260])
        self.setCentralWidget(splitter)

        self.graph = NightGraph()
        self.graph.pointClicked.connect(self.goto_src)
        self.graph_dock = QDockWidget("Évolution pendant la nuit", self)
        self.graph_dock.setObjectName("graphique")
        self.graph_dock.setWidget(self.graph)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.graph_dock)
        self.graph_dock.hide()

        tb = self.addToolBar("Affichage")
        tb.setMovable(False)
        tb.addWidget(QLabel(" Étirement : "))
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(list(STRETCH_MODES.keys()))
        self.stretch_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stretch_combo.currentIndexChanged.connect(self.refresh_current)
        tb.addWidget(self.stretch_combo)
        tb.addSeparator()
        self.debayer_cb = QCheckBox("Débayeriser (couleur)")
        self.debayer_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.debayer_cb.setToolTip(
            "Utilise la matrice de Bayer des images de caméras couleur")
        self.debayer_cb.toggled.connect(self.refresh_current)
        tb.addWidget(self.debayer_cb)
        tb.addSeparator()
        self.advance_cb = QCheckBox("Passer à l'image suivante après marquage")
        self.advance_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.advance_cb.setChecked(True)
        tb.addWidget(self.advance_cb)
        tb.addSeparator()

        self.play_action = QAction("▶ Lire", self)
        self.play_action.setCheckable(True)
        self.play_action.setShortcut(QKeySequence("P"))
        self.play_action.setToolTip("Lire la liste comme une vidéo (P)")
        self.play_action.toggled.connect(self.set_playing)
        tb.addAction(self.play_action)
        tb.addWidget(QLabel(" Vitesse : "))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(10)
        self.fps_spin.setSuffix(" im/s")
        self.fps_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.fps_spin.valueChanged.connect(self.update_play_speed)
        tb.addWidget(self.fps_spin)
        tb.addSeparator()
        self.analyze_action = QAction("Analyser et classer", self)
        self.analyze_action.setShortcut(QKeySequence("Ctrl+R"))
        self.analyze_action.setToolTip(
            "Mesure chaque image, la classe de la meilleure à la plus "
            "mauvaise et présélectionne les mauvaises (Ctrl+R)")
        self.analyze_action.triggered.connect(lambda: self.analyze())
        tb.addAction(self.analyze_action)
        self.toolbar = tb

        self.list.setFocus()

    def _build_menus(self):
        mb = self.menuBar()

        m = mb.addMenu("&Fichier")
        self._action(m, "Ajouter des fichiers FITS, SER ou CR2…",
                     self.add_files_dialog, QKeySequence.StandardKey.Open)
        self._action(m, "Ajouter un dossier…", self.add_folder_dialog, "Ctrl+D")
        m.addSeparator()
        self._action(m, "Retirer la sélection de la liste", self.remove_selected)
        self._action(m, "Vider la liste", self.clear_list)
        m.addSeparator()
        self._action(m, "Quitter", self.close, QKeySequence.StandardKey.Quit)

        m = mb.addMenu("&Tri")
        self._action(m, "Rejeter / garder la sélection   (Espace)",
                     self.toggle_reject)
        self._action(m, "Garder toutes les images", self.unmark_all)
        m.addSeparator()
        self._action(m, f"Déplacer les fichiers rejetés dans « {REJECT_DIR} »…",
                     self.move_rejected, "Ctrl+M")
        self._action(m, "Supprimer définitivement les fichiers rejetés…",
                     self.delete_rejected)

        m = mb.addMenu("A&nalyse")
        m.addAction(self.analyze_action)
        self._action(m, "Critères de présélection…", self.preselect_dialog,
                     "Ctrl+P")
        self._action(m, "Annuler la présélection automatique",
                     self.clear_preselection)
        m.addSeparator()
        self._action(m, "Classer par score (meilleure en haut)",
                     lambda: self.list.sortByColumn(COL_SCORE,
                                                    Qt.SortOrder.DescendingOrder))
        self._action(m, "Classer par nom (ordre de prise de vue)",
                     lambda: self.list.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder))
        self._action(m, "Aller à l'image de référence", self.goto_reference,
                     "Ctrl+Home")
        self._action(m, "Définir l'image affichée comme référence",
                     self.set_reference, "Ctrl+Shift+R")
        self._action(m, "Prendre la première image comme référence "
                        "(comme le Dwarf 3)", self.first_as_reference,
                     "Ctrl+Shift+Home")
        self._action(m, "Revenir à la référence automatique",
                     self.reset_reference)
        m.addSeparator()
        self._action(m, "Bilan de la séance…", self.show_session_report,
                     "Ctrl+B")
        self._action(m, "Rotation de champ et zone utile…",
                     self.show_geometry_report, "Ctrl+U")
        self._action(m, "Lieu d'observation…", self.site_dialog)
        m.addSeparator()
        self._action(m, "Vérifier les fichiers de calibration…",
                     self.show_calibration_check, "Ctrl+K")
        self._action(m, "Contrôle des darks…", self.show_dark_control,
                     "Ctrl+Shift+K")
        self._action(m, "Contrôle des flats…", self.show_flat_control,
                     "Ctrl+Shift+F")
        m.addSeparator()
        self._action(m, "Enregistrer les mesures (CSV)…", self.save_csv)

        m = mb.addMenu("&Export")
        self._action(m, "Créer un SER avec les images sélectionnées…",
                     lambda: self.export_ser(selected_only=True), "Ctrl+E")
        self._action(m, "Créer un SER avec toutes les images gardées…",
                     lambda: self.export_ser(selected_only=False),
                     "Ctrl+Shift+E")
        m.addSeparator()
        self._action(m, "Exporter vers un dossier pour Siril / PixInsight…",
                     self.export_folder, "Ctrl+Shift+S")

        m = mb.addMenu("T&raitement")
        self._action(m, "Lancer un script Siril…", self.run_siril_dialog)
        self._action(m, "Composer en bande étroite (Ha, OIII, HOO, SHO)…",
                     self.compose_dialog)

        m = mb.addMenu("&Affichage")
        m.addAction(self.play_action)
        m.addSeparator()
        self._action(m, "Ajuster à la fenêtre", self.view.fit, "F")
        self._action(m, "Zoom 100 %", self.view.zoom_100, "1")
        self.flip_action = self._action(m, "Retourner verticalement",
                                        self.redisplay, "V")
        self.flip_action.setCheckable(True)
        self.flip_action.toggled.connect(self.on_flip)
        m.addSeparator()
        self.corners_action = self._action(
            m, "Vue des coins (inspection du champ)", self.toggle_corners, "C")
        self.corners_action.setCheckable(True)
        self.corners_action.setIconText("Coins")
        self.corners_action.setToolTip("Vue des coins : centre, bords et "
                                       "coins de l'image côte à côte (C)")
        self.toolbar.addAction(self.corners_action)
        self.zone_action = self._action(
            m, "Zone utile après empilement", self.redisplay, "Z")
        self.zone_action.setCheckable(True)
        self.zone_action.setIconText("Zone")
        self.zone_action.setToolTip(
            "Zone couverte par toutes les images gardées une fois alignées : "
            "ce qui restera après recadrage (Z)")
        self.toolbar.addAction(self.zone_action)
        graph_act = self.graph_dock.toggleViewAction()
        graph_act.setText("Graphique de la nuit")
        graph_act.setShortcut(QKeySequence("G"))
        graph_act.setIconText("Graphique")
        graph_act.setToolTip("Évolution des mesures pendant la nuit (G)")
        m.addAction(graph_act)
        self.toolbar.addAction(graph_act)

    def _action(self, menu, text, slot, shortcut=None):
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda checked=False: slot())
        menu.addAction(act)
        return act

    # --- gestion de la liste -------------------------------------------------

    def all_items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    # --- séries (caméra, filtre, pose, gain) ---------------------------------

    @staticmethod
    def series_key(meta):
        """Une série = mêmes caméra, filtre, pose, gain et binning : ses
        brutes se comparent et s'empilent ensemble."""
        meta = meta or {}
        return (meta.get("camera") or meta.get("instrument") or "",
                meta.get("filter") or "", meta.get("exptime"),
                meta.get("gain"), meta.get("binning") or "")

    @staticmethod
    def series_label(key, n=None):
        cam, filt, exp, gain, binning = key
        parts = [x for x in (cam, filt) if x]
        if exp is not None:
            parts.append(f"{_fs(exp)} s")
        if gain is not None:
            parts.append(f"gain {fr(gain, 0)}")
        if binning and binning != "1x1":
            parts.append(f"bin {binning}")
        text = " · ".join(parts) or "série sans en-tête"
        return text + (f"  ({n})" if n is not None else "")

    def in_work(self, it):
        """Vrai si la ligne fait partie du travail en cours : toutes les
        calibrations, et les brutes de la série choisie."""
        if self.active_series is None or self.item_type(it) != "light":
            return True
        return self.series_key(it.data(0, ROLE_META)) == self.active_series

    def working_items(self):
        return [it for it in self.all_items() if self.in_work(it)]

    def update_series_box(self):
        counts = {}
        for it in self.all_items():
            if self.item_type(it) == "light":
                k = self.series_key(it.data(0, ROLE_META))
                counts[k] = counts.get(k, 0) + 1
        keys = sorted(counts, key=lambda k: (-counts[k], str(k)))
        if len(keys) < 2:
            changed = self.active_series is not None
            self.series_keys, self.active_series = keys, None
            self.series_box.hide()
            self.series_label_w.hide()
            if changed:
                self.series_changed()
            return
        active = self.active_series if self.active_series in keys else keys[0]
        self.series_box.blockSignals(True)
        self.series_box.clear()
        for k in keys:
            self.series_box.addItem(self.series_label(k, counts[k]))
        self.series_keys = keys
        self.series_box.setCurrentIndex(keys.index(active))
        self.series_box.blockSignals(False)
        self.series_box.show()
        self.series_label_w.show()
        if active != self.active_series:
            self.active_series = active
            self.series_changed()

    def on_series_selected(self, index):
        if 0 <= index < len(self.series_keys) and \
                self.series_keys[index] != self.active_series:
            self.active_series = self.series_keys[index]
            self.series_changed()
            self.statusBar().showMessage(
                "Série : " + self.series_label(self.active_series), 6000)

    def series_changed(self):
        """Classement, présélection, référence et graphique ne portent que
        sur la série choisie."""
        self.apply_type_filter()
        self._geom_key = None
        if self.measures:
            self.update_scores()
        self.refresh_graph()
        self.redisplay()

    def _add_item(self, src, text, meta):
        item = ImageItem([text])
        item.setData(0, ROLE_SRC, src)
        item.setData(0, ROLE_REJ, False)
        item.setData(0, ROLE_AUTO, False)
        item.setData(0, ROLE_MANUAL, False)
        item.setData(0, ROLE_META, meta)
        label = FRAME_TYPES.get(meta["type"], "Brute")
        item.setText(COL_TYPE, label)
        item.setData(COL_TYPE, ROLE_SORT,
                     list(FRAME_TYPES).index(meta["type"]))
        t = meta.get("temp")
        item.setText(COL_TEMP, "" if t is None else fr(t, 1))
        item.setData(COL_TEMP, ROLE_SORT, t)
        self.fill_altitude(item)
        for c in range(1, len(COLUMNS)):
            if c not in LEFT_COLUMNS:
                set_align(item, c, ALIGN_RIGHT)
        self.list.addItem(item)
        self.update_item(item)

    # --- lieu d'observation et hauteur de l'objet ----------------------------

    def site(self, meta=None):
        """(latitude, longitude est) : celles de l'en-tête, sinon celles
        réglées dans Analyse ▸ Lieu d'observation. None si inconnues."""
        meta = meta or {}
        if meta.get("site_lat") is not None and \
                meta.get("site_lon") is not None:
            return meta["site_lat"], meta["site_lon"]
        st = settings()
        try:
            lat = st.value("site/lat")
            lon = st.value("site/lon")
            if lat in (None, "") or lon in (None, ""):
                return None
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None

    def sky_position(self, meta):
        """(hauteur, azimut, latitude) de l'objet pendant la pose."""
        meta = meta or {}
        site = self.site(meta)
        utc = meta.get("date_utc")
        if site is None or utc is None or meta.get("ra") is None \
                or meta.get("dec") is None:
            return None
        exp = meta.get("exptime") or 0
        if (meta.get("camera") or "").upper() in ("TELE", "WIDE"):
            utc = utc - timedelta(seconds=exp / 2)   # Dwarf : date de fin
        else:
            utc = utc + timedelta(seconds=exp / 2)   # milieu de la pose
        alt, az = alt_az(meta["ra"], meta["dec"], site[0], site[1], utc)
        return alt, az, site[0]

    def fill_altitude(self, item):
        pos = self.sky_position(item.data(0, ROLE_META))
        if pos is None:
            item.setText(COL_ALT, "")
            item.setData(COL_ALT, ROLE_SORT, None)
            return False
        item.setText(COL_ALT, f"{pos[0]:.0f}°")
        item.setData(COL_ALT, ROLE_SORT, pos[0])
        return True

    def refresh_altitudes(self):
        sorting = self.list.isSortingEnabled()
        self.list.setSortingEnabled(False)
        known = [self.fill_altitude(it) for it in self.all_items()
                 if self.item_type(it) == "light"]
        self.list.setSortingEnabled(sorting)
        self.list.setColumnHidden(COL_ALT, not any(known))
        self.refresh_graph()

    def site_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Lieu d'observation")
        form = QFormLayout(dlg)
        st = settings()
        lat, lon = QDoubleSpinBox(), QDoubleSpinBox()
        for w, lim in ((lat, 90), (lon, 180)):
            w.setRange(-lim, lim)
            w.setDecimals(4)
            w.setSuffix(" °")
        try:
            lat.setValue(float(st.value("site/lat", 46.0)))
            lon.setValue(float(st.value("site/lon", 2.0)))
        except (TypeError, ValueError):
            pass
        note = QLabel("Sert à calculer la hauteur de l'objet et la rotation "
                      "de champ attendue quand l'en-tête FITS ne donne pas "
                      "le lieu (SITELAT, SITELONG). Longitude positive à "
                      "l'est de Greenwich.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow("Latitude (nord +) :", lat)
        form.addRow("Longitude (est +) :", lon)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if not dlg.exec():
            return
        st.setValue("site/lat", lat.value())
        st.setValue("site/lon", lon.value())
        self.refresh_altitudes()
        self.statusBar().showMessage(
            f"Lieu d'observation : {fr(lat.value(), 4)}°, "
            f"{fr(lon.value(), 4)}°", 6000)

    def seq_label(self, src):
        """Nom de l'image suivi de son numéro dans la séquence."""
        n = self.seq_number(src)
        return src_name(src) + (f" (n° {n} de la séquence)" if n else "")

    def seq_number(self, src):
        it = self.find_item(src)
        return it.data(COL_NUM, ROLE_SORT) if it is not None else None

    def number_sequence(self):
        """Numérote les images dans l'ordre de prise de vue (1 = la
        première), séparément pour chaque dossier et chaque type, et pour
        chaque SER. Indépendant de l'analyse et du tri de la liste."""
        groups = {}
        for it in self.all_items():
            path, frame = it.data(0, ROLE_SRC)
            if frame >= 0:
                key = ("ser", path)
            else:
                key = (os.path.dirname(path), self.item_type(it))
            groups.setdefault(key, []).append(it)
        for key, items in groups.items():
            if key[0] == "ser":
                order = sorted(items, key=lambda i: i.data(0, ROLE_SRC)[1])
            else:
                def when(i):
                    d = (i.data(0, ROLE_META) or {}).get("date")
                    return (d is None, d or datetime.min,
                            natural_key(os.path.basename(
                                i.data(0, ROLE_SRC)[0])))
                order = sorted(items, key=when)
            width = len(str(len(order)))
            for n, it in enumerate(order, 1):
                it.setText(COL_NUM, f"{n:0{width}d}")
                it.setData(COL_NUM, ROLE_SORT, n)

    def add_paths(self, paths, force_type=None):
        files = []
        for p in paths:
            if os.path.isdir(p):
                base = p.rstrip(os.sep).count(os.sep)
                for root, dirs, names in os.walk(p):
                    # sous-dossiers : darks, flats… mais pas nos exports
                    dirs[:] = sorted(
                        d for d in dirs if not d.startswith(".")
                        and d.lower() not in SKIP_DIRS
                        and root.count(os.sep) - base < 3)
                    files += [os.path.join(root, n) for n in sorted(names)
                              if is_supported(n)]
            elif os.path.isfile(p) and is_supported(p):
                files.append(p)
        if len(files) > 50:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))

        existing = {it.data(0, ROLE_SRC) for it in self.all_items()}
        added, errors = 0, []
        self.list.setUpdatesEnabled(False)
        self.list.setSortingEnabled(False)     # tri unique à la fin : rapide
        try:
            for f in files:
                f = os.path.abspath(f)
                name = os.path.basename(f)
                if is_ser(f):
                    try:
                        ser = open_ser(f)
                    except (OSError, ValueError) as e:
                        errors.append(f"{name} : {e}")
                        continue
                    digits = len(str(ser.count))
                    bayer = SER_BAYER.get(ser.color_id, "")
                    for i in range(ser.count):
                        if (f, i) not in existing:
                            meta = {"type": "light", "exptime": None,
                                    "gain": None, "temp": None,
                                    "binning": None, "filter": "",
                                    "size": (ser.width, ser.height),
                                    "bayer": bayer, "date": None}
                            if ser.timestamps is not None:
                                meta["date"] = utc_to_local(
                                    ticks_to_datetime(ser.timestamps[i]))
                            self._add_item((f, i),
                                           f"{name}  #{i + 1:0{digits}d}", meta)
                            added += 1
                elif (f, -1) not in existing:
                    meta = read_meta(f)
                    if force_type:
                        meta["type"] = force_type
                    self._add_item((f, -1), name, meta)
                    added += 1
            if added:
                self.number_sequence()
                self.list.setColumnHidden(
                    COL_ALT, not any(it.data(COL_ALT, ROLE_SORT) is not None
                                     for it in self.all_items()))
        finally:
            self.list.setSortingEnabled(True)
            self.list.setUpdatesEnabled(True)
            if len(files) > 50:
                QApplication.restoreOverrideCursor()

        self.update_count()
        if added and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        if errors:
            QMessageBox.warning(self, APP_NAME, "Fichiers illisibles :\n"
                                + "\n".join(errors[:20]))
        elif not added and paths:
            self.statusBar().showMessage(
                "Aucune nouvelle image trouvée", 4000)

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des fichiers FITS, SER ou CR2", "",
            "Images FITS, vidéos SER, RAW Canon (*.fit *.fits *.fts *.ser "
            "*.cr2 *.FIT *.FITS *.FTS *.SER *.CR2 *.gz);;Tous les fichiers (*)")
        if files:
            self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Ajouter un dossier")
        if d:
            self.add_paths([d])

    def remove_selected(self):
        self.list.setUpdatesEnabled(False)
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.list.setUpdatesEnabled(True)
        self.update_count()

    def clear_list(self):
        self.play_action.setChecked(False)
        self.analysis_pool.clear()
        self.list.clear()
        self.cache.clear()
        self.measures.clear()
        self.scores.clear()
        self.ref = None
        self.ref_manual = None
        self.transforms, self.rot_rates = {}, {}
        self.zone, self.zone_dirty, self._geom_key = None, True, None
        self._dark_stats = {}
        self._flat_stats = {}
        self._item_index = {}
        self.refresh_graph()
        self.current_src = None
        self.shown_key = None
        self.view.clear()
        self.header_view.clear()
        self.update_count()

    def update_count(self):
        n = self.list.count()
        items = self.all_items()
        r = sum(1 for it in items if it.data(0, ROLE_REJ))
        a = sum(1 for it in items
                if it.data(0, ROLE_REJ) and it.data(0, ROLE_AUTO))
        auto = f" (dont {a} par la présélection)" if a else ""
        text = f"{n} image(s)   —   {n - r} gardée(s), {r} rejetée(s){auto}"
        counts = {}
        for it in items:
            t = (it.data(0, ROLE_META) or {}).get("type", "light")
            counts[t] = counts.get(t, 0) + 1
        if len(counts) > 1:
            text += "\n" + " · ".join(type_count(t, counts[t])
                                       for t in FRAME_TYPES if t in counts)
        self.count_label.setText(text)
        self.update_type_tabs(counts)
        self.update_series_box()
        self.refresh_graph()

    # --- onglets par type ----------------------------------------------------

    TAB_LABELS = {None: "Toutes", "light": "Brutes", "dark": "Darks",
                  "flat": "Flats", "darkflat": "Darks de flat",
                  "bias": "Offsets", "master": "Résultats"}

    def update_type_tabs(self, counts):
        """Un onglet par type d'image présent, plus « Toutes »."""
        keys = [t for t in FRAME_TYPES if counts.get(t)]
        keys = ([None] + keys) if len(keys) > 1 else []
        total = sum(counts.values())
        if keys != self.tab_keys:
            current = self.current_tab_key()
            first_time = not self.tab_keys
            self.type_tabs.blockSignals(True)
            while self.type_tabs.count():
                self.type_tabs.removeTab(0)
            for k in keys:
                self.type_tabs.addTab("")
            self.tab_keys = keys
            if first_time and "light" in keys:
                current = "light"          # les brutes d'abord
            idx = keys.index(current) if current in keys else 0
            self.type_tabs.setCurrentIndex(idx)
            self.type_tabs.blockSignals(False)
            self.type_tabs.setVisible(bool(keys))
            self.apply_type_filter()
        for i, k in enumerate(keys):
            n = total if k is None else counts.get(k, 0)
            self.type_tabs.setTabText(i, f"{self.TAB_LABELS[k]} ({n})")

    def current_tab_key(self):
        i = self.type_tabs.currentIndex()
        return self.tab_keys[i] if 0 <= i < len(self.tab_keys) else None

    def show_type_tab(self, key):
        if key in self.tab_keys:
            self.type_tabs.setCurrentIndex(self.tab_keys.index(key))

    def apply_type_filter(self, *_):
        key = self.current_tab_key()
        self.list.setUpdatesEnabled(False)
        for it in self.all_items():
            it.setHidden((key is not None and self.item_type(it) != key)
                         or not self.in_work(it))
        self.list.setUpdatesEnabled(True)
        cur = self.list.currentItem()
        if cur is None or cur.isHidden():
            self.list.clearSelection()
            row = self.visible_row(-1, +1)
            if row is not None:
                self.list.setCurrentRow(row)
        else:
            self.list.scrollToItem(cur)

    def visible_row(self, row, step):
        """Prochaine ligne visible dans l'onglet courant, ou None."""
        r = row + step
        while 0 <= r < self.list.count():
            if not self.list.item(r).isHidden():
                return r
            r += step
        return None

    # --- marquage ------------------------------------------------------------

    def set_rejected(self, item, flag, auto=False, manual=False):
        item.setData(0, ROLE_REJ, flag)
        item.setData(0, ROLE_AUTO, bool(flag and auto))
        if manual:
            item.setData(0, ROLE_MANUAL, True)
        color = None
        if flag:
            color = QColor("#e08a3c") if auto else QColor("#e05555")
        for c in range(len(COLUMNS)):
            font = item.font(c)
            font.setStrikeOut(flag and c != COL_REASON)
            item.setFont(c, font)
            item.setData(c, FOREGROUND_ROLE, color)
        self.update_reason(item)
        if item.data(0, ROLE_SRC) in self.transforms:
            self.zone_dirty = True

    def update_reason(self, item):
        """Remplit la colonne Raison d'une ligne."""
        reasons = item.data(0, ROLE_REASONS) or []
        short = []
        for r in reasons:
            label = REASON_SHORT.get(reason_kind(r), r)
            if label not in short:
                short.append(label)
        if item.data(0, ROLE_REJ):
            if item.data(0, ROLE_AUTO):
                text = ", ".join(short)
            elif "dark non conforme" in short:
                text = "dark non conforme"
            elif "flat non conforme" in short:
                text = "flat non conforme"
            else:
                text = "à la main"
        elif short and item.data(0, ROLE_MANUAL):
            text = "gardée (" + ", ".join(short) + ")"
        else:
            text = ""
        m = self.measures.get(item.data(0, ROLE_SRC)) or {}
        if m.get("trail"):
            text = (text + ", traînée") if text else "traînée"
        item.setText(COL_REASON, text)
        item.setData(COL_REASON, ROLE_SORT, text or None)
        if reasons:
            item.setToolTip(COL_REASON, "\n".join(reasons))

    def toggle_reject(self):
        items = self.list.selectedItems()
        if not items and self.list.currentItem():
            items = [self.list.currentItem()]
        if not items:
            return
        new_state = not all(it.data(0, ROLE_REJ) for it in items)
        for it in items:
            self.set_rejected(it, new_state, manual=True)
        self.update_count()
        if (len(items) == 1 and self.advance_cb.isChecked()
                and not self.play_action.isChecked()):
            nxt = self.visible_row(self.list.row(items[0]), +1)
            if nxt is not None:
                self.list.setCurrentRow(nxt)

    def unmark_all(self):
        for it in self.all_items():
            it.setData(0, ROLE_MANUAL, False)
            if it.data(0, ROLE_REJ):
                self.set_rejected(it, False)
        self.update_count()

    def rejected_fits_items(self):
        """Images rejetées qui sont des fichiers FITS, et nombre d'images
        rejetées issues de SER (qui ne sont pas des fichiers séparés)."""
        rej = [it for it in self.all_items() if it.data(0, ROLE_REJ)]
        files = [it for it in rej if it.data(0, ROLE_SRC)[1] < 0]
        return files, len(rej) - len(files)

    def _ser_note(self, n_ser):
        if not n_ser:
            return ""
        return (f"\n\n{n_ser} image(s) rejetée(s) proviennent de vidéos SER : "
                "ce ne sont pas des fichiers séparés, elles restent dans la "
                "liste. Pour les écarter, exportez les images gardées en SER "
                "ou en FITS (menu Export).")

    def move_rejected(self):
        rej, n_ser = self.rejected_fits_items()
        if not rej:
            QMessageBox.information(self, APP_NAME, "Aucun fichier rejeté."
                                    + self._ser_note(n_ser))
            return
        if QMessageBox.question(
                self, APP_NAME,
                f"Déplacer {len(rej)} fichier(s) rejeté(s) dans un "
                f"sous-dossier « {REJECT_DIR} » placé à côté de chaque fichier ?"
                + self._ser_note(n_ser)
        ) != QMessageBox.StandardButton.Yes:
            return
        done, errors = 0, []
        for it in rej:
            src = it.data(0, ROLE_SRC)[0]
            dst_dir = os.path.join(os.path.dirname(src), REJECT_DIR)
            try:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, unique_path(
                    os.path.join(dst_dir, os.path.basename(src))))
                self.forget(it)
                done += 1
            except OSError as e:
                errors.append(f"{os.path.basename(src)} : {e}")
        self.report(f"{done} fichier(s) déplacé(s).", errors)

    def delete_rejected(self):
        rej, n_ser = self.rejected_fits_items()
        if not rej:
            QMessageBox.information(self, APP_NAME, "Aucun fichier rejeté."
                                    + self._ser_note(n_ser))
            return
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                          f"Supprimer définitivement {len(rej)} fichier(s) "
                          f"du disque ?\nCette action est irréversible."
                          + self._ser_note(n_ser),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.exec()
        if box.standardButton(box.clickedButton()) != \
                QMessageBox.StandardButton.Yes:
            return
        done, errors = 0, []
        for it in rej:
            path = it.data(0, ROLE_SRC)[0]
            try:
                os.remove(path)
                self.forget(it)
                done += 1
            except OSError as e:
                errors.append(f"{os.path.basename(path)} : {e}")
        self.report(f"{done} fichier(s) supprimé(s).", errors)

    def forget(self, item):
        src = item.data(0, ROLE_SRC)
        for k in [k for k in self.cache if k[0] == src]:
            del self.cache[k]
        self.measures.pop(src, None)
        self.scores.pop(src, None)
        self.list.takeItem(self.list.row(item))

    def report(self, msg, errors):
        self.update_count()
        if errors:
            QMessageBox.warning(self, APP_NAME,
                                msg + "\n\nErreurs :\n" + "\n".join(errors[:20]))
        else:
            self.statusBar().showMessage(msg, 6000)

    # --- export --------------------------------------------------------------

    def chosen_sources(self, selected_only):
        if selected_only:
            items = sorted(self.list.selectedItems(), key=self.list.row)
            if not items:
                QMessageBox.information(
                    self, APP_NAME,
                    "Aucune image sélectionnée.\nSélectionnez des images avec "
                    "Ctrl+clic ou Maj+clic dans la liste.")
                return None
        else:
            items = [it for it in self.all_items() if not it.data(0, ROLE_REJ)]
            if not items:
                QMessageBox.information(self, APP_NAME,
                                        "Aucune image gardée dans la liste.")
                return None
        return [it.data(0, ROLE_SRC) for it in items]

    def run_with_progress(self, title, srcs, work):
        """Exécute work(progress) avec une barre de progression annulable.
        Renvoie le résultat de work, ou None en cas d'annulation ou d'erreur."""
        self.play_action.setChecked(False)
        prog = QProgressDialog(title, "Annuler", 0, len(srcs), self)
        prog.setWindowTitle(APP_NAME)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)

        def progress(i, src):
            prog.setValue(i)
            prog.setLabelText(f"{title}\nImage {i + 1} / {len(srcs)} : "
                              f"{src_name(src)}")
            QApplication.processEvents()
            return not prog.wasCanceled()

        result, error, cancelled = None, None, False
        try:
            result = work(progress)
        except Cancelled:
            cancelled = True
        except Exception as e:   # noqa: BLE001
            error = e
        finally:
            prog.setValue(len(srcs))
            prog.close()

        if cancelled:
            self.statusBar().showMessage(f"{title} : annulé", 5000)
        elif error is not None:
            QMessageBox.warning(self, APP_NAME, f"{title} : échec.\n{error}")
        return result

    def export_ser(self, selected_only):
        srcs = self.chosen_sources(selected_only)
        if not srcs:
            return
        first_path, first_frame = srcs[0]
        folder = os.path.dirname(first_path)
        if first_frame >= 0:
            stem = os.path.splitext(os.path.basename(first_path))[0]
            default = os.path.join(folder, f"{stem}_selection.ser")
        else:
            default = os.path.join(folder, os.path.basename(folder) + ".ser")
        out, _ = QFileDialog.getSaveFileName(
            self, f"Créer un SER ({len(srcs)} images)", default,
            "Vidéo SER (*.ser)")
        if not out:
            return
        if not out.lower().endswith(".ser"):
            out += ".ser"
        if os.path.abspath(out) in {os.path.abspath(p) for p, _ in srcs}:
            QMessageBox.warning(self, APP_NAME,
                                "Le SER ne peut pas remplacer une vidéo dont "
                                "il reprend les images. Choisissez un autre nom.")
            return

        res = self.run_with_progress(
            "Création du SER", srcs,
            lambda progress: write_ser(out, srcs, progress))
        if res:
            n, size = res
            QMessageBox.information(
                self, APP_NAME,
                f"{n} images enregistrées dans\n{out}\n({size / 1e6:.0f} Mo)")

    def export_folder(self):
        items = self.working_items()
        if not items:
            QMessageBox.information(self, APP_NAME, "La liste est vide.")
            return
        kept_items = [it for it in items if not it.data(0, ROLE_REJ)]
        kept = [it.data(0, ROLE_SRC) for it in kept_items]
        sel = [it.data(0, ROLE_SRC)
               for it in sorted(self.list.selectedItems(), key=self.list.row)]
        everything = [it.data(0, ROLE_SRC) for it in items]
        counts = {}
        for it in kept_items:
            counts[self.item_type(it)] = counts.get(self.item_type(it), 0) + 1
        kept_summary = ", ".join(type_count(t, counts[t])
                                 for t in FRAME_TYPES if t in counts)
        light_dirs = [os.path.dirname(it.data(0, ROLE_SRC)[0]) for it in items
                      if self.item_type(it) == "light"] or \
            [os.path.dirname(everything[0][0])]
        base = max(set(light_dirs), key=light_dirs.count)
        default = os.path.join(base, "traitement")
        dlg = ExportDialog(
            self, len(kept), len(sel), default,
            has_raw=any(is_raw(p) for p, f in everything if f < 0),
            has_ser=any(f >= 0 for _, f in everything),
            has_measures=bool(self.measures),
            has_ref=self.ref is not None, kept_summary=kept_summary)
        if not dlg.exec():
            return
        o = dlg.options()
        srcs = sel if o["selected_only"] else kept
        if not srcs or not o["dest"]:
            if not srcs:
                QMessageBox.information(self, APP_NAME,
                                        "Aucune image à exporter.")
            return
        root = os.path.abspath(o["dest"])
        siril = o["lights"]

        # répartition par type
        groups = OrderedDict((t, []) for t in FRAME_TYPES)
        metas = []
        for src in srcs:
            it = self.find_item(src)
            meta = (it.data(0, ROLE_META) if it is not None else None) or \
                {"type": "light"}
            groups[meta["type"]].append(src)
            metas.append(meta)
        groups = OrderedDict((t, v) for t, v in groups.items() if v)

        def out_dir_for(t):
            return os.path.join(root, SIRIL_DIRS[t]) if siril else root

        sources = {os.path.dirname(os.path.abspath(p)) for p, _ in srcs}
        if any(os.path.abspath(out_dir_for(t)) in sources for t in groups):
            QMessageBox.warning(self, APP_NAME,
                                "Choisissez un dossier différent de celui "
                                "des images d'origine.")
            return
        busy = [out_dir_for(t) for t in groups
                if os.path.isdir(out_dir_for(t)) and os.listdir(out_dir_for(t))]
        if busy:
            if QMessageBox.question(
                    self, APP_NAME,
                    "Ces dossiers contiennent déjà des fichiers :\n"
                    + "\n".join(sorted(set(busy))) +
                    "\nAjouter les images à côté ? (rien ne sera écrasé)") \
                    != QMessageBox.StandardButton.Yes:
                return

        need = export_size(srcs, o["link"], o["raw_to_fits"])
        try:
            probe = root
            while not os.path.exists(probe):
                probe = os.path.dirname(probe)
            free = shutil.disk_usage(probe).free
        except OSError:
            free = None
        if free is not None and need > free * 0.98:
            QMessageBox.warning(
                self, APP_NAME,
                f"Place insuffisante : il faut environ {need / 1e9:.1f} Go, "
                f"{free / 1e9:.1f} Go sont libres.\nEssayez l'option "
                "« Créer des liens », qui ne prend pas de place.")
            return

        ordered = []
        for t, group in groups.items():
            numbered = o["numbered"] and t == "light"
            ordered.append((t, export_order(group, self.ref, numbered),
                            numbered))
        flat_list = [src for _, g, _ in ordered for src in g]

        def work(progress):
            done, created = 0, {}
            for t, group, numbered in ordered:
                def prog(i, src, done=done):
                    return progress(done + i, src)
                created[t] = export_files(
                    out_dir_for(t), group, prog, link=o["link"],
                    raw_to_fits=o["raw_to_fits"], numbered=numbered,
                    prefix="light")
                done += len(group)
            return created

        created = self.run_with_progress("Export des images", flat_list, work)
        if not created:
            return
        if o["csv"]:
            self.write_csv(os.path.join(root, "mesures.csv"))
        weight_note = ""
        if o.get("weight") and created.get("light"):
            group = next(g for t, g, _ in ordered if t == "light")
            n_w = sum(write_weight(dst, self.scores[src] / 100.0)
                      for src, dst in zip(group, created["light"])
                      if self.scores.get(src) is not None)
            weight_note = (f"Poids {WEIGHT_KEY} écrit dans {n_w} brute(s) "
                           f"sur {len(group)}.")

        script_note = ""
        if o["script"]:
            lights_meta = [m for m in metas if m["type"] == "light"]
            filters = {m.get("filter") for m in lights_meta if m.get("filter")}
            if "light" not in groups:
                script_note = "Pas de script : aucune brute exportée."
            elif len(filters) > 1:
                script_note = ("Pas de script : les brutes ont plusieurs "
                               "filtres, à traiter séparément.")
            else:
                try:
                    with open(os.path.join(root, "traitement.ssf"), "w",
                              encoding="ascii", newline="\n") as f:
                        f.write(siril_script(set(groups), is_cfa(lights_meta)))
                    script_note = "Script Siril : traitement.ssf"
                except OSError as e:
                    script_note = f"Script non écrit : {e}"

        n = sum(len(v) for v in created.values())
        ref_export = None
        for t, group, _numbered in ordered:
            if t == "light" and self.ref in group and created.get("light"):
                ref_export = created["light"][group.index(self.ref)]
        report, warn = self.calibration_report(metas)
        self.write_summary(root, created, report, ref_export, o)

        lines = [f"{n} image(s) exportée(s) dans\n{root}"]
        if siril:
            lines.append(" · ".join(
                f"{SIRIL_DIRS[t]} : {len(v)}" for t, v in created.items()))
        if ref_export:
            lines.append(f"\nImage de référence : "
                         f"{os.path.basename(ref_export)}"
                         + (f" ({src_name(self.ref)})"
                            if o["numbered"] else ""))
        if warn or len(groups) > 1:
            lines.append("\n" + report.split("\n", 2)[-1])
        if script_note:
            lines.append("\n" + script_note)
        if weight_note:
            lines.append("\n" + weight_note + " Dans ImageIntegration : "
                         f"Weights = FITS keyword, Weight keyword = "
                         f"{WEIGHT_KEY}.")
        if self.active_series is not None:
            lines.append("\nBrutes de la série : "
                         + self.series_label(self.active_series))
        if o["target"] == "pixinsight":
            lines.append("\nPixInsight : dans WeightedBatchPreprocessing, "
                         "« Add Directory » sur ce dossier ; WBPP classe "
                         "lui-même brutes et calibrations. Pour fixer la "
                         "référence d'alignement, choisissez l'image indiquée "
                         "ci-dessus (Registration Reference Image : manual).")
        elif siril and script_note.startswith("Script Siril") and not o["run"]:
            lines.append("\nSiril : choisissez ce dossier comme répertoire de "
                         "travail, puis tapez @traitement.ssf dans la ligne de "
                         "commande. Ou cochez « Lancer Siril tout de suite » "
                         "au prochain export.")
        lines.append("\nRésumé de la nuit : " + SUMMARY_NAME)

        if o["run"] and script_note.startswith("Script Siril"):
            self.statusBar().showMessage(
                f"{n} image(s) exportée(s) ; Siril démarre…", 8000)
            if warn:
                QMessageBox.warning(self, APP_NAME, "\n".join(lines))
            self.run_siril(root, os.path.join(root, "traitement.ssf"),
                           o["narrow"])
            return

        box = QMessageBox(QMessageBox.Icon.Warning if warn else
                          QMessageBox.Icon.Information, APP_NAME, "\n".join(lines),
                          QMessageBox.StandardButton.Ok, self)
        open_btn = box.addButton("Ouvrir le dossier", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    # --- Siril ---------------------------------------------------------------

    def run_siril(self, workdir, script, narrow="rgb"):
        if not find_siril():
            QMessageBox.warning(
                self, APP_NAME, "siril-cli est introuvable. Installez Siril "
                "(paquet de la distribution ou Flatpak org.siril.Siril).")
            return
        dlg = SirilRunDialog(self, workdir, script)
        dlg.resultsReady.connect(lambda paths: self.open_results(paths, narrow))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
        self._siril_dialog = dlg

    def run_siril_dialog(self):
        script, _ = QFileDialog.getOpenFileName(
            self, "Lancer un script Siril", "", "Scripts Siril (*.ssf)")
        if script:
            self.run_siril(os.path.dirname(script), script)

    def open_results(self, paths, narrow="rgb"):
        if narrow in ("hoo", "sho"):
            palettes = ("hoo", "sho") if narrow == "sho" else ("hoo",)
            extra = []
            for p in paths:
                try:
                    extra += narrowband_compose(
                        p, palettes, float(settings().value(
                            "compose/green_ha", 0.5)))
                except Exception as e:   # noqa: BLE001
                    QMessageBox.warning(self, APP_NAME,
                                        f"Composition impossible pour "
                                        f"{os.path.basename(p)} :\n{e}")
            paths = list(paths) + extra
        self.add_paths(paths, force_type="master")
        self.show_type_tab("master")
        item = self.find_item((os.path.abspath(paths[-1] if narrow != "rgb"
                                               else paths[0]), -1))
        if item is not None:
            if self.corners_action.isChecked():
                self.corners_action.setChecked(False)
            self.list.setCurrentRow(self.list.row(item))
            self.list.setFocus()

    def compose_dialog(self):
        """Composition HOO / SHO synthétique d'une image couleur empilée."""
        src = self.current_src
        start = src[0] if src and src[1] < 0 else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Image empilée en couleur (filtre multibande)",
            start, "Images FITS (*.fit *.fits *.fts)")
        if not path:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Composer en bande étroite")
        form = QFormLayout(dlg)
        intro = QLabel(
            "Pour une caméra couleur avec un filtre bi, tri ou quadri-bande : "
            "Ha = canal rouge, OIII = moyenne du vert et du bleu, ramené au "
            "niveau du Ha. Le SII ne peut pas être séparé du Ha avec un "
            "capteur couleur : le SHO obtenu est synthétique.")
        intro.setWordWrap(True)
        form.addRow(intro)
        cb_hoo = QCheckBox("HOO")
        cb_hoo.setChecked(True)
        cb_sho = QCheckBox("SHO synthétique")
        cb_sho.setChecked(True)
        form.addRow(cb_hoo)
        form.addRow(cb_sho)
        green = QDoubleSpinBox()
        green.setRange(0, 100)
        green.setDecimals(0)
        green.setSingleStep(5)
        green.setSuffix(" % de Ha")
        green.setValue(100 * float(settings().value("compose/green_ha", 0.5)))
        form.addRow("Vert du SHO", green)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if not dlg.exec():
            return
        settings().setValue("compose/green_ha", green.value() / 100.0)
        palettes = tuple(k for k, cb in (("hoo", cb_hoo), ("sho", cb_sho))
                         if cb.isChecked())
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            written = narrowband_compose(path, palettes, green.value() / 100.0)
        except Exception as e:   # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_NAME, f"Composition impossible :\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.add_paths(written, force_type="master")
        self.show_type_tab("master")
        self.goto_src((os.path.abspath(written[-1]), -1))
        self.statusBar().showMessage(
            f"{len(written)} image(s) créée(s) à côté de "
            f"{os.path.basename(path)}", 8000)

    def write_summary(self, root, created, report, ref_export, o):
        """Résumé de la nuit, à garder avec les données."""
        lines = ["AstroCheck — résumé de la nuit",
                 "=" * 40,
                 f"Export du {datetime.now():%d/%m/%Y à %H:%M}",
                 f"Dossier : {root}",
                 f"Logiciel prévu : "
                 f"{'Siril' if o['target'] == 'siril' else 'PixInsight'}",
                 "",
                 report.replace("Images gardées", "Images exportées"),
                 ""]
        if self.ref is not None:
            lines.append("Image de référence (choisie à la main) :"
                         if self.ref == self.ref_manual else
                         "Image de référence (meilleur score) :")
            lines.append(f"  {self.seq_label(self.ref)}{self.measure_text(self.ref)}")
            if ref_export:
                lines.append(f"  exportée sous : "
                             f"{os.path.relpath(ref_export, root)}")
            lines.append("")
        verdict, detail = self.series_field() if self.measures else ("", "")
        if verdict:
            lines += [f"Champ (médiane de la série) : {verdict}",
                      f"  {detail}", ""]
        geo = self.geometry_text()
        if geo:
            lines += geo + [""]
        lines += ["Bilan de la séance", "-" * 18, self.session_report(), ""]
        rejected = [it for it in self.all_items() if it.data(0, ROLE_REJ)]
        if self.measures:
            c = self.criteria
            lines.append(
                f"Critères de présélection : FWHM ≤ {fr(c['fwhm'], 2)} × "
                f"médiane ; étoiles, excentricité et fond à moins de "
                f"{fr(c['stars'], 1)} / {fr(c['ecc'], 1)} / {fr(c['bg'], 1)} "
                "× dispersion")
        lines.append(f"Images écartées : {len(rejected)}")
        for it in rejected:
            why = " ; ".join(it.data(0, ROLE_REASONS) or []) or "à la main"
            if not it.data(0, ROLE_AUTO):
                why = "à la main"
            lines.append(f"  - {it.text(COL_NAME)} : {why}")
        try:
            with open(os.path.join(root, SUMMARY_NAME), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    # --- analyse et classement ----------------------------------------------

    def analyze(self, srcs=None):
        """Mesure les images pas encore mesurées, puis classe et
        présélectionne."""
        if self.analysis_todo:
            return
        if srcs is None:
            srcs = [it.data(0, ROLE_SRC) for it in self.working_items()
                    if self.item_type(it) == "light"]
            if not srcs and self.list.count():
                QMessageBox.information(
                    self, APP_NAME, "Aucune image brute dans la liste : "
                    "l'analyse ne porte que sur les brutes, pas sur les "
                    "darks, flats et offsets.")
                return
        srcs = [s for s in srcs if s not in self.measures]
        if not srcs:
            if self.list.count():
                self.finish_analysis(cancelled=False)
            return
        self.play_action.setChecked(False)
        self.list.setSortingEnabled(False)      # pas de re-tri à chaque mesure
        self.analysis_todo, self.analysis_done = len(srcs), 0
        self.analysis_errors = []
        prog = QProgressDialog("Analyse des images…", "Arrêter", 0,
                               len(srcs), self)
        prog.setWindowTitle(APP_NAME)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.canceled.connect(self.cancel_analysis)
        prog.setValue(0)
        self.analysis_prog = prog
        for s in srcs:
            self.analysis_pool.start(AnalyzeJob(s, self.asignals))

    def cancel_analysis(self):
        self.analysis_pool.clear()       # les mesures en cours se terminent
        self.analysis_todo = self.analysis_done = 0
        self.close_progress()
        self.finish_analysis(cancelled=True)

    def close_progress(self):
        if self.analysis_prog is not None:
            prog, self.analysis_prog = self.analysis_prog, None
            prog.canceled.disconnect(self.cancel_analysis)
            prog.close()
            prog.deleteLater()

    def on_measured(self, src, m, error):
        self.measures[src] = m
        if error:
            self.analysis_errors.append(f"{src_name(src)} : {error}")
        item = self.find_item(src)
        if item is not None:
            self.update_item(item)
        if not self.analysis_todo:
            return                        # analyse arrêtée entre-temps
        self.analysis_done += 1
        if self.analysis_prog is not None:
            self.analysis_prog.setValue(self.analysis_done)
            self.analysis_prog.setLabelText(
                f"Analyse des images…\n{self.analysis_done} / "
                f"{self.analysis_todo} : {src_name(src)}")
        if self.analysis_done >= self.analysis_todo:
            self.analysis_todo = 0
            self.close_progress()
            self.finish_analysis(cancelled=False)

    @staticmethod
    def item_type(item):
        return (item.data(0, ROLE_META) or {}).get("type", "light")

    def find_item(self, src):
        it = self._item_index.get(src)
        if it is not None and self.list.row(it) >= 0:
            return it
        self._item_index = {i.data(0, ROLE_SRC): i for i in self.all_items()}
        return self._item_index.get(src)

    def finish_analysis(self, cancelled):
        self.update_scores()
        crit_msg = self.apply_preselection()
        self.list.setSortingEnabled(True)
        self.list.sortByColumn(COL_SCORE, Qt.SortOrder.DescendingOrder)
        self.show_type_tab("light")
        self.goto_reference()
        self.refresh_graph()
        if self.measures:
            self.graph_dock.show()
        if cancelled:
            self.statusBar().showMessage(
                "Analyse arrêtée : classement des images déjà mesurées", 8000)
            return
        ref = self.reference_src()
        lines = []
        if ref is not None:
            lines.append(f"Image de référence : {self.seq_label(ref)}"
                         f"{self.measure_text(ref)}")
        lines.append(crit_msg)
        cost = self.cost_text()
        if cost:
            lines.append(cost)
        n_tr = sum(1 for r in self.session_rows() if r["trail"])
        if n_tr:
            lines.append(f"Traînées (satellites, avions) repérées sur "
                         f"{n_tr} image(s) : colonne Raison.")
        geo = self.geometry_text(short=True)
        if geo:
            lines.append("\n" + "\n".join(geo)
                         + "\nDétails : Analyse ▸ Rotation de champ et zone "
                           "utile (Ctrl+U), zone sur l'image : touche Z.")
        verdict, detail = self.series_field()
        if verdict:
            lines.append(f"\nChamp (médiane de la série) : {verdict}.\n"
                         f"{detail}.\nVue des coins : touche C.")
        lines.append("\nLes images présélectionnées sont en orange barré. "
                     "Vérifiez-les : Espace pour en garder une.\n"
                     "Critères réglables dans Analyse ▸ Critères de "
                     "présélection (Ctrl+P).")
        if self.analysis_errors:
            lines.append("\nImages illisibles :\n"
                         + "\n".join(self.analysis_errors[:10]))
        QMessageBox.information(self, APP_NAME, "\n".join(lines))

    def update_scores(self):
        present = {it.data(0, ROLE_SRC) for it in self.working_items()}
        ms = {k: m for k, m in self.measures.items() if k in present}
        self.scores = compute_scores(ms)
        self.planet = series_mode(ms) == "planet"
        self.ref = self.reference_src()
        sharp = [m["sharp"] for m in ms.values() if m]
        self.sharp_best = max(sharp) if sharp else 1.0
        # colonnes utiles selon le type de série
        for c in (COL_FWHM, COL_ECC, COL_STARS):
            self.list.setColumnHidden(c, self.planet and bool(ms))
        self.list.setColumnHidden(COL_SHARP, not self.planet)
        self.update_geometry()
        sorting = self.list.isSortingEnabled()
        self.list.setSortingEnabled(False)
        for it in self.all_items():
            self.update_item(it)
        self.list.setSortingEnabled(sorting)

    def reference_src(self):
        if not self.scores:
            return None
        if (self.ref_manual is not None and self.ref_manual in self.scores
                and self.find_item(self.ref_manual) is not None):
            return self.ref_manual
        # à égalité, la première dans l'ordre de prise de vue
        return max(sorted(self.scores, key=lambda k: (k[0], k[1])),
                   key=self.scores.get)

    def goto_reference(self):
        ref = self.reference_src()
        item = self.find_item(ref) if ref is not None else None
        if item is not None and item.isHidden():
            self.show_type_tab("light")
        if item is not None:
            self.list.setCurrentRow(self.list.row(item))
            self.list.setFocus()

    def update_item(self, item):
        """Remplit les colonnes de mesure d'une ligne."""
        src = item.data(0, ROLE_SRC)
        m = self.measures.get(src)
        score = self.scores.get(src)
        ref = src == self.ref

        def put(col, text, value):
            item.setText(col, text)
            item.setData(col, ROLE_SORT, value)

        if src in self.measures and m is None:
            put(COL_SCORE, "erreur", None)
        else:
            put(COL_SCORE, "" if score is None else
                (f"★ {score:.0f}" if ref else f"{score:.0f}"), score)
        tr = self.transforms.get(src)
        if tr is not None:
            ang = self.display_angle(tr["angle"])
            put(COL_ROT, fr(ang, 2), ang)
        else:
            put(COL_ROT, "—" if src in self.transforms else "", None)
        if m:
            f, e = m["fwhm"], m["ecc"]
            put(COL_FWHM, fr(f, 2) if f else "—", f)
            put(COL_ECC, fr(e, 2) if e is not None else "—", e)
            put(COL_STARS, str(m["stars"]), m["stars"])
            put(COL_BG, f"{m['bg']:.0f}", m["bg"])
            sh = 100.0 * m["sharp"] / max(getattr(self, "sharp_best", 1.0)
                                          or 1.0, 1e-12)
            put(COL_SHARP, f"{sh:.0f}" if self.scores else "", m["sharp"])
        else:
            for c in (COL_FWHM, COL_ECC, COL_STARS, COL_BG, COL_SHARP):
                put(c, "", None)
        font = item.font(0)
        font.setBold(ref)
        item.setFont(0, font)

        tip = src[0] if src[1] < 0 else f"{src[0]}\nimage {src[1] + 1}"
        if ref:
            tip += ("\n★ Image de référence (choisie à la main)"
                    if src == self.ref_manual else
                    "\n★ Image de référence (meilleur score)")
        if m and m.get("trail"):
            tip += (f"\nTraînée probable (satellite, avion) : "
                    f"{m['trail']['length']:.0f} px")
        meta = item.data(0, ROLE_META) or {}
        if meta.get("temp") is not None:
            tip += f"\nCapteur : {fr(meta['temp'], 1)} °C"
        if tr is not None and not ref:
            tip += (f"\nRotation : {fr(self.display_angle(tr['angle']), 2)}°"
                    f" par rapport à la référence")
            rate = self.rot_rates.get(src)
            if rate is not None:
                tip += f" ({fr(self.display_angle(rate) * 60, 1)}°/h)"
        reasons = item.data(0, ROLE_REASONS)
        if reasons:
            tip += "\nPrésélection : " + " ; ".join(reasons)
        for c in range(len(COLUMNS)):
            item.setToolTip(c, tip)
        self.update_reason(item)

    def measure_text(self, src):
        m = self.measures.get(src)
        if not m:
            return ""
        parts = []
        sc = self.scores.get(src)
        if sc is not None:
            parts.append(f"score {sc:.0f}")
        if self.planet:
            best = getattr(self, "sharp_best", 0) or 1.0
            parts.append(f"netteté {100 * m['sharp'] / best:.0f} %")
            return "   |   " + "   ".join(parts)
        if m["fwhm"]:
            t = f"FWHM {fr(m['fwhm'], 2)} px"
            if m["arcsec"]:
                t += f" ({fr(m['fwhm'] * m['arcsec'], 1)}\")"
            parts.append(t)
            parts.append(f"excentricité {fr(m['ecc'], 2)}")
        parts.append(f"{m['stars']} étoiles")
        if m.get("trail"):
            parts.append("traînée")
        return "   |   " + "   ".join(parts)

    def apply_preselection(self):
        """Rejette les mauvaises images selon les critères. Les images
        tranchées à la main ne sont jamais modifiées. Renvoie un résumé."""
        present = {it.data(0, ROLE_SRC) for it in self.working_items()}
        ms = {k: m for k, m in self.measures.items() if k in present}
        bad = preselect(ms, self.criteria)
        n_auto, causes = 0, {}
        sorting = self.list.isSortingEnabled()
        self.list.setSortingEnabled(False)
        for it in self.working_items():
            src = it.data(0, ROLE_SRC)
            reasons = bad.get(src)
            it.setData(0, ROLE_REASONS, reasons)
            if not it.data(0, ROLE_MANUAL):
                if reasons:
                    self.set_rejected(it, True, auto=True)
                    n_auto += 1
                    for r in reasons:
                        key = reason_kind(r)
                        causes[key] = causes.get(key, 0) + 1
                elif it.data(0, ROLE_AUTO):
                    self.set_rejected(it, False)
            self.update_item(it)
        self.list.setSortingEnabled(sorting)
        self.update_count()
        n = len(ms)
        if not n:
            return "Aucune image mesurée."
        mode = ("Série planétaire : classement par netteté."
                if series_mode(ms) == "planet" else "")
        msg = (f"Présélection : {n_auto} image(s) sur {n} écartée(s)"
               + (f" ({100 * n_auto / n:.0f} %)." if n else "."))
        if causes:
            labels = {"fwhm": "étoiles trop larges (flou, mise au point, "
                      "turbulence)", "ecc": "étoiles allongées (suivi, vent)",
                      "stars": "trop peu d'étoiles (nuages, buée)",
                      "bg": "fond de ciel élevé (voile, Lune, aube)",
                      "none": "aucune étoile mesurable",
                      "sharp": "netteté insuffisante"}
            msg += "\n" + "\n".join(
                f"  • {labels.get(k, k)} : {v}" for k, v in causes.items())
        if n and n_auto > n / 2:
            msg += ("\n\nAttention : plus de la moitié des images sont "
                    "écartées. Les critères sont peut-être trop stricts "
                    "pour cette série.")
        return (mode + "\n" if mode else "") + msg

    def clear_preselection(self):
        sorting = self.list.isSortingEnabled()
        self.list.setSortingEnabled(False)
        for it in self.all_items():
            it.setData(0, ROLE_REASONS, None)
            if it.data(0, ROLE_AUTO) and it.data(0, ROLE_REJ):
                self.set_rejected(it, False)
            self.update_item(it)
        self.list.setSortingEnabled(sorting)
        self.update_count()

    def preselect_dialog(self):
        present = {it.data(0, ROLE_SRC) for it in self.all_items()}
        ms = {k: m for k, m in self.measures.items() if k in present}
        if not ms:
            if QMessageBox.question(
                    self, APP_NAME, "Les images n'ont pas encore été "
                    "analysées. Lancer l'analyse maintenant ?") == \
                    QMessageBox.StandardButton.Yes:
                self.analyze()
            return
        dlg = PreselectDialog(self, self.criteria, ms)
        if dlg.exec():
            self.criteria = dlg.values()
            msg = self.apply_preselection()
            self.statusBar().showMessage(msg.replace("\n", " "), 10000)

    def save_csv(self):
        if not self.measures:
            QMessageBox.information(self, APP_NAME,
                                    "Analysez d'abord les images (Ctrl+R).")
            return
        first = self.list.item(0).data(0, ROLE_SRC)[0] if self.list.count() \
            else os.path.expanduser("~")
        out, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer les mesures",
            os.path.join(os.path.dirname(first), "mesures.csv"),
            "Tableau CSV (*.csv)")
        if not out:
            return
        if self.write_csv(out):
            self.statusBar().showMessage(f"Mesures enregistrées dans {out}",
                                         6000)

    def write_csv(self, out):
        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["rang", "image", "fichier", "numero", "score",
                            "fwhm_px", "fwhm_arcsec", "excentricite",
                            "etoiles", "fond", "bruit", "nettete", "statut",
                            "raisons"])
                for i, it in enumerate(self.all_items(), 1):
                    path, frame = it.data(0, ROLE_SRC)
                    m = self.measures.get((path, frame)) or {}
                    sc = self.scores.get((path, frame))

                    def num(v, nd=3):
                        return "" if v is None else fr(v, nd)
                    arc = (m.get("fwhm") * m["arcsec"]
                           if m.get("fwhm") and m.get("arcsec") else None)
                    w.writerow([
                        i, src_name((path, frame)), path,
                        it.data(COL_NUM, ROLE_SORT) or "", num(sc, 1),
                        num(m.get("fwhm")), num(arc, 2), num(m.get("ecc")),
                        m.get("stars", ""), num(m.get("bg"), 1),
                        num(m.get("noise"), 2), num(m.get("sharp"), 6),
                        "rejetée" if it.data(0, ROLE_REJ) else "gardée",
                        " ; ".join(it.data(0, ROLE_REASONS) or [])])
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Enregistrement impossible :"
                                f"\n{e}")
            return False
        return True

    # --- lecture vidéo -------------------------------------------------------

    def set_playing(self, on):
        if on and self.list.count() == 0:
            self.play_action.setChecked(False)
            return
        self.play_action.setText("⏸ Pause" if on else "▶ Lire")
        if on:
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(0)
            self.update_play_speed()
        else:
            self.play_timer.stop()

    def update_play_speed(self, *_):
        if self.play_action.isChecked():
            self.play_timer.start(max(1, int(1000 / self.fps_spin.value())))

    def next_play_row(self, row):
        """Image suivante pour la lecture : saute les rejetées et boucle.
        Pour un SER, la lecture reste dans la même vidéo."""
        n = self.list.count()
        if n == 0:
            return None
        if row < 0:
            return 0
        path, frame = self.list.item(row).data(0, ROLE_SRC)
        if frame >= 0:
            def in_group(r):
                return self.list.item(r).data(0, ROLE_SRC)[0] == path
        else:
            def in_group(_r):
                return True
        r = row
        for _ in range(n):
            r += 1
            if r >= n or not in_group(r):          # retour au début
                r = row
                while r > 0 and in_group(r - 1):
                    r -= 1
            it = self.list.item(r)
            if not it.data(0, ROLE_REJ) and not it.isHidden():
                return r
        return None

    def play_step(self):
        if (self.current_src is not None
                and self.shown_key != self.make_key(self.current_src)):
            return                     # image courante pas encore prête
        nxt = self.next_play_row(self.list.currentRow())
        if nxt is None:
            self.play_action.setChecked(False)
        elif nxt != self.list.currentRow():
            self.list.setCurrentRow(nxt)

    # --- affichage -----------------------------------------------------------

    def make_key(self, src):
        return (src, self.stretch_combo.currentText(),
                self.debayer_cb.isChecked())

    def submit(self, src, priority=0):
        key = self.make_key(src)
        if key in self.cache or key in self.pending:
            return
        self.pending.add(key)
        self.pool.start(LoadJob(key, STRETCH_MODES[key[1]], self.signals),
                        priority)

    def on_current_changed(self, cur, _prev):
        if cur is None:
            self.current_src = None
            self.view.clear()
            self.header_view.clear()
            return
        self.show_src(cur.data(0, ROLE_SRC))

    def show_src(self, src):
        self.current_src = src
        self.graph.set_current(src)
        key = self.make_key(src)
        if key in self.cache:
            self.cache.move_to_end(key)
            self.display(key, self.cache[key])
        else:
            if not self.play_action.isChecked():
                self.statusBar().showMessage(f"Chargement de {src_name(src)}…")
            self.submit(src, priority=10)
        self.prefetch()

    def prefetch(self):
        row = self.list.currentRow()
        if row < 0:
            return
        if self.play_action.isChecked():
            rows, r = [], row
            for _ in range(4):
                r = self.next_play_row(r)
                if r is None or r == row or r in rows:
                    break
                rows.append(r)
        else:
            rows = [r for r in (self.visible_row(row, +1),
                                self.visible_row(row, -1)) if r is not None]
            nxt = self.visible_row(rows[0], +1) if rows and rows[0] > row \
                else None
            if nxt is not None:
                rows.append(nxt)
        for r in rows:
            if 0 <= r < self.list.count():
                self.submit(self.list.item(r).data(0, ROLE_SRC))

    def on_loaded(self, key, result, error):
        self.pending.discard(key)
        is_current = (self.current_src is not None
                      and key == self.make_key(self.current_src))
        if error:
            if is_current:
                self.shown_key = key   # la lecture vidéo ne reste pas bloquée
                self.view.clear()
                self.header_view.setPlainText(f"Lecture impossible :\n{error}")
                self.statusBar().showMessage(
                    f"{src_name(key[0])} : lecture impossible ({error})")
            return
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_MAX:
            self.cache.popitem(last=False)
        if is_current:
            self.display(key, result)

    def display(self, key, result):
        img, tile = result["image"], None
        if self.corners_action.isChecked():
            img, tile = corner_mosaic(img)
        qimg = numpy_to_qimage(img)
        flip = self.flip_action.isChecked()
        if flip:
            qimg = qimg.mirrored(False, True)
        extra = self.measure_text(key[0])
        if not tile and self.zone_action.isChecked():
            qimg = self.zone_overlay(qimg, key[0])
            z = self.zone
            if z and z["rect"]:
                x0, y0, x1, y1 = z["rect"]
                extra += (f"   |   zone utile {int(x1 - x0)}×{int(y1 - y0)}"
                          f" px ({pct((x1 - x0) * (y1 - y0) / (z['w'] * z['h']))})")
            elif key[0] not in self.transforms:
                extra += "   |   zone utile : lancez l'analyse (Ctrl+R)"
        if tile:
            qimg, verdict = self.annotate_corners(qimg, tile, key[0])
            if verdict:
                extra += "   |   champ : " + verdict
        self.view.set_image(qimg)
        self.header_view.setPlainText(result["header"])
        self.statusBar().showMessage(result["info"] + extra)
        self.shown_key = key

    def display_grid(self, m):
        """Grille de mesures orientée comme l'image affichée."""
        if not m or not m.get("grid"):
            return None
        return orient_grid(m["grid"], m.get("rowflip", True)
                           != self.flip_action.isChecked())

    def annotate_corners(self, qimg, tile, src, gap=6):
        """Inscrit la FWHM de chaque zone sur la mosaïque des coins."""
        grid = self.display_grid(self.measures.get(src))
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB32)
        painter = QPainter(qimg)
        font = painter.font()
        texts = {}
        for r in range(3):
            for c in range(3):
                cell = grid[r][c] if grid else None
                text = ZONE_NAMES[r][c]
                if cell:
                    text += f"  ·  FWHM {fr(cell[0], 2)}  ·  e {fr(cell[1], 2)}"
                texts[r, c] = text
        # une seule taille pour toutes les vignettes, la plus grande qui tient
        size = max(12, tile // 14)
        while size > 9:
            font.setPixelSize(size)
            painter.setFont(font)
            widest = max(painter.fontMetrics().horizontalAdvance(t) for t in texts.values())
            if widest + 14 <= tile:
                break
            size -= 1
        fm = painter.fontMetrics()
        for (r, c), text in texts.items():
            x, y = c * (tile + gap), r * (tile + gap)
            w = min(tile, fm.horizontalAdvance(text) + 14)
            painter.fillRect(x, y, w, fm.height() + 8, QColor(0, 0, 0, 170))
            painter.setPen(QColor(255, 220, 120))
            painter.drawText(QRectF(x + 7, y + 4, w - 10, fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(text, Qt.TextElideMode.ElideRight, w - 10))
        painter.end()
        verdict = field_summary(grid)[0] if grid else ""
        if not grid:
            verdict = "lancez l'analyse (Ctrl+R) pour mesurer chaque zone"
        return qimg, verdict

    def toggle_corners(self):
        self.redisplay()
        self.view.fit()

    def goto_src(self, src):
        item = self.find_item(src)
        if item is not None and item.isHidden():
            self.show_type_tab(self.item_type(item))
        if item is not None:
            self.list.setCurrentRow(self.list.row(item))
            self.list.setFocus()

    def series_field(self):
        """Diagnostic du champ sur toute la série (grille médiane)."""
        grids = [self.display_grid(m) for k, m in self.measures.items()
                 if m and self.find_item(k) is not None]
        return field_summary(median_grid(grids))

    def refresh_graph(self):
        entries = []
        for it in self.working_items():
            src = it.data(0, ROLE_SRC)
            m = self.measures.get(src)
            if not m or self.item_type(it) != "light":
                continue
            state = "kept"
            if it.data(0, ROLE_REJ):
                state = "auto" if it.data(0, ROLE_AUTO) else "manual"
            date = (it.data(0, ROLE_META) or {}).get("date")
            entries.append((src, date, m, state, it.text(COL_NAME)))
        present = {e[0]: self.measures[e[0]] for e in entries}
        limits = preselect_limits(present, self.criteria) if present else {}
        rot = {s: self.display_angle(t["angle"])
               for s, t in self.transforms.items() if t} \
            if self.field_rotates() else {}
        alt = {e[0]: self.find_item(e[0]).data(COL_ALT, ROLE_SORT)
               for e in entries}
        alt = {k: v for k, v in alt.items() if v is not None}
        self.graph.set_data(entries, limits,
                            limits.get("mode") == "planet", rot, alt)
        self.graph.set_current(self.current_src)

    # --- rotation de champ et zone utile -------------------------------------

    def mirrored(self):
        """Vrai si l'image affichée est le miroir vertical du fichier."""
        m = self.measures.get(self.ref) or {}
        return bool(m.get("rowflip", True)) != self.flip_action.isChecked()

    def display_angle(self, angle):
        """Angle tel qu'on le voit à l'écran (positif = sens antihoraire)."""
        a = -angle if self.mirrored() else angle
        return 0.0 if abs(a) < 0.005 else a

    def light_order(self, srcs):
        """Sources triées dans l'ordre de prise de vue."""
        def key(s):
            it = self.find_item(s)
            d = (it.data(0, ROLE_META) or {}).get("date") if it else None
            return (d is None, d or datetime.min, s[0], s[1])
        return sorted(srcs, key=key)

    def update_geometry(self, force=False):
        """Aligne toutes les brutes mesurées sur la référence : rotation de
        champ de chaque image et vitesse de rotation."""
        stars = {}
        if not self.planet:
            for it in self.working_items():
                src = it.data(0, ROLE_SRC)
                m = self.measures.get(src)
                if (m and self.item_type(it) == "light"
                        and len(m.get("stars_xy", ())) >= 6):
                    stars[src] = m["stars_xy"]
        key = (self.ref, frozenset(stars))
        if not force and key == self._geom_key:
            return
        self._geom_key = key
        self.transforms, self.rot_rates = {}, {}
        self.zone, self.zone_dirty = None, True
        if self.ref in stars and len(stars) > 1:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                order = self.light_order(stars)
                found = align_series(stars, order, self.ref)
            finally:
                QApplication.restoreOverrideCursor()
            self.transforms = {s: found.get(s) for s in stars}
            entries = []
            for s, tr in found.items():
                it = self.find_item(s)
                d = (it.data(0, ROLE_META) or {}).get("date") if it else None
                entries.append((s, d, tr["angle"]))
            self.rot_rates = rotation_rates(entries)
        self.list.setColumnHidden(COL_ROT, not self.field_rotates())

    def field_rotates(self):
        """La colonne Rot. n'est utile que si le champ tourne vraiment."""
        if not self.transforms:
            return False
        if any((it.data(0, ROLE_META) or {}).get("eqmode") == 0
               for it in self.all_items()):
            return True
        angles = [abs(t["angle"]) for t in self.transforms.values() if t]
        return bool(angles) and max(angles) >= 0.1

    def compute_zone(self):
        """Zone couverte par toutes les brutes gardées après alignement."""
        if not self.zone_dirty:
            return self.zone
        self.zone_dirty = False
        self.zone = None
        m = self.measures.get(self.ref)
        if not m or not self.transforms or "shape" not in m:
            return None
        w, h = m["shape"]
        kept = {}
        for it in self.working_items():
            src = it.data(0, ROLE_SRC)
            if src in self.transforms and not it.data(0, ROLE_REJ):
                kept[src] = self.transforms[src]
        aligned = {s: t for s, t in kept.items() if t is not None}
        if not aligned:
            return None
        poly, rect, frac = usable_area(w, h, aligned)
        # les images qui rognent le plus : gain si on les retirait
        gains = []
        if rect and len(aligned) > 2:
            def reach(t):
                # distance du coin le plus éloigné de sa place d'origine
                c = frame_polygon(w, h, t) - frame_polygon(w, h)
                return float(np.hypot(c[:, 0], c[:, 1]).max())
            area = (rect[2] - rect[0]) * (rect[3] - rect[1])
            worst = sorted(aligned, key=lambda s: -reach(aligned[s]))[:8]
            for s in worst:
                rest = {k: v for k, v in aligned.items() if k != s}
                _p, r2, _f = usable_area(w, h, rest)
                if r2:
                    a2 = (r2[2] - r2[0]) * (r2[3] - r2[1])
                    if a2 > area * 1.005:
                        gains.append((s, a2 / area - 1.0))
            gains.sort(key=lambda g: -g[1])
        x0, y0, x1, y1 = rect if rect else (0, 0, 0, 0)
        if self.mirrored():
            top, bottom = h - y1, y0
        else:
            top, bottom = y0, h - y1
        self.zone = {"w": w, "h": h, "poly": poly, "rect": rect,
                     "frac": frac, "n": len(aligned),
                     "unaligned": [s for s, t in kept.items() if t is None],
                     "margins": (x0, w - x1, top, bottom), "gains": gains}
        return self.zone

    def mark_zone_dirty(self):
        self.zone_dirty = True
        if self.zone_action.isChecked():
            self.redisplay()

    def rotation_stats(self):
        """Amplitude, vitesse et filé dans les coins pour la série."""
        trs = {s: t for s, t in self.transforms.items() if t}
        if len(trs) < 2:
            return None
        angles = [t["angle"] for t in trs.values()]
        st = {"span": max(angles) - min(angles), "n": len(trs),
              "missing": sum(1 for t in self.transforms.values() if not t)}
        rates = {s: r for s, r in self.rot_rates.items() if s in trs}
        if rates:
            fast = max(rates, key=lambda s: abs(rates[s]))
            st["rate_med"] = float(np.median(np.abs(list(rates.values()))))
            st["rate_max"] = abs(rates[fast])
            it = self.find_item(fast)
            st["fast_date"] = ((it.data(0, ROLE_META) or {}).get("date")
                               if it else None)
            st["fast_src"] = fast
            m = self.measures.get(self.ref) or {}
            w, h = m.get("shape", (0, 0))
            halfdiag = 0.5 * float(np.hypot(w, h))
            exps = _vals([(self.find_item(s).data(0, ROLE_META) or {})
                          for s in trs if self.find_item(s)], "exptime")
            exp = float(np.median(exps)) if exps else None
            fw = _median([(self.measures.get(s) or {}).get("fwhm")
                          for s in trs])
            st["exptime"], st["fwhm"], st["halfdiag"] = exp, fw, halfdiag
            if exp:
                rad_s = np.radians(st["rate_max"]) / 60.0
                st["trail"] = rad_s * exp * halfdiag
                st["trail_med"] = np.radians(st["rate_med"]) / 60.0 * exp \
                    * halfdiag
                if fw and rad_s > 0:
                    st["t_max"] = 0.5 * fw / (rad_s * halfdiag)
        return st

    def geometry_text(self, short=False):
        """Résumé de la rotation de champ et de la zone utile."""
        st = self.rotation_stats()
        if not st:
            return []
        lines = []
        if self.field_rotates():
            lines.append(f"Rotation de champ : {fr(st['span'], 1)}° sur la "
                         f"série" + (f", jusqu'à {fr(st['rate_max'] * 60, 1)}"
                                     f"°/h" if "rate_max" in st else ""))
            if "trail" in st and not short:
                lines.append(f"  filé dans les coins pendant une pose : "
                             f"{fr(st['trail'], 1)} px au pire "
                             f"(FWHM {fr(st['fwhm'], 1)} px)")
        if st["missing"] and not short:
            lines.append(f"  {st['missing']} image(s) non alignée(s)")
        z = self.compute_zone()
        if z and z["rect"]:
            x0, y0, x1, y1 = z["rect"]
            part = (x1 - x0) * (y1 - y0) / (z["w"] * z["h"])
            if part < 0.995 or lines:
                lines.append(f"Zone utile après empilement : "
                             f"{int(x1 - x0)}×{int(y1 - y0)} px "
                             f"({pct(part)} du champ)")
        return lines

    def show_geometry_report(self):
        if not self.transforms:
            QMessageBox.information(
                self, APP_NAME,
                "Lancez d'abord l'analyse (Ctrl+R) : la rotation se mesure "
                "sur les étoiles des brutes.")
            return
        st = self.rotation_stats() or {}
        z = self.compute_zone()
        out = [f"Image de référence : {self.seq_label(self.ref)}"
               + (" (choisie à la main)" if self.ref == self.ref_manual
                  else ""), ""]
        alt = any((it.data(0, ROLE_META) or {}).get("eqmode") == 0
                  for it in self.all_items())
        out.append("ROTATION DE CHAMP")
        if alt:
            out.append("Monture alt-azimutale (EQMODE = 0) : le champ tourne "
                       "autour de l'axe optique pendant la séance.")
        if st:
            out.append(f"• Amplitude sur la série : {fr(st['span'], 2)}° "
                       f"({st['n']} images alignées)")
            if "rate_max" in st:
                when = (f" vers {st['fast_date']:%H:%M}"
                        if st.get("fast_date") else "")
                out.append(f"• Vitesse : {fr(st['rate_med'] * 60, 1)}°/h en "
                           f"médiane, {fr(st['rate_max'] * 60, 1)}°/h au "
                           f"maximum{when}")
            if "trail" in st:
                out.append(f"• Filé dans les coins pendant une pose de "
                           f"{fr(st['exptime'], 0)} s : "
                           f"{fr(st['trail_med'], 1)} px en médiane, "
                           f"{fr(st['trail'], 1)} px au pire, pour une FWHM "
                           f"de {fr(st['fwhm'], 2)} px")
                if st["trail"] > 0.5 * st["fwhm"]:
                    out.append("  ⚠ les étoiles des coins sont allongées "
                               "par la rotation pendant la pose")
                if st.get("t_max"):
                    out.append(f"• Pose maximale conseillée au moment le plus "
                               f"rapide : {fr(st['t_max'], 0)} s (filé "
                               f"inférieur à une demi-FWHM)")
            if st["missing"]:
                out.append(f"• {st['missing']} image(s) n'ont pas pu être "
                           "alignées (trop peu d'étoiles, nuages)")
        else:
            out.append("Pas assez d'images alignées pour mesurer la rotation.")
        out += ["", "ZONE UTILE APRÈS EMPILEMENT (images gardées)"]
        if z and z["rect"]:
            w, h = z["w"], z["h"]
            x0, y0, x1, y1 = z["rect"]
            l, r, t, b = z["margins"]
            out.append(f"• Zone couverte par toutes les images : "
                       f"{pct(z['frac'])} du champ")
            out.append(f"• Plus grand rectangle : {int(x1 - x0)}×"
                       f"{int(y1 - y0)} px sur {w}×{h} "
                       f"({pct((x1 - x0) * (y1 - y0) / (w * h))})")
            out.append(f"• À rogner : {int(l)} px à gauche, {int(r)} à "
                       f"droite, {int(t)} en haut, {int(b)} en bas")
            if z["gains"]:
                out.append("• Images qui réduisent le plus la zone :")
                for s, g in z["gains"][:5]:
                    out.append(f"    {src_name(s)} : +{pct(g)} de surface "
                               f"si on la rejette")
            out.append("La touche Z affiche la zone sur l'image.")
        else:
            out.append("Les images gardées n'ont pas de zone commune : "
                       "rejetez celles qui sont trop décalées.")
        dlg = QDialog(self)
        dlg.setWindowTitle("Rotation de champ et zone utile")
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit("\n".join(out))
        txt.setReadOnly(True)
        txt.setMinimumSize(620, 380)
        lay.addWidget(txt)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

    def zone_overlay(self, qimg, src):
        """Assombrit ce qui sera rogné et trace la zone utile."""
        z = self.compute_zone()
        tr = self.transforms.get(src)
        if not z or tr is None or len(z["poly"]) < 3:
            return qimg
        w, h = z["w"], z["h"]
        dw, dh = qimg.width(), qimg.height()
        sx, sy = dw / float(w), dh / float(h)
        mir = self.mirrored()

        def to_disp(pts):
            # repère de la référence -> repère de l'image affichée
            pts = _rot(np.asarray(pts, float) - [tr["dx"], tr["dy"]],
                       -tr["angle"])
            return QPolygonF([QPointF(x * sx, (h - y) * sy if mir else y * sy)
                              for x, y in pts])

        qimg = qimg.convertToFormat(QImage.Format.Format_RGB32)
        p = QPainter(qimg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        poly = to_disp(z["poly"])
        outside = QPainterPath()
        outside.addRect(QRectF(0, 0, dw, dh))
        inner = QPainterPath()
        inner.addPolygon(poly)
        p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 140))
        pen = QPen(QColor(255, 200, 80), max(1.0, dw / 900.0))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(poly)
        if z["rect"]:
            x0, y0, x1, y1 = z["rect"]
            pen = QPen(QColor(90, 220, 120), max(1.5, dw / 600.0))
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPolygon(to_disp([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
        p.end()
        return qimg

    # --- choix de la référence -------------------------------------------------

    def set_reference(self, src=None):
        if src is None:
            src = self.current_src
        it = self.find_item(src) if src is not None else None
        if it is None or self.item_type(it) != "light":
            QMessageBox.information(self, APP_NAME,
                                    "Choisissez une brute dans la liste.")
            return
        if src not in self.scores:
            QMessageBox.information(
                self, APP_NAME, "Cette image n'a pas été mesurée : lancez "
                "l'analyse (Ctrl+R) avant de la choisir comme référence.")
            return
        self.ref_manual = src
        self.reference_changed()
        self.statusBar().showMessage(
            f"Nouvelle image de référence : {self.seq_label(src)}", 6000)

    def first_as_reference(self):
        """Référence = la première brute prise, comme le Dwarf 3."""
        lights = [it.data(0, ROLE_SRC) for it in self.working_items()
                  if self.item_type(it) == "light"]
        scored = [s for s in self.light_order(lights) if s in self.scores]
        if not scored:
            QMessageBox.information(
                self, APP_NAME, "Lancez d'abord l'analyse (Ctrl+R) : la "
                "référence doit être une brute mesurée.")
            return
        first = scored[0]
        self.set_reference(first)
        if self.seq_number(first) != 1:
            self.statusBar().showMessage(
                f"La première image n'a pas pu être mesurée : référence sur "
                f"{self.seq_label(first)}", 8000)
        self.goto_reference()

    def reset_reference(self):
        self.ref_manual = None
        self.reference_changed()
        self.statusBar().showMessage(
            "Image de référence : retour au meilleur score", 6000)

    def on_flip(self, _checked=False):
        """Le sens des angles et des marges suit l'orientation affichée."""
        self.zone_dirty = True
        if self.transforms:
            self.reference_changed()

    def reference_changed(self):
        self.ref = self.reference_src()
        self.update_geometry()
        sorting = self.list.isSortingEnabled()
        self.list.setSortingEnabled(False)
        for it in self.all_items():
            self.update_item(it)
        self.list.setSortingEnabled(sorting)
        self.refresh_graph()
        self.redisplay()

    def list_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        menu = QMenu(self)

        def add(text, slot):
            act = QAction(text, menu)
            act.triggered.connect(lambda checked=False: slot())
            menu.addAction(act)
            return act
        rej = it.data(0, ROLE_REJ)
        add("Garder" if rej else "Rejeter", self.toggle_reject)
        if self.item_type(it) == "light":
            menu.addSeparator()
            src = it.data(0, ROLE_SRC)
            a = add("Définir comme image de référence",
                    lambda: self.set_reference(src))
            a.setEnabled(src in self.scores and src != self.ref)
            if self.ref_manual is not None:
                add("Revenir à la référence automatique", self.reset_reference)
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()

    # --- contrôle des darks ------------------------------------------------------

    def dark_tolerance(self):
        try:
            return float(settings().value("darks/temp_tol", 2.0))
        except (TypeError, ValueError):
            return 2.0

    def show_dark_control(self):
        items = list(self.working_items())
        darks = [(it.data(0, ROLE_SRC), it.data(0, ROLE_META) or {})
                 for it in items if self.item_type(it) == "dark"]
        lights = [(it.data(0, ROLE_SRC), it.data(0, ROLE_META) or {})
                  for it in items if self.item_type(it) == "light"
                  and not it.data(0, ROLE_REJ)]
        if not darks:
            QMessageBox.information(
                self, "Contrôle des darks",
                "Aucun dark dans la liste.\nAjoutez le dossier des darks "
                "(par exemple DWARF_DARK pour le Dwarf 3) : leur type est "
                "reconnu d'après l'en-tête ou le nom du dossier.")
            return
        # mesure de chaque dark (médiane, bruit) pour repérer les intrus
        stats = self._dark_stats
        todo = [s for s, _ in darks if s not in stats]
        if todo:
            prog = QProgressDialog("Mesure des darks…", "Arrêter", 0,
                                   len(todo), self)
            prog.setWindowModality(Qt.WindowModality.WindowModal)
            prog.setMinimumDuration(300)
            for k, s in enumerate(todo):
                prog.setValue(k)
                QApplication.processEvents()
                if prog.wasCanceled():
                    break
                try:
                    stats[s] = dark_stats(read_source(s)[0])
                except Exception:   # noqa: BLE001
                    pass
            prog.close()
        self._dark_stats = stats

        dlg = QDialog(self)
        dlg.setWindowTitle("Contrôle des darks")
        dlg.resize(900, 600)
        lay = QVBoxLayout(dlg)
        row = QHBoxLayout()
        row.addWidget(QLabel("Écart de température toléré :"))
        tol_spin = QDoubleSpinBox()
        tol_spin.setRange(0.5, 10.0)
        tol_spin.setSingleStep(0.5)
        tol_spin.setDecimals(1)
        tol_spin.setSuffix(" °C")
        tol_spin.setValue(self.dark_tolerance())
        row.addWidget(tol_spin)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("<b>Brutes gardées et darks compatibles</b>"))
        gtab = QTableWidget()
        gtab.setColumnCount(7)
        gtab.setHorizontalHeaderLabels(
            ["Pose", "Gain", "Binning", "Brutes (temp.)", "Darks (temp.)",
             "Couverture", "Verdict"])
        lay.addWidget(gtab, 1)
        lay.addWidget(QLabel("<b>Darks</b>"))
        dtab = QTableWidget()
        dtab.setColumnCount(8)
        dtab.setHorizontalHeaderLabels(
            ["Fichier", "Pose", "Gain", "Binning", "Temp.", "Médiane",
             "Bruit", "Verdict"])
        lay.addWidget(dtab, 2)
        advice = QLabel()
        advice.setWordWrap(True)
        lay.addWidget(advice)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        reject_btn = box.addButton("Rejeter les darks non conformes",
                                   QDialogButtonBox.ButtonRole.ActionRole)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        colors = {"ok": QColor("#3c9d5d"), "warn": QColor("#d08a20"),
                  "bad": QColor("#e05555"), "info": None}
        state = {}

        def cell(tab, r, c, text, level=None, right=False):
            itm = QTableWidgetItem(text)
            itm.setFlags(itm.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if right:
                set_align(itm, None, ALIGN_RIGHT)
            if level and colors.get(level) is not None:
                itm.setForeground(colors[level])
            tab.setItem(r, c, itm)

        def rng(t):
            if not t:
                return "?"
            return (f"{fr(t[0], 1)} °C" if t[1] - t[0] < 0.05 else
                    f"{fr(t[0], 1)} à {fr(t[1], 1)} °C")

        def fill():
            tol = tol_spin.value()
            groups, verdicts, tips = dark_control(lights, darks, tol, stats)
            state["verdicts"] = verdicts
            gtab.setRowCount(len(groups))
            for r, g in enumerate(groups):
                cell(gtab, r, 0, f"{_fs(g['exptime'])} s", right=True)
                cell(gtab, r, 1, fr(g["gain"], 0), right=True)
                cell(gtab, r, 2, g["binning"] or "?")
                cell(gtab, r, 3, f"{g['n']}  ({rng(g['temps'])})")
                cell(gtab, r, 4, f"{g['darks']}  ({rng(g['dark_temps'])})"
                     if g["darks"] else "0")
                cell(gtab, r, 5, "?" if g["cover"] is None
                     else f"{pct(g['cover'])}", right=True)
                cell(gtab, r, 6, g["verdict"][1], g["verdict"][0])
            dtab.setRowCount(len(darks))
            for r, (s, m) in enumerate(darks):
                med, noise = stats.get(s, (None, None))
                lvl, txt = verdicts[s]
                cell(dtab, r, 0, src_name(s))
                cell(dtab, r, 1, f"{_fs(m.get('exptime'))} s"
                     if m.get("exptime") is not None else "?", right=True)
                cell(dtab, r, 2, fr(m.get("gain"), 0)
                     if m.get("gain") is not None else "?", right=True)
                cell(dtab, r, 3, m.get("binning") or "?")
                cell(dtab, r, 4, f"{fr(m['temp'], 1)} °C"
                     if m.get("temp") is not None else "?", right=True)
                cell(dtab, r, 5, "" if med is None else f"{med:.0f}",
                     right=True)
                cell(dtab, r, 6, "" if noise is None else fr(noise, 1),
                     right=True)
                cell(dtab, r, 7, txt, lvl)
            for tab in (gtab, dtab):
                tab.resizeColumnsToContents()
                tab.horizontalHeader().setStretchLastSection(True)
            bad = sum(1 for v in verdicts.values() if v[0] == "bad")
            ok = sum(1 for v in verdicts.values() if v[0] == "ok")
            head = f"{ok} dark(s) conforme(s), {bad} non conforme(s). "
            advice.setText(head + " ".join(tips) if tips else
                           head + "Les darks couvrent toutes les brutes.")
            reject_btn.setEnabled(bad > 0)

        def on_tol(v):
            settings().setValue("darks/temp_tol", float(v))
            fill()

        def reject_bad():
            n = 0
            for s, (lvl, txt) in state["verdicts"].items():
                it = self.find_item(s)
                if lvl == "bad" and it is not None and \
                        not it.data(0, ROLE_REJ):
                    it.setData(0, ROLE_REASONS, [f"dark : {txt}"])
                    self.set_rejected(it, True, manual=True)
                    n += 1
            self.update_count()
            self.statusBar().showMessage(f"{n} dark(s) rejeté(s)", 6000)
            reject_btn.setEnabled(False)

        tol_spin.valueChanged.connect(on_tol)
        reject_btn.clicked.connect(reject_bad)
        fill()
        dlg.exec()

    # --- bilan de la séance ------------------------------------------------------

    def session_rows(self):
        """Une ligne par brute de la série en cours, avec ses mesures."""
        rows = []
        for it in self.working_items():
            if self.item_type(it) != "light":
                continue
            src = it.data(0, ROLE_SRC)
            meta = it.data(0, ROLE_META) or {}
            m = self.measures.get(src) or {}
            pos = self.sky_position(meta)
            rows.append({
                "src": src, "name": it.text(COL_NAME), "date": meta.get("date"),
                "exp": meta.get("exptime"), "temp": meta.get("temp"),
                "alt": pos[0] if pos else None, "az": pos[1] if pos else None,
                "lat": pos[2] if pos else None, "fwhm": m.get("fwhm"),
                "bg": m.get("bg"), "trail": m.get("trail"),
                "measured": bool(m), "kept": not it.data(0, ROLE_REJ),
                "eqmode": meta.get("eqmode")})
        return rows

    def cost_text(self, rows=None):
        rows = [r for r in (rows or self.session_rows()) if r["measured"]]
        if not rows:
            return ""
        c = rejection_cost(rows)
        if c["kept"] == c["n"]:
            return ""
        txt = (f"Tri : {c['n'] - c['kept']} image(s) écartée(s), "
               f"{pct(c['time_lost'])} du temps de pose")
        if c["exp_total"]:
            txt += f" ({hm(c['exp_total'] - c['exp_kept'])})"
        txt += f", soit environ {pct(c['snr_loss'])} de signal/bruit en moins"
        if "fwhm_gain" in c and c["fwhm_gain"] > 0.002:
            txt += (f" ; FWHM médiane {fr(c['fwhm_all'], 2)} → "
                    f"{fr(c['fwhm_kept'], 2)} px ({pct(c['fwhm_gain'])} plus "
                    f"fin)")
        return txt + "."

    def session_report(self):
        rows = self.session_rows()
        meas = [r for r in rows if r["measured"]]
        out = []
        if self.active_series is not None:
            out += [f"Série : {self.series_label(self.active_series)}", ""]
        kept = [r for r in rows if r["kept"]]
        exp_k = sum(r["exp"] or 0 for r in kept)
        exp_t = sum(r["exp"] or 0 for r in rows)
        out.append(f"{len(rows)} brute(s), {len(kept)} gardée(s)"
                   + (f" : {hm(exp_k)} de pose sur {hm(exp_t)}"
                      if exp_t else ""))
        # coût du tri
        out += ["", "CE QUE COÛTE LE TRI"]
        if meas:
            c = rejection_cost(meas)
            if c["kept"] == c["n"]:
                out.append("• Aucune image écartée.")
            else:
                out.append(f"• Temps de pose perdu : {pct(c['time_lost'])}"
                           + (f" ({hm(c['exp_total'] - c['exp_kept'])})"
                              if c["exp_total"] else ""))
                out.append(f"• Rapport signal/bruit : environ "
                           f"{pct(c['snr_loss'])} de moins qu'avec toutes "
                           f"les images (il varie comme la racine du temps "
                           f"de pose)")
                if "fwhm_gain" in c:
                    out.append(f"• FWHM médiane : {fr(c['fwhm_all'], 2)} px "
                               f"avec toutes les images, "
                               f"{fr(c['fwhm_kept'], 2)} px avec les "
                               f"gardées ({pct(c['fwhm_gain'], sign=True)} de finesse)")
                out.append("  Écarter une image vaut la peine quand elle "
                           "dégrade la finesse plus qu'elle n'apporte de "
                           "signal.")
        else:
            out.append("• Lancez l'analyse (Ctrl+R).")
        # nuits
        nights = nights_summary(rows)
        if nights and nights[0]["night"] is not None:
            out += ["", "NUITS" if len(nights) > 1 else "LA NUIT"]
            for n in nights:
                line = f"• Nuit du {n['night']:%d/%m/%Y}"
                if n["span"]:
                    line += (f", {n['span'][0]:%H:%M} – "
                             f"{n['span'][1]:%H:%M}")
                line += (f" : {n['kept']}/{n['n']} gardées, "
                         f"{hm(n['exp_kept'])} de pose")
                out.append(line)
                det = []
                if n["fwhm"]:
                    det.append(f"FWHM {fr(n['fwhm'], 2)} px")
                if n["bg"] is not None:
                    det.append(f"fond {n['bg']:.0f}")
                if n["temps"]:
                    det.append(f"capteur {fr(n['temps'][0], 1)} à "
                               f"{fr(n['temps'][1], 1)} °C")
                if n["alts"]:
                    det.append(f"hauteur {n['alts'][0]:.0f}° à "
                               f"{n['alts'][1]:.0f}°")
                if n["trails"]:
                    det.append(f"{n['trails']} traînée(s)")
                if det:
                    out.append("    " + ", ".join(det))
            if len(nights) > 1:
                fw = [n["fwhm"] for n in nights if n["fwhm"]]
                best = min((n for n in nights if n["fwhm"]),
                           key=lambda n: n["fwhm"], default=None)
                if best and max(fw) > 1.03 * min(fw):
                    out.append(f"  Meilleure nuit pour la finesse : "
                               f"{best['night']:%d/%m}. L'export réunit "
                               f"toutes les nuits gardées dans un seul "
                               f"empilement.")
        # mise au point et température
        out += ["", "MISE AU POINT ET TEMPÉRATURE"]
        fd = focus_drift(rows)
        if fd is None:
            out.append("• Pas assez de brutes mesurées avec une température "
                       "(il en faut 10).")
        elif not fd["enough"]:
            out.append(f"• Le capteur n'a varié que de {fr(fd['range'], 1)} °C"
                       " : pas de dérive mesurable.")
        else:
            out.append(f"• Capteur de {fr(fd['t_min'], 1)} à "
                       f"{fr(fd['t_max'], 1)} °C sur {fd['n']} images.")
            if fd["significant"]:
                sens = "baisse" if fd["slope"] < 0 else "hausse"
                out.append(f"• La FWHM augmente d'environ "
                           f"{fr(abs(fd['slope']) * 100, 1)} % par degré de "
                           f"{sens} (corrélation {fr(fd['r'], 2)}"
                           + (", hauteur de l'objet prise en compte"
                              if fd["with_alt"] else "") + ").")
                if "refocus_every" in fd:
                    out.append(f"• Conseil : refaites la mise au point "
                               f"tous les {fr(fd['refocus_every'], 1)} °C "
                               f"environ (5 % de FWHM perdus au-delà).")
                else:
                    out.append("• La FWHM se dégrade quand le capteur se "
                               "réchauffe : vérifiez la mise au point en "
                               "début de séance.")
            else:
                out.append(f"• Pas de lien net entre FWHM et température "
                           f"(corrélation {fr(fd['r'], 2)}) : la mise au "
                           f"point a tenu.")
        # hauteur et rotation
        out += ["", "HAUTEUR DE L'OBJET"]
        with_alt = [r for r in rows if r["alt"] is not None]
        if not with_alt:
            if not any(self.site(it.data(0, ROLE_META))
                       for it in self.working_items()):
                out.append("• Lieu d'observation inconnu : indiquez-le dans "
                           "Analyse ▸ Lieu d'observation pour calculer la "
                           "hauteur et la rotation attendue.")
            else:
                out.append("• Les en-têtes ne donnent pas la position de "
                           "l'objet (RA, DEC).")
        else:
            alts = [r["alt"] for r in with_alt]
            out.append(f"• De {min(alts):.0f}° à {max(alts):.0f}° au-dessus "
                       f"de l'horizon.")
            low = [r for r in with_alt if r["alt"] < 30]
            if low:
                out.append(f"• {len(low)} image(s) sous 30° : l'atmosphère "
                           f"y épaissit les étoiles et monte le fond.")
            alt_az_mount = any(r["eqmode"] == 0 for r in rows) or \
                self.field_rotates()
            if alt_az_mount:
                lat = with_alt[0]["lat"]
                theo = {r["src"]: rotation_rate_theory(lat, r["alt"], r["az"])
                        for r in with_alt}
                pairs = [(abs(self.rot_rates[s] * 60), abs(v))
                         for s, v in theo.items()
                         if v is not None and s in self.rot_rates
                         and abs(v) > 2]
                if len(pairs) >= 5:
                    ratio = float(np.median([a / b for a, b in pairs]))
                    out.append(f"• Rotation mesurée / rotation attendue : "
                               f"{fr(ratio, 2)}"
                               + (" (cohérent)" if 0.8 < ratio < 1.25 else
                                  " : vérifiez le lieu d'observation"))
                st = self.rotation_stats() or {}
                win = slow_exposure_windows(
                    with_alt, lat, st.get("fwhm"), st.get("halfdiag"),
                    st.get("exptime"))
                for a, b, rate in win:
                    out.append(f"• De {a:%H:%M} à {b:%H:%M}, la rotation "
                               f"(jusqu'à {fr(rate, 0)}°/h) allonge les "
                               f"étoiles des coins pendant les poses de "
                               f"{_fs(st.get('exptime'))} s.")
                if st and not win and st.get("exptime"):
                    out.append(f"• La rotation reste assez lente pour des "
                               f"poses de {_fs(st['exptime'])} s.")
        # traînées
        out += ["", "TRAÎNÉES (satellites, avions)"]
        tr = [r for r in meas if r["trail"]]
        if not meas:
            out.append("• Lancez l'analyse (Ctrl+R).")
        elif not tr:
            out.append("• Aucune traînée repérée.")
        else:
            out.append(f"• {len(tr)} image(s) sur {len(meas)} : "
                       + ", ".join(r["name"] for r in tr[:8])
                       + (" …" if len(tr) > 8 else ""))
            out.append("  La réjection des pixels à l'empilement (sigma "
                       "clipping) efface en général les traînées : inutile "
                       "de rejeter ces images si elles sont nombreuses.")
        return "\n".join(out)

    def show_session_report(self):
        if not self.list.count():
            QMessageBox.information(self, APP_NAME, "La liste est vide.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Bilan de la séance")
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit(self.session_report())
        txt.setReadOnly(True)
        txt.setMinimumSize(680, 460)
        lay.addWidget(txt)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

    # --- contrôle des flats ----------------------------------------------------

    def show_flat_control(self):
        items = list(self.working_items())
        flats = [(it.data(0, ROLE_SRC), it.data(0, ROLE_META) or {})
                 for it in items if self.item_type(it) == "flat"]
        lights = [(it.data(0, ROLE_SRC), it.data(0, ROLE_META) or {})
                  for it in items if self.item_type(it) == "light"
                  and not it.data(0, ROLE_REJ)]
        if not flats:
            QMessageBox.information(
                self, "Contrôle des flats",
                "Aucun flat dans la liste.\nAjoutez le dossier des flats : "
                "leur type est reconnu d'après l'en-tête (IMAGETYP) ou le nom "
                "du dossier (Flats).")
            return
        stats = self._flat_stats
        todo = [s for s, _ in flats if s not in stats]
        if todo:
            prog = QProgressDialog("Mesure des flats…", "Arrêter", 0,
                                   len(todo), self)
            prog.setWindowModality(Qt.WindowModality.WindowModal)
            prog.setMinimumDuration(300)
            for k, s in enumerate(todo):
                prog.setValue(k)
                QApplication.processEvents()
                if prog.wasCanceled():
                    break
                try:
                    data, _h, bitpix = read_source(s)
                    stats[s] = flat_stats(data, bitpix)
                except Exception:   # noqa: BLE001
                    pass
            prog.close()
        verdicts, advice = flat_control(flats, stats, lights)

        dlg = QDialog(self)
        dlg.setWindowTitle("Contrôle des flats")
        dlg.resize(820, 520)
        lay = QVBoxLayout(dlg)
        tab = QTableWidget()
        tab.setColumnCount(6)
        tab.setHorizontalHeaderLabels(["Fichier", "Filtre", "Niveau",
                                       "Dynamique", "Vignetage", "Verdict"])
        colors = {"ok": QColor("#3c9d5d"), "warn": QColor("#d08a20"),
                  "bad": QColor("#e05555")}
        tab.setRowCount(len(flats))
        for r, (s, m) in enumerate(flats):
            st = stats.get(s) or {}
            lvl, txt = verdicts[s]
            vals = [src_name(s), m.get("filter") or "",
                    f"{st['median']:.0f}" if st else "",
                    f"{pct(st['level'])}" if st else "",
                    f"{pct(st['vignetting'])}" if st else "", txt]
            for c, v in enumerate(vals):
                itm = QTableWidgetItem(v)
                itm.setFlags(itm.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c in (2, 3, 4):
                    set_align(itm, None, ALIGN_RIGHT)
                if c == 5 and lvl in colors:
                    itm.setForeground(colors[lvl])
                tab.setItem(r, c, itm)
        tab.resizeColumnsToContents()
        tab.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(tab, 1)
        bad = [s for s, v in verdicts.items() if v[0] == "bad"]
        ok = sum(1 for v in verdicts.values() if v[0] == "ok")
        lab = QLabel(f"{ok} flat(s) conforme(s), {len(bad)} non conforme(s). "
                     + " ".join(advice))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn = box.addButton("Rejeter les flats non conformes",
                            QDialogButtonBox.ButtonRole.ActionRole)
        btn.setEnabled(bool(bad))
        box.rejected.connect(dlg.reject)

        def reject_bad():
            n = 0
            for s in bad:
                it = self.find_item(s)
                if it is not None and not it.data(0, ROLE_REJ):
                    it.setData(0, ROLE_REASONS,
                               [f"flat : {verdicts[s][1]}"])
                    self.set_rejected(it, True, manual=True)
                    n += 1
            self.update_count()
            self.statusBar().showMessage(f"{n} flat(s) rejeté(s)", 6000)
            btn.setEnabled(False)
        btn.clicked.connect(reject_bad)
        lay.addWidget(box)
        dlg.exec()

    # --- calibrations --------------------------------------------------------

    def kept_metas(self):
        return [it.data(0, ROLE_META) or {"type": "light"}
                for it in self.working_items() if not it.data(0, ROLE_REJ)]

    def calibration_report(self, metas):
        summary, issues = check_calibration(metas, self.dark_tolerance())
        icons = {"ok": "✔", "info": "ℹ", "attention": "⚠"}
        order = {"attention": 0, "info": 1, "ok": 2}
        lines = [f"Images gardées : {summary}", ""]
        lines += [f"{icons[lvl]}  {txt}" for lvl, txt in
                  sorted(issues, key=lambda x: order[x[0]])]
        return "\n".join(lines), any(l == "attention" for l, _ in issues)

    def show_calibration_check(self):
        if not self.list.count():
            QMessageBox.information(self, APP_NAME, "La liste est vide.")
            return
        text, warn = self.calibration_report(self.kept_metas())
        text += ("\n\nLe type de chaque image vient de l'en-tête FITS "
                 "(IMAGETYP) ou, à défaut, du nom du fichier ou du dossier.")
        (QMessageBox.warning if warn else QMessageBox.information)(
            self, "Vérification des calibrations", text)

    def redisplay(self):
        if self.current_src:
            key = self.make_key(self.current_src)
            if key in self.cache:
                self.display(key, self.cache[key])

    def refresh_current(self, *_):
        if self.current_src:
            self.show_src(self.current_src)
        self.list.setFocus()

    # --- glisser-déposer -----------------------------------------------------

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            e.acceptProposedAction()

    def closeEvent(self, e):
        self.play_timer.stop()
        self.analysis_pool.clear()
        self.analysis_pool.waitForDone(5000)
        self.pool.clear()
        self.pool.waitForDone(2000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)   # associe la fenêtre à astrocheck.desktop
    here = os.path.dirname(os.path.abspath(__file__))
    icon = QIcon.fromTheme(APP_ID)
    for name in (APP_ID + ".svg", APP_ID + ".png"):
        if icon.isNull() and os.path.exists(os.path.join(here, name)):
            icon = QIcon(os.path.join(here, name))
    app.setWindowIcon(icon)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.add_paths(sys.argv[1:])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
