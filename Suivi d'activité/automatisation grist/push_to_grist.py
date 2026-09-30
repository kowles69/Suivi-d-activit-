"""
push_to_grist.py
=================
Pousse le fichier transforme par transform_tempeau.py dans UNE SEULE table
Grist qui grossit chaque annee (par defaut "Tempeau_Historique"), plutot que
de creer une nouvelle table par annee.

POURQUOI une seule table : DigDash lie son modele de donnees (hierarchies,
jointures, mesures calculees) a une table precise. Avec une table qui change
de nom chaque annee, il faut tout reconstruire cote DigDash a chaque import.
Avec une seule table qui grossit, le modele DigDash se construit UNE FOIS,
et chaque nouvelle annee n'est qu'un rafraichissement de la source -- plus
aucune manipulation cote DigDash.

Comportement (idempotent, rejouable sans creer de doublons) :
  - Si la table n'existe pas encore : elle est creee avec les bonnes colonnes.
  - Les lignes de l'ANNEE presente dans le CSV sont supprimees si elles
    existaient deja (permet de relancer apres correction d'anomalies), puis
    les nouvelles lignes de cette annee sont ajoutees.
  - Les autres annees deja presentes dans la table ne sont jamais touchees.

Pre-requis : variable d'environnement GRIST_API_KEY (Grist > Profil >
Parametres du compte > cle API).

Usage :
    $env:GRIST_API_KEY="ta_cle"          (PowerShell / Windows)
    python push_to_grist.py \
        --csv Tempeau_2027_pret_a_importer.csv \
        --server https://grist.grandlyon.fr \
        --doc-id XXXXXXXXXXXX
    # ANNEE lue automatiquement dans le CSV -> ajoutee/remplacee dans "Tempeau_Historique"
"""

import argparse
import math
import os
import sys
import requests
import pandas as pd

CHUNK_SIZE = 500
TABLE_PAR_DEFAUT = "Tempeau_Historique"

