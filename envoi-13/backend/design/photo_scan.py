"""Scan 3D par photos (5.8.5) : 30 à 100 photos tout autour d'un objet → volume fermé imprimable.

1. **Positions des photos** : COLMAP (pycolmap, gratuit, sur le processeur) retrouve la position et l'orientation du
   téléphone pour chaque photo à partir des points communs (structure acquise par le mouvement).
2. **Échelle et sol** : le plan d'appui est le plus grand plan des points trouvés. Si l'objet est posé sur la
   feuille de repère, ses pastilles (positions connues) donnent l'échelle en mm ; sinon l'utilisateur donne une
   longueur connue.
3. **Profondeur** : pour chaque photo, balayage de plans (numpy + Pillow) : chaque pixel prend la profondeur où
   les photos voisines se ressemblent le plus (corrélation sur 7 × 7 pixels). Le papier blanc, sans texture,
   prend la profondeur du sol quand les couleurs concordent.
4. **Volume** : fusion des profondeurs dans une grille (distance signée tronquée) ; ce qu'une photo voit à
   travers est vide, le reste est plein. La surface est extraite par manifold3d : le volume est toujours
   fermé, et la base est plate (coupée au sol).

Aucun service en ligne : tout tourne sur le PC. pycolmap est facultatif (absent sur le téléphone)."""
import concurrent.futures
import json
import math
import os
import time

import numpy as np

try:
    from PIL import Image, ImageOps
except ImportError:                     # pragma: no cover
    Image = ImageOps = None

import manifold3d as m3d

from backend import security
from backend.design import photo_repair as pr

MIN_PHOTOS = 12
MAX_PHOTOS = 200
MAX_SIDE = 3200                         # photos ramenées à 3200 px de côté au plus (copie de travail)
SFM_SIDE = 2400                         # recherche des points communs (5.8.7 : 2400, petits objets mieux placés)
QUALITY = {                             # côté des cartes de profondeur, nombre de plans, voxels sur le grand côté
    "draft": {"side": 640, "planes": 96, "voxels": 160, "label": "Brouillon"},
    "normal": {"side": 900, "planes": 128, "voxels": 220, "label": "Normale"},
    "fine": {"side": 1280, "planes": 176, "voxels": 300, "label": "Fine"},
}
SMOOTH = {"0": ("Aucun", 0), "1": ("Léger", 4), "2": ("Conseillé", 12), "3": ("Fort", 30)}   # passes de lissage
SMOOTH_DEFAULT = "2"
MIN_DOTS = 6
MAX_TRIANGLES = 400_000                 # 5.8.9 : modèle ouvert sous la limite de l'aperçu (600 000)
AI_CARVE = (2, 0.3, 3, 0.5)             # voxel non mesuré vide si ≥ 2 photos le voient sur le fond et ≥ 30 % des
                                        # photos qui le voient ; tout voxel vide si ≥ 3 photos et ≥ 50 %                            # pastilles reconnues au minimum (sur deux bords de la feuille)
NCC_R = 3                               # fenêtre de corrélation 7 × 7
NCC_MIN = 0.55
UNAVAILABLE = ("Le scan 3D a besoin de la bibliothèque gratuite pycolmap : dans une invite de commandes, "
               "tapez  python -m pip install pycolmap  puis relancez Graou3D.")


class Cancelled(Exception):
    pass


_COLMAP = []


def colmap():
    """pycolmap, chargé à la première utilisation (lourd) ; None s'il manque (téléphone)."""
    if not _COLMAP:
        try:
            import pycolmap as mod
        except Exception:
            mod = None
        _COLMAP.append(mod)
    return _COLMAP[0]


def available():
    return Image is not None and colmap() is not None


