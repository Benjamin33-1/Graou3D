"""5.8.3 : pièce cassée recréée à partir de photos posées sur la feuille de repère.

Les photos sont fabriquées (tests/synth_photo.py) : un téléphone virtuel (focale, hauteur, inclinaison), la feuille
imprimée et une pièce de cotes connues, avec flou, bruit et JPEG. On vérifie la précision annoncée, la correction
d'épaisseur (sans elle l'erreur atteint plusieurs mm), les diamètres normalisés, les trois familles de pièces, la 3D,
le fichier OpenSCAD (mêmes volumes qu'OpenSCAD quand il est installé) et le branchement dans l'interface."""
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import security  # noqa: E402
from backend.design import geom  # noqa: E402

HAVE = geom.mf is not None and geom.np is not None
if HAVE:
    import numpy as np  # noqa: E402

    import synth_photo as sp  # noqa: E402
    from backend.design import photo_repair as pr  # noqa: E402
    from backend.design import service  # noqa: E402

FIT = {"press": 0.10, "slide": 0.20, "free": 0.35}
SIZE = (3200, 2400)
_CACHE = {}


def photo(key):
    """Photos de synthèse, fabriquées une seule fois pour tout le fichier."""
    if key in _CACHE:
        return _CACHE[key]
    holes = [sp.circle(90, 110, 2.5), sp.circle(140, 110, 2.5), sp.circle(115, 126, 4.0)]
    plate = sp.plate_outline(80, 100, 70, 40)
    if key.startswith("plate-"):         # plate-<épaisseur>-<inclinaison>-<orientation>
        _k, t, tilt, yaw = key.split("-")
        cam = sp.Camera(size=SIZE, height=380, tilt=float(tilt), yaw=float(yaw))
        raw = sp.render(cam, plate, holes, height=float(t))
    elif key == "plate97":
        raw = sp.render(sp.Camera(size=SIZE, height=380), plate, holes, height=4, print_scale=0.97)
    elif key == "light":
        raw = sp.render(sp.Camera(size=SIZE, height=380, tilt=5), plate, holes, height=4, color=(235, 235, 228), dark=True)
    elif key == "noexif":
        raw = sp.render(sp.Camera(size=SIZE, height=380, tilt=12, yaw=60), plate, holes, height=6, exif=False)
    elif key == "steep":
        raw = sp.render(sp.Camera(size=SIZE, height=380, tilt=35), plate, holes, height=4)
    elif key == "nosheet":
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200), (180, 170, 160)).save(buf, "JPEG")
        raw = buf.getvalue()
    elif key == "bracket-top":
        x0, y0 = 75, 110
        raw = sp.render(sp.Camera(size=SIZE, height=420, tilt=7, yaw=200), sp.plate_outline(x0, y0, 60, 30),
                        [sp.circle(x0 + 30, y0 + 15, 2.25), sp.circle(x0 + 50, y0 + 15, 2.25)], height=4,
                        more=[(sp.plate_outline(x0, y0, 5, 30), 35)], color=(70, 70, 75))
    elif key == "bracket-side":
        prof = np.array([[0, 0], [60, 0], [60, 4], [5, 4], [5, 35], [0, 35]], float)
        raw = sp.render(sp.Camera(size=SIZE, height=420, tilt=5, yaw=20), prof + [75, 130], [], height=30, color=(70, 70, 75))
    elif key == "knob":
        faces = sp.lying_round(95, 150, [(0, 15), (12, 15), (12.001, 10), (20, 10)])
        raw = sp.render(sp.Camera(size=SIZE, height=400, tilt=6, yaw=70), np.zeros((0, 2)), [], height=0, faces3d=faces,
                        color=(180, 40, 40))
    else:
        raise KeyError(key)
    _CACHE[key] = raw
    return raw


