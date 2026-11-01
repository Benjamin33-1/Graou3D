"""5.8.8 : IA de profondeur pour le scan 3D (Depth Anything V2 petite taille, téléchargement facultatif).
Le relief deviné est recalé sur ce qui a été mesuré et donne la silhouette de l'objet sur chaque photo ; un voxel
que plusieurs photos voient sur le fond est vide (vote : une photo mal placée ne casse pas la pièce)."""
import os
import sys
import tempfile
import shutil
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.design import depth_ai  # noqa: E402

try:
    import manifold3d  # noqa: F401
    from backend.design import photo_scan as ps
    import synth_scan as ss
    HAVE_GEOM = True
except ImportError:                     # pragma: no cover
    HAVE_GEOM = False

BALL = (105.0, -150.0, 15.0, 15.0)
LO, HI = np.array([80.0, -175, 0]), np.array([130.0, -125, 32])


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def truth(cam):
    """Profondeur exacte (composante z caméra) et masque de la boule pour une vue de synthèse."""
    W, H = cam["W"], cam["H"]
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    r = cam["R"].T @ (np.linalg.inv(cam["K"]) @ np.vstack([xs.ravel(), ys.ravel(), np.ones(W * H)]))
    C, c = cam["C"], np.array(BALL[:3])
    lam_p = np.where(r[2] < 0, -C[2] / np.where(r[2] < 0, r[2], -1), np.inf)
    a = (r * r).sum(0)
    b = 2 * (r.T @ (C - c))
    disc = b * b - 4 * a * ((C - c) @ (C - c) - BALL[3] ** 2)
    lam_b = np.where(disc > 0, (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a), np.inf)
    on = lam_b < lam_p
    return np.where(on, lam_b, lam_p).reshape(H, W), on.reshape(H, W)


def fake_disp(z):
    """Ce que rendrait l'IA : l'inverse de la profondeur, à un facteur et un décalage près, un peu déformé."""
    H, W = z.shape
    warp = 1 + 0.04 * (np.arange(W) / W)[None, :] - 0.03 * (np.arange(H) / H)[:, None]
    return (3.0 / z * warp + 0.002).astype(np.float32)


class FitTests(unittest.TestCase):
    def test_inverse_fit_ignores_outliers(self):
        rng = np.random.default_rng(0)
        z = rng.uniform(150, 300, 5000)
        r = (1 / z - 0.001) / 0.5
        z_meas = z.copy()
        z_meas[:400] *= rng.uniform(0.5, 2.0, 400)               # mesures fausses
        a, b = depth_ai.fit_inverse(r, z_meas)
        self.assertAlmostEqual(a, 0.5, delta=0.01)
        self.assertAlmostEqual(b, 0.001, delta=1e-4)

    def test_local_fit_removes_a_smooth_distortion(self):
        H, W = 120, 160
        true = 200 + 40 * np.linspace(0, 1, W)[None, :] * np.ones((H, 1))
        guess = true * (1 + 0.05 * np.sin(np.linspace(0, 3, H))[:, None])   # déformation lente
        ok = np.zeros((H, W), bool)
        ok[::3, ::3] = True
        ok[40:80, 60:100] = False                                  # trou sans mesure : extrapolé en douceur
        fixed = depth_ai.local_fit(guess, np.where(ok, true, 0), ok)
        before = np.median(np.abs(guess - true) / true)
        after = np.median(np.abs(fixed - true) / true)
        self.assertLess(after, before / 4)
        self.assertLess(np.abs(fixed - true)[40:80, 60:100].max() / 240, 0.02)


