"""Scans 3D par photos (5.8.5) : dossiers des scans, réception des photos du téléphone, reconstruction en tâche
de fond (une à la fois, annulable) et ouverture du résultat dans Conception. 5.8.7 : scan en deux faces (la
pièce retournée est un second scan, recalé puis combiné avec le premier).

Chaque scan a son dossier dans Conception/Scans : photos/ (telles que reçues), travail/ (calculs, effacé à la
fin sauf les positions des photos) et resultat.npz (volume). L'état est dans scan.json, relu au démarrage :
un scan interrompu par la fermeture de Graou3D repasse à « photos prêtes »."""
import base64
import datetime
import json
import os
import re
import secrets
import shutil
import threading
import time

from backend import security
from backend.design import scan_link

MAX_SCANS = 60
STATES = ("photos", "running", "done", "error", "cancelled")
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
MERGED = "resultat-2faces.npz"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


class ScanService:
    def __init__(self, export_dir, store=None, ai_folder=None):
        """store(name, man, info) → vue d'une session Conception (fourni par DesignService).
        ai_folder : dossier de l'IA de profondeur (5.8.8, téléchargement facultatif)."""
        self.root = os.path.join(export_dir, "Conception", "Scans")
        self.ai = None
        if ai_folder:
            from backend.design import depth_ai
            self.ai = depth_ai.DepthAI(ai_folder)
        self._store = store
        self._lock = threading.RLock()
        self._run = None                 # {"id", "thread", "cancel": Event, "pct", "msg"}
        self._rx = None                  # (scan_id, Receiver)
        self._sender = scan_link.Sender()

    # ------------------------------------------------------------ dossiers
    def _dir(self, sid):
        sid = security.text(sid, 40)
        if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", sid):
            raise security.ValidationError("Scan introuvable.")
        path = os.path.join(self.root, sid)
        if not os.path.isdir(path):
            raise security.ValidationError("Scan introuvable (supprimé ?).")
        return path

    def _meta(self, sid):
        path = os.path.join(self._dir(sid), "scan.json")
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        meta.setdefault("id", sid)
        meta.setdefault("title", "Scan")
        if meta.get("state") not in STATES:
            meta["state"] = "photos"
        if meta["state"] == "running" and not (self._run and self._run["id"] == sid):
            meta["state"] = "photos"     # Graou3D fermé pendant le calcul
            meta["msg"] = "Calcul interrompu : relancez-le."
        return meta

    def _save(self, sid, meta):
        path = os.path.join(self._dir(sid), "scan.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def _photos(self, sid):
        d = os.path.join(self._dir(sid), "photos")
        if not os.path.isdir(d):
            return []
        return sorted(os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(PHOTO_EXT))

    def create(self, title=""):
        title = security.text(title, 60).strip() or "Scan du " + _now()
        os.makedirs(self.root, exist_ok=True)
        existing = self._ids()
        if len(existing) >= MAX_SCANS:
            raise security.ValidationError(f"{MAX_SCANS} scans au plus : supprimez-en un ancien.")
        sid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        os.makedirs(os.path.join(self.root, sid, "photos"))
        self._save(sid, {"id": sid, "title": title, "created": _now(), "state": "photos"})
        return self.status(sid)

    def _ids(self):
        if not os.path.isdir(self.root):
            return []
        return sorted((n for n in os.listdir(self.root) if re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", n)), reverse=True)

    def list(self):
        out = []
        for sid in self._ids():
            try:
                out.append(self.status(sid))
            except security.ValidationError:
                continue
        return out

    def rename(self, sid, title):
        with self._lock:
            meta = self._meta(sid)
            meta["title"] = security.text(title, 60).strip() or meta["title"]
            self._save(sid, meta)
        return self.status(sid)

    def delete(self, sid):
        path = self._dir(sid)
        with self._lock:
            if self._run and self._run["id"] == sid:
                raise security.ValidationError("Ce scan est en cours de calcul : annulez-le d'abord.")
            if self._rx and self._rx[0] == sid:
                self.receive_stop()
        shutil.rmtree(path, ignore_errors=True)
        return {"ok": True}

    def status(self, sid):
        meta = self._meta(sid)
        out = {k: meta.get(k) for k in ("id", "title", "created", "state", "msg", "info", "quality", "error", "finished",
                                        "face_of", "merged", "merge_msg")}
        out["count"] = len(self._photos(sid))
        out["result"] = os.path.exists(os.path.join(self._dir(sid), "resultat.npz"))
        if out["merged"] and not os.path.exists(os.path.join(self._dir(sid), MERGED)):
            out["merged"] = None
        out["folder"] = self._dir(sid)
        if self._run and self._run["id"] == sid:
            out.update(state="running", pct=self._run["pct"], msg=self._run["msg"])
        if self._rx and self._rx[0] == sid:
            out["receiving"] = self._rx[1].public()
        return out

    def thumbs(self, sid, limit=60):
        """Petites vignettes JPEG (base64) des photos du scan."""
        from PIL import Image, ImageOps
        out = []
        for p in self._photos(sid)[:limit]:
            try:
                with Image.open(p, formats=("JPEG", "PNG", "WEBP")) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((160, 160))
                    import io
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=70)
                    out.append({"name": os.path.basename(p), "jpeg": base64.b64encode(buf.getvalue()).decode()})
            except Exception:
                continue
        return out

    # ------------------------------------------------------------ ajout des photos
    def add_photo(self, sid, raw):
        if not raw or not scan_link._is_photo(raw):
            raise security.ValidationError("Ce n'est pas une photo JPEG ou PNG.")
        with self._lock:
            if self._run and self._run["id"] == sid:
                raise security.ValidationError("Calcul en cours : attendez la fin pour ajouter des photos.")
            photos = self._photos(sid)
            if len(photos) >= scan_link.MAX_COUNT:
                raise security.ValidationError(f"{scan_link.MAX_COUNT} photos au plus par scan.")
            ext = ".png" if raw[:4] == b"\x89PNG" else ".jpg"
            d = os.path.join(self._dir(sid), "photos")
            os.makedirs(d, exist_ok=True)
            k = len(photos) + 1
            while os.path.exists(os.path.join(d, f"{k:04d}{ext}")):
                k += 1
            with open(os.path.join(d, f"{k:04d}{ext}"), "wb") as f:
                f.write(raw)
            meta = self._meta(sid)
            if meta["state"] in ("done", "error", "cancelled"):
                meta["state"] = "photos"
                self._save(sid, meta)
            return len(photos) + 1

    def add_files(self, sid, paths):
        added = skipped = 0
        for p in list(paths)[:scan_link.MAX_COUNT]:
            try:
                if os.path.getsize(p) > scan_link.MAX_PHOTO:
                    skipped += 1
                    continue
                with open(p, "rb") as f:
                    raw = f.read()
                self.add_photo(sid, raw)
                added += 1
            except (OSError, security.ValidationError):
                skipped += 1
        return {"added": added, "skipped": skipped, "count": len(self._photos(sid))}

    def remove_photo(self, sid, name):
        name = os.path.basename(security.text(name, 40))
        path = os.path.join(self._dir(sid), "photos", name)
        if not name.lower().endswith(PHOTO_EXT) or not os.path.isfile(path):
            raise security.ValidationError("Photo introuvable.")
        os.remove(path)
        return {"count": len(self._photos(sid))}

    # ------------------------------------------------------------ réception du téléphone
    def receive(self, sid):
        self._dir(sid)
        with self._lock:
            if self._rx and self._rx[0] == sid and not self._rx[1].closed:
                return self.status(sid)
            self.receive_stop()
            title = self._meta(sid)["title"]
            rx = scan_link.Receiver(title, lambda raw: self.add_photo(sid, raw), status=lambda: self._phone_view(sid),
                                    on_done=lambda: self.start(sid, "normal"))
            rx.open()
            self._rx = (sid, rx)
        return self.status(sid)

    def receive_stop(self):
        with self._lock:
            if self._rx:
                self._rx[1].close()
            self._rx = None
        return {"ok": True}

    def _phone_view(self, sid):
        st = self.status(sid)
        return {k: st.get(k) for k in ("title", "state", "pct", "msg", "count", "error")}

    # ------------------------------------------------------------ reconstruction
    def start(self, sid, quality="normal"):
        from backend.design import photo_scan
        if not photo_scan.available():
            raise security.ValidationError(photo_scan.UNAVAILABLE)
        quality = quality if quality in photo_scan.QUALITY else "normal"
        photos = self._photos(sid)
        if len(photos) < photo_scan.MIN_PHOTOS:
            raise security.ValidationError(f"{len(photos)} photo(s) : il en faut au moins {photo_scan.MIN_PHOTOS} "
                                           "(30 à 80 conseillées).")
        with self._lock:
            if self._run:
                raise security.ValidationError("Un scan est déjà en calcul : attendez qu'il se termine ou annulez-le.")
            cancel = threading.Event()
            run = {"id": sid, "cancel": cancel, "pct": 0, "msg": "Préparation des photos…"}
            meta = self._meta(sid)
            meta.update(state="running", quality=quality, error=None, msg=None)
            self._save(sid, meta)
            self._run = run
        t = threading.Thread(target=self._work, args=(sid, run, photos, quality), name="scan-3d", daemon=True)
        run["thread"] = t
        t.start()
        return self.status(sid)

    def cancel(self, sid):
        with self._lock:
            if self._run and self._run["id"] == sid:
                self._run["cancel"].set()
                self._run["msg"] = "Annulation…"
        return self.status(sid)

    def _work(self, sid, run, photos, quality):
        from backend.design import photo_scan
        base = self._dir(sid)
        work = os.path.join(base, "travail")
        meta = self._meta(sid)

        def progress(pct, msg=""):
            if pct is not None:
                run["pct"] = max(run["pct"], min(99, int(pct)))
            if msg:
                run["msg"] = msg
        try:
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            images = os.path.join(work, "images")
            names = photo_scan.prepare(photos, images)
            sess = self.ai.session() if self.ai is not None and self.ai.enabled() else None
            out = photo_scan.reconstruct(images, names, work, quality, progress, run["cancel"].is_set, ai=sess)
            photo_scan.save_mesh(out["man"], os.path.join(base, "resultat.npz"))
            try:
                os.remove(os.path.join(base, MERGED))
            except OSError:
                pass
            meta.update(state="done", info=out["info"], finished=_now(), msg=None, error=None, merged=None)
        except photo_scan.Cancelled:
            meta.update(state="cancelled", msg="Calcul annulé.")
        except security.ValidationError as e:
            meta.update(state="error", error=str(e))
        except MemoryError:
            meta.update(state="error", error="Mémoire insuffisante : choisissez la qualité Brouillon, ou moins de photos.")
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Scan 3D")
            meta.update(state="error", error="La reconstruction a échoué. Reprenez les photos (plus nombreuses, plus nettes).")
        finally:
            shutil.rmtree(os.path.join(work, "images"), ignore_errors=True)
            try:
                os.remove(os.path.join(work, "points.db"))
            except OSError:
                pass
            with self._lock:
                self._run = None
                try:
                    self._save(sid, meta)
                except (OSError, security.ValidationError):
                    pass

    # ------------------------------------------------------------ deux faces (5.8.7)
    def other_face(self, sid):
        """Nouveau scan pour la pièce retournée, lié au premier."""
        meta = self._meta(sid)
        out = self.create((meta["title"][:48] + " · dessous"))
        with self._lock:
            m2 = self._meta(out["id"])
            m2["face_of"] = sid
            self._save(out["id"], m2)
        return self.status(out["id"])

    def merge(self, sid, other):
        """Combine le scan `sid` (pièce posée normalement) avec `other` (pièce retournée), en tâche de fond.
        Le résultat va dans le dossier de `sid`."""
        from backend.design import photo_scan
        if sid == other:
            raise security.ValidationError("Choisissez l'autre scan, celui de la pièce retournée.")
        paths = []
        for x in (sid, other):
            st = self.status(x)
            if not st["result"]:
                raise security.ValidationError(f"« {st['title']} » n'a pas encore de résultat : lancez sa reconstruction.")
            if not (st.get("info") or {}).get("scaled"):
                raise security.ValidationError(f"« {st['title']} » n'est pas à l'échelle : pour combiner deux faces, la "
                                               "feuille de repère doit se voir sur les deux séries.")
            paths.append(os.path.join(self._dir(x), "resultat.npz"))
        with self._lock:
            if self._run:
                raise security.ValidationError("Un calcul est déjà en cours : attendez qu'il se termine ou annulez-le.")
            run = {"id": sid, "cancel": threading.Event(), "pct": 5, "msg": "Recalage des deux faces…"}
            self._run = run                  # l'état « en calcul » vient de _run : rien d'écrit dans scan.json
        other_title = self._meta(other)["title"]

        def work():
            meta = self._meta(sid)

            def progress(pct, msg=""):
                if pct is not None:
                    run["pct"] = max(run["pct"], min(99, int(pct)))
                if msg:
                    run["msg"] = msg
                    run["pct"] = min(95, run["pct"] + 3)
            try:
                top, bottom = (photo_scan.load_mesh(p) for p in paths)
                man, info = photo_scan.merge_faces(top, bottom, progress, run["cancel"].is_set)
                photo_scan.save_mesh(man, os.path.join(self._dir(sid), MERGED))
                meta.update(merged=dict(info, other=other, other_title=other_title, at=_now()), msg=None, merge_msg=None)
            except photo_scan.Cancelled:
                meta.update(merge_msg="Combinaison annulée.")
            except security.ValidationError as e:
                meta.update(merge_msg=str(e))
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Scan 3D : deux faces")
                meta.update(merge_msg="La combinaison des deux faces a échoué.")
            finally:
                with self._lock:
                    self._run = None
                    try:
                        self._save(sid, meta)
                    except (OSError, security.ValidationError):
                        pass
        t = threading.Thread(target=work, name="scan-3d-faces", daemon=True)
        run["thread"] = t
        t.start()
        return self.status(sid)

    # ------------------------------------------------------------ résultat
    def _finished(self, sid, opts):
        """Volume du scan avec les réglages du panneau (taille, lissage, base, faces) → (meta, info, manifold)."""
        from backend.design import photo_scan
        opts = opts if isinstance(opts, dict) else {}
        path = os.path.join(self._dir(sid), "resultat.npz")
        if not os.path.exists(path):
            raise security.ValidationError("Ce scan n'a pas encore de résultat : lancez la reconstruction.")
        meta = self._meta(sid)
        both = os.path.join(self._dir(sid), MERGED)
        if meta.get("merged") and os.path.exists(both) and str(opts.get("faces", "2")) == "2":
            path = both
        info = dict(meta.get("info") or {})
        return meta, info, photo_scan.finish(photo_scan.load_mesh(path), bool(info.get("scaled")), opts)

    def open_result(self, sid, opts=None):
        meta, info, man = self._finished(sid, opts)
        if self._store is None:
            raise security.ValidationError("Conception indisponible.")
        return self._store(meta["title"], man, info)

    def export_stl(self, sid, opts=None):
        """5.8.9 : le .stl directement, sans passer par l'aperçu 3D. Écrit dans le dossier du scan."""
        from backend.design import geom
        meta, _info, man = self._finished(sid, opts)
        if man.is_empty():
            raise security.ValidationError("Le modèle est vide : relancez la reconstruction.")
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(meta.get("title") or "")).strip(" ._")[:60] or "scan"
        path = os.path.join(self._dir(sid), name + ".stl")
        with open(path + ".part", "wb") as f:
            f.write(geom.stl_bytes(geom.on_plate(man)))
        os.replace(path + ".part", path)
        lo, hi = man.bounding_box()[:3], man.bounding_box()[3:]
        return {"path": path, "folder": self._dir(sid), "name": os.path.basename(path),
                "size_mm": [round(hi[k] - lo[k], 1) for k in range(3)], "triangles": int(man.num_tri())}

    # ------------------------------------------------------------ côté téléphone
    def phone_find(self):
        return {"pcs": scan_link.discover()}

    def phone_pair(self, host, port, code):
        return self._sender.pair(host, port, code)

    def phone_send(self, raw):
        return self._sender.send(raw)

    def phone_done(self):
        return self._sender.done()

    def phone_status(self):
        return self._sender.status()