def analyzed(key, **kw):
    ck = ("state", key, tuple(sorted(kw.items())))
    if ck not in _CACHE:
        _CACHE[ck] = pr.analyze(photo(key), **kw)
    st, info = _CACHE[ck]
    return dict(st), info


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class SheetTests(unittest.TestCase):
    def test_thirty_dots_one_big_corner_inside_a4(self):
        dots = pr.sheet_dots()
        self.assertEqual(len(dots), 30)
        self.assertEqual(dots[0], (15.0, 15.0, pr.BIG_D))
        self.assertEqual(sum(1 for d in dots if d[2] == pr.BIG_D), 1)
        self.assertEqual(len({(x, y) for x, y, _d in dots}), 30)
        for x, y, d in dots:                         # marges d'impression (5 mm) respectées
            self.assertGreaterEqual(min(x - d / 2, y - d / 2), 5)
            self.assertLessEqual(x + d / 2, pr.SHEET[0] - 5)
            self.assertLessEqual(y + d / 2, pr.SHEET[1] - 5)
        x0, y0, x1, y1 = pr.ZONE                     # le cadre ne touche aucune pastille
        for x, y, d in dots:
            self.assertTrue(x + d / 2 < x0 or x - d / 2 > x1 or y + d / 2 < y0 or y - d / 2 > y1)

    def test_pdf_is_a4_with_control_line(self):
        for dark in (False, True):
            raw = pr.sheet_pdf(dark)
            self.assertTrue(raw.startswith(b"%PDF"))
            self.assertIn(b"595.28", raw)
            self.assertIn(b"841.89", raw)
        self.assertEqual(pr.CONTROL[1] - pr.CONTROL[0], 100.0)

    def test_service_writes_the_sheet(self):
        tmp = tempfile.mkdtemp()
        try:
            out = service.DesignService(tmp).photo_sheet(dark=True)
            self.assertTrue(os.path.isfile(out["path"]))
            self.assertIn("cadre noir", out["file"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class LocateTests(unittest.TestCase):
    def test_sheet_and_phone_position_found(self):
        for key, height, tilt in (("plate-3-0-0", 380, 0), ("plate-10-15-30", 380, 15), ("plate-6-20-120", 380, 20)):
            img, f35 = pr.load_photo(photo(key))
            s = pr.locate_sheet(img)
            self.assertEqual(s["matched"], 30, key)
            self.assertLess(s["rms_mm"], 0.05, key)
            f, src = pr.focal_px(s["H"], img.size, f35)
            self.assertEqual(src, "exif")
            cam = pr.camera(s["H"], img.size, f)
            self.assertAlmostEqual(cam["height"], height * math.cos(math.radians(tilt)), delta=3)
            self.assertAlmostEqual(cam["tilt"], tilt, delta=0.5)

    def test_focal_from_perspective_without_exif(self):
        img, f35 = pr.load_photo(photo("noexif"))
        self.assertIsNone(f35)
        s = pr.locate_sheet(img)
        f, src = pr.focal_px(s["H"], img.size, None)
        self.assertEqual(src, "perspective")
        true = 26.0 * math.hypot(*SIZE) / 43.2666
        self.assertLess(abs(f - true) / true, 0.05)

    def test_homography_round_trip(self):
        H = np.array([[2.1, 0.3, 50], [-0.2, 1.9, 80], [1e-4, 2e-4, 1]])
        src = np.array([[0, 0], [100, 0], [100, 50], [0, 50], [33, 21]], float)
        self.assertLess(np.abs(pr.apply_h(pr.homography(src, pr.apply_h(H, src)), src) - pr.apply_h(H, src)).max(), 1e-6)

    def test_clear_errors(self):
        with self.assertRaises(security.ValidationError) as e:
            pr.analyze(photo("nosheet"))
        self.assertIn("Feuille de repère", str(e.exception))
        with self.assertRaises(security.ValidationError) as e:
            pr.analyze(photo("steep"))
        self.assertIn("penché", str(e.exception))
        with self.assertRaises(security.ValidationError):
            pr.analyze(b"pas une image")


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class PlateTests(unittest.TestCase):
    def measure(self, key, t, **kw):
        st, info = analyzed(key, **kw)
        return pr.features(pr.shape_plate(st, t, {})["cs"], FIT), info

    def test_dimensions_within_a_quarter_mm(self):
        for key, t in (("plate-3-0-0", 3), ("plate-10-0-0", 10), ("plate-10-15-30", 10), ("plate-6-20-120", 6)):
            f, info = self.measure(key, t)
            o = f["outline"]
            self.assertEqual(o["type"], "rect", key)
            self.assertEqual(o["r"], 0.0, key)
            self.assertAlmostEqual(o["L"], 70, delta=0.25, msg=key)
            self.assertAlmostEqual(o["W"], 40, delta=0.25, msg=key)
            ds = sorted(h["d"] for h in f["holes"])
            self.assertEqual(len(ds), 3, key)
            for got, true in zip(ds, (5, 5, 8)):
                self.assertAlmostEqual(got, true, delta=0.2, msg=key)
            spans = sorted(s["mm"] for s in f["spacings"])
            self.assertAlmostEqual(spans[-1], 50, delta=0.2, msg=key)
            self.assertLessEqual(info["quality"]["uncertainty_mm"], 0.35)

    def test_without_thickness_correction_error_is_millimetres(self):
        st, _ = analyzed("plate-10-0-0")
        f = pr.features(pr.shape_plate(st, 0.2, {})["cs"], FIT)
        self.assertGreater(f["size"][0] - 70, 1.5)          # voilà pourquoi l'épaisseur est demandée

    def test_thickness_is_required(self):
        st, _ = analyzed("plate-3-0-0")
        with self.assertRaises(security.ValidationError):
            pr.shape_plate(st, 0, {})

    def test_sheet_printed_at_97_percent(self):
        st, info = analyzed("plate97", control_mm=97)
        f = pr.features(pr.shape_plate(st, 4, {})["cs"], FIT)
        self.assertAlmostEqual(f["size"][0], 70, delta=0.3)
        st, info = analyzed("plate97")
        f = pr.features(pr.shape_plate(st, 4, {})["cs"], FIT)
        self.assertGreater(f["size"][0] - 70, 1.5)

    def test_light_part_on_dark_frame(self):
        st, info = analyzed("light", dark=True)
        f = pr.features(pr.shape_plate(st, 4, {})["cs"], FIT)
        self.assertAlmostEqual(f["size"][0], 70, delta=0.3)
        self.assertEqual(len(f["holes"]), 3)

    def test_warning_without_exif(self):
        st, info = analyzed("noexif")
        self.assertEqual(info["quality"]["focal"], "perspective")
        f = pr.features(pr.shape_plate(st, 6, {})["cs"], FIT)
        self.assertAlmostEqual(f["size"][0], 70, delta=0.3)

    def test_preview_for_the_interface(self):
        st, info = analyzed("plate-3-0-0")
        self.assertTrue(info["preview"]["jpeg"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(info["preview"]["contours"])
        self.assertEqual(info["quality"]["dots"], 30)


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class HoleTests(unittest.TestCase):
    def first(self, d, fit=None):
        opts, choice = pr.hole_options(d, fit or FIT)
        return next(o for o in opts if o["key"] == choice), opts

    def test_screw_clearance_uses_calibrated_fit(self):
        o, _ = self.first(3.38)
        self.assertEqual(o["key"], "screw:3")
        self.assertAlmostEqual(o["printed"], 3.7)
        o, _ = self.first(3.38, {"press": 0.05, "slide": 0.1, "free": 0.2})
        self.assertAlmostEqual(o["printed"], 3.4)
        self.assertEqual(self.first(3.2)[0]["key"], "screw:3")
        self.assertEqual(self.first(4.5)[0]["key"], "screw:4")

    def test_bearing_shaft_self_tap_and_fallback(self):
        o, _ = self.first(22.05)
        self.assertEqual(o["key"], "bearing:22")
        self.assertIn("608", o["label"])
        self.assertAlmostEqual(o["printed"], 22.2)
        self.assertEqual(self.first(5.02)[0]["key"], "shaft:5")
        self.assertEqual(self.first(2.55)[0]["key"], "self_tap:3")
        o, opts = self.first(17.5)
        self.assertEqual(o["key"], "measured")
        self.assertEqual(opts[-1]["key"], "measured")

    def test_heads_for_countersink_and_counterbore(self):
        o, _ = self.first(3.38)
        self.assertAlmostEqual(o["heads"]["csk"], 6.4)
        self.assertAlmostEqual(o["heads"]["cbore"], 6.5)

    def test_circle_and_slot_fits(self):
        cx, cy, r, rms = pr.fit_circle(sp.circle(3, -2, 4.2))
        self.assertAlmostEqual(cx, 3, places=4)
        self.assertAlmostEqual(r, 4.2, places=3)
        slot = geom.circle(2, 64).translate((-5, 0)) + geom.circle(2, 64).translate((5, 0)) + geom.mf.CrossSection.square((10, 4), center=True)
        s = pr.fit_slot(np.asarray(slot.rotate(30).translate((7, 9)).to_polygons()[0]))
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["length"], 14, delta=0.05)
        self.assertAlmostEqual(s["width"], 4, delta=0.05)
        self.assertAlmostEqual(s["angle"], 30, delta=0.5)
        self.assertAlmostEqual(s["x"], 7, delta=0.05)
        self.assertIsNone(pr.fit_slot(np.asarray(geom.mf.CrossSection.square((10, 4)).to_polygons()[0])))


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class SteppedAndRoundTests(unittest.TestCase):
    def test_bracket_from_two_photos(self):
        top, _ = analyzed("bracket-top")
        side, _ = analyzed("bracket-side")
        g = pr.shape_stepped(top, side, 0, {})
        L, W = pr._size(g["cs"])
        self.assertAlmostEqual(L, 60, delta=0.3)
        self.assertAlmostEqual(W, 30, delta=0.3)
        self.assertAlmostEqual(g["thickness"], 35, delta=0.3)
        self.assertAlmostEqual(g["length_side"], 60, delta=0.3)
        f = pr.features(g["cs"], FIT)
        self.assertEqual(len(f["holes"]), 2)
        for h in f["holes"]:
            self.assertAlmostEqual(h["d"], 4.5, delta=0.25)
        # mauvais sens du profil choisi à la main : les deux longueurs ne collent plus
        bad = pr.shape_stepped(dict(top), dict(side), 0, {}, False, False)
        self.assertGreater(abs(bad["length_top"] - bad["length_side"]), 1.0)

    def test_knob_from_one_photo(self):
        side, _ = analyzed("knob")
        g = pr.shape_revolve(side)
        prof = np.array(g["rprofile"])
        self.assertAlmostEqual(g["dmax"], 30, delta=0.3)
        self.assertAlmostEqual(g["length"], 20, delta=0.4)
        z = np.linspace(0, g["length"], 401)
        r = np.interp(z, prof[1:-1, 1], prof[1:-1, 0])
        if r[5] > 12.5:                         # profil dans l'autre sens : le petit diamètre en haut
            z, r = g["length"] - z[::-1], r[::-1]
        step = z[np.argmax(r > 12.5)]
        self.assertAlmostEqual(step, 8, delta=0.4)
        small = r[(z > 0.5) & (z < step - 0.5)]
        big = r[(z > step + 0.5) & (z < g["length"] - 0.5)]
        self.assertLess(np.abs(small - 10).max(), 0.2)
        self.assertLess(np.abs(big - 15).max(), 0.2)
        self.assertLess(g["wobble"], 0.2)
        flipped = pr.shape_revolve(dict(side), flip_z=True)
        self.assertAlmostEqual(flipped["length"], g["length"], delta=1e-6)


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class PlanBuildTests(unittest.TestCase):
    def plate_geo(self):
        st, _ = analyzed("plate-3-0-0")
        sh = pr.shape_plate(st, 3, {})
        return {"cs": sh["cs"], "features": pr.features(sh["cs"], FIT), "thickness": 3, "uncertainty": 0.2}

    def test_plate_plan_build_and_gauge(self):
        g = self.plate_geo()
        plan = pr.make_plan("plate", g, {})
        self.assertEqual(plan["outline"]["type"], "rect")
        self.assertEqual(len(plan["holes"]), 3)
        man = pr.build(plan)
        holes = sum(math.pi * (h["d"] / 2) ** 2 for h in plan["holes"]) * 3
        self.assertAlmostEqual(man.volume(), plan["outline"]["L"] * plan["outline"]["W"] * 3 - holes, delta=5)
        gauge = pr.gauge(plan)
        self.assertEqual(len(gauge), 1)
        lo, hi = geom.bounds(gauge[0][1])
        self.assertAlmostEqual(hi[2] - lo[2], 1.2, places=3)

    def test_user_choices_rect_holes_and_sinks(self):
        g = self.plate_geo()
        f = g["features"]
        holes = [{"x": h["x"], "y": h["y"], "d": 3.7, "label": "M3", "sink": "csk", "head_d": 6.4} for h in f["holes"]]
        holes.append({"x": f["bounds"][0] + 5, "y": f["bounds"][1] + 5, "d": 2.5})
        holes.append({"x": 1, "y": 1, "d": 3, "keep": False})
        plan = pr.make_plan("plate", g, {"holes": holes, "rect": {"L": 80, "r": 4}, "thickness": 5, "name": "Patte"})
        self.assertEqual(plan["outline"]["L"], 80)
        self.assertEqual(plan["outline"]["r"], 4)
        self.assertEqual(plan["thickness"], 5)
        self.assertEqual(len(plan["holes"]), 4)
        self.assertEqual(plan["holes"][0]["sink"], "csk")
        xs = sorted(h["x"] for h in plan["holes"][:3])
        self.assertGreater(xs[-1] - xs[0], 50 * 80 / 70 - 0.5)          # les trous suivent la longueur retouchée
        man = pr.build(plan)
        lo, hi = geom.bounds(man)
        self.assertAlmostEqual(hi[0] - lo[0], 80, delta=0.01)
        self.assertAlmostEqual(hi[2] - lo[2], 5, delta=0.01)

    def test_edits_mirror_add_remove(self):
        st, _ = analyzed("plate-3-0-0")
        base = pr.shape_plate(st, 3, {})["cs"]
        b = base.bounds()
        L = b[2] - b[0]
        mir = pr.shape_plate(st, 3, {"mirror": {"axis": "x", "pos": b[2]}})["cs"]
        self.assertAlmostEqual(pr._size(mir)[0], 2 * L, delta=0.05)
        add = pr.shape_plate(st, 3, {"add": [[[b[2], b[1]], [b[2] + 10, b[1]], [b[2] + 10, b[1] + 10], [b[2], b[1] + 10]]]})["cs"]
        self.assertAlmostEqual(add.area() - base.area(), 100, delta=1)
        rem = pr.shape_plate(st, 3, {"remove": [[[b[0] - 1, b[1] - 1], [b[0] + 10, b[1] - 1], [b[0] + 10, b[1] + 10], [b[0] - 1, b[1] + 10]]]})["cs"]
        self.assertAlmostEqual(base.area() - rem.area(), 100, delta=1.5)

    def test_scad_has_named_dimensions(self):
        plan = pr.make_plan("plate", self.plate_geo(), {})
        text = pr.scad(plan, "5.8.3")
        for needle in ("epaisseur = 3;", "longueur = ", "largeur = ", "rayon_angles = ", "trou_1 = [", "$fn = 96;",
                       "linear_extrude(height = epaisseur)", "Graou3D 5.8.3"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("("), text.count(")"))
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertEqual(text.count("["), text.count("]"))

    def test_bad_values_are_clamped(self):
        g = self.plate_geo()
        plan = pr.make_plan("plate", g, {"holes": [{"x": "a", "y": None, "d": 1e9}, "x", {"d": -3}], "thickness": -4,
                                        "rect": {"L": "abc"}})
        self.assertLessEqual(plan["holes"][0]["d"], 200)
        self.assertEqual(len(plan["holes"]), 1)
        self.assertGreater(plan["thickness"], 0)
        pr.build(plan)


def _openscad():
    exe = shutil.which("openscad")
    return exe


def _stl_volume(path):
    import struct
    data = open(path, "rb").read()
    if data[:5] == b"solid":
        v = np.array([list(map(float, m)) for m in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)]).reshape(-1, 3, 3)
    else:
        n = struct.unpack("<I", data[80:84])[0]
        dt = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
        v = np.frombuffer(data[84:84 + n * 50], dtype=dt)["v"].reshape(-1, 3, 3).astype(float)
    return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6)


@unittest.skipUnless(HAVE and _openscad(), "OpenSCAD absent : comparaison des volumes sautée")
class OpenScadTests(unittest.TestCase):
    def check(self, plan):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "p.scad")
            out = os.path.join(tmp, "p.stl")
            with open(src, "w", encoding="utf-8") as f:
                f.write(pr.scad(plan, "test"))
            subprocess.run([_openscad(), "-o", out, src], capture_output=True, timeout=600, check=True)
            self.assertAlmostEqual(_stl_volume(out) / pr.build(plan).volume(), 1.0, delta=0.002)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_three_families_match_openscad(self):
        st, _ = analyzed("plate-3-0-0")
        sh = pr.shape_plate(st, 3, {})
        geo = {"cs": sh["cs"], "features": pr.features(sh["cs"], FIT), "thickness": 3}
        f = geo["features"]
        holes = [{"x": f["holes"][0]["x"], "y": f["holes"][0]["y"], "d": 3.7, "sink": "csk", "head_d": 6.4},
                 {"x": f["holes"][1]["x"], "y": f["holes"][1]["y"], "d": 3.7, "sink": "cbore", "head_d": 6.5, "head_depth": 1.5,
                  "side": "bottom"}]
        self.check(pr.make_plan("plate", geo, {"holes": holes, "rect": {"r": 3}}))
        top, _ = analyzed("bracket-top")
        side, _ = analyzed("bracket-side")
        g = pr.shape_stepped(top, side, 0, {})
        g.update({"features": pr.features(g["cs"], FIT)})
        self.check(pr.make_plan("stepped", g, {}))
        knob, _ = analyzed("knob")
        g = pr.shape_revolve(knob)
        self.check(pr.make_plan("revolve", g, {"axial": [{"d": 6, "depth": 10, "side": "bottom", "flat": 1.5}]}))


