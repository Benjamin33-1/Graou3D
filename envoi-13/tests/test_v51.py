"""5.1 : corrections de l'audit (cerveau local de Graoully, clé API, relevés des imprimantes)."""
import hashlib
import http.server
import json
import os
import pathlib
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                       # noqa: E402
from backend.graoully import brains, local_runtime                  # noqa: E402
from backend.printers import klipper_api                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Redirector(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    hits = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        # lire le corps : sous Windows, fermer sans l'avoir lu coupe la connexion (WinError 10053) au lieu de répondre
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Redirector.hits.append((self.path, self.headers.get("Authorization", "")))
        self.send_response(302)
        self.send_header("Location", "/ailleurs")
        self.end_headers()

    do_GET = do_POST


class KeyNeverFollowsRedirect(unittest.TestCase):
    def test_redirect_refused(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            Redirector.hits = []
            base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
            with self.assertRaises(brains.BrainError) as cm:
                brains.openai_chat(base, "sk-secret", "m", [{"role": "user", "content": "x"}], lambda p: None, lambda: False)
            self.assertIn("autre adresse", str(cm.exception))
            self.assertEqual([h[0] for h in Redirector.hits], ["/v1/chat/completions"])   # /ailleurs jamais appelé
        finally:
            srv.shutdown()
            srv.server_close()


class Hangup(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.close_connection = True                     # ferme sans répondre, comme un antivirus qui coupe


class CutConnection(unittest.TestCase):
    def test_cut_connection_is_a_brain_error(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hangup)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
            with self.assertRaises(brains.BrainError) as cm:
                brains.openai_chat(base, "k", "m", [{"role": "user", "content": "x"}], lambda p: None, lambda: False)
            self.assertIn("coupée", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()


class Download(unittest.TestCase):
    def test_complete_part_is_verified_not_refetched(self):
        tmp = tempfile.mkdtemp()
        data = b"GGUF" + b"z" * 3000
        dest = os.path.join(tmp, "m.gguf")
        with open(dest + ".part", "wb") as f:
            f.write(data)
        # adresse injoignable : elle ne doit pas être utilisée (avant : « erreur 416 » sans fin)
        local_runtime.download("http://127.0.0.1:9/x", dest, len(data), hashlib.sha256(data).hexdigest(),
                               lambda *a: None, lambda: False, 10 ** 6)
        self.assertTrue(os.path.isfile(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_resume_still_works(self):
        tmp = tempfile.mkdtemp()
        data = b"GGUF" + b"r" * 4000
        src = os.path.join(tmp, "src.bin")
        with open(src, "wb") as f:
            f.write(data)
        dest = os.path.join(tmp, "m.gguf")
        local_runtime.download(pathlib.Path(src).as_uri(), dest, len(data), hashlib.sha256(data).hexdigest(),
                               lambda *a: None, lambda: False, 10 ** 6)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data)


class ModelArgument(unittest.TestCase):
    def test_ascii_name_relative(self):
        folder = os.path.join("C:", os.sep, "Users", "Jérôme", "AppData", "Local", "Graou3D", "graoully", "modeles")
        model = os.path.join(folder, "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf")
        self.assertEqual(local_runtime.model_argument(model), (folder, "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"))

    def test_accented_name_kept_off_windows(self):
        model = os.path.join(tempfile.gettempdir(), "modèle.gguf")
        folder, arg = local_runtime.model_argument(model)
        self.assertEqual(folder, os.path.dirname(model))
        if os.name != "nt":
            self.assertEqual(arg, model)


FAKE_SERVER = textwrap.dedent('''\
    #!{python}
    import http.server, json, sys
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]); key = args[args.index("--api-key") + 1]
    model = args[args.index("-m") + 1]
    ready = {ready}
    open("args.json", "w").write(json.dumps(args))
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200 if ready else 503); self.end_headers()
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
            if self.headers.get("Authorization") != "Bearer " + key:
                self.send_response(401); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(("data: " + json.dumps({{"choices": [{{"delta": {{"content": "ok " + model}}}}]}}) + "\\n\\n").encode())
            self.wfile.write(b"data: [DONE]\\n\\n")
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
    ''')


@unittest.skipIf(os.name == "nt", "faux llama-server en script (Linux / macOS)")
class LocalServer(unittest.TestCase):
    def make(self, ready=True):
        tmp = tempfile.mkdtemp()
        llama = os.path.join(tmp, "llama")
        os.makedirs(llama)
        exe = os.path.join(llama, local_runtime.SERVER_EXE)
        with open(exe, "w", encoding="utf-8") as f:
            f.write(FAKE_SERVER.format(python=sys.executable, ready=ready))
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        models = os.path.join(tmp, "modeles")
        os.makedirs(models)
        model = os.path.join(models, "petit-Q4_K_M.gguf")
        with open(model, "wb") as f:
            f.write(b"GGUF0000")
        rt = local_runtime.LocalRuntime(tmp)
        rt.use_files(server=exe, model=model)
        self.addCleanup(rt.stop)
        return rt, models

    def test_random_key_and_model_name(self):
        rt, models = self.make()
        base = rt.base_url()
        with open(os.path.join(models, "args.json"), encoding="utf-8") as f:   # lancé dans le dossier du modèle
            args = json.load(f)
        self.assertEqual(args[args.index("-m") + 1], "petit-Q4_K_M.gguf")
        self.assertGreaterEqual(len(rt.api_key()), 24)
        out = brains.openai_chat(base, rt.api_key(), "local", [{"role": "user", "content": "x"}], lambda p: None, lambda: False)
        self.assertEqual(out, "ok petit-Q4_K_M.gguf")
        with self.assertRaises(brains.BrainError):                 # sans la clé : refusé
            brains.openai_chat(base, "", "local", [{"role": "user", "content": "x"}], lambda p: None, lambda: False)
        key = rt.api_key()
        rt.stop()
        rt.base_url()
        self.assertNotEqual(rt.api_key(), key)                       # nouvelle clé à chaque démarrage

    def test_stop_during_start_does_not_block(self):
        rt, _ = self.make(ready=False)
        errors = []

        def start():
            try:
                rt.base_url()
            except local_runtime.LocalError as e:
                errors.append(str(e))
        t = threading.Thread(target=start)
        t.start()
        time.sleep(1.0)
        t0 = time.monotonic()
        rt.stop()
        t.join(10)
        self.assertLess(time.monotonic() - t0, 8)
        self.assertFalse(t.is_alive())
        self.assertTrue(errors)
        self.assertFalse(rt.running())


class PollerStop(unittest.TestCase):
    def test_no_update_after_stop(self):
        updates = []
        gate = threading.Event()

        class Slow(klipper_api._Poller):
            interval = 0.01

            def poll(self):
                gate.wait(2)
                return {"connected": True}
        p = Slow(updates.append)
        p.start()
        time.sleep(0.05)
        threading.Timer(0.2, gate.set).start()       # la lecture en cours se termine après l'arrêt
        p.stop()
        n = len(updates)
        time.sleep(0.3)
        self.assertEqual(len(updates), n)
        self.assertFalse(p._thread.is_alive())

    def test_restart_uses_a_fresh_thread(self):
        calls = []

        class Fast(klipper_api._Poller):
            interval = 0.02

            def poll(self):
                return {"connected": True}
        p = Fast(calls.append)
        p.start()
        first = p._thread
        p.stop()
        p.start()
        self.assertIsNot(p._thread, first)
        self.assertFalse(first.is_alive())
        p.stop()


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(config.APP_VERSION, "5.8.9")       # 5.5 : version de lancement (tests/test_v55.py)
        from backend import updater                          # x.y.z obligatoire : Setup, publication, mises à jour
        self.assertIsNotNone(updater.parse_version(config.APP_VERSION))


class LlamaAssets(unittest.TestCase):
    """5.1.1 : « Archive de llama.cpp pour Windows introuvable » (noms changés, ou archives pas encore en ligne)."""
    def a(self, name, digest=""):
        return {"name": name, "browser_download_url": "https://github.com/x/" + name, "size": 10, "digest": digest}

    def test_names_variants(self):
        rel = {"assets": [self.a("cudart-llama-bin-win-cuda-12.4-x64.zip"), self.a("llama-b9000-bin-win-cuda-12.4-x64.zip"),
                          self.a("llama-b9000-bin-win-vulkan-x64.zip"), self.a("llama-b9000-bin-win-cpu-arm64.zip"),
                          self.a("llama-b9000-bin-win-cpu-x64.zip", "sha256:AB"), self.a("llama-b9000-bin-ubuntu-x64.zip"),
                          self.a("llama-b9000-bin-macos-arm64.zip")]}
        self.assertEqual(local_runtime.pick_asset(rel, "vulkan")[0], "llama-b9000-bin-win-vulkan-x64.zip")
        self.assertEqual(local_runtime.pick_asset(rel, "cpu")[0:4:3], ("llama-b9000-bin-win-cpu-x64.zip", "ab"))
        other = {"assets": [self.a("llama-b9100-bin-windows-x86_64-vulkan.zip"), self.a("llama-b9100-bin-win-avx2-x64.zip"),
                            self.a("llama-b9100-bin-win-noavx-x64.zip")]}
        self.assertEqual(local_runtime.pick_asset(other, "vulkan")[0], "llama-b9100-bin-windows-x86_64-vulkan.zip")
        self.assertEqual(local_runtime.pick_asset(other, "cpu")[0], "llama-b9100-bin-win-avx2-x64.zip")

    def test_latest_without_windows_files_uses_previous(self):
        releases = [{"tag_name": "b9002", "assets": [self.a("llama-b9002-bin-ubuntu-x64.zip")]},
                    {"tag_name": "b9001", "draft": True, "assets": [self.a("llama-b9001-bin-win-vulkan-x64.zip")]},
                    {"tag_name": "b9000", "assets": [self.a("llama-b9000-bin-win-vulkan-x64.zip")]}]
        name, url, size, sha, variant = local_runtime.pick_release_asset(releases, "vulkan")
        self.assertEqual((name, variant), ("llama-b9000-bin-win-vulkan-x64.zip", "vulkan"))

    def test_vulkan_missing_falls_back_to_cpu(self):
        releases = [{"assets": [self.a("llama-b9000-bin-win-cpu-x64.zip")]}]
        self.assertEqual(local_runtime.pick_release_asset(releases, "vulkan")[4], "cpu")
        with self.assertRaises(local_runtime.LocalError) as cm:
            local_runtime.pick_release_asset([{"assets": []}], "vulkan")
        self.assertIn("J'ai déjà les fichiers", str(cm.exception))
        self.assertEqual(local_runtime.pick_release_asset({"assets": [self.a("llama-b1-bin-win-cpu-x64.zip")]}, "cpu")[4], "cpu")


if __name__ == "__main__":
    unittest.main()
