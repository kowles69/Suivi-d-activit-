"""
fix_annee_historique.py
========================
Correction ponctuelle : certaines lignes de l'historique Tempeau ont une
ANNEE fixee manuellement par table (2024 pour tout "Tempeau2024", 2025 pour
tout "Feuil1"), au lieu d'etre deduite de l'annee reelle contenue dans
"Semaine de l'activite". Consequence : des semaines de fin decembre 2023
se retrouvent avec ANNEE=2024, et des semaines de debut janvier 2026 avec
ANNEE=2025.

Ce script relit chaque ligne de la table Grist, recalcule la vraie annee a
partir du prefixe de "Semaine de l'activite", et corrige (PATCH) uniquement
les lignes ou ca ne correspond pas -- sans toucher au reste.

Usage :
    $env:GRIST_API_KEY="ta_cle"
    python fix_annee_historique.py \
        --server https://grist.grandlyon.fr \
        --doc-id vF8K7SqGxK7X \
        --table-id Tempeau_Historique
    # ajoute --dry-run pour voir ce qui serait corrige sans rien modifier
"""

import argparse
import os
import sys
import requests

CHUNK_SIZE = 500


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def annee_reelle(semaine_activite):
    if not semaine_activite:
        return None
    try:
        return int(str(semaine_activite).split("-")[0])
    except (ValueError, IndexError):
        return None


def main():
    p = argparse.ArgumentParser(description="Corrige les ANNEE mal assignees dans la table Grist, d'apres 'Semaine de l'activité'")
    p.add_argument("--server", required=True)
    p.add_argument("--doc-id", required=True)
    p.add_argument("--table-id", default="Tempeau_Historique")
    p.add_argument("--dry-run", action="store_true", help="Affiche ce qui serait corrige sans rien modifier")
    args = p.parse_args()

    api_key = os.environ.get("GRIST_API_KEY")
    if not api_key:
        print("ERREUR : variable d'environnement GRIST_API_KEY manquante.")
        sys.exit(1)

    base_doc = f"{args.server}/api/docs/{args.doc_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # Vrais identifiants de colonnes
    rcols = requests.get(f"{base_doc}/tables/{args.table_id}/columns", headers=headers)
    rcols.raise_for_status()
    label_vers_id = {c["fields"]["label"]: c["id"] for c in rcols.json()["columns"]}
    id_annee = label_vers_id["ANNEE"]
    id_semaine_act = label_vers_id["Semaine de l'activité"]

    # Toutes les lignes ayant une Semaine de l'activité (donc importees automatiquement,
    # pas les ajouts manuels stagiaires/apprentis qui n'en ont pas)
    r = requests.get(f"{base_doc}/tables/{args.table_id}/records", headers=headers)
    r.raise_for_status()
    records = r.json()["records"]
    print(f"[1/3] {len(records)} lignes au total dans '{args.table_id}'")

    a_corriger = []
    for rec in records:
        semaine = rec["fields"].get(id_semaine_act)
        annee_actuelle = rec["fields"].get(id_annee)
        vraie_annee = annee_reelle(semaine)
        if vraie_annee is not None and vraie_annee != annee_actuelle:
            a_corriger.append({"id": rec["id"], "fields": {id_annee: vraie_annee}})

    print(f"[2/3] {len(a_corriger)} ligne(s) avec une ANNEE incorrecte détectée(s)")
    if not a_corriger:
        print("Rien à corriger.")
        return

    # Petit resume par correction (avant -> apres)
    resume = {}
    for rec in records:
        semaine = rec["fields"].get(id_semaine_act)
        annee_actuelle = rec["fields"].get(id_annee)
        vraie_annee = annee_reelle(semaine)
        if vraie_annee is not None and vraie_annee != annee_actuelle:
            cle = (annee_actuelle, vraie_annee)
            resume[cle] = resume.get(cle, 0) + 1
    for (avant, apres), n in sorted(resume.items()):
        print(f"       ANNEE {avant} -> {apres} : {n} ligne(s)")

    if args.dry_run:
        print("--dry-run actif : aucune modification envoyée à Grist.")
        return

    total = 0
    for chunk in chunked(a_corriger, CHUNK_SIZE):
        payload = {"records": chunk}
        r = requests.patch(f"{base_doc}/tables/{args.table_id}/records", headers=headers, json=payload)
        r.raise_for_status()
        total += len(chunk)
        print(f"[3/3] {total}/{len(a_corriger)} lignes corrigées")

    print("Terminé.")


if __name__ == "__main__":
    sys.exit(main())
