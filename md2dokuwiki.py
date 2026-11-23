#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md2dokuwiki.py : convertit un document Markdown en syntaxe DokuWiki.

Fonctionnement :
	1. Pandoc fait la conversion Markdown -> DokuWiki. Un petit filtre Lua
	   (embarqué dans ce script) remplace les blocs de code par des blocs
	   du plugin CodePrism : <codeprism lang=python> ... </codeprism>.
	   Les options des blocs sont reprises, y compris la syntaxe MkDocs :
	     ```python title="hello.py" hl_lines="2 4-5" linenums="10"
	     ```{.python .line-numbers title="hello.py"}
	2. Le texte obtenu est ensuite « typographié » à la française :
	   - espaces insécables avant « : ; ! ? » et à l'intérieur des guillemets ;
	   - guillemets droits ou courbes remplacés par des guillemets français ;
	   - apostrophes courbes remplacées par des apostrophes droites.
	   Les zones sensibles (code, liens, URL…) sont protégées pendant ce
	   traitement afin de ne jamais être modifiées.

Exemples :
	python3 md2dokuwiki.py --input page.md --output page.txt
	python3 md2dokuwiki.py --input page.md > page.txt
	cat page.md | python3 md2dokuwiki.py > page.txt

Documentation :
	python3 -m pydoc md2dokuwiki