TYPES_COLONNES = {
    "Semaine de l'activité": "Text",
    "Agent": "Text",
    "Unité": "Text",
    "Thématique": "Text",
    "Sous-thématique": "Text",
    "Type d'activité": "Text",
    "Projet": "Text",
    "Famille": "Text",
    "Thématiques": "Text",
    "Durée": "Numeric",
    "Coût Horaire": "Numeric",
    "Coût Total": "Numeric",
    "Bénéficiaires": "Text",
    "Lieux": "Text",
    "Commentaire": "Text",
    "ANNEE": "Int",
    "SEMAINE": "Int",
    "MOIS": "Int",
    "Agent_anonyme": "Text",
    "Quotite": "Numeric",
    "ETP": "Numeric",
    "Statut": "Text",
}


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def nettoyer_valeur(v):
    """Remplace NaN/Infinity/-Infinity (non representables en JSON strict) par None.
    Verifie chaque valeur individuellement, quel que soit son type d'origine
    (float, str "nan"/"inf" residuel, etc.), pour etre robuste aux variations
    de version de pandas entre environnements."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, str) and v.strip().lower() in ("nan", "inf", "-inf", "infinity", "-infinity"):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def main():
    p = argparse.ArgumentParser(description="Alimente la table Grist historique unique a partir d'un CSV transforme")
    p.add_argument("--csv", required=True, help="CSV genere par transform_tempeau.py")
    p.add_argument("--server", required=True, help="URL de base de ton instance Grist")
    p.add_argument("--doc-id", required=True, help="ID du document Grist (visible dans l'URL)")
    p.add_argument("--table-id", default=TABLE_PAR_DEFAUT, help=f"Nom de la table unique cible (defaut: {TABLE_PAR_DEFAUT})")
    p.add_argument("--dry-run", action="store_true", help="Simule sans rien modifier dans Grist")
    args = p.parse_args()

    api_key = os.environ.get("GRIST_API_KEY")
    if not api_key:
        print("ERREUR : variable d'environnement GRIST_API_KEY manquante.")
        print("  $env:GRIST_API_KEY=\"ta_cle\"  (recuperable dans Grist > Profil > cle API)")
        sys.exit(1)

    df = pd.read_csv(args.csv)
    if "ANNEE" not in df.columns:
        print("ERREUR : le CSV ne contient pas de colonne ANNEE (il doit venir de transform_tempeau.py).")
        sys.exit(1)

    # Ecarter les lignes fantomes (ANNEE vide ou 0 -- lignes vides/mal remplies)
    avant = len(df)
    df = df[df["ANNEE"].notna() & (df["ANNEE"] != 0)].copy()
    if len(df) != avant:
        print(f"[0/5] {avant - len(df)} ligne(s) avec ANNEE vide ou à 0 écartée(s) (probablement des lignes vides)")

    annees = sorted(int(a) for a in df["ANNEE"].unique())
    table_id = args.table_id

    base_doc = f"{args.server}/api/docs/{args.doc_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # Neutraliser les valeurs non representables en JSON : NaN ET +/-inf
    # (ex: ETP = Durée / Heures_attendues quand Heures_attendues vaut 0)
    df = df.replace([float("inf"), float("-inf")], pd.NA)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")
    print(f"[1/5] Années {annees} lues dans le CSV -> table cible unique '{table_id}' -- {len(records)} lignes a inserer")

    if args.dry_run:
        print("--dry-run actif : aucune modification envoyee a Grist. Fin.")
        return

    # --- 1. La table existe-t-elle deja ? ---
    resp = requests.get(f"{base_doc}/tables", headers=headers)
    resp.raise_for_status()
    tables_existantes = [t["id"] for t in resp.json()["tables"]]

    if table_id not in tables_existantes:
        colonnes = [
            {"id": col, "fields": {"label": col, "type": TYPES_COLONNES.get(col, "Text")}}
            for col in df.columns
        ]
        payload = {"tables": [{"id": table_id, "columns": colonnes}]}
        r = requests.post(f"{base_doc}/tables", headers=headers, json=payload)
        r.raise_for_status()
        print(f"[2/5] Table '{table_id}' créée avec {len(colonnes)} colonnes (première utilisation)")
    else:
        print(f"[2/5] Table '{table_id}' déjà existante, réutilisée telle quelle")

    # --- 2. Vrais identifiants de colonnes (Grist assainit accents/espaces/apostrophes) ---
    rcols = requests.get(f"{base_doc}/tables/{table_id}/columns", headers=headers)
    rcols.raise_for_status()
    label_vers_id = {c["fields"]["label"]: c["id"] for c in rcols.json()["columns"]}
    colonnes_manquantes = [col for col in df.columns if col not in label_vers_id]
    if colonnes_manquantes:
        print(f"ERREUR : colonnes non retrouvées dans la table Grist : {colonnes_manquantes}")
        sys.exit(1)
    id_annee = label_vers_id["ANNEE"]
    id_semaine_act = label_vers_id["Semaine de l'activité"]

    # --- 3. Supprimer UNIQUEMENT les lignes importées automatiquement des années
    # concernées par ce fichier (peut en couvrir plusieurs, ex: semaines de fin
    # d'année precedente / debut d'annee suivante dans le meme export).
    # On ne touche pas aux lignes sans "Semaine de l'activité" : ce sont celles
    # ajoutees a la main pour les stagiaires/apprentis (qui ne remplissent pas
    # Tempeau), elles doivent survivre a un reimport. ---
    total_supprimees, total_preservees = 0, 0
    for annee in annees:
        filtre = f'{{"{id_annee}":[{annee}]}}'
        r = requests.get(f"{base_doc}/tables/{table_id}/records", headers=headers, params={"filter": filtre})
        r.raise_for_status()
        tous_les_ids = r.json()["records"]
        ids_existants = [rec["id"] for rec in tous_les_ids if rec["fields"].get(id_semaine_act)]
        total_preservees += len(tous_les_ids) - len(ids_existants)
        if ids_existants:
            for chunk in chunked(ids_existants, CHUNK_SIZE):
                rd = requests.post(f"{base_doc}/tables/{table_id}/data/delete", headers=headers, json=chunk)
                rd.raise_for_status()
        total_supprimees += len(ids_existants)
    print(f"[3/5] {total_supprimees} ancienne(s) ligne(s) importée(s) supprimée(s) sur les années {annees} "
          f"({total_preservees} ligne(s) ajoutée(s) manuellement préservée(s), autres années non touchées)")

    # --- 4. Inserer les nouvelles lignes de cette annee ---
    records = [
        {label_vers_id[k]: nettoyer_valeur(v) for k, v in rec.items()}
        for rec in records
    ]
    total = 0
    for num_chunk, chunk in enumerate(chunked(records, CHUNK_SIZE), start=1):
        payload = {"records": [{"fields": rec} for rec in chunk]}
        try:
            r = requests.post(f"{base_doc}/tables/{table_id}/records", headers=headers, json=payload)
        except requests.exceptions.InvalidJSONError:
            print(f"ERREUR : valeur non-JSON détectée dans le paquet {num_chunk} (lignes {total+1} à {total+len(chunk)}).")
            for i, rec in enumerate(chunk):
                for k, v in rec.items():
                    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                        print(f"  -> ligne {total+i+1}, colonne '{k}' : valeur {v!r}")
            sys.exit(1)
        r.raise_for_status()
        total += len(chunk)
        print(f"[4/5] {total}/{len(records)} lignes insérées")

    print(f"[5/5] Terminé. Table '{table_id}' à jour pour les années {annees}.")
    print("       Côté DigDash : rien à reconstruire, il suffit de rafraîchir la source.")


if __name__ == "__main__":
    sys.exit(main())
