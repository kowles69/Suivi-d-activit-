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

Usage :
    python check_activites.py \
        --raw export_activite_2027.csv \
        --lookup lookup_activite_thematique.csv \
        --unite "EGDD/EAU/PAG/Gestion des patrimoines" \
        --out activites_a_corriger.csv
"""

import argparse
import sys
import pandas as pd
from match_activite import build_reference, resoudre_activite, lire_fichier

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
    args = p.parse_args()

    raw = lire_fichier(args.raw, sheet_name=args.sheet)
    raw = raw[raw["Unité"] == args.unite].copy()

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
        pd.DataFrame(columns=["Type d'activité", "Nb_lignes"]).to_csv(args.out, index=False)
        return

    resume = anomalies.groupby("Type d'activité").size().reset_index(name="Nb_lignes")
    resume = resume.sort_values("Nb_lignes", ascending=False)
    resume.to_csv(args.out, index=False)

    print(f"{len(resume)} 'Type d'activité' réellement inconnu(s), représentant {anomalies.shape[0]} lignes -- À TRANSMETTRE A Hervé :")
    print(f"Liste écrite dans {args.out}")
    print()
    print(resume.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