# ------------------------------------------------------------------ photos
def prepare(paths, out_dir):
    """Photos d'origine → copies JPEG tournées selon l'EXIF, au plus MAX_SIDE px. → [(nom, focale 24×36 ou None)]."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    sizes = set()
    for k, p in enumerate(paths):
        try:
            with open(p, "rb") as f:
                img, f35 = pr.load_photo(f.read())
        except security.ValidationError:
            continue
        s = MAX_SIDE / max(img.size)
        if s < 1:
            img = img.resize((round(img.size[0] * s), round(img.size[1] * s)), Image.LANCZOS)
        name = f"photo_{k:03d}.jpg"
        img.save(os.path.join(out_dir, name), "JPEG", quality=94)
        out.append((name, f35))
        sizes.add(img.size)
    if len(out) < MIN_PHOTOS:
        raise security.ValidationError(f"Il faut au moins {MIN_PHOTOS} photos lisibles (30 à 80 conseillées), "
                                       f"tout autour de l'objet.")
    return out


# ------------------------------------------------------------------ 1. positions des photos (COLMAP)
def sfm(image_dir, names, work_dir, progress=None, cancelled=None):
    """→ pycolmap.Reconstruction (la plus grande), positions des photos dans une unité arbitraire."""
    if not available():
        raise security.ValidationError(UNAVAILABLE)
    pycolmap = colmap()
    progress = progress or (lambda *_: None)
    db = os.path.join(work_dir, "points.db")
    if os.path.exists(db):
        os.remove(db)
    sparse = os.path.join(work_dir, "sparse")
    os.makedirs(sparse, exist_ok=True)
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    f35 = [f for _n, f in names if f]
    if f35:                              # focale de l'EXIF : point de départ (affiné ensuite)
        with Image.open(os.path.join(image_dir, names[0][0]), formats=("JPEG",)) as im:
            w, h = im.size
        f = float(np.median(f35)) * math.hypot(w, h) / 43.2666
        reader.camera_params = f"{f:.2f},{w / 2:.2f},{h / 2:.2f},0"
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = SFM_SIDE
    ext.sift.max_num_features = 8192
    progress(3, "Recherche des points remarquables sur chaque photo…")
    pycolmap.extract_features(db, image_dir, image_names=[n for n, _f in names], camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=ext, device=pycolmap.Device.cpu)
    _check(cancelled)
    progress(10, "Mise en correspondance des photos deux à deux…")
    pycolmap.match_exhaustive(db, device=pycolmap.Device.cpu)
    _check(cancelled)
    progress(20, "Calcul de la position du téléphone pour chaque photo…")
    recs = pycolmap.incremental_mapping(db, image_dir, sparse)
    _check(cancelled)
    if not recs:
        raise security.ValidationError("Les photos ne se raccordent pas entre elles : prenez-en plus, en tournant "
                                       "de 10° environ entre deux photos, l'objet bien net et texturé.")
    rec = max(recs.values(), key=lambda r: r.num_reg_images())
    if rec.num_reg_images() < max(MIN_PHOTOS - 2, len(names) // 2):
        raise security.ValidationError(f"Seules {rec.num_reg_images()} photos sur {len(names)} ont pu être placées : "
                                       "reprenez l'objet en tournant plus doucement, sans reflets.")
    return rec


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise Cancelled()


def _pose(im):
    pose = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
    M = pose.matrix()
    return M[:, :3].copy(), M[:, 3].copy()


def cameras_of(rec):
    """→ liste de {name, f, cx, cy, k1, w, h, R, t, C, pts(ids des points vus)}."""
    out = []
    for iid, im in rec.images.items():
        hp = getattr(im, "has_pose", True)
        if not (hp() if callable(hp) else hp):
            continue
        cam = rec.cameras[im.camera_id]
        p = list(cam.params)
        f, cx, cy = p[0], p[1], p[2]
        k1 = p[3] if len(p) > 3 else 0.0
        R, t = _pose(im)
        ids = [q.point3D_id for q in im.points2D if q.has_point3D()]
        out.append({"name": im.name, "f": f, "cx": cx, "cy": cy, "k1": k1, "w": cam.width, "h": cam.height, "R": R, "t": t,
                    "C": -R.T @ t, "pts": set(ids)})
    out.sort(key=lambda c: c["name"])
    return out


# ------------------------------------------------------------------ 2. sol, échelle, repère
def ground_plane(pts, cams, seed=0, iters=1500):
    """Plan d'appui : le plus grand plan des points épars qui a presque toutes les photos d'un côté et presque
    aucun point de l'autre (5.8.7 : sur une feuille blanche, il y a bien plus de points sur l'objet que sur le
    sol ; un pan plat de l'objet n'est pas le sol, puisque le reste de l'objet passerait dessous).
    → (n, d, tol) avec n·x + d = 0, n tourné vers les photos."""
    rng = np.random.default_rng(seed)
    lo, hi = np.percentile(pts, 5, 0), np.percentile(pts, 95, 0)
    tol = 0.004 * float(np.linalg.norm(hi - lo))
    best, inl_best = 0, None
    # les photos entourent l'objet par-dessus : « en haut » ≈ des points vers les photos. Une itération sur deux
    # tire ses 3 points parmi les plus bas (le sol est souvent pauvre en points : papier blanc).
    up0 = cams.mean(0) - np.median(pts, 0)
    up0 = up0 / max(np.linalg.norm(up0), 1e-12)
    low = np.nonzero(pts @ up0 <= np.percentile(pts @ up0, 35))[0]
    for it in range(iters):
        pool = low if (it % 2 and len(low) >= 3) else np.arange(len(pts))
        a, b, c = pts[rng.choice(pool, 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-12:
            continue
        n = n / np.linalg.norm(n)
        d = -n @ a
        side = cams @ n + d
        if np.mean(side > 0) < 0.5:
            n, d, side = -n, -d, -side
        if np.mean(side > 0) < 0.85:                 # quelques photos mal placées (sous le sol) sont tolérées
            continue
        dist = pts @ n + d
        if np.mean(dist < -3 * tol) > 0.03:
            continue
        inl = np.abs(dist) < tol
        if inl.sum() <= best * 0.2:
            continue
        # les points du sol s'étalent autour de l'objet ; un plan qui frôle un pan de l'objet n'a ses points que
        # sous l'objet : ceux-là comptent peu
        e1 = np.cross(n, [1.0, 0, 0]) if abs(n[0]) < 0.9 else np.cross(n, [0, 1.0, 0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(n, e1)
        uv = np.column_stack([pts @ e1, pts @ e2])
        up = dist > 4 * tol
        if up.sum() >= 10:
            blo, bhi = np.percentile(uv[up], 0.5, 0), np.percentile(uv[up], 99.5, 0)
            pad = 0.08 * (bhi - blo) + tol
            under = np.all((uv >= blo - pad) & (uv <= bhi + pad), axis=1)
        else:
            under = np.zeros(len(pts), bool)
        score = float((inl & ~under).sum() + 0.2 * (inl & under).sum())
        if score > best:
            best, inl_best = score, (inl & ~under) if (inl & ~under).sum() >= 15 else inl
    if inl_best is None or best < 15:
        raise security.ValidationError("Sol introuvable : posez l'objet sur une table ou sur la feuille de repère, "
                                       "et photographiez aussi un peu autour.")
    q = pts[inl_best]
    m = q.mean(0)
    n = np.linalg.svd(q - m)[2][2]
    d = -n @ m
    if np.mean(cams @ n + d > 0) < 0.5:
        n, d = -n, -d
    return n, float(d), tol


def _undistort_pts(x, y, cam):
    u, v = (x - cam["cx"]) / cam["f"], (y - cam["cy"]) / cam["f"]
    u0, v0 = u, v
    for _ in range(4):
        r2 = u * u + v * v
        u, v = u0 / (1 + cam["k1"] * r2), v0 / (1 + cam["k1"] * r2)
    return u, v


def sheet_frame(image_dir, cams, n, d, progress=None):
    """Pastilles de la feuille vues sur les photos → repère en mm (x, y sur la feuille, z vers le haut) ou None.
    → {"M": 4×4 (monde COLMAP → mm), "dots", "rms_mm", "scale"}."""
    e1 = np.cross(n, [1.0, 0, 0])
    if np.linalg.norm(e1) < 0.1:
        e1 = np.cross(n, [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    o = -d * n
    obs, rays = [], []
    for k, cam in enumerate(cams):
        if progress and k % 8 == 0:
            progress(None, f"Recherche des pastilles de la feuille… ({k}/{len(cams)})")
        with Image.open(os.path.join(image_dir, cam["name"]), formats=("JPEG",)) as im:
            g = im.convert("L")
            s = min(1.0, pr.FIND_PX / max(g.size))
            if s < 1:
                g = g.resize((round(g.size[0] * s), round(g.size[1] * s)), Image.BILINEAR)
            g = np.asarray(g, np.float64)
        for (x, y, a) in pr._blobs(g):
            x, y = (x + 0.5) / s - 0.5, (y + 0.5) / s - 0.5
            u, v = _undistort_pts(x, y, cam)
            ray = cam["R"].T @ np.array([u, v, 1.0])
            den = n @ ray
            if abs(den) < 1e-9:
                continue
            lam = -(n @ cam["C"] + d) / den
            if lam <= 0:
                continue
            P = cam["C"] + lam * ray
            cosang = abs(den) / np.linalg.norm(ray)
            area = a / s / s * (lam * np.linalg.norm(ray) / cam["f"]) ** 2 / cosang
            obs.append([(P - o) @ e1, (P - o) @ e2, area, k])
            rays.append(np.concatenate([cam["C"], ray / np.linalg.norm(ray)]))
    if len(obs) < 12:
        return None
    obs = np.array(obs)
    rays = np.array(rays)
    # Taille des pastilles : la plus fréquente parmi les taches sombres… sauf si la table a un motif (bois,
    # carrelage) qui en donne plus. On essaie les tailles les plus fréquentes et on garde la meilleure feuille.
    la = np.log(obs[:, 2])
    cnt = np.array([int((np.abs(la - x) < 0.25).sum()) for x in la])
    tried, best = [], None
    for i in np.argsort(-cnt):
        if len(tried) >= 4:
            break
        if any(abs(la[i] - t) < 0.5 for t in tried):
            continue
        tried.append(la[i])
        a0 = float(np.exp(np.median(la[np.abs(la - la[i]) < 0.25])))
        out = _sheet_try(obs, rays, cnt, a0, n, d)
        if out and (best is None or (out["dots"], -out["rms_mm"]) > (best["dots"], -best["rms_mm"])):
            best = out
    return best


def _sheet_try(obs, rays, cnt, a0, n, d):
    """Pastilles de taille a0 → feuille reconnue (voir sheet_frame) ou None."""
    r0 = math.sqrt(a0 / math.pi)
    used = np.zeros(len(obs), bool)
    cl, X3 = [], []
    for i in np.argsort(-cnt):
        if used[i]:
            continue
        dd = np.hypot(obs[:, 0] - obs[i, 0], obs[:, 1] - obs[i, 1])
        mem = (dd < 1.5 * r0) & ~used
        used |= mem
        if len(set(obs[mem, 3].astype(int))) >= 3:
            cl.append((np.median(obs[mem, 0]), np.median(obs[mem, 1]), np.median(obs[mem, 2])))
            X3.append(_triangulate(rays[mem]))
    cl = np.array(cl) if cl else np.zeros((0, 3))
    if len(cl) < 6:
        return None
    keep = ((cl[:, 2] > 0.6 * a0) & (cl[:, 2] < 1.6 * a0)) | ((cl[:, 2] > 2.0 * a0) & (cl[:, 2] < 4.5 * a0))
    cl, X3 = cl[keep], np.array(X3)[keep]
    if len(cl) < 6:
        return None
    e1, e2, o = _plane_axes(n, d)
    # 5.8.7 : les pastilles, vues sur plusieurs photos, donnent elles-mêmes le plan de la feuille (plus juste que
    # les points épars, rares sur du papier blanc) : on le recalcule et on y reprojette les pastilles.
    fit = _dots_plane(X3, n)
    if fit is not None:
        n, d = fit
        e1, e2, o = _plane_axes(n, d)
        cl[:, 0], cl[:, 1] = (X3 - o) @ e1, (X3 - o) @ e2
    fit = _match_sheet(cl, a0)
    if fit is None:
        return None
    refl, src, dst, fmt = fit
    ms, md = src.mean(0), dst.mean(0)
    U, S, Vt = np.linalg.svd((dst - md).T @ (src - ms))
    Dg = np.diag([1.0, np.sign(np.linalg.det(U @ Vt)) or 1.0])
    R2 = U @ Dg @ Vt
    sc = float((S * np.diag(Dg)).sum() / ((src - ms) ** 2).sum())
    res = np.linalg.norm((sc * (R2 @ (src - ms).T)).T + md - dst, axis=1)
    # monde → (u, v, h) dans le plan → feuille (x, y vers le bas) → repère final (x, -y, hauteur)
    Pw = np.array([e1, e2 * refl, n])            # lignes : u, v·refl, h
    Mxy = sc * R2
    M = np.eye(4)
    rot = np.vstack([Mxy @ Pw[:2], sc * Pw[2]])  # (x_feuille, y_feuille, h·échelle)
    off = np.concatenate([md - Mxy @ ms - Mxy @ (Pw[:2] @ o), [sc * (-(Pw[2] @ o))]])
    flip = np.diag([1.0, -1.0, 1.0])             # y de la feuille vers le bas → y vers le haut (repère direct)
    M[:3, :3] = flip @ rot
    M[:3, 3] = flip @ off
    if np.linalg.det(M[:3, :3]) < 0:
        return None
    return {"M": M, "dots": int(len(src)), "rms_mm": float(np.sqrt(np.mean(res ** 2))), "scale": sc, "format": fmt}


def _plane_axes(n, d):
    e1 = np.cross(n, [1.0, 0, 0])
    if np.linalg.norm(e1) < 0.1:
        e1 = np.cross(n, [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(n, e1), -d * n


def _dots_plane(X, n0):
    """Plan des pastilles triangulées (robuste aux faux amas) → (n, d) proche de n0, ou None."""
    if len(X) < 6:
        return None
    dd = np.linalg.norm(X[:, None] - X[None], axis=2)
    np.fill_diagonal(dd, np.inf)
    tol = 0.08 * float(np.median(dd.min(axis=1)))
    best = None
    rng = np.random.default_rng(5)
    for _ in range(1500):
        i, j, k = rng.choice(len(X), 3, replace=False)
        nn = np.cross(X[j] - X[i], X[k] - X[i])
        if np.linalg.norm(nn) < 1e-12:
            continue
        nn /= np.linalg.norm(nn)
        inl = np.abs((X - X[i]) @ nn) < tol
        if best is None or inl.sum() > best.sum():
            best = inl
    if best is None or best.sum() < 6:
        return None
    q = X[best]
    m = q.mean(0)
    sv = np.linalg.svd(q - m)
    if sv[1][1] < 0.2 * sv[1][0]:                      # pastilles presque alignées : plan mal défini
        return None
    n = sv[2][2] * (1.0 if sv[2][2] @ n0 > 0 else -1.0)
    if n @ n0 < math.cos(math.radians(20)):
        return None
    return n, float(-n @ m)


def _triangulate(r):
    """Point le plus proche d'un faisceau de rayons (origine, direction unitaire)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for row in r:
        c, u = row[:3], row[3:]
        Pm = np.eye(3) - np.outer(u, u)
        A += Pm
        b += Pm @ c
    return np.linalg.lstsq(A, b, rcond=None)[0]


