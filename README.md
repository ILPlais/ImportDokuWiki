# ImportDokuWiki

Analyse syntaxique de pages Web pour export vers DokuWiki.

Deux scripts Python, utilisables séparément ou enchaînés, permettent de
transformer une page Web en page DokuWiki :

```
page Web ──► web2md.py ──► Markdown ──► md2dokuwiki.py ──► DokuWiki
```

## Ce que font les scripts

### `web2md.py` : page Web → Markdown

- Télécharge la page (`--url`), lit un fichier HTML local (`--input`) ou l'entrée standard.
- Repère le **contenu principal** : sélecteur CSS fourni (`--selector`), sinon balises
  sémantiques (`<article>`, `<main>`…), sinon analyse de la densité de texte.
- Retire les éléments parasites : menus, pieds de page, publicités, commentaires,
  boutons de partage, bandeaux de cookies, etc.
- Conserve les **blocs de code** à l'identique (indentation et tabulations comprises),
  détecte leur langage et supprime les numéros de ligne et boutons « Copier ».
- Conserve les **images, vidéos, sons et contenus intégrés** (iframe) :
  - les liens sont rendus absolus ;
  - l'image originale est retrouvée quand le site sert une version redimensionnée ou
    convertie (`photo-800x750.png.webp` → `photo.png`) ;
  - avec `--download-media`, les médias sont téléchargés et référencés par un chemin relatif.

Les messages d'état sont écrits sur la sortie d'erreur : le Markdown produit peut donc être
redirigé ou envoyé directement à `md2dokuwiki.py`.

### `md2dokuwiki.py` : Markdown → DokuWiki

- Convertit le Markdown en syntaxe DokuWiki avec [Pandoc](https://pandoc.org/).
- Transforme les blocs de code en blocs du plugin
  [CodePrism](https://www.dokuwiki.org/plugin:codeprism) :
  `<codeprism lang=python> … </codeprism>`.
- Applique la **typographie française** :
  - espaces insécables avant `: ; ! ?` et à l'intérieur des guillemets ;
  - guillemets droits ou anglais remplacés par des guillemets français « … » ;
  - apostrophes courbes remplacées par des apostrophes droites.

  Le code, les liens et les URL ne sont jamais modifiés.

## Installation

### Prérequis

- **Python 3.7** ou plus récent ;
- **[Pandoc](https://pandoc.org/installing.html)** (nécessaire uniquement pour
  `md2dokuwiki.py`), accessible dans le `PATH` ou indiqué avec l'option `--pandoc`.

### Étapes

1. Récupérer le dépôt :

   ```bash
   git clone https://github.com/ILPlais/ImportDokuWiki.git
   cd ImportDokuWiki
   ```

2. (Facultatif) Créer un environnement virtuel :

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate      # Linux / macOS
   .venv\Scripts\activate         # Windows
   ```

3. Installer les dépendances Python de `web2md.py` :

   ```bash
   pip install requests beautifulsoup4 markdownify lxml
   ```

   `md2dokuwiki.py` n'utilise que la bibliothèque standard de Python.

4. Installer Pandoc :

   ```bash
   sudo apt install pandoc        # Debian / Ubuntu
   brew install pandoc            # macOS
   winget install JohnMacFarlane.Pandoc   # Windows
   ```

> Sous Windows, remplacez `python3` par `python` (ou `py`) dans les commandes ci-dessous.

## Convertir une page Web en une ligne de commande

Les deux scripts s'enchaînent avec un tube (`|`) :

```bash
python3 web2md.py --url https://exemple.fr/article | python3 md2dokuwiki.py > article.txt
```

Le fichier `article.txt` contient la page au format DokuWiki, prête à être collée dans
l'éditeur de DokuWiki (ou copiée dans le dossier `data/pages/` du wiki).

Variantes utiles :

```bash
# Ajouter l'adresse de la page source en fin de document
python3 web2md.py --url https://exemple.fr/article --add-source | python3 md2dokuwiki.py > article.txt

# Imposer la zone de contenu si la détection automatique se trompe
python3 web2md.py --url https://exemple.fr/article --selector "div.post-body" | python3 md2dokuwiki.py > article.txt

# Garder l'étape intermédiaire en Markdown
python3 web2md.py --url https://exemple.fr/article --output article.md
python3 md2dokuwiki.py --input article.md --output article.txt
```

## Options

### `web2md.py`

| Option | Description |
|---|---|
| `--url URL` | Adresse de la page à convertir |
| `--input FICHIER` | Fichier HTML local (`-` ou rien : entrée standard) |
| `--base-url URL` | Adresse d'origine de la page, pour rendre les liens absolus (utile avec `--input`) |
| `--output FICHIER` | Fichier Markdown à écrire (sortie standard par défaut) |
| `--selector CSS` | Sélecteur CSS du contenu principal |
| `--download-media` | Télécharge les images et aperçus de vidéos, référencés par un chemin relatif |
| `--media-dir DOSSIER` | Dossier des médias (active `--download-media` ; par défaut `<sortie>_media`, ou `media` sans `--output`) |
| `--no-original-images` | Garde les images telles qu'elles sont dans la page, sans chercher l'original |
| `--add-source` | Ajoute l'adresse de la page en fin de document |
| `--timeout SECONDES` | Délai maximal de chaque requête réseau (30 par défaut) |
| `--user-agent TEXTE` | Identifiant du navigateur envoyé aux sites (Firefox par défaut) |

### `md2dokuwiki.py`

| Option | Description |
|---|---|
| `--input FICHIER` | Fichier Markdown à convertir (entrée standard par défaut) |
| `--output FICHIER` | Fichier DokuWiki à écrire (sortie standard par défaut) |
| `--pandoc CHEMIN` | Commande ou chemin de Pandoc (`pandoc` par défaut) |
| `--skip-typography` | N'applique pas les règles de typographie française |
| `--no-add-missing-spaces` | N'ajoute pas l'espace insécable manquante avant `: ; ! ?` |

Chaque script affiche l'aide complète avec `--help`.

## Licence

Voir le fichier [LICENSE](LICENSE).

## Soutenir le projet

| **Cryptos**    | **QR-Codes**                                                                                             |
| :------------- | :------------------------------------------------------------------------------------------------------- |
| **₿ Bitcoin**  | ![QR-code Bitcoin](https://github.com/ILPlais/ILPlais/raw/main/.github/funding/bitcoin.png) <br>`bc1qd8875ezcdzmncl2pe3cek2ymupje5fhddynw36`         |
| **Ξ Ethereum** | ![QR-code Ethereum](https://github.com/ILPlais/ILPlais/raw/main/.github/funding/ethereum.png) <br>`0x771B29bc8dCCdbFa33e0Ad65ec507bdF3fcf05A5`       |
| **💳 PayPal**  | ![QR-code PayPal](https://github.com/ILPlais/ILPlais/raw/main/.github/funding/paypal.png) <br>[paypal.me/LionelPlais](https://paypal.me/LionelPlais) |

Merci ! 🙏
