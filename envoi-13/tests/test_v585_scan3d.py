"""5.8.5 : scan 3D par photos (backend/design/photo_scan.py, scan_service.py, scan_link.py, Conception › Scan 3D,
Scan 3D sur le téléphone). Scènes de synthèse : tests/synth_scan.py (boule texturée sur la feuille de repère)."""
import base64
import io
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import security  # noqa: E402

try:
    import manifold3d  # noqa: F401
    from PIL import Image
    from backend.design import photo_scan as ps
    import synth_scan as ss
    HAVE_GEOM = True
except ImportError:                     # pragma: no cover
    HAVE_GEOM = False
from backend.design import scan_link, scan_service  # noqa: E402


def jpeg(color=(120, 90, 60), size=(64, 48)):
    from PIL import Image as Img
    buf = io.BytesIO()
    Img.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------- transfert téléphone → PC
class LinkTests(unittest.TestCase):
    def setUp(self):
        self.got = []
        self.rx = scan_link.Receiver("Figurine", self._photo, status=lambda: {"state": "photos"}).open()
        self.addCleanup(self.rx.close)
        # les tests passent par la boucle locale : on l'autorise pour l'envoi
        p = mock.patch.object(scan_link, "_lan_host", lambda h: h)
        p.start()
        self.addCleanup(p.stop)

    def _photo(self, raw):
        self.got.append(raw)
        return len(self.got)

    def test_pair_send_status(self):
        s = scan_link.Sender()
        with self.assertRaises(security.ValidationError):
            s.send(jpeg())                                       # pas encore appairé
        with self.assertRaises(security.ValidationError) as e:
            s.pair("127.0.0.1", self.rx.port, "000000" if self.rx.code != "000000" else "111111")
        self.assertIn("Code faux", str(e.exception))
        out = s.pair("127.0.0.1", self.rx.port, self.rx.code[:3] + " " + self.rx.code[3:])
        self.assertEqual(out["title"], "Figurine")
        self.assertEqual(s.send(jpeg())["count"], 1)
        self.assertEqual(s.send(jpeg((1, 2, 3)))["count"], 2)
        self.assertEqual(len(self.got), 2)
        self.assertEqual(s.status()["count"], 2)
        self.assertTrue(self.rx.public()["paired"])

    def test_refuses_non_photos_and_bad_tokens(self):
        s = scan_link.Sender()
        s.pair("127.0.0.1", self.rx.port, self.rx.code)
        with self.assertRaises(security.ValidationError):
            s.send(b"MZ\x90\x00 pas une photo")
        s.token = "f" * 32
        with self.assertRaises(security.ValidationError) as e:
            s.send(jpeg())
        self.assertIn("Code requis", str(e.exception))
        self.assertEqual(self.got, [])

    def test_five_wrong_codes_close_the_reception(self):
        s = scan_link.Sender()
        wrong = "123456" if self.rx.code != "123456" else "654321"
        for _ in range(scan_link.MAX_TRIES):
            with self.assertRaises(security.ValidationError):
                s.pair("127.0.0.1", self.rx.port, wrong)
        self.assertTrue(self.rx.closed)
        with self.assertRaises(security.ValidationError):
            s.pair("127.0.0.1", self.rx.port, self.rx.code)

    def test_discovery_answers_on_the_local_network(self):
        if not self.rx.public()["discovery"]:
            self.skipTest("port de découverte occupé")
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.settimeout(2)
            u.sendto(scan_link.HELLO, ("127.0.0.1", scan_link.DISCOVERY_PORT))
            data, _ = u.recvfrom(512)
        info = json.loads(data)
        self.assertEqual((info["app"], info["port"], info["title"]), (scan_link.ANSWER, self.rx.port, "Figurine"))

    def test_phone_only_talks_to_the_local_network(self):
        mock.patch.stopall()
        for bad in ("8.8.8.8", "127.0.0.1", "exemple.fr", "224.0.0.1", "0.0.0.0"):
            with self.assertRaises(security.ValidationError, msg=bad):
                scan_link._lan_host(bad)
        for ok in ("192.168.1.20", "10.0.0.7", "172.20.3.4"):
            self.assertEqual(scan_link._lan_host(ok), ok)