def _match_sheet(cl, a0):
    """Pastilles trouvées (u, v, aire) → appariement avec la feuille : (refl, points (u, refl·v), pastilles) ou None.
    Le pas entre pastilles voisines (30 mm) donne l'échelle ; la grosse pastille, si elle est vue, lève la symétrie
    du cadre (sinon la feuille peut être retournée d'un demi-tour : l'échelle et le sol restent justes)."""
    pitch = pr.DOT_X[1] - pr.DOT_X[0]
    P = cl[:, :2]
    dd = np.linalg.norm(P[:, None] - P[None], axis=2)
    np.fill_diagonal(dd, np.inf)
    nn = float(np.median(dd.min(axis=1)))
    big = cl[:, 2] > 2.0 * a0
    best = []
    for fmt in pr.FORMATS:                          # 5.8.7 : feuille A4 ou A3 (même pas, même pastilles)
        model = np.array([(x, y) for x, y, _d in pr.sheet_dots(fmt)])
        best += _match_model(P, dd, nn, big, model, pitch, fmt)
    if not best:
        return None
    best.sort(key=lambda x: (-x[0], x[4] != "A4"))  # à égalité (bord droit seul vu) : A4, l'échelle est la même
    score, refl, ok, k, fmt = best[0]
    if int(ok.sum()) < MIN_DOTS:
        return None
    model = np.array([(x, y) for x, y, _d in pr.sheet_dots(fmt)])
    mk = model[k[ok]]
    if np.linalg.svd(mk - mk.mean(0), compute_uv=False)[1] < 0.5 * pitch:
        return None                                   # pastilles toutes sur un seul bord : orientation incertaine
    return refl, P[ok] * [1.0, refl], model[k[ok]], fmt


def _match_model(P, dd, nn, big, model, pitch, fmt):
    mdd = np.linalg.norm(model[:, None] - model[None], axis=2)
    pairs = [(a, b) for a in range(len(model)) for b in range(len(model)) if abs(mdd[a, b] - pitch) < 1e-6]
    # repère du plan (u, v, n vers le haut) direct ; feuille (x, y vers le bas, z dans le papier) direct, donc
    # z = -n : le passage (u, v) → (x, y) retourne le plan, d'où v changé de signe avant la rotation.
    refl = -1.0
    Q = P * [1.0, refl]
    best = []
    for i in range(len(Q)):
        for j in np.argsort(dd[i])[:4]:
            if abs(dd[i, j] - nn) > 0.3 * nn:
                continue
            A = Q[j] - Q[i]
            for a, b in pairs:
                B = model[b] - model[a]
                s = pitch / np.linalg.norm(A)
                ang = math.atan2(B[1], B[0]) - math.atan2(A[1], A[0])
                c, si = math.cos(ang), math.sin(ang)
                T = ((Q - Q[i]) @ np.array([[c, si], [-si, c]])) * s + model[a]
                dist = np.linalg.norm(T[:, None] - model[None], axis=2)
                k = dist.argmin(axis=1)
                ok = dist[np.arange(len(T)), k] < 0.1 * pitch
                score = int(ok.sum()) + (3 if np.any(big & ok & (k == 0)) else 0) - (3 if np.any(big & ok & (k != 0)) else 0)
                best.append((score, refl, ok, k, fmt))
    return best


