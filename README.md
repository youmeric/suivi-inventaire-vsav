# Suivi inventaire VSAV

Site web pour suivre l'inventaire des VSAV et les dates de péremption des
médicaments ASUP et du matériel. Il prévient par WhatsApp avant qu'un produit
ne périme.

- **Inventaire de référence chargé au premier démarrage** : VSSUAP ASUP 1
  (novembre 2025), toutes les sections du PDF (sacs, pochettes, tiroirs,
  protection…). Les produits qui périment sont suivis d'office :
  - **médicaments ASUP** : Anapen 150, 300 et 500 µg, Nyxoid, salbutamol 5 et
    2,5 mg, ipratropium 0,5 et 0,25 mg, dosettes de NaCl ;
  - **matériel** : électrodes DSA, pansements hémostatiques et israéliens,
    Brulstop, gel de resucrage, bandelettes du glucomètre, NaCl 50 et 500 ml,
    désinfectant, champs et compresses stériles, kits AES, accouchement et
    membre sectionné, masques FFP2, gel hydroalcoolique, poches de froid.

  Chaque article peut ensuite être modifié (« Suivre la date de péremption »,
  quantité, emplacement), ajouté ou supprimé dans l'onglet Inventaire.
- **Plusieurs véhicules** : Gestion → Véhicules → « Ajouter un véhicule »,
  en copiant la liste d'un véhicule existant (sans les lots) ou en repartant de
  l'inventaire de référence. Le sélecteur en haut passe d'un véhicule à l'autre.
