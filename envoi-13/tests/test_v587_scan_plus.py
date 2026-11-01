"""5.8.7 : scan 3D plus robuste après un vrai essai (F1 miniature blanche, 24 photos d'un Pixel 10 Pro).
Le sol trouvé même quand l'objet a bien plus de points que la feuille blanche, les photos mal placées écartées,
la feuille reconnue avec moins de pastilles (plan recalculé sur les pastilles elles-mêmes), le bas de l'objet
gardé (emprise au sol), un pas de grille plus fin pour les objets plats, le lissage, la feuille A3, le scan en
deux faces, le panneau qui ne remonte plus tout seul."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import security  # noqa: E402

try:
    import manifold3d as m3d
    from backend.design import photo_scan as ps, photo_repair as pr
    import synth_scan as ss
    HAVE_GEOM = True
except ImportError:                     # pragma: no cover
    HAVE_GEOM = False
from backend.design import scan_service  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def noisy(man, sigma, seed=0, keep_below=None):
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties, np.float64)[:, :3].copy()
    rng = np.random.default_rng(seed)
    move = np.ones(len(v), bool) if keep_below is None else v[:, 2] > keep_below
    v[move] += rng.normal(0, sigma, (int(move.sum()), 3))
    return m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(v, np.float32),
                                 tri_verts=np.ascontiguousarray(mesh.tri_verts, np.uint32)))


@unittest.skipUnless(HAVE_GEOM, "numpy, Pillow, manifold3d")
class GroundTests(unittest.TestCase):
    def test_ground_is_under_the_object_even_with_few_paper_points(self):
        """Feuille blanche : 60 points au sol, 1500 sur le dessus plat d'une voiture. Le sol reste le sol."""
        rng = np.random.default_rng(1)
        floor = np.column_stack([rng.uniform(-100, 100, 60), rng.uniform(-100, 100, 60), rng.normal(0, 0.05, 60)])
        roof = np.column_stack([rng.uniform(-60, 60, 1500), rng.uniform(-20, 20, 1500), rng.normal(12, 0.05, 1500)])
        body = np.column_stack([rng.uniform(-60, 60, 400), rng.uniform(-20, 20, 400), rng.uniform(1, 12, 400)])
        a = np.linspace(0, 2 * np.pi, 16, endpoint=False)
        cams = np.column_stack([200 * np.cos(a), 200 * np.sin(a), np.full(16, 150.0)])
        cams[:2, 2] = -40.0                                   # deux photos mal placées, sous le sol
        n, d, _tol = ps.ground_plane(np.vstack([floor, roof, body]), cams)
        self.assertGreater(n[2], 0.999)
        self.assertLess(abs(d), 0.2)

    def test_dots_plane_ignores_false_blobs(self):
        rng = np.random.default_rng(2)
        X = [(x, 0.0, 0.0) for x in (0, 30, 60, 90)] + [(0.0, y, 0.0) for y in (30, 60, 90)]
        X = np.array(X) + rng.normal(0, 0.2, (7, 3))
        X = np.vstack([X, [[40, 40, 25.0], [70, 20, 14.0]]])             # pneus noirs vus comme des pastilles
        R = ps._rot("x", 12) @ ps._rot("z", 30)
        n, d = ps._dots_plane(X @ R.T, R @ np.array([0, 0.05, 1.0]) / np.linalg.norm([0, 0.05, 1.0]))
        self.assertGreater(abs(n @ (R @ [0, 0, 1.0])), 0.9995)

    def test_sheet_match_with_few_dots_on_two_edges(self):
        """7 pastilles autour d'un coin (A4) et 3 faux amas : reconnu ; un seul bord : refusé."""
        model = np.array([(x, y) for x, y, _d in pr.sheet_dots("A4")])
        pick = [3, 4, 5, 6, 7, 8, 9]                                 # haut droit : bord du haut + bord droit
        rng = np.random.default_rng(3)
        ang, s = 0.7, 0.021
        Rm = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
        P = (model[pick] @ Rm.T) * s
        P[:, 1] *= -1                                                  # repère du plan : v retourné
        P += rng.normal(0, 0.004, P.shape)
        cl = np.column_stack([P, np.full(len(P), 0.02)])
        junk = np.array([[1.5, -3.0, 0.02], [1.9, -2.2, 0.02], [0.4, -1.1, 0.02]])
        fit = ps._match_sheet(np.vstack([cl, junk]), 0.02)
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(len(fit[1]), 7)
        self.assertEqual(fit[3], "A4")
        line = cl[:4]                                                  # bord du haut seulement
        self.assertIsNone(ps._match_sheet(np.vstack([line, line + [0.0001, 0.0001, 0]]), 0.02))

    def test_a3_sheet_is_recognised(self):
        model = np.array([(x, y) for x, y, _d in pr.sheet_dots("A3")])
        self.assertEqual(len(model), 44)
        pick = list(range(6, 23))                                      # coin haut droit et grand bord (14 pastilles : plus long qu'une A4)
        P = model[pick] * 0.02
        P[:, 1] *= -1
        fit = ps._match_sheet(np.column_stack([P, np.full(len(P), 0.02)]), 0.02)
        self.assertIsNotNone(fit)
        self.assertEqual(fit[3], "A3")

    def test_sheet_pdfs(self):
        a4, a3 = pr.sheet_pdf(False), pr.sheet_pdf(True, "A3")
        self.assertTrue(a4.startswith(b"%PDF") and a3.startswith(b"%PDF"))
        self.assertIn(b"841.89", a3)                                   # largeur A3 = hauteur A4 (842 pt)
        self.assertIn(b"1190.55", a3)
        img = pr.sheet_image(2.0, False, "A3")
        self.assertEqual(img.size, (594, 840))
        self.assertEqual(len(pr.sheet_dots()), 30)                     # la feuille A4 de Pièce cassée ne change pas