def plane_frame(n, d, cams, size=100.0):
    """Sans feuille : repère sol (z vers le haut), échelle provisoire (distance médiane des photos = `size`)."""
    e1 = np.cross(n, [1.0, 0, 0])
    if np.linalg.norm(e1) < 0.1:
        e1 = np.cross(n, [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    o = -d * n
    C = np.array([c["C"] for c in cams])
    target = convergence(C, np.array([c["R"][2] for c in cams]))
    dist = float(np.median(np.linalg.norm(C - target, axis=1)))
    sc = size / max(dist, 1e-9)
    M = np.eye(4)
    M[:3, :3] = sc * np.array([e1, e2, n])
    tg = M[:3, :3] @ (target - o)
    M[:3, 3] = -M[:3, :3] @ o - [tg[0], tg[1], 0]
    return {"M": M, "scale": sc}


def convergence(C, D):
    """Point le plus proche de tous les axes de visée (C + λ D)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for c, dv in zip(C, D):
        dv = dv / np.linalg.norm(dv)
        P = np.eye(3) - np.outer(dv, dv)
        A += P
        b += P @ c
    return np.linalg.lstsq(A, b, rcond=None)[0]


def transform_cams(cams, M):
    """Caméras dans le repère final (mm ou provisoire) : x_cam = R x + t avec x = monde final."""
    sR = M[:3, :3]
    s = float(np.cbrt(np.linalg.det(sR)))
    Rw = sR / s
    out = []
    for c in cams:
        R = c["R"] @ Rw.T
        C = M[:3, :3] @ c["C"] + M[:3, 3]
        out.append(dict(c, R=R, t=-R @ C, C=C))
    return out, s


def object_box(rec, cams, M, plane_tol, with_footprint=False):
    """Boîte de l'objet (repère final) : points épars au-dessus du sol, près de l'endroit que visent les photos."""
    pts = np.array([p.xyz for p in rec.points3D.values()])
    P = (M[:3, :3] @ pts.T).T + M[:3, 3]
    s = float(np.cbrt(np.linalg.det(M[:3, :3])))
    C = np.array([c["C"] for c in cams])
    target = convergence(C, np.array([c["R"][2] for c in cams]))
    reach = 0.6 * float(np.median(np.hypot(C[:, 0] - target[0], C[:, 1] - target[1])))
    h_min = max(3 * plane_tol * s, 0.004 * reach)
    near = np.hypot(P[:, 0] - target[0], P[:, 1] - target[1]) < reach
    sel = P[near & (P[:, 2] > h_min) & (P[:, 2] < 2.5 * reach)]
    if len(sel) < 50:
        raise security.ValidationError("Objet introuvable au milieu des photos : gardez-le au centre de chaque photo.")
    sel = _main_cluster(sel, reach / 15.0, target)
    lo, hi = np.percentile(sel, 0.5, 0), np.percentile(sel, 99.5, 0)
    size = float(np.max(hi - lo))
    pad = 0.2 * size                                  # les bords peu texturés ont peu de points épars
    lo, hi = lo - pad, hi + pad
    lo[2] = 0.0
    if with_footprint:
        return lo, hi, footprint(sel, lo, hi, size)
    return lo, hi


def footprint(sel, lo, hi, size):
    """Emprise au sol de l'objet (5.8.7) : cases sous les points épars de l'objet, élargies et bouchées.
    Sous l'objet et tout près de lui, le sol n'est jamais « deviné » (ground_fill) : sinon les parties sombres ou
    unies posées au ras de la feuille (pneus, fond plat d'une voiture) passaient pour du papier et étaient effacées."""
    cell = max(size / 120.0, 1e-6)
    nx = int(np.ceil((hi[0] - lo[0]) / cell)) + 1
    ny = int(np.ceil((hi[1] - lo[1]) / cell)) + 1
    grid = np.zeros((nx, ny), bool)
    ix = np.clip(((sel[:, 0] - lo[0]) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((sel[:, 1] - lo[1]) / cell).astype(int), 0, ny - 1)
    grid[ix, iy] = True
    close = max(2, int(round(0.06 * size / cell)))     # bouche les trous entre points (zones sans motif)
    grid = _erode(_fill_holes(_dilate(grid, close)), close)   # dessus blanc et lisse (peu de points) : bouché aussi
    grid = _dilate(grid, max(2, int(round(0.03 * size / cell))))   # marge autour de l'objet
    # hauteur au plus haut de l'objet, case par case (points épars, élargie) : une zone que les photos n'ont pas
    # pu vider (dessus blanc et lisse, ombre portée de l'objet) ne monte pas au-dessus de l'objet
    top = np.full((nx, ny), -np.inf)
    np.maximum.at(top, (ix, iy), sel[:, 2])
    for _ in range(close + 2):
        n = top.copy()
        n[1:] = np.maximum(n[1:], top[:-1])
        n[:-1] = np.maximum(n[:-1], top[1:])
        n[:, 1:] = np.maximum(n[:, 1:], top[:, :-1])
        n[:, :-1] = np.maximum(n[:, :-1], top[:, 1:])
        top = n
    top[~np.isfinite(top)] = float(np.percentile(sel[:, 2], 98))
    top += 0.04 * size
    return {"lo": np.array(lo[:2], float), "cell": cell, "grid": grid, "top": top}


def _dilate(g, r):
    """Élargit de r cases (alternance croix / carré : à peu près un octogone)."""
    out = g.copy()
    for k in range(r):
        n = out.copy()
        n[1:] |= out[:-1]
        n[:-1] |= out[1:]
        n[:, 1:] |= out[:, :-1]
        n[:, :-1] |= out[:, 1:]
        if k % 2:
            n[1:, 1:] |= out[:-1, :-1]
            n[:-1, :-1] |= out[1:, 1:]
            n[1:, :-1] |= out[:-1, 1:]
            n[:-1, 1:] |= out[1:, :-1]
        out = n
    return out


def _fill_holes(g):
    """Cases vides que l'on ne peut pas rejoindre depuis le bord : bouchées."""
    out = np.zeros_like(g)
    out[0, :], out[-1, :], out[:, 0], out[:, -1] = ~g[0, :], ~g[-1, :], ~g[:, 0], ~g[:, -1]
    free = ~g
    while True:
        n = out.copy()
        n[1:] |= out[:-1]
        n[:-1] |= out[1:]
        n[:, 1:] |= out[:, :-1]
        n[:, :-1] |= out[:, 1:]
        n &= free
        if (n == out).all():
            return ~out
        out = n


def _erode(g, r):
    return ~_dilate(~g, r)


def _fp_index(fp, x, y):
    g = fp["grid"]
    ix = np.floor((x - fp["lo"][0]) / fp["cell"]).astype(np.int64)
    iy = np.floor((y - fp["lo"][1]) / fp["cell"]).astype(np.int64)
    ok = (ix >= 0) & (ix < g.shape[0]) & (iy >= 0) & (iy < g.shape[1])
    return ix, iy, ok


def in_footprint(fp, x, y):
    if fp is None:
        return np.zeros(np.shape(x), bool)
    g = fp["grid"]
    ix = np.floor((x - fp["lo"][0]) / fp["cell"]).astype(np.int64)
    iy = np.floor((y - fp["lo"][1]) / fp["cell"]).astype(np.int64)
    ok = (ix >= 0) & (ix < g.shape[0]) & (iy >= 0) & (iy < g.shape[1])
    out = np.zeros(np.shape(x), bool)
    out[ok] = g[ix[ok], iy[ok]]
    return out


def _main_cluster(pts, cell, target):
    """Points de l'objet : le plus gros amas de cases occupées (voisines par un sommet), sans les points isolés
    (reflets, faux appariements) qui agrandiraient la boîte."""
    keys = np.floor(pts / cell).astype(np.int64)
    cells, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    index = {tuple(c): i for i, c in enumerate(cells)}
    label = -np.ones(len(cells), np.int64)
    sizes = []
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]
    for start in range(len(cells)):
        if label[start] >= 0:
            continue
        lab = len(sizes)
        label[start] = lab
        stack, total = [start], 0
        while stack:
            i = stack.pop()
            total += int(counts[i])
            cx, cy, cz = cells[i]
            for a, b, c in offsets:
                j = index.get((cx + a, cy + b, cz + c))
                if j is not None and label[j] < 0:
                    label[j] = lab
                    stack.append(j)
        sizes.append(total)
    main = int(np.argmax(sizes))
    out = pts[label[inverse] == main]
    return out if len(out) >= 30 else pts


# ------------------------------------------------------------------ 3. cartes de profondeur
def _box(a, r):
    """Moyenne sur une fenêtre (2r+1)² (sommes cumulées, bords répétés)."""
    k = 2 * r + 1
    c = np.pad(a, ((r + 1, r), (r + 1, r)), mode="edge").cumsum(0).cumsum(1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) * (1.0 / (k * k))


def load_view(image_dir, cam, side, box=None):
    """Photo dé-distordue et réduite à `side` px → dict(K, R, t, C, col, gray, W, H).
    box = (lo, hi) : seule la partie de la photo où se trouve l'objet est gardée (5.8.7) ; un petit objet au milieu
    d'une grande photo garde ainsi tous ses détails au lieu d'occuper un tiers de la carte de profondeur."""
    f, cx, cy, k1 = cam["f"], cam["cx"], cam["cy"], cam["k1"]
    with Image.open(os.path.join(image_dir, cam["name"]), formats=("JPEG",)) as im:
        im = im.convert("RGB")
        W0, H0 = im.size
        x0, y0, x1, y1 = 0, 0, W0, H0
        if box is not None:
            lo, hi = box
            P = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            q = (cam["R"] @ P.T).T + cam["t"]
            if np.all(q[:, 2] > 1e-9):
                u, v = f * q[:, 0] / q[:, 2] + cx, f * q[:, 1] / q[:, 2] + cy
                pad = 0.08 * max(u.max() - u.min(), v.max() - v.min())
                x0, x1 = int(max(0, np.floor(u.min() - pad))), int(min(W0, np.ceil(u.max() + pad)))
                y0, y1 = int(max(0, np.floor(v.min() - pad))), int(min(H0, np.ceil(v.max() + pad)))
                if x1 - x0 < 64 or y1 - y0 < 64:
                    x0, y0, x1, y1 = 0, 0, W0, H0
        if (x0, y0, x1, y1) != (0, 0, W0, H0):
            im = im.crop((x0, y0, x1, y1))
        Wc, Hc = im.size
        s = min(1.0, side / max(Wc, Hc))
        W, H = max(1, round(Wc * s)), max(1, round(Hc * s))
        small = im.resize((W, H), Image.BOX) if s < 1 else im.copy()
    sx, sy = W / Wc, H / Hc
    K = np.array([[f * sx, 0, (cx - x0 + 0.5) * sx - 0.5], [0, f * sy, (cy - y0 + 0.5) * sy - 0.5], [0, 0, 1.0]])
    col = np.asarray(small, np.float32)
    if abs(k1) > 1e-7:                                 # dé-distorsion radiale sur l'image réduite
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
        u = (xs - K[0, 2]) / K[0, 0]
        v = (ys - K[1, 2]) / K[1, 1]
        r2 = u * u + v * v
        xd = K[0, 2] + K[0, 0] * u * (1 + k1 * r2)
        yd = K[1, 2] + K[1, 1] * v * (1 + k1 * r2)
        col = _bilinear(col, xd, yd)
    gray = col @ np.array([0.299, 0.587, 0.114], np.float32)
    return {"name": cam["name"], "K": K, "R": cam["R"], "t": cam["t"], "C": cam["C"], "col": col, "gray": gray.astype(np.float32),
            "W": W, "H": H, "pts": cam["pts"]}


def _bilinear(img, x, y):
    H, W = img.shape[:2]
    x = np.clip(x, 0, W - 1.001)
    y = np.clip(y, 0, H - 1.001)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    ax, ay = (x - x0)[..., None], (y - y0)[..., None]
    if img.ndim == 2:
        ax, ay = ax[..., 0], ay[..., 0]
    return (img[y0, x0] * (1 - ax) * (1 - ay) + img[y0, x0 + 1] * ax * (1 - ay) + img[y0 + 1, x0] * (1 - ax) * ay
            + img[y0 + 1, x0 + 1] * ax * ay).astype(np.float32)


def neighbours(views, i, box_center, n=5):
    """Photos voisines utiles pour la photo i : beaucoup de points communs, angle de 5 à 40° sur l'objet."""
    a = views[i]
    va = box_center - a["C"]
    va /= np.linalg.norm(va)
    out = []
    for j, b in enumerate(views):
        if j == i:
            continue
        shared = len(a["pts"] & b["pts"])
        vb = box_center - b["C"]
        vb /= np.linalg.norm(vb)
        ang = math.degrees(math.acos(float(np.clip(va @ vb, -1, 1))))
        if shared < 30 or not 4.0 <= ang <= 40.0:
            continue
        out.append((shared * math.exp(-((ang - 14.0) / 10.0) ** 2), j))
    return [j for _s, j in sorted(out, reverse=True)[:n]]


def _depth_range(v, lo, hi):
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    z = (v["R"] @ corners.T + v["t"][:, None])[2]
    zmin = max(float(z.min()), 1e-3 * float(z.max()))
    return zmin, float(z.max())


def depth_map(views, i, srcs, lo, hi, planes, cancelled=None):
    """Balayage de plans parallèles à la photo i → (profondeur (0 = inconnue), corrélation)."""
    a = views[i]
    W, H = a["W"], a["H"]
    zmin, zmax = _depth_range(a, lo, hi)
    inv = np.linspace(1.0 / zmin, 1.0 / zmax, planes)
    g = a["gray"]
    r = NCC_R
    mu = _box(g, r)
    sd = np.sqrt(np.maximum(_box(g * g, r) - mu * mu, 1e-6))
    Kinv = np.linalg.inv(a["K"])
    T = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.0]])
    Ti = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1.0]])
    nrm = np.array([0, 0, 1.0])
    srcs_d = []
    for j in srcs:
        b = views[j]
        Rrel = b["R"] @ a["R"].T
        srcs_d.append((b["K"], Rrel, b["t"] - Rrel @ a["t"], Image.fromarray(b["gray"], mode="F")))
    best_c = np.full((H, W), -2.0, np.float32)
    best_k = np.zeros((H, W), np.int32)
    c_prev = np.full((H, W), -2.0, np.float32)
    c_next = np.full((H, W), -2.0, np.float32)
    prev = np.full((H, W), -2.0, np.float32)
    flat = sd < 2.0
    for k, iz in enumerate(inv):
        if k % 16 == 0:
            _check(cancelled)
        d = 1.0 / iz
        sc = []
        for Kb, Rrel, trel, src in srcs_d:
            Hm = T @ (Kb @ (Rrel + np.outer(trel, nrm) / d) @ Kinv) @ Ti
            Hm = Hm / Hm[2, 2]
            w = np.asarray(src.transform((W, H), Image.PERSPECTIVE, tuple(Hm.ravel()[:8]), Image.BILINEAR), np.float32)
            mw = _box(w, r)
            sw = np.sqrt(np.maximum(_box(w * w, r) - mw * mw, 1e-6))
            ncc = (_box(g * w, r) - mu * mw) / (sd * sw)
            ncc[(sw < 2.0) | flat | (w == 0)] = -1.0
            sc.append(ncc)
        if len(sc) >= 2:
            sc = np.sort(np.stack(sc), axis=0)
            cost = sc[-2:].mean(axis=0)                 # les deux meilleures photos (une peut être cachée)
        else:
            cost = sc[0]
        upd = best_k == k - 1
        c_next[upd] = cost[upd]
        better = cost > best_c
        best_c[better] = cost[better]
        best_k[better] = k
        c_prev[better] = prev[better]
        c_next[better] = -2.0
        prev = cost
    ok = (best_k > 0) & (best_k < planes - 1) & (c_prev > -2) & (c_next > -2) & (best_c > NCC_MIN)
    den = c_prev - 2 * best_c + c_next
    off = np.where(ok & (den < -1e-6), 0.5 * (c_prev - c_next) / np.where(den < -1e-6, den, -1.0), 0.0)
    off = np.clip(off, -0.5, 0.5)
    iz = inv[best_k] + off * (inv[1] - inv[0])
    depth = np.where(ok, 1.0 / iz, 0.0).astype(np.float32)
    return depth, best_c