# ---------------------------------------------------------------------------- dossiers des scans
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.stored = []
        self.svc = scan_service.ScanService(self.dir, lambda name, man, info: self.stored.append((name, man, info)) or {"sid": "x"})

    def test_create_add_rename_delete(self):
        sc = self.svc.create("")
        self.assertTrue(sc["title"].startswith("Scan du"))
        sid = sc["id"]
        self.assertEqual(self.svc.add_photo(sid, jpeg()), 1)
        self.assertEqual(self.svc.add_photo(sid, jpeg()), 2)
        with self.assertRaises(security.ValidationError):
            self.svc.add_photo(sid, b"GIF89a....")
        self.assertEqual(len(self.svc.thumbs(sid)), 2)
        self.assertEqual(self.svc.rename(sid, "Ma figurine")["title"], "Ma figurine")
        self.assertEqual([x["id"] for x in self.svc.list()], [sid])
        self.assertEqual(self.svc.remove_photo(sid, "0001.jpg")["count"], 1)
        with self.assertRaises(security.ValidationError):
            self.svc.remove_photo(sid, "../scan.json")
        self.svc.delete(sid)
        self.assertEqual(self.svc.list(), [])
        for bad in ("../x", "20261029-210000-abcd"):
            with self.assertRaises(security.ValidationError):
                self.svc.status(bad)

    def test_needs_enough_photos(self):
        sid = self.svc.create("Peu")["id"]
        for _ in range(3):
            self.svc.add_photo(sid, jpeg())
        with mock.patch("backend.design.photo_scan.available", return_value=True):
            with self.assertRaises(security.ValidationError) as e:
                self.svc.start(sid)
        self.assertIn("au moins", str(e.exception))

    def test_interrupted_run_comes_back_as_photos(self):
        sid = self.svc.create("Coupé")["id"]
        meta = self.svc._meta(sid)
        meta["state"] = "running"
        self.svc._save(sid, meta)
        st = self.svc.status(sid)
        self.assertEqual(st["state"], "photos")
        self.assertIn("interrompu", st["msg"])

    @unittest.skipUnless(HAVE_GEOM, "manifold3d")
    def test_open_result_scale_and_base(self):
        sid = self.svc.create("Cube")["id"]
        base = os.path.join(self.svc.root, sid)
        ps.save_mesh(manifold3d.Manifold.cube([10, 20, 30]), os.path.join(base, "resultat.npz"))
        meta = self.svc._meta(sid)
        meta.update(state="done", info={"scaled": False})
        self.svc._save(sid, meta)
        with self.assertRaises(security.ValidationError):               # sans feuille : une taille est obligatoire
            self.svc.open_result(sid, {})
        self.svc.open_result(sid, {"size_mm": 60, "axis": "z", "base_mm": 3})
        name, man, info = self.stored[-1]
        b = man.bounding_box()
        self.assertEqual(name, "Cube")
        self.assertAlmostEqual(b[5] - b[2], 57.0, places=4)               # 60 mm de haut, 3 mm rabotés
        self.assertAlmostEqual(b[3] - b[0], 20.0, places=4)

    def test_receive_feeds_the_scan_and_done_starts_the_computation(self):
        sid = self.svc.create("Téléphone")["id"]
        st = self.svc.receive(sid)
        self.addCleanup(self.svc.receive_stop)
        rx = st["receiving"]
        self.assertTrue(rx["open"])
        self.assertRegex(rx["code"], r"^\d{6}$")
        with mock.patch.object(scan_link, "_lan_host", lambda h: h):
            s = scan_link.Sender()
            s.pair("127.0.0.1", rx["port"], rx["code"])
            for _ in range(2):
                s.send(jpeg())
            with mock.patch("backend.design.photo_scan.available", return_value=True):
                with self.assertRaises(security.ValidationError) as e:
                    s.done()                                            # 2 photos : le PC refuse de lancer
            self.assertIn("au moins", str(e.exception))
            self.assertEqual(s.status()["count"], 2)
        self.assertEqual(self.svc.status(sid)["count"], 2)
        self.svc.receive_stop()
        self.assertNotIn("receiving", self.svc.status(sid))