@unittest.skipUnless(HAVE_GEOM, "numpy, Pillow, manifold3d")
class VolumeTests(unittest.TestCase):
    def test_flat_objects_get_a_finer_grid(self):
        self.assertAlmostEqual(ps.grid_step(np.array([80.0, 80, 80]), 220), 80 / 220)
        car = ps.grid_step(np.array([104.0, 190, 43]), 220)
        self.assertLess(car, 0.7 * 190 / 220)
        self.assertGreaterEqual(car, 190 / (1.6 * 220) - 1e-9)

    def test_footprint_blocks_ground_guess_under_the_object(self):
        """Sous l'objet et tout autour (marge), le sol n'est jamais deviné : une pièce sombre ou unie posée au ras de
        la feuille ne passe plus pour du papier."""
        cams = ss.ring((105, -150, 12), 140, (30, 55), 8, size=(200, 150))
        V = ss.views(cams)
        sel = np.column_stack([105 + 14 * np.cos(np.linspace(0, 6.28, 40)), -150 + 14 * np.sin(np.linspace(0, 6.28, 40)),
                               np.full(40, 15.0)])
        lo, hi = np.array([80, -175, 0.0]), np.array([130, -125, 36.0])
        fp = ps.footprint(sel, lo, hi, 30.0)
        self.assertTrue(ps.in_footprint(fp, np.array([105.0]), np.array([-150.0]))[0])
        self.assertTrue(ps.in_footprint(fp, np.array([105.0 + 14.5]), np.array([-150.0]))[0])   # marge
        self.assertTrue(ps.in_footprint(fp, np.array([105.0 + 5]), np.array([-150.0 - 6]))[0])  # dessus sans points
        self.assertFalse(ps.in_footprint(fp, np.array([105.0 + 24]), np.array([-150.0]))[0])
        self.assertFalse(ps.in_footprint(None, np.array([1.0]), np.array([1.0]))[0])
        empty = np.zeros((V[0]["H"], V[0]["W"]), np.float32)
        _d, g0 = ps.ground_fill(V, 0, [1, 7], empty)
        _d, g1 = ps.ground_fill(V, 0, [1, 7], empty, fp)
        v = V[0]
        ys, xs = np.nonzero(g1)
        rays = v["R"].T @ (np.linalg.inv(v["K"]) @ np.vstack([xs, ys, np.ones(len(xs))]))
        lam = -v["C"][2] / rays[2]
        P = v["C"][:, None] + rays * lam
        self.assertFalse(np.any(np.hypot(P[0] - 105, P[1] + 150) < 13))  # rien de deviné sous l'objet
        self.assertGreater(g0.sum(), g1.sum())

    def test_smoothing_removes_grain_and_keeps_a_flat_base(self):
        ball = m3d.Manifold.sphere(10, 96).translate([0, 0, 8]).trim_by_plane([0, 0, 1], 0.0)
        rough = noisy(ball, 0.12, keep_below=1.0)
        mesh = lambda m: np.asarray(m.to_mesh().vert_properties)[:, :3]

        def grain(m):
            v = mesh(m)
            up = v[:, 2] > 1.5
            return float(np.sqrt(np.mean((np.linalg.norm(v[up] - [0, 0, 8], axis=1) - 10) ** 2)))
        out = ps.finish(rough, True, {"smooth": "2"})
        self.assertLess(grain(out), 0.6 * grain(rough))
        self.assertAlmostEqual(out.volume(), ball.volume(), delta=0.02 * ball.volume())
        b = out.bounding_box()
        self.assertAlmostEqual(b[2], 0.0, places=4)                      # base toujours à plat sur le plateau
        raw = ps.finish(rough, True, {"smooth": "0"})
        self.assertAlmostEqual(raw.volume(), rough.volume(), places=3)
        self.assertEqual(set(ps.SMOOTH), {"0", "1", "2", "3"})