def ground_fill(views, i, srcs, depth, fp=None):
    """Pixels sans profondeur dont le rayon touche le sol et dont la couleur concorde sur ≥ 2 photos voisines
    (papier blanc, table unie) : profondeur du sol. → (profondeur complétée, masque sol).
    `fp` : emprise au sol de l'objet (footprint), où le sol n'est jamais deviné."""
    a = views[i]
    W, H = a["W"], a["H"]
    zc, hit, P = ground_rays(a)                         # profondeur = λ (rayons de composante z caméra = 1)
    col = _box3(a["col"], 2)
    agree = np.zeros(W * H, np.int32)
    for j in srcs:
        b = views[j]
        q = b["K"] @ (b["R"] @ P + b["t"][:, None])
        qx, qy = q[0] / q[2], q[1] / q[2]
        inside = (q[2] > 0) & (qx >= 0) & (qx <= b["W"] - 1) & (qy >= 0) & (qy <= b["H"] - 1)
        cb = _bilinear(_box3(b["col"], 2), qx, qy).reshape(-1, 3)
        diff = np.abs(cb - col.reshape(-1, 3)).max(axis=1)
        agree += (inside & (diff < 14.0)).astype(np.int32)
    fill = hit & (agree >= 2) & (depth.ravel() == 0) & ~in_footprint(fp, P[0], P[1])
    out = depth.copy().ravel()
    out[fill] = zc[fill]
    return out.reshape(H, W), fill.reshape(H, W)


def ground_rays(a):
    """Rayon de chaque pixel jusqu'au sol (z = 0) → (profondeur, touche le sol, point touché (3, N))."""
    W, H = a["W"], a["H"]
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    rw = a["R"].T @ (np.linalg.inv(a["K"]) @ np.vstack([xs.ravel(), ys.ravel(), np.ones(W * H)]))
    lam = -a["C"][2] / np.where(np.abs(rw[2]) > 1e-9, rw[2], 1e-9)
    hit = (lam > 0) & (rw[2] < 0)
    return lam, hit, a["C"][:, None] + rw * lam


def depth_ai_views(views, depths, lo, hi, fp, sess, progress=None, cancelled=None):
    """5.8.8 : IA de profondeur (depth_ai) sur chaque photo, recalée sur les profondeurs mesurées et sur le sol
    → silhouettes [(fond, objet) ou None si le recalage de cette photo n'est pas fiable], ou None sans aucune.
    Le relief deviné ne remplace jamais une mesure : essayé en complément des profondeurs, il abîmait les détails
    fins (roues d'une F1 de synthèse) ; seules les silhouettes, sûres, sont gardées."""
    from backend.design import depth_ai
    sil = []
    for i, v in enumerate(views):
        _check(cancelled)
        if progress:
            progress(None, f"IA de profondeur… ({i + 1}/{len(views)})")
        try:
            r = depth_ai.disparity(sess, v["col"])
        except Exception:                               # modèle illisible : le scan continue sans l'IA
            return None
        lam, hit, P = ground_rays(v)
        safe = hit & ~in_footprint(fp, P[0], P[1])
        m = depth_ai.view_masks(v, r, depths[i], lam.reshape(r.shape), safe.reshape(r.shape), lo, hi)
        sil.append(None if m is None else (m["bg"], m["obj"]))
    return sil if any(x is not None for x in sil) else None


def _box3(img, r):
    return np.stack([_box(img[..., c], r) for c in range(3)], axis=-1)


def consistency(views, depths, i, srcs, rel=0.01):
    """Garde les profondeurs confirmées par au moins 2 photos voisines (écart relatif < `rel`)."""
    a = views[i]
    d = depths[i]
    H, W = d.shape
    ys, xs = np.nonzero(d > 0)
    if not len(xs):
        return d
    z = d[ys, xs].astype(np.float64)
    P = a["R"].T @ (np.linalg.inv(a["K"]) @ np.vstack([xs * z, ys * z, z]) - a["t"][:, None])
    votes = np.zeros(len(xs), np.int32)
    for j in srcs:
        b = views[j]
        q = b["K"] @ (b["R"] @ P + b["t"][:, None])
        u, v = np.round(q[0] / q[2]).astype(int), np.round(q[1] / q[2]).astype(int)
        ins = (q[2] > 0) & (u >= 0) & (u < b["W"]) & (v >= 0) & (v < b["H"])
        db = np.zeros(len(xs))
        db[ins] = depths[j][v[ins], u[ins]]
        votes += (ins & (db > 0) & (np.abs(db - q[2]) < rel * q[2])).astype(np.int32)
    out = np.zeros_like(d)
    keep = votes >= 2
    out[ys[keep], xs[keep]] = d[ys[keep], xs[keep]]
    return out