- **Lots et dates** : chaque produit suivi a un ou plusieurs lots (date de
  péremption, n° de lot, quantité). Une date « 06/2027 » compte jusqu'au
  dernier jour du mois, comme sur les boîtes. En ajoutant un nouveau lot, les
  lots périmés ou à remplacer du même produit sont retirés en même temps (case
  cochée d'office). Un retrait garde son motif : périmé, utilisé en
  intervention, erreur de saisie.
- **Scan des boîtes** : le bouton « Scanner une boîte » lit le code DataMatrix
  des boîtes de médicaments (date de péremption, n° de lot, code produit). La
  première fois, on indique de quel produit il s'agit ; ensuite la boîte est
  reconnue toute seule, dans tous les véhicules. Lecteur intégré au navigateur
  sur Android, ZXing (fourni dans `frontend/vendor/zxing`) sur iPhone ; à
  défaut on peut prendre une photo du code. **La caméra ne marche qu'en
  HTTPS.**
- **États** : rouge *périmé*, orange *à remplacer* (sous 30 jours), jaune *à
  surveiller* (sous 90 jours), vert *à jour*, gris *date à saisir*, violet
  *incomplet* (moins de boîtes que prévu). Les deux délais se règlent.
- **Alertes WhatsApp** (Gestion → Alertes WhatsApp) : chaque jour à l'heure
  choisie (8 h par défaut), un seul message récapitule les lots qui viennent
  d'atteindre un palier : **J-30, J-7, le jour même, puis périmé**. Un lot n'est
  signalé qu'une fois par palier, donc pas de rappel tous les jours. Chaque
  lundi, un rappel reprend les lots périmés toujours en place (désactivable).
  Les messages partent **directement par le bot WhatsApp** (wppconnect, son
  `POST /send`, comme le site des calendriers), vers un groupe et/ou les
  personnes cochées « reçoit les alertes ». Si le bot est coupé, rien n'est
  perdu : l'envoi est retenté toutes les heures. Boutons « Envoyer un message
  de test » et « Envoyer l'alerte maintenant », avec l'aperçu du prochain
  message.
- **Vérification de garde** (onglet Vérification) : la liste complète du
  véhicule à cocher, section par section (« Tout cocher »). Ce qui n'est pas
  coché est enregistré comme manquant, avec une remarque libre ; un message
  WhatsApp part si des articles manquent (désactivable). L'accueil affiche la
  dernière vérification (date, nom).
- **À commander** : tous véhicules confondus, les lots périmés ou qui périment
  dans la période choisie (15 à 90 jours) et les quantités incomplètes,
  médicaments en premier. Export CSV pour Excel, à envoyer à la pharmacie.
- **Accès** :
  - le compte **Administrateur** (mot de passe `ADMIN_PASSWORD` du `.env`) a
    tous les droits ;
  - chaque **personne** (Gestion → Personnes) a un **code personnel** de
    8 caractères. « Envoyer l'accès » lui envoie par WhatsApp un lien
    (`/#acces=CODE`) : en l'ouvrant, son téléphone reste connecté 6 mois.
    « Nouveau code » coupe l'ancien accès ;
  - une personne cochée **Gestion** a les mêmes droits que l'administrateur
    (inventaire, personnes, alertes, journal) ; les autres saisissent et
    retirent des lots, scannent et font les vérifications.

  10 essais ratés d'affilée bloquent les connexions pendant 10 minutes.
- **Journal** (Gestion → Journal) : qui a fait quoi et quand (lots ajoutés et
  retirés avec le motif, vérifications, alertes envoyées ou en échec,
  connexions, modifications de l'inventaire et des réglages), avec recherche.
- **Application sur le téléphone** : depuis le navigateur, « Ajouter à l'écran
  d'accueil ». L'application s'ouvre même sans réseau, mais les dates affichées
  viennent toujours du serveur.
- **Sauvegardes** : une copie de la base par jour (30 gardées) dans
  `data/db/sauvegardes/`, téléchargeables depuis Gestion → Sauvegardes.

## Installation sur le serveur (Docker + Nginx Proxy Manager)

Même organisation que le site des calendriers.

```bash
git clone https://github.com/youmeric/suivi-inventaire-vsav.git
cd suivi-inventaire-vsav
cp .env.example .env
nano .env                       # ADMIN_PASSWORD, PROXY_NETWORK, HOST_PORT
docker compose up -d --build
```

Dans **Nginx Proxy Manager** : nouveau *Proxy Host*, par exemple
`inventaire.mondomaine.fr` → *Forward Hostname* `suivi-inventaire-vsav`, port
`8000`, onglet SSL : certificat Let's Encrypt + *Force SSL*. Le HTTPS est
indispensable pour le scan avec la caméra.

Puis, connecté en administrateur, dans **Gestion → Alertes WhatsApp** :

1. **Adresse du bot** vue depuis le conteneur : `http://host.docker.internal:3000`
   si le bot tourne sur la même machine (ou son nom de conteneur s'il est sur le
   réseau `proxy`), et sa **clé API** ;
2. **Groupe WhatsApp** (identifiant `…@g.us`) et/ou des personnes cochées
   « reçoit les alertes » ;
3. **Adresse du site** (`https://inventaire.mondomaine.fr`) pour le lien dans
   les messages et dans les accès envoyés ;
4. « Envoyer un message de test ».

Ensuite, dans **Gestion → Personnes**, ajoutez les équipiers et envoyez-leur leur
accès.

### Mise à jour

```bash
git pull && docker compose up -d --build
```

Les données (`./data/db/`) ne sont pas touchées.

## Développement

```bash
pip install -r backend/requirements.txt pytest httpx
cd backend && ADMIN_PASSWORD=test COOKIE_SECURE=false DB_PATH=../data/db/dev.db uvicorn app.main:app --reload
# tests (depuis la racine)
python -m pytest tests
```

- `backend/app/inventaire.py` : inventaire de référence (ASUP 1, novembre 2025).
- `backend/app/alertes.py` : états des lots, paliers et message WhatsApp.
- `frontend/js/gs1.js` : lecture des codes DataMatrix GS1 et de la caméra.
- `frontend/js/app.js` : l'application (un seul écran, onglets).
