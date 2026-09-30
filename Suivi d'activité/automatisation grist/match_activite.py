"""
match_activite.py
==================
Module partage par transform_tempeau.py et check_activites.py.

Fait la distinction entre deux categories quand un "Type d'activite" brut ne
correspond pas exactement a un intitule connu :

  - FAUTE DE FORME (a corriger automatiquement, sans remonter au chef) :
      * accent manquant/en trop, majuscule/minuscule, espace en trop
      * nom de projet connu (SEMAPLUS, Les Voies Lyonnaises, etc.) utilise
        sans le prefixe "Grands projets - " -- on sait deja ce que c'est,
        on ajoute le prefixe automatiquement
      * faute de frappe mineure sur un intitule par ailleurs tres proche
        d'un intitule connu (au-dela de la simple casse/accent/espace)

  - VRAIE ANOMALIE (a remonter au chef) :
      * rien de suffisamment proche d'un intitule connu -> probablement une
        activite reellement nouvelle ou une erreur de fond, pas une faute
        de forme
"""

import re
import unicodedata
import difflib
import pandas as pd


def lire_fichier(path, sheet_name=0):
    """Lit un CSV ou un Excel (.xlsx/.xls/.xlsm) selon l'extension du fichier.
    Pour Excel, sheet_name choisit l'onglet (0 = premier onglet par defaut)."""
    if path.lower().endswith((".xlsx", ".xls", ".xlsm")):
        return pd.read_excel(path, sheet_name=sheet_name)
    return pd.read_csv(path, encoding="utf-8-sig")


def normalize(s):
    """minuscule, sans accents, espaces multiples reduits a un seul, trim."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def nettoyer(s):
    """Forme propre d'un intitule brut, SANS toucher a la casse ni aux accents :
    forme Unicode composee (NFC), espaces multiples reduits a un seul, trim.
    Garantit que les positions calculees sur normalize(s) correspondent bien
    aux memes positions dans ce texte (necessaire pour decouper au bon endroit)."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFC", s)
    return re.sub(r"\s+", " ", s).strip()


def build_reference(lookup_df):
    """Construit toutes les structures de reconnaissance a partir du lookup
    (colonnes: Type d'activité, Thématique, Sous-thématique)."""
    thematiques = sorted(lookup_df["Thématique"].dropna().unique())
    norm_thematiques = {normalize(t): t for t in thematiques}

    lookup_sans_gp = lookup_df[lookup_df["Thématique"] != "Grands projets"]
    type_act_dict = {
        row["Type d'activité"]: (row["Thématique"], row["Sous-thématique"])
        for _, row in lookup_sans_gp.iterrows()
    }
    norm_type_act = {normalize(k): k for k in type_act_dict}

    gp = lookup_df[lookup_df["Thématique"] == "Grands projets"]
    gp_roots = set()
    for type_act in gp["Type d'activité"].dropna():
        if not type_act.startswith("Grands projets - "):
            gp_roots.add(type_act.split(" - ")[0])
    norm_gp_roots = {normalize(r): r for r in gp_roots}

    # Forme canonique de chaque sous-thematique connue, par thematique :
    # permet de rendre "Diagnostic" meme si l'agent a saisi "diagnostic",
    # pour ne pas creer deux segments differents dans DigDash.
    norm_sous = {}
    for them, sous in zip(lookup_df["Thématique"], lookup_df["Sous-thématique"]):
        if isinstance(them, str) and isinstance(sous, str):
            norm_sous.setdefault((them, normalize(sous)), sous)

    return {
        "norm_thematiques": norm_thematiques,
        "type_act_dict": type_act_dict,
        "norm_type_act": norm_type_act,
        "norm_gp_roots": norm_gp_roots,
        "norm_sous": norm_sous,
    }


def _sous_canonique(ref, them, sous):
    """Renvoie l'ecriture de reference de la sous-thematique si elle est connue
    (a la casse/aux accents/aux espaces pres), sinon la sous-thematique telle quelle."""
    return ref["norm_sous"].get((them, normalize(sous)), sous)


def resoudre_activite(type_act_brut, ref, seuil_fuzzy=0.85):
    """Retourne (thematique, sous_thematique, methode).
    methode vaut None si rien n'a ete trouve (= vraie anomalie a remonter),
    sinon un des libelles ci-dessous (utile pour tracer ce qui a ete
    corrige automatiquement) :
      'prefixe'            -> deja au bon format (eventuellement typo corrigee)
      'lookup'             -> intitule legacy connu (hors Grands projets)
      'grands_projets'     -> nom de projet connu, prefixe ajoute automatiquement
      'fuzzy_lookup'       -> faute de frappe corrigee vers un intitule legacy connu
      'fuzzy_grands_projets' -> faute de frappe corrigee vers un nom de projet connu
    """
    if not isinstance(type_act_brut, str) or not type_act_brut.strip():
        return None, None, None

    brut = nettoyer(type_act_brut)
    norm_brut = normalize(brut)

    # 1. Regle du prefixe exact, tolerante aux accents/casse/espaces
    for norm_t, them in ref["norm_thematiques"].items():
        prefix = norm_t + " - "
        if norm_brut.startswith(prefix):
            reste = brut[len(prefix):].strip()
            return them, _sous_canonique(ref, them, reste), "prefixe"

    # 2. Lookup exact normalise (hors Grands projets)
    if norm_brut in ref["norm_type_act"]:
        cle = ref["norm_type_act"][norm_brut]
        them, sous = ref["type_act_dict"][cle]
        return them, sous, "lookup"

    # 3. Nom de projet Grands projets connu, sans son prefixe
    premier_segment = brut.split(" - ")[0]
    norm_premier = normalize(premier_segment)
    if norm_premier in ref["norm_gp_roots"]:
        racine_canonique = ref["norm_gp_roots"][norm_premier]
        reste = brut[len(premier_segment):]
        sous = (racine_canonique + reste).strip()
        return "Grands projets", _sous_canonique(ref, "Grands projets", sous), "grands_projets"

    # 4. Faute de frappe au-dela de la simple casse/accent/espace
    proche = difflib.get_close_matches(norm_brut, list(ref["norm_type_act"].keys()), n=1, cutoff=seuil_fuzzy)
    if proche:
        cle = ref["norm_type_act"][proche[0]]
        them, sous = ref["type_act_dict"][cle]
        return them, sous, "fuzzy_lookup"

    proche_gp = difflib.get_close_matches(norm_premier, list(ref["norm_gp_roots"].keys()), n=1, cutoff=seuil_fuzzy)
    if proche_gp:
        racine_canonique = ref["norm_gp_roots"][proche_gp[0]]
        reste = brut[len(premier_segment):]
        sous = (racine_canonique + reste).strip()
        return "Grands projets", _sous_canonique(ref, "Grands projets", sous), "fuzzy_grands_projets"

    return None, None, None