@unittest.skipUnless(HAVE_GEOM, "manifold3d absent")
class SilhouetteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cams = ss.ring((105, -150, 12), 140, (25, 50), 8, size=(400, 300))
        cls.views = ss.views(cls.cams)

    def masks(self, i, measured_frac=0.3):
        v = self.views[i]
        z, on = truth(v)
        rng = np.random.default_rng(i)
        measured = np.where(on & (rng.random(z.shape) < measured_frac), z, 0).astype(np.float32)
        lam, hit, P = ps.ground_rays(v)
        safe = hit & (np.hypot(P[0] - 105, P[1] + 150) > 22)
        m = depth_ai.view_masks(v, fake_disp(z), measured, lam.reshape(z.shape), safe.reshape(z.shape), LO, HI)
        return m, on

    def test_silhouette_matches_the_object(self):
        for i in (0, 9):
            m, on = self.masks(i)
            self.assertIsNotNone(m)
            iou = (m["obj"] & on).sum() / (m["obj"] | on).sum()
            self.assertGreater(iou, 0.85)
            self.assertFalse(np.any(m["bg"] & on))                   # la marge protège le bord de l'objet

    def test_unreliable_fit_is_refused(self):
        v = self.views[0]
        z, on = truth(v)
        lam, hit, P = ps.ground_rays(v)
        noise = np.random.default_rng(1).uniform(0.001, 0.02, z.shape).astype(np.float32)   # « IA » sans rapport
        m = depth_ai.view_masks(v, noise, np.where(on, z, 0).astype(np.float32), lam.reshape(z.shape),
                                hit.reshape(z.shape) & ~on, LO, HI)
        self.assertIsNone(m)

    def volume(self, sil):
        empty = [np.zeros((v["H"], v["W"]), np.float32) for v in self.views]
        sdf, step, axes = ps.fuse(self.views, empty, LO, HI, 60, sil=sil)
        man = ps.largest(ps.extract(sdf, step, axes))
        return 0.0 if man.is_empty() else man.volume()

    def test_silhouettes_carve_what_no_photo_measured(self):
        """Rien de mesuré (objet tout uni) : sans l'IA, toute la boîte reste pleine ; avec les silhouettes, il reste
        à peu près la boule (son enveloppe vue des photos)."""
        sil = []
        for v in self.views:
            _z, on = truth(v)
            sil.append((~on, on))
        ball = 4 / 3 * np.pi * 15 ** 3
        self.assertGreater(self.volume(None), 3 * ball)
        carved = self.volume(sil)
        self.assertGreater(carved, 0.85 * ball)
        self.assertLess(carved, 1.6 * ball)
        # une photo dont la silhouette est fausse (tout « fond ») ne vide pas la pièce : il faut un vote
        bad = list(sil)
        bad[3] = (np.ones_like(sil[3][0]), np.zeros_like(sil[3][1]))
        self.assertGreater(self.volume(bad), 0.85 * carved)

    def test_pipeline_step_without_usable_ai_changes_nothing(self):
        class Flat:                                                    # modèle qui ne voit rien (relief plat)
            def get_inputs(self):
                return [mock.Mock(name="x")]

            def run(self, _out, feed):
                return [np.ones((1, depth_ai.INPUT, depth_ai.INPUT), np.float32)]
        depths = [np.zeros((v["H"], v["W"]), np.float32) for v in self.views]
        self.assertIsNone(ps.depth_ai_views(self.views, depths, LO, HI, None, Flat()))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_optional_download_checked_and_removable(self):
        ai = depth_ai.DepthAI(os.path.join(self.tmp, "scan-ia"))
        st = ai.status()
        self.assertFalse(st["installed"])
        self.assertTrue(st["enabled"])
        self.assertIsNone(ai.session())

        def fake_download(url, dest, size, sha, progress, cancelled, max_bytes):
            self.assertTrue(url.startswith("https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/"))
            self.assertEqual(sha, depth_ai.SHA256)
            with open(dest, "wb") as f:
                f.write(b"x" * size)
        with mock.patch.object(depth_ai, "SIZE", 64), mock.patch("backend.graoully.local_runtime.download", fake_download):
            ai._install_work()
            self.assertEqual(ai.status()["install"]["step"], "done")
            self.assertTrue(ai.installed())
            self.assertIsNone(ai.session())                    # fichier illisible : le scan continue sans l'IA
        ai.set_enabled(False)
        self.assertFalse(depth_ai.DepthAI(ai.folder).enabled())
        ai.remove()
        self.assertFalse(os.path.exists(ai.path()))

    def test_scan_service_owns_the_ai(self):
        from backend.design import scan_service
        svc = scan_service.ScanService(self.tmp, None, os.path.join(self.tmp, "scan-ia"))
        self.assertIsInstance(svc.ai, depth_ai.DepthAI)
        self.assertIsNone(scan_service.ScanService(self.tmp).ai)
        self.assertIn("ai=sess", read("backend", "design", "scan_service.py"))


class InterfaceTests(unittest.TestCase):
    def test_api_demo_and_panel(self):
        api, demo, js = read("backend", "api.py"), read("ui", "js", "demo_api.js"), read("ui", "js", "views", "design_scan.js")
        for name in ("scan_ai_status", "scan_ai_install", "scan_ai_cancel", "scan_ai_enable", "scan_ai_remove"):
            self.assertIn(f"def {name}(", api)
            self.assertIn(f"{name}:", demo)
        for key in ('data-action="scan-ai-install"', 'data-action="scan-ai-enable"', 'data-action="scan-ai-remove"',
                    "IA de profondeur : ${i.depth_ai} photos"):
            self.assertIn(key, js)

    def test_license_is_commercial_friendly(self):
        lic = read("TIERS-LICENCES.txt")
        self.assertIn("Depth Anything V2", lic)
        self.assertIn("Apache", lic)
        self.assertIn("Small", read("backend", "design", "depth_ai.py"))

    def test_version(self):
        import config
        self.assertEqual(config.APP_VERSION, "5.8.9")


if __name__ == "__main__":
    unittest.main()