# ---------------------------------------------------------------------------- géométrie
@unittest.skipUnless(HAVE_GEOM, "numpy, Pillow, manifold3d")
class GeometryTests(unittest.TestCase):
    def test_ground_plane_and_convergence(self):
        rng = np.random.default_rng(0)
        floor = np.column_stack([rng.uniform(-100, 100, 600), rng.uniform(-100, 100, 600), rng.normal(0, 0.05, 600)])
        obj = rng.normal([0, 0, 20], 8, (300, 3))
        a = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        cams = np.column_stack([150 * np.cos(a), 150 * np.sin(a), np.full(12, 90.0)])
        n, d, tol = ps.ground_plane(np.vstack([floor, obj]), cams)
        self.assertGreater(n[2], 0.999)
        self.assertLess(abs(d), 0.1)
        target = np.array([3.0, -2.0, 20.0])
        dirs = target - cams
        self.assertTrue(np.allclose(ps.convergence(cams, dirs), target, atol=1e-6))

    def test_finish_needs_a_size_without_the_sheet(self):
        cube = manifold3d.Manifold.cube([10, 10, 10])
        with self.assertRaises(security.ValidationError):
            ps.finish(cube, False, {})
        out = ps.finish(cube, True, {"size_mm": 25, "axis": "x"})
        self.assertAlmostEqual(out.bounding_box()[3] - out.bounding_box()[0], 25, places=4)
        with self.assertRaises(security.ValidationError):
            ps.finish(cube, True, {"base_mm": 12})                       # tout raboté

    def test_mesh_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "m.npz")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        ps.save_mesh(manifold3d.Manifold.sphere(10, 48), path)
        self.assertAlmostEqual(ps.load_mesh(path).volume(), manifold3d.Manifold.sphere(10, 48).volume(), delta=1.0)

    def test_depth_maps_and_volume_of_a_ball(self):
        """Poses exactes (sans COLMAP) : profondeur par balayage de plans, puis volume fermé, à plat sur le sol."""
        cams = ss.ring((105, -150, 12), 140, (30, 55), 8, size=(360, 270))
        V = ss.views(cams)
        lo, hi = np.array([80, -175, 0.0]), np.array([130, -125, 36.0])
        c = 0.5 * (lo + hi)
        nb = [ps.neighbours(V, i, c) for i in range(len(V))]
        self.assertTrue(all(len(x) >= 2 for x in nb))
        D = []
        errs = []
        for i, v in enumerate(V):
            d, _c = ps.depth_map(V, i, nb[i], lo, hi, 64)
            ys, xs = np.nonzero(d > 0)
            z = d[ys, xs]
            P = (v["R"].T @ (np.linalg.inv(v["K"]) @ np.vstack([xs * z, ys * z, z]) - v["t"][:, None])).T
            r = np.linalg.norm(P - [105, -150, 15], axis=1)
            errs.append(np.abs(r[(r < 20) & (P[:, 2] > 3)] - 15))
            d, _g = ps.ground_fill(V, i, nb[i], d)
            D.append(d)
        e = np.concatenate(errs)
        self.assertLess(np.median(e), 0.4)                                # 360 px : pixel de 0,4 mm environ
        D = [ps.consistency(V, D, i, nb[i]) for i in range(len(V))]
        sdf, step, axes = ps.fuse(V, D, lo, hi, 90)
        man = ps.largest(ps.extract(sdf, step, axes))
        b = man.bounding_box()
        self.assertAlmostEqual(b[2], 0.0, places=6)                      # base plate, au sol
        self.assertAlmostEqual(b[5], 30.0, delta=0.6)                    # sommet de la boule
        for k, centre in ((0, 105), (1, -150)):
            self.assertAlmostEqual(0.5 * (b[k] + b[k + 3]), centre, delta=0.6)
            self.assertAlmostEqual(b[k + 3] - b[k], 30.0, delta=3.5)
        vert = np.asarray(man.to_mesh().vert_properties)[:, :3]
        up = vert[:, 2] > 10
        self.assertLess(np.median(np.abs(np.linalg.norm(vert[up] - [105, -150, 15], axis=1) - 15)), 0.5)

    def test_cancel_stops_the_depth_sweep(self):
        cams = ss.ring((105, -150, 12), 140, (40,), 4, size=(160, 120))
        V = ss.views(cams)
        with self.assertRaises(ps.Cancelled):
            ps.depth_map(V, 0, [1, 3], np.array([80, -175, 0.0]), np.array([130, -125, 36.0]), 32, cancelled=lambda: True)