@unittest.skipUnless(HAVE, "numpy / manifold3d absents")
class ServiceTests(unittest.TestCase):
    def wait(self, svc, job):
        for _ in range(600):
            j = svc.job(job["id"])
            if j["status"] != "running":
                return j
            time.sleep(0.05)
        self.fail("tâche trop longue")

    def test_photo_to_part_and_scad_file(self):
        tmp = tempfile.mkdtemp()
        try:
            svc = service.DesignService(tmp)
            j = self.wait(svc, svc.photo_analyze(photo("plate-3-0-0"), {"role": "top"}))
            self.assertEqual(j["status"], "done", j.get("error"))
            pid = j["result"]["photo_id"]
            spec = {"family": "plate", "top": pid, "thickness": 3}
            m = svc.photo_measure(spec, FIT)
            self.assertEqual(m["family"], "plate")
            self.assertEqual(len(m["features"]["holes"]), 3)
            self.assertTrue(m["drawing"])
            j = self.wait(svc, svc.photo_build(spec, {"name": "Patte"}, FIT))
            self.assertEqual(j["status"], "done", j.get("error"))
            res = j["result"]
            self.assertEqual(res["kind"], "photo")
            self.assertEqual(res["name"], "Patte")
            out = svc.photo_scad(res["sid"], "5.8.3")
            self.assertTrue(out["path"].endswith(".scad"))
            self.assertIn("epaisseur = 3;", open(out["path"], encoding="utf-8").read())
            g = self.wait(svc, svc.photo_build(spec, {}, FIT, gauge=True))
            self.assertEqual(g["status"], "done")
            self.assertTrue(g["result"]["info"]["gauge"])
            with self.assertRaises(security.ValidationError):
                svc.photo_scad(g["result"]["sid"])               # le gabarit n'a pas de fichier OpenSCAD
            with self.assertRaises(security.ValidationError):
                svc.photo_measure({"family": "plate", "top": "inconnu", "thickness": 3}, FIT)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class InterfaceTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_api_ui_and_demo_are_wired(self):
        api = self.read("backend", "api.py")
        ui = self.read("ui", "js", "views", "design_photo.js")
        demo = self.read("ui", "js", "demo_api.js")
        for name in ("design_photo_sheet", "design_photo_analyze", "design_photo_measure", "design_photo_build", "design_photo_scad"):
            self.assertIn(f"def {name}(", api)
            self.assertIn(f"{name}: no", demo)
        for name in ("design_photo_analyze", "design_photo_measure", "design_photo_build", "design_photo_scad", "design_photo_sheet"):
            self.assertIn(f'"{name}"', ui)
        html = self.read("ui", "index.html")
        self.assertLess(html.index("js/views/design.js"), html.index("js/views/design_photo.js"))
        self.assertIn('["photo", "Pièce cassée", "wrench"]', self.read("ui", "js", "views", "design.js"))
        self.assertIn("BC.readFile(f)", ui)                     # photo d'origine : EXIF et pleine résolution

    def test_version(self):
        import config
        self.assertEqual(config.APP_VERSION, "5.8.9")


if __name__ == "__main__":
    unittest.main()
