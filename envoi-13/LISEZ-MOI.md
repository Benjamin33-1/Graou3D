# Graou3D 5.8.9

*Anciennement « Bambu Companion ». Application indépendante, non affiliée à Bambu Lab, Prusa Research,
Klipper, OctoPrint ou MakerWorld.*

Suivi du stock de filament, de l'imprimante et de l'entretien, avec un vrai réseau d'amis.
Cette version reprend toute l'application : mêmes fonctions, code découpé en petits fichiers,
données uniquement dans Supabase, sécurité corrigée, interface épurée, et trois nouveautés :
**Slicer Express**, **détecteur de bouchage** et **surveillance caméra locale et gratuite**.

---

## 1. À faire tout de suite (sécurité)

L'ancienne version enregistrait sur le disque, en clair, **le mot de passe du compte**, le
**code d'accès LAN de l'imprimante** et les jetons. L'archive `bambu-v2.zip` les contient.

1. Changez le mot de passe du compte Graou3D (et partout où il était réutilisé).
2. Régénérez le code d'accès LAN sur l'écran de l'imprimante (Réglages › Réseau).
3. Supprimez les anciens `local_companion_store.json` et `bambu_companion.db` (après avoir
   importé vos données, voir § 4).

## 2. Installer

Python 3.11 ou plus récent, sous Windows.

```bash
python -m pip install -r requirements.txt
```

Copiez `.env.example` en `.env` et remplissez l'adresse et la clé **publique** (anon) du projet
Supabase, puis lancez :

```bash
python main.py
```

Pour fabriquer Graou3D : double-cliquez sur `build_exe.bat`. Il lance les tests, contrôle les
failles connues des bibliothèques (pip-audit), fabrique `dist\Graou3D\Graou3D.exe` puis, si Inno Setup 6
est installé, l'installateur `dist\Graou3D-Setup-<version>.exe` (voir § 8). Le fichier `.env` est copié
à côté du `.exe` et installé avec lui.

## 3. Préparer Supabase (une seule fois)

1. **Schéma et règles d'accès** — Supabase › SQL Editor › collez et exécutez
   `supabase/migrations/001_bambu_companion_v3.sql`. Le script reprend les tables existantes
   (profils, bobines, tâches, anciennes amitiés), supprime les anciennes règles d'accès et peut être
   relancé sans risque.