@unittest.skipUnless(HAVE_GEOM, "numpy, manifold3d")
class TwoFacesTests(unittest.TestCase):
    def test_flipped_scan_gives_back_the_real_underside(self):
        """Pièce avec une rainure dessus, une poche dessous et un trou : chaque scan bouche ce qu'il ne voit pas.
        Retournée, tournée de 37° et déplacée sur la feuille : la combinaison retrouve la vraie pièce."""
        C = m3d.Manifold.cube
        box = C([60, 40, 18])
        groove = C([50, 4, 3]).translate([4, 26, 15.01])
        hole = m3d.Manifold.cylinder(20, 3, 3, 48).translate([12, 12, 2])
        pocket = C([30, 20, 8]).translate([24, 6, -0.01])
        truth = box - groove - hole - pocket
        top = box - groove - hole
        flipped = (box - pocket - hole).rotate([180, 0, 0])
        flipped = flipped.translate((-np.array(flipped.bounding_box()[:3])).tolist())
        flipped = flipped.rotate([0, 0, 37]).translate([100, -40, 0])
        out, info = ps.merge_faces(top, flipped)
        wrong = (out - truth).volume() + (truth - out).volume()
        self.assertLess(wrong, 0.01 * truth.volume())
        self.assertLess(info["rms_mm"], 0.8)

    def test_two_different_parts_are_refused(self):
        a = m3d.Manifold.cube([60, 40, 18])
        b = m3d.Manifold.cylinder(30, 12, 12, 64)
        with self.assertRaises(security.ValidationError):
            ps.merge_faces(a, b)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.stored = []
        self.svc = scan_service.ScanService(self.dir, lambda name, man, info: self.stored.append((name, man, info)) or {"sid": "x"})

    def test_other_face_is_linked_and_merge_needs_scaled_results(self):
        a = self.svc.create("Bielle")
        b = self.svc.other_face(a["id"])
        self.assertEqual(b["face_of"], a["id"])
        self.assertIn("dessous", b["title"])
        self.assertTrue(os.path.isdir(b["folder"]))
        with self.assertRaises(security.ValidationError):              # pas encore de résultat
            self.svc.merge(a["id"], b["id"])
        with self.assertRaises(security.ValidationError):
            self.svc.merge(a["id"], a["id"])

    @unittest.skipUnless(HAVE_GEOM, "manifold3d")
    def test_merge_runs_in_background_and_open_uses_it(self):
        C = m3d.Manifold.cube
        box, hole = C([60, 40, 18]), m3d.Manifold.cylinder(20, 3, 3, 48).translate([12, 12, 2])
        pocket = C([30, 20, 8]).translate([24, 6, -0.01])
        flipped = (box - pocket - hole).rotate([180, 0, 0])
        flipped = flipped.translate((-np.array(flipped.bounding_box()[:3])).tolist()).rotate([0, 0, 20])
        ids = []
        for title, man in (("Pièce", box - hole), ("Pièce · dessous", flipped)):
            sid = self.svc.create(title)["id"]
            ps.save_mesh(man, os.path.join(self.svc.root, sid, "resultat.npz"))
            meta = self.svc._meta(sid)
            meta.update(state="done", info={"scaled": True})
            self.svc._save(sid, meta)
            ids.append(sid)
        st = self.svc.merge(ids[0], ids[1])
        self.assertEqual(st["state"], "running")
        self.svc._run["thread"].join(240)
        st = self.svc.status(ids[0])
        self.assertEqual(st["state"], "done")
        self.assertTrue(st["merged"], st.get("merge_msg"))
        self.assertEqual(st["merged"]["other_title"], "Pièce · dessous")
        self.svc.open_result(ids[0], {"smooth": "0"})
        both = self.stored[-1][1].volume()
        self.svc.open_result(ids[0], {"smooth": "0", "faces": "1"})
        alone = self.stored[-1][1].volume()
        self.assertAlmostEqual(alone - both, pocket.volume(), delta=0.03 * pocket.volume())
        # relancer la reconstruction du premier scan oublie l'ancienne combinaison
        meta = self.svc._meta(ids[0])
        meta["merged"] = st["merged"]
        os.remove(os.path.join(self.svc.root, ids[0], scan_service.MERGED))
        self.assertIsNone(self.svc.status(ids[0])["merged"])

    def test_merge_refuses_unscaled_scans(self):
        if not HAVE_GEOM:
            self.skipTest("manifold3d")
        ids = []
        for t in ("A", "B"):
            sid = self.svc.create(t)["id"]
            ps.save_mesh(m3d.Manifold.cube([10, 10, 10]), os.path.join(self.svc.root, sid, "resultat.npz"))
            meta = self.svc._meta(sid)
            meta.update(state="done", info={"scaled": False})
            self.svc._save(sid, meta)
            ids.append(sid)
        with self.assertRaises(security.ValidationError) as e:
            self.svc.merge(*ids)
        self.assertIn("échelle", str(e.exception))


