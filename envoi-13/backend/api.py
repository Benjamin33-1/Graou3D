"""BambuBackendAPI : les méthodes appelables depuis le JavaScript (window.pywebview.api).

Règles de sécurité appliquées ici :
- seules les méthodes marquées @exposed sont visibles du JavaScript, et seulement sous forme de
  fonctions indépendantes (js_functions) : l'objet API lui-même n'est jamais remis à pywebview,
  donc aucun attribut interne (« _secrets », « _client »…) n'est joignable depuis la page ;
- un appel n'est accepté que si la fenêtre affiche l'interface locale de Graou3D (même origine
  qu'au premier chargement) : une page étrangère chargée par erreur n'obtient que « Refusé. » ;
- l'identifiant de l'utilisateur vient toujours de la session, jamais de l'interface ;
- chaque entrée est validée (backend/security.py) et chaque erreur est convertie en
  message lisible, sans trace technique.
"""
import base64
import binascii
import concurrent.futures
import contextlib
import http.client
import json
import datetime as dt
import functools
import logging
import os
import re
import secrets as pysecrets
import shutil
import tempfile
import threading
import time
import urllib.parse
import webbrowser

import config
from backend import accounting, estimate, local_prefs, orders, preview3d, quote_pdf, real_cost, security, support, updater
from backend import clients as clients_mod, invoice_pdf, invoices, messages, planning, pro_dashboard, sales_import, urssaf
from backend import catalog, conception_inbox, device, forecast, legal
from backend import workshop as workshop_mod
from backend.auth_service import AuthService, SecretStore
from backend.graoully import actions as graoully_actions, attachments as graoully_attachments
from backend.graoully.library import machine_family
from backend.graoully.service import GraoullyService
from backend import backup_service, business_backup, data_export, desktop_notify, maintenance_presets, multicolor, print_usage, seasons, spare_parts, stock_alerts
from backend.skip_object import ActiveSkip
from backend.legacy_import import import_legacy
from backend.printers import bambu_connect
from backend.local_vision import DEFECT_LABELS, LocalVision, import_training_photos
from backend.model_thumbs import thumbnail
from backend.notify_service import EventQueue, Notifier
from backend.phone_remote import PhoneRemote, is_strong_cmd_topic, new_cmd_topic, topic_problem
from backend.print_scheduler import PrintScheduler
from backend.quotes import QuoteStore, clean_quote
from backend.printer_service import PrinterService
from backend.printers.bambu_cloud import BambuCloudError
from backend.printers.tls_pinning import PinMismatch
from backend.slicer_analyzer import (ENGINES, SlicerExpress, analyze_bytes, available_presets, find_slicer,
                                     orca_presets, plate_gcode, stock_check)
from backend.slicer_watch import FolderWatch, default_folder as watch_default_folder
from backend.social_service import SocialService, parse_recipe_code, recipe_code
from backend import filament_catalog
from backend.store_service import MATERIALS, NOZZLES, StoreService, clean_palette, drying_preset, feature_enabled, print_cost
from backend.supabase_rest import SupabaseClient, SupabaseError

log = logging.getLogger(__name__)


QUOTES_DIR = os.path.join(config.EXPORT_DIR, "devis")
INVOICES_DIR = os.path.join(config.EXPORT_DIR, "factures")
ACCOUNTING_DIR = os.path.join(config.EXPORT_DIR, "exports")
TIMELAPSE_DIR = os.path.join(config.EXPORT_DIR, "timelapses")
WELD_DIR = os.path.join(config.EXPORT_DIR, "soudage")
TIMELAPSE_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")      # lisibles par le lecteur vidéo de l'interface
TIMELAPSE_CHUNK = 3 * 1024 * 1024
TIMELAPSE_MAX_BYTES = 400 * 1024 * 1024
TIMELAPSE_MAX_JOBS = 2                       # vidéos « avec bandeau » écrites en même temps
TIMELAPSE_IDLE_S = 30 * 60                   # enregistrement sans nouveau morceau depuis 30 min : abandonné
TIMELAPSE_PART_MAX_AGE_S = 24 * 3600         # .part orphelin de plus de 24 h : effacé
AUTO_BACKUP_DELAY_S = 45          # la sauvegarde automatique attend que le démarrage soit fini
ANDROID_NO_SLICER = ("La découpe se fait sur le PC (Bambu Studio ou OrcaSlicer). "
                     "Depuis le téléphone, envoyez un fichier déjà découpé (.3mf ou .gcode).")
_LINK_KEYS = ("quote_id", "quote_number", "client", "quote_line", "quote_label", "quote_pieces", "quote_cost",
              "quote_price", "price_kg")

_MW_HOSTS = ("makerworld.com", "makerworld.com.cn")
_LOGIN_HOSTS = ("bambulab.com", "bambulab.cn", "accounts.google.com", "appleid.apple.com", "facebook.com")


def _local_palette():
    """5.5 : couleurs gardées sur ce PC, pour l'écran de démarrage (avant la connexion)."""
    try:
        return clean_palette(json.loads(local_prefs.get("palette", "") or "{}"))
    except ValueError:
        return clean_palette({})


def _makerworld_host(url, login):
    try:
        host = url.split("/")[2].split("@")[-1].split(":")[0].lower()
    except IndexError:
        return False
    allowed = _MW_HOSTS + (_LOGIN_HOSTS if login else ())
    return any(host == h or host.endswith("." + h) for h in allowed)


def _open_local(path):
    """Ouvre un fichier ou un dossier local avec l'application par défaut de Windows.
    Android (3.8) : un fichier s'ouvre dans l'application adaptée du téléphone ; un dossier, non."""
    if device.ANDROID:
        if os.path.isdir(path):
            raise security.ValidationError("Les dossiers ne s'ouvrent pas sur le téléphone : fichier enregistré dans l'application.")
        if not device.open_path(path):
            raise security.ValidationError("Aucune application du téléphone ne sait ouvrir ce fichier.")
        return
    if hasattr(os, "startfile"):
        os.startfile(path)  # noqa: S606 - chemin construit par l'application, jamais par l'interface
    else:
        webbrowser.open("file://" + path)


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def url_origin(url):
    """« schéma://hôte:port » d'une adresse http(s), en minuscules ; "" pour tout le reste (file:, data:…)."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    port = port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{host}:{port}"


def local_origin(url):
    """Origine de l'adresse si elle est servie par ce PC (serveur local de pywebview), sinon ""."""
    origin = url_origin(url)
    try:
        host = urllib.parse.urlsplit(str(url or "")).hostname
    except ValueError:
        return ""
    return origin if origin and host in _LOCAL_HOSTS else ""


_ORIGIN_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{1,5})?")


def _label_page_files(supabase_url, anon_key):
    """Fichiers de la page mobile des étiquettes, remplis avec l'adresse et la clé PUBLIQUE du projet.
    La CSP de index.html n'autorise les requêtes que vers l'origine exacte du projet Supabase."""
    import json
    m = _ORIGIN_RE.match(supabase_url or "")
    if not m:
        raise security.ValidationError("Adresse Supabase invalide dans le fichier .env.")
    folder = os.path.join(config.BASE_DIR, "web", "bobine")
    files = {}
    for name in ("index.html", "app.js", "style.css"):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            files[name] = f.read()
    files["index.html"] = files["index.html"].replace("__SUPABASE_ORIGIN__", m.group(0))
    files["app.js"] = (files["app.js"].replace("__SUPABASE_URL__", json.dumps(supabase_url)[1:-1])
                       .replace("__SUPABASE_ANON_KEY__", json.dumps(anon_key)[1:-1]))
    return files


def exposed(require_auth=True):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(self, *args):
            if not self._origin_ok():
                log.warning("Appel %s refusé : la fenêtre n'affiche pas l'interface de Graou3D.", fn.__name__)
                return {"ok": False, "msg": "Refusé."}
            if not config.SUPABASE_CONFIGURED:
                return {"ok": False, "setup": True, "msg": "Supabase n'est pas configuré : complétez le fichier .env."}
            if require_auth and not self._client.user_id:
                return {"ok": False, "auth": False, "msg": "Connectez-vous d'abord."}
            try:
                result = fn(self, *args)
            except security.ValidationError as e:
                return {"ok": False, "msg": str(e)}
            except SupabaseError as e:
                return {"ok": False, "msg": str(e), "auth": e.status != 401}
            except (BambuCloudError, PinMismatch) as e:
                return {"ok": False, "msg": str(e)}
            except ImportError as e:        # bibliothèque absente (téléphone sans Pillow ou numpy, 3.8)
                log.warning("%s : %s", fn.__name__, e)
                return {"ok": False, "msg": "Fonction indisponible sur cet appareil."}
            except (OSError, TimeoutError) as e:
                log.warning("%s : %s", fn.__name__, e)
                return {"ok": False, "msg": "Opération réseau ou fichier impossible : " + (str(e)[:140] or "erreur")}
            except Exception:
                log.exception("Erreur dans %s", fn.__name__)
                return {"ok": False, "msg": "Erreur inattendue, réessayez."}
            if result is None:
                return {"ok": True}
            if isinstance(result, dict):
                result.setdefault("ok", True)
                return result
            return {"ok": True, "data": result}
        wrapper._exposed = True
        return wrapper
    return decorator


def js_functions(api):
    """Fonctions à donner à window.expose() : une fermeture par méthode @exposed, du même nom.

    pywebview retrouve une fonction exposée par son nom exact (dictionnaire) ; avec js_api=None il n'a
    aucun objet à parcourir avec getattr, donc ni « _secrets.get » ni « app_info.__globals__ » ne mènent
    nulle part. Les fermetures ne sont pas des méthodes liées : pas de __self__ vers l'API."""
    out = []
    for name in sorted(dir(type(api))):
        if name.startswith("_") or not getattr(getattr(type(api), name), "_exposed", False):
            continue

        def make(method_name):
            def call(*args):
                return getattr(api, method_name)(*args)
            call.__name__ = call.__qualname__ = method_name
            call.__doc__ = None
            return call
        out.append(make(name))
    return out


