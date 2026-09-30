"""
ajouter_corrections.py
=======================
Ajoute les corrections remplies par Hervé (colonne "Intitulé corrigé" du
fichier produit par check_activites.py) au fichier cumulatif
corrections_activites.csv, utilise ensuite par transform_tempeau.py et
check_activites.py (--corrections).

- Cree corrections_activites.csv s'il n'existe pas encore (premiere fois).
- Les lignes sans "Intitulé corrigé" sont ignorees.
- En cas de doublon sur un meme "Type d'activité", la correction la plus
  recente l'emporte.
- Accepte un fichier rempli par Hervé en .csv (';' ou ',', UTF-8 ou
  reenregistre depuis Excel) ou en .xlsx.

Usage :
    python ajouter_corrections.py a_corriger.csv
    python ajouter_corrections.py a_corriger.csv --cumul corrections_activites.csv
"""

import argparse
import os
import sys
import pandas as pd
from transform_tempeau import lire_corrections


def main():
    p = argparse.ArgumentParser(description="Ajoute les corrections d'Hervé au fichier cumulatif")
    p.add_argument("fichier_herve", help="Fichier rempli par Hervé (colonnes Type d'activité / Intitulé corrigé)")
    p.add_argument("--cumul", default="corrections_activites.csv", help="Fichier cumulatif (defaut: corrections_activites.csv)")
    args = p.parse_args()

    nouvelles = lire_corrections(args.fichier_herve)
    if not nouvelles:
        print(f"Aucune correction remplie dans {args.fichier_herve} : rien à ajouter.")
        return

    existantes = lire_corrections(args.cumul) if os.path.exists(args.cumul) else {}
    remplacees = [k for k in nouvelles if k in existantes and existantes[k] != nouvelles[k]]
    ajoutees = [k for k in nouvelles if k not in existantes]
    cumul = {**existantes, **nouvelles}

    pd.DataFrame({"Type d'activité": list(cumul.keys()), "Intitulé corrigé": list(cumul.values())}) \
        .to_csv(args.cumul, index=False, sep=";", encoding="utf-8-sig")

    print(f"{len(ajoutees)} correction(s) ajoutée(s), {len(remplacees)} remplacée(s) -> {len(cumul)} au total dans {args.cumul}")
    for k in remplacees:
        print(f"  remplacée : '{k}' : '{existantes[k]}' -> '{nouvelles[k]}'")


if __name__ == "__main__":
    sys.exit(main())