# ------------------------------------------------------------------ 4. volume
def grid_step(ext, voxels):
    """Pas de la grille : `voxels` cases sur le grand côté pour un objet trapu ; un objet plat ou long (voiture)
    garde le même nombre total de cases, donc des cases plus fines (jusqu'à 1,6 fois)."""
    big = float(np.max(ext))
    budget = 0.45 * float(voxels) ** 3
    step = float(np.cbrt(float(np.prod(ext)) / budget))
    return float(min(big / voxels, max(step, big / (1.6 * voxels))))


def fuse(views, depths, lo, hi, voxels, progress=None, cancelled=None, fp=None, sil=None):
    """Distance signée tronquée + vide vu à travers → grille (positive = dedans) et pas de la grille.
    Ce qu'aucune photo n'a vu est compté plein (le dessous, les creux cachés)… mais seulement sous l'objet :
    avec `fp` (emprise et hauteur de l'objet d'après les points épars), l'inconnu hors de l'objet est vide."""
    ext = hi - lo
    step = grid_step(ext, voxels)
    dims = np.maximum(np.ceil(ext / step).astype(int) + 1, 2)
    mu = 3.0 * step
    xs = lo[0] + step * np.arange(dims[0])
    ys = lo[1] + step * np.arange(dims[1])
    zs = lo[2] + step * np.arange(dims[2])
    tsdf = np.zeros(dims, np.float32)
    wsum = np.zeros(dims, np.float32)
    free = np.zeros(dims, np.uint8)
    seen = np.zeros(dims, np.uint8)
    if sil is not None:                                 # 5.8.8 : votes des silhouettes de l'IA (fond / objet)
        bg = np.zeros(dims, np.uint16)
        fg = np.zeros(dims, np.uint16)
    for k, (v, d) in enumerate(zip(views, depths)):
        if progress and k % 4 == 0:
            progress(None, f"Assemblage du volume… ({k}/{len(views)})")
        _check(cancelled)
        H, W = d.shape
        # traitement par tranches en z (mémoire)
        for iz in range(dims[2]):
            X, Y = np.meshgrid(xs, ys, indexing="ij")
            Pw = np.stack([X.ravel(), Y.ravel(), np.full(X.size, zs[iz])])
            Pc = v["R"] @ Pw + v["t"][:, None]
            zc = Pc[2]
            q = v["K"] @ Pc
            with np.errstate(divide="ignore", invalid="ignore"):
                u = np.round(q[0] / q[2]).astype(np.int64)
                w = np.round(q[1] / q[2]).astype(np.int64)
            ins = (zc > 0) & (u >= 0) & (u < W) & (w >= 0) & (w < H)
            dm = np.zeros(X.size, np.float32)
            dm[ins] = d[w[ins], u[ins]]
            has = ins & (dm > 0)
            sdf = dm - zc                                   # > 0 : devant la surface vue (vide)
            near = has & (sdf > -mu)
            val = np.clip(sdf / mu, -1.0, 1.0)
            sl = (slice(None), slice(None), iz)
            t2 = tsdf[sl].ravel()
            w2 = wsum[sl].ravel()
            f2 = free[sl].ravel()
            s2 = seen[sl].ravel()
            t2[near] += val[near]
            w2[near] += 1.0
            f2[has & (sdf > mu)] = np.minimum(f2[has & (sdf > mu)].astype(np.int32) + 1, 255)
            s2[ins] = np.minimum(s2[ins].astype(np.int32) + 1, 255)
            tsdf[sl] = t2.reshape(dims[0], dims[1])
            wsum[sl] = w2.reshape(dims[0], dims[1])
            free[sl] = f2.reshape(dims[0], dims[1])
            seen[sl] = s2.reshape(dims[0], dims[1])
            if sil is not None and sil[k] is not None:
                mb, mo = sil[k]
                b2 = np.zeros(X.size, bool)
                o2 = np.zeros(X.size, bool)
                b2[ins] = mb[w[ins], u[ins]]
                o2[ins] = mo[w[ins], u[ins]]
                bg[sl] += b2.reshape(dims[0], dims[1]).astype(np.uint16)
                fg[sl] += o2.reshape(dims[0], dims[1]).astype(np.uint16)
    sdf = np.where(wsum > 0, -tsdf / np.maximum(wsum, 1), np.where(free >= 2, -1.0, 1.0)).astype(np.float32)
    # vide franc vu à travers par au moins 3 photos, même si une mesure isolée disait « surface »
    sdf[(free >= 3) & (free.astype(np.float32) > 0.6 * np.maximum(wsum, 1))] = -1.0
    if sil is not None:
        n1, f1, n2, f2 = AI_CARVE
        b, tot = bg.astype(np.int32), bg.astype(np.int32) + fg
        carve = ((b >= n1) & (b >= f1 * tot) & (wsum == 0)) | ((b >= n2) & (b >= f2 * tot))
        sdf[carve & (zs[None, None, :] > 0)] = -1.0
    if fp is not None:
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        ix, iy, ok = _fp_index(fp, X, Y)
        inside = np.zeros(X.shape, bool)
        cap = np.full(X.shape, -np.inf)
        inside[ok] = fp["grid"][ix[ok], iy[ok]]
        cap[ok] = fp["top"][ix[ok], iy[ok]]
        outside = (~inside[:, :, None]) | (zs[None, None, :] > cap[:, :, None])
        sdf[outside & (wsum == 0)] = -1.0
    # liseré au ras du sol : de l'inconnu en bas d'une colonne vide au-dessus n'est pas de l'objet
    # (le sol caché juste à côté de la pièce) ; sous un surplomb ou sous une partie non vue, on garde
    band = zs < max(4.0 * step, 0.08 * ext[2])
    if band.any() and (~band).any():
        solid_above = (sdf[:, :, ~band] > 0).any(axis=2)
        skirt = band[None, None, :] & ~solid_above[:, :, None] & (wsum == 0)
        sdf[skirt] = -1.0
    return sdf, step, (xs, ys, zs)


def extract(sdf, step, axes, smooth=1):
    """Grille → Manifold (positif = dedans), coupée au sol (z ≥ 0)."""
    xs, ys, zs = axes
    g = sdf.astype(np.float64)
    if smooth:
        g = _blur3(g)
    x0, y0, z0 = xs[0], ys[0], zs[0]
    nx, ny, nz = g.shape
    inv = 1.0 / step

    def f(x, y, z):
        if z < 0.0:
            return -1.0
        fx, fy, fz = (x - x0) * inv, (y - y0) * inv, (z - z0) * inv
        if fx < 0 or fy < 0 or fz < 0 or fx >= nx - 1 or fy >= ny - 1 or fz >= nz - 1:
            return -1.0
        i, j, k = int(fx), int(fy), int(fz)
        ax, ay, az = fx - i, fy - j, fz - k
        c00 = g[i, j, k] * (1 - ax) + g[i + 1, j, k] * ax
        c10 = g[i, j + 1, k] * (1 - ax) + g[i + 1, j + 1, k] * ax
        c01 = g[i, j, k + 1] * (1 - ax) + g[i + 1, j, k + 1] * ax
        c11 = g[i, j + 1, k + 1] * (1 - ax) + g[i + 1, j + 1, k + 1] * ax
        v = (c00 * (1 - ay) + c10 * ay) * (1 - az) + (c01 * (1 - ay) + c11 * ay) * az
        return min(v, z * inv)                            # sol : la base est plate
    bounds = [x0, y0, 0.0, xs[-1], ys[-1], zs[-1]]
    man = m3d.Manifold.level_set(f, bounds, step)
    return man


def _blur3(g):
    k = np.array([0.25, 0.5, 0.25])
    for ax in range(3):
        g = _conv1(g, k, ax)
    return g


def _conv1(g, k, ax):
    p = [(0, 0)] * 3
    p[ax] = (1, 1)
    gp = np.pad(g, p, mode="edge")
    sl = lambda a, b: tuple(slice(a, gp.shape[i] - b) if i == ax else slice(None) for i in range(3))
    return k[0] * gp[sl(0, 2)] + k[1] * gp[sl(1, 1)] + k[2] * gp[sl(2, 0)]


def largest(man):
    parts = man.decompose()
    if not parts:
        return man
    return max(parts, key=lambda p: p.volume())


