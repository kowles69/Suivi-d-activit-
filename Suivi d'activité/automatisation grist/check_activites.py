"""
check_activites.py
===================
Etape de controle qualite, a lancer AVANT que Hervé ne retravaille l'export
brut Tempeau.

Deux sorties bien distinctes :
  1. Un resume affiche a l'ecran de ce qui a ete corrige automatiquement
     (faute d'accent, de casse, d'espace, ou nom de projet Grands projets
     utilise sans son prefixe) -- juste pour information, rien a faire.
  2. Le fichier --out : uniquement les VRAIES anomalies, c'est-a-dire les
     "Type d'activite" qui ne ressemblent a rien de connu -- celles-la
     seules doivent etre transmises a Hervé pour correction.

Le fichier --out contient une colonne vide "Intitulé corrigé" : Hervé la
remplit (format "Thématique - Sous-thématique", thematique existante ecrite
exactement), puis ses lignes sont ajoutees au fichier cumulatif
corrections_activites.csv, utilise par transform_tempeau.py (--corrections).
Le fichier est ecrit en CSV ';' UTF-8 avec BOM pour s'ouvrir correctement
dans Excel en francais.

Si --corrections est fourni, les corrections deja connues sont appliquees
avant le controle : les intitules deja corriges les annees precedentes ne
sont donc pas renvoyes a Hervé.

Usage :
    python check_activites.py \
        --raw export_activite_2027.csv \
        --lookup lookup_activite_thematique.csv \
        --corrections corrections_activites.csv \
        --out activites_a_corriger.csv
"""

import argparse
import sys
import pandas as pd
from match_activite import build_reference, resoudre_activite, lire_fichier
from transform_tempeau import appliquer_corrections

LIBELLE_METHODE = {
    "prefixe": "deja conforme (eventuelle faute d'accent/casse/espace corrigee)",
    "lookup": "intitule legacy connu",
    "grands_projets": "nom de projet connu, prefixe 'Grands projets' ajoute",
    "fuzzy_lookup": "faute de frappe corrigee (intitule legacy)",
    "fuzzy_grands_projets": "faute de frappe corrigee (nom de projet connu)",
}


def main():
    p = argparse.ArgumentParser(description="Controle qualite des Type d'activite d'un export brut Tempeau")
    p.add_argument("--raw", required=True, help="CSV brut Tempeau (non retravaille)")
    p.add_argument("--lookup", required=True, help="CSV de reference Type d'activite -> Thematique/Sous-thematique")
    p.add_argument("--unite", default="EGDD/EAU/PAG/Gestion des patrimoines")
    p.add_argument("--out", required=True, help="CSV de sortie : uniquement les vraies anomalies a transmettre a Hervé")
    p.add_argument("--sheet", default=0, help="Nom ou numero de l'onglet si --raw est un fichier Excel (defaut: premier onglet)")
    p.add_argument("--corrections", default=None,
                    help="Optionnel : corrections deja validees (corrections_activites.csv), appliquees avant le controle")
    args = p.parse_args()

    raw = lire_fichier(args.raw, sheet_name=args.sheet)
    raw = raw[raw["Unité"] == args.unite].copy()

    if args.corrections:
        raw, n_corr, n_lignes = appliquer_corrections(raw, args.corrections)
        print(f"Corrections déjà validées appliquées : {n_corr} chargée(s), {n_lignes} ligne(s) modifiée(s)")
        print()

    lookup = pd.read_csv(args.lookup)
    ref = build_reference(lookup)

    resultats = raw["Type d'activité"].apply(lambda x: resoudre_activite(x, ref))
    raw["_thematique"] = [r[0] for r in resultats]
    raw["_methode"] = [r[2] for r in resultats]

    # --- 1. Ce qui a ete corrige automatiquement (juste pour info) ---
    corrections = raw[raw["_methode"].isin(["grands_projets", "fuzzy_lookup", "fuzzy_grands_projets"])]
    if len(corrections):
        resume_corr = corrections.groupby(["Type d'activité", "_methode"]).size().reset_index(name="Nb_lignes")
        print(f"{len(resume_corr)} intitulé(s) corrigé(s) automatiquement, rien à faire de ton côté :")
        for _, r in resume_corr.sort_values("Nb_lignes", ascending=False).iterrows():
            type_act = r["Type d'activité"]
            print(f"  - '{type_act}' ({r['Nb_lignes']} lignes) -- {LIBELLE_METHODE[r['_methode']]}")
        print()

    # --- 2. Les vraies anomalies, a transmettre a Hervé ---
    anomalies = raw[raw["_thematique"].isna()]
    if anomalies.empty:
        print("Aucune vraie anomalie détectée. Rien à transmettre a Hervé.")
        pd.DataFrame(columns=["Type d'activité", "Nb_lignes", "Intitulé corrigé"]).to_csv(
            args.out, index=False, sep=";", encoding="utf-8-sig")
        return

    # Regroupe les variantes qui ne different que par des espaces en debut/fin
    # (la correction s'applique de toute facon aux deux)
    anomalies = anomalies.assign(**{"Type d'activité": anomalies["Type d'activité"].map(
        lambda v: v.strip() if isinstance(v, str) else v)})
    resume = anomalies.groupby("Type d'activité").size().reset_index(name="Nb_lignes")
    resume = resume.sort_values("Nb_lignes", ascending=False)
    resume["Intitulé corrigé"] = ""   # colonne a remplir par Hervé
    resume.to_csv(args.out, index=False, sep=";", encoding="utf-8-sig")

    print(f"{len(resume)} 'Type d'activité' réellement inconnu(s), représentant {anomalies.shape[0]} lignes -- À TRANSMETTRE A Hervé :")
    print(f"Liste écrite dans {args.out} (colonne 'Intitulé corrigé' à remplir par Hervé)")
    print()
    print(resume.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