class BambuBackendAPI:
    def __init__(self):
        self._window = None
        self._ui_origin = None     # origine locale de l'interface (fixée au premier chargement)
        self._ui_home = None
        self._origin_lock = threading.Lock()
        self._closing = False
        self._quit_ready = threading.Event()
        self._client = SupabaseClient(config.SUPABASE_URL or "https://invalid.local", config.SUPABASE_ANON_KEY,
                                      timeout=config.HTTP_TIMEOUT)
        self._secrets = SecretStore()
        self._auth = AuthService(self._client, self._secrets)
        self._store = StoreService(self._client)
        self._quotes = QuoteStore(self._client)
        self._workshop = workshop_mod.WorkshopService(self._client)
        self._orders = orders.OrderStore(self._client)
        self._products = catalog.ProductStore(self._client)    # 5.5 : catalogue de produits (migration 012)
        self._print_recipes = catalog.RecipeStore(self._client)  # 5.5 : recettes d'impression (migration 012)
        self._legal = legal.LegalTexts(self._client)                # 5.5 : CGU, confidentialité, mentions légales
        self._legal_pending = ""      # version acceptée à l'inscription, enregistrée à la première connexion
        self._season = seasons.SeasonSource(self._client)      # 3.8.7 : Halloween, Noël ; 5.1.2 : jours de fête (migration 008)
        self._invoices = invoices.InvoiceStore(self._client)    # 3.9 : factures (migration 006)
        self._clients = clients_mod.ClientStore(self._client)   # 4.3 : carnet de clients (migration 010)
        self._social = SocialService(self._client)
        self._messages = messages.MessageStore(self._client)   # 3.9 : messagerie entre amis (migration 006)
        self._events = EventQueue()
        self._notifier = Notifier(self._client, self._store, self._events, desktop_notify.DesktopNotifier())
        self._ai = LocalVision(config.DATA_DIR, config.BUNDLED_MODELS_DIR)
        self._printer = PrinterService(self._client, self._store, self._notifier, self._events, self._ai, self._secrets)
        self._slicer = SlicerExpress()
        self._graoully = GraoullyService(config.DATA_DIR, config.GRAOULLY_LIBRARY_DIR, self._secrets)   # 5.0
        if not device.ANDROID:
            self._graoully.designer = self._design             # 5.3 : pièces créées par Graoully → Conception
            conception_inbox.cleanup()                         # 5.8 : arrivées GraouConception de plus de 14 jours
        self._graoully.action_context = self._graoully_action_ctx   # 5.3 : Graoully qui agit (avec confirmation)
        self._graoully_lock = threading.Lock()                      # 5.5.3 : actions déjà faites et « Défaire »
        self._graoully_recent, self._graoully_undos = {}, {}
        self._scheduler = PrintScheduler(self._store, self._printer, self._notifier)
        self._remote = PhoneRemote(self._store, self._printer, self._notifier)
        self._printer.on_started = self._remote.new_print
        # Autre imprimante active : les boutons déjà envoyés sur le téléphone ne valent plus (revue de sécurité 3.7).
        self._printer.on_active_changed = self._remote.forget_codes
        # Plusieurs imprimantes : les boutons pause / arrêt (imprimante active) ne vont qu'avec ses alertes.
        self._notifier.actions_for = self._printer.guard_actions(self._remote.actions_for)
        # 3.7 : « Ignorer » une alerte caméra et « Retirer une pièce » visent l'imprimante active.
        self._remote.on_ignore = lambda kind: self._printer.vision_guard().mute(kind)
        self._remote.skipper = ActiveSkip(self._printer)
        self._printer.on_finished = lambda: self._pool.submit(self._check_alerts)
        self._alert_lock = threading.Lock()
        self._picked = {}          # jeton -> chemin choisi dans une boîte de dialogue native
        self._mw_tokens = []       # jetons des fichiers listés dans MakerWorld (renouvelés à chaque liste)
        self._lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
        self._updater = updater.Updater()
        self._support_file = None
        self._watch = FolderWatch()     # import automatique depuis le slicer (relevé avec « live »)
        self._watch_last = 0.0
        self._tl_out = {}          # jeton -> fichier vidéo « avec bandeau » en cours d'écriture
        self._tl_swept = False     # .part laissés par une session précédente : effacés au premier enregistrement

    # ---------------------------------------------------------- non exposé
    def _bind_window(self, window):
        self._window = window
        self._closing = False
        self._quit_ready = threading.Event()
        self._ui_origin = None
        self._ui_home = None

    def _remember_origin(self, url):
        """Premier chargement : retient l'origine locale de l'interface (http://127.0.0.1:port)."""
        origin = local_origin(url)
        with self._origin_lock:
            if self._ui_origin is None and origin:
                self._ui_origin = origin
                path = urllib.parse.urlsplit(str(url)).path or "/index.html"
                self._ui_home = origin + path
        return self._ui_origin

    def _origin_ok(self, url=None):
        """Vrai si l'appel vient de l'interface locale. Sans fenêtre (tests, appels internes) : rien à vérifier.
        L'adresse est lue en direct (sous WebView2, simple lecture d'un attribut tenu à jour à chaque navigation) :
        un cache mis à jour sur « loaded » laisserait une page étrangère appeler avant ce signal."""
        if self._window is None:
            return True
        if url is None:
            try:
                url = self._window.get_current_url()
            except Exception:
                return False
        origin = self._ui_origin or self._remember_origin(url)
        return bool(origin) and url_origin(url) == origin

    def _on_loaded(self):
        """Chaque chargement de page : si ce n'est pas l'interface locale, on y revient."""
        win = self._window
        if win is None:
            return
        try:
            url = win.get_current_url() or ""
        except Exception:
            return
        if self._origin_ok(url) or not self._ui_home:
            return
        log.warning("Page étrangère dans la fenêtre principale (%s) : retour à Graou3D.", url_origin(url) or "autre")
        try:
            win.load_url(self._ui_home)
        except Exception:
            log.exception("Retour à l'interface impossible")

    def _shutdown(self):
        self._graoully.shutdown()
        self._scheduler.shutdown()
        self._remote.shutdown()
        self._printer.shutdown()
        self._pool.shutdown(wait=False)

    def _after_login(self, profile):
        self._store.reset_cache()
        settings = self._store.settings(refresh=True)
        self._printer.load_and_connect()
        self._schedule_auto_backup()
        self._graoully.warm_up()
        return {"profile": profile, "settings": settings, "legal": self._legal_after_login(settings)}

    def _legal_prefetch(self):
        try:
            self._legal.current_version()
        except Exception:       # jamais bloquant : la connexion relira les textes
            log.debug("Textes juridiques non préchargés", exc_info=True)

    def _legal_after_login(self, settings):
        """5.5 : enregistre l'acceptation faite à l'inscription, puis dit à l'interface s'il faut (re)demander."""
        current = self._legal.current_version()
        if self._legal_pending:
            if self._legal_pending == current and (settings.get("legal") or {}).get("version") != current:
                try:
                    settings = self._store.update_settings("legal", lambda _old: legal.acceptance(current))
                except (security.ValidationError, SupabaseError):
                    pass
            self._legal_pending = ""
        return legal.status(settings, current)

    def _schedule_auto_backup(self):
        """Sauvegarde locale hebdomadaire, en tâche de fond, un peu après la connexion (jamais bloquante)."""
        if local_prefs.get("auto_backup", "on") == "off":
            return
        timer = threading.Timer(AUTO_BACKUP_DELAY_S, self._auto_backup)
        timer.daemon = True
        timer.start()

    def _auto_backup(self):
        if self._closing or local_prefs.get("auto_backup", "on") == "off":
            return None
        try:
            uid = self._client.user_id
            if not uid:
                return None
            path = backup_service.run_auto_backup(self._store, backup_service.auto_folder(config.DATA_DIR),
                                                  backup_service.account_tag(uid), config.APP_VERSION,
                                                  business=self._business_stores())
            if path:
                log.info("Sauvegarde automatique : %s", path)
            return path
        except Exception as e:          # jamais d'erreur visible pour une tâche de fond
            log.warning("Sauvegarde automatique impossible : %s", e)
            return None

    def _pick_file(self, file_types):
        if device.ANDROID:
            paths = device.pick_files(file_types)
            if not paths:
                return None
            return self._remember_pick(paths[0])
        if not self._window:
            raise security.ValidationError("Fenêtre indisponible.")
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        paths = self._window.create_file_dialog(dialog, allow_multiple=False, file_types=file_types)
        if not paths:
            return None
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        return self._remember_pick(path)

    def _remember_pick(self, path):
        token = pysecrets.token_hex(8)
        with self._lock:
            self._picked[token] = path
        return {"token": token, "name": os.path.basename(path), "size": os.path.getsize(path)}

    def _picked_path(self, token):
        with self._lock:
            path = self._picked.get(str(token))
        if not path or not os.path.isfile(path):
            raise security.ValidationError("Fichier introuvable, choisissez-le à nouveau.")
        return path

    # ============================================================ Session
    @exposed(require_auth=False)
    def app_info(self):
        if config.SUPABASE_CONFIGURED:      # 5.5 : textes juridiques lus pendant que l'écran de connexion s'affiche
            threading.Thread(target=self._legal_prefetch, daemon=True).start()
        return {"version": config.APP_VERSION, "configured": config.SUPABASE_CONFIGURED, "device": device.name(),
                "nozzles": NOZZLES, "materials": MATERIALS, "filaments": filament_catalog.catalog(), "lang": local_prefs.get("lang", ""),
                "anim": local_prefs.get("anim", "on") != "off", "theme": local_prefs.get("theme", ""),
                "palette": _local_palette(),
                "anim_last": {"open": local_prefs.get("anim_open", ""), "close": local_prefs.get("anim_close", "")}}

    @exposed(require_auth=False)
    def set_language(self, lang):
        if lang not in config.LANGUAGES:
            raise security.ValidationError("Langue inconnue.")
        local_prefs.put("lang", lang)
        return {"lang": lang}

    @exposed(require_auth=False)
    def set_animations(self, on):
        local_prefs.put("anim", "on" if on is True else "off")
        return {"anim": on is True}

    @exposed(require_auth=False)
    def remember_intro(self, mode, variant):
        """Dernière variante jouée (ouverture ou fermeture), pour ne pas la rejouer au lancement suivant."""
        if mode not in ("open", "close") or not isinstance(variant, str) or not re.fullmatch(r"[a-z0-9-]{1,24}", variant):
            raise security.ValidationError("Animation inconnue.")
        local_prefs.put("anim_" + mode, variant)

    @exposed(require_auth=False)
    def remember_theme(self, theme, palette=None):
        """Thème du compte gardé sur ce PC : l'écran de démarrage le prend avant la connexion.
        5.5 : « auto » suit Windows ; les couleurs choisies (palette) sont gardées aussi."""
        if theme not in ("light", "dark", "auto"):
            raise security.ValidationError("Thème inconnu.")
        local_prefs.put("theme", theme)
        if palette is not None:
            local_prefs.put("palette", json.dumps(clean_palette(palette)))

    @exposed(require_auth=False)
    def app_quit(self):
        """Fin de l'animation de fermeture : la fenêtre peut se fermer."""
        self._quit_ready.set()

    def _on_closing(self):
        """Fermeture de la fenêtre : on la retient le temps de l'animation du Graoully (si elle est active)."""
        if self._closing or local_prefs.get("anim", "on") == "off" or self._window is None:
            return True
        self._closing = True
        threading.Thread(target=self._close_after_outro, daemon=True).start()
        return False                                   # annule cette fermeture ; on la refait après l'animation

    def _close_after_outro(self):
        try:
            playing = self._window.evaluate_js("!!(window.BC && BC.intro && BC.intro.outro())")
        except Exception:
            playing = False
        if playing:
            self._quit_ready.wait(2.0)                 # filet de sécurité si l'interface ne répond pas
        try:
            self._window.destroy()
        except Exception:
            log.exception("Fermeture de la fenêtre impossible")
            os._exit(0)

    @exposed(require_auth=False)
    def auth_restore(self):
        profile = self._auth.restore()
        if not profile:
            return {"ok": False, "msg": ""}
        return self._after_login(profile)

    @exposed(require_auth=False)
    def auth_register(self, email, pseudo, password, accepted=False):
        if accepted is not True:
            raise security.ValidationError("Pour créer un compte, acceptez les conditions d'utilisation et la politique de confidentialité.")
        self._legal_pending = self._legal.current_version()
        res = self._auth.register(email, pseudo, password)
        if not res["verification_required"]:
            return self._after_login(res["profile"])
        return {"verification_required": True, "email": res["email"],
                "msg": "Compte créé. Entrez le code reçu par email."}

    @exposed(require_auth=False)
    def auth_verify(self, email, code, remember):
        return self._after_login(self._auth.verify_signup(email, code, remember))

    @exposed(require_auth=False)
    def auth_resend(self, email):
        self._auth.resend(email)
        return {"msg": "Nouveau code envoyé."}

    @exposed(require_auth=False)
    def auth_login(self, email, password, remember):
        return self._after_login(self._auth.login(email, password, remember))

    @exposed(require_auth=False)
    def auth_reset_start(self, email):
        self._auth.reset_start(email)
        return {"msg": "Si ce compte existe, un code a été envoyé par email."}

    @exposed(require_auth=False)
    def auth_reset_finish(self, email, code, new_password, remember):
        return self._after_login(self._auth.reset_finish(email, code, new_password, remember))

    @exposed()
    def auth_logout(self):
        self._printer.disconnect()
        self._store.reset_cache()
        self._auth.logout()

    @exposed()
    def profile_save(self, pseudo, avatar_data):
        try:
            return {"profile": self._auth.update_profile(pseudo, avatar_data)}
        except SupabaseError as e:
            if e.code == "23505":
                raise security.ValidationError("Ce pseudo est déjà pris.")
            raise

    @exposed()
    def password_change(self, new_password):
        self._auth.change_password(new_password)
        return {"msg": "Mot de passe modifié."}

    # ========================================================== Tableau
    @exposed()
    def dashboard(self):
        workshop, workshop_error = self._sync_workshop()
        jobs = {
            "settings": self._pool.submit(self._store.settings),
            "spools": self._pool.submit(self._store.spools),
            "tasks": self._pool.submit(self._store.tasks),
            "maintenance": self._pool.submit(self._store.maintenance),
            "shopping": self._pool.submit(self._store.shopping),
            "projects": self._pool.submit(self._store.projects),
            "bookmarks": self._pool.submit(self._store.bookmarks),
        }
        out = {name: job.result(timeout=30) for name, job in jobs.items()}
        self._check_alerts(out)
        out["reservations"] = graoully_actions.reservations(self._graoully.folder)     # 5.3 : bobines réservées (ce PC)
        out["forecast"] = forecast.shortages(self._queue_order(out["tasks"]), [s for s in out["spools"] if s.get("mine") is not False])
        out["profile"] = self._auth.profile
        out["printer"] = self._printer.public_config()
        out["telemetry"] = self._printer.telemetry()
        out["fleet"] = self._printer.fleet()
        out["workshop"] = workshop
        out["season"] = self._season.current()
        if workshop_error:
            out["workshop_error"] = workshop_error
        return out

    @staticmethod
    def _queue_order(tasks):
        """Impressions « à faire » dans l'ordre de la file (position, puis date limite, puis ancienneté)."""
        todo = [x for x in tasks or [] if x.get("status") == "todo"]
        todo.sort(key=lambda x: (x.get("position") if isinstance(x.get("position"), (int, float)) else 1e9,
                                 str(x.get("due_on") or "9999"), str(x.get("created_at") or "")))
        return todo

    @exposed()
    def forecast_to_shopping(self):
        """5.3 : les ruptures prévues d'après la file d'attente vont dans la liste de courses (sans doublon)."""
        spools = [s for s in self._store.spools() if s.get("mine") is not False]
        short = forecast.shortages(self._queue_order(self._store.tasks()), spools)
        wanted = {str(it.get("name") or "").strip().lower() for it in self._store.shopping() if not it.get("done")}
        added = 0
        for x in short:
            name = forecast.shopping_name(x)
            if name.lower() not in wanted:
                self._store.add_shopping(name, "Filament", "Urgent" if x.get("due") else "Normale")
                wanted.add(name.lower())
                added += 1
        return {"shopping": self._store.shopping(), "forecast": short,
                "msg": f"{added} article(s) ajouté(s) à la liste de courses." if added else "Déjà dans la liste de courses."}

    # ================================================= Mode entreprise (3.7)
    def _enterprise(self, key):
        return feature_enabled(self._store.settings(), key)

    def _require(self, key):
        if not self._enterprise(key):
            label = {"workshop": "Atelier partagé", "orders": "Commandes clients", "invoices": "Factures",
                     "urssaf": "Déclaration URSSAF", "planning": "Planning", "clients": "Carnet de clients",
                     "quotes": "Devis", "catalog": "Catalogue et stock"}.get(key, key)
            raise security.ValidationError(f"Activez d'abord « {label} » dans Réglages › Fonctions (mode entreprise).")

    def _sync_workshop(self, info=None):
        """Charge l'atelier (si la fonction est active) et dit au stock quelles lignes partagées lire.
        Renvoie (infos pour l'interface ou None, message d'erreur ou "")."""
        if not self._enterprise("workshop"):
            self._store.set_workshop(None)
            return None, ""
        try:
            info = info if info is not None else self._workshop.get()
        except (security.ValidationError, SupabaseError) as e:
            self._store.set_workshop(None)      # migration 005 absente, réseau… : on continue sans atelier
            return None, str(e)
        self._store.set_workshop(workshop_mod.store_scope(info))
        return info, ""

    def _workshop_out(self, info, msg=""):
        info, _ = self._sync_workshop(info)
        out = {"workshop": info, "printers": self._workshop_printers()}
        if msg:
            out["msg"] = msg
        return out

    @staticmethod
    def _printer_key(row):
        return (row.get("protocol") or "", (row.get("host") or "").strip().lower(), (row.get("serial") or "").strip().upper())

    def _workshop_printers(self):
        """Imprimantes partagées de l'atelier. Avec « Plusieurs imprimantes », in_fleet dit si celle d'un autre
        membre est déjà dans MON parc : il l'ajoute comme une imprimante à lui (Réglages › Imprimante) et
        saisit le code d'accès sur son PC, où il reste (coffre-fort) ; la ligne partagée n'en a jamais."""
        rows = self._store.workshop_printers()
        if rows and self._enterprise("multi_printer"):
            own = {self._printer_key(r) for r in self._store.printers()}
            for r in rows:
                if not r.get("mine"):
                    r["in_fleet"] = self._printer_key(r) in own
        return rows

    def _workshop_full(self):
        info, error = self._sync_workshop()
        if error:
            raise security.ValidationError(error)
        mine = self._store.printer()
        return {"workshop": info, "printers": self._workshop_printers(),
                "my_printer": {"name": mine.get("name", ""), "protocol": mine.get("protocol", ""),
                               "shared": mine.get("workshop_id") is not None} if mine else None}

    @exposed()
    def workshop_info(self):
        self._require("workshop")
        return self._workshop_full()

    @exposed()
    def workshop_create(self, name):
        self._require("workshop")
        return self._workshop_out(self._workshop.create(name), "Atelier créé : invitez vos amis.")

    @exposed()
    def workshop_rename(self, name):
        self._require("workshop")
        return self._workshop_out(self._workshop.rename(name), "Atelier renommé.")

    @exposed()
    def workshop_invite(self, target, role):
        self._require("workshop")
        pseudo = self._workshop.invite(target, role)
        return self._workshop_out(self._workshop.get(), f"Invitation envoyée à {pseudo}. Elle apparaîtra dans son onglet Atelier.")

    @exposed()
    def workshop_respond(self, workshop_id, accept):
        self._require("workshop")
        info = self._workshop.respond(workshop_id, accept)
        return self._workshop_out(info, "Bienvenue dans l'atelier !" if accept is True else "Invitation refusée.")

    @exposed()
    def workshop_set_role(self, user_id, role):
        self._require("workshop")
        return self._workshop_out(self._workshop.set_role(user_id, role), "Rôle modifié.")

    @exposed()
    def workshop_remove(self, user_id):
        self._require("workshop")
        return self._workshop_out(self._workshop.remove(user_id), "Membre retiré : il n'a plus accès à l'atelier.")

    @exposed()
    def workshop_leave(self):
        self._require("workshop")
        return self._workshop_out(self._workshop.leave(), "Vous avez quitté l'atelier.")

    @exposed()
    def workshop_delete(self, confirmed):
        self._require("workshop")
        if confirmed is not True:
            raise security.ValidationError("Confirmez la suppression de l'atelier.")
        return self._workshop_out(self._workshop.delete(), "Atelier supprimé : chacun garde ses propres données.")

    @exposed()
    def workshop_share_printer(self, shared):
        self._require("workshop")
        self._sync_workshop()
        self._store.share_printer(shared is True)
        return dict(self._workshop_full(), msg="Imprimante partagée avec l'atelier (sans son code d'accès)." if shared is True
                    else "Imprimante reprise : elle n'est plus partagée.")

    # ---- Commandes clients
    def _orders_payload(self):
        self._require("orders")
        info, _ = self._sync_workshop()
        uid = self._client.user_id
        rows = self._orders.list(self._store.workshop_id())
        log = self._store.settings().get("print_log") or []
        for row in rows:
            if row.get("user_id") != uid:
                continue
            synced = orders.sync_prints(row, log)
            if synced:
                prints, cost = synced
                try:
                    row.update(self._orders.update(row["id"], {"prints": prints, "real_cost": cost}))
                except security.ValidationError:
                    pass
        items = [orders.decorate(r, uid) for r in rows]
        links = self._invoice_links()
        by_order, by_quote = links.get("order") or {}, links.get("quote") or {}
        for it in items:
            if it["mine"]:      # facturée directement, ou depuis son devis
                it["invoice_number"] = by_order.get(str(it.get("id"))) or by_quote.get(str(it.get("quote_id")), "")
        pseudos = (workshop_mod.store_scope(info) or {}).get("pseudos") or {}
        for it in items:
            if not it["mine"]:
                it["owner_pseudo"] = pseudos.get(it.get("user_id"), "")
        return {"orders": items, "summary": orders.dashboard(items), "can_share": self._store.can_manage_workshop(),
                "carriers": [[k, v[0]] for k, v in catalog.CARRIERS.items()],
                "journal": [{"id": e.get("id"), "file": e.get("file"), "ts": e.get("ts"), "cost": e.get("cost")}
                            for e in log[:60] if e.get("id")]}

    @exposed()
    def orders_list(self):
        return self._orders_payload()

    @exposed()
    def order_from_quote(self, quote_id, due_on=None, shared=False):
        self._require("orders")
        self._sync_workshop()
        quote = self._quotes.get(quote_id)
        if quote.get("status") != "accepted":
            raise security.ValidationError("Seul un devis accepté devient une commande : changez d'abord son statut.")
        if self._orders.for_quote(quote["id"]):
            raise security.ValidationError(f"Une commande existe déjà pour le devis {quote.get('number')}.")
        values = orders.from_quote(quote)
        values["due_on"] = orders.clean_date(due_on)
        values["workshop_id"] = self._store.workshop_scope(shared is True)
        synced = orders.sync_prints(values, self._store.settings().get("print_log") or [])
        if synced:
            values["prints"], values["real_cost"] = synced
        self._orders.insert(values)
        return dict(self._orders_payload(), msg=f"Commande créée depuis le devis {quote.get('number')}.")

    @exposed()
    def order_save(self, data):
        self._require("orders")
        self._sync_workshop()
        data = data if isinstance(data, dict) else {}
        values = orders.clean_edit(data, self._store.can_manage_workshop())
        if "shared" in data:
            values["workshop_id"] = self._store.workshop_scope(data.get("shared") is True)
        if data.get("id"):
            try:
                self._orders.update(data["id"], values)
            except security.ValidationError as e:
                # Colonne « activity » (3.9) absente : c'est la migration 006 qui manque, pas la 005.
                if "activity" in values and str(e) == orders.MIGRATION_HELP:
                    raise security.ValidationError(orders.MIGRATION_006_HELP) from None
                raise
            msg = "Commande enregistrée."
        else:
            if not values.get("title"):
                raise security.ValidationError("Donnez un titre à la commande.")
            if not (values.get("client") or {}).get("name"):
                raise security.ValidationError("Indiquez le client.")
            values.setdefault("price_ttc", values.get("price_ht", 0))
            try:
                self._orders.insert(values)
            except security.ValidationError as e:
                if "activity" in values and str(e) == orders.MIGRATION_HELP:
                    raise security.ValidationError(orders.MIGRATION_006_HELP) from None
                raise
            msg = "Commande créée."
        return dict(self._orders_payload(), msg=msg)

    @exposed()
    def order_status(self, order_id, status):
        self._require("orders")
        if status not in orders.STATUSES:
            raise security.ValidationError("Statut inconnu.")
        self._orders.update(order_id, {"status": status})
        return self._orders_payload()

    @exposed()
    def order_link_print(self, order_id, entry_id, linked):
        """Ajoute (ou retire) une impression du journal à une commande : son coût entre dans le coût réel."""
        self._require("orders")
        order = self._orders.get(order_id)
        prints = [p for p in order.get("prints") or [] if isinstance(p, dict) and p.get("id") != entry_id]
        if linked is True:
            prints.insert(0, orders.print_snapshot(self._journal_entry(entry_id)))
        prints = prints[:orders.MAX_PRINTS]
        self._orders.update(order["id"], {"prints": prints, "real_cost": real_cost.order_summary({"prints": prints})["real_cost"]})
        return dict(self._orders_payload(), msg="Impression liée à la commande." if linked is True else "Impression retirée de la commande.")

    @exposed()
    def order_delete(self, order_id):
        self._require("orders")
        self._orders.delete(order_id)
        return dict(self._orders_payload(), msg="Commande supprimée.")

    # ---- Expédition des commandes (5.5)
    def _shipping_order(self, order_id):
        order = self._orders.get(order_id)
        if order.get("user_id") != self._client.user_id:
            raise security.ValidationError("Seul l'auteur de la commande prépare son expédition.")
        return order

    @exposed()
    def order_shipping(self, order_id, data, deliver=False):
        """Enregistre l'expédition (transporteur, suivi, date, adresse) ; `deliver` passe la commande en « livrée »."""
        self._require("orders")
        order = self._shipping_order(order_id)
        shipping = catalog.clean_shipping(data)
        values = {"shipping": shipping}
        if deliver is True:
            values["status"] = "delivered"
        try:
            self._orders.update(order["id"], values)
        except (security.ValidationError, SupabaseError) as e:
            if catalog.missing_table(e) or str(e) == orders.MIGRATION_HELP:
                raise security.ValidationError(catalog.MIGRATION_HELP) from None
            raise
        order["shipping"] = shipping
        return dict(self._orders_payload(), message=catalog.shipped_message(order, self._seller_or_default()),
                    msg="Expédition enregistrée." + (" Commande marquée comme livrée." if deliver is True else ""))

    @exposed()
    def order_shipping_message(self, order_id):
        self._require("orders")
        order = self._shipping_order(order_id)
        return {"message": catalog.shipped_message(order, self._seller_or_default()),
                "tracking_url": catalog.tracking_url(order.get("shipping"))}

    @exposed()
    def order_delivery_note(self, order_id, open_it=False):
        self._require("orders")
        order = self._shipping_order(order_id)
        try:
            seller, logo = self._quotes.seller()
        except (security.ValidationError, SupabaseError):
            seller, logo = {}, ""
        if order.get("quote_id") and self._enterprise("quotes"):
            try:      # les lignes du devis donnent le détail des articles livrés
                order["lines"] = self._quotes.get(order["quote_id"]).get("lines") or []
            except (security.ValidationError, SupabaseError):
                pass
        path = catalog.save_delivery_note(order, seller, logo, os.path.join(config.EXPORT_DIR, "livraisons"))
        if open_it is True:
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "Bon de livraison enregistré : " + path}

    # ---- Catalogue de produits et stock de pièces finies (5.5)
    def _catalog_payload(self, msg=""):
        self._require("catalog")
        rows = self._products.list()
        try:
            tasks = self._store.tasks()
        except (security.ValidationError, SupabaseError):
            tasks = []
        items = [catalog.decorate_product(p, catalog.queued_pieces(p, tasks)) for p in rows]
        active = [p for p in items if p.get("active", True)]
        out = {"products": items, "materials": MATERIALS,
               "summary": {"count": len(active), "low": sum(1 for p in active if p["low"]),
                           "pieces": sum(int(p.get("stock") or 0) for p in active),
                           "value": round(sum(p["value"] for p in active), 2)}}
        if msg:
            out["msg"] = msg
        return out

    @exposed()
    def catalog_list(self):
        return self._catalog_payload()

    @exposed()
    def catalog_save(self, data):
        self._require("catalog")
        data = data if isinstance(data, dict) else {}
        values = catalog.clean_product(data)
        if data.get("id"):
            self._products.update(data["id"], values)
            return self._catalog_payload("Article enregistré.")
        self._products.insert(values)
        return self._catalog_payload(f"« {values['name']} » ajouté au catalogue.")

    @exposed()
    def catalog_delete(self, product_id):
        self._require("catalog")
        self._products.delete(product_id)
        return self._catalog_payload("Article retiré du catalogue.")

    @exposed()
    def catalog_stock(self, product_id, delta):
        """Ajoute (pièces imprimées) ou retire (pièces vendues) du stock ; jamais en dessous de zéro."""
        self._require("catalog")
        delta = security.integer(delta, -100000, 100000, 0)
        product = self._products.get(product_id)
        stock = max(0, int(product.get("stock") or 0) + delta)
        self._products.update(product["id"], {"stock": stock})
        word = "ajoutée(s) au" if delta >= 0 else "retirée(s) du"
        return self._catalog_payload(f"{abs(delta)} pièce(s) {word} stock de « {product['name']} » : {stock} en stock.")

    @exposed()
    def catalog_restock(self, product_id, plates=None):
        """Remet l'article en file d'attente : une tâche par plateau (par défaut, de quoi repasser le seuil)."""
        self._require("catalog")
        product = self._products.get(product_id)
        tasks = self._store.tasks()
        if plates is None:
            plates = catalog.restock_plan(product, catalog.queued_pieces(product, tasks))[0] or 1
        plates = security.integer(plates, 1, 50, 1)
        for task in catalog.restock_tasks(product, plates):
            self._store.add_task(task)
        pieces = plates * max(1, int(product.get("batch") or 1))
        return self._catalog_payload(f"{plates} plateau(x) de « {product['name']} » ajouté(s) à la file d'attente ({pieces} pièces).")

    # ---- Recettes d'impression (5.5)
    def _print_recipes_payload(self, msg=""):
        rows = self._print_recipes.list()
        out = {"recipes": rows}
        if msg:
            out["msg"] = msg
        return out

    @exposed()
    def print_recipes_list(self):
        return self._print_recipes_payload()

    @exposed()
    def print_recipe_save(self, data):
        data = data if isinstance(data, dict) else {}
        values = catalog.clean_recipe(data)
        if data.get("id"):
            self._print_recipes.update(data["id"], values)
            return self._print_recipes_payload("Recette enregistrée.")
        self._print_recipes.insert(values)
        return self._print_recipes_payload(f"Recette « {values['name']} » enregistrée.")

    @exposed()
    def print_recipe_from_journal(self, entry_id):
        """Recette pré-remplie depuis une impression réussie du journal (rien n'est enregistré ici)."""
        entry = self._journal_entry(entry_id)
        if entry.get("success") is False:
            raise security.ValidationError("Cette impression a échoué : enregistrez plutôt une impression réussie.")
        return {"recipe": catalog.recipe_from_journal(entry, self._store.settings())}

    @exposed()
    def print_recipe_delete(self, recipe_id):
        self._print_recipes.delete(recipe_id)
        return self._print_recipes_payload("Recette supprimée.")

    @exposed()
    def print_recipe_print(self, recipe_id, slot=None):
        """Relance la recette : même fichier et même plateau, sur la bobine de l'AMS qui correspond le mieux."""
        recipe = self._print_recipes.get(recipe_id)
        if not recipe.get("file_name"):
            raise security.ValidationError("Cette recette n'a pas de fichier : indiquez le nom du fichier sur l'imprimante.")
        if not re.search(r"\.(3mf|gcode|gco|g|bgcode|bgc)$", recipe["file_name"], re.I):
            # Impression lancée depuis le slicer : le journal ne garde que le nom du projet, sans le fichier.
            raise security.ValidationError(f"« {recipe['file_name']} » n'est pas un nom de fichier de l'imprimante. Modifiez la recette "
                                           "et indiquez le fichier exact de l'écran Fichiers (par ex. « support.gcode.3mf »).")
        if slot is None:
            slot, spool = catalog.pick_slot(recipe, self._store.own_spools())
            if slot is None:
                raise security.ValidationError(spool)
        else:
            slot, spool = security.integer(slot, 0, 16, 0), None
        sent = self._printer.start_print(recipe["file_name"], slot, recipe.get("plate") or None)
        if not sent:
            raise security.ValidationError("Imprimante hors ligne : l'impression n'est pas lancée.")
        try:
            self._print_recipes.update(recipe["id"], {"uses": int(recipe.get("uses") or 0) + 1,
                                                      "last_used_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        except (security.ValidationError, SupabaseError):
            pass
        where = f" sur l'emplacement {slot} ({spool.get('name') or spool.get('material')})" if spool else ""
        return self._print_recipes_payload(f"« {recipe['name']} » relancée{where}.")

    # ======================================== Import des ventes Etsy, Shopify, Vinted (4.3)
    def _sales_parse(self, source, options):
        self._require("orders")
        options = options if isinstance(options, dict) else {}
        source = source if isinstance(source, dict) else {}
        security.filename(source.get("name"), (".csv", ".txt", ".tsv"))
        raw = security.b64_payload(source.get("data"), sales_import.MAX_BYTES)
        columns = options.get("columns") if isinstance(options.get("columns"), dict) else None
        parsed = sales_import.parse(raw, security.text(options.get("source"), 10), columns)
        fresh, dup = sales_import.split_new(parsed["sales"], self._orders.external_refs())
        return parsed, fresh, dup

    def _seller_or_default(self):
        """Régime de TVA du vendeur (Devis › Mes coordonnées) ; franchise si rien n'est renseigné."""
        try:
            return self._quotes.seller()[0]
        except (security.ValidationError, SupabaseError):
            return {"vat_mode": "franchise", "vat_rate": 20}

    @exposed()
    def orders_import_preview(self, source, options=None):
        """Ce que l'import ajouterait (rien n'est enregistré) : ventes reconnues, déjà importées, total."""
        parsed, fresh, dup = self._sales_parse(source, options)
        seller = self._seller_or_default()
        total = sum(s["ttc"] for s in fresh)
        return {"format": parsed["format"], "source": parsed["source"], "columns": parsed["columns"], "head": parsed["head"],
                "count": len(fresh), "duplicates": dup, "total_ttc": round(total, 2), "sales": fresh[:80],
                "truncated": parsed["truncated"], "vat_mode": seller.get("vat_mode", "franchise")}

    @exposed()
    def orders_import(self, source, options=None):
        parsed, fresh, dup = self._sales_parse(source, options)
        if not fresh:
            raise security.ValidationError("Rien de nouveau : toutes ces ventes sont déjà importées.")
        seller = self._seller_or_default()
        activity = "services" if (options or {}).get("activity") == "services" else "goods"
        rows = [sales_import.to_order(s, seller.get("vat_mode", "franchise"), seller.get("vat_rate", 20), activity) for s in fresh]
        self._orders.insert_many(rows)
        label = sales_import.SOURCE_LABEL.get(parsed["source"], "")
        more = f" ({dup} déjà présente(s), ignorée(s))" if dup else ""
        return dict(self._orders_payload(), msg=f"{len(rows)} vente(s) {label} importée(s){more}.")

    # ====================================== Carnet de clients et tableau de bord Pro (4.3)
    def _business_rows(self):
        """Devis, commandes (les miennes) et factures, selon les fonctions actives ; vide si la table manque."""
        def safe(fn):
            try:
                return fn()
            except (security.ValidationError, SupabaseError):
                return []
        uid = self._client.user_id
        quote_rows = safe(lambda: self._quotes.list(limit=5000)) if self._enterprise("quotes") else []
        order_rows = safe(lambda: [o for o in self._orders.list() if o.get("user_id") == uid]) if self._enterprise("orders") else []
        invoice_rows = safe(lambda: self._invoices.list(limit=5000)) if self._enterprise("invoices") else []
        return quote_rows, order_rows, invoice_rows

    def _clients_payload(self, rows=None):
        self._require("clients")
        quote_rows, order_rows, invoice_rows = rows or self._business_rows()
        ready, msg = True, ""
        try:
            saved = self._clients.list()
        except security.ValidationError as e:
            saved, ready, msg = [], False, str(e)
        people = clients_mod.directory(saved, quote_rows, order_rows, invoice_rows)
        return {"clients": people, "summary": clients_mod.summary(people), "ready": ready, "msg": msg}

    @exposed()
    def clients_list(self):
        return self._clients_payload()

    @exposed()
    def client_save(self, data):
        self._require("clients")
        row = self._clients.save(data if isinstance(data, dict) else {})
        return dict(self._clients_payload(), client=row, msg=f"Fiche de {row.get('name')} enregistrée.")

    @exposed()
    def client_delete(self, client_id):
        self._require("clients")
        self._clients.delete(client_id)
        return dict(self._clients_payload(), msg="Fiche supprimée (les devis, commandes et factures ne changent pas).")

    @exposed()
    def clients_suggest(self):
        """Noms proposés dans les formulaires (devis, commandes, factures) : fiches enregistrées seulement."""
        if not self._enterprise("clients"):
            return {"clients": []}
        try:
            saved = self._clients.list()
        except (security.ValidationError, SupabaseError):
            saved = []
        return {"clients": [{k: c.get(k) or "" for k in ("name", "company", "email", "address", "siren", "vat_number")} for c in saved]}

    @exposed()
    def pro_dashboard(self):
        if not self._enterprise("quotes") and not self._enterprise("orders") and not self._enterprise("invoices"):
            raise security.ValidationError("Activez les devis, les commandes ou les factures dans Réglages › Fonctions (mode Pro).")
        rows = self._business_rows()
        people = None
        if self._enterprise("clients"):
            try:
                people = self._clients_payload(rows)["clients"]
            except (security.ValidationError, SupabaseError):
                people = None
        plan = None
        if self._enterprise("planning"):
            try:
                plan = self._planning_payload()
                plan = plan if plan.get("ready") else None
            except (security.ValidationError, SupabaseError):
                plan = None
        out = pro_dashboard.build(*rows, people=people, plan=plan)
        out["features"] = {k: self._enterprise(k) for k in ("quotes", "orders", "invoices", "clients", "planning", "urssaf")}
        return out

    # ================================================= Planning par imprimante (4.3)
    def _planning_payload(self):
        self._require("planning")
        self._sync_workshop()
        settings = self._store.settings()
        if not self._store.planning_ready():
            return {"ready": False, "msg": planning.MIGRATION_HELP}
        tasks = self._store.tasks()
        rows = self._store.printers() if self._enterprise("multi_printer") else [r for r in [self._store.printer()] if r]
        printers = [{"id": r.get("id"), "name": r.get("name") or r.get("host") or "Imprimante"} for r in rows]
        busy = {}
        fleet = self._printer.fleet()
        if fleet:
            busy = {c.get("id"): c.get("remaining_min") for c in fleet if c.get("state") in ("printing", "paused") and c.get("remaining_min")}
        elif printers:
            t = self._printer.telemetry() or {}
            if t.get("printing") and t.get("remaining_min"):
                busy = {printers[0]["id"]: t.get("remaining_min")}
        rate, personal = planning.rate_from_log(settings.get("print_log") or [])
        night = (settings.get("planning") or {}).get("night")
        out = planning.plan(tasks, printers, dt.datetime.now().replace(second=0, microsecond=0), night=night, rate=rate, busy=busy)
        out.update({"ready": True, "printers": printers, "personal_rate": personal,
                    "tasks": [t for t in tasks if t.get("status") in ("todo", "printing")]})
        if self._enterprise("orders"):
            try:
                uid = self._client.user_id
                out["orders"] = [{"id": o.get("id"), "title": o.get("title"), "client": (o.get("client") or {}).get("name", ""),
                                  "due_on": o.get("due_on")} for o in self._orders.list(self._store.workshop_id())
                                 if o.get("status") in orders.OPEN and o.get("user_id") == uid]
            except (security.ValidationError, SupabaseError):
                out["orders"] = []
        return out

    def _planning_write(self, fn):
        try:
            fn()
        except SupabaseError as e:
            if e.code == "PGRST204" or "column" in str(e).lower():
                raise security.ValidationError(planning.MIGRATION_HELP) from None
            raise

    @exposed()
    def planning_get(self):
        return self._planning_payload()

    @exposed()
    def planning_task_save(self, task_id, data):
        """Durée, imprimante, commande liée ou date limite d'une impression de la file."""
        self._require("planning")
        values = planning.clean_fields(data)
        if values.get("order_id") and not self._enterprise("orders"):
            values.pop("order_id")
        if values.get("order_id") and not (data or {}).get("due_on"):
            try:                              # date limite reprise de la commande, si elle en a une
                due = self._orders.get(values["order_id"]).get("due_on")
                if due:
                    values["due_on"] = str(due)[:10]
            except security.ValidationError:
                pass
        self._planning_write(lambda: self._store.update_task(task_id, values))
        return dict(self._planning_payload(), msg="Planning mis à jour.")

    @exposed()
    def planning_move(self, task_id, delta):
        self._require("planning")
        tasks = self._store.tasks()
        positions = planning.move(tasks, security.record_id(task_id), security.integer(delta, -1, 1, 1))
        self._planning_write(lambda: self._save_positions(tasks, positions))
        return self._planning_payload()

    @exposed()
    def planning_sort_due(self):
        self._require("planning")
        tasks = self._store.tasks()
        self._planning_write(lambda: self._save_positions(tasks, planning.by_due_date(tasks)))
        return dict(self._planning_payload(), msg="File rangée par date limite.")

    def _save_positions(self, tasks, positions):
        current = {t.get("id"): int(t.get("position") or 0) for t in tasks}
        for tid, pos in positions.items():
            if current.get(tid) != pos:
                self._store.update_task(tid, {"position": pos})

    @exposed()
    def planning_night_save(self, night):
        self._require("planning")
        self._store.patch_from_ui({"planning": {"night": night}})
        return dict(self._planning_payload(), msg="Plage de nuit enregistrée.")

    def _check_alerts(self, out=None):
        """Entretien dû et stock sous le seuil : une alerte par rappel et par échéance, jamais en double.
        Appelé à l'ouverture (tableau), après chaque action et à la fin d'une impression."""
        with self._alert_lock:
            try:
                self._check_maintenance(out)
                self._check_stock(out)
            except Exception:
                log.exception("Vérification des alertes d'entretien et de stock impossible")

    def _check_maintenance(self, out):
        settings = self._store.settings()
        hours = float((settings.get("stats") or {}).get("total_hours_printed") or 0)
        reminders = out["maintenance"] if out else self._store.maintenance()
        before = settings.get("maint_notified") or {}
        fresh, memory = maintenance_presets.due_to_notify(reminders, hours, before)
        if memory != before:
            settings = self._store.update_settings("maint_notified", lambda _old: memory)
            if out:
                out["settings"] = settings
        if fresh:
            self._notifier.notify("maintenance", "Entretien à faire", maintenance_presets.due_message(fresh, hours), "warning")

    def _check_stock(self, out):
        settings = self._store.settings()
        before = settings.get("stock_notified") or {}
        if not (settings.get("features") or {}).get("stock_alerts"):
            return
        spools = out["spools"] if out else self._store.spools()
        crossed, memory = stock_alerts.evaluate(settings.get("stock_rules") or [], spools, before)
        if memory != before:
            settings = self._store.update_settings("stock_notified", lambda _old: memory)
            if out:
                out["settings"] = settings
        if not crossed:
            return
        shopping = self._store.shopping()
        wanted = {str(it.get("name") or "").strip().lower() for it in shopping if not it.get("done")}
        for rule in crossed:
            name = stock_alerts.shopping_name(rule)
            if name.lower() not in wanted:
                self._store.add_shopping(name, "Filament", "Urgent" if not rule["have"] else "Normale")
                wanted.add(name.lower())
            self._notifier.notify("stock", "Stock bas : " + stock_alerts.label(rule), stock_alerts.message(rule), "warning")
        if out:
            out["shopping"] = self._store.shopping()

    @exposed()
    def live(self):
        self._watch_tick()
        return {"telemetry": self._printer.telemetry(), "events": self._events.drain(), "fleet": self._printer.fleet()}

    # ============================================ Import depuis le slicer
    @staticmethod
    def _watch_folder():
        """Dossier surveillé choisi sur ce PC, ou "" : aucun dossier par défaut (revue de sécurité 3.7 :
        Téléchargements recevait n'importe quel fichier venu d'internet). L'utilisateur le choisit."""
        return local_prefs.get("slicer_watch_dir", "") or ""

    def _watch_tick(self):
        """Toutes les 5 s au plus (appelé par « live ») : liste du dossier surveillé, sans lecture."""
        now = time.monotonic()
        if now - self._watch_last < 5:
            return
        self._watch_last = now
        try:
            folder = self._watch_folder()
            if not folder or not (self._store.settings().get("features") or {}).get("slicer_watch"):
                self._watch.reset()
                return
            for path, size in self._watch.scan(folder, force=True):
                if size <= config.MAX_FILE_BYTES:
                    self._pool.submit(self._watch_announce, path)
        except Exception:
            log.debug("Surveillance du dossier du slicer", exc_info=True)

    def _watch_announce(self, path):
        """Fichier découpé terminé : lu une fois, analysé, puis proposé à l'interface."""
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw = f.read(config.MAX_FILE_BYTES + 1)
            if len(raw) > config.MAX_FILE_BYTES:
                return
            a = analyze_bytes(name, raw)
        except Exception:
            log.info("Fichier du slicer illisible : %s", name)
            return
        self._events.push("slicer_file", name=name, title=a.get("title") or name,
                          grams=a.get("grams") or 0, hours=a.get("duration_h") or 0,
                          material=a.get("material") or "", nozzle=a.get("nozzle") or "",
                          plates=a.get("plates") or 1)

    def _watch_info(self):
        folder = self._watch_folder()
        return {"folder": folder, "custom": bool(folder), "exists": bool(folder) and os.path.isdir(folder)}

    @exposed()
    def slicer_watch_info(self):
        return self._watch_info()

    @exposed()
    def slicer_watch_pick(self):
        """Choix du dossier surveillé (préférence de ce PC, jamais enregistrée dans le compte)."""
        if device.ANDROID:
            raise security.ValidationError("La surveillance du dossier du slicer se règle sur le PC.")
        if not self._window:
            raise security.ValidationError("Fenêtre indisponible.")
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        paths = self._window.create_file_dialog(dialog, directory=self._watch_folder() or watch_default_folder())
        if not paths:
            return {"ok": False, "msg": ""}
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        if str(path).startswith(("\\\\", "//")) or not os.path.isdir(path):
            raise security.ValidationError("Choisissez un dossier de ce PC.")
        local_prefs.put("slicer_watch_dir", os.path.abspath(path))
        return dict(self._watch_info(), msg="Dossier surveillé : " + os.path.abspath(path))

    @exposed()
    def slicer_watch_reset(self):
        local_prefs.put("slicer_watch_dir", "")
        self._watch.reset()
        return dict(self._watch_info(), msg="Plus aucun dossier n'est surveillé.")

    @exposed()
    def settings_save(self, patch):
        patch = patch if isinstance(patch, dict) else {}
        settings = self._store.patch_from_ui(patch)
        if "features" in patch:
            self._printer.refresh_fleet()   # mode entreprise › Plusieurs imprimantes activé ou coupé
        return {"settings": settings}

    @exposed()
    def alert_test(self):
        self._notifier.send_test_email()
        return {"msg": "Email de test envoyé à l'adresse de votre compte."}

    @exposed()
    def desktop_test(self):
        """5.4 : notification Windows d'essai (affichée même si Graou3D est au premier plan)."""
        self._notifier.send_test_desktop()
        return {"msg": "Notification envoyée : regardez en bas à droite de l'écran."}

    @exposed()
    def push_test(self):
        self._notifier.send_test_push()
        return {"msg": "Notification envoyée : regardez votre téléphone."}

    @exposed()
    def remote_set(self, enabled):
        """Active la télécommande du téléphone ; le canal de commande secret est créé ici."""
        enabled = security.boolean(enabled)
        settings = self._store.settings()
        topic = (settings.get("alerts") or {}).get("push_topic") or ""
        problem = topic_problem(topic) if enabled else None
        if problem:
            raise security.ValidationError(problem)
        remote = dict(settings.get("remote") or {})
        remote["enabled"] = enabled
        if enabled and not is_strong_cmd_topic(remote.get("cmd_topic")):
            remote["cmd_topic"] = new_cmd_topic()     # 128 bits ; un ancien canal plus court est remplacé
        return {"settings": self._store.patch_from_ui({"remote": remote})}

    @exposed()
    def remote_test(self):
        settings = self._store.settings()
        remote = settings.get("remote") or {}
        if not remote.get("enabled"):
            raise security.ValidationError("Activez d'abord la télécommande.")
        actions = self._remote.test_actions()
        if not actions:
            raise security.ValidationError("Télécommande inactive : vérifiez le canal ntfy.")
        self._notifier.push_only("Télécommande prête 🐉", "Pendant une impression, les notifications porteront ces "
                                 "boutons. Ceux-ci sont des essais : touchez-en un, il ne commande rien.",
                                 "info", actions)
        return {"msg": "Notification envoyée avec ses boutons : regardez votre téléphone."}

    # =========================================================== Séchage
    @exposed()
    def drying_start(self, spool_id, temp, hours):
        spool = next((s for s in self._store.spools() if str(s.get("id")) == str(spool_id)), None)
        if not spool:
            raise security.ValidationError("Bobine introuvable.")
        item = self._scheduler.drying_add(spool, temp, hours)
        return {"item": item, "msg": f"Séchage lancé : {item['label']}, {item['hours']} h à {item['temp']} °C. "
                                     "Je vous préviens à la fin (l'application doit rester ouverte)."}

    @exposed()
    def drying_cancel(self, item_id):
        self._scheduler.drying_cancel(item_id)
        return {"msg": "Minuteur de séchage annulé."}

    @exposed()
    def drying_preset(self, material):
        temp, hours = drying_preset(material)
        return {"temp": temp, "hours": hours}

    @exposed()
    def egg_found(self, egg_id):
        return {"eggs_found": self._store.record_egg(egg_id).get("eggs_found", [])}

    @exposed()
    def print_outcome(self, success, grams):
        settings = self._store.record_print(bool(success), grams)
        spool = self._store.consume_filament(security.integer(grams, 0, 10000, 0)) if success else {}
        return {"settings": settings, "emptied": bool(spool.get("emptied"))}

    @exposed()
    def print_add_grams(self, grams):
        """Grammes saisis après une impression dont le poids était inconnu."""
        grams = security.integer(grams, 1, 10000, 0)

        def mutate(stats):
            stats["total_grams_printed"] = int(stats.get("total_grams_printed", 0)) + grams
            return stats

        self._store.update_settings("stats", mutate)
        slot = self._printer.telemetry().get("active_tray")
        spool = self._store.consume_filament(grams, slot if isinstance(slot, int) else None)
        return {"emptied": bool(spool.get("emptied")),
                "msg": f"{grams} g retirés de {spool.get('spool') or 'la bobine active'}."}

    @exposed()
    def print_usage_fix(self, entry_id, before, after):
        """3.8 : grammes par couleur vérifiés après une impression. before = ce qui a déjà été retiré
        (envoyé avec la fin d'impression), after = ce que l'utilisateur a validé ; seule la différence est
        appliquée aux bobines, aux statistiques et à la ligne du journal."""
        own = {str(s.get("id")) for s in self._store.own_spools()}
        before = print_usage.clean_rows(before, own | {str(r.get("spool_id")) for r in before or []
                                                       if isinstance(r, dict) and r.get("spool_id")})
        after = print_usage.clean_rows(after, own)
        emptied = False
        for spool_id, grams in print_usage.deltas(before, after).items():
            if spool_id in own:
                emptied = bool(self._store.adjust_spool(spool_id, -grams).get("emptied")) or emptied
        old_total = sum(r["grams"] for r in before)
        total = sum(r["grams"] for r in after)
        diff = total - old_total
        if diff:
            def mutate(stats):
                stats["total_grams_printed"] = max(0, int(stats.get("total_grams_printed", 0)) + diff)
                return stats
            self._store.update_settings("stats", mutate)
        entry_id = security.text(entry_id, 16)
        if entry_id:
            price = float((self._store.settings().get("quote") or {}).get("spool_price", 23.0))

            def fix(items):
                for it in items or []:
                    if it.get("id") == entry_id:
                        it["grams"] = total
                        it["cost"] = round(max(0.0, float(it.get("cost") or 0) + diff / 1000.0 * price), 2)
                        if len([r for r in after if r["grams"]]) > 1:
                            it["colors"] = print_usage.compact(after)
                        else:
                            it.pop("colors", None)
                return items or []
            self._store.update_settings("print_log", fix)
        return {"emptied": emptied, "msg": f"Filament enregistré : {total} g."}

    # ============================================================ Journal
    @exposed()
    def journal_photo(self, entry_id):
        return {"photo": self._printer.journal_photo(entry_id)}

    @exposed()
    def journal_delete(self, entry_id):
        self._store.delete_journal(entry_id)
        self._printer.journal_photo_delete(entry_id)
        return {"msg": "Impression retirée du journal."}

    def _journal_entry(self, entry_id):
        entry_id = security.text(entry_id, 16)
        entry = next((e for e in self._store.settings().get("print_log") or [] if e.get("id") == entry_id), None)
        if not entry:
            raise security.ValidationError("Impression introuvable dans le journal.")
        return entry

    @staticmethod
    def _recost(entry, grams, price_kg, settings):
        """Coût recalculé après une pesée ou un changement de prix matière (électricité et usure gardées)."""
        detail = entry.get("cost_detail")
        if not detail:
            return {"cost": print_cost(grams, float(entry.get("hours") or 0), settings.get("quote"))}
        kg = price_kg or (settings.get("quote") or {}).get("spool_price", 23.0)
        matter = round(grams / 1000.0 * float(kg), 2)
        return {"cost": round(matter + float(detail.get("power") or 0) + float(detail.get("wear") or 0), 2),
                "cost_detail": dict(detail, matter=matter)}

    @exposed()
    def journal_set_grams(self, entry_id, grams):
        """Grammes pesés après coup (bobine pesée avant / après) : le coût est recalculé."""
        entry = self._journal_entry(entry_id)
        grams = security.integer(grams, 0, 100000, 0)
        settings = self._store.settings()
        fields = dict(self._recost(entry, grams, entry.get("price_kg"), settings), grams=grams, grams_source="weighed")
        return {"settings": self._store.patch_journal(entry["id"], fields), "msg": "Poids corrigé."}

    @exposed()
    def journal_link_quote(self, entry_id, quote_id, line_index=None, pieces=None):
        """Rattache (ou détache, quote_id vide) une impression du journal à un devis."""
        entry = self._journal_entry(entry_id)
        settings = self._store.settings()
        if not quote_id:
            fields = {k: None for k in _LINK_KEYS}
            fields.update(self._recost(entry, int(entry.get("grams") or 0), None, settings))
            return {"settings": self._store.patch_journal(entry["id"], fields), "msg": "Impression détachée du devis."}
        quote = self._quotes.get(quote_id)
        fields = real_cost.link_fields(quote, line_index, pieces)
        fields.update(self._recost(entry, int(entry.get("grams") or 0), fields.get("price_kg"), settings))
        return {"settings": self._store.patch_journal(entry["id"], fields),
                "msg": f"Impression rattachée au devis {quote.get('number')}."}

    @exposed()
    def print_quote_link(self, quote_id, line_index=None, pieces=None):
        """Devis choisi au lancement : la prochaine impression terminée y sera rattachée dans le journal."""
        if not quote_id:
            self._printer.set_quote_link(None)
            return {}
        quote = self._quotes.get(quote_id)
        self._printer.set_quote_link(real_cost.link_fields(quote, line_index, pieces))
        return {"msg": f"La prochaine impression sera rattachée au devis {quote.get('number')}."}

    # ============================================================== Devis
    @exposed()
    def quotes_list(self):
        seller, logo = self._quotes.seller()
        items = self._quotes.list()
        log = self._store.settings().get("print_log") or []
        for q in items:
            q["real"] = real_cost.quote_summary(q, log)
        if self._enterprise("orders"):
            # Devis déjà suivis comme commande : l'écran Devis propose « Voir la commande ».
            try:
                linked = {str(o.get("quote_id")): o.get("id") for o in self._orders.quote_links()}
            except (security.ValidationError, SupabaseError):
                linked = {}
            for q in items:
                q["order_id"] = linked.get(str(q.get("id")))
        billed = self._invoice_links().get("quote") or {}
        for q in items:
            q["invoice_number"] = billed.get(str(q.get("id")), "")
        return {"quotes": items, "seller": seller, "logo": logo}

    @exposed()
    def quote_seller_save(self, profile, logo=None):
        seller, logo = self._quotes.save_seller(profile, logo)
        return {"seller": seller, "logo": logo, "msg": "Coordonnées enregistrées."}

    @exposed()
    def quote_preview(self, data):
        """Calcul en direct pendant la saisie (rien n'est enregistré)."""
        values = clean_quote(data, {}, self._store.settings().get("quote") or {})
        return {"lines": values["lines"], "totals": values["totals"]}

    # ------------------------------------------------ 4.3 : devis depuis un STL
    @staticmethod
    def _estimate_materials():
        from backend.design import materials
        return [{"key": m["key"], "name": m["name"], "density": m["density"]} for m in materials.MATERIALS]

    @exposed()
    def quote_measure_file(self, source):
        """Modèle déposé → volume, surface et taille (le fichier n'est pas gardé)."""
        if device.ANDROID:
            raise security.ValidationError("Le devis depuis un fichier 3D se fait sur le PC.")
        from backend.design import repair
        with self._source_file(source, (".stl", ".3mf", ".obj")) as (path, name):
            volume, area, size = estimate.mesh_measure(repair.load_raw(path))
        if volume < 1:
            raise security.ValidationError("Ce modèle n'a pas de volume mesurable (surface ouverte ?).")
        return {"measure": {"volume": volume, "area": area}, "size": size, "name": os.path.splitext(name)[0],
                "materials": self._estimate_materials()}

    @exposed()
    def design_measure(self, sid, part=None):
        """Résultat de Conception → volume, surface et taille (toutes les pièces, ou une seule)."""
        out = self._design().measure(sid, part)
        out["materials"] = self._estimate_materials()
        return out

    @exposed()
    def quote_estimate(self, measure, opts):
        measure = measure if isinstance(measure, dict) else {}
        volume = security.number(measure.get("volume"), 0, 1e9, 0)
        area = security.number(measure.get("area"), 0, 1e9, 0)
        return {"estimate": estimate.estimate(volume, area, opts, self._store.settings().get("print_log") or [])}

    @exposed()
    def quote_save(self, data):
        quote = self._quotes.save(data if isinstance(data, dict) else {}, self._store.settings().get("quote") or {})
        return {"quote": quote, "msg": f"Devis {quote.get('number')} enregistré."}

    @exposed()
    def quote_status(self, quote_id, status, choice=None):
        """Statut d'un devis ; pour un devis à options ou à grille, « accepté » retient le choix du client."""
        quote = self._quotes.set_status(quote_id, str(status), choice if isinstance(choice, dict) else None,
                                        self._store.settings().get("quote") or {})
        return {"quote": quote}

    @exposed()
    def quote_delete(self, quote_id):
        self._quotes.delete(quote_id)
        return {"msg": "Devis supprimé."}

    @exposed()
    def quote_pdf(self, quote_id, open_it=False):
        quote = self._quotes.get(quote_id)
        _, logo = self._quotes.seller()
        path = quote_pdf.save(quote, logo, QUOTES_DIR)
        if open_it is True:
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "PDF enregistré : " + path}

    # ========================================================= Factures (3.9)
    def _invoices_payload(self):
        self._require("invoices")
        seller, _ = self._quotes.seller()
        rows = self._invoices.list()
        return {"invoices": invoices.decorate(rows), "summary": invoices.summary(rows), "seller": seller,
                "missing": invoices.seller_problems(seller)}

    def _invoice_links(self):
        """Devis et commandes déjà facturés (vide sans l'option ou avant la migration 006)."""
        if not self._enterprise("invoices"):
            return {}
        try:
            return self._invoices.links()
        except (security.ValidationError, SupabaseError):
            return {}

    @exposed()
    def invoices_list(self):
        return self._invoices_payload()

    @exposed()
    def invoice_draft(self, source, source_id):
        """Brouillon de facture (rien n'est enregistré) depuis un devis accepté ou une commande."""
        self._require("invoices")
        if source == "quote":
            quote = self._quotes.get(source_id)
            if quote.get("status") != "accepted":
                raise security.ValidationError("Seul un devis accepté se facture : changez d'abord son statut.")
            done = self._invoices.for_quote(quote["id"])
            if done:
                raise security.ValidationError(f"Ce devis a déjà la facture {done[0].get('number')}.")
            return {"draft": invoices.draft_from_quote(quote)}
        if source == "order":
            self._require("orders")
            order = self._orders.get(source_id)
            done = self._invoices.for_order(order["id"]) or (order.get("quote_id") and self._invoices.for_quote(order["quote_id"]))
            if done:
                raise security.ValidationError(f"Cette commande a déjà la facture {done[0].get('number')}.")
            quote = None
            if order.get("quote_id"):
                try:
                    quote = self._quotes.get(order["quote_id"])
                except security.ValidationError:
                    quote = None
            return {"draft": invoices.draft_from_order(order, quote)}
        raise security.ValidationError("Source inconnue.")

    @exposed()
    def invoice_preview(self, data):
        """Totaux en direct pendant la saisie (aucun contrôle des mentions, rien n'est enregistré)."""
        data = data if isinstance(data, dict) else {}
        raw = data.get("lines") if isinstance(data.get("lines"), list) else []
        seller, _ = self._quotes.seller()
        vat_mode = "franchise" if data.get("vat_mode", seller.get("vat_mode")) == "franchise" else "rate"
        rate = data.get("vat_rate", seller.get("vat_rate"))
        lines, totals = invoices.compute([invoices.clean_line(x) for x in raw[:invoices.MAX_LINES]],
                                         data.get("discount_pct"), vat_mode, float(invoices.quotes.dec(rate, 0, 30, 20)))
        return {"lines": lines, "totals": totals, "vat_mode": vat_mode}

    @exposed()
    def invoice_issue(self, data):
        """Émet la facture : numéro et date donnés par la base, plus aucune modification ensuite."""
        self._require("invoices")
        seller, _ = self._quotes.seller()
        values = invoices.clean_invoice(data, seller)
        if values.get("order_id"):
            self._orders.get(values["order_id"])
            done = self._invoices.for_order(values["order_id"])
            if done:
                raise security.ValidationError(f"Cette commande a déjà la facture {done[0].get('number')}.")
        if values.get("quote_id"):
            self._quotes.get(values["quote_id"])
            done = self._invoices.for_quote(values["quote_id"])
            if done:
                raise security.ValidationError(f"Ce devis a déjà la facture {done[0].get('number')}.")
        row = self._invoices.issue(values, seller)
        return dict(self._invoices_payload(), invoice=row, msg=f"Facture {row.get('number')} émise.")

    @exposed()
    def invoice_credit(self, invoice_id, data):
        """Avoir (total ou partiel) sur une facture : au plus ce qui n'a pas encore été crédité."""
        self._require("invoices")
        seller, _ = self._quotes.seller()
        inv = self._invoices.get(invoice_id)
        before = invoices.credited(self._invoices.list(), inv["id"])
        row = self._invoices.issue(invoices.credit_from(inv, data, before), seller)
        return dict(self._invoices_payload(), invoice=row, msg=f"Avoir {row.get('number')} émis sur la facture {inv.get('number')}.")

    @exposed()
    def invoice_payment(self, invoice_id, data):
        self._require("invoices")
        inv = self._invoices.get(invoice_id)
        payment = invoices.clean_payment(data)
        self._invoices.set_payment(inv["id"], payment)
        word = "Avoir remboursé" if inv.get("kind") == "credit" else "Paiement enregistré"
        return dict(self._invoices_payload(), msg=word + "." if payment["paid_on"] else "Paiement retiré.")

    @exposed()
    def invoice_pdf(self, invoice_id, open_it=False):
        self._require("invoices")
        inv = self._invoices.get(invoice_id)
        _, logo = self._quotes.seller()
        path = invoice_pdf.save(inv, logo, INVOICES_DIR)
        if open_it is True:
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "PDF enregistré : " + path}

    # ================================================ Récapitulatif URSSAF (3.9)
    def _urssaf_rows(self):
        self._require("urssaf")
        inv = []
        if self._enterprise("invoices"):
            try:
                inv = self._invoices.list(limit=5000)
            except security.ValidationError:      # migration 006 pas encore faite
                inv = []
        ords = []
        if self._enterprise("orders"):
            try:
                uid = self._client.user_id
                ords = [o for o in self._orders.list(self._store.workshop_id()) if o.get("user_id") == uid]
            except (security.ValidationError, SupabaseError):
                ords = []
        return urssaf.receipts(inv, ords)

    def _urssaf_settings(self):
        return dict(urssaf.DEFAULTS, **(self._store.settings().get("urssaf") or {}))

    @exposed()
    def urssaf_summary(self):
        rows = self._urssaf_rows()
        out = urssaf.summary(rows, self._urssaf_settings())
        out["sources"] = {"invoices": self._enterprise("invoices"), "orders": self._enterprise("orders")}
        return out

    @exposed()
    def urssaf_detail(self, key):
        key = security.text(key, 10)
        return {"rows": urssaf.detail(self._urssaf_rows(), key, self._urssaf_settings()["period"])}

    @exposed()
    def urssaf_export(self):
        rows = self._urssaf_rows()
        os.makedirs(ACCOUNTING_DIR, exist_ok=True)
        path = os.path.join(ACCOUNTING_DIR, f"urssaf_{dt.date.today().isoformat()}.csv")
        accounting.write_csv(path, urssaf.CSV_HEADER, urssaf.csv_rows(rows, self._urssaf_settings()["period"]))
        return {"path": path, "folder": ACCOUNTING_DIR, "msg": "Récapitulatif enregistré : " + path}

    # ===================================================== Export comptable
    @exposed()
    def accounting_export(self):
        settings = self._store.settings()
        quote_list = None
        if (settings.get("features") or {}).get("quotes"):
            try:
                quote_list = self._quotes.list(limit=5000)
            except security.ValidationError:      # table des devis pas encore créée : export sans devis
                quote_list = None
        paths = accounting.export(ACCOUNTING_DIR, settings.get("print_log") or [], quote_list)
        return {"paths": paths, "folder": ACCOUNTING_DIR,
                "msg": f"{len(paths)} fichiers CSV enregistrés dans {ACCOUNTING_DIR}"}

    @exposed()
    def incidents(self):
        return {"incidents": self._store.incidents(days=60)}

    # =========================================================== Bobines
    @exposed()
    def spool_add(self, data):
        data = data if isinstance(data, dict) else {}
        qty = self._store.add_spools(data)
        if data.get("scope") == "workshop":
            return {"msg": f"{qty} bobine(s) ajoutée(s) au stock de l'atelier."}
        return {"msg": f"{qty} bobine{'s' if qty > 1 else ''} ajoutée{'s' if qty > 1 else ''}."}

    @exposed()
    def spool_update(self, spool_id, data):
        self._store.update_spool(spool_id, data if isinstance(data, dict) else {})

    @exposed()
    def spool_adjust(self, spool_id, delta):
        return self._store.adjust_spool(spool_id, delta)

    @exposed()
    def spool_delete(self, spool_id):
        self._store.delete_spool(spool_id)

    @exposed()
    def spool_dried(self, spool_id):
        self._store.mark_dried(spool_id)

    @exposed()
    def ams_import(self, selections):
        added = linked = 0
        for item in selections if isinstance(selections, list) else []:
            slot = security.integer(item.get("slot"), 1, 16, 0)
            action = str(item.get("action") or "IGNORE")
            if not slot or action == "IGNORE":
                continue
            if action == "NEW":
                added += self._store.add_spools(dict(item, ams_slot=slot))
            elif action.startswith("EXISTING:"):
                self._store.assign_slot(action.split(":", 1)[1], slot)
                linked += 1
        return {"msg": f"{added} bobine(s) ajoutée(s), {linked} bobine(s) placée(s) dans l'AMS."}

    @exposed()
    def purge_add(self, grams):
        return {"poop_chute": self._store.add_purge(grams)}

    @exposed()
    def purge_empty(self):
        return {"poop_chute": self._store.empty_purge()}

    @exposed()
    def nozzle_set(self, name):
        return {"settings": self._store.set_active_nozzle(name)}

    @exposed()
    def nozzle_reset(self, name):
        return {"settings": self._store.reset_nozzle(name)}

    @exposed()
    def nozzle_hours_set(self, name, hours):
        return {"settings": self._store.set_nozzle_hours(name, hours)}

    # =================================================== Listes et projets
    @exposed()
    def task_add(self, data):
        return {"task": self._store.add_task(data if isinstance(data, dict) else {})}

    @exposed()
    def task_move(self, task_id, status):
        before = self._store.move_task(task_id, status)
        emptied = False
        if status == "done" and before.get("status") != "done":
            grams = int(before.get("estimated_grams") or 0)
            self._store.record_print(True, grams)
            emptied = bool(self._store.consume_filament(grams).get("emptied"))
        return {"finished": status == "done" and before.get("status") != "done", "emptied": emptied}

    @exposed()
    def task_delete(self, task_id):
        self._store.delete_task(task_id)

    @exposed()
    def maint_add(self, title, interval_hours, shared=False):
        hours = self._store.settings()["stats"].get("total_hours_printed", 0)
        self._store.add_maintenance(title, interval_hours, hours, shared is True)

    @exposed()
    def maint_presets(self):
        """Rappels conseillés par famille d'imprimante, et la famille détectée."""
        family = maintenance_presets.detect_family(self._printer.public_config(), self._store.settings())
        return {"families": maintenance_presets.catalog(), "detected": family}

    @exposed()
    def maint_apply_presets(self, family):
        family = security.text(family, 20)
        hours = self._store.settings()["stats"].get("total_hours_printed", 0)
        rows = maintenance_presets.preset_rows(family, self._store.maintenance())
        for title, interval in rows:
            self._store.add_maintenance(title, interval, hours)
        label = maintenance_presets.FAMILIES[family][0]
        if not rows:
            return {"added": 0, "msg": f"Les rappels conseillés pour {label} sont déjà tous dans la liste."}
        return {"added": len(rows), "msg": f"{len(rows)} rappel(s) ajouté(s) pour {label}."}

    @exposed()
    def maint_done(self, row_id):
        hours = self._store.settings()["stats"].get("total_hours_printed", 0)
        self._store.maintenance_done(row_id, hours)

    @exposed()
    def maint_delete(self, row_id):
        self._store.delete_maintenance(row_id)

    # ================================================= Pièces détachées (3.8)
    @exposed()
    def parts_save(self, data, part_id=""):
        settings, part = self._store.save_part(data if isinstance(data, dict) else {}, part_id)
        return {"settings": settings, "msg": f"« {part['name']} » enregistrée."}

    @exposed()
    def parts_adjust(self, part_id, delta):
        settings, part = self._store.adjust_part(part_id, delta)
        return {"settings": settings, "part": part, "low": spare_parts.is_low(part)}

    @exposed()
    def parts_delete(self, part_id):
        return {"settings": self._store.delete_part(part_id)}

    @exposed()
    def parts_to_shopping(self, part_id):
        part = next((p for p in spare_parts.clean_parts(self._store.settings().get("parts"))
                     if p["id"] == security.text(part_id, 16)), None)
        if not part:
            raise security.ValidationError("Pièce introuvable.")
        name = spare_parts.shopping_name(part)
        wanted = {str(it.get("name") or "").strip().lower() for it in self._store.shopping() if not it.get("done")}
        if name.lower() in wanted:
            return {"msg": f"« {name} » est déjà dans la liste d'achats."}
        self._store.add_shopping(name, "Pièce", "Urgent" if not part["qty"] else "Normale")
        return {"msg": f"« {name} » ajoutée à la liste d'achats."}

    @exposed()
    def shop_add(self, name, category, priority, shared=False):
        self._store.add_shopping(name, category, priority, shared is True)

    @exposed()
    def shop_toggle(self, row_id):
        self._store.toggle_shopping(row_id)

    @exposed()
    def shop_delete(self, row_id):
        self._store.delete_shopping(row_id)

    @exposed()
    def project_add(self, data):
        self._store.add_project(data if isinstance(data, dict) else {})

    @exposed()
    def project_step(self, row_id, delta):
        self._store.step_project(row_id, delta)

    @exposed()
    def project_to_shopping(self, row_id):
        n = self._store.project_to_shopping(row_id)
        return {"msg": f"{n} article(s) ajouté(s) à la liste d'achats."}

    @exposed()
    def project_delete(self, row_id):
        self._store.delete_project(row_id)

    @exposed()
    def bookmark_add(self, data):
        self._store.add_bookmark(data if isinstance(data, dict) else {})

    @exposed()
    def bookmark_delete(self, row_id):
        self._store.delete_bookmark(row_id)

    # ============================================================ Textes juridiques (5.5)
    @exposed(require_auth=False)
    def legal_index(self):
        return {"docs": self._legal.index(), "current": self._legal.current_version()}

    @exposed(require_auth=False)
    def legal_text(self, key):
        return {"doc": self._legal.get(security.text(key, 20))}

    @exposed()
    def legal_status(self):
        return {"legal": legal.status(self._store.settings(), self._legal.current_version())}

    @exposed()
    def legal_accept(self, version):
        current = self._legal.current_version()
        if security.text(version, 120) != current:
            raise security.ValidationError("Les textes viennent de changer : relisez la nouvelle version avant d'accepter.")
        settings = self._store.update_settings("legal", lambda _old: legal.acceptance(current))
        return {"legal": legal.status(settings, current), "msg": "Merci, c'est noté."}

    @exposed(require_auth=False)
    def open_url(self, url):
        url = security.text(url, 500)
        if not url.startswith(("https://", "http://")):
            raise security.ValidationError("Lien refusé.")
        webbrowser.open(url)

    # ============================================================ Social
    @exposed()
    def social(self):
        jobs = {"overview": self._pool.submit(self._social.overview),
                "feed": self._pool.submit(self._social.feed),
                "borrows": self._pool.submit(self._social.borrow_requests)}
        out = {name: job.result(timeout=30) for name, job in jobs.items()}
        out["unread"] = self._unread_messages()
        return out

    # ---- Messagerie entre amis (3.9)
    def _unread_messages(self):
        """Messages non lus par ami ; None tant que la migration 006 n'est pas faite (pas d'erreur au fil)."""
        try:
            return self._messages.unread()
        except (security.ValidationError, SupabaseError) as e:
            # Le bouton « Écrire » reste visible : la conversation affiche alors la vraie cause.
            log.warning("Messagerie indisponible : %s", e)
            return None

    @exposed()
    def messages_thread(self, friend_id):
        """Conversation avec un ami ; les messages reçus passent en « lu »."""
        rows = self._messages.thread(friend_id)
        if any(not r["mine"] and not r["read"] for r in rows):
            self._messages.mark_read(friend_id)
        return {"messages": rows, "unread": self._unread_messages()}

    @exposed()
    def message_send(self, friend_id, body):
        self._messages.send(friend_id, body)
        return {"messages": self._messages.thread(friend_id)}

    @exposed()
    def message_delete(self, message_id, friend_id):
        self._messages.delete(message_id)
        return {"messages": self._messages.thread(friend_id), "msg": "Message retiré."}

    @exposed()
    def friend_request(self, code):
        res = self._social.send_request(code)
        if res.get("status") == "accepted":
            return {"msg": f"Vous êtes maintenant ami avec {res.get('pseudo', '')} !", "accepted": True}
        return {"msg": f"Demande envoyée à {res.get('pseudo', '')}."}

    @exposed()
    def friend_respond(self, request_id, accept):
        self._social.respond(request_id, accept)
        return {"accepted": bool(accept)}

    @exposed()
    def friend_cancel(self, request_id):
        self._social.cancel(request_id)

    @exposed()
    def friend_remove(self, friend_id):
        self._social.remove(friend_id)

    @exposed()
    def post_publish(self, data):
        self._social.publish(data if isinstance(data, dict) else {})

    @exposed()
    def post_delete(self, post_id):
        self._social.delete_post(post_id)

    @exposed()
    def post_image(self, post_id):
        return {"src": self._social.post_image(post_id)}

    # ---------------------------------------------------- Fiches recettes (3.7)
    @exposed()
    def recipe_publish(self, data):
        self._social.publish_recipe(data if isinstance(data, dict) else {})
        return {"msg": "Fiche recette publiée dans le fil."}

    @exposed()
    def recipe_code(self, data):
        return {"code": recipe_code(data if isinstance(data, dict) else {})}

    @exposed()
    def recipe_save(self, post_id):
        recipe, author = self._social.recipe_of(post_id)
        settings = self._store.save_recipe(recipe, author, security.record_id(post_id))
        return {"settings": settings, "msg": "Fiche enregistrée dans vos Favoris."}

    @exposed()
    def recipe_import(self, code):
        settings = self._store.save_recipe(parse_recipe_code(code))
        return {"settings": settings, "msg": "Fiche enregistrée dans vos Favoris."}

    @exposed()
    def recipe_delete(self, recipe_id):
        return {"settings": self._store.delete_recipe(recipe_id)}

    @exposed()
    def post_like(self, post_id):
        return {"liked": self._social.toggle_like(post_id)}

    @exposed()
    def post_preset_save(self, post_id):
        preset = self._social.preset(post_id)
        if not preset or not preset.get("preset_json"):
            raise security.ValidationError("Cette publication n'a pas de réglages joints.")
        name = security.filename(preset.get("preset_name") or "reglages.json", (".json",))
        os.makedirs(config.EXPORT_DIR, exist_ok=True)
        target = os.path.join(config.EXPORT_DIR, name)
        stem, ext = os.path.splitext(target)
        n = 1
        while os.path.exists(target):
            target = f"{stem} ({n}){ext}"
            n += 1
        with open(target, "w", encoding="utf-8") as f:
            f.write(preset["preset_json"])
        return {"msg": "Réglages enregistrés : " + target}

    @exposed()
    def borrow_request(self, spool_id, grams, message):
        self._social.request_borrow(spool_id, grams, message)
        return {"msg": "Demande de prêt envoyée."}

    @exposed()
    def borrow_respond(self, request_id, accept):
        self._social.respond_borrow(request_id, accept)

    @exposed()
    def borrow_cancel(self, request_id):
        self._social.cancel_borrow(request_id)

    # ======================================================== Imprimante
    @exposed()
    def printer_config(self):
        return {"printer": self._printer.public_config()}

    @exposed()
    def printer_connect(self, cfg):
        ok, msg = self._printer.save_and_connect(cfg if isinstance(cfg, dict) else {})
        return {"ok": ok, "msg": msg, "printer": self._printer.public_config()}

    @exposed()
    def printer_select(self, printer_id):
        """Mode plusieurs imprimantes : l'imprimante sur laquelle agissent commandes, fichiers et caméra."""
        self._printer.select(printer_id)
        return {"printer": self._printer.public_config(), "telemetry": self._printer.telemetry(),
                "fleet": self._printer.fleet()}

    @exposed()
    def printer_remove(self, printer_id):
        self._printer.remove_printer(printer_id)
        return {"msg": "Imprimante retirée.", "printer": self._printer.public_config(),
                "telemetry": self._printer.telemetry(), "fleet": self._printer.fleet()}

    @exposed()
    def printer_disconnect(self):
        self._printer.disconnect(keep_config=True)
        return {"msg": "Imprimante déconnectée."}

    @exposed()
    def printer_reset_pin(self):
        ok, msg = self._printer.reset_pins()
        return {"ok": ok, "msg": msg}

    @exposed()
    def cloud_login(self, email, password, code, remember):
        return self._printer.cloud_login(email, password, code, remember)

    @exposed()
    def cloud_use_lan(self, serial, host, name=""):
        host = security.host(host) if host else ""
        res = self._printer.cloud_use_lan(serial, host, security.text(name, 60))
        res["printer"] = self._printer.public_config()
        return res

    @exposed()
    def printer_discover(self, kind="bambu"):
        return {"printers": self._printer.discover("creality" if kind == "creality" else "bambu")}

    @exposed()
    def cloud_send_code(self, email):
        self._printer.cloud_send_code(email)
        return {"msg": "Code envoyé par Bambu Lab à cette adresse."}

    @exposed()
    def gcode_send(self, text):
        sent = self._printer.gcode(text)
        return {"ok": bool(sent), "msg": "Commande envoyée." if sent else "Imprimante hors ligne."}

    @exposed()
    def jog(self, axis, distance):
        axis = str(axis).upper()
        if axis == "HOME":
            return self.gcode_send("G28")
        if axis not in ("X", "Y", "Z", "E"):
            raise security.ValidationError("Axe inconnu.")
        dist = security.number(distance, -100, 100, 0, 2)
        speed = 300 if axis == "E" else 3000
        return self.gcode_send(f"M211 S1\nG91\nG0 {axis}{dist} F{speed}\nG90")

    @exposed()
    def temperature_set(self, target, value):
        if target == "nozzle":
            return self.gcode_send(f"M104 S{security.integer(value, 0, 300, 0)}")
        return self.gcode_send(f"M140 S{security.integer(value, 0, 110, 0)}")

    @exposed()
    def job_control(self, action):
        res = self._printer.job_control(action)
        labels = {"pause": "Pause envoyée.", "resume": "Reprise envoyée.", "stop": "Arrêt envoyé."}
        return {"ok": res["ok"], "msg": labels[action] if res["ok"] else "Imprimante hors ligne."}

    @exposed()
    def fan_set(self, which, pct):
        sent = self._printer.set_fan(str(which), pct)
        return {"ok": bool(sent), "msg": "" if sent else "Imprimante hors ligne."}

    @exposed()
    def speed_set(self, level):
        sent = self._printer.set_speed(level)
        return {"ok": bool(sent), "msg": "" if sent else "Imprimante hors ligne."}

    @exposed()
    def ams_refresh(self):
        """3.8 : bouton « Rafraîchir » de l'AMS (rapport complet redemandé à l'imprimante)."""
        if not self._printer.refresh():
            raise security.ValidationError("Imprimante hors ligne.")
        return {"msg": "Demande envoyée : l'AMS se met à jour dans quelques secondes."}

    @exposed()
    def light_toggle(self):
        sent, on = self._printer.light()
        return {"ok": sent, "on": on}

    @exposed()
    def melody_play(self, text):
        return self.gcode_send(text)

    @exposed()
    def camera_start(self):
        self._printer.camera_start()

    @exposed()
    def camera_stop(self):
        self._printer.camera_stop()

    @exposed()
    def camera_frame(self):
        return self._printer.camera_frame()

    @exposed()
    def camera_snapshot(self):
        return {"msg": "Cliché enregistré : " + self._printer.save_snapshot()}

    @exposed()
    def ai_analyze_now(self):
        return {"result": self._printer.ai_analyze_now()}

    @exposed()
    def ai_analyze_image(self, data_url):
        raw = security.b64_payload(security.image_data_url(data_url, 3_000_000, allow_empty=False), 3_000_000)
        if raw[:2] != b"\xff\xd8":
            raise security.ValidationError("Envoyez une photo au format JPEG.")
        return {"result": self._printer.ai_analyze_image(raw)}

    @exposed()
    def ai_status(self):
        return {"engine": self._ai.status()["engine"], "muted": self._printer.vision_guard().muted()}

    @exposed()
    def ai_label(self, frame_id, verdict, defect_type=""):
        engine = self._ai.label(security.text(frame_id, 32), str(verdict), security.text(defect_type, 32))
        if engine.get("stored_only"):
            return {"engine": engine, "msg": "Photo rangée dans « Photos marquées » : elle servira à entraîner un modèle."}
        thanks = "Merci, c'est noté." if engine["learned_active"] else (
            f"Noté ({engine['learned_ok']} « OK », {engine['learned_defect']} « problème » ; "
            f"l'apprentissage démarre à {engine['min_per_class']} de chaque).")
        return {"engine": engine, "msg": thanks}

    @exposed()
    def ai_mute(self, defect_type):
        guard = self._printer.vision_guard()
        label = guard.mute(security.text(defect_type, 32))
        return {"muted": guard.muted(), "msg": f"« {label} » ne sera plus signalé pendant cette impression."}

    @exposed()
    def printer_objects(self):
        """Pièces du plateau en cours (« Retirer une pièce ») : [{"id", "name", "skipped", "current"}]."""
        return {"objects": self._printer.skip_objects().objects()}

    @exposed()
    def printer_skip_object(self, obj_id):
        skipper = self._printer.skip_objects()
        obj = skipper.skip(security.text(obj_id, 120))
        return {"msg": f"« {obj['name']} » ne sera plus imprimée.", "objects": skipper.objects()}

    @exposed()
    def ai_forget(self):
        return {"engine": self._ai.forget(), "msg": "Apprentissage effacé."}

    @exposed()
    def ai_model_install(self):
        picked = self._pick_file(("Modèle ONNX (*.onnx)",))
        if not picked:
            return {"cancelled": True}
        engine = self._ai.install_model(self._picked_path(picked["token"]))
        return {"engine": engine, "msg": "Modèle installé : " + engine["model"]}

    @exposed()
    def ai_model_remove(self):
        return {"engine": self._ai.remove_model(), "msg": "Modèle retiré."}

    @exposed()
    def ai_add_photos(self, label):
        """Range des photos (internet, téléphone…) dans le dossier d'entraînement d'une classe.
        Elles servent seulement à entraîner un modèle sur Colab, pas à l'apprentissage sur ce PC."""
        label = str(label or "")
        if label != "ok" and label not in DEFECT_LABELS:
            raise security.ValidationError("Classe inconnue.")
        if device.ANDROID:
            raise security.ValidationError("Les photos d'entraînement s'ajoutent depuis le PC.")
        if not self._window:
            raise security.ValidationError("Fenêtre indisponible.")
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        paths = self._window.create_file_dialog(dialog, allow_multiple=True,
                                                file_types=("Images (*.jpg;*.jpeg;*.png;*.webp)",)) or []
        folder = os.path.join(self._ai.status()["engine"]["frames_dir"], label)
        added, skipped = import_training_photos(list(paths)[:500], folder)
        msg = f"{added} photo(s) ajoutée(s) à « {label} »." + (f" {skipped} ignorée(s) (pas une image)." if skipped else "")
        return {"added": added, "skipped": skipped, "msg": msg}

    @exposed()
    def ai_open_frames(self):
        path = self._ai.status()["engine"]["frames_dir"]
        os.makedirs(path, exist_ok=True)
        _open_local(path)
        return {"ok": True}

    # =========================================================== Carte SD
    @exposed()
    def sd_list(self, directory):
        return {"directory": security.sd_path(directory), "items": self._printer.sd_list(directory)}

    @exposed()
    def sd_download(self, remote_path):
        return {"msg": "Fichier enregistré : " + self._printer.sd_download(remote_path)}

    @exposed()
    def sd_upload(self, filename, data, start_print, slot):
        raw = security.b64_payload(data, config.MAX_FILE_BYTES)
        return self._sd_upload(filename, raw, start_print, slot)

    @exposed()
    def sd_upload_picked(self, token, start_print, slot):
        """Envoie sur la carte SD un fichier déjà repéré sur le PC (ex. téléchargé depuis MakerWorld)."""
        path = self._picked_path(token)
        if os.path.getsize(path) > config.MAX_FILE_BYTES:
            raise security.ValidationError("Fichier trop volumineux.")
        with open(path, "rb") as f:
            raw = f.read()
        return self._sd_upload(os.path.basename(path), raw, start_print, slot)

    def _sd_upload(self, filename, raw, start_print, slot):
        name = security.filename(filename, self._printer.upload_types())
        try:
            analysis = analyze_bytes(name, raw) if name.lower().endswith((".3mf", ".gcode")) else {}
        except security.ValidationError:
            raise
        except Exception:
            analysis = {}
        if name.lower().endswith(".3mf") and analysis and not analysis.get("sliced"):
            raise security.ValidationError(
                "Ce .3mf est un projet non découpé : l'imprimante ne sait pas le lire. Dans Bambu Studio, "
                "découpez puis utilisez « Exporter le fichier découpé » (.gcode.3mf), ou passez par Slicer Express.")
        name = self._printer.sd_upload(name, raw)
        if analysis:
            self._printer.remember_file(name, analysis)
        if start_print:
            sent = self._printer.start_print(name, slot)
            return {"msg": f"« {name} » envoyé et impression lancée." if sent else
                    f"« {name} » envoyé, mais l'ordre d'impression n'est pas passé."}
        return {"msg": f"« {name} » envoyé sur l'imprimante."}

    @exposed()
    def sd_print(self, path, slot):
        sent = self._printer.start_print(path, slot)
        return {"ok": sent, "msg": "Impression lancée." if sent else "Imprimante hors ligne."}

    @exposed()
    def schedule_add(self, path, slot, mode, at_local):
        item = self._scheduler.add(path, slot, str(mode), at_local or "")
        when = dt.datetime.fromtimestamp(item["start_at"])
        return {"item": item, "scheduled_prints": self._scheduler.items(),
                "msg": f"« {item['name']} » sera lancé le {when:%d/%m à %H:%M}. Laissez l'application ouverte."}

    @exposed()
    def schedule_cancel(self, item_id):
        self._scheduler.cancel(item_id)
        return {"scheduled_prints": self._scheduler.items(), "msg": "Programmation annulée."}

    @exposed()
    def sd_delete(self, remote_path, is_dir):
        path = self._printer.sd_delete(remote_path, bool(is_dir))
        return {"msg": f"« {path.rsplit('/', 1)[-1]} » supprimé de l'imprimante."}

    # ========================================================= Timelapses
    @exposed()
    def timelapse_list(self):
        return {"items": self._printer.timelapses()}

    @exposed()
    def timelapse_thumb(self, remote_path):
        return {"src": self._printer.timelapse_thumb(remote_path)}

    @exposed()
    def timelapse_download(self, remote_path, play):
        path = self._printer.timelapse_download(remote_path)
        if play and path.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "Vidéo enregistrée : " + path}

    # ---- Bandeau sur un timelapse (3.7) : la vidéo est refabriquée dans l'interface (canvas + MediaRecorder)
    @staticmethod
    def _timelapse_local(name):
        name = security.filename(name, TIMELAPSE_VIDEO_EXT)
        path = os.path.join(TIMELAPSE_DIR, name)
        if not os.path.isfile(path):
            raise security.ValidationError("Vidéo introuvable sur ce PC : téléchargez-la d'abord.")
        return path

    @exposed()
    def timelapse_chunk(self, name, offset):
        """Un morceau (3 Mo) d'une vidéo déjà téléchargée, pour la relire dans l'interface."""
        path = self._timelapse_local(name)
        size = os.path.getsize(path)
        if size > TIMELAPSE_MAX_BYTES:
            raise security.ValidationError("Vidéo trop lourde pour ajouter un bandeau (400 Mo au plus).")
        offset = security.integer(offset, 0, size, 0)
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(TIMELAPSE_CHUNK)
        return {"data": base64.b64encode(raw).decode("ascii"), "size": size, "next": offset + len(raw),
                "done": offset + len(raw) >= size, "type": "video/webm" if path.lower().endswith(".webm") else "video/mp4"}

    @exposed()
    def timelapse_banner_begin(self, name):
        stem = os.path.splitext(security.filename(name or "timelapse.webm"))[0][:80] or "timelapse"
        os.makedirs(TIMELAPSE_DIR, exist_ok=True)
        target = os.path.join(TIMELAPSE_DIR, f"{stem} - bandeau.webm")
        n = 1
        while os.path.exists(target):
            target = os.path.join(TIMELAPSE_DIR, f"{stem} - bandeau ({n}).webm")
            n += 1
        token = pysecrets.token_hex(8)
        with self._lock:
            self._tl_sweep()
            if len(self._tl_out) >= TIMELAPSE_MAX_JOBS:
                raise security.ValidationError("Deux vidéos avec bandeau sont déjà en cours d'enregistrement : "
                                               "attendez qu'elles soient terminées.")
            with open(target + ".part", "wb"):
                pass
            self._tl_out[token] = {"path": target, "size": 0, "last": time.monotonic()}
        return {"token": token}

    def _tl_sweep(self, now=None):
        """Appelé sous self._lock : oublie les enregistrements sans nouvelles depuis 30 min et efface les .part
        abandonnés (tous ceux d'une session précédente au premier passage, puis ceux de plus de 24 h)."""
        now = time.monotonic() if now is None else now
        for token, job in list(self._tl_out.items()):
            if now - job.get("last", now) > TIMELAPSE_IDLE_S:
                self._tl_out.pop(token, None)
                try:
                    os.remove(job["path"] + ".part")
                except OSError:
                    pass
        busy = {os.path.normcase(job["path"] + ".part") for job in self._tl_out.values()}
        first, self._tl_swept = not getattr(self, "_tl_swept", False), True
        try:
            entries = list(os.scandir(TIMELAPSE_DIR))
        except OSError:
            return
        for e in entries:
            if not e.name.endswith(".part") or os.path.normcase(e.path) in busy:
                continue
            try:
                if e.is_file(follow_symlinks=False) and (first or time.time() - e.stat().st_mtime > TIMELAPSE_PART_MAX_AGE_S):
                    os.remove(e.path)
            except OSError:
                pass

    def _tl_job(self, token):
        with self._lock:
            job = self._tl_out.get(str(token))
        if not job:
            raise security.ValidationError("Enregistrement interrompu : recommencez.")
        return job

    @exposed()
    def timelapse_banner_part(self, token, data):
        job = self._tl_job(token)
        if not isinstance(data, str) or len(data) > (TIMELAPSE_CHUNK * 4) // 3 + 16:
            raise security.ValidationError("Morceau de vidéo invalide.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise security.ValidationError("Morceau de vidéo invalide.")
        if job["size"] + len(raw) > TIMELAPSE_MAX_BYTES:
            raise security.ValidationError("Vidéo trop lourde.")
        with open(job["path"] + ".part", "ab") as f:
            f.write(raw)
        job["size"] += len(raw)
        job["last"] = time.monotonic()

    @exposed()
    def timelapse_banner_end(self, token, keep):
        job = self._tl_job(token)
        with self._lock:
            self._tl_out.pop(str(token), None)
        part = job["path"] + ".part"
        if keep is not True or job["size"] == 0:
            if os.path.exists(part):
                os.remove(part)
            return {"path": ""}
        os.replace(part, job["path"])
        return {"path": job["path"], "msg": "Vidéo avec bandeau enregistrée : " + job["path"]}

    @exposed()
    def timelapse_delete(self, remote_path, thumb):
        path = self._printer.timelapse_delete(remote_path, thumb or "")
        return {"msg": f"« {path.rsplit('/', 1)[-1]} » supprimé de l'imprimante."}

    @exposed()
    def open_folder(self, which):
        folders = {"timelapses": os.path.join(config.EXPORT_DIR, "timelapses"), "exports": config.EXPORT_DIR,
                   "backups": os.path.join(config.EXPORT_DIR, "Sauvegardes"),
                   "auto_backups": backup_service.auto_folder(config.DATA_DIR),
                   "devis": QUOTES_DIR, "factures": INVOICES_DIR, "comptabilite": ACCOUNTING_DIR,
                   "conception": os.path.join(config.EXPORT_DIR, "Conception"),
                   "calibration": os.path.join(config.EXPORT_DIR, "Calibration"),
                   "downloads": os.path.join(os.path.expanduser("~"), "Downloads")}
        path = folders.get(str(which))
        if not path:
            raise security.ValidationError("Dossier inconnu.")
        os.makedirs(path, exist_ok=True)
        if device.ANDROID:      # pas de dossier sur le téléphone : son dernier fichier (à ouvrir ou partager)
            latest = device.latest_file(path)
            if not latest:
                raise security.ValidationError("Aucun fichier pour l'instant.")
            _open_local(latest)
            return {"msg": f"Dernier fichier : {os.path.basename(latest)}"}
        _open_local(path)

    # ======================================================= Mises à jour
    @staticmethod
    def _update_public(info):
        return {"available": True, "version": info["version"], "notes": info["notes"], "date": info["date"],
                "size": info["size"]}

    @exposed(require_auth=False)
    def update_status(self):
        u = self._updater
        return {"enabled": u.enabled, "auto": updater.auto_enabled(), "version": config.APP_VERSION,
                "update": self._update_public(u.info) if u.info else None, "progress": dict(u.progress),
                "support": bool(support.support_email()), "support_email": support.support_email()}

    @exposed(require_auth=False)
    def update_set_auto(self, on):
        updater.set_auto(on is True)
        return {"auto": on is True}

    @exposed(require_auth=False)
    def update_check(self, manual):
        """manual=False : vérification automatique au démarrage (au plus une fois par 24 h, silencieuse)."""
        manual, u = manual is True, self._updater
        if not u.enabled:
            if manual:
                raise security.ValidationError("Les mises à jour ne sont pas configurées dans cette copie de Graou3D.")
            return {"available": False}
        if not manual:
            if not updater.auto_enabled():
                return {"available": False}
            if not updater.due():
                cached = updater.read_cache()
                if cached:
                    try:
                        return self._update_public(u.from_cache(*cached))
                    except updater.UpdateError:
                        pass
                return {"available": False}
        try:
            info, raw, sig = u.fetch()
        except updater.UpToDate:
            updater.mark_checked()
            return {"available": False, "msg": f"Graou3D {config.APP_VERSION} est à jour."}
        except updater.UpdateError as e:
            log.warning("Mise à jour refusée : %s", e)
            updater.mark_checked()
            if manual:
                raise security.ValidationError(str(e))
            return {"available": False}
        except (OSError, http.client.HTTPException, ValueError) as e:
            log.info("Recherche de mise à jour impossible : %s", e)
            if manual:
                raise security.ValidationError("Impossible de joindre GitHub : vérifiez la connexion Internet, puis réessayez.")
            return {"available": False}
        updater.mark_checked(raw, sig)
        return self._update_public(info)

    @exposed(require_auth=False)
    def update_download(self):
        try:
            self._updater.download()
        except updater.UpdateError as e:
            raise security.ValidationError(str(e))
        except (OSError, http.client.HTTPException, ValueError) as e:
            log.warning("Téléchargement de la mise à jour impossible : %s", e)
            raise security.ValidationError("Téléchargement impossible : vérifiez la connexion Internet, puis réessayez.")
        return {"ready": True, "version": self._updater.info["version"]}

    @exposed(require_auth=False)
    def update_install(self):
        """Après confirmation dans l'interface : lance l'assistant d'installation (visible) et ferme Graou3D."""
        try:
            path = self._updater.ready_file()
        except updater.UpdateError as e:
            raise security.ValidationError(str(e))
        if device.ANDROID or not hasattr(os, "startfile"):
            raise security.ValidationError("L'installation automatique n'est possible que sous Windows.")
        os.startfile(path)  # noqa: S606 - installateur téléchargé, taille et sha256 signés vérifiés juste avant
        if self._window is not None and not self._closing:
            self._closing = True
            threading.Thread(target=self._close_after_outro, daemon=True).start()
        return {"msg": "Installation lancée : Graou3D va se fermer."}

    # ================================================= Signaler un problème
    @exposed(require_auth=False)
    def support_report(self):
        """Prépare le journal nettoyé à joindre. Rien n'est envoyé : l'utilisateur choisit d'écrire l'email."""
        if not support.support_email():
            raise security.ValidationError("Aucune adresse de support n'est configurée dans cette copie de Graou3D.")
        printer = self._printer.public_config() if self._client.user_id else {}
        self._support_file = support.write_report(local_prefs.get("lang", "fr"), printer, extra=self._support_secrets(printer))
        return {"file": self._support_file, "name": os.path.basename(self._support_file)}

    def _support_secrets(self, printer):
        """Valeurs propres à ce compte à masquer dans le rapport (canaux ntfy, adresse et série de l'imprimante)."""
        values = [printer.get("host"), printer.get("serial")]
        if self._client.user_id:
            try:
                s = self._store.settings()
                values += [(s.get("alerts") or {}).get("push_topic"), (s.get("remote") or {}).get("cmd_topic")]
            except Exception:  # noqa: BLE001 - le rapport doit rester possible hors ligne
                pass
        return [v for v in values if v]

    @exposed(require_auth=False)
    def support_mail(self, kind="problem"):
        """Ouvre la messagerie avec un email prérempli. kind : problem (rapport à joindre), question ou idea.
        5.4 : renvoie aussi l'adresse, l'objet et le texte, pour les copier quand la messagerie ne s'ouvre pas
        (Gmail ou Outlook dans le navigateur)."""
        kind = kind if kind in support.KINDS else "problem"
        address = support.support_email()
        if not address:
            raise security.ValidationError("Aucune adresse de support n'est configurée dans cette copie de Graou3D.")
        if kind == "problem" and not self._support_file:
            raise security.ValidationError("Préparez d'abord le rapport.")
        printer = self._printer.public_config() if self._client.user_id else {}
        lang = local_prefs.get("lang", "fr")
        name = os.path.basename(self._support_file) if kind == "problem" else ""
        if device.ANDROID:      # messagerie du téléphone, rapport déjà joint
            subject, body = support.mail_parts(lang, printer, name, attached=bool(name), kind=kind)
            out = {"email": address, "subject": subject, "body": body}
            sent = (device.send_email(address, subject, body, self._support_file) if name
                    else device.open_url(support.mailto(address, lang, printer, "", kind=kind)))
            if sent:
                return dict(out, opened=True)
            return dict(out, opened=False, msg=f"Aucune messagerie n'est installée sur ce téléphone : écrivez à {address}.")
        subject, body = support.mail_parts(lang, printer, name, kind=kind)
        out = {"email": address, "subject": subject, "body": body}
        url = support.mailto(address, lang, printer, name, kind=kind)
        try:
            if hasattr(os, "startfile"):
                os.startfile(url)  # noqa: S606 - lien mailto: construit ici
            elif not webbrowser.open(url):
                raise OSError("aucune messagerie")
        except OSError:
            return dict(out, opened=False,
                        msg=f"Aucune messagerie n'est configurée sur ce PC : copiez le message et écrivez à {address}.")
        return dict(out, opened=True)

    @exposed(require_auth=False)
    def support_open_folder(self):
        if device.ANDROID and self._support_file:
            _open_local(self._support_file)
            return
        folder = os.path.dirname(self._support_file) if self._support_file else config.EXPORT_DIR
        os.makedirs(folder, exist_ok=True)
        _open_local(folder)

    # ============================================ Réglages filament partagés
    def _nozzle(self):
        return float(self._store.settings()["slicer"].get("nozzle", "0.4"))

    @exposed()
    def filament_share(self, spool_id):
        consensus = self._store.share_filament(spool_id, self._nozzle())
        return {"consensus": consensus, "msg": "Merci ! Vos réglages sont partagés anonymement."}

    @exposed()
    def filament_consensus(self, brand, material):
        return {"consensus": self._store.filament_consensus(brand, material, self._nozzle())}

    # ================================================ Étiquettes QR / NFC
    @exposed()
    def labels_export_page(self):
        """Prépare la page mobile des étiquettes (dossier à mettre en ligne, ex. Netlify Drop ou GitHub Pages).
        Elle contient l'adresse du projet Supabase et sa clé PUBLIQUE ; jamais de mot de passe."""
        if not config.SUPABASE_CONFIGURED:
            raise security.ValidationError("Supabase n'est pas configuré dans le fichier .env.")
        folder = os.path.join(config.EXPORT_DIR, "page-mobile")
        os.makedirs(os.path.join(folder, "bobine"), exist_ok=True)
        for name, text in _label_page_files(config.SUPABASE_URL, config.SUPABASE_ANON_KEY).items():
            with open(os.path.join(folder, "bobine", name), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
            f.write('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=bobine/">Graou3D')
        _open_local(folder)
        return {"folder": folder, "msg": "Page mobile prête : " + folder}

    # ========================================================= MakerWorld
    @exposed()
    def makerworld_open(self, url, in_app):
        """Ouvre MakerWorld (page d'accueil, recherche ou modèle) dans une fenêtre de l'application ou
        dans le navigateur. La fenêtre n'a PAS accès à l'API de l'application (pas de js_api)."""
        url = security.https_url(url or config.MAKERWORLD_URL, allow_empty=False)
        if not _makerworld_host(url, login=False):
            raise security.ValidationError("Seuls les liens makerworld.com sont ouverts ici.")
        if in_app and not device.ANDROID:     # Android : toujours dans le navigateur du téléphone
            import webview
            win = webview.create_window("MakerWorld · Graou3D", url, width=1280, height=860,
                                        background_color="#101214", text_select=True)
            state = {"last": url}

            def guard():
                # La fenêtre n'a pas de barre d'adresse : on y reste sur MakerWorld et les pages de
                # connexion (Bambu, Google, Apple, Facebook). Tout autre lien part dans le navigateur.
                try:
                    current = win.get_current_url() or ""
                except Exception:
                    return
                if not current.startswith("https://") or _makerworld_host(current, login=True):
                    if current.startswith("https://"):
                        state["last"] = current
                    return
                webbrowser.open(current)
                win.load_url(state["last"])
            win.events.loaded += guard
        else:
            webbrowser.open(url)
        return {}

    @exposed()
    def makerworld_downloads(self):
        """Modèles 3D téléchargés ces 30 derniers jours (dossier Téléchargements)."""
        folder = os.path.join(os.path.expanduser("~"), "Downloads")
        limit = dt.datetime.now().timestamp() - 30 * 86400
        items = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            entries = []
        for e in entries:
            if not e.is_file() or not e.name.lower().endswith((".3mf", ".stl", ".obj", ".step", ".stp")):
                continue
            st = e.stat()
            if st.st_mtime < limit or st.st_size > config.MAX_FILE_BYTES:
                continue
            items.append((st.st_mtime, e.path, e.name, st.st_size))
        items.sort(reverse=True)
        out = []
        with self._lock:
            for old in self._mw_tokens:
                self._picked.pop(old, None)
            self._mw_tokens = []
        for mtime, path, name, size in items[:40]:
            token = pysecrets.token_hex(8)
            with self._lock:
                self._picked[token] = path
                self._mw_tokens.append(token)
            out.append({"token": token, "name": name, "size": size, "mtime": int(mtime),
                        "sliced": name.lower().endswith(".gcode.3mf")})
        return {"items": out, "folder": folder, "keep_login": local_prefs.get("mw_keep_login", "1") != "0"}

    @exposed()
    def makerworld_keep_login(self, keep):
        """3.8 : garder la connexion MakerWorld d'un lancement à l'autre (pris en compte au prochain démarrage)."""
        local_prefs.put("mw_keep_login", "1" if keep is True else "0")
        return {"keep_login": keep is True,
                "msg": "Connexion gardée à partir du prochain démarrage." if keep is True
                else "Plus rien n'est gardé à partir du prochain démarrage."}

    @exposed()
    def makerworld_logout(self):
        local_prefs.put("mw_forget", "1")
        return {"msg": "Vous serez déconnecté de MakerWorld au prochain démarrage de Graou3D."}

    @exposed()
    def makerworld_thumb(self, token):
        """Miniature d'un fichier listé par makerworld_downloads (image du .3mf ou rendu 3D)."""
        with self._lock:
            known = str(token) in self._mw_tokens
        if not known:
            raise security.ValidationError("Fichier inconnu, actualisez la liste.")
        path = self._picked_path(token)
        return {"thumb": thumbnail(path, os.path.join(config.DATA_DIR, "thumbs"))}

    # ========================================== Analyse et Slicer Express
    @exposed()
    def file_analyze(self, filename, data):
        name = security.filename(filename, (".3mf", ".gcode"))
        raw = security.b64_payload(data, config.MAX_FILE_BYTES)
        analysis = analyze_bytes(name, raw)
        analysis["stock"] = stock_check(analysis["grams"], self._store.spools(), material=analysis.get("material", ""))
        self._printer.remember_file(name, analysis)
        return {"analysis": analysis}

    @exposed()
    def file_need(self, filename, data):
        """Grammes nécessaires d'un fichier découpé (vérification « assez de filament ? »).
        Ne garde rien et n'envoie rien à l'imprimante."""
        name = security.filename(filename, (".3mf", ".gcode"))
        raw = security.b64_payload(data, config.MAX_FILE_BYTES)
        try:
            a = analyze_bytes(name, raw)
        except Exception:              # fichier illisible : poids inconnu, on ne bloque rien
            return {"grams": 0, "filaments": []}
        return {"grams": a.get("grams") or 0, "filaments": a.get("filaments") or [], "material": a.get("material", "")}

    @exposed()
    def file_need_known(self, path):
        """Grammes d'un fichier de l'imprimante déjà analysé pendant cette session (0 si inconnu)."""
        name = security.sd_path(path).rsplit("/", 1)[-1]
        return {"grams": self._printer.known_file(name).get("grams") or 0}

    @staticmethod
    def _slicer_path(engine, s):
        """Chemin choisi sur CE PC (local_prefs) ; l'ancien réglage du compte n'est repris qu'une fois, s'il
        désigne bien l'exe attendu sur ce PC : le compte en ligne ne peut pas faire lancer un autre programme."""
        legacy = s.get("orca_path" if engine == "orca" else "bambu_studio_path", "")
        return local_prefs.slicer_path(engine, legacy, lambda p: find_slicer(engine, p) == p)

    def _slicer_exe(self, s):
        engine = "orca" if s.get("engine") == "orca" else "bambu"
        return engine, find_slicer(engine, self._slicer_path(engine, s))

    @exposed()
    def slicer_info(self):
        s = self._store.settings()["slicer"]
        engine, exe = self._slicer_exe(s)
        if engine == "orca":
            presets = orca_presets(exe, s.get("machine", ""))
        else:
            presets = available_presets(exe, s.get("printer_model", "A1"), s.get("nozzle", "0.4"))
        return {"engine": engine, "engine_label": ENGINES[engine]["label"], "exe": exe,
                "installed": {k: bool(find_slicer(k, self._slicer_path(k, s))) for k in ENGINES},
                "paths": {k: self._slicer_path(k, s) for k in ENGINES},
                "printer_model": s.get("printer_model"), "nozzle": s.get("nozzle"), **presets}

    @exposed()
    def slicer_pick_file(self):
        picked = self._pick_file(("Modèles 3D (*.stl;*.3mf;*.obj;*.step)",))
        return {"file": picked} if picked else {"ok": False, "msg": ""}

    @exposed()
    def slicer_pick_exe(self, engine="bambu"):
        if device.ANDROID:
            raise security.ValidationError(ANDROID_NO_SLICER)
        engine = "orca" if engine == "orca" else "bambu"
        spec = ENGINES[engine]
        picked = self._pick_file((f"{spec['label']} (*.exe)",))     # 5.1.3 : filtre valide pour pywebview
        if not picked:
            return {"ok": False, "msg": ""}
        path = self._picked_path(picked["token"])
        if os.path.basename(path).lower() != spec["exe"]:
            raise security.ValidationError(f"Choisissez le fichier {spec['exe']}.")
        key = "orca_path" if engine == "orca" else "bambu_studio_path"
        local_prefs.put(local_prefs.SLICER_KEYS[engine], path)   # préférence de ce PC
        # L'ancien emplacement dans le compte est vidé : il n'est plus jamais lu.
        return {"settings": self._store.patch_from_ui({"slicer": {key: "", "engine": engine}}), "path": path}

    # ---------------------------------------------- 3.8.2 : couleurs et coloriage
    def _slot_info(self, slot):
        """Matière et couleur du slot `slot` : bobine du stock à cet emplacement, sinon ce que voit l'AMS."""
        spool = self._store.spool_for_slot(slot) if slot else None
        tray = next((t for t in self._printer.telemetry().get("ams_trays", []) if t.get("slot") == slot), None)
        return {"slot": slot, "material": (spool or {}).get("material") or (tray or {}).get("type") or "",
                "color_hex": (spool or {}).get("color_hex") or (tray or {}).get("color_hex") or ""}

    def _ams_slots(self):
        slots = {int(t.get("slot")): t for t in self._printer.telemetry().get("ams_trays", []) if t.get("slot")}
        for s in self._store.own_spools():
            if int(s.get("ams_slot") or 0) > 0:
                slots.setdefault(int(s["ams_slot"]), {})
        return [self._slot_info(n) for n in sorted(slots)]

    @contextlib.contextmanager
    def _source_file(self, source, allowed):
        """Chemin lisible d'un modèle choisi (jeton) ou déposé (octets, écrits dans un dossier temporaire)."""
        source = source if isinstance(source, dict) else {}
        if source.get("token"):
            path = self._picked_path(source["token"])
            security.filename(os.path.basename(path), allowed)
            yield path, os.path.basename(path)
            return
        name = security.filename(source.get("name"), allowed)
        raw = security.b64_payload(source.get("data"), config.MAX_FILE_BYTES)
        with tempfile.TemporaryDirectory(prefix="graou3d_couleurs_") as tmp:
            path = os.path.join(tmp, "modele" + os.path.splitext(name)[1].lower())
            with open(path, "wb") as f:
                f.write(raw)
            yield path, name

    @exposed()
    def slicer_inspect(self, source):
        """Couleurs d'un modèle avant la découpe, et slot de l'AMS proposé pour chacune."""
        if device.ANDROID:
            raise security.ValidationError(ANDROID_NO_SLICER)
        with self._source_file(source, (".stl", ".3mf", ".obj", ".step", ".stp")) as (path, _name):
            info = multicolor.inspect(path)
        slots = self._ams_slots()
        if info.pop("numbered", False):         # couleurs inconnues : couleur N → slot N (s'il existe)
            have = {x["slot"] for x in slots}
            info["slots"] = [i + 1 if i + 1 in have else 0 for i in range(len(info["filaments"]))]
        else:
            info["slots"] = multicolor.assign_slots(info["filaments"], slots) if info["filaments"] else []
        return info

    @exposed()
    def paint_open(self, source):
        """Maillage d'un modèle à colorier : sommets et triangles (le même ordre servira à la découpe)."""
        if device.ANDROID:
            raise security.ValidationError(ANDROID_NO_SLICER)
        if multicolor.np is None:
            raise security.ValidationError("Coloriage indisponible sur cet appareil.")
        with self._source_file(source, (".stl", ".3mf", ".obj")) as (path, name):
            verts, faces = multicolor.load_mesh(path)
        pid = pysecrets.token_hex(6)
        with self._lock:
            sessions = getattr(self, "_paint", {})
            sessions = {k: v for k, v in sessions.items() if time.time() - v["at"] < 6 * 3600}
            while len(sessions) >= 3:            # quelques modèles en mémoire au plus
                sessions.pop(min(sessions, key=lambda k: sessions[k]["at"]))
            sessions[pid] = {"verts": verts, "faces": faces, "name": name, "at": time.time()}
            self._paint = sessions
        lo, hi = verts.min(axis=0), verts.max(axis=0)
        return {"paint_id": pid, "triangles": int(len(faces)), "vertices": int(len(verts)),
                "min": [round(float(v), 3) for v in lo], "size": [round(float(v), 3) for v in hi - lo],
                "positions": base64.b64encode(verts.astype("<f4").tobytes()).decode("ascii"),
                "indices": base64.b64encode(faces.astype("<u4").tobytes()).decode("ascii")}

    def _painted_3mf(self, colors, filaments):
        """.3mf colorié (octets) d'après la peinture et les bandes envoyées par l'interface."""
        with self._lock:
            session = (getattr(self, "_paint", {}) or {}).get(security.text(colors.get("paint_id"), 16))
        if not session:
            raise security.ValidationError("Le coloriage n'est plus en mémoire : rouvrez « Colorier ».")
        faces = session["faces"]
        states = multicolor.decode_states(colors.get("paint") or "", len(faces), filaments).copy()
        bands = multicolor.clean_bands(colors.get("bands"), filaments)
        verts, faces, states = multicolor.apply_bands(session["verts"], faces, states, bands)
        return multicolor.write_3mf(verts, faces, states, os.path.splitext(session["name"])[0]), session["name"]

    @exposed()
    def paint_save(self, colors):
        """Enregistre le modèle colorié en .3mf (pour l'ouvrir dans Bambu Studio et vérifier)."""
        colors = colors if isinstance(colors, dict) else {}
        n = max(1, len(colors.get("slots") or []))
        raw, name = self._painted_3mf(colors, n)
        os.makedirs(config.EXPORT_DIR, exist_ok=True)
        target = os.path.join(config.EXPORT_DIR, security.filename(os.path.splitext(name)[0] + "_couleurs.3mf"))
        with open(target, "wb") as f:
            f.write(raw)
        return {"msg": "Modèle colorié enregistré : " + target}

    @exposed()
    def slicer_start(self, source, quality, slot, material="", colors=None):
        if device.ANDROID:
            raise security.ValidationError(ANDROID_NO_SLICER)
        source = source if isinstance(source, dict) else {}
        s = self._store.settings()["slicer"]
        slot = security.integer(slot, 0, 16, 0)
        spool = self._store.spool_for_slot(slot) if slot else None
        tray = next((t for t in self._printer.telemetry().get("ams_trays", []) if t.get("slot") == slot), None)
        material = (spool or {}).get("material") or (tray or {}).get("type") or security.text(material, 40) or "PLA Basic"
        engine, exe = self._slicer_exe(s)
        kwargs = dict(exe_path=exe, engine=engine, machine=s.get("machine", ""),
                      printer_model=s.get("printer_model", "A1"), nozzle=s.get("nozzle", "0.4"),
                      quality=security.text(quality, 80), slot=slot, material=material,
                      spools=self._store.spools())
        allowed = (".stl", ".3mf", ".obj", ".step", ".stp")
        colors = colors if isinstance(colors, dict) else {}
        mapping = [security.integer(x, 0, 16, 0) for x in (colors.get("slots") or [])[:multicolor.MAX_FILAMENTS]]
        first = next((x for x in mapping if x), 0)
        # Couleur sans slot : celui de la 1re couleur (garder l'ordre : couleur N du fichier = mapping[N-1]).
        mapping = [x or first for x in mapping] if first else []
        if mapping:
            # Une matière par couleur, d'après la bobine du slot choisi ; le 1er slot sert de slot principal.
            kwargs.update(mapping=mapping, slot=mapping[0],
                          materials=[self._slot_info(n)["material"] or material for n in mapping])
        if colors.get("paint_id"):
            raw, name = self._painted_3mf(colors, max(1, len(mapping)))
            kwargs.update(source_name=security.filename(os.path.splitext(name)[0] + ".3mf", allowed),
                          source_bytes=raw, orient=False)
        elif source.get("token"):
            path = self._picked_path(source["token"])
            kwargs.update(source_name=security.filename(os.path.basename(path), allowed), source_path=path)
        else:
            kwargs.update(source_name=security.filename(source.get("name"), allowed),
                          source_bytes=security.b64_payload(source.get("data"), config.MAX_FILE_BYTES))
        job = self._slicer.start(**kwargs)
        return {"job": job.public()}

    @exposed()
    def slicer_job(self, job_id):
        job = self._slicer.job(str(job_id))
        if not job:
            raise security.ValidationError("Tâche de découpe introuvable.")
        return {"job": job.public()}

    @exposed()
    def slicer_send(self, job_id, start_print, force):
        job = self._slicer.job(str(job_id))
        if not job or job.status != "ready":
            raise security.ValidationError("La découpe n'est pas prête.")
        if start_print and job.stock and not job.stock.get("ok") and not force:
            raise security.ValidationError("Stock insuffisant pour ce slot : confirmez pour lancer quand même.")
        name, raw = self._slicer.read_output(job, gcode=not self._printer.capabilities()["bambu"])
        job.status, job.message = "sending", "Envoi à l'imprimante…"
        try:
            name = self._printer.sd_upload(name, raw)
            analysis = dict(job.analysis or {})
            if job.mapping:
                # Couleur réelle de chaque bobine (fin d'impression : grammes retirés de la bonne bobine).
                fils = []
                for i, f in enumerate(analysis.get("filaments") or []):
                    fid = int(f.get("id") or i + 1)
                    slot = job.mapping[fid - 1] if 0 < fid <= len(job.mapping) else 0
                    fils.append(dict(f, color=self._slot_info(slot)["color_hex"] or f.get("color")) if slot else f)
                analysis["filaments"] = fils
            self._printer.remember_file(name, analysis)
            if start_print:
                sent = (self._printer.start_print(name, job.slot, mapping=job.mapping) if len(job.mapping) > 1
                        else self._printer.start_print(name, job.slot))
                job.status = "printing" if sent else "done"
                job.message = "Impression lancée." if sent else "Fichier envoyé, mais l'ordre d'impression n'est pas passé."
            else:
                job.status, job.message = "done", "Fichier envoyé à l'imprimante."
        except Exception:
            job.status, job.message = "ready", "Envoi impossible."
            raise
        finally:
            if job.status != "ready":
                job.cleanup()
        return {"job": job.public(), "msg": job.message}

    @exposed()
    def slicer_bambu_connect(self, job_id):
        """3.8.4 : passe le fichier découpé à Bambu Connect, qui signe l'ordre d'impression (le firmware refuse
        ceux de Graou3D) ; Bambu Handy reste actif. Copie gardée dans Téléchargements › Graou3D › bambu-connect."""
        job = self._slicer.job(str(job_id))
        if not job or job.status != "ready":
            raise security.ValidationError("La découpe n'est pas prête.")
        if device.ANDROID:
            raise security.ValidationError("Bambu Connect s'utilise sur le PC.")
        folder = os.path.join(config.EXPORT_DIR, "bambu-connect")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(job.output_path))
        if not target.lower().endswith(".gcode.3mf"):
            raise security.ValidationError("Bambu Connect attend un fichier découpé pour Bambu Lab (.gcode.3mf).")
        shutil.copyfile(job.output_path, target)
        name = os.path.splitext(os.path.basename(str(job.source or "")))[0] or "Graou3D"
        bambu_connect.send(target, name)
        return {"msg": "Bambu Connect s'ouvre : choisissez l'imprimante et les bobines, puis lancez l'impression."}

    @exposed()
    def slicer_save(self, job_id):
        job = self._slicer.job(str(job_id))
        if not job or job.status != "ready":
            raise security.ValidationError("La découpe n'est pas prête.")
        os.makedirs(config.EXPORT_DIR, exist_ok=True)
        target = os.path.join(config.EXPORT_DIR, os.path.basename(job.output_path))
        shutil.copyfile(job.output_path, target)
        return {"msg": "Fichier enregistré : " + target}

    # ============================================== 4.0 : Conception (PC)
    def _design(self):
        if device.ANDROID:
            raise security.ValidationError("Les outils de conception sont sur le PC.")
        from backend.design import geom
        geom.require()
        with self._lock:
            if getattr(self, "_design_svc", None) is None:
                from backend.design.service import DesignService
                self._design_svc = DesignService(config.EXPORT_DIR)
            return self._design_svc

    def _design_printer(self):
        """Imprimante pour le jeu calibré et le plateau : celle connectée, sinon le modèle choisi pour le slicer."""
        from backend.design import service
        s = self._store.settings()
        model = (s.get("slicer") or {}).get("printer_model") or "A1"
        cfg = self._printer.public_config() or {}
        key = cfg.get("serial") or cfg.get("name") or model
        caps = service.printer_caps(model)
        return {"key": key, "label": cfg.get("name") or f"Bambu Lab {model}", "model": model, "bed": list(caps["bed"]),
                "enclosure": caps["enclosure"], "max_nozzle": caps["nozzle"], "max_bed": caps["bed_t"],
                "hardened": "Trempé" in str(s.get("active_nozzle") or "")}

    def _design_fit(self, material=""):
        from backend.design import fit
        p = self._design_printer()
        return fit.for_printer(self._store.settings().get("fit") or [], p["key"], material or "PLA")

    @exposed()
    def design_info(self):
        """Ce qu'il faut à l'écran Conception : imprimante, jeux calibrés, polices, listes de choix."""
        if device.ANDROID:
            return {"available": False, "msg": "Les outils de conception sont sur le PC."}
        from backend.design import cutter, fem, fit, geom, materials, parametric, relief, text2d, texture
        from backend.design.fea import bench, calculators, ccx
        if geom.mf is None:
            return {"available": False, "msg": geom.UNAVAILABLE}
        printer = self._design_printer()
        profiles = fit.clean_profiles(self._store.settings().get("fit") or [])
        return {"available": True, "printer": printer, "fit": self._design_fit(), "profiles": profiles,
                "clearances": [int(round(c * 100)) for c in fit.CLEARANCES], "fonts": text2d.fonts(),
                "joints": list(cutter.JOINTS), "magnets": [list(m) for m in cutter.MAGNETS], "patterns": list(texture.PATTERNS),
                "relief_modes": list(relief.MODES), "directions": [{"key": k, "label": v} for k, v in fem.DIRECTIONS.items()],
                "qualities": list(fem.QUALITY), "infill_patterns": [{"key": k, "label": v[0]} for k, v in fem.PATTERNS.items()],
                "needs": [{"key": k, "label": v} for k, v in materials.NEEDS],
                "material_keys": [{"key": m["key"], "name": m["name"], "category": m["category"]} for m in materials.MATERIALS],
                "parametric": parametric.catalog(), "openscad": bool(parametric.find_openscad()), "openscad_url": parametric.OPENSCAD_URL,
                "calculators": calculators.catalog(), "precision": bench.published(), "ccx": bool(ccx.find_ccx())}

    @exposed()
    def design_pick_file(self):
        if device.ANDROID:
            raise security.ValidationError("Les outils de conception sont sur le PC.")
        picked = self._pick_file(("Modèles 3D (*.stl;*.3mf;*.obj)",))
        return {"file": picked} if picked else {"ok": False, "msg": ""}

    @exposed()
    def design_open(self, source):
        svc = self._design()
        with self._source_file(source, (".stl", ".3mf", ".obj")) as (path, name):
            return svc.open(path, name)

    @exposed()
    def design_view(self, sid):
        return self._design().view(sid)

    @exposed()
    def design_extract(self, sid, part):
        return self._design().extract(sid, part)

    @exposed()
    def design_job(self, job_id):
        return {"job": self._design().job(job_id)}

    @exposed()
    def design_auto_cuts(self, sid, part, joint):
        return self._design().auto_cuts(sid, part, joint, tuple(self._design_printer()["bed"]))

    @exposed()
    def design_cut(self, sid, part, params):
        params = params if isinstance(params, dict) else {}
        svc = self._design()
        fit = self._design_fit(security.text(params.get("material"), 40))
        return {"job": svc.cut(sid, part, params, fit, tuple(self._design_printer()["bed"]))}

    @exposed()
    def design_study(self, sid, part, params):
        from backend.design import materials
        params = params if isinstance(params, dict) else {}
        mat = materials.get(params.get("material")) or materials.get("pla")
        return {"job": self._design().study(sid, part, params, mat)}

    @exposed()
    def design_optimize(self, sid, part, params):
        from backend.design import materials
        params = params if isinstance(params, dict) else {}
        mat = materials.get(params.get("material")) or materials.get("pla")
        return {"job": self._design().optimize(sid, part, params, mat)}

    @exposed()
    def design_ccx(self, sid, part, axis="", run_it=True):
        """5.8 : contre-vérification CalculiX de la dernière étude (export .inp, calcul si ccx est installé)."""
        from backend.design.fea import ccx
        exe = ccx.find_ccx() if run_it is True else ""
        return {"job": self._design().ccx_check(sid, part, security.text(axis, 2), exe)}

    @exposed()
    def design_study_report(self, sid, part, project="", open_it=False):
        path = self._design().study_report(sid, part, security.text(project, 120))
        if open_it is True:
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "Note de calcul enregistrée : " + path}

    @exposed()
    def design_open_folder(self, path):
        """Ouvre le dossier d'un fichier exporté par Conception (jamais un chemin hors du dossier d'export)."""
        folder = os.path.dirname(os.path.abspath(str(path or "")))
        root = os.path.abspath(self._design().export_dir)
        if not (folder + os.sep).startswith(root + os.sep) or not os.path.isdir(folder):
            raise security.ValidationError("Dossier introuvable.")
        _open_local(folder)
        return {}

    @exposed()
    def design_calc(self, key, params):
        """5.8 : calculs rapides de pièces imprimées (clip, poutre, équerre, vis, charnière, pression, engrenage)."""
        from backend.design.fea import calculators
        return calculators.compute(security.text(key, 20), params if isinstance(params, dict) else {})

    @exposed()
    def design_logo(self, data):
        raw = security.b64_payload(data, 8 * 1024 * 1024)
        return self._design().logo(raw)

    @exposed()
    def design_relief(self, sid, part, items):
        return {"job": self._design().relief(sid, part, items if isinstance(items, list) else [])}

    @exposed()
    def design_texture(self, sid, part, params):
        return {"job": self._design().texture(sid, part, params)}

    @exposed()
    def design_organizer_analyze(self, data, threshold=0):
        from backend.design import service
        raw = security.b64_payload(data, service.MAX_PHOTO_BYTES)
        return {"job": self._design().organizer_analyze(raw, threshold)}

    # 5.8.3 : pièce cassée recréée à partir de photos posées sur la feuille de repère
    @exposed()
    def design_photo_sheet(self, dark=False, open_it=True, fmt="A4"):
        out = self._design().photo_sheet(dark is True, fmt if fmt in ("A4", "A3") else "A4")
        if open_it is True:
            _open_local(out["path"])
        return out

    @exposed()
    def design_photo_analyze(self, data, params=None):
        from backend.design import service
        raw = security.b64_payload(data, service.MAX_PHOTO_BYTES)
        return {"job": self._design().photo_analyze(raw, params)}

    @exposed()
    def design_photo_measure(self, spec, material=""):
        return {"measure": self._design().photo_measure(spec, self._design_fit(security.text(material, 40)))}

    @exposed()
    def design_photo_build(self, spec, choices=None, material="", gauge=False):
        fit = self._design_fit(security.text(material, 40))
        return {"job": self._design().photo_build(spec, choices, fit, gauge is True)}

    @exposed()
    def design_photo_scad(self, sid, open_it=False):
        out = self._design().photo_scad(sid, config.APP_VERSION)
        if open_it is True:
            _open_local(out["path"])
        return out

    # ----------------------------------------------------------- scan 3D par photos (5.8.5)
    def _scans(self):
        """Scans 3D : sur le PC, dossiers + reconstruction ; sur le téléphone, prise de vue et envoi au PC."""
        with self._lock:
            if getattr(self, "_scan_svc", None) is None:
                from backend.design.scan_service import ScanService
                store = None
                if not device.ANDROID:
                    def store(name, man, info):
                        from backend.design import geom, service
                        d = self._design()
                        sid = d._store(name, [service._part(name, geom.on_plate(man))], "scan", {"info": info})
                        return d.view(sid)
                self._scan_svc = ScanService(config.EXPORT_DIR, store, None if device.ANDROID else os.path.join(config.DATA_DIR, "scan-ia"))
            return self._scan_svc

    def _scans_pc(self):
        if device.ANDROID:
            raise security.ValidationError("La reconstruction se fait sur le PC : envoyez-lui les photos.")
        return self._scans()

    @exposed()
    def scan_info(self):
        if device.ANDROID:
            return {"available": False, "android": True}
        from backend.design import photo_scan
        return {"available": photo_scan.available(), "missing": "" if photo_scan.available() else photo_scan.UNAVAILABLE,
                "qualities": {k: v["label"] for k, v in photo_scan.QUALITY.items()}, "min_photos": photo_scan.MIN_PHOTOS,
                "android": device.ANDROID, "ai": self._scans().ai.status() if self._scans().ai else None}

    @exposed()
    def scan_list(self):
        return {"scans": self._scans_pc().list()}

    @exposed()
    def scan_new(self, title=""):
        return {"scan": self._scans_pc().create(title)}

    @exposed()
    def scan_status(self, scan_id):
        return {"scan": self._scans_pc().status(scan_id)}

    @exposed()
    def scan_thumbs(self, scan_id):
        return {"thumbs": self._scans_pc().thumbs(scan_id)}

    @exposed()
    def scan_rename(self, scan_id, title):
        return {"scan": self._scans_pc().rename(scan_id, title)}

    @exposed()
    def scan_delete(self, scan_id):
        return self._scans_pc().delete(scan_id)

    @exposed()
    def scan_add_files(self, scan_id):
        """Photos déjà prises (appareil photo, téléphone vidé sur le PC…) choisies dans une fenêtre."""
        svc = self._scans_pc()
        svc.status(scan_id)
        if not self._window:
            raise security.ValidationError("Fenêtre indisponible.")
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        paths = self._window.create_file_dialog(dialog, allow_multiple=True,
                                                file_types=("Photos (*.jpg;*.jpeg;*.png)",)) or []
        return svc.add_files(scan_id, list(paths))

    @exposed()
    def scan_add_photo(self, scan_id, data):
        from backend.design import scan_link
        return {"count": self._scans_pc().add_photo(scan_id, security.b64_payload(data, scan_link.MAX_PHOTO))}

    @exposed()
    def scan_remove_photo(self, scan_id, name):
        return self._scans_pc().remove_photo(scan_id, name)

    @exposed()
    def scan_receive(self, scan_id):
        return {"scan": self._scans_pc().receive(scan_id)}

    @exposed()
    def scan_receive_stop(self):
        return self._scans_pc().receive_stop()

    @exposed()
    def scan_start(self, scan_id, quality="normal"):
        return {"scan": self._scans_pc().start(scan_id, security.text(quality, 10))}

    @exposed()
    def scan_cancel(self, scan_id):
        return {"scan": self._scans_pc().cancel(scan_id)}

    @exposed()
    def scan_open(self, scan_id, opts=None):
        return {"view": self._scans_pc().open_result(scan_id, opts)}

    @exposed()
    def scan_export_stl(self, scan_id, opts=None, open_it=True):
        """5.8.9 : enregistre le .stl du scan (mêmes réglages que l'ouverture) et ouvre son dossier."""
        out = self._scans_pc().export_stl(scan_id, opts)
        if security.boolean(open_it):
            _open_local(out["folder"])
        x, y, z = out["size_mm"]
        return dict(out, msg=f"{out['name']} enregistré ({x:g} × {y:g} × {z:g} mm) dans {out['folder']}")

    @exposed()
    def scan_other_face(self, scan_id):
        return {"scan": self._scans_pc().other_face(scan_id)}

    @exposed()
    def scan_merge(self, scan_id, other_id):
        return {"scan": self._scans_pc().merge(scan_id, other_id)}

    # 5.8.8 : IA de profondeur (Depth Anything V2 petite taille, téléchargement facultatif)
    def _scan_ai(self):
        ai = self._scans_pc().ai
        if ai is None:
            raise security.ValidationError("IA de profondeur indisponible sur cet appareil.")
        return ai

    @exposed()
    def scan_ai_status(self):
        return {"ai": self._scan_ai().status()}

    @exposed()
    def scan_ai_install(self):
        self._scan_ai().install()
        return {"ai": self._scan_ai().status()}

    @exposed()
    def scan_ai_cancel(self):
        self._scan_ai().cancel_install()
        return {"ai": self._scan_ai().status()}

    @exposed()
    def scan_ai_enable(self, on=True):
        self._scan_ai().set_enabled(bool(on))
        return {"ai": self._scan_ai().status()}

    @exposed()
    def scan_ai_remove(self):
        self._scan_ai().remove()
        return {"ai": self._scan_ai().status(), "msg": "IA de profondeur effacée de ce PC."}

    @exposed()
    def scan_open_folder(self, scan_id):
        path = self._scans_pc().status(scan_id)["folder"]
        _open_local(path)
        return {"ok": True, "path": path}

    @exposed()
    def scan_phone_find(self):
        return self._scans().phone_find()

    @exposed()
    def scan_phone_pair(self, host, port, code):
        return {"pc": self._scans().phone_pair(host, port, code)}

    @exposed()
    def scan_phone_send(self, data):
        from backend.design import scan_link
        return self._scans().phone_send(security.b64_payload(data, scan_link.MAX_PHOTO))

    @exposed()
    def scan_phone_done(self):
        return self._scans().phone_done()

    @exposed()
    def scan_phone_status(self):
        return {"pc": self._scans().phone_status()}

    @exposed()
    def design_organizer_build(self, organizer_id, params):
        return {"job": self._design().organizer_build(organizer_id, params)}

    @exposed()
    def design_generate(self, kind, params, photo=None):
        from backend.design import service
        params = params if isinstance(params, dict) else {}
        raw = security.b64_payload(photo, service.MAX_PHOTO_BYTES) if photo else None
        fit = self._design_fit(security.text(params.get("material"), 40))
        return {"job": self._design().generate(security.text(kind, 20), params, fit, raw)}

    # ------------------------------------------------ 4.3 : modèles paramétriques et OpenSCAD
    # ------------------------------------------------ 5.8 : pièces envoyées par GraouConception
    @exposed()
    def conception_inbox(self):
        """Pièces déposées par GraouConception dans la boîte d'arrivée, pas encore prises."""
        return {"items": [] if device.ANDROID else conception_inbox.pending()}

    @exposed()
    def conception_take(self, item_id):
        """Prend une pièce reçue : jeton de fichier, comme design_pick_file / slicer_pick_file."""
        if device.ANDROID:
            raise security.ValidationError("GraouConception est sur le PC.")
        try:
            got = conception_inbox.take(security.text(item_id, 120))
        except ValueError as e:
            raise security.ValidationError(str(e))
        picked = self._remember_pick(got["path"])
        nom = re.sub(r'[\\/:*?"<>|]+', "-", str(got["nom"] or "").strip())[:80]
        if nom:
            picked["name"] = nom + ".3mf"                          # le nom de la pièce plutôt que « piece.3mf »
        return {"file": picked, "nom": got["nom"], "action": got["action"]}

    # ------------------------------------------------ 5.8 : GraouWeld (soudage)
    def _weld(self):
        if device.ANDROID:
            raise security.ValidationError("GraouWeld est sur le PC.")
        if not feature_enabled(self._store.settings(), "weld"):
            raise security.ValidationError("Activez d'abord « GraouWeld » dans Réglages › Fonctions.")

    @exposed()
    def weld_info(self):
        """Formulaires des calculateurs et des outils imprimables, normes citées."""
        self._weld()
        from backend.weld import catalog, data, tools
        return {"calculators": catalog.catalog(), "tools": tools.catalog(), "sources": [list(s) for s in data.SOURCES],
                "heat_note": tools.HEAT_NOTE}

    @exposed()
    def weld_compute(self, key, params):
        self._weld()
        from backend.weld import catalog
        return catalog.compute(security.text(key, 20), params if isinstance(params, dict) else {})

    @exposed()
    def weld_report(self, key, params, project="", open_it=False):
        """Note de calcul PDF dans Téléchargements › Graou3D › soudage."""
        self._weld()
        from backend.weld import report
        path = report.save(security.text(key, 20), params if isinstance(params, dict) else {}, WELD_DIR,
                           security.text(project, 120))
        if open_it is True:
            _open_local(path)
        return {"path": path, "name": os.path.basename(path), "msg": "Note de calcul enregistrée : " + path}

    @exposed()
    def weld_coping_pdf(self, params, open_it=False):
        """Gabarit papier de gueule de loup (échelle 1)."""
        self._weld()
        from backend.weld import tools
        v = tools.clean("weld_coping", params if isinstance(params, dict) else {})
        raw, info = tools.coping_pdf(v["branch"], v["main"], v["angle"], v["offset"], v["wall"], v["contact"])
        os.makedirs(WELD_DIR, exist_ok=True)
        name = f"gueule-de-loup-{v['branch']:g}-sur-{v['main']:g}-{v['angle']:g}deg.pdf"
        path = os.path.join(WELD_DIR, name)
        with open(path, "wb") as f:
            f.write(raw)
        if open_it is True:
            _open_local(path)
        return dict(info, path=path, name=name, msg="Gabarit enregistré : " + path)

    @exposed()
    def weld_tool(self, kind, params):
        """Outil d'atelier imprimable : construit comme un modèle paramétrique, affiché dans Conception."""
        self._weld()
        kind = security.text(kind, 20)
        if not kind.startswith("weld_"):
            raise security.ValidationError("Outil inconnu.")
        return self.design_parametric(kind, params)

    @exposed()
    def design_parametric(self, kind, params):
        params = params if isinstance(params, dict) else {}
        fit = self._design_fit(security.text(params.get("material"), 40))
        return {"job": self._design().parametric(kind, params, fit)}

    @exposed()
    def design_scad_open(self, source):
        from backend.design import parametric
        svc = self._design()
        source = source if isinstance(source, dict) else {}
        if source.get("token"):
            path = self._picked_path(source["token"])
            security.filename(os.path.basename(path), (".scad",))
            if os.path.getsize(path) > parametric.SCAD_MAX_BYTES:
                raise security.ValidationError("Fichier .scad trop gros (1 Mo au plus).")
            with open(path, "rb") as f:
                raw, name = f.read(), os.path.basename(path)
        else:
            name = security.filename(source.get("name"), (".scad",))
            raw = security.b64_payload(source.get("data"), parametric.SCAD_MAX_BYTES)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", "replace")
        out = svc.scad_open(text, name)
        out["openscad"] = bool(parametric.find_openscad())
        return out

    @exposed()
    def design_scad_build(self, scad_id, values):
        from backend.design import parametric
        return {"job": self._design().scad_build(scad_id, values, parametric.find_openscad())}

    # ------------------------------------------------ 4.1 : peinture au filament
    def _paint_spools(self):
        from backend.design import filament_paint
        out = []
        for sp in self._store.own_spools():
            if float(sp.get("weight_remaining") or 0) <= 20 or not sp.get("color_hex"):
                continue
            name = " ".join(x for x in (sp.get("material"), sp.get("color_name")) if x)
            out.append({"id": str(sp.get("id")), "name": name, "color": sp.get("color_hex"), "material": sp.get("material") or "",
                        "slot": int(sp.get("ams_slot") or 0), "grams": round(float(sp.get("weight_remaining") or 0)),
                        "td": filament_paint.estimate_td(sp.get("color_hex"), sp.get("material"))})
        return out

    @exposed()
    def design_paint_spools(self):
        """Bobines utilisables pour une peinture au filament, avec leur TD estimée."""
        self._design()
        return {"spools": self._paint_spools()}

    @exposed()
    def design_paint(self, photo, choices, params):
        """choices = [{"id": bobine} ou {"color", "name"}, "td"?] : les couleurs viennent du stock, pas de l'interface."""
        from backend.design import service
        raw = security.b64_payload(photo, service.MAX_PHOTO_BYTES) if photo else None
        mine = {s["id"]: s for s in self._paint_spools()}
        filaments = []
        for c in (choices if isinstance(choices, list) else [])[:16]:
            if not isinstance(c, dict):
                continue
            sp = mine.get(str(c.get("id") or ""))
            f = dict(sp) if sp else {"name": security.text(c.get("name"), 60), "color": security.hex_color(c.get("color")), "material": ""}
            if c.get("td") not in (None, ""):
                f["td"] = security.number(c.get("td"), 0.1, 20, f.get("td") or 1.0)
            filaments.append(f)
        return {"job": self._design().paint(raw, filaments, params)}

    @exposed()
    def design_paint_update(self, sid, swaps, tds=None):
        return {"job": self._design().paint_update(sid, swaps, tds)}

    @exposed()
    def design_coupon(self):
        return {"job": self._design().coupon()}

    @exposed()
    def design_fit_record(self, tight_index, slide_index, material):
        """Résultat du test de jeu imprimé : le trou « serré » et le trou « glissant »."""
        from backend import filament_catalog
        from backend.design import fit
        p = self._design_printer()
        family = filament_catalog.family_of(security.text(material, 40)) or "PLA"
        self._store.update_settings("fit", lambda v: fit.record(v or [], p["key"], p["label"], family, tight_index, slide_index))
        out = fit.for_printer(self._store.settings().get("fit") or [], p["key"], family)
        return {"fit": out, "profiles": fit.clean_profiles(self._store.settings().get("fit") or []),
                "msg": f"Jeu enregistré pour {p['label']} ({family}) : serré {out['press']:.2f} mm, glissant {out['slide']:.2f} mm."}

    @exposed()
    def design_fit_forget(self, printer, family):
        from backend.design import fit
        key, fam = security.text(printer, 80), security.text(family, 12).upper()
        self._store.update_settings("fit", lambda v: [x for x in fit.clean_profiles(v) if not (x["printer"] == fit._key(key) and x["family"] == fam)])
        return {"profiles": fit.clean_profiles(self._store.settings().get("fit") or []), "fit": self._design_fit()}

    # ===================================================== Calibration sans caméra (5.8)
    def _calib_printer(self):
        """Imprimante calibrée : clé, nom, modèle (préréglage du slicer) et firmware (choisi, sinon deviné)."""
        from backend import calibration
        s = self._store.settings()
        model = (s.get("slicer") or {}).get("printer_model") or "A1"
        cfg = self._printer.public_config() or {}
        key = security.text(cfg.get("serial") or cfg.get("name") or model, 60).upper() or "DEFAULT"
        saved = calibration.clean(s.get("calib")).get("printers", {}).get(key) or {}
        guess = calibration.firmware_of(cfg.get("protocol"))
        return {"key": key, "label": cfg.get("name") or f"Bambu Lab {model}", "model": model,
                "firmware": saved.get("firmware") if saved.get("at") or saved.get("label") else guess, "guess": guess}

    def _calib_spool(self, spool_id):
        sid = security.record_id(spool_id)
        spool = next((x for x in self._store.spools() if str(x.get("id")) == str(sid)), None)
        if not spool:
            raise security.ValidationError("Bobine introuvable.")
        return spool

    @exposed()
    def calib_info(self, spool_id):
        """Tout ce qu'affiche l'assistant : bobine, plages conseillées, valeurs calibrées, historique, fiche à recopier."""
        from backend import calibration
        from backend.design import geom
        spool = self._calib_spool(spool_id)
        data = calibration.clean(self._store.settings().get("calib"))
        printer = self._calib_printer()
        entry = data["spools"].get(str(spool["id"])) or {"at": {}}
        r = calibration.ranges(spool.get("material"))
        lo, hi = calibration.nozzle_range(spool.get("material"))
        label = f"{spool.get('material') or ''} {spool.get('color_name') or ''}".strip()
        history = [x for x in data["history"] if x["who"] in (label, printer["label"])][:20]
        return {"spool": {k: spool.get(k) for k in ("id", "brand", "material", "color_name", "k_factor", "flow_ratio",
                                                       "max_volumetric_speed")},
                "family": calibration.family(spool.get("material")), "k_range": list(r["k"]), "vol_range": list(r["vol"]),
                "nozzle_range": [lo, hi], "entry": entry, "printer": printer,
                "printer_entry": data["printers"].get(printer["key"]) or {"at": {}}, "history": history,
                "sheet": calibration.copy_sheet(spool, entry),
                "parts": not device.ANDROID and geom.mf is not None, "firmwares": list(calibration.FIRMWARES)}

    @exposed()
    def calib_compute(self, test, inputs, spool_id):
        """Calcul seul (aperçu pendant la saisie) : rien n'est enregistré."""
        from backend import calibration
        spool = self._calib_spool(spool_id)
        return {"result": calibration.compute(security.text(test, 20), inputs, spool.get("material"))}

    @exposed()
    def calib_save(self, test, inputs, spool_id):
        """Refait le calcul (jamais le chiffre de l'interface), écrit la bobine ou le réglage « calib », garde l'historique."""
        from backend import calibration
        test = security.text(test, 20)
        spool = self._calib_spool(spool_id)
        result = calibration.compute(test, inputs, spool.get("material"))
        printer = self._calib_printer()
        if test in calibration.PRINTER_TESTS and isinstance(inputs, dict) and inputs.get("firmware") in calibration.FIRMWARES:
            printer["firmware"] = inputs["firmware"]
        if result["spool"]:
            self._store.update_spool(spool["id"], result["spool"])
        label = f"{spool.get('material') or ''} {spool.get('color_name') or ''}".strip()
        self._store.update_settings("calib", lambda v: calibration.record(
            v, result, spool["id"], label, printer["key"], printer["label"], printer["firmware"]))
        out = self.calib_info(spool["id"])
        out["result"] = result
        unit = f" {result['unit']}" if result["unit"] else ""
        value = calibration._fmt(result["value"], result["digits"])
        where = printer["label"] if test in calibration.PRINTER_TESTS else label
        out["msg"] = f"{result['label']} : {value}{unit} enregistré pour {where}."
        return out

    @exposed()
    def calib_firmware(self, firmware):
        from backend import calibration
        p = self._calib_printer()
        fw = security.text(firmware, 10)
        if fw not in calibration.FIRMWARES:
            raise security.ValidationError("Firmware inconnu.")
        self._store.update_settings("calib", lambda v: calibration.set_firmware(v, p["key"], p["label"], fw))
        return {"firmware": fw}

    @exposed()
    def calib_part(self, kind, params):
        """Pièce de test à imprimer : elle s'ouvre dans Conception (aperçu, fichier, envoi au slicer)."""
        if device.ANDROID:
            raise security.ValidationError("Les pièces de test se créent sur le PC.")
        from backend.design import calib_parts
        bed = tuple(self._design_printer()["bed"])
        return {"job": calib_parts.job(self._design(), security.text(kind, 12), params if isinstance(params, dict) else {}, bed[:2])}

    @exposed()
    def calib_preset(self, spool_id):
        """Préréglage de filament .json pour OrcaSlicer et Bambu Studio, avec les valeurs calibrées de la bobine."""
        from backend import calibration
        spool = self._calib_spool(spool_id)
        entry = calibration.clean(self._store.settings().get("calib"))["spools"].get(str(spool["id"])) or {}
        printer = self._calib_printer()
        data = calibration.preset(spool, entry, printer["model"] if printer["firmware"] == "bambu" else "",
                                  config.GRAOULLY_LIBRARY_DIR, config.APP_VERSION)
        folder = os.path.join(config.EXPORT_DIR, "Calibration")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, security.filename(data["name"] + ".json", (".json",)))
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        base = data["inherits"] or "aucun (préréglage autonome)"
        return {"path": target, "inherits": data["inherits"],
                "msg": f"Préréglage enregistré : {target}. Hérite de : {base}. Dans le slicer : Fichier › Importer › "
                       "Importer les configurations."}

    @exposed()
    def design_materials(self):
        from backend.design import materials
        out = materials.public()
        stock = {}
        for sp in self._store.own_spools():
            key = (materials.guess(sp.get("material")) or {}).get("key")
            if key:
                stock[key] = stock.get(key, 0) + float(sp.get("weight_remaining") or 0)
        out["stock"] = {k: round(v) for k, v in stock.items()}
        out["needs"] = [{"key": k, "label": v} for k, v in materials.NEEDS]
        from backend.graoully import profiles             # 5.3 : profil de départ pour cette imprimante
        printer = self._design_printer()
        out["profiles"] = {m["key"]: profiles.start_profile(m, printer) for m in materials.MATERIALS}
        return out

    @exposed()
    def design_advise(self, params):
        from backend.design import materials
        params = params if isinstance(params, dict) else {}
        p = self._design_printer()
        needs = [security.text(n, 20) for n in (params.get("needs") or [])][:12]
        return materials.advise(needs, params.get("heat", 0), p["enclosure"], p["hardened"], p["max_nozzle"], p["max_bed"],
                                self._store.own_spools())

    @exposed()
    def design_save(self, sid, fmt, open_it=False):
        out = self._design().save(sid, "stl" if fmt == "stl" else "3mf", tuple(self._design_printer()["bed"]))
        if security.boolean(open_it):
            _open_local(out["folder"])
        return {"folder": out["folder"], "files": out["files"], "msg": "Enregistré dans " + out["folder"]}

    # 4.3 : réparation et mise à l'échelle
    @exposed()
    def design_diagnose(self, sid, part=0):
        from backend.design import repair
        out = self._design().diagnose(sid, part)
        out["hint"] = repair.scale_hint(out["size"], self._design_printer()["bed"])
        return out

    @exposed()
    def design_repair(self, sid, part=0, opts=None):
        return {"job": self._design().repair(sid, part, opts)}

    @exposed()
    def design_scale(self, sid, part, data):
        return self._design().scale(sid, part, data, tuple(self._design_printer()["bed"]))

    @exposed()
    def design_to_slicer(self, sid):
        """Le résultat (.3mf) part dans Slicer Express : découpe, puis envoi avec la confirmation habituelle."""
        out = self._design().save(sid, "3mf", tuple(self._design_printer()["bed"]))
        return {"file": self._remember_pick(out["path"])}

    # ========================================================= Aperçu 3D
    @exposed()
    def preview_model(self, source):
        """Maillage d'un modèle choisi (jeton) ou déposé (octets) pour l'aperçu 3D."""
        source = source if isinstance(source, dict) else {}
        allowed = (".stl", ".3mf", ".obj", ".gcode")
        if source.get("token"):
            path = self._picked_path(source["token"])
            security.filename(os.path.basename(path), allowed)
            if path.lower().endswith((".gcode.3mf", ".gcode")):   # déjà découpé : trajets couche par couche
                try:
                    return self._preview(preview3d.gcode, plate_gcode(path))
                except security.ValidationError:
                    pass
            return self._preview(preview3d.mesh, path)
        name = security.filename(source.get("name"), allowed)
        raw = security.b64_payload(source.get("data"), config.MAX_FILE_BYTES)
        with tempfile.TemporaryDirectory(prefix="graou3d_apercu_") as tmp:
            path = os.path.join(tmp, "modele" + os.path.splitext(name)[1].lower())
            with open(path, "wb") as f:
                f.write(raw)
            return self._preview(preview3d.mesh, path)

    @exposed()
    def preview_job(self, job_id):
        """Trajets du G-code produit par Slicer Express, couche par couche."""
        job = self._slicer.job(str(job_id))
        if not job or job.status != "ready":
            raise security.ValidationError("La découpe n'est pas prête.")
        return self._preview(preview3d.gcode, plate_gcode(job.output_path))

    @staticmethod
    def _preview(fn, arg):
        if preview3d.np is None:
            raise security.ValidationError("Aperçu 3D indisponible sur cet appareil.")
        try:
            return {"preview": fn(arg)}
        except ValueError as e:
            raise security.ValidationError(str(e))

    @exposed()
    def slicer_discard(self, job_id):
        job = self._slicer.job(str(job_id))
        if job:
            job.cleanup()
            job.status = "done"

    # ================================================ Sauvegarde du compte
    def _backup_folder(self):
        return os.path.join(config.EXPORT_DIR, "Sauvegardes")

    def _business_stores(self):
        """5.4 : tables de gestion pour la sauvegarde (une table absente est simplement ignorée)."""
        return business_backup.Stores(getattr(self, "_quotes", None), getattr(self, "_clients", None),
                                      getattr(self, "_orders", None), getattr(self, "_invoices", None), self._client.user_id,
                                      getattr(self, "_products", None) if self._enterprise("catalog") else None,
                                      getattr(self, "_print_recipes", None))

    @exposed()
    def backup_export(self):
        payload = backup_service.export_data(self._store, config.APP_VERSION, self._business_stores())
        path = backup_service.write_backup(payload, self._backup_folder())
        return {"path": path, "summary": backup_service.summary(payload), "msg": "Sauvegarde enregistrée : " + path}

    @exposed()
    def backup_auto_info(self):
        """Sauvegardes automatiques de ce compte sur ce PC (Réglages › Données)."""
        folder = backup_service.auto_folder(config.DATA_DIR)
        files = backup_service.auto_files(folder, backup_service.account_tag(self._client.user_id))
        return {"enabled": local_prefs.get("auto_backup", "on") != "off", "keep": backup_service.AUTO_KEEP,
                "items": [{"name": os.path.basename(p), "at": int(t)} for p, t in files]}

    @exposed()
    def backup_auto_set(self, on):
        on = on is True
        local_prefs.put("auto_backup", "on" if on else "off")
        if on:
            self._pool.submit(self._auto_backup)
        return {"enabled": on}

    @exposed()
    def backup_pick(self):
        picked = self._pick_file(("Sauvegarde Graou3D (*.graou3d;*.json)",))
        if not picked:
            return {"ok": False, "msg": ""}
        payload = backup_service.read_file(self._picked_path(picked["token"]))
        return {"token": picked["token"], "name": picked["name"], "summary": backup_service.summary(payload)}

    @exposed()
    def backup_restore(self, token, mode, confirmed):
        mode = str(mode)
        if mode == "replace" and confirmed is not True:
            raise security.ValidationError("Confirmez le remplacement de toutes vos données.")
        payload = backup_service.read_file(self._picked_path(token))      # relu et revérifié
        safety = ""
        if mode == "replace":
            current = backup_service.export_data(self._store, config.APP_VERSION, self._business_stores())
            safety = backup_service.write_backup(current, self._backup_folder(), "securite-avant-restauration")
        result = backup_service.restore(self._store, payload, mode, self._business_stores(),
                                        self._store.settings().get("quote") or {})
        c = result["counts"]
        msg = (f"Restauration terminée : {c['spools']} bobine(s), {c['tasks']} tâche(s), {c['maintenance']} rappel(s), "
               f"{c['shopping']} achat(s), {c['projects']} projet(s), {c['bookmarks']} favori(s)")
        gestion = [f"{c[k]} {label}" for k, label in (("clients", "client(s)"), ("quotes", "devis"), ("orders", "commande(s)"))
                   if c.get(k)]
        msg += (", " + ", ".join(gestion) if gestion else "") + "."
        if c.get("quotes"):
            msg += " Les devis restaurés ont reçu un nouveau numéro (l'ancien est noté dans le devis)."
        if result.get("archived"):
            msg += (f" {result['archived']} facture(s) et avoir(s) gardés en archive dans le fichier : une facture émise reste "
                    "toujours dans le compte, elle n'est jamais réimportée.")
        if result["skipped"]:
            msg += f" {result['skipped']} élément(s) déjà présent(s) ou refusé(s)."
        if safety:
            msg += " Sauvegarde de sécurité : " + safety
        return {"result": result, "safety": safety, "msg": msg}

    # ============================================ Mes données (5.4 : RGPD)
    def _local_data(self):
        """Ce que ce PC garde pour ce compte ou pour Graou3D (sans aucune clé ni chemin personnel)."""
        prefs = {k: local_prefs.get(k) for k in ("lang", "theme", "anim", "update_auto", "auto_backup", "mw_keep_login")
                 if local_prefs.get(k) is not None}
        try:
            graoully = self._graoully.settings()
            graoully = {"mode": graoully.get("mode"), "api": {"provider": graoully["api"].get("provider"),
                                                             "model": graoully["api"].get("model")}}
        except Exception:  # noqa: BLE001 - réglages du Graoully illisibles : on exporte le reste
            graoully = {}
        return {"preferences": prefs, "graoully": graoully,
                "reservations_bobines": graoully_actions.reservations(self._graoully.folder)}

    @exposed()
    def data_export_all(self):
        """« Exporter toutes mes données » : ZIP (JSON complet, CSV, PDF des factures et devis)."""
        data, missing = data_export.collect(self._client, self._client.user_id, self._auth.email, self._local_data())
        logo = ""
        try:
            _, logo = self._quotes.seller()
        except (security.ValidationError, SupabaseError):
            pass
        path = os.path.join(ACCOUNTING_DIR, data_export.file_name("mes-donnees-graou3d"))
        counts = data_export.write_zip(path, data, config.APP_VERSION, missing, logo)
        return {"path": path, "name": os.path.basename(path), "counts": counts, "missing": missing,
                "msg": "Export enregistré : " + path}

    def _find_client(self, key):
        people = self._clients_payload()["clients"]
        person = next((p for p in people if p.get("key") == str(key or "")), None)
        if not person:
            raise security.ValidationError("Client introuvable.")
        return person

    @exposed()
    def client_export(self, key):
        """Fiche, devis, commandes et factures d'un client (sa demande d'accès RGPD) : ZIP JSON + CSV + PDF."""
        self._require("clients")
        person = self._find_client(key)
        quote_rows, order_rows, invoice_rows = self._business_rows()
        try:
            saved = self._clients.list()
        except security.ValidationError:
            saved = []
        data = data_export.client_data(person, saved, quote_rows, order_rows, invoice_rows)
        logo = ""
        try:
            _, logo = self._quotes.seller()
        except (security.ValidationError, SupabaseError):
            pass
        path = os.path.join(ACCOUNTING_DIR, data_export.file_name("client-" + data_export.slug(person.get("name"))))
        data_export.write_zip(path, data, config.APP_VERSION, logo=logo, who=f" pour {person.get('name')}",
                              what=f"toutes les données que vous avez sur {person.get('name')}")
        return {"path": path, "name": os.path.basename(path),
                "msg": f"Données de {person.get('name')} enregistrées : {path}. Vous pouvez envoyer ce fichier au client."}

    @exposed()
    def client_anonymize(self, key, confirmed):
        """Efface les coordonnées d'un client à sa demande : fiche supprimée, nom remplacé dans les devis et commandes.
        Les factures émises ne changent pas : la loi impose de les garder (10 ans) telles qu'elles ont été émises."""
        self._require("clients")
        if confirmed is not True:
            raise security.ValidationError("Confirmez l'anonymisation.")
        person = self._find_client(key)
        label = "Client anonymisé " + dt.date.today().strftime("%d/%m/%Y")
        quote_rows, order_rows, invoice_rows = self._business_rows()
        data = data_export.client_data(person, [], quote_rows, order_rows, invoice_rows)
        uid = self._client.user_id
        for q in data["devis"]:
            self._client.update("quotes", {"client": {"name": label}}, [("id", "eq", q["id"]), ("user_id", "eq", uid)])
        for o in data["commandes"]:
            self._client.update("orders", {"client": {"name": label, "email": ""}}, [("id", "eq", o["id"]), ("user_id", "eq", uid)])
        try:
            for c in self._clients.list():
                if data_export._same(c, person.get("key"), clients_mod.key_of(person.get("name"))):
                    self._clients.delete(c["id"])
        except security.ValidationError:
            pass
        n_inv = len(data["factures"])
        msg = f"{person.get('name')} est anonymisé : {len(data['devis'])} devis et {len(data['commandes'])} commande(s) modifiés."
        if n_inv:
            msg += f" Ses {n_inv} facture(s) ne changent pas : la loi impose de les garder 10 ans."
        return dict(self._clients_payload(), msg=msg)

    @exposed()
    def account_delete(self, password, confirm_text):
        """Supprime le compte en ligne (toutes les données, migration 011) puis ce que ce PC garde pour lui."""
        if str(confirm_text or "").strip().upper() != "SUPPRIMER":
            raise security.ValidationError("Tapez SUPPRIMER pour confirmer.")
        email = self._auth.email
        if not password or not email:
            raise security.ValidationError("Entrez votre mot de passe.")
        uid = self._client.user_id
        try:
            self._client.sign_in(email, str(password))          # vérifie que c'est bien la personne
        except SupabaseError:
            raise security.ValidationError("Mot de passe incorrect.") from None
        try:
            printers = self._store.printers() or []
        except (security.ValidationError, SupabaseError):
            printers = []
        try:
            self._client.rpc("delete_my_account", {"p_confirm": "SUPPRIMER"})
        except SupabaseError as e:
            if e.status == 404 or e.code in ("PGRST202", "42883"):
                raise security.ValidationError(
                    "La suppression du compte n'est pas encore activée sur le serveur : l'administrateur de Graou3D doit "
                    "exécuter supabase/migrations/011_graou3d_v54_suppression_compte.sql. Rien n'a été supprimé.") from None
            raise
        log.info("Compte supprimé à la demande de la personne.")
        # Ce PC : codes des imprimantes, jeton Bambu Cloud, session, sauvegardes automatiques et réservations du compte.
        try:
            self._printer.forget_account(printers)
        except Exception:  # noqa: BLE001 - le compte en ligne est déjà supprimé : on nettoie ce qu'on peut
            log.exception("Nettoyage des codes d'imprimante incomplet")
        removed = backup_service.rotate(backup_service.auto_folder(config.DATA_DIR), backup_service.account_tag(uid), keep=0)
        try:
            os.remove(os.path.join(self._graoully.folder, "reservations.json"))
        except OSError:
            pass
        self._store.reset_cache()
        try:
            self._auth.logout()
        except Exception:  # noqa: BLE001 - le compte n'existe plus : la déconnexion en ligne peut échouer
            self._client.clear_session()
        return {"deleted": True, "auto_backups_removed": len(removed),
                "msg": "Votre compte et toutes ses données ont été supprimés. Les fichiers que vous aviez exportés "
                       "(dossier Téléchargements\\Graou3D) restent sur ce PC : supprimez-les si vous n'en voulez plus."}

    # ===================================================== Ancien fichier
    @exposed()
    def legacy_import(self):
        picked = self._pick_file(("Ancienne sauvegarde (*.json)",))     # 5.1.3 : filtre valide pour pywebview
        if not picked:
            return {"ok": False, "msg": ""}
        summary = import_legacy(self._picked_path(picked["token"]), self._store)
        return {"summary": summary, "token": picked["token"], "name": picked["name"]}

    @exposed()
    def legacy_delete(self, token):
        path = self._picked_path(token)
        if os.path.basename(path).lower() != "local_companion_store.json":
            raise security.ValidationError("Seul l'ancien fichier local_companion_store.json peut être supprimé ici.")
        os.remove(path)
        return {"msg": "Ancien fichier supprimé."}

    # ================================================= 5.0 : Graoully, l'assistant
    def _graoully_situation(self):
        """Ce que Graou3D sait déjà, pour les réponses calculées (coach) : jamais d'action sur l'imprimante."""
        out = {}
        try:
            settings = self._store.settings()
            cfg = self._printer.public_config() or {}
            t = self._printer.telemetry() or {}
            dp = self._design_printer()
            out["printer"] = {
                "name": cfg.get("name") or "", "model": dp.get("model") if cfg.get("configured") else "",
                "family": machine_family(cfg.get("protocol", ""), dp.get("model", "") if cfg.get("configured") else "",
                                         cfg.get("name", "")) or ("bambu" if cfg.get("configured") and "BAMBU" in str(cfg.get("protocol", "")).upper() else ""),
                "connected": bool(t.get("connected") or t.get("gcode_state")), "printing": bool(t.get("printing")),
                "progress": t.get("progress") or 0, "remaining_min": t.get("remaining_min") or 0,
                "file": str(t.get("subtask_name") or "")[:80], "nozzle": str(settings.get("active_nozzle") or ""),
                "enclosure": dp.get("enclosure") if cfg.get("configured") else None, "bed": dp.get("bed"),
                "label": dp.get("label"), "max_nozzle": dp.get("max_nozzle"), "max_bed": dp.get("max_bed"),
                "hardened": dp.get("hardened")}
            stats = settings.get("stats") or {}
            out["stats"] = {"success": stats.get("prints_success", 0), "failed": stats.get("prints_failed", 0),
                            "streak": stats.get("success_streak", 0)}
            hours = float(stats.get("total_hours_printed") or 0)
            out["maintenance_due"] = [str(m.get("title") or "")[:60] for m in self._store.maintenance()
                                      if maintenance_presets.is_due(m, hours)]
            out["spools"] = [{"material": s.get("material"), "color": s.get("color_name") or "",
                              "remaining_g": s.get("weight_remaining") or 0, "ams_slot": s.get("ams_slot") or 0,
                              "needs_drying": bool(s.get("needs_drying")), "days_open": s.get("days_open") or 0}
                             for s in self._store.own_spools()][:40]
            todo = [x for x in self._store.tasks() if x.get("status") == "todo"]
            todo.sort(key=lambda x: (x.get("position") if isinstance(x.get("position"), (int, float)) else 1e9,
                                     str(x.get("due_on") or "9999"), str(x.get("created_at") or "")))
            out["queue_count"] = len(todo)
            out["queue"] = [{"title": str(t.get("title") or "")[:80], "material": t.get("material") or "",
                             "grams": t.get("estimated_grams") or 0, "due_on": t.get("due_on") or ""} for t in todo[:60]]
            if todo:
                n = todo[0]
                out["next"] = {"title": str(n.get("title") or "")[:80], "material": n.get("material") or "",
                               "grams": n.get("estimated_grams") or 0, "nozzle": n.get("required_nozzle") or "",
                               "due_on": n.get("due_on") or ""}
        except (security.ValidationError, SupabaseError, OSError, TypeError, ValueError) as e:
            log.info("Graoully : données de l'appli partielles (%s)", e)
        return out

    def _graoully_action_ctx(self):
        """Données que Graoully peut citer et modifier, avec leurs identifiants (5.3, élargi en 5.5.3) :
        bobines, impressions de la file, rappels d'entretien, liste d'achats et clients."""
        spools = [{"id": s.get("id"), "material": s.get("material") or "", "color": s.get("color_name") or "",
                   "brand": s.get("brand") or "", "hex": s.get("color_hex") or "", "ams_slot": s.get("ams_slot") or 0,
                   "remaining_g": s.get("weight_remaining") or 0, "initial_g": s.get("weight_initial") or 0,
                   "opened_at": s.get("opened_at") or ""}
                  for s in self._store.own_spools() if s.get("id")][:80]
        tasks = [{"id": t.get("id"), "title": str(t.get("title") or "")[:80], "material": t.get("material") or "",
                  "grams": t.get("estimated_grams") or 0, "status": t.get("status")} for t in self._store.tasks()
                 if t.get("status") in ("todo", "printing") and t.get("id")][:60]
        hours = float(self._store.settings()["stats"].get("total_hours_printed") or 0)
        maintenance = [{"id": m.get("id"), "title": str(m.get("title") or "")[:80], "interval_hours": m.get("interval_hours") or 0,
                        "last_done_hours": m.get("last_done_hours") or 0, "due": maintenance_presets.is_due(m, hours)}
                       for m in self._store.maintenance() if m.get("id")][:40]
        shopping = [str(x.get("name") or "") for x in self._store.shopping() if not x.get("done")][:200]
        try:
            clients = [{"id": c.get("id"), "name": str(c.get("name") or "")[:80], "email": c.get("email") or "",
                        "address": c.get("address") or ""} for c in self._clients.list() if c.get("id")][:200]
        except (security.ValidationError, SupabaseError):
            clients = []                                # carnet de clients pas encore créé (migration 010)
        held = graoully_actions.prune(self._graoully.folder, [s["id"] for s in spools],
                                      [t["id"] for t in tasks if t["status"] == "todo"])
        for s in spools:
            s["reserved_for"] = (held.get(str(s["id"])) or {}).get("task", "")
        return {"spools": spools, "tasks": tasks, "maintenance": maintenance, "shopping": shopping, "clients": clients,
                "reservations": held}

    @exposed()
    def graoully_act(self, action):
        """5.3 : l'utilisateur a confirmé une action proposée par Graoully. Tout est revérifié ici (5.5.3 : 12 actions,
        jamais deux fois la même d'affilée, et un « Défaire » quand c'est possible). Rien n'est envoyé à l'imprimante."""
        action = action if isinstance(action, dict) else {}
        checked = graoully_actions.check(action, self._graoully_action_ctx())
        kind, p = checked["type"], checked["params"]
        if kind in graoully_actions.PC_ONLY_TYPES and device.ANDROID:
            raise security.ValidationError("Sur le téléphone, cette action n'est pas disponible : elle se fait sur le PC.")
        key = json.dumps([kind, {k: v for k, v in p.items() if k != "before"}], sort_keys=True, ensure_ascii=False, default=str)
        now = time.time()
        with self._graoully_lock:
            done = self._graoully_recent
            for k in [k for k, t in done.items() if now - t > 20]:
                done.pop(k)
            if key in done:
                raise security.ValidationError("C'est déjà fait : cette action vient d'être confirmée.")
            done[key] = now
        try:
            res, undo = self._graoully_do(kind, p)
        except Exception:
            with self._graoully_lock:
                self._graoully_recent.pop(key, None)
            raise
        res.update(done=kind, go=checked["go"])
        if undo:
            token = pysecrets.token_hex(8)
            with self._graoully_lock:
                self._graoully_undos[token] = (now, kind, undo)
                for k in sorted(self._graoully_undos, key=lambda k: self._graoully_undos[k][0])[:-30]:
                    self._graoully_undos.pop(k)
            res["undo"] = token
        return res

    def _graoully_do(self, kind, p):
        """Fait l'action vérifiée → (réponse, de quoi la défaire ou None)."""
        if kind == "spool_add":
            rows = []
            self._store.add_spools(dict(p, color_hex=p.get("color_hex") or "#9E9E9E"), rows)
            n = p["quantity"]
            ids = [r.get("id") for r in rows if r.get("id")]
            return ({"msg": "Bobine ajoutée au stock." if n == 1 else f"Bobines ajoutées au stock : {n}."},
                    {"spools": ids} if ids else None)
        if kind == "spool_update":
            before = p["before"]
            change = {k: v for k, v in p.items() if k not in ("spool", "before")}
            self._store.update_spool(p["spool"], change)
            return {"msg": "Bobine mise à jour."}, {"spool": p["spool"], "values": {k: before[k] for k in change if k in before}}
        if kind == "spool_dry":
            spool = next((s for s in self._store.spools() if str(s.get("id")) == p["spool"]), None)
            if not spool:
                raise security.ValidationError("Bobine introuvable.")
            item = self._scheduler.drying_add(spool, p["temp"], p["hours"])
            return ({"msg": f"Minuteur lancé : {item['label']}, {item['hours']} h à {item['temp']} °C. "
                            "Je vous préviens à la fin (l'application doit rester ouverte)."}, {"drying": item["id"]})
        if kind == "spool_dried":
            self._store.mark_dried(p["spool"])
            return {"msg": "Bobine notée comme séchée."}, ({"spool": p["spool"], "opened_at": p["before"]} if p["before"] else None)
        if kind == "spool_reserve":
            graoully_actions.reserve(self._graoully.folder, p["spool"], p["task"], p["title"])
            return {"msg": f"Bobine réservée pour « {p['title']} » (sur ce PC)."}, {"release": p["spool"]}
        if kind == "queue_add":
            if "due_on" in p and not self._store.planning_ready():
                p = {k: v for k, v in p.items() if k != "due_on"}
            task = self._store.add_task(p)
            return {"task": task, "msg": f"« {p['title']} » ajoutée à la file d'attente."}, {"task": task.get("id")}
        if kind == "task_status":
            before = self._store.move_task(p["task"], p["status"])
            if p["status"] == "done":
                emptied = False
                if before.get("status") != "done":          # comme task_move : journal et filament décompté
                    grams = int(before.get("estimated_grams") or 0)
                    self._store.record_print(True, grams)
                    emptied = bool(self._store.consume_filament(grams).get("emptied"))
                msg = f"« {p['title']} » est marquée imprimée." + (" Une bobine est maintenant vide." if emptied else "")
                return {"msg": msg}, None       # le filament est déjà décompté : à corriger à la main si besoin
            return {"msg": f"« {p['title']} » est de nouveau en attente."}, {"task": p["task"], "status": p["before"]}
        if kind == "maint_done":
            self._store.maintenance_done(p["reminder"], self._store.settings()["stats"].get("total_hours_printed", 0))
            return {"msg": f"Entretien « {p['title']} » noté comme fait."}, {"reminder": p["reminder"], "hours": p["before"]}
        if kind == "maint_add":
            row = self._store.add_maintenance(p["title"], p["interval_hours"],
                                              self._store.settings()["stats"].get("total_hours_printed", 0))
            return {"msg": f"Rappel « {p['title']} » ajouté."}, {"maintenance": (row or {}).get("id")}
        if kind == "shop_add":
            row = self._store.add_shopping(p["name"], p["category"], p["priority"])
            return {"msg": f"« {p['name']} » ajouté à la liste d'achats."}, {"shopping": (row or {}).get("id")}
        if kind == "client_add":
            self._require("clients")
            row = self._clients.save(p)
            return {"msg": f"{row.get('name') or p['name']} ajouté au carnet de clients."}, {"client": row.get("id")}
        # quote_draft
        self._require("quotes")
        quote = self._quotes.save({"status": "draft", "client": p["client"], "title": p["title"], "lines": p["lines"]},
                                  self._store.settings().get("quote") or {})
        return ({"quote": quote, "msg": f"Devis {quote.get('number') or ''} préparé en brouillon : vérifiez-le avant de l'envoyer."},
                {"quote": quote.get("id")} if quote.get("id") else None)

    @exposed()
    def graoully_undo(self, token):
        """5.5.3 : « Défaire » sous une action confirmée (pendant 30 minutes, tant que l'appli est ouverte)."""
        with self._graoully_lock:
            item = self._graoully_undos.pop(security.text(token, 32), None)
        if not item or time.time() - item[0] > 1800:
            raise security.ValidationError("Trop tard pour défaire cette action : corrigez-la directement dans l'onglet concerné.")
        _, kind, u = item
        if "spools" in u:
            for sid in u["spools"]:
                self._store.delete_spool(sid)
        elif "values" in u:
            self._store.update_spool(u["spool"], u["values"])
        elif "drying" in u:
            self._scheduler.drying_cancel(u["drying"])
        elif "opened_at" in u:
            self._store.set_opened(u["spool"], u["opened_at"])
        elif "release" in u:
            graoully_actions.release(self._graoully.folder, u["release"])
        elif "status" in u:
            self._store.move_task(u["task"], u["status"])
        elif "task" in u:
            self._store.delete_task(u["task"])
        elif "reminder" in u:
            self._store.set_maintenance_hours(u["reminder"], u["hours"])
        elif "maintenance" in u:
            self._store.delete_maintenance(u["maintenance"])
        elif "shopping" in u:
            self._store.delete_shopping(u["shopping"])
        elif "client" in u:
            self._clients.delete(u["client"])
        elif "quote" in u:
            self._quotes.delete(u["quote"])
        return {"msg": "C'est défait : tout est revenu comme avant.", "go": graoully_actions.GO.get(kind, "")}

    @exposed()
    def graoully_reservations(self):
        return {"reservations": graoully_actions.reservations(self._graoully.folder)}

    @exposed()
    def graoully_release(self, spool_id):
        graoully_actions.release(self._graoully.folder, spool_id)
        return {"reservations": graoully_actions.reservations(self._graoully.folder), "msg": "Réservation retirée."}

    @exposed()
    def graoully_info(self):
        return self._graoully.info()

    @exposed()
    def graoully_settings(self, changes):
        return {"settings": self._graoully.save_settings(changes)}

    @exposed()
    def graoully_key(self, key):
        """Clé API de l'utilisateur → coffre-fort de Windows. Vide : la clé est oubliée."""
        saved = self._graoully.set_key(key)
        return {"has_key": saved, "msg": "Clé enregistrée sur ce PC." if saved else "Clé oubliée."}

    @exposed()
    def graoully_test(self):
        reply = self._graoully.test_api()
        return {"reply": reply, "msg": "Ça marche : le modèle a répondu." if reply else "Le modèle a répondu, mais sans texte."}

    @exposed()
    def graoully_attach(self, images=False):
        """5.3 : fichier joint à une question. Modèles 3D dans tous les modes ; photos en mode API (ou pour Meshy)."""
        images = images is True and (self._graoully.settings()["mode"] == "api" or self._graoully.can_create()["organic"])
        if images:
            picked = self._pick_file(("Images et modèles 3D (*.png;*.jpg;*.jpeg;*.webp;*.stl;*.3mf;*.obj)",
                                      "Modèles 3D (*.stl;*.3mf;*.obj)", "Images (*.png;*.jpg;*.jpeg;*.webp)"))
        else:
            picked = self._pick_file(("Modèles 3D (*.stl;*.3mf;*.obj)",))
        if not picked:
            return {"cancelled": True}
        kind = graoully_attachments.kind(picked["name"])
        if not kind or (kind == "image" and not images):
            raise security.ValidationError("Formats acceptés : .stl, .3mf, .obj" + (", .png, .jpg, .webp" if images else "") + ".")
        return {"file": dict(picked, kind=kind)}

    @exposed()
    def graoully_ask(self, question, history=None, files=None):
        lang = local_prefs.get("lang", "fr") or "fr"
        return {"job": self._graoully.ask(question, history if isinstance(history, list) else [],
                                          self._graoully_situation(), lang, self._graoully_files(files))}

    def _graoully_files(self, files):
        joined = []
        for token in (files if isinstance(files, list) else [])[:graoully_attachments.MAX_FILES]:
            path = self._picked_path(token)
            kind = graoully_attachments.kind(path)
            if kind:
                joined.append({"kind": kind, "name": os.path.basename(path), "path": path})
        return joined

    @exposed()
    def graoully_create(self, request, files=None, kind="exact", creator=""):
        """5.3 : Graoully crée une pièce (formes exactes en mode API, ou forme organique). 5.5.2 : creator = le service
        choisi dans « Créer avec » (« free3d:trellis », « meshy », « ai:gemini »…)."""
        lang = local_prefs.get("lang", "fr") or "fr"
        return {"job": self._graoully.create(request, self._graoully_situation(), lang, self._graoully_files(files),
                                             "organic" if kind == "organic" else "exact", str(creator or "")[:40])}

    @exposed()
    def graoully_meshy_key(self, key):
        saved = self._graoully.set_meshy_key(key)
        return {"has_meshy": saved, "create": self._graoully.can_create(),
                "msg": "Clé Meshy enregistrée sur ce PC." if saved else "Clé Meshy oubliée."}

    @exposed()
    def graoully_hf_token(self, key):
        """5.5 : jeton Hugging Face (gratuit) pour les générateurs 3D gratuits : plus de calcul par jour."""
        saved = self._graoully.set_hf_token(key)
        return {"has_hf": saved, "msg": "Jeton Hugging Face enregistré sur ce PC." if saved else "Jeton Hugging Face oublié."}

    @exposed()
    def graoully_hf_test(self, full=False):
        """5.5.2 : vérifie le jeton Hugging Face et le générateur 3D choisi. 5.5.3 : full=True fait une vraie génération."""
        if full is True:
            return {"msg": self._graoully.test_hf_full()}
        return {"msg": self._graoully.test_hf()}

    @exposed()
    def graoully_job(self, job_id):
        job = self._graoully.job(job_id)
        if not job:
            raise security.ValidationError("Question introuvable.")
        return job

    @exposed()
    def graoully_cancel(self, job_id):
        self._graoully.cancel(job_id)
        return {}

    @exposed()
    def graoully_local_install(self, model_key, variant="vulkan"):
        if device.ANDROID:
            raise security.ValidationError("Le cerveau local s'installe sur le PC.")
        self._graoully.local.install(str(model_key or ""), "cpu" if variant == "cpu" else "vulkan")
        self._graoully.save_settings({"offer_seen": True})
        return {"local": self._graoully.local.status()}

    @exposed()
    def graoully_local_status(self):
        return {"local": self._graoully.local.status()}

    @exposed()
    def graoully_local_cancel(self):
        self._graoully.local.cancel_install()
        return {}

    @exposed()
    def graoully_local_pick(self, what):
        """Fichier choisi à la main : « model » (.gguf) ou « server » (llama-server.exe)."""
        if what == "server":
            # 5.1.3 : « Description (*.ext) » obligatoire pour pywebview (« llama-server (llama-server.exe) » était
            # refusé : « Erreur inattendue »). Le nom exact est vérifié ensuite par use_files.
            picked = self._pick_file(("Programme llama server (*.exe)",))
        else:
            picked = self._pick_file(("Modèle GGUF (*.gguf)",))
        if not picked:
            return {"cancelled": True}
        path = self._picked_path(picked["token"])
        self._graoully.local.use_files(server=path if what == "server" else None, model=path if what != "server" else None)
        return {"local": self._graoully.local.status(), "msg": "Fichier retenu : " + os.path.basename(path)}

    @exposed()
    def graoully_local_remove(self):
        self._graoully.local.remove()
        if self._graoully.settings()["mode"] == "local":
            self._graoully.save_settings({"mode": "library"})
        return {"local": self._graoully.local.status(), "msg": "Cerveau local effacé de ce PC."}

    # 5.7.0 : générateur 3D sur ce PC (TripoSR)
    @exposed()
    def graoully_local3d_install(self, quality="standard"):
        if device.ANDROID:
            raise security.ValidationError("Le générateur 3D local s'installe sur le PC.")
        self._graoully.local3d.install("max" if quality == "max" else "standard")
        return {"local3d": self._graoully.local3d.status()}

    @exposed()
    def graoully_local3d_status(self):
        return {"local3d": self._graoully.local3d.status()}

    @exposed()
    def graoully_local3d_cancel(self):
        self._graoully.local3d.cancel_install()
        return {}

    @exposed()
    def graoully_local3d_detail(self, detail):
        self._graoully.local3d.set_detail(str(detail or ""))
        return {"local3d": self._graoully.local3d.status()}

    @exposed()
    def graoully_local3d_remove(self):
        self._graoully.local3d.remove()
        return {"local3d": self._graoully.local3d.status(), "msg": "Générateur 3D local effacé de ce PC."}

    @exposed()
    def graoully_offer_seen(self):
        self._graoully.save_settings({"offer_seen": True})
        return {}