class InterfaceTests(unittest.TestCase):
    def test_api_and_demo(self):
        api, demo = read("backend", "api.py"), read("ui", "js", "demo_api.js")
        for name in ("scan_other_face", "scan_merge", "scan_open_folder"):
            self.assertIn(f"def {name}(", api)
            self.assertIn(f"{name}:", demo)
        self.assertIn('fmt if fmt in ("A4", "A3")', api)

    def test_scan_tab(self):
        js = read("ui", "js", "views", "design_scan.js")
        for key in ('data-action="scan-faces"', 'data-action="scan-other-face"', 'data-action="scan-merge"',
                    'data-action="scan-sheet" data-f="A3"', 'data-action="scan-folder"', 'data-action="scan-smooth"',
                    "faces: String(o.faces)", "smooth: String(o.smooth)", "cadre noir"):
            self.assertIn(key, js)

    def test_phone_asks_for_the_low_round(self):
        js = read("ui", "js", "views", "scan.js")
        self.assertIn('[0, 25, "Au ras de la table"]', js)
        self.assertIn("téléphone presque posé", js)

    def test_panels_keep_their_scroll_on_refresh(self):
        js = read("ui", "js", "app.js")
        self.assertIn("scrolled.forEach", js)
        self.assertLess(js.index("const scrolled"), js.index("BC.set(root, view.render())"))

    def test_version(self):
        import config
        self.assertEqual(config.APP_VERSION, "5.8.9")


if __name__ == "__main__":
    unittest.main()
