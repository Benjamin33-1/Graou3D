"""5.8.9 : un scan très détaillé (personnage en qualité Fine) s'ouvre : il est simplifié sous la limite de l'aperçu.
Et le .stl se télécharge directement depuis l'onglet Scan 3D, sans passer par l'aperçu."""
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import manifold3d as m3d
    from backend.design import photo_scan as ps, geom
    HAVE_GEOM = True
except ImportError:                     # pragma: no cover
    HAVE_GEOM = False


@unittest.skipUnless(HAVE_GEOM, "manifold3d absent")
class HeavyScanTests(unittest.TestCase):
    def test_heavy_result_is_lightened_without_changing_the_shape(self):
        ball = m3d.Manifold.sphere(30, 260).translate([0, 0, 30])
        self.assertGreater(ball.num_tri(), 30_000)
        with mock.patch.object(ps, "MAX_TRIANGLES", 10_000):
            out = ps.finish(ball, True, {"smooth": "0"})
        self.assertLessEqual(out.num_tri(), 10_000)
        self.assertAlmostEqual(out.volume(), ball.volume(), delta=0.01 * ball.volume())
        geom.payload([{"man": out}], max_triangles=10_000)          # l'aperçu l'accepte

    def test_light_result_untouched(self):
        ball = m3d.Manifold.sphere(10, 48)
        self.assertIs(ps.lighten(ball), ball)

    def test_limit_below_preview(self):
        self.assertLess(ps.MAX_TRIANGLES, 600_000)

    def test_stl_download_without_preview(self):
        from backend.design import scan_service
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        svc = scan_service.ScanService(tmp)                      # sans Conception : l'export marche quand même
        sid = svc.create("Mon perso / test")["id"]
        meta = svc._meta(sid)
        meta.update(state="done", info={"scaled": True})
        svc._save(sid, meta)
        ps.save_mesh(m3d.Manifold.sphere(20, 64).translate([0, 0, 20]), os.path.join(svc._dir(sid), "resultat.npz"))
        out = svc.export_stl(sid, {"smooth": "0", "size_mm": 50, "axis": "z"})
        self.assertTrue(out["path"].endswith(".stl"))
        self.assertEqual(os.path.dirname(out["path"]), svc._dir(sid))
        self.assertNotIn("/", out["name"][:-4])
        self.assertAlmostEqual(out["size_mm"][2], 50, delta=0.5)
        with open(out["path"], "rb") as f:
            data = f.read()
        n = struct.unpack("<I", data[80:84])[0]
        self.assertEqual(n, out["triangles"])
        self.assertEqual(len(data), 84 + 50 * n)

    def test_stl_button_api_and_demo(self):
        def read(*p):
            with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
                return f.read()
        self.assertIn("def scan_export_stl(", read("backend", "api.py"))
        self.assertIn("scan_export_stl:", read("ui", "js", "demo_api.js"))
        js = read("ui", "js", "views", "design_scan.js")
        self.assertIn('data-action="scan-stl"', js)
        self.assertIn("Télécharger le .stl", js)

    def test_version(self):
        import config
        self.assertEqual(config.APP_VERSION, "5.8.9")


if __name__ == "__main__":
    unittest.main()