# ------------------------------------------------------------------ chaîne complète
def reconstruct(image_dir, names, work_dir, quality="normal", progress=None, cancelled=None, threads=None, ai=None):
    """Photos préparées → {"man", "info"} : volume fermé dans le repère final (mm si la feuille est vue)."""
    progress = progress or (lambda *_: None)
    q = QUALITY.get(quality) or QUALITY["normal"]
    t0 = time.time()
    rec = sfm(image_dir, names, work_dir, progress, cancelled)
    cams = cameras_of(rec)
    pts = np.array([p.xyz for p in rec.points3D.values()])
    if len(pts) < 200:
        raise security.ValidationError("Trop peu de points communs entre les photos : l'objet doit être texturé "
                                       "(un objet uni ou brillant se scanne mal : un voile de bombe mate aide).")
    # photo raccrochée par trop peu de points communs : position peu sûre, elle abîmerait le volume
    cams = [c for c in cams if len(c["pts"]) >= max(40, 0.08 * np.median([len(x["pts"]) for x in cams]))]
    n, d, tol = ground_plane(pts, np.array([c["C"] for c in cams]))
    # photo placée sous le sol (série presque plane, peu de points communs) : position fausse, écartée
    cams = [c for c in cams if c["C"] @ n + d > 0]
    if len(cams) < MIN_PHOTOS - 2:
        raise security.ValidationError(f"Seules {len(cams)} photos ont pu être placées : prenez-en plus, en tournant "
                                       "de 10 à 15° entre deux photos, l'objet assez grand sur chaque photo.")
    progress(28, "Recherche de la feuille de repère…")
    sheet = sheet_frame(image_dir, cams, n, d, lambda _p, m: progress(None, m))
    frame = sheet or plane_frame(n, d, cams)
    M = frame["M"]
    cams, _s = transform_cams(cams, M)
    lo, hi, fp = object_box(rec, cams, M, tol, with_footprint=True)
    _check(cancelled)
    progress(32, "Préparation des photos…")
    views = [load_view(image_dir, c, q["side"], (lo, hi)) for c in cams]
    center = 0.5 * (lo + hi)
    nb = [neighbours(views, i, center) for i in range(len(views))]
    depths = [None] * len(views)
    done = [0]
    workers = threads or max(1, min(4, (os.cpu_count() or 2)))

    def one(i):
        if len(nb[i]) < 2:
            return i, np.zeros((views[i]["H"], views[i]["W"]), np.float32)
        dm, _c = depth_map(views, i, nb[i], lo, hi, q["planes"], cancelled)
        dm, _g = ground_fill(views, i, nb[i], dm, fp)
        return i, dm
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, dm in ex.map(one, range(len(views))):
            depths[i] = dm
            done[0] += 1
            progress(34 + 48 * done[0] / len(views), f"Profondeur vue par chaque photo… ({done[0]}/{len(views)})")
    _check(cancelled)
    progress(83, "Vérification croisée des profondeurs…")
    depths = [consistency(views, depths, i, nb[i]) for i in range(len(views))]
    sil = None
    if ai is not None:                                  # ai : session onnxruntime de l'IA de profondeur (facultative)
        progress(84, "IA de profondeur…")
        sil = depth_ai_views(views, depths, lo, hi, fp, ai, progress, cancelled)
    progress(86, "Assemblage du volume…")
    sdf, step, axes = fuse(views, depths, lo, hi, q["voxels"], lambda _p, m: progress(None, m), cancelled, fp, sil)
    _check(cancelled)
    progress(95, "Extraction de la surface…")
    man = largest(extract(sdf, step, axes))
    if man.is_empty() or man.volume() < 1e-9:
        raise security.ValidationError("Aucun volume n'a pu être reconstruit : objet trop lisse, brillant ou trop petit "
                                       "sur les photos.")
    info = {"photos": len(names), "placed": len(cams), "points": int(len(pts)), "scaled": bool(sheet),
            "sheet_dots": sheet["dots"] if sheet else 0, "sheet_format": sheet["format"] if sheet else None, "sheet_rms_mm": round(sheet["rms_mm"], 2) if sheet else None,
            "voxel": round(step, 3), "seconds": round(time.time() - t0), "quality": quality,
            "coverage": round(float(np.mean([(dm > 0).mean() for dm in depths])), 3),
            "depth_ai": sum(x is not None for x in sil) if sil else 0}
    return {"man": man, "info": info}


# ------------------------------------------------------------------ 5. scan en deux faces (5.8.7)
def surface_points(man, n, seed=0):
    """Points tirés au hasard sur la surface (au prorata des aires) → (points, normales)."""
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties, np.float64)[:, :3]
    f = np.asarray(mesh.tri_verts, np.int64)
    A, B, C = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cr = np.cross(B - A, C - A)
    area = np.linalg.norm(cr, axis=1)
    if not len(f) or area.sum() <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    rng = np.random.default_rng(seed)
    k = rng.choice(len(f), n, p=area / area.sum())
    r1, r2 = rng.random(n), rng.random(n)
    swap = r1 + r2 > 1
    r1[swap], r2[swap] = 1 - r1[swap], 1 - r2[swap]
    P = A[k] + r1[:, None] * (B[k] - A[k]) + r2[:, None] * (C[k] - A[k])
    N = cr[k] / np.maximum(area[k], 1e-12)[:, None]
    return P, N


def walls(P, N):
    """Surface que les deux faces voient pour de vrai : les flancs à peu près verticaux, hors des bords haut et bas.
    Une face tournée vers le haut sur un scan l'est vers le bas sur l'autre (bouchée), donc seuls les flancs
    servent au recalage."""
    z0, z1 = float(P[:, 2].min()), float(P[:, 2].max())
    h = z1 - z0
    return (P[:, 2] > z0 + 0.1 * h) & (P[:, 2] < z1 - 0.1 * h) & (np.abs(N[:, 2]) < 0.5)


def _nearest(A, B, chunk=256):
    """Plus proche voisin brut (sans scipy) → (distances, indices)."""
    d = np.empty(len(A))
    idx = np.empty(len(A), np.int64)
    bb = (B * B).sum(1)
    for i in range(0, len(A), chunk):
        a = A[i:i + chunk]
        d2 = (a * a).sum(1)[:, None] - 2 * a @ B.T + bb[None]
        j = d2.argmin(1)
        idx[i:i + chunk] = j
        d[i:i + chunk] = np.sqrt(np.maximum(d2[np.arange(len(a)), j], 0))
    return d, idx


def _rigid(src, dst):
    ms, md = src.mean(0), dst.mean(0)
    U, _S, Vt = np.linalg.svd((dst - md).T @ (src - ms))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt)) or 1.0])
    R = U @ D @ Vt
    return R, md - R @ ms


def _icp(src, dst, R, t, iters, zfix, keep=0.7, planar=True):
    """ICP élagué : à chaque tour, seuls les `keep` meilleurs couples comptent (le bas bouché d'une face ne
    correspond à rien sur l'autre). La hauteur n'est pas cherchée : la pièce retournée touche la feuille par son
    point le plus bas, donc `zfix(R)` la donne. planar=True : seulement un tour autour de z et un glissement."""
    err = np.inf
    for _ in range(iters):
        S = src @ R.T + t
        d, j = _nearest(S, dst)
        m = d <= np.quantile(d, keep)
        if planar:
            a, b = S[m, :2], dst[j[m], :2]
            ma, mb = a.mean(0), b.mean(0)
            U, _S, Vt = np.linalg.svd((b - mb).T @ (a - ma))
            D = np.diag([1.0, np.sign(np.linalg.det(U @ Vt)) or 1.0])
            R2 = np.eye(3)
            R2[:2, :2] = U @ D @ Vt
            t2 = np.zeros(3)
            t2[:2] = mb - R2[:2, :2] @ ma
        else:
            R2, t2 = _rigid(S[m], dst[j[m]])
        R, t = R2 @ R, R2 @ t + t2
        t[2] = zfix(R)
        err = float(np.sqrt(np.mean(d[m] ** 2)))
    return R, t, err


def _rot(axis, deg):
    a = math.radians(deg)
    c, s_ = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s_], [0, s_, c]])
    if axis == "y":
        return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    return np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])