"""

# Annotations de type évaluées à la demande (syntaxe « list[str] » dès Python 3.7)
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Espace insécable (U+00A0). Elle est écrite telle quelle dans le fichier de
# sortie : DokuWiki la conserve et le navigateur l'affiche sans coupure.
NBSP = '\u00a0'

# Délimiteurs des « jetons » qui remplacent temporairement les zones
# protégées (caractères de la zone d'usage privé d'Unicode, jamais utilisés
# dans un texte normal).
TOKEN_START = '\ue000'
TOKEN_END = '\ue001'

# Filtre Pandoc (Lua) : transforme chaque bloc de code en bloc CodePrism.
# Il est écrit dans un fichier temporaire au moment de la conversion.
LUA_FILTER = r'''
-- Correspondance entre les noms de langages Markdown et ceux de Prism.js
-- (à compléter selon vos besoins).
local ALIASES = {
	delphi = "pascal",
	dpr = "pascal",
	pas = "pascal",
	objectpascal = "pascal",
}

-- Attributs CodePrism que l'on peut transmettre depuis le Markdown, par ex. :
--   ```{.python title=hello.py hl=2-4}
local PASSTHROUGH = { "title", "cmd", "cmdout", "css", "user" }

-- Classes qui demandent la numérotation des lignes (Pandoc, Prism.js)
local LINE_NUMBER_CLASSES = { numberLines = true, ["line-numbers"] = true }

-- Écrit « clé=valeur », entre guillemets si la valeur contient des espaces
local function attribute(key, value)
	if value:find("%s") then
		value = '"' .. value:gsub('"', "'") .. '"'
	end
	return " " .. key .. "=" .. value
end

function CodeBlock(block)
	local tag = "<codeprism"
	local attrs = block.attributes
	local numbered = false

	-- La première classe qui n'est pas une option est le langage (```python)
	for _, class in ipairs(block.classes) do
		if LINE_NUMBER_CLASSES[class] then
			numbered = true
		elseif not tag:find(" lang=") then
			tag = tag .. " lang=" .. (ALIASES[class:lower()] or class)
		end
	end

	-- Numéro de la première ligne : startFrom (Pandoc), linenums (MkDocs)
	-- ou sl (CodePrism). Avec linenums="1", la numérotation est activée.
	local start = attrs["sl"] or attrs["startFrom"] or attrs["linenums"]
	if attrs["linenums"] or attrs["el"] == "true" then
		numbered = true
	end
	if numbered then
		tag = tag .. " el=true"
	end
	if start and start:match("^%d+$") and start ~= "1" then
		tag = tag .. " sl=" .. start
	end

	-- Lignes surlignées : hl_lines="2 4-6" (MkDocs) ou hl=2,4-6 (CodePrism).
	-- Les séparateurs (espaces, virgules) sont normalisés en virgules.
	local hl = attrs["hl"] or attrs["hl_lines"]
	if hl then
		hl = hl:gsub("^[%s,]+", ""):gsub("[%s,]+$", ""):gsub("[%s,]+", ",")
		if hl ~= "" then
			tag = tag .. " hl=" .. hl
		end
	end

	-- Autres attributs CodePrism transmis tels quels
	for _, key in ipairs(PASSTHROUGH) do
		local value = attrs[key]
		if value and value ~= "" then
			tag = tag .. attribute(key, value)
		end
	end

	-- Bloc brut : Pandoc l'écrit tel quel dans la sortie DokuWiki
	return pandoc.RawBlock("dokuwiki", tag .. ">\n" .. block.text .. "\n</codeprism>\n")
end
'''

# Zones à ne jamais modifier lors de la typographie (appliquées dans l'ordre).
# Chaque entrée : (expression régulière, drapeaux).
PROTECTED_PATTERNS = [
	# Blocs de code (CodePrism, natifs DokuWiki), HTML/PHP brut et nowiki
	(r'<(codeprism|code|file|html|php|nowiki)\b[^>]*>.*?</\1>', re.DOTALL | re.IGNORECASE),
	# Texte désactivé : %%...%%
	(r'%%.*?%%', re.DOTALL),
	# Code en ligne : ''code''
	(r"''.+?''", 0),
	# Cible des liens : [[cible|libellé]] (on ne protège que la cible)
	(r'(?<=\[\[)[^\]|]+', 0),
	# Cible des images : {{cible|légende}} (idem)
	(r'(?<=\{\{)[^}|]+', 0),
	# Adresses web écrites telles quelles dans le texte
	(r'(?:https?|ftp)://[^\s\ue000\ue001]+', 0),
	# Entités HTML (&amp; etc.) : leur « ; » ne doit pas être touché
	(r'&#?\w+;', 0),
]


# ---------------------------------------------------------------------------
# Protection / restauration des zones sensibles
# ---------------------------------------------------------------------------

def protect(text: str, pattern: str, store: list[str], flags: int | re.RegexFlag) -> str:
	"""Remplace chaque occurrence du motif par un jeton et la mémorise.

	Args:
		text (str): texte à traiter.
		pattern (str): expression régulière des zones à protéger.
		store (list[str]): liste des textes protégés, complétée sur place ; le numéro d'un jeton est l'indice de son texte.
		flags (int | re.RegexFlag): drapeaux de l'expression régulière.

	Returns:
		str: texte où chaque zone protégée est remplacée par un jeton `TOKEN_START` + numéro + `TOKEN_END`.
	"""

	def _replace(match: re.Match[str]) -> str:
		"""Mémorise le texte trouvé et renvoie son jeton.

		Args:
			match (re.Match[str]): zone à protéger.

		Returns:
			str: jeton numéroté qui la remplace.
		"""
		# On garde le texte original et on renvoie un jeton numéroté
		store.append(match.group(0))
		return TOKEN_START + str(len(store) - 1) + TOKEN_END

	return re.sub(pattern, _replace, text, flags = flags)


def restore(text: str, store: list[str]) -> str:
	"""Remet en place les textes d'origine à la place des jetons.

	Args:
		text (str): texte contenant des jetons.
		store (list[str]): textes protégés, remplis par `protect()`.

	Returns:
		str: texte sans aucun jeton.
	"""
	token = re.compile(TOKEN_START + r'(\d+)' + TOKEN_END)

	# Boucle : un fragment protégé peut lui-même contenir un jeton
	while token.search(text):
		text = token.sub(lambda match: store[int(match.group(1))], text)
	return text


# ---------------------------------------------------------------------------
# Typographie française
# ---------------------------------------------------------------------------

def apply_french_typography(text: str, add_missing_spaces: bool) -> str:
	"""Applique les règles typographiques françaises à un texte déjà protégé.

	Args:
		text (str): texte DokuWiki dont les zones sensibles sont remplacées par des jetons.
		add_missing_spaces (bool): `True` pour ajouter l'espace insécable manquante avant « : ; ! ? » (« Bonjour! » -> « Bonjour ! »).

	Returns:
		str: texte typographié.
	"""

	# 1) Apostrophes courbes -> apostrophes droites
	text = re.sub('[\u2019\u2018\u02bc]', "'", text)

	# 2) Guillemets anglais courbes -> guillemets français
	text = re.sub('\u201c[ \t]*', '«' + NBSP, text)
	text = re.sub('[ \t]*\u201d', NBSP + '»', text)

	# 3) Paires de guillemets droits (sur une même ligne) -> guillemets français
	text = re.sub(
		r'"[ \t]*([^"\n]+?)[ \t]*"',
		lambda match: '«' + NBSP + match.group(1) + NBSP + '»',
		text,
	)

	# 4) Espaces insécables à l'intérieur des guillemets français
	#    (existants ou saisis directement dans le Markdown)
	text = re.sub('«[ \t\u00a0\u202f]*', '«' + NBSP, text)
	text = re.sub('(?<=\\S)[ \t\u00a0\u202f]*»', NBSP + '»', text)

	# 5) Espace (même normale) avant « : ; ! ? » -> espace insécable.
	#    Le (?<=\S) évite de toucher à l'indentation des listes DokuWiki.
	text = re.sub(
		r'(?<=\S)[ \t\u202f]+([:;!?])',
		lambda match: NBSP + match.group(1),
		text,
	)

	# 6) (Facultatif) Ajoute l'espace insécable manquante : « Bonjour! » -> « Bonjour ! ».
	#    Heuristique prudente : le signe doit suivre un mot (ou « ) ] ») et être
	#    suivi d'une espace, d'une fin de ligne ou d'un autre signe. Ainsi
	#    « 12:30 », « ns:page » ou « std::vector » ne sont pas modifiés.
	if add_missing_spaces:
		text = re.sub(
			r'(?<=[\w)\]»' + TOKEN_END + r'])([:;!?])(?=[\s*_!?;:\u00a0]|$)',
			lambda match: NBSP + match.group(1),
			text,
			flags = re.MULTILINE,
		)

	return text


def typographize(dokuwiki_text: str, add_missing_spaces: bool) -> str:
	"""Protège les zones sensibles, applique la typographie, puis les restaure.

	Args:
		dokuwiki_text (str): texte DokuWiki produit par Pandoc.
		add_missing_spaces (bool): `True` pour ajouter l'espace insécable manquante avant « : ; ! ? ».

	Returns:
		str: texte DokuWiki typographié (code, liens et URL inchangés).
	"""
	store: list[str] = []
	protected = dokuwiki_text

	for pattern, flags in PROTECTED_PATTERNS:
		protected = protect(protected, pattern, store, flags)

	protected = apply_french_typography(protected, add_missing_spaces)
	return restore(protected, store)


# ---------------------------------------------------------------------------
# Normalisation des blocs de code
# ---------------------------------------------------------------------------

# Ligne d'ouverture ou de fermeture d'un bloc de code délimité
FENCE_LINE = re.compile(r'^([ \t]*)(`{3,}|~{3,})(.*?)\s*$')

# Éléments d'une ligne d'information : clé=valeur (valeur éventuellement
# entre guillemets), .classe, ou mot seul (le langage)
INFO_TOKEN = re.compile(r'''([\w-]+)=("[^"]*"|'[^']*'|\S+)|(\S+)''')


def info_to_pandoc(info: str) -> str:
	"""Convertit une ligne d'information de style MkDocs en attributs Pandoc.

	Exemple : ```python title="hello.py" hl_lines="2 3" .line-numbers```
	devient ```{.python .line-numbers title="hello.py" hl_lines="2 3"}```

	Args:
		info (str): texte qui suit la barrière d'ouverture « ``` ».

	Returns:
		str: attributs Pandoc entre accolades.
	"""
	classes: list[str] = []
	attributes: list[str] = []

	for match in INFO_TOKEN.finditer(info):
		key, value, word = match.groups()
		if key:
			# Retire les guillemets éventuels, puis les remet (sans « " » interne)
			if value[:1] in '"\'' and value[-1:] == value[:1]:
				value = value[1:-1]
			attributes.append(f'{key}="{value.replace(chr(34), chr(39))}"')
		else:
			# « .classe » ou mot seul : dans les deux cas, une classe Pandoc
			classes.append('.' + word.lstrip('.'))

	return '{' + ' '.join(classes + attributes) + '}'


def normalize_code_fences(markdown: str) -> str:
	"""Réécrit les lignes d'ouverture des blocs de code pour Pandoc.

	Pandoc ne reconnaît pas ```python title="x" hl_lines="2" (syntaxe MkDocs) : il n'accepte qu'un mot seul ou des attributs entre accolades. Les blocs déjà écrits avec des accolades ou un mot seul sont laissés tels quels.

	Args:
		markdown (str): document Markdown.

	Returns:
		str: document Markdown dont les barrières d'ouverture sont compréhensibles par Pandoc.
	"""
	lines = markdown.split('\n')
	fence: str | None = None  # Délimiteur du bloc en cours (None hors d'un bloc)

	for index, line in enumerate(lines):
		match = FENCE_LINE.match(line)
		if not match:
			continue
		indent, marker, info = match.groups()

		if fence is None:
			# Ouverture : une barrière « ` » ne peut pas contenir de « ` »
			if marker[0] == '`' and '`' in info:
				continue
			fence = marker
			info = info.strip()
			if info and not info.startswith('{') and (len(info.split()) > 1 or info.startswith('.')):
				lines[index] = indent + marker + info_to_pandoc(info)
		elif marker[0] == fence[0] and len(marker) >= len(fence) and not info.strip():
			# Fermeture : même caractère, au moins aussi long, rien derrière
			fence = None

	return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Appel de Pandoc
# ---------------------------------------------------------------------------

def run_pandoc(markdown: str, pandoc_path: str) -> str:
	"""Convertit le Markdown en DokuWiki avec Pandoc et le filtre Lua.

	Args:
		markdown (str): document Markdown à convertir.
		pandoc_path (str): commande ou chemin de l'exécutable Pandoc.

	Returns:
		str: texte DokuWiki produit par Pandoc (blocs de code en CodePrism).

	Raises:
		RuntimeError: Pandoc s'est terminé en erreur (message de Pandoc joint).
	"""

	# Le filtre Lua doit exister sous forme de fichier : on le crée
	# dans un dossier temporaire supprimé automatiquement.
	with tempfile.TemporaryDirectory() as temp_dir:
		filter_path = Path(temp_dir) / 'codeprism.lua'
		filter_path.write_text(LUA_FILTER, encoding = 'utf-8')

		command = [
			pandoc_path,
			# « -smart » : on garde les guillemets/apostrophes droits tels quels,
			# c'est ce script qui s'occupe de la typographie.
			# « +autolink_bare_uris » : les URL nues deviennent de vrais liens.
			'--from', 'markdown-smart+autolink_bare_uris',
			'--to', 'dokuwiki',
			# Conserve les tabulations (sinon Pandoc les remplace par des espaces,
			# ce qui altère le code des blocs)
			'--preserve-tabs',
			# Pas de retour à la ligne automatique dans les paragraphes
			'--wrap', 'none',
			'--lua-filter', str(filter_path),
		]

		result = subprocess.run(
			command,
			input = normalize_code_fences(markdown),
			capture_output = True,
			text = True,
			encoding = 'utf-8',
			check = False,
		)

	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or 'erreur inconnue de Pandoc')
	return result.stdout


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
	"""Définit et lit les options de la ligne de commande.

	Returns:
		argparse.Namespace: options lues (`input`, `output`, `pandoc`, `skip_typography`, `no_add_missing_spaces`).
	"""
	parser = argparse.ArgumentParser(
		description = 'Convertit un document Markdown en DokuWiki '
		              '(code via CodePrism, typographie française).',
	)
	parser.add_argument(
		'--input',
		metavar = 'FICHIER',
		help = 'fichier Markdown à convertir (entrée standard par défaut)',
	)
	parser.add_argument(
		'--output',
		metavar = 'FICHIER',
		help = 'fichier DokuWiki à écrire (sortie standard par défaut)',
	)
	parser.add_argument(
		'--pandoc',
		metavar = 'CHEMIN',
		default = 'pandoc',
		help = 'commande ou chemin de Pandoc (par défaut : pandoc)',
	)
	parser.add_argument(
		'--skip-typography',
		action = 'store_true',
		help = 'ne pas appliquer les règles de typographie française',
	)
	parser.add_argument(
		'--no-add-missing-spaces',
		action = 'store_true',
		help = "ne pas ajouter l'espace insécable manquante avant « : ; ! ? »",
	)
	return parser.parse_args()


def main() -> int:
	"""Point d'entrée : lit le Markdown, le convertit et écrit le texte DokuWiki.

	Returns:
		int : code de sortie du programme (`0` = succès, `1` = erreur).
	"""
	args = parse_arguments()

	# Force l'UTF-8 sur les flux standard (utile surtout sous Windows)
	for stream in (sys.stdin, sys.stdout, sys.stderr):
		stream.reconfigure(encoding = 'utf-8')

	# Vérifie que Pandoc est bien disponible
	if shutil.which(args.pandoc) is None:
		print(
			'❌ Pandoc est introuvable. Installez-le : https://pandoc.org/installing.html',
			file = sys.stderr,
		)
		return 1

	# Lecture du Markdown
	try:
		if args.input:
			with open(args.input, 'r', encoding = 'utf-8') as source:
				markdown = source.read()
		else:
			markdown = sys.stdin.read()
	except OSError as error:
		print(f'❌ Impossible de lire le fichier : {error}', file = sys.stderr)
		return 1

	print('⏳ Conversion en cours…', file = sys.stderr)

	# Conversion Pandoc, puis typographie
	try:
		dokuwiki = run_pandoc(markdown, args.pandoc)
	except RuntimeError as error:
		print(f'❌ Pandoc a échoué : {error}', file = sys.stderr)
		return 1

	if not args.skip_typography:
		dokuwiki = typographize(
			dokuwiki,
			add_missing_spaces = not args.no_add_missing_spaces,
		)

	# Écriture du résultat (fins de ligne « \n » quel que soit le système)
	if args.output:
		try:
			with open(args.output, 'w', encoding = 'utf-8', newline = '\n') as target:
				target.write(dokuwiki)
		except OSError as error:
			print(f'❌ Impossible d\'écrire le fichier : {error}', file = sys.stderr)
			return 1
		print(f'✅ Conversion terminée → {args.output}', file = sys.stderr)
	else:
		sys.stdout.write(dokuwiki)
		print('✅ Conversion terminée', file = sys.stderr)

	return 0


if __name__ == '__main__':
	sys.exit(main())
