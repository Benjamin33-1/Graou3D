"""Constantes, chemins et chargement du fichier .env.

Aucune donnée utilisateur n'est stockée ici : tout ce qui dépend d'un compte vit
dans Supabase. Le .env ne contient que l'adresse du projet et la clé publique.
"""
import os
import sys

APP_NAME = "Graou3D"
APP_VERSION = "5.8.9"


def _base_dir():
    # PyInstaller décompresse les ressources dans sys._MEIPASS
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
EXE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
UI_INDEX = os.path.join(BASE_DIR, "ui", "index.html")


def _read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key:
                    values[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


_ENV = {}
# Un .env posé à côté du .exe a priorité sur celui embarqué dans le bundle.
for _candidate in (os.path.join(BASE_DIR, ".env"), os.path.join(EXE_DIR, ".env")):
    _ENV.update(_read_env_file(_candidate))


def env(key, default=""):
    return os.environ.get(key) or _ENV.get(key, default)


SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY")


def is_secret_key(key):
    """Vrai pour une clé secrète Supabase (service_role / sb_secret_) : elle ne doit JAMAIS être livrée."""
    if key.startswith("sb_secret_"):
        return True
    try:
        import base64
        import json
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role") == "service_role"
    except (IndexError, ValueError, AttributeError):
        return False


SUPABASE_SECRET_KEY_REFUSED = is_secret_key(SUPABASE_ANON_KEY)
SUPABASE_CONFIGURED = (SUPABASE_URL.startswith("https://") and len(SUPABASE_ANON_KEY) > 20
                       and not SUPABASE_SECRET_KEY_REFUSED)

# Mises à jour (backend/updater.py) : versions publiées dans les « Releases » d'un dépôt GitHub public.
# UPDATE_REPO = « compte/dépôt » ; UPDATE_PUBLIC_KEY = clé publique Ed25519 (64 caractères hexadécimaux)
# affichée par « python tools/publier_version.py generer-cle ». Tant que l'un des deux est vide, les mises à jour
# sont désactivées.
# Ils sont FIGÉS à la fabrication : build_exe.bat lance « publier_version.py figer », qui écrit update_build.json
# (à partir de GRAOU3D_UPDATE_REPO / GRAOU3D_UPDATE_PUBLIC_KEY du .env du développeur) ; ce fichier est embarqué
# dans l'exe. Dans l'exe, un .env ou une variable d'environnement ne peut PAS les changer (sinon un faux .env
# donné à un ami détournerait les mises à jour). En développement (python main.py), le .env reste lu d'abord.
UPDATE_BUILD_FILE = os.path.join(BASE_DIR, "update_build.json")
_UPDATE_KEYS = {"repo": "GRAOU3D_UPDATE_REPO", "public_key": "GRAOU3D_UPDATE_PUBLIC_KEY"}


def read_update_build(path=None):
    """Valeurs figées à la fabrication ({"repo": ..., "public_key": ...}) ; {} si le fichier manque."""
    import json
    try:
        with open(path or UPDATE_BUILD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: str(data.get(k) or "").strip() for k in _UPDATE_KEYS} if isinstance(data, dict) else {}


def update_setting(name, frozen=None, build=None, env_get=None):
    """« repo » ou « public_key ». Exe figé : seulement la valeur de fabrication. Développement : .env/variable
    d'environnement d'abord (pour tester), puis la valeur de fabrication si elle existe."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    build = read_update_build() if build is None else build
    if frozen:
        return str(build.get(name) or "")
    return (env_get or env)(_UPDATE_KEYS[name], "") or str(build.get(name) or "")


UPDATE_REPO = update_setting("repo")
UPDATE_PUBLIC_KEY = update_setting("public_key")
# Seuls hôtes acceptés (y compris pendant les redirections) : GitHub et ses serveurs de fichiers.
UPDATE_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
UPDATE_MAX_BYTES = 300 * 1024 * 1024
UPDATE_CHECK_INTERVAL = 24 * 3600

# « Contacter le support » : adresse qui reçoit les messages. 5.4 : adresse de l'auteur intégrée par défaut ;
# GRAOU3D_SUPPORT_EMAIL (.env) la remplace pour une copie distribuée par quelqu'un d'autre.
DEFAULT_SUPPORT_EMAIL = "b.hbr57@gmail.com"
SUPPORT_EMAIL = env("GRAOU3D_SUPPORT_EMAIL", "") or DEFAULT_SUPPORT_EMAIL

# Coffre-fort Windows (Gestionnaire d'identification) : jeton de session et code de l'imprimante.
# Nom d'origine conservé (versions ≤ 3.4 « Bambu Companion ») pour ne pas perdre la session enregistrée.
KEYRING_SERVICE = "BambuCompanion"


def _folder(parent, name, old_name="BambuCompanion"):
    """Dossier « Graou3D ». L'ancien dossier « BambuCompanion » est renommé une fois (rien n'est effacé) ;
    s'il est verrouillé par Windows, on continue simplement de l'utiliser."""
    new, old = os.path.join(parent, name), os.path.join(parent, old_name)
    if not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)
        except OSError:
            return old
    return new


# Fichiers que l'utilisateur exporte volontairement (téléchargements, clichés caméra, journal).
EXPORT_DIR = _folder(os.path.join(os.path.expanduser("~"), "Downloads"), "Graou3D")

# Données locales de l'application (apprentissage de la surveillance caméra, modèle de vision).
DATA_DIR = _folder(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share"), "Graou3D")
# Langues de l'interface (dictionnaires dans ui/i18n/)
LANGUAGES = ("fr", "en", "de", "es", "it", "zh")

# Modèle de vision livré avec l'application (facultatif) : models/model.onnx + models/model.json
BUNDLED_MODELS_DIR = os.path.join(BASE_DIR, "models")

HTTP_TIMEOUT = 12
MAX_FILE_BYTES = 80 * 1024 * 1024      # fichiers envoyés depuis l'interface (STL, 3MF, G-code)
MAX_IMAGE_BYTES = 600 * 1024           # photo de publication (déjà réduite côté interface)
MAX_AVATAR_BYTES = 120 * 1024

# Imprimantes Bambu Lab
BAMBU_MQTT_PORT = 8883
BAMBU_FTPS_PORT = 990
BAMBU_CAMERA_PORT = 6000
BAMBU_CLOUD_API = "https://api.bambulab.com"
BAMBU_CLOUD_MQTT = {"us": "us.mqtt.bambulab.com", "cn": "cn.mqtt.bambulab.com"}
MAKERWORLD_URL = "https://makerworld.com/fr"

# 5.0 : bibliothèque de Graoully (livrée avec l'appli, lecture seule)
GRAOULLY_LIBRARY_DIR = os.path.join(BASE_DIR, "graoully")