def merge_faces(top, bottom, progress=None, cancelled=None):
    """Deux scans de la même pièce, à l'échelle (mm) : posée normalement (`top`) puis retournée (`bottom`).
    Chaque scan bouche ce qu'il n'a pas vu (le dessous) : la vraie pièce est donc dans les deux volumes, et leur
    intersection, une fois recalés, garde le vrai dessus de l'un et le vrai dessous de l'autre. On sait que la
    pièce a été retournée : seuls le tour autour de la verticale et le glissement sur la feuille sont cherchés.
    → (volume dans le repère de `top`, {"rms_mm", "overlap"})."""
    progress = progress or (lambda *_: None)
    Pa, Na = surface_points(top, 12000, 1)
    Pb, Nb = surface_points(bottom, 12000, 2)
    A, B = Pa[walls(Pa, Na)], Pb[walls(Pb, Nb)]
    if len(A) < 200 or len(B) < 200:
        raise security.ValidationError("Les deux scans sont trop petits ou trop incomplets pour être recalés.")
    size = float(np.max(np.ptp(Pa, 0)))
    rng = np.random.default_rng(3)
    a_s = A[rng.choice(len(A), min(700, len(A)), replace=False)]
    b_s = B[rng.choice(len(B), min(2500, len(B)), replace=False)]
    ca, cb = A.mean(0), B.mean(0)
    vb = np.asarray(bottom.to_mesh().vert_properties, np.float64)[:, :3]
    zfix = lambda R: -float((vb @ R.T)[:, 2].min())    # point le plus bas de la face retournée sur la feuille
    # départs : pièce retournée (demi-tour autour de x), tous les 15° autour de z
    starts = []
    for yaw in range(0, 360, 15):
        R = _rot("z", yaw) @ _rot("x", 180)
        t = ca - R @ cb
        t[2] = zfix(R)
        starts.append((R, t))
    found = []
    for k, (R, t) in enumerate(starts):
        if k % 6 == 0:
            _check(cancelled)
            progress(None, f"Recalage des deux faces… ({k}/{len(starts)})")
        R, t, err = _icp(b_s, a_s, R, t, 10, zfix)
        found.append((err, R, t))
    found.sort(key=lambda x: x[0])
    refined = []
    for err, R, t in found[:4]:                        # les meilleurs départs, affinés avec plus de points
        sub = B[rng.choice(len(B), min(4000, len(B)), replace=False)]
        R, t, _e = _icp(sub, A, R, t, 15, zfix)
        R, t, e2 = _icp(sub, A, R, t, 10, zfix, planar=False)   # petit basculement (pièce pas tout à fait à plat)
        refined.append((e2, R, t))
    refined.sort(key=lambda x: x[0])
    # pièce symétrique : plusieurs recalages collent aussi bien aux flancs. La vraie pièce est dans les deux
    # volumes, donc le bon recalage est celui qui garde le plus de matière en commun.
    best, keep = None, None
    for e2, R, t in refined:
        if e2 > 1.5 * refined[0][0] + 0.05:
            continue
        M = np.hstack([R, t[:, None]])
        vol = (top ^ bottom.transform(M.tolist())).volume()
        if best is None or vol > keep:
            best, keep = (e2, R, t), vol
    err, R, t = best
    d, _j = _nearest(B @ R.T + t, A)
    overlap = float((d < max(0.6, 0.01 * size)).mean())
    if err > 0.03 * size or overlap < 0.15:
        raise security.ValidationError("Les deux faces ne se recalent pas : vérifiez que c'est bien la même pièce, "
                                       "à l'échelle sur les deux scans (feuille de repère), et que les flancs se voient "
                                       "bien sur les deux séries.")
    M = np.hstack([R, t[:, None]])
    out = largest(top ^ bottom.transform(M.tolist()))
    if out.is_empty():
        raise security.ValidationError("La combinaison des deux faces est vide : le recalage a échoué.")
    return out, {"rms_mm": round(err, 3), "overlap": round(overlap, 3)}


def save_mesh(man, path):
    v, f = man.to_mesh().vert_properties, man.to_mesh().tri_verts
    np.savez_compressed(path, v=np.asarray(v, np.float32)[:, :3], f=np.asarray(f, np.int32))


def load_mesh(path):
    with np.load(path) as z:
        v, f = z["v"].astype(np.float64), z["f"].astype(np.int64)
    man = m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(v, np.float32), tri_verts=np.ascontiguousarray(f, np.uint32)))
    if man.is_empty():
        raise security.ValidationError("Le résultat du scan est illisible : relancez la reconstruction.")
    return man


def finish(man, scaled, opts):
    """Lissage, mise à l'échelle et base : opts = {"smooth" (0 à 3), "size_mm", "axis" (x|y|z), "base_mm"…}.
    Sans feuille, l'unité est provisoire : la taille demandée (longueur réelle ou taille d'impression) la fixe."""
    opts = opts if isinstance(opts, dict) else {}
    level = str(opts.get("smooth", SMOOTH_DEFAULT))
    man = smooth_mesh(man, SMOOTH.get(level, SMOOTH[SMOOTH_DEFAULT])[1])
    lo, hi = (np.array(b) for b in _bbox(man))
    axis = {"x": 0, "y": 1, "z": 2}.get(opts.get("axis"), 2)
    size = security.number(opts.get("size_mm"), 0, 5000, 0)
    ext = float(hi[axis] - lo[axis])
    if size > 0 and ext > 0:
        man = man.scale([size / ext] * 3)
    elif not scaled:
        raise security.ValidationError("Sans la feuille de repère sur les photos, donnez une longueur réelle (ou la "
                                       "taille voulue) pour mettre le modèle à l'échelle.")
    base = security.number(opts.get("base_mm"), 0, 500, 0)
    if base > 0:
        lo, hi = (np.array(b) for b in _bbox(man))
        man = man.trim_by_plane([0.0, 0.0, 1.0], float(lo[2] + base))
    simplify = security.number(opts.get("simplify_mm"), 0, 5, 0)
    if simplify > 0:
        man = man.simplify(simplify)
    man = lighten(man)
    man = largest(man)
    if man.is_empty():
        raise security.ValidationError("Il ne reste rien après la coupe de la base : coupez moins haut.")
    return man


def lighten(man, limit=None):
    """5.8.9 : un personnage en qualité Normale ou Fine dépassait les 600 000 triangles de l'aperçu (« trop détaillé »).
    Simplification par paliers : écart toléré d'un dix-millième de la taille, doublé jusqu'à passer sous la limite
    (invisible à l'impression : 0,01 mm sur une pièce de 100 mm au premier palier)."""
    limit = limit or MAX_TRIANGLES
    if man.num_tri() <= limit:
        return man
    lo, hi = (np.array(b) for b in _bbox(man))
    size = float(np.max(hi - lo))
    tol = 1e-4 * size
    while man.num_tri() > limit and tol <= 0.01 * size:
        man = man.simplify(tol)
        tol *= 2
    return man


def smooth_mesh(man, passes, lam=0.5, mu=-0.53):
    """Lissage de Taubin (5.8.7) : enlève le grain de la surface sans la faire maigrir (une passe qui lisse, une
    qui regonfle). La base posée sur la feuille reste plate : ses sommets ne bougent pas en hauteur."""
    if passes <= 0 or man.is_empty() or man.num_vert() < 1000:
        return man                                     # maillage grossier (pas un scan) : rien à lisser
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties, np.float64)[:, :3].copy()
    f = np.asarray(mesh.tri_verts, np.int64)
    if not len(f):
        return man
    src, dst = np.asarray(mesh.merge_from_vert, np.int64), np.asarray(mesh.merge_to_vert, np.int64)
    if len(src):                                       # sommets dédoublés (coutures) : un seul sommet lissé
        idx = np.arange(len(v))
        idx[src] = dst
        f = idx[f]
    used = np.zeros(len(v), bool)
    used[f.ravel()] = True
    e = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    a, b = e[:, 0], e[:, 1]
    deg = np.bincount(np.concatenate([a, b]), minlength=len(v)).astype(np.float64)
    deg[deg == 0] = 1.0
    zmin = float(v[used, 2].min())
    tol = 1e-4 * max(float(np.ptp(v[used], axis=0).max()), 1e-9)
    base = v[:, 2] <= zmin + tol

    def step(v, k):
        s = np.zeros_like(v)
        np.add.at(s, a, v[b])
        np.add.at(s, b, v[a])
        d = s / deg[:, None] - v
        d[base, 2] = 0.0
        return v + k * d
    v0 = v.copy()
    el = np.linalg.norm(v[a] - v[b], axis=1)
    reach = 1.5 * float(np.median(el)) if len(el) else 0.0
    for _ in range(int(passes)):
        v = step(step(v, lam), mu)
    move = v - v0                                      # jamais plus d'une case et demie : les arêtes restent
    norm = np.linalg.norm(move, axis=1)
    big = norm > reach
    v[big] = v0[big] + move[big] * (reach / norm[big])[:, None]
    out = m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(v, np.float32),
                                tri_verts=np.ascontiguousarray(f, np.uint32)))
    return man if out.is_empty() else out


def _bbox(man):
    b = man.bounding_box()
    return b[:3], b[3:]