2. **Codes à 6 chiffres par email** — Authentication › Email Templates : dans « Confirm signup »
   et « Reset password », affichez le code avec `{{ .Token }}` (l'application demande ce code ;
   le lien de confirmation seul ne suffit pas).
   Puis exécutez aussi `supabase/migrations/002_graou3d_v35.sql` (imprimantes Prusa et partage des
   réglages filament ; peut être relancé).
   Pour les devis (3.6, Réglages › Fonctions) : `supabase/migrations/003_graou3d_v36_devis.sql`
   (tables des devis et numérotation automatique ; peut être relancé).
   Puis `supabase/migrations/004_graou3d_v36_securite.sql` (plafonds par compte, onglet Amis protégé,
   pseudos ; peut être relancé).
   Pour le mode entreprise (3.7) : `supabase/migrations/005_graou3d_v37_atelier.sql` (atelier partagé
   entre plusieurs comptes et commandes clients ; peut être relancé).
   **Si vous relancez un jour 001**, relancez ensuite 004 puis 005, dans cet ordre : 001 remplace les
   règles d'accès et plusieurs fonctions (onglet Amis, prêts, quotas). 005 doit toujours passer en
   dernier. Entre-temps, l'atelier reste protégé (un déclencheur refuse toute ligne ajoutée à un
   atelier sans le rôle gestion), mais ses membres n'y ont plus accès jusqu'à la relance de 005.
   Test de la migration : `tests/sql/test005_atelier.sql` (mode d'emploi en tête du fichier).
   Puis `supabase/migrations/006_graou3d_v38_gestion.sql` (factures et avoirs, nature des ventes pour
   l'URSSAF, messagerie entre amis ; peut être relancé, passe après 005). Test : `tests/sql/test006_gestion.sql`.
   Puis `007_graou3d_v386_creality.sql` (imprimantes Creality) et `008_graou3d_v387_saisons.sql`
   (table `app_config` des modes saisons).
   Puis `009_graou3d_v40_messages_24h.sql` (4.0 : les messages entre amis s'effacent 24 h après l'envoi ;
   passe après 006, peut être relancé).
3. **Envoi des emails par Brevo** — Authentication › SMTP Settings : serveur `smtp-relay.brevo.com`,
   port 587, identifiants SMTP Brevo. Les emails de confirmation partent alors par Brevo.
4. **Fonction serveur** (alertes email) — avec la
   [CLI Supabase](https://supabase.com/docs/guides/cli) :

   ```bash
   supabase login
   supabase link --project-ref VOTRE_REF
   supabase secrets set BREVO_API_KEY=xkeysib-... BREVO_SENDER_EMAIL=alertes@votredomaine.fr BREVO_SENDER_NAME="Graou3D"
   supabase functions deploy send-alert
   ```

   L'adresse `BREVO_SENDER_EMAIL` doit être un expéditeur validé dans Brevo. Sans cette fonction,
   l'application marche : seuls les emails d'alerte sont indisponibles.

   Depuis la 3.4, aucune IA payante n'est utilisée. Si vous aviez déployé l'ancienne analyse IA,
   supprimez-la : `supabase functions delete detect-defects` puis `supabase secrets unset ANTHROPIC_API_KEY`.

## 4. Récupérer les anciennes données

Réglages › Données › **Importer l'ancien fichier** : choisissez `local_companion_store.json`.
Bobines (avec calibration), file d'attente, rappels, projets, achats, favoris, statistiques, buses
et imprimante sont copiés dans votre compte. L'application propose ensuite de supprimer le fichier.

## 5. Ce qui a changé

### Données : plus rien en local
Tout est dans Supabase et rattaché au compte : stock, file, entretien, réglages, statistiques,
imprimante, secrets découverts. On retrouve tout en se connectant sur un autre PC. Le seul élément
gardé sur le PC est, si on coche « Rester connecté », le jeton de session dans le **Gestionnaire
d'identification Windows** (chiffré par le système) — jamais le mot de passe.

### Amis
- Chaque compte a un code ami `BMB-XXXX-XXXX` (copiable dans la barre latérale).
- **Envoyer une demande** avec le code → l'autre personne la voit dans Amis › Demandes et
  **accepte ou refuse**. Si les deux s'invitent mutuellement, l'amitié est créée directement.
- On peut annuler une demande envoyée et retirer un ami.
- Une fois amis : stocks visibles, prêts de filament (demande → accepter/refuser), fil
  d'actualité partagé avec likes, podium entre amis.
- Ces règles sont appliquées **dans la base** (fonctions SQL + RLS) : un compte ne peut ni lire
  les données d'un autre, ni s'ajouter en ami sans accord, même en contournant l'application.

### Sécurité corrigée
| Problème de l'ancienne version | Correction |
|---|---|
| Mot de passe, code d'accès, clé Brevo et jetons en clair dans un JSON | Plus aucun secret sur le disque ; session dans le coffre-fort Windows |
| Profils (avec email, IP, série et **code d'accès de l'imprimante**) lisibles par tous les comptes | Profil lisible par soi seul ; imprimante dans une table privée ; amis via des fonctions qui ne renvoient que pseudo, avatar, stats et bobines |
| Ajout d'ami sans accord, recherche avec filtre injectable (`%`, `,`…) | Demande + acceptation, code exact validé, aucune requête construite à partir du texte saisi |
| Connexion par pseudo obligeant à exposer les emails | Connexion par email |
| Contenu des amis inséré tel quel dans la page (injection de code possible, qui aurait pu piloter l'imprimante) | Tout l'affichage échappe les données ; politique CSP sans script en ligne |
| Attributs internes (client MQTT, base…) exposés au JavaScript par PyWebView | Seules les méthodes de l'API sont visibles ; entrées validées (`backend/security.py`) |
| `open_external_url` acceptait n'importe quel lien | Seuls `http(s)` sont ouverts |
| Clé Brevo dans l'application, email envoyé à n'importe quelle adresse | Clé côté serveur, email seulement à l'adresse du compte, 20 par heure |
| Aspiration de jetons Bambu (script injecté dans MakerWorld, lecture des fichiers de Bambu Studio) | Connexion officielle : email + mot de passe, ou code reçu par email (marche pour les comptes Google/Apple) |
| Certificats de l'imprimante jamais vérifiés | Empreinte mémorisée à la 1re connexion (MQTT, FTPS, caméra) et vérifiée avant d'envoyer le code d'accès |
| Fichiers SD : chemins et noms non contrôlés | Noms et chemins nettoyés, pas d'écrasement, taille limitée |

### Interface
Navigation courte (Stock, Imprimante, Slicer Express, Carte SD, File d'attente, Entretien,
Projets & outils, Favoris, Amis, Trophées), icônes dessinées, textes courts, thèmes sombre et clair.
Les réglages sont regroupés (Compte, Imprimante, Alertes, Surveillance, Heures creuses, Slicer, Thème, Données).

### Slicer Express (.stl → impression)
Déposez un `.stl`, `.3mf`, `.obj` ou `.step`, choisissez la qualité (profils officiels, ex.
« 0.20mm Standard ») et la bobine (slot AMS). L'application lance `bambu-studio.exe` en arrière-plan
(`--slice`, sans interface), lit le `.gcode.3mf` produit (grammes, durée), **vérifie le stock du
slot**, puis après confirmation l'envoie en FTPS sur la carte SD et lance l'impression.
Prérequis : Bambu Studio installé et ouvert une fois (pour qu'il télécharge ses profils),
imprimante en mode LAN. Le modèle d'imprimante et la buse se règlent dans Réglages › Slicer.

### Détecteur de bouchage (boîte noire thermique)
Pendant l'impression, l'écart entre `nozzle_temp` et `nozzle_target` (MQTT) est mesuré en continu.
Alerte si trop de mesures sortent de **±4 °C** (oscillation) ou si la buse reste sous la consigne :
début de bouchage partiel, chaussette silicone abîmée, thermistance. Chaque impression arrêtée est
enregistrée avec sa couche et sa hauteur Z ; si plusieurs s'arrêtent **à la même hauteur**, une
alerte signale un probable heat creep ou un point dur sur l'axe Z. La courbe et l'historique sont
dans l'onglet Imprimante ; les seuils dans Réglages › Surveillance.

### Surveillance caméra (remplace l'analyse par IA payante depuis la 3.4)
Voir § 5 quinquies.

### Les secrets de l'atelier
17 easter eggs illustrés (scènes animées, personnages qui traversent l'écran) se débloquent en
utilisant l'application. La collection est dans **Trophées**, avec un indice pour chacun.

## 5 bis. Nouveautés 3.1 (octobre 2026)

**Corrigé**
- Fenêtres (bobine, réglages, publication) : le choix de couleur, le menu « Calibration », la photo de
  profil, la photo d'une publication et les interrupteurs ne réagissaient pas. Un clic dans une fenêtre
  était bloqué par le fond de la fenêtre (`ui/js/core.js`).
- Slicer Express : l'erreur « Failed exporting 3mf files » venait d'un chemin complet passé à
  `--export-3mf`, qui n'accepte qu'un nom de fichier. Si la découpe échoue encore, le journal de
  Bambu Studio s'affiche sous l'erreur.
- Carte SD : un `.gcode` était lancé comme un projet `.3mf`, ce qui figeait l'écran de l'imprimante. Il
  part maintenant avec la commande `gcode_file`. Un `.3mf` non découpé est refusé avant l'envoi. Les
  envois ne restent plus bloqués en fin de transfert, et la taille reçue est vérifiée.

**Ajouté**
- Carte SD : suppression de fichiers (un par un ou par sélection), avec confirmation.
- Compte Bambu Lab : boutons Google, Apple et Facebook. Bambu Lab ne permet pas aux autres applications
  d'utiliser sa page de connexion Google/Apple/Facebook, ces boutons passent donc par un code envoyé à
  l'email du compte.
- Bobines : pastilles des couleurs courantes en plus du sélecteur de teinte.
- Thème orange sur fond noir et nouveau logo : le Graoully, dragon de Metz (`assets/graouli.svg`,
  icône du `.exe` : `assets/graouli.ico`).

## 5 ter. Nouveautés 3.2

- **Assistant de calibration** (icône réglages sur chaque bobine) : facteur K, débit en deux passes et
  débit volumétrique max. Il guide les tests de Bambu Studio (menu Calibration), fait le calcul et
  enregistre le résultat sur la bobine.
- **Notifications sur le téléphone** avec l'application gratuite ntfy : Réglages › Alertes ›
  « Sur le téléphone ». Choisissez un nom de canal secret, abonnez-vous-y dans ntfy, puis testez.
- **Heures creuses** : Réglages › Heures creuses (plage et prix du kWh). Dans Carte SD › Imprimer,
  choisissez « Heures creuses » ou une heure précise. L'application doit rester ouverte : si
  l'imprimante est occupée ou hors ligne, elle réessaie pendant une heure puis annule et prévient.
- **Secrets du Graoully** : tapez GRAOULLY au clavier ; chauffez la buse au-delà de 250 °C ; réussissez
  10 impressions d'affilée pour que saint Clément l'apprivoise (le logo porte alors son étole, jusqu'au
  prochain raté).

## 5 quater. Nouveautés 3.3

- **Connexion avec Google / Apple / compte Bambu : piloter l'imprimante.** Depuis 2025, Bambu Lab
  bloque les ordres (pause, arrêt, lancement, lumière) envoyés par son Cloud depuis une application
  tierce : l'application suivait l'impression mais ne pouvait pas la commander, sans le dire. La 3.3 :
  - vérifie la réponse de l'imprimante à chaque ordre et affiche clairement un refus ;
  - après la connexion au compte Bambu, propose **« Piloter en réseau local (recommandé) »** : elle
    récupère le code d'accès sur le compte et trouve l'imprimante sur la box (ou demande son IP) ;
  - si l'imprimante refuse encore les ordres en réseau local : voir le mode hybride et Bambu Connect
    (3.8.3 et 3.8.4) ; ou « Mode LAN uniquement » puis « Mode développeur », sans Bambu Handy.
  - Réglages › Imprimante › réseau local : bouton « Trouver l'imprimante sur le réseau ».
- **Journal** (menu de gauche) : chaque impression terminée ou ratée s'ajoute avec durée, grammes,
  coût (matière + électricité + usure, prix de Réglages › Devis), matière et photo de fin prise par la
  caméra (rangée sur ce PC dans Téléchargements › Graou3D › journal). Bilan et taux de réussite
  par matière. Les 150 dernières impressions sont gardées.
- **Arrêt d'urgence depuis le téléphone** : Réglages › Alertes › « Arrêt d'urgence ». Pendant une
  impression, les notifications ntfy portent des boutons Pause / Reprendre / Arrêter (l'arrêt demande
  une confirmation, valable 2 minutes). Le PC et l'application doivent rester allumés : ils relaient
  l'ordre (environ 10 secondes). Un bouton ne vaut que pour l'impression en cours.
- **Timelapses** (menu de gauche, réseau local) : vidéos de la carte SD avec miniature ; « Regarder »
  télécharge puis ouvre la vidéo, « Télécharger » la range dans Téléchargements › Graou3D ›
  timelapses, la corbeille la supprime de la carte (après confirmation).
- **Minuteur de séchage** : icône flamme sur chaque bobine. Réglage conseillé par matière (PETG 65 °C
  8 h, TPU 70 °C, PA 80 °C 12 h…), compte à rebours sur la carte, alerte PC + téléphone à la fin, et la
  bobine est marquée comme séchée.
- **MakerWorld** (menu de gauche) : recherche et raccourcis ouvrant MakerWorld dans une fenêtre de
  l'application (ou le navigateur). Les modèles téléchargés (.3mf, .stl…) apparaissent dans « Modèles
  téléchargés » : « Préparer et imprimer » les ouvre dans Slicer Express ; un fichier déjà découpé part
  sur la carte SD. Le bouton « Imprimer » de MakerWorld reste réservé à Bambu Studio et Bambu Handy.
- **27 nouveaux secrets** : Poudlard, Terre du Milieu, Star Wars, Interstellar, Eragon, grands livres
  de SF, Wall-E, Flash McQueen, classiques du cinéma, et la légende du Graoully et de saint Clément
  (mirabelles quand on imprime en jaune, Saint-Clément le 23 novembre, Saint-Nicolas le 6 décembre…).
  Indices dans Trophées › Secrets.

## 5 quinquies. Nouveautés 3.4

- **Plus aucune IA payante.** L'appel à l'API Claude (fonction Supabase `detect-defects`, clé
  `ANTHROPIC_API_KEY`) est supprimé. Publier l'application ne coûte plus rien par analyse.
- **Surveillance caméra 100 % locale** (Réglages › Surveillance, résultat sur la page Imprimante).
  L'analyse se fait sur le PC. Depuis la 3.7, une seule image peut en sortir : la photo jointe à une
  alerte « à surveiller » (niveau 1), envoyée vers votre canal ntfy, et seulement si les notifications
  ntfy sont configurées et que « Notification sur le téléphone avec la photo » est cochée (Réglages ›
  Surveillance). ntfy.sh l'efface au bout de quelques heures. Trois étages :
  1. *Règles* : image trop sombre, caméra masquée ou figée, fils désordonnés qui apparaissent
     (spaghetti possible), changement brutal. Elles alertent, mais ne mettent jamais en pause.
  2. *Apprentissage sur ce PC* : sous chaque analyse, « Tout va bien » ou « Problème » (avec le type).
     Dès 8 réponses de chaque sorte, une régression logistique apprend votre caméra, votre éclairage,
     votre plateau ; la justesse estimée s'affiche. On peut aussi analyser puis marquer des photos
     d'échecs trouvées en ligne pour l'amorcer.
  3. *Modèle de vision* facultatif (`.onnx`, lu par onnxruntime) : il reconnaît les défauts dès la
     première impression. Il s'entraîne gratuitement sur Google Colab avec
     `tools/entrainer_modele_colab.ipynb` (ou `tools/entrainer_modele.py`) à partir du dossier
     « Photos marquées » et d'images publiques **dont la licence permet votre usage** (CC BY 4.0, CC0).
     Évitez les modèles et données sous licence AGPL (par exemple Obico ou Ultralytics YOLO) si
     l'application doit rester fermée et payante. Installation : Réglages › Surveillance ›
     « Installer un modèle », ou `models/model.onnx` + `models/model.json` avant de fabriquer le .exe.
  - **Pause automatique** (désactivée par défaut) : seulement si un modèle (2 ou 3) voit un défaut
    au-dessus de 85 % sur 3 images de suite (réglables ; de 2 à 10 images depuis la 3.7, où elle devient
    le « niveau 2 » de la surveillance). Dès qu'une image est suspecte, l'analyse suivante a lieu
    20 s plus tard pour confirmer vite. Alertes « à surveiller » limitées à une toutes les 10 minutes.
  - **5.5.2, Graouli-Vision v2 (moins de fausses alertes)** : rien n'est jugé sous 1 mm de hauteur
    (`ai.skip_mm`, en plus de `ai.skip_layers`) ; lumière normalisée avant le modèle ; probabilités
    corrigées (`logit_bias` / `temperature` du .json, valeurs par défaut pour un modèle v1) ; les 3
    premières analyses fixent le niveau « normal » de l'impression ; lissage dans le temps ; alerte de
    niveau 1 après 2 images douteuses de suite (`ai.alert_frames`) ; image prise juste après un changement
    de lumière ni jugée ni comptée. Les images des impressions réussies sans alerte sont gardées dans
    `vision\frames_auto\ok` (`ai.collect_ok`, sur ce PC seulement) pour réentraîner le modèle
    (`tools/entrainer_modele.py --mes-images`), et `tools/mesurer_vision.py` compare deux modèles sur vos images.
  - Analyse toutes les 60 s par défaut (réglable dès 20 s), environ 20 ms par image.
  - Données : `%LOCALAPPDATA%\Graou3D\vision` (exemples, photos marquées, modèle).
- **Nouvelles dépendances** : `numpy`, `Pillow`, `onnxruntime` (`pip install -r requirements.txt`).
  Sans elles, l'application marche ; seules la surveillance caméra et les miniatures manquent.
- **Interface épurée** : icônes dans les onglets des réglages, explications rangées derrière une petite
  icône « i » (au survol), étapes en pastilles, nouvel onglet Réglages › Surveillance (caméra + buse).
- **MakerWorld** : miniature de chaque modèle téléchargé à la place du petit cube (image intégrée au
  .3mf, sinon rendu 3D du .stl / .obj / .3mf calculé sur le PC et mis en cache).

## 5 sexies. Nouveautés 3.5 : Graou3D, toutes les imprimantes

- **Nouveau nom : Graou3D.** Votre compte, vos réglages et votre connexion enregistrée sont gardés.
  Les dossiers `Téléchargements\BambuCompanion` et `%LOCALAPPDATA%\BambuCompanion` sont renommés
  `Graou3D` au premier lancement (rien n'est effacé ; s'ils sont verrouillés, l'ancien nom reste utilisé).
- **Toutes les imprimantes** : Bambu Lab (réseau local ou Cloud), Klipper / Moonraker (Creality K1/K2,
  Voron, Elegoo, Sovol, Qidi…), OctoPrint, et maintenant **Prusa (PrusaLink)** : MK4, MK3.9, XL, MINI,
  CORE One (identifiant `maker` + mot de passe affiché sur l'écran de l'imprimante, ou `key:` + clé API).
  La page **Fichiers** (ex-« Carte SD ») liste, envoie, lance et supprime les fichiers de l'imprimante
  pour tous ces modèles. L'interface n'affiche que ce que l'imprimante sait faire (caméra, AMS, lumière,
  commandes manuelles, timelapses). La webcam Klipper / OctoPrint est trouvée automatiquement.
- **Code de l'imprimante dans le coffre-fort Windows**, plus dans Supabase : il est déplacé puis effacé
  de la base au premier lancement.
- **Langues** : français, anglais, allemand, espagnol, italien et chinois (Réglages › Thème › Langue).
  Les traductions sont dans `ui/i18n/<langue>.json` ; après une modification de l'interface :
  `python tools/extraire_textes.py` (liste les textes) puis `python tools/construire_langues.py`.
- **Slicer Express avec OrcaSlicer** (Réglages › Slicer) : en plus de Bambu Studio, Graou3D pilote
  OrcaSlicer installé sur le PC, avec les profils de toutes les marques. Pour Klipper, OctoPrint et
  Prusa, le G-code est extrait et envoyé seul. Les deux slicers restent des programmes séparés (AGPL).
- **Aperçu 3D** : bouton cube à côté d'un modèle (Slicer Express, MakerWorld) et « Voir les couches »
  après la découpe, avec un curseur couche par couche et une couleur par type de trajet.
- **Étiquettes QR / NFC** (Stock › Étiquettes, ou l'icône QR d'une bobine) : un scan au téléphone ouvre
  la fiche de la bobine (poids restant, −10 g / −50 g, emplacement AMS). Mise en place une seule fois :
  « Créer la page » produit un dossier `page-mobile`, à glisser sur https://app.netlify.com/drop (gratuit),
  puis collez l'adresse obtenue. Pour une puce NFC (NTAG215), écrivez le lien de la bobine avec
  l'application gratuite « NFC Tools ». Sur le téléphone, on se connecte avec son compte Graou3D.
- **Réglages partagés par marque** (fenêtre de calibration d'une bobine) : « Partager mes valeurs » envoie
  K, débit et débit max, sans nom. La médiane de la communauté s'affiche dès 3 makers, avec « Appliquer ».
  Demande la migration 002.
- **Photos d'entraînement** : Réglages › Surveillance › « Ajouter des photos » range des images
  (internet, téléphone) dans le dossier de la classe choisie, prêtes pour le notebook Colab.

## 5 septies. Nouveautés 3.6 : partager Graou3D

- **Installateur** (`installer/graou3d.iss`, Inno Setup gratuit) : sans droits administrateur, dans
  `%LOCALAPPDATA%\Programs\Graou3D`, raccourci menu Démarrer (bureau en option), français et anglais.
  La désinstallation ne touche pas aux données de `%LOCALAPPDATA%\Graou3D`.
- **Mises à jour signées** : bandeau « Graou3D x.y.z est disponible », Réglages › À propos ›
  « Rechercher une mise à jour » et « Vérifier automatiquement » (une fois par jour au plus).
- **Signaler un problème** (Réglages › À propos) : email prérempli dans la messagerie de la personne
  et rapport technique nettoyé à joindre (ni email, ni code, ni adresse IP, ni numéro de série).
- **Contrôle des failles** : `build_exe.bat` s'arrête si pip-audit trouve une faille connue.
- **Intro du Graoully refaite** : une seule animation à la fois, dix variantes à l'ouverture et six à la fermeture.
- **Premier lancement** : assistant (langue, compte, imprimante) puis visite guidée ; Réglages › Découvrir
  relance la visite ou ouvre le **mode démo** (fausse imprimante, faux stock, rien n'est envoyé).
- **Ventilateurs et vitesse** (Imprimante › Commandes) : Sieste, Balade, Envol, Furie.
- **Surveillance** : pas de verdict sur un plateau vide, premières couches ignorées, seuil d'alerte réglable.
- **Options activables** (Réglages › Fonctions) : « Assez de filament ? », alertes de stock, devis PDF,
  export comptable, coût réel, mode présentation.
- **Entretien** : rappels conseillés par imprimante, alerte sur le PC et par ntfy.
- **Sauvegarde et restauration** du compte en un fichier (Réglages › Données).

## 5 octies. Nouveautés 3.7 : mode entreprise

- **Mode entreprise** (Réglages › Fonctions) : un interrupteur maître fait apparaître trois options :
  plusieurs imprimantes en même temps (tableau de bord en tête de la vue Imprimante), atelier partagé
  (stock, imprimantes, achats, tâches et rappels partagés entre comptes, rôles gestion / lecture seule ;
  migration 005) et commandes clients (du devis accepté à la livraison, avec la marge réelle).
- **Surveillance à deux niveaux** : alerte ntfy avec boutons « Mettre en pause » / « Ignorer » (photo
  jointe seulement si le canal est robuste et l'option cochée), pause automatique (coupée par défaut).
- **Retirer une pièce** du plateau pendant l'impression (Bambu réseau local avec firmware récent, Klipper
  avec objets marqués).
- **Devis** : 2 ou 3 options, grille de prix par quantité, quincaillerie et finition ; **import
  automatique** du dernier fichier découpé (option, dossier à choisir).
- **Aperçu 3D** : surplombs en rouge, contact plateau, meilleure orientation proposée.
- **Fil allégé** (WebP), **fiches recettes**, **bandeau sur les timelapses** (WebM), **sauvegardes
  automatiques** hebdomadaires (5 gardées), **clé de publication chiffrée** par phrase secrète.
- **Bobines** : 110 matières, 113 marques et un nuancier de 568 couleurs (couleurs nommées, soie, pailleté, fluo, dégradés, RAL, gammes Bambu Lab, Prusament et Polymaker), en listes déroulantes avec saisie libre (3.7.1).

## 5 nonies. Nouveautés 3.8 : Graou3D sur Android

- **Application Android** (APK, Android 8 ou plus récent, téléphones 64 bits) : la même interface et le
  même Python que sur le PC, dans le téléphone. Même compte, mêmes imprimantes, mêmes données.
- **Sur le téléphone** : imprimantes en réseau local (Bambu, Klipper, OctoPrint, PrusaLink) et Bambu Cloud,
  recherche des imprimantes sur la box, caméra, carte SD, stock, file d'attente, devis, amis et fil,
  MakerWorld (dans le navigateur du téléphone). Les PDF et CSV s'ouvrent dans l'application du téléphone ;
  les étiquettes s'impriment par l'impression d'Android (ou « Enregistrer en PDF »).
- **Restent sur le PC** : la découpe (Slicer Express, surveillance du dossier du slicer), l'ajout de photos
  d'entraînement de la surveillance, l'installation automatique des mises à jour. Le téléphone **programme**
  une impression, mais c'est **le PC qui la lance** (jamais deux lancements du même fichier).
- **Secrets** (connexion automatique, codes des imprimantes) chiffrés par le coffre matériel d'Android.
- Graou3D ne veille que **pendant qu'il est ouvert** sur le téléphone : la surveillance continue
  (fin d'impression, bouchage, caméra) reste le travail du PC allumé près de l'imprimante.
- **Corrections 3.8** (PC et téléphone) :
  - **AMS** : les bobines ne disparaissent plus entre deux rapports de l'imprimante (A1, P1), relecture
    complète toutes les 5 minutes et bouton **Rafraîchir** ; un emplacement signale quand la bobine du
    stock n'est plus celle que voit l'AMS ;
  - **recherche** : le texte ne s'écrit plus à l'envers dans les champs de recherche ;
  - **MakerWorld** : la connexion est gardée d'un lancement à l'autre (« Rester connecté », « Se déconnecter ») ;
  - **podium des amis** : les trois premiers de chaque catégorie ; une impression vue par le PC et le
    téléphone ouverts en même temps ne compte plus qu'une fois ;
  - **secrets** : un secret trouvé ne réapparaît plus tout seul (on le revoit dans Trophées) ;
  - **buses** : saisie des heures déjà faites par une buse ;
  - **Trophées et secrets** : actifs d'office, désactivables dans Réglages › Fonctions.
- **Stock 3.8.1** :
  - **tri des bobines** : « à utiliser d'abord » (AMS, puis bobines entamées, la plus vide en premier),
    matière, couleur, restant ou ancienneté ; filtres rapides (dans l'AMS, entamées, neuves, presque vides,
    à sécher) et regroupement par matière, gardés sur l'appareil ;
  - **pièces détachées** (onglet du Stock) : buses, chaussettes, engrenages, courroies… créées par
    l'utilisateur, avec quantité, seuil d'alerte et ajout à la liste d'achats ; comprises dans la sauvegarde ;
  - **grammes par couleur** : à chaque fin d'impression, une fenêtre montre ce qui a été retiré de chaque
    bobine (baisse de l'AMS, sinon détail du fichier découpé, chaque couleur allant à la bobine de l'AMS
    la plus proche) ; on corrige, on ajoute une couleur, et seule la différence est appliquée ;
  - **sauvegarde** : une sauvegarde faite par la 3.8.0 se restaure de nouveau (champs du journal en plus ignorés).
- **Slicer Express multicouleur 3.8.2** (PC) :
  - **couleurs d'un .3mf** : les filaments d'un projet Bambu Studio / MakerWorld sont lus
    (`Metadata/project_settings.config`) ; chaque couleur est proposée sur la bobine de l'AMS de la couleur
    la plus proche (même matière d'abord) et se change dans une liste. La découpe charge un profil filament
    par couleur, et l'envoi passe `ams_mapping` à l'imprimante ;
  - **coloriage** d'un modèle d'une couleur (.stl, .obj, .3mf) dans une fenêtre 3D : palette tirée des
    bobines de l'AMS, **pot de peinture** (toute la face plate cliquée, tolérance d'angle réglable), gomme ;
  - **bandes par hauteur** : « à partir de 12 mm, sur 3 mm, couleur 2 ». Le maillage est coupé exactement à
    ces hauteurs (`backend/multicolor.py`) ;
  - Graou3D écrit un .3mf dont les triangles portent `paint_color` (format de la peinture multicolore de
    Bambu Studio) ; « Enregistrer en .3mf » permet de l'ouvrir dans Bambu Studio pour vérifier ;
  - la vérification du stock se fait couleur par couleur.
- **Mode hybride 3.8.3 et Bambu Connect 3.8.4** (Bambu Lab) : Graou3D se branche sur l'imprimante en réseau
  local **sans** le « Mode LAN uniquement ». L'imprimante reste sur le Cloud et Bambu Handy continue de marcher :
  - Réglages › Imprimante › Bambu Lab (Cloud) : connexion au compte, puis **« Mode hybride : PC + Bambu Handy »**.
    Le code d'accès vient du compte et l'imprimante est cherchée sur la box (ou son IP est demandée) ;
  - suivi complet (températures, progression, AMS), caméra et carte SD ;
  - le firmware 2025 refuse les ordres non signés (code 84033543), même en réseau local : pour lancer une
    impression, Slicer Express › **« Imprimer via Bambu Connect »** (logiciel gratuit de Bambu Lab, à installer
    une fois). Graou3D lui passe le .gcode.3mf ; on choisit l'imprimante et les bobines dans sa fenêtre, puis on lance.
    Une copie du fichier reste dans Téléchargements › Graou3D › bambu-connect ;
  - pause, arrêt et lumière : Bambu Handy ou Bambu Studio. Tout piloter depuis Graou3D demande le
    « Mode LAN uniquement » puis le « Mode développeur », et Bambu Handy ne voit plus l'imprimante.

## 5 decies. Nouveautés 3.9 : factures, URSSAF, messages et le Graoully en F1
Demande la migration 006 (section 3). Sans elle, ces écrans affichent la marche à suivre au lieu de planter.
- **Factures** (mode entreprise, Réglages › Fonctions › Factures) : bouton « Facturer » sur un devis accepté
  ou une commande client, ou facture libre. Numéro F-AAAA-NNNN et date d'émission donnés par la base,
  continus et sans trou ; une facture émise ne se modifie plus, on la corrige par un avoir (total ou partiel).
  Mentions légales du vendeur (SIRET, adresse, IBAN, pénalités, TVA) dans « Mes mentions légales ».
  PDF rangé dans Documents › Graou3D › factures. Suivi des paiements : payée, impayée, en retard.
- **Déclaration URSSAF** (mode entreprise, Réglages › Fonctions) : chiffre d'affaires encaissé par mois ou
  par trimestre, séparé entre ventes d'objets et prestations de services, cotisations estimées, part du
  plafond micro-entreprise, prochaine échéance, export CSV. Les commandes ont un champ « Nature (URSSAF) ».
  C'est une aide au calcul : le montant déclaré reste à vérifier sur autoentrepreneur.urssaf.fr.
- **Messages entre amis** : bouton « Écrire » sur la carte d'un ami, pastille des messages non lus,
  conversation qui se met à jour seule. Seuls les amis acceptés peuvent s'écrire.
- **Le Graoully** : Verstappen, Schumacher et Alonso passent en F1, une pub « qui mieux que le Graoully »
  pendant les longues impressions, et le Graoully s'endort de 22 h à 6 h (un clic le réveille une minute).

## 5 undecies. Nouveautés 4.0 : Conception
Nouvel écran **Conception** (PC seulement, Réglages › Fonctions › Conception). Tout est calculé sur le PC,
sans connexion, avec la bibliothèque libre manifold3d (géométrie exacte). Les fichiers vont dans
Documents › Graou3D › Conception, ou directement dans le Slicer Express (l'impression demande toujours confirmation).
- **Découpe en pièces** : coupes à la main (plan bleu) ou automatiques pour tenir sur le plateau, 8 jonctions
  (droite, goupilles, tenons, aimants visibles ou noyés, clips, queues d'aronde, puzzle, ondulée), numéros
  gravés, pièces posées sur leur face de coupe, notice d'assemblage, vue éclatée.
- **Résistance** : on peint la face tenue et la face qui reçoit la force, on choisit la force et la matière.
  Calcul par éléments finis simplifié (petits cubes), carte des contraintes, coefficient de sécurité et
  comparaison des 3 sens d'impression. C'est une estimation, pas une certification.
- **Texte, logo et QR code** posés sur une face cliquée : en relief, gravés ou incrustés en 2 couleurs (AMS).
- **Textures** : moletage, cannelures, bois, cuir, carbone, nid d'abeille, sablé, briques, sur les faces choisies.
- **Organiseur à partir d'une photo** : outils posés sur une feuille A4 ; Graou3D les détoure et crée le bac
  avec leurs empreintes (taille en multiples de 42 mm possible).
- **Générateurs** : lithophanie, porte-clés et plaques (texte, QR, 2 couleurs), boîtes à couvercle, bacs et
  plaques de base modulaires (grille de 42 mm), vis, écrous, rondelles et tiges filetées ISO.
- **Banque de matériaux** : plus de 50 matières (PLA à PEEK), fiches détaillées et conseiller qui tient compte
  de l'imprimante (enceinte, buse acier trempé) et du stock.
- **Jeu calibré** : un test (barrette à 7 trous et goupille) imprimé une fois par imprimante ; le jeu mesuré
  est gardé dans le compte et utilisé par la découpe, les boîtes, les vis et les couvercles.
- **Messages entre amis** : ils s'effacent 24 h après l'envoi (migration 009). Le bouton « Écrire » s'affiche
  désormais même si la migration 006 manque (la conversation explique alors quoi faire).

## 5 duodecies. Nouveautés 4.1 : peinture au filament
Onglet **Conception › Peinture au filament** (PC). Une photo devient une plaque de 2 à 4 mm imprimée en
changeant de bobine à quelques hauteurs : une couche fine laisse voir la couleur d'en dessous, et l'empilement
reproduit la photo. Graou3D choisit parmi les bobines cochées du stock les meilleures (8 au plus), leur ordre
et les hauteurs de changement, et montre les couleurs attendues. Chaque bobine a une TD (épaisseur à partir de
laquelle elle cache ce qui est dessous), estimée d'après sa couleur et modifiable. Le .3mf peint chaque tranche
de hauteur avec sa bobine et note les couleurs : le Slicer Express associe tout seul chaque couleur à la bonne
bobine de l'AMS (vrai aussi pour les plaques bicolores et le texte incrusté de la 4.0).
La 4.0.1 vide aussi le cache de la fenêtre à chaque nouvelle version (l'ancienne interface pouvait rester affichée).

## 5 terdecies. Nouveautés 4.2 : nouveau menu
Le menu est construit par `ui/js/menu.js` à partir d'une seule liste (groupes Imprimer, Créer, Atelier,
Communauté et Entreprise). Une entrée peut ouvrir plusieurs écrans, montrés en onglets au-dessus de l'écran :
**Matériaux** (Mes bobines, Banque & conseiller, Réglages mesurés, Étiquettes), **Entretien** (avec les pièces
détachées), **Journal** (avec les timelapses), **Modèles** (MakerWorld et Favoris), **Compta & URSSAF**.
- Sélecteur **Perso / Pro** en haut du menu (= `features.enterprise`). Tout ce qui sert à vendre est en Pro :
  devis, commandes, factures, URSSAF, export comptable, coût réel, plusieurs imprimantes, équipe. Un compte
  qui avait activé les devis, l'export comptable ou le coût réel passe en Pro à la mise à jour (`_upgrade`).
- **Accueil** : imprimante, ce qui demande une action, AMS, file d'attente, raccourcis.
- **Modifier le menu** (en bas du menu) : réordonner les entrées et les groupes, masquer une entrée (elle
  reste dans « Plus » et dans la recherche). Profils Débutant / Passionné / Pro au premier lancement et dans
  Réglages › Fonctions, qui devient un catalogue des fonctions rangées par famille.
- **Recherche rapide** (Ctrl+K) : écrans, onglets de Conception et des Réglages, fonctions, bobines, projets,
  file d'attente, favoris.
Le menu est enregistré dans les réglages du compte (`menu`) : pas de migration Supabase.

## 5 quattuordecies. Nouveautés 4.3 : pack Pro et Conception
**Migration Supabase** : exécuter `supabase/migrations/010_graou3d_v43_clients.sql` (table `clients`, une ligne
par fiche, chaque compte ne voit que les siennes). Le planning et l'import des ventes utilisent les colonnes de la
migration `006` (déjà demandée en 3.8). Sans ces migrations, l'écran concerné affiche quoi exécuter.
- **Tableau de bord** (Pro) : chiffre d'affaires encaissé (HT, mêmes règles que l'URSSAF) sur 12 mois, commandes
  en cours et en retard, devis en attente et taux d'acceptation, factures à encaisser, meilleurs clients, ventes
  par canal, résumé du planning (`backend/pro_dashboard.py`).
- **Planning** (Pro) : la file d'attente placée dans le temps, une ligne par imprimante (`backend/planning.py`).
  Durée saisie, sinon estimée d'après les grammes et le rythme réel du journal ; date limite (reprise de la
  commande liée) ; retards prévus ; plage de nuit sans lancement ; ordre changé avec les flèches ou « Ranger par
  date limite ». Rien ne part tout seul.
- **Import des ventes** (écran Commandes) : CSV Etsy (commandes ou articles), Shopify, ou tableau Vinted / fait
  main (colonnes reconnues ou choisies). Une vente expédiée devient une commande payée à sa date de vente ; une
  vente déjà importée n'est jamais ajoutée deux fois (`backend/sales_import.py`).
- **Carnet de clients** (Pro) : fiches (coordonnées, SIREN, TVA, notes) et historique réuni depuis les devis,
  commandes et factures ; un client se choisit dans les formulaires de devis et de commande (`backend/clients.py`).
- **Conception › Réparer** : un fichier qui n'est pas un volume fermé s'ouvre quand même (« À réparer ») ;
  diagnostic (trous, faces retournées, triangles en double, coques à l'envers, morceaux) et réparation
  (`backend/design/repair.py`, petits trous bouchés par triangulation d'aire minimale).
- **Conception › Échelle** : pourcentage, taille voulue sur un axe, remplir le plateau, par axe, raccourcis
  pouces → mm et cm → mm.
- **Conception › Modèles paramétriques** : crochet mural, support de téléphone, vase ou pot, entretoise, équerre,
  clip de câble, bouton, posés dans le sens d'impression le plus solide (`backend/design/parametric.py`). Les
  fichiers OpenSCAD personnalisables (.scad) sont lus et calculés si OpenSCAD (gratuit) est installé.
- **Devis depuis un fichier 3D** (Devis, et bouton « Devis » de Conception) : poids et durée estimés à partir du
  volume et de la surface (`backend/estimate.py`, environ ±20 %), puis un devis prérempli.

## 5 quindecies. Nouveautés 5.0 : Graoully, l'assistant
Une petite tête de dragon qu'on pose où on veut dans l'appli ; un clic ouvre la discussion. Graoully répond
**uniquement** à partir de sa bibliothèque (dossier `graoully/`, 733 sources avec leur texte, 10 074 passages :
fiches Graoully en français, docs Klipper / Marlin / Voron, glossaires OrcaSlicer et Cura, profils de filaments)
et de ce que Graou3D sait déjà, cite ses sources, et dit quand il ne sait pas. Il n'agit jamais sur l'imprimante.
Aucune migration Supabase, aucun serveur : rien ne passe par le compte en ligne.
- **Moteur commun** (`backend/graoully/`) : `library.py` (recherche BM25 sans dépendance, accents ignorés,
  lexique français → anglais pour retrouver les docs anglaises, fiches Graoully d'abord, filtre par famille
  d'imprimante), `coach.py` (réponses calculées : imprimante, impression en cours, prochaine impression de la
  file, bobines, séchage, stock, entretien, journal, et les conseils du coach avant de lancer), `service.py`
  (une question = un travail en arrière-plan, la réponse arrive en flux).
- **Trois cerveaux, même bibliothèque** : *Sans IA* (les passages et les données, avec leurs sources) ; *Sur ce
  PC* (`local_runtime.py` : llama.cpp `llama-server` sur 127.0.0.1 et un modèle GGUF, Ministral 3 3B conseillé
  ou Qwen 3.5 4B ; téléchargés au premier usage dans `%LOCALAPPDATA%\Graou3D\graoully`, sha256 vérifiée,
  reprise si coupure, arrêt après 15 min sans question) ; *Avec votre clé API* (`brains.py` : Mistral, OpenAI,
  OpenRouter, Ollama ou tout service compatible OpenAI, et Claude via le SDK `anthropic`). La clé reste dans le
  coffre-fort de Windows. L'utilisateur paie son fournisseur, Graou3D ne paie rien.
- **Installateur** : case « Installer l'IA locale de Graoully » (décochée) ; si elle est cochée, Graoully
  propose le téléchargement au premier lancement (`installation.ini` dans le dossier des données).
- **Interface** (`ui/js/graoully.js`) : tête déplaçable (position gardée), discussion, renvois [1] cliquables,
  « D'après vos données Graou3D », « Pour aller plus loin » (liens seulement), réglages du cerveau. En mode API,
  Graoully a grandi : cornes, ailes, yeux de dragon, toujours mignon. En mode Sans IA, il porte des lunettes.
- **Compléter la bibliothèque** : `tools/graoully/telecharger_libres.py` puis `construire_corpus.py` (voir
  `graoully/A-LIRE.md`), et recopier `corpus.jsonl` dans `graoully/`.

## 5 sexdecies. 5.1 : audit de fiabilité et de sécurité
Aucune fonction ajoutée ni retirée, réglages repris tels quels, aucune migration Supabase.
- **Fabrication** : `build_exe.bat` ne s'arrête plus sur `test_v37_machines` (relevés Klipper / OctoPrint /
  PrusaLink : l'arrêt attend la fin de la lecture en cours, et une lecture finie après l'arrêt n'est plus publiée).
- **Cerveau local** : clé aléatoire à chaque démarrage de llama-server (aucun autre programme du PC ni page web ne
  peut s'en servir), modèle lancé depuis son dossier (comptes Windows avec accents), llama-server fermé avec
  Graou3D même en cas de plantage (job Windows), fermeture de Graou3D jamais bloquée par un démarrage en cours,
  téléchargement complet mais non vérifié repris sans « erreur 416 ».
- **Clé API** : aucune redirection suivie (la clé ne peut pas partir vers une autre adresse).
- **5.1.1** : l'archive Windows de llama.cpp est cherchée dans les 10 dernières versions (llama.cpp en publie plusieurs
  par jour et les archives Windows arrivent après coup), noms reconnus plus largement, repli sur la version processeur.
- Tests : `tests/test_v51.py`.
- **5.1.2** : jours de fête (`backend/seasons.py`, `ui/js/seasons.js`, bloc « Modes saisons » de `theme.css`). En plus
  d'Halloween et Noël : Pâques (date calculée chaque année), 8 mai, 14 juillet, 11 novembre et Saint-Nicolas. Plus rien
  ne défile : couleurs, accessoire dessiné sur le Graoully (logo et assistant), filet en haut du menu, motif pâle au bas
  du menu, mention sous « Graou3D », message une fois par an. 8 mai et 11 novembre au ton commémoratif. Valeurs de
  `app_config.season` : `auto`, `paques`, `victoire`, `fete_nationale`, `halloween`, `armistice`, `saint_nicolas`, `noel`
  (aucune migration). Tests : `tests/test_v512_fetes.py`.

## 5.1.3 : l'IA locale s'installe toute seule
- llama.cpp publie ses versions bXXXX comme « préversions » sur GitHub (sa « dernière version » officielle est une
  ancienne v0.x sans fichier Windows) : elles sont maintenant acceptées. Si la recherche échoue quand même (limite de
  60 interrogations par heure de GitHub, réseau filtré…), Graou3D prend une version figée et vérifiée (sha256) :
  llama.cpp b11889 et Ministral 3 3B Q4_K_M (`LLAMA_PINNED` et `MODELS[...]["pinned"]` dans `local_runtime.py`).
- Case « Installer l'IA locale » de l'installateur : le téléchargement démarre tout seul au premier lancement.
- Fenêtres « Ouvrir » réparées (filtre refusé par pywebview, « Erreur inattendue ») : llama-server, Bambu Studio /
  OrcaSlicer, ancienne sauvegarde. `tests/test_v513.py` vérifie tous les filtres.

## 5.1.4 : Google Gemini
- Fournisseur « Google Gemini » préréglé dans Graoully (interface compatible OpenAI, modèle `gemini-flash-latest`,
  clé gratuite sur Google AI Studio). Une erreur 404 rappelle le nom du modèle demandé.

## 5.3 : Graoully complet
Aucune migration Supabase : les réservations de bobines sont gardées sur ce PC (`<données>/graoully/reservations.json`).
- **Mode API, Graoully répond à tout** (impression 3D, conception, dimensionnement, matériaux en priorité) : données de
  Graou3D d'abord, puis bibliothèque, puis recherche internet du service (Claude : outil `web_search` ; Gemini : interface
  d'origine avec la recherche Google, clé dans l'en-tête ; OpenRouter : greffon `web`, payant, seulement hors
  bibliothèque). Chaque phrase est sourcée : [n], [Graou3D] ou « Hors bibliothèque : ». Les liens consultés sont listés
  à part (« Sur internet »), les recherches Google sont affichées comme Google le demande. Le mode local reste limité à
  la bibliothèque. `brains.py` (`Extras`, `ask_api`), invite `SYSTEM_API` dans `service.py`.
- **Erreur 500 du mode local corrigée** (« conversation roles must alternate ») : une question restée sans réponse
  laissait deux messages « utilisateur » à la suite. `brains.tidy` remet l'historique en ordre pour tous les services.
- **Pièces jointes** (trombone, 3 fichiers au plus) : fichier 3D (.stl, .3mf, .obj) dans les trois modes, mesuré par
  Graou3D (taille, volume, poids, surplombs, appui, défauts) ; photo (.png, .jpg, .webp) en mode API seulement,
  réduite et sans métadonnées. `graoully/attachments.py`.
- **Créer une pièce** (bouton étincelle, mode API) : pièce technique décrite en JSON de formes (boîtes, cylindres,
  extrusions, textes, différences…) construite par manifold3d, sans exécuter de code ; jusqu'à 3 essais, une relecture
  du rendu par le modèle, contrôle d'imprimabilité, puis ouverture dans Conception. Forme organique (figurine, objet
  d'après photo) avec la clé Meshy de l'utilisateur. `graoully/generate.py`.
- **Graoully qui agit** (toujours avec Confirmer / Annuler) : 12 actions depuis la 5.5.3 (voir plus bas).
  Tout est revérifié en Python. `graoully/actions.py`, `Api.graoully_act`.
- **Diagnostic d'une impression ratée** sur photo (mode API).
- **Profil de départ** par matière et machine (températures, ventilateur, vitesses, précautions), dans les trois modes
  et dans Matériaux › Banque. `graoully/profiles.py`.
- **Résumé du matin** sur l'Accueil et **ruptures prévues** d'après la file d'attente, avec ajout à la liste de courses
  (Matériaux). `backend/forecast.py`. Tests : `tests/test_v53.py`.

## 5.4 : mes données, suppression du compte, support, notifications Windows
**Migration Supabase** : exécuter `supabase/migrations/011_graou3d_v54_suppression_compte.sql` (fonction
`public.delete_my_account`, et les factures deviennent supprimables seulement pendant la suppression de son propre compte).
Sans elle, tout le reste marche ; « Supprimer mon compte » répond que la fonction n'est pas encore activée et ne supprime rien.
Test SQL : `tests/sql/test011_suppression_compte.sql` (base neuve, 001 → 011).
- **Sauvegarde .graou3d version 2** : en plus du reste, clients, devis, commandes, factures et coordonnées du vendeur
  (`backend/business_backup.py`). Restauration : on ajoute seulement ce qui manque, même en « Tout remplacer » ; un devis
  restauré reçoit un nouveau numéro (l'ancien est noté dedans) ; les factures ne sont jamais réimportées (une facture
  émise ne se supprime pas, elle est toujours dans le compte). Les sauvegardes version 1 restent lisibles. 40 Mo au plus.
- **Exporter toutes mes données** (Réglages › Compte) : ZIP dans `Téléchargements\Graou3D\exports` avec
  `mes-donnees.json` (tout), un CSV par rubrique pour Excel, les PDF des factures et devis, et un `LISEZ-MOI.txt`.
  Ni mot de passe, ni jeton, ni code d'imprimante, ni empreinte TLS, ni clé API, ni canal ntfy. `backend/data_export.py`.
- **Fiche client** (Pro) : « Exporter ses données » (sa demande d'accès RGPD) et « Anonymiser » (fiche supprimée, nom
  retiré des devis et commandes ; factures inchangées, obligation de les garder 10 ans).
- **Supprimer mon compte** (Réglages › Compte) : propose l'export, rappelle la conservation des factures, demande le mot
  de passe et le mot SUPPRIMER. Supprime le compte en ligne (toutes les tables suivent en cascade), puis sur ce PC les
  codes d'imprimante, le jeton Bambu Cloud, la session, les sauvegardes automatiques et les réservations de ce compte.
- **Contacter le support** (Réglages › À propos) : toujours visible, adresse intégrée par défaut
  (`config.DEFAULT_SUPPORT_EMAIL`, remplaçable par `GRAOU3D_SUPPORT_EMAIL`). Trois boutons : question, idée, problème
  (seul « problème » prépare le rapport technique nettoyé). Si la messagerie ne s'ouvre pas (Gmail dans le navigateur),
  une fenêtre permet de copier l'adresse, l'objet et le message.
- **Notifications Windows** (Réglages › Alertes › Sur ce PC) : les alertes (fin, échec, bouchage, défaut, séchage,
  entretien, stock) s'affichent aussi dans les notifications de Windows quand Graou3D n'est pas au premier plan.
  Rien à installer (PowerShell et WinRT, sans fenêtre). L'installateur et `main.py` utilisent l'identifiant
  `Graou3D.App`. `backend/desktop_notify.py`.
- **Publier** : `python tools\publier_version.py publier --notes "..."` trouve tout seul `dist\Graou3D-Setup-<version>.exe`
  et explique ce qui manque (numéro sans trois chiffres, Setup absent ou d'une autre version).
Tests : `tests/test_v54.py`.

## 5.5.3 : Graoully assistant (il fait les choses à votre place) et e-mails Graou3D
- **Demandes courantes comprises sans IA**, dans les trois modes (bibliothèque, local, API) : « ajoute une bobine bleue
  en PLA de 1 kg dans mon stock », « j'ai reçu 2 bobines PLA noir et 1 PETG blanc de 750 g », « il reste 300 g sur la
  bobine bleu ciel », « retire 45 g de la PETG noire », « lance le séchage de la PETG noire », « j'ai graissé les axes »,
  « ajoute un rappel pour changer la courroie toutes les 500 h », « ajoute un vase en PETG de 45 g à la file »,
  « marque le vase comme imprimé », « ajoute des inserts M3 à la liste d'achats », « ajoute le client Jean Dupont,
  jean@exemple.fr ». Couleurs du nuancier (accords compris : bleue, noires…), matières et marques du catalogue,
  poids (« 1kg », « un kilo », « 1,5 kg »). `graoully/understand.py`, `graoully/intents.py`.
- **Rien n'est deviné** : si plusieurs bobines, impressions ou rappels correspondent, Graoully les liste avec leur numéro
  et demande ; on répond « #9 » et il reprend la demande. S'il manque la matière, il la demande.
- **12 actions** (`actions.TYPES`) : ajouter des bobines, corriger une bobine (restant, utilisé, matière, couleur,
  marque), minuteur de séchage, bobine séchée, réserver une bobine, ajouter à la file, marquer imprimée ou remettre en
  attente, entretien fait, nouveau rappel, liste d'achats, nouveau client (PC), devis brouillon (PC). Aucune ne
  touche l'imprimante. Jusqu'à 4 cartes par réponse, bouton « Tout confirmer ».
- **Défaire** sous chaque action faite (30 minutes, appli ouverte), sauf « imprimée » (le filament est déjà décompté).
  La même action confirmée deux fois en 20 secondes est refusée (double clic).
- **Réponses exactes sur le stock** : « combien il me reste de bleu ? » additionne toutes les bobines bleues (Bleu ciel,
  Bleu marine, Turquoise…) et les liste ; « bleu marine » ne compte que celles-là. Sans bobine ou sous 250 g, Graoully
  propose l'ajout à la liste d'achats. `graoully/stock_query.py`.
- **Mode API** : une demande courte passe par la lecture sans IA (instantané, gratuit, exact) ; une demande mêlée à une
  question part au modèle, qui reçoit les bobines, la file, les rappels et les clients avec leurs numéros, et ne peut
  proposer que ces 12 actions. Pour un ajout de bobine, les valeurs écrites dans la phrase priment sur celles du modèle.
- **E-mails d'inscription Graou3D** : modèles prêts à coller dans Supabase (`supabase/emails/`, voir son A-LIRE.md) et nom d'expéditeur « Graou3D » dans les réglages SMTP de Supabase.
Tests : `tests/test_v553.py`.

## 5.8.1 : éléments finis sur les cordons de soudure et les appareils à pression (GraouWeld)
- **Moteur 2D** (`backend/weld/fe2d.py`, numpy seul) : éléments Q8 à 8 nœuds (intégration 3 × 3) en déformation plane,
  contrainte plane ou révolution ; maillage par blocs avec resserrement, fusion des nœuds avec liaison choisie (racine
  non soudée = fente) ; renumérotation Cuthill-McKee et Cholesky par bandes ; contraintes lissées aux nœuds, sondes,
  linéarisation à travers l'épaisseur (membrane, flexion, Tresca) ; carte de von Mises en SVG et en PDF vectoriel ;
  export CalculiX (CPE8, CPS8, CAX8) et lecture du `.frd`. Contrôles : barre tirée, tube épais (Lamé), flexion pure ;
  CalculiX 2.21 donne les mêmes déplacements et contraintes (écart < 0,1 %).
- **Cordon en éléments finis** (`backend/weld/fe_joint.py`, onglet « Cordon en éléments finis ») : bout à bout
  (surépaisseur, désalignement), en T, en croix, à recouvrement ; cordons d'angle, pénétration partielle ou complète ;
  effort dans la tôle ou par la platine, extrémités articulées ou encastrées. Contrainte « hot spot » au pied
  (1,67 σ0,4t − 0,67 σ1,0t, IIW et EN 1993-1-9 annexe B), catégories 112 / 100 / 90 (aluminium : IIW FAT 40 / 36),
  fatigue de la racine (36*), contraintes σ⊥ et τ⊥ moyennes sur la gorge et vérification EN 1993-1-8. Contrôles :
  bout à bout arasé = 1,00 ; désalignement 1 mm sur 10 mm = 1,29 contre 1,30 par la formule de l'IIW.
- **Appareil à pression** (`backend/weld/pressure.py`, onglet « Appareil à pression ») : EN 13445-3 (contrainte f par
  famille de matière, coefficient de soudure z, virole, fonds hémisphérique, elliptique 2:1, Klöpper, Korbbogen avec β
  et flambage du raccord, fond plat simplifié), pression maximale admissible, épreuve (EN 13445-5), volume, masse,
  catégorie et modules de la directive 2014/68/UE (indicatifs), rappel de la directive 2014/29/UE. Éléments finis de
  l'appareil entier (révolution, demi-appareil), piquage central facultatif ; contraintes linéarisées de l'annexe C sur
  les lignes clés et balayage de 90 lignes le long du contour. Contrôles : virole loin des fonds = P (Ri + e/2) / e
  exact, sommets des fonds = P R / 2e à 1 %, fond plat = plaque sur appui simple ; maillage convergé à 2 % près.
- Ces deux calculs durent de 0,5 à 3 s et partent sur le bouton **Calculer** ; notes de calcul PDF avec les cartes.
Tests : `tests/test_v581_fe_weld_pressure.py`.

## 5.8.0 : calcul de résistance de niveau pro, GraouWeld (soudage), calibration sans caméra
- **Calcul de résistance (Conception › Résistance)** : cubes à 8 nœuds avec modes incompatibles, multigrille sans
  matrice, matière imprimée anisotrope (parois pleines, dessus et dessous, remplissage homogénéisé selon le motif),
  critères de rupture dans la matière et entre les couches. Précision mesurée sur 7 cas à solution connue
  (`backend/design/fea/bench.py`, affichée dans l'appli) : 5 % d'écart en moyenne.
- **« Trouve-moi le réglage »** (`fea/optimize.py`) : sens d'impression, parois et remplissage les plus légers qui
  tiennent la charge avec la sécurité visée ; le réglage retenu est recalculé pour confirmer.
- **Contre-vérification CalculiX** (`fea/ccx.py`) : export `.inp` (C3D8I, matériaux orthotropes par groupe) ; si
  CalculiX est installé (FreeCAD, PrePoMax), Graou3D le lance et compare déplacement et sécurité. Validé avec
  CalculiX 2.21 : 0,1 % d'écart sur le déplacement.
- **Note de calcul PDF** de chaque étude (`fea/report.py`) et **Calculs rapides** sans modèle (`fea/calculators.py`) :
  clip, poutre, équerre, vis et inserts, charnière souple, paroi sous pression, dent d'engrenage.
- **GraouWeld** (option de Réglages › Fonctions, désactivée par défaut ; `backend/weld/`) : cordons d'angle et bout à
  bout (EN 1993-1-8 méthode directionnelle, EN 1999-1-1 avec zone affectée thermiquement), fatigue (EN 1993-1-9, Miner),
  préchauffage et t8/5 (EN 1011-2), réglages du poste, coût, notes de calcul PDF ; outils d'atelier imprimables
  (jauges de cordon, équerres aimantées, gabarits de chanfrein, cales, gueule de loup en 3D et gabarit papier à
  l'échelle 1). Écran `ui/js/views/weld.js`, API `weld_*`.
- **Pièces envoyées par GraouConception** (`backend/conception_inbox.py`, `ui/js/conception_link.js`) : boîte
  d'arrivée `%LOCALAPPDATA%\Graou3D\echange\arrivee`, carte « Pièce reçue » → Conception ou Slicer Express.
- **Calibration guidée sans caméra** (Stock › bobine › Calibrer) : voir `backend/calibration.py`.
Tests : `tests/test_v580_fem_weld.py`, `tests/test_v580_calibration.py`.

## 5.7.0 : générateur 3D sur cet ordinateur (TripoSR), sans quota
- **`backend/graoully/local3d.py`** : TripoSR (Tripo AI et Stability AI, licence MIT) en ONNX, sur le processeur avec
  onnxruntime (déjà présent pour la caméra). Téléchargement facultatif depuis `fernandotonon/QtMeshEditor-models`
  (révision figée `726f9825…`, puis `main` ; tailles et sha256 vérifiées) : encodeur int8 (416 Mo) ou complet (1,6 Go),
  décodeur (180 Ko), U²-Net pour le détourage (168 Mo). Dossier : `%LOCALAPPDATA%\Graou3D\graoully\local3d`.
- **Chaîne** : détourage (transparence de l'image, sinon U²-Net, sinon couleur du bord) ; cadrage de TripoSR (objet à
  85 %, fond gris 0,5, 512 × 512) ; encodeur → triplan [1, 3, 40, 64, 64] ; décodeur sur une grille n³ du cube
  [-0,87 ; 0,87]³ par paquets de 131 072 points (n = 160, 224 ou 288 : « Détail de la forme ») ; surface densité = 25
  par **tétraèdres** (découpe de Kuhn, vectorisée avec numpy) : fermée et sans fente par construction, orientée vers
  l'extérieur ; manifold3d vérifie, retire les morceaux détachés (< 2 % du volume), simplifie (écart ≤ 1/5 de maille).
  Repère TripoSR : Z vers le haut ; un quart de tour autour de Z met la face vers -Y.
- **Graoully** : « Créer avec › TripoSR · sur cet ordinateur » (photo jointe, sinon image dessinée en ligne d'après la
  description) ; **relais automatique** depuis TRELLIS, Hunyuan3D et TripoSG (`free3d.generate(fallback=…)`) quand le
  quota gratuit est épuisé ou qu'aucun n'aboutit, avec la même image. Réglages : section « 3. Le générateur 3D sur cet
  ordinateur » (installer, annuler, détail, effacer). API : `graoully_local3d_install / status / cancel / detail / remove`.
- Le texte des réglages sur Hugging Face dit maintenant la vraie limite (environ 3 créations par jour avec un compte
  gratuit, essais ratés compris).

## 5.6.2 : refus « trop de questions » de Gemini expliqué
- **La vraie limite de Google** (`brains._gemini_429`) : le refus 429 de Gemini disait seulement « trop de questions
  d'un coup ou quota atteint ». Graou3D lit maintenant les détails que Google joint (QuotaFailure, RetryInfo) et dit :
  quel modèle, quelle limite (par minute, par jour, ou modèle sans quota gratuit, « limit: 0 »), le délai conseillé, et
  la ligne « Quota exceeded for metric… » de Google. La réponse brute est aussi écrite dans le journal.
- **Limite par minute** : si Google demande d'attendre 60 s au plus, Graou3D attend puis réessaie une fois (la création
  de pièce enchaîne plusieurs demandes), et le dit dans la réponse.
- **Quota de la recherche Google** : la question est reposée sans recherche web au lieu d'échouer.

## 5.6.1 : refus « quota épuisé » de Hugging Face expliqué, calculs économisés
- **Limite de calculs des comptes gratuits** : depuis avril 2026, Hugging Face limite un compte gratuit à environ
  3 calculs ZeroGPU par jour (tous Spaces gratuits confondus, essais ratés compris), en plus du temps de calcul. Ce refus
  arrive avec le texte du quota de temps, « 0s left », et un « Try again in 0:00:00 » faux, que Graou3D affichait comme
  « Il revient dans 1 min ». `free3d.quota_message` le reconnaît maintenant (aussi « runs limit ») et dit la vraie règle :
  le compteur repart 24 heures après le premier calcul de la série, visible sur https://huggingface.co/settings/billing.
- **Jeton non reconnu** : si Hugging Face répond « Authenticate with a Hugging Face token » alors qu'un jeton est réglé,
  le message le dit (calcul compté comme visiteur anonyme).
- Le message de Hugging Face est recopié à la fin, pour le diagnostic. `_is_quota` reconnaît aussi « runs limit »,
  « GPU limit » et « You have exceeded », que l'ancien test sur le mot « quota » laissait passer.
- **Image de départ** : Gemini (si une clé est réglée) passe avant les Spaces ZeroGPU, pour garder les calculs du jour à
  l'étape 3D. Ordre : crédit mensuel Hugging Face, Gemini, puis FLUX et les autres Spaces.

## 5.6.0 : création de pièces fiabilisée, logo en relief, revue de l'application
- **Cases « State » de Gradio** (`free3d._state_slots`) : `/info` ne montre pas les entrées cachées d'un Space
  (`gr.State`), mais Gradio attend une valeur à leur place. Sans elle, tous les réglages étaient décalés d'une case et
  TRELLIS (`is_multiimage`) comme Hunyuan3D-2.1 (`caption`) plantaient sans message (« null »). Graou3D lit `/config`
  et remet `None` à ces places, comme le client officiel. Vérifié avec Gradio 5.49 sur des copies fidèles des Spaces.
- **TripoSR retiré** (Space en panne d'installation chez Hugging Face) et remplacé par **TripoSG** (`VAST-AI/TripoSG` :
  `/run_segmentation` puis `/image_to_3d`). Un réglage enregistré « triposr » devient « triposg » (`OLD_ENGINES`).
- **Quota** : le message dit quand il revient (« Try again in 13:42:05 »), qu'il est commun à tous les générateurs, et ne
  conseille le jeton que s'il manque. Un générateur qui réserve 120 s peut être refusé alors qu'un autre (60 s) passe :
  on ne s'arrête que si moins de 30 s restent (`Busy.left`, `Busy.quota`). Un Space lent ou plein ne bloque plus les
  suivants.
- **Image de départ** : avec le jeton, d'abord les **Inference Providers** de Hugging Face (`providers_image` : FLUX par
  hf-inference, Together, Nscale ou fal, sur le crédit gratuit mensuel du compte), qui ne touchent pas au quota de
  calcul gardé pour la 3D ; puis les Spaces, puis Gemini.
- **Erreur sans raison** : le message nomme la fonction du Space qui a planté (`_fail(data, endpoint)`).
- **Barre de progression** pendant la création : étape, pourcentage, temps écoulé, place dans la file d'attente du
  Space (messages `estimation`, `process_starts`, `progress` de Gradio) ; la barre avance seule pendant une étape longue
  (`_phase`, `_shown_progress`). Bouton **Réessayer** après un échec, et liens de recherche du même objet sur
  MakerWorld, Printables et Thingiverse.
- **Gemini et pièces exactes** : en création, 16 000 jetons de réponse (8 192 pour les anciens modèles Gemini, 8 000
  pour les services compatibles OpenAI et Claude, contre 4 096 et 900 avant : la réflexion avec photo épuisait le budget
  et la réponse arrivait vide). Raison d'arrêt du service lue (`Extras.finish` : MAX_TOKENS, length…) et donnée dans le
  message, début de la réponse dans le journal. Lecture du JSON plus tolérante (`generate._first_object`).
- **Logo en relief** (Conception › Générateurs, `generators.logo`) : SVG lu par Graou3D lui-même (`design/svg2d.py` :
  chemins, courbes, arcs, rect, cercle, ellipse, polygone, `use`, transformations ; formes blanches qui creusent), ou
  image PNG / JPEG. Socle qui suit le contour, plaque rectangulaire ou logo découpé, relief en 2e couleur pour l'AMS,
  trou de porte-clés. Aussi dans Graoully : « Créer avec › Image en relief », sans IA, sans quota, sans limite.
- **Revue** : 30 écrans ouverts en français et en anglais dans l'aperçu sans aucune erreur, analyse statique du Python
  (pyflakes) et du JavaScript (eslint), SVG refusé s'il déclare des entités XML.

## 5.5.5 : générateurs 3D gratuits plus patients, et plusieurs façons de dessiner l'image
- **Space qui démarre** (502, 503, 504) : Graou3D lit son état chez Hugging Face (`free3d.space_status`, `runtime.stage`),
  attend jusqu'à environ 2 min 30 s'il démarre (`_patient`, `WAKE_WAITS`), et nomme la panne avec la raison donnée par
  Hugging Face s'il est en panne ou en pause (`BROKEN_STAGES`). Ensuite, le générateur suivant prend le relais.
- **Image de départ** (description sans photo) : FLUX.1-schnell, puis Stable Diffusion 3.5 Large Turbo, puis FLUX.1-dev,
  puis Gemini avec la clé Gemini de l'utilisateur (`gemini_image`, modèles `GEMINI_IMAGE_MODELS`). Si rien n'aboutit,
  le message liste chaque essai et sa cause, et conseille de joindre une photo.
- Les relais (« X n'a pas abouti, essai avec Y ») restent affichés sous la réponse.
- Si l'envoi de la photo (`/upload`) échoue, elle part directement dans la demande (adresse `data:`, acceptée par
  Gradio 4 et 5). Les erreurs HTTP donnent la demande en cause et la raison brute du serveur (`_raw_reason`).

## 5.5.4 : générateurs 3D gratuits réparés
- **Le « 404: Not Found » de TRELLIS** venait de Graou3D : avec une session (`session_hash`), Gradio range les messages
  sous la session, pas sous l'identifiant de l'appel. Graou3D lit maintenant `/queue/data?session_hash=…` pour un Space
  qui ouvre une session (`/start_session`), et `/call/<nom>/<event_id>` sinon, sans session (`Space.session_mode`).
  Test : `test_trellis_session_and_preprocess` simule ce rangement de Gradio.
- Réglages du Graoully › générateur 3D : bouton **Essai complet** (`graoully_hf_test(full=True)`), une vraie génération
  à partir d'une petite image dessinée (environ 1 minute de quota).
- Création de pièce par une IA : délai d'attente de 240 s au lieu de 90 (`brains.longer_timeout`), et le message de
  délai donne la durée.

## 5.5.2 : correctifs après les premiers essais (et Graouli-Vision v2, voir la surveillance caméra)
- **TRELLIS** : il faut ouvrir une session (`/start_session`, même `session_hash` pour tous les appels) et détourer la
  photo (`/preprocess_image`) avant `/generate_and_extract_glb`, sinon le Space échoue sans message.
- Si un générateur gratuit échoue, les autres sont essayés avec la même image (pas en cas de quota).
- Une figurine demandée dans la conversation (« crée-moi un petit dragon… ») propose « Créer cette forme »
  (`makers.organic`) au lieu de partir vers le cerveau.
- Erreurs 500 à 504 d'un service API : message « indisponible pour le moment, réessayez ou changez de service ».
- **« Créer avec »** (barre de création du Graoully) : le service précis qui crée la pièce, retenu sur ce PC :
  TRELLIS, Hunyuan3D, TripoSR (gratuits), Meshy (si clé), puis chaque IA dont la clé est enregistrée, nommée avec son
  modèle (`GraoullyService.creators`, `api_models` dans `reglages.json`). Si l'IA choisie ne répond pas, les autres IA
  enregistrées sont essayées (`_create_exact_any`), en le disant.
- Réglages du Graoully : bloc « Qui fait quoi », état de chaque clé (4 derniers caractères), bouton « Tester » du
  générateur 3D (`graoully_hf_test` : jeton via `whoami-v2`, puis le Space). « Autre service compatible OpenAI » devient
  « Autre service (à votre adresse) » et ses erreurs donnent l'adresse du service.
- Jeton Hugging Face refusé (401/403, ou 404 pendant le calcul) : nouvel essai sans jeton (`free3d.Refused`).
- Pièce courante (vis, boîte…) demandée sans IA disponible, ou en forme libre : le bon générateur de Conception s'ouvre.

## 5.5 : version de lancement
**Migration Supabase** : exécuter `supabase/migrations/012_graou3d_v55_catalogue_recettes.sql` (tables `products`,
`print_recipes` et `legal_documents`, colonne `orders.shipping`). Sans elle, le reste marche ; catalogue, recettes et
expédition répondent qu'il faut l'exécuter. Test SQL : `tests/sql/test012_catalogue.sql` (base neuve, 001 → 012).
- **Générateurs** (Conception › Paramétrique) : engrenage, poulie GT2, charnière imprimée d'un bloc, collier de tube,
  embout de tube, adaptateur de tuyau, passe-câble de bureau, molette pour écrou, pince à sac, patin de pied, plaque
  percée (`backend/design/parametric.py`).
- **Formes libres gratuites** (Graoully › Créer) : TRELLIS, Hunyuan3D ou TripoSR sur Hugging Face, sans clé (environ
  2 min de calcul par jour), 5 min avec un jeton gratuit `hf_…` (coffre-fort Windows). Une description sans photo passe
  d'abord par FLUX.1-schnell. Meshy (payant, clé de l'utilisateur) reste au choix. Un autre Space compatible peut être
  indiqué (réglage avancé). `backend/graoully/free3d.py`.
- **Graoully ouvre les générateurs** : « fais-moi une vis M5x20 », « une boîte de 80 × 60 × 40 » : il propose le bon
  générateur de Conception, valeurs remplies, sans IA (`backend/graoully/makers.py`).
- **Thème** (Réglages › Thème) : sombre, clair ou comme Windows, couleur principale, ambiance, taille du texte, coins.
- **Catalogue et stock** (Pro, Réglages › Fonctions) : articles, prix, fichier, pièces prêtes, seuil de réassort.
  « Réassortir » ajoute à la file d'attente des plateaux marqués `[cat#id]`, comptés tant qu'ils y sont.
- **Expédition** (Commandes, bouton camion) : transporteur, suivi, date, adresse, bon de livraison PDF sans prix
  (`Téléchargements\Graou3D\livraisons`), message « votre colis est parti » à copier.
- **Recettes d'impression** (Journal › Recettes, et bouton « Recette » sous une impression réussie) : relance le même
  fichier et le même plateau sur la bobine de l'AMS de même matière à la couleur la plus proche.
- **Textes juridiques** : dossier `juridique/` (CGU/CGV, confidentialité, mentions légales, sous-traitance), livré avec
  l'application. Pour les changer sans recompiler : modifier les `.md`, lancer `python tools\textes_juridiques.py`,
  coller `juridique\publier.sql` dans Supabase › SQL Editor. Case à cocher à l'inscription ; version acceptée et date
  gardées dans les réglages du compte (`legal`) ; nouvelle acceptation quand la ligne « Version x.y » des CGU ou de la
  politique change. Bouton « ? » (Aide) en haut, et Réglages › À propos. `backend/legal.py`, `ui/js/legal.js`.
- **Graoully en ligne** : avant le premier envoi vers un service (clé API, Hugging Face, Meshy), un message dit où
  partent les questions et les fichiers.
- **Fiabilité** : démarrage plus rapide (connexions HTTPS préparées au premier usage), textes juridiques lus pendant
  l'écran de connexion, sauvegarde .graou3d et export complet avec catalogue et recettes.
Tests : `tests/test_v55.py`.

## 6. Organisation des fichiers

```
Graou3D/
├── main.py                  point d'entrée (fenêtre PyWebView)
├── config.py                constantes, chemins, lecture du .env
├── backend/
│   ├── api.py               méthodes appelées par l'interface
│   ├── print_scheduler.py   impressions programmées (heures creuses) et minuteurs de séchage
│   ├── phone_remote.py      boutons Pause / Arrêter des notifications ntfy
│   ├── security.py          validation de toutes les entrées
│   ├── supabase_rest.py     client Supabase (Auth, base, fonctions)
│   ├── auth_service.py      inscription, connexion, session
│   ├── store_service.py     données du compte (stock, file, réglages…)
│   ├── social_service.py    amis, fil, prêts
│   ├── notify_service.py    alertes (son, interface, email)
│   ├── printer_service.py   imprimantes du compte : configuration, codes, imprimante active
│   ├── printer_conn/        une imprimante connectée (une par imprimante en mode « Plusieurs
│   │                        imprimantes ») : connection, events (fin / échec, journal), watch
│   │                        (surveillance thermique et caméra), commands, files, camera
│   ├── thermal_monitor.py   détecteur de bouchage
│   ├── local_vision.py      surveillance caméra locale (règles, apprentissage, ONNX)
│   ├── model_thumbs.py      miniatures des modèles 3D (MakerWorld)
│   ├── slicer_analyzer.py   analyse .3mf/.gcode + Slicer Express
│   ├── legacy_import.py     import de l'ancien fichier
│   ├── updater.py, ed25519.py  mises à jour signées (GitHub Releases)
│   ├── support.py           « Contacter le support » (journal nettoyé, email prérempli)
│   ├── business_backup.py, data_export.py   5.4 sauvegarde de la gestion, export complet (RGPD)
│   ├── desktop_notify.py    5.4 notifications Windows
│   ├── device.py            PC ou téléphone Android (liens, fichiers, coffre-fort)
│   ├── design/              4.0 Conception : geom, cutter, fem, materials, fit, text2d, relief,
│   │                        texture, organizer, generators, filament_paint (4.1), repair et
│   │                        parametric (4.3), service
│   ├── planning.py, sales_import.py, clients.py, pro_dashboard.py, estimate.py   pack Pro 4.3
│   ├── graoully/            5.0 l'assistant : library, coach, brains, local_runtime, service ; 5.3 attachments, generate, actions, profiles
│   └── printers/            bambu_mqtt, bambu_ftps (SD, timelapses, caméra), bambu_cloud,
│                            bambu_discovery (recherche sur la box), klipper_api, tls_pinning
├── supabase/
│   ├── migrations/001_bambu_companion_v3.sql
│   └── functions/           send-alert
├── ui/
│   ├── index.html           structure uniquement
│   ├── css/theme.css
│   ├── js/core.js, app.js, easter_eggs.js, eggs_art.js, eggs_legends.js
│   ├── js/menu.js, search.js  4.2 : menu (groupes, Perso / Pro, personnalisation), recherche Ctrl+K
│   ├── js/graoully.js       5.0 : l'assistant (tête de dragon, discussion, réglages)
│   ├── js/views/            un fichier par onglet
│   ├── js/android_bridge.js pont vers Python sur Android (ne fait rien sur le PC)
│   └── dev/mock_api.js      fausse API pour l'aperçu navigateur
├── android/                 application Android (Java + Chaquopy), fabriquée par GitHub
├── mobile/android_main.py   démarrage de Python dans l'application Android
├── models/                  modèle de vision livré avec le .exe (facultatif)
├── graoully/                bibliothèque de Graoully (corpus.jsonl, index.json, donnees/, LICENCES/)
├── installer/               graou3d.iss (Inno Setup), A-LIRE-AMI.txt
├── tools/                   entraînement du modèle (Colab), publier_version.py (mises à jour)
└── tests/                 test_backend.py, test_v33.py, test_vision.py, test_thumbs.py
```

## 7. Tests et aperçu

```bash
python -m unittest discover -s tests
```

Aperçu de l'interface sans Python ni imprimante : lancez un serveur dans `ui/`
(`python -m http.server 8791`) puis ouvrez `http://localhost:8791/index.html?dev=1`.

Vérifié pendant la refonte : 28 tests Python ; le script SQL exécuté sur un vrai Postgres
(PGlite) à partir d'une copie de l'ancien schéma, relancé deux fois, avec 46 contrôles d'accès
(amis, prêts, publications, accès anonyme) ; tous les écrans dans le navigateur.
**Pas encore testé en conditions réelles** : connexion à votre projet Supabase, imprimante réelle
(MQTT, FTPS, caméra), ligne de commande de votre version de Bambu Studio, connexion Cloud Bambu Lab,
déploiement des fonctions Edge. Faites un premier essai avec une petite impression.

## 8. Partager Graou3D et publier une mise à jour

Vos amis installent Graou3D avec un installateur et reçoivent ensuite les mises à jour tout seuls.
Les mises à jour passent par GitHub (gratuit) et sont **signées avec une clé que vous seul possédez** :
une version modifiée par quelqu'un d'autre est refusée.

### Une seule fois

1. **Inno Setup 6** (gratuit) : téléchargez-le sur https://jrsoftware.org/isdl.php et installez-le.
2. **Compte GitHub** : créez un compte sur https://github.com, puis un dépôt **public** vide
   (« New repository », par exemple `graou3d-versions`). Il ne contiendra que les versions publiées,
   jamais le code ni le `.env`.
3. **Clé de publication** :
   ```bash
   python tools/publier_version.py generer-cle
   ```
   La clé **privée** est écrite dans `%USERPROFILE%\Graou3D-cle-privee\graou3d-maj.key` (hors du projet).
   **Gardez-la secrète et sauvegardez-la** (clé USB, gestionnaire de mots de passe) : sans elle, vous ne
   pourrez plus publier de mise à jour que les copies installées acceptent. Ne la mettez jamais sur GitHub.
4. Dans le `.env` du projet, ajoutez :
   ```
   GRAOU3D_UPDATE_REPO=votre-compte/graou3d-versions
   GRAOU3D_UPDATE_PUBLIC_KEY=<la clé publique affichée à l'étape 3>
   GRAOU3D_SUPPORT_EMAIL=votre.adresse@exemple.fr   (facultatif depuis la 5.4 : b.hbr57@gmail.com par défaut)
   ```
   Le `.env` ne contient que des valeurs publiques : l'adresse Supabase, la clé **anon**, le dépôt,
   la clé publique et l'adresse de signalement.
   **Le dépôt et la clé publique sont figés dans l'exe** au moment de `build_exe.bat` (fichier
   `update_build.json`, écrit tout seul). Dans l'exe, un `.env` ne peut plus les changer : personne ne
   peut détourner les mises à jour d'un ami en lui donnant un faux `.env`. Si vous changez l'un des deux,
   refabriquez et redonnez l'installateur.

### À chaque version

1. Augmentez `APP_VERSION` dans `config.py` (ex. `3.6.0` → `3.6.1` ; toujours trois nombres).
2. Double-cliquez sur `build_exe.bat` → `dist\Graou3D-Setup-3.6.1.exe`.
   S'il s'arrête sur une **faille connue** : mettez à jour la bibliothèque indiquée
   (`python -m pip install -U <nom>`) et relancez. Pour passer outre en connaissance de cause
   (faille sans rapport avec Graou3D, ou pas encore corrigée) : `build_exe.bat --sans-audit`.
3. Signez :
   ```bash
   python tools/publier_version.py publier --notes "Ce qui change"
   ```
   (Sans chemin, il prend `dist\Graou3D-Setup-<version de config.py>.exe`.)
   Il écrit `latest.json` et `latest.json.sig` dans `dist\` et affiche les étapes suivantes.
4. Sur GitHub : « Releases » › « Draft a new release », étiquette `v3.6.1`, déposez les **trois fichiers**
   (`Graou3D-Setup-3.6.1.exe`, `latest.json`, `latest.json.sig`), laissez « Set as the latest release »
   coché, « Publish release ».

### Ce que voit votre ami

- **Première fois** : vous lui envoyez `Graou3D-Setup-x.y.z.exe` (ou le lien de la Release) et le
  fichier `installer/A-LIRE-AMI.txt`. Windows affiche « Windows a protégé votre PC » (l'application
  n'a pas de certificat payant) : **Informations complémentaires › Exécuter quand même**.
- **Ensuite** : au démarrage (une fois par jour au plus), un bandeau « Graou3D x.y.z est disponible ·
  Voir les nouveautés · Installer ». « Installer » télécharge, vérifie (signature, taille, empreinte),
  demande confirmation, ferme Graou3D et ouvre l'assistant d'installation. Ses données sont conservées.
- **Un souci** : Réglages › À propos › « Contacter le support » › « Signaler un problème » ouvre sa messagerie avec un email
  prérempli vers votre adresse ; il y joint le rapport technique nettoyé (bouton « Ouvrir le dossier »).

## 9. Graou3D sur Android (APK)

L'APK est fabriqué par GitHub (gratuit), rien à installer sur le PC. Le code va dans un dépôt **privé**,
distinct du dépôt public des versions PC (`graou3d-versions`).

### Une seule fois

1. Sur GitHub, créez un dépôt **privé** vide (par exemple `graou3d-source`) et envoyez-y ce dossier
   (le `.gitignore` garde le `.env` et la clé de publication hors du dépôt).
2. Dans ce dépôt : **Settings › Secrets and variables › Actions › New repository secret**, ajoutez
   `SUPABASE_URL` et `SUPABASE_ANON_KEY` (les mêmes valeurs que dans le `.env` ; la clé **anon**, jamais
   une clé `sb_secret_…`, que la fabrication refuse). `GRAOU3D_SUPPORT_EMAIL` est facultatif.

### À chaque envoi

GitHub lance « APK Android » (onglet **Actions**) : tests, puis fabrication (une dizaine de minutes).
L'APK arrive dans la publication **android-essai** du dépôt (onglet **Releases**), nommé
`Graou3D-x.y.z.apk`.

### Installer sur le téléphone

1. Sur le téléphone, ouvrez la publication **android-essai** (connecté à votre compte GitHub, le dépôt
   étant privé) et touchez le fichier `.apk`.
2. Android demande d'autoriser l'installation depuis le navigateur : **Paramètres › Autoriser depuis
   cette source**, puis **Installer**. Play Protect peut afficher « Application inconnue » : **Plus de
   détails › Installer quand même**.
3. Un nouvel APK s'installe par-dessus l'ancien ; les données restent.

**Clé d'essai** : l'APK est signé avec `android/app/graou3d-essai.keystore`, rangée dans le dépôt privé.
Elle suffit pour vos téléphones. Avant de donner l'APK à d'autres personnes, remplacez-la par une vraie
clé gardée en secret GitHub (à préparer ensemble).

**Pas encore vérifié** : la fabrication sur GitHub et l'application sur un vrai téléphone (connexion,
imprimante, caméra, aperçu 3D et photos, qui dépendent de numpy et Pillow compilés pour Android).

## 5.8.2 : fabrication sous Windows
- **Test du refus de redirection** (`tests/test_v51.py`) : le petit serveur de test lit maintenant la requête avant de
  répondre. Sous Windows, une connexion fermée sans lire la requête est coupée (WinError 10053) et la fabrication
  s'arrêtait sur cette erreur.
- **Graoully** : une connexion coupée en cours de réponse (antivirus, pare-feu, réseau) donne un message clair au lieu
  d'une erreur Python, pour OpenAI et compatibles, Anthropic et Google Gemini.

## 5.8.3 : pièce cassée recréée à partir d'une photo
Conception › **Pièce cassée** (`backend/design/photo_repair.py`, `ui/js/views/design_photo.js`).
- **Feuille de repère** à imprimer (PDF A4, bouton dans l'onglet) : 30 pastilles noires à des positions connues, un
  cadre de pose (blanc, ou noir pour les pièces claires) et un trait de contrôle de 100 mm. Si l'imprimante papier a
  réduit la feuille, la longueur lue à la règle corrige l'échelle.
- **Mesure** : les pastilles donnent la transformation feuille → photo au sous-pixel et, avec la focale (EXIF, sinon
  déduite de la perspective), la position du téléphone. Le contour est corrigé de la perspective **et de l'épaisseur**
  (le dessus d'une pièce épaisse paraît plus grand et ses parois se voient en biais : jusqu'à 4 mm d'erreur sur 10 mm
  d'épaisseur sans correction). Sur photos de synthèse : ± 0,15 mm sur le contour et les trous.
- **Trois familles** : plaque (photo de dessus + épaisseur), pièce à étages ou équerre (dessus + profil, le sens du
  profil est trouvé seul), pièce ronde (profil, pièce couchée : enveloppe visuelle des sections, faces d'extrémité et
  épaulements corrigés).
- **Cotes normalisées** : chaque trou rond reçoit des propositions (passage de vis, vis dans le plastique, insert à
  chaud, axe, roulement, aimant) imprimées avec le **jeu calibré** de l'imprimante ; fraisage et lamage ; oblongs.
- **Plan coté** modifiable : longueur, largeur, rayon des angles, symétrie pour une partie manquante, matière ajoutée
  ou enlevée, trous ajoutés d'un clic. **Gabarit d'essai** (tranche de 1,2 mm) avant d'imprimer la pièce.
- **OpenSCAD** : la pièce est construite directement (aperçu 3D, .3mf, .stl, Slicer Express) et son fichier `.scad`
  lisible (toutes les cotes en variables) est enregistré dans le dossier Conception. Mêmes volumes qu'OpenSCAD
  (vérifié par `tests/test_v583_piece_cassee.py` quand OpenSCAD est installé).
- Tout tourne sur le PC, hors ligne, sans nouvelle bibliothèque (numpy, Pillow, manifold3d).

## 5.8.5 : scan 3D par photos
Conception › **Scan 3D** sur le PC, menu **Scan 3D** sur le téléphone (`backend/design/photo_scan.py`,
`scan_service.py`, `scan_link.py`, `ui/js/views/design_scan.js`, `ui/js/views/scan.js`).
- **Prise de vue guidée sur le téléphone** : un anneau montre les angles déjà couverts (24 secteurs × 3 hauteurs,
  d'après la boussole et l'inclinaison), le mode automatique déclenche tous les 12° quand le téléphone est immobile,
  une photo floue est refusée. Chaque photo part aussitôt vers le PC par le **wifi de la maison** : le PC ouvre une
  réception (code à 6 chiffres), le téléphone le trouve tout seul. Ni câble, ni mail, ni internet.
- **Ou des photos déjà prises** (appareil photo, téléphone vidé sur le PC) : bouton « Photos du PC ».
- **Reconstruction sur le PC**, sans carte graphique : COLMAP (pycolmap, gratuit) place les photos ; la profondeur
  vue par chaque photo est calculée par balayage de plans (numpy + Pillow) ; les profondeurs sont fusionnées en un
  volume toujours fermé, base plate au sol (manifold3d). Trois qualités : Brouillon, Normale, Fine. Calcul en
  tâche de fond, annulable ; le téléphone en suit l'avancement.
- **À la bonne taille** si l'objet est posé sur la feuille de repère de la 5.8.3 (les 30 pastilles donnent les mm,
  même si une partie seulement est visible sur chaque photo). Sinon : longueur réelle ou taille voulue à taper
  (voiture, monument…). Rabotage de la base.
- Sur une figurine de synthèse de 71 mm (48 photos) : écart médian 0,18 mm, 90 % de la surface à moins de 0,5 mm,
  9 minutes en qualité Normale sur 4 cœurs. Sur de vraies photos, compter 0,3 à 1 mm : objets brillants, transparents
  ou unis à matifier (bombe mate, talc).
- Le téléphone demande l'accès à la caméra au premier scan (et seulement pour l'interface de Graou3D).

## 5.8.6 : nouvelle identité visuelle

- **Logo officiel** partout : dragon low-poly orange et anthracite, buse d'imprimante, nom « Graou3D » (G et 3 en
  orange), slogan « CONCEVOIR • SIMULER • FABRIQUER. » et liseré bleu-blanc-rouge. Fichiers sources dans `ui/brand/`
  (logo, emblème, texte, version horizontale, chacun en clair et en sombre).
- **Icônes** : `assets/graou3d.ico` (16 à 256 px) pour le `.exe`, l'installateur et la barre des tâches,
  `assets/graou3d.png` pour les notifications, icônes Android (normale et ronde) sur tuile anthracite.
- **Couleurs** de la marque : orange #DA5A04, anthracite #233134, ardoise #425050, acier #96A0A0. Le thème sombre
  passe de noir à anthracite, le thème clair garde un orange plus profond pour rester lisible.
- **Polices** : Exo 2 pour les titres et le nom, Montserrat pour le slogan, intégrées à l'application
  (`ui/fonts`, licence SIL OFL 1.1), donc identiques sur tous les PC, sans internet.
- **Le Graoully** reprend la tête du logo, en plus souriant, avec ses humeurs : éveillé (assistant), clin d'œil,
  réfléchit (fumée aux naseaux), crache sa flamme, endormi. Les tenues du calendrier (Halloween, Noël,
  Saint-Nicolas…) se posent sur la nouvelle tête.
- **Nouvelle cinématique d'ouverture** : la buse trace le contour du dragon, il se remplit couche par couche pendant
  que ses yeux s'allument, la buse repart avec un clin d'œil et des étincelles, puis lueur chaude, yeux ambrés,
  nom et slogan. **Fermeture** : il ferme les yeux et la buse remonte (ou un clin d'œil). Un clic ou une touche passe.
- E-mails Supabase (`supabase/emails`), page mobile des étiquettes et aperçu 3D aux nouvelles couleurs.

## 5.8.7 : scan 3D plus solide (après un vrai essai sur une F1 miniature)
- **Le sol est trouvé même sur une feuille blanche** : avant, l'objet avait bien plus de points que le papier et un
  pan plat de l'objet pouvait passer pour le sol. Les photos placées de travers (sous la feuille) sont écartées.
- **La feuille est reconnue avec moins de pastilles** (6 au lieu de 8, sur deux bords) : leur plan est recalculé à
  partir des pastilles elles-mêmes. Une table à motif (bois, carrelage) ne trompe plus la recherche.
- **Le bas de l'objet est gardé** : sous l'objet et tout autour, le sol n'est plus « deviné », ce qui effaçait les
  pneus noirs et les parties posées au ras de la feuille. Le vide autour de l'objet n'est plus bouché.
- **Lissage automatique** réglable à l'ouverture du modèle (Aucun, Léger, Conseillé, Fort), sans faire maigrir la pièce.
- **Scan en deux faces** : une série pièce posée, une série pièce retournée ; Graou3D recale les deux sur leurs flancs
  et garde le vrai dessus de l'une et le vrai dessous de l'autre.
- **Feuille de repère A3** pour les objets plus grands que le cadre A4, et boutons d'impression dans l'onglet Scan 3D
  (A4, A3, cadre noir pour les objets blancs).
- Téléphone : le tour **au ras de la table** est demandé en premier (c'est lui qui montre le bas de l'objet).
- Bouton « Ouvrir le dossier du scan ». Le panneau ne remonte plus tout seul en haut pendant un calcul.

## 5.8.8 : IA de profondeur pour le scan 3D

- **`backend/design/depth_ai.py`** : Depth Anything V2, taille Small (Apache-2.0, usage commercial permis ; les tailles
  Base et Large sont non commerciales et ne servent pas), en ONNX (fabio-sim/Depth-Anything-ONNX v2.0.0, 95 Mo,
  sha256 vérifiée). Téléchargement facultatif depuis l'onglet Scan 3D (« IA de profondeur », Installer), dans
  `%LOCALAPPDATA%\Graou3D\scan-ia`. Sur le processeur avec onnxruntime : environ 1 s par photo.
- **Recalage** : le relief relatif de l'IA est recalé photo par photo sur les profondeurs confirmées et sur le sol
  (inverse de la profondeur, puis correction douce par cases de 16 px). Sur les photos de la F1 d'Alexis : écart
  médian de 0,25 % avec le mesuré, contre 1,4 % sans la correction locale. Recalage refusé (photo ignorée) si l'écart
  médian dépasse 1 %.
- **Silhouettes votées** : chaque photo classe ses pixels en objet / fond (hauteur du point deviné au-dessus du sol,
  marge de 0,7 % de l'image). Dans le volume, un voxel jamais mesuré est vide si au moins 2 photos le voient sur le
  fond et au moins 30 % de celles qui le voient ; n'importe quel voxel l'est à partir de 3 photos et 50 %. Une photo
  mal placée ne casse donc pas la pièce. C'est ce qui enlève la matière en trop autour des objets unis ou blancs.
- **Le relief deviné ne remplace jamais une mesure** : essayé en complément, il abîmait les roues de la F1 de
  synthèse (99 % → 61 à 93 % de la surface des roues à moins de 1,5 mm). Seules les silhouettes sont gardées : sur la
  F1 de synthèse, résultat identique à la 5.8.7 (précision médiane 0,47 mm) ; sur la F1 d'Alexis, silhouette nette.
- Sans l'IA (non installée ou coupée), le calcul est exactement celui de la 5.8.7.

## 5.8.9 : scans très détaillés ouverts sans erreur

- Un personnage scanné en qualité Normale ou Fine dépassait les 600 000 triangles de l'aperçu : « Résultat trop
  détaillé pour l'aperçu ». À l'ouverture, le modèle est maintenant simplifié par paliers sous 400 000 triangles
  (`photo_scan.lighten` : écart toléré d'un dix-millième de la taille, doublé à chaque palier ; volume inchangé à
  0,01 % près sur l'essai). Le résultat du scan n'est pas recalculé : il suffit de rouvrir le scan.
- **Télécharger le .stl** directement depuis Conception › Scan 3D › 4. Le modèle, sans passer par l'aperçu 3D
  (`scan_export_stl`) : le fichier est écrit dans le dossier du scan avec la taille, le lissage, la base et les
  faces choisis, puis le dossier s'ouvre.