# ---------------------------------------------------------------------------- chaîne complète (COLMAP)
@unittest.skipUnless(HAVE_GEOM and ps.available(), "pycolmap")
class PipelineTests(unittest.TestCase):
    def test_photos_to_scaled_volume(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "photos")
        os.makedirs(src)
        for k, c in enumerate(ss.ring((105, -150, 12), 170, (25, 45, 65), 8, size=(800, 600))):
            img = Image.fromarray(np.clip(ss.render(c), 0, 255).astype(np.uint8))
            ex = Image.Exif()
            ex.get_ifd(0x8769)[41989] = 26
            img.save(os.path.join(src, f"p{k:02d}.jpg"), quality=93, exif=ex)
        names = ps.prepare(sorted(os.path.join(src, f) for f in os.listdir(src)), os.path.join(tmp, "img"))
        seen = []
        out = ps.reconstruct(os.path.join(tmp, "img"), names, tmp, "draft", progress=lambda p, m: seen.append(m))
        info = out["info"]
        self.assertEqual(info["placed"], 24)
        self.assertTrue(info["scaled"])                                  # la feuille donne les mm
        self.assertGreaterEqual(info["sheet_dots"], 12)
        b = out["man"].bounding_box()
        self.assertAlmostEqual(b[5] - b[2], 30.0, delta=1.0)             # boule de 30 mm
        self.assertAlmostEqual(0.5 * (b[0] + b[3]), 105.0, delta=1.0)
        self.assertAlmostEqual(0.5 * (b[1] + b[4]), -150.0, delta=1.0)
        self.assertTrue(any("Profondeur" in (m or "") for m in seen))

    def test_too_few_photos(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "a.jpg")
        with open(p, "wb") as f:
            f.write(jpeg(size=(800, 600)))
        with self.assertRaises(security.ValidationError):
            ps.prepare([p] * 3, os.path.join(tmp, "img"))


# ---------------------------------------------------------------------------- interface et fabrication
class InterfaceTests(unittest.TestCase):
    API = ("scan_info", "scan_list", "scan_new", "scan_status", "scan_thumbs", "scan_rename", "scan_delete", "scan_add_files",
           "scan_add_photo", "scan_remove_photo", "scan_receive", "scan_receive_stop", "scan_start", "scan_cancel", "scan_open",
           "scan_phone_find", "scan_phone_pair", "scan_phone_send", "scan_phone_done", "scan_phone_status")

    def test_api_and_demo(self):
        from backend import api
        demo = read("ui", "js", "demo_api.js")
        for name in self.API:
            self.assertTrue(callable(getattr(api.BambuBackendAPI, name, None)), name)
            self.assertIn(f"{name}:", demo, name)

    def test_pages_menu_and_tab(self):
        html = read("ui", "index.html")
        for needle in ('id="view-scan"', "js/views/design_scan.js", "js/views/scan.js"):
            self.assertIn(needle, html)
        self.assertLess(html.index("js/views/design.js"), html.index("js/views/design_scan.js"))
        menu = read("ui", "js", "menu.js")
        self.assertIn('id: "scan"', menu)
        self.assertIn("phoneOnly", menu)
        self.assertIn('["scan", "Scan 3D", "camera"]', read("ui", "js", "views", "design.js"))
        for f in ("design_scan.js", "scan.js"):
            js = read("ui", "js", "views", f)
            self.assertNotIn("innerHTML", js)
            self.assertNotIn("eval(", js)

    def test_android_camera_only_for_the_app(self):
        manifest = read("android", "app", "src", "main", "AndroidManifest.xml")
        self.assertIn("android.permission.CAMERA", manifest)
        self.assertIn('android:name="android.hardware.camera" android:required="false"', manifest)
        act = read("android", "app", "src", "main", "java", "fr", "graou3d", "app", "MainActivity.java")
        self.assertIn("onPermissionRequest", act)
        self.assertIn("RESOURCE_VIDEO_CAPTURE", act)
        self.assertIn("isLocal(request.getOrigin())", act)
        self.assertNotIn("pycolmap", read("android", "app", "build.gradle"))

    def test_build_and_licences(self):
        self.assertIn("pycolmap", read("requirements.txt"))
        self.assertIn("--collect-all pycolmap", read("build_exe.bat"))
        self.assertIn("COLMAP", read("TIERS-LICENCES.txt"))

    def test_version(self):
        import config
        self.assertEqual(config.APP_VERSION, "5.8.9")


if __name__ == "__main__":
    unittest.main()
