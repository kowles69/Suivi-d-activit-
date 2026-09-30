"""
transform_tempeau.py
=====================
Transforme l'export brut annuel Tempeau (13 colonnes) au format utilisé
dans les tables Grist (22 colonnes), en s'appuyant sur :
  - une table de correspondance Type d'activité -> Thématique / Sous-thématique
    (construite une fois à partir de l'historique déjà propre, puis enrichie
    chaque année avec les nouvelles activités que tu valides)
  - la table Ref_Agents (Agent_anonyme, Quotite, Statut, Heures_attendues)
  - optionnel : un fichier de corrections validees par Hervé
    (Type d'activité fautif -> Intitulé corrigé), applique avant tout le reste

Usage :
    python transform_tempeau.py \
        --raw export_activite_2026.csv \
        --annee 2026 \
        --lookup lookup_activite_thematique.csv \
        --ref-agents Ref_Agents.csv \
        --unite "EGDD/EAU/PAG/Gestion des patrimoines" \
        --corrections corrections_activites.csv \
        --out Tempeau_2026_pret_a_importer.csv

En sortie :
  - <out>                          : fichier prêt à importer dans Grist (22 colonnes, ordre identique)
  - <out>_anomalies_activites.csv  : lignes dont le "Type d'activité" n'a pas de correspondance connue
  - <out>_anomalies_agents.csv     : lignes dont l'agent n'a pas de correspondance dans Ref_Agents
  - <lookup> mis à jour automatiquement si tu corriges les anomalies (voir plus bas)
"""

import argparse
import os
import sys
import pandas as pd
from datetime import date
from match_activite import build_reference, resoudre_activite, lire_fichier

# Colonnes exactes et ordre exact des tables Grist (Feuil1 / Tempeau2024)
COLONNES_FINALES = [
    "Semaine de l'activité", "Agent", "Unité", "Thématique", "Sous-thématique",
    "Type d'activité", "Projet", "Famille", "Thématiques", "Durée",
    "Coût Horaire", "Coût Total", "Bénéficiaires", "Lieux", "Commentaire",
    "ANNEE", "SEMAINE", "MOIS", "Agent_anonyme", "Quotite", "ETP", "Statut",
]


def lire_corrections(chemin):
    """Lit le fichier de corrections rempli par Hervé.

    Accepte un .xlsx, ou un .csv separe par ';' ou ',' (detection auto), en
    UTF-8 ou en ANSI (cas d'un CSV reenregistre depuis Excel).
    Colonnes attendues : "Type d'activité" et "Intitulé corrigé".
    Retourne un dict {intitule fautif: intitule corrige}, lignes vides ignorees.
    """
    if str(chemin).lower().endswith((".xlsx", ".xls")):
        corr = pd.read_excel(chemin)
    else:
        try:
            corr = pd.read_csv(chemin, sep=None, engine="python", encoding="utf-8-sig")
        except UnicodeDecodeError:
            corr = pd.read_csv(chemin, sep=None, engine="python", encoding="cp1252")
    manquantes = {"Type d'activité", "Intitulé corrigé"} - set(corr.columns)
    if manquantes:
        raise SystemExit(f"Fichier de corrections {chemin} : colonne(s) manquante(s) {sorted(manquantes)}")
    corr = corr.dropna(subset=["Type d'activité", "Intitulé corrigé"])
    mapping = {}
    for a, b in zip(corr["Type d'activité"], corr["Intitulé corrigé"]):
        a, b = str(a).strip(), str(b).strip()
        if a and b:
            mapping[a] = b
    return mapping


def appliquer_corrections(raw, chemin):
    """Remplace dans raw["Type d'activité"] chaque intitulé fautif par sa version
    corrigée. Retourne (raw modifié, nb de corrections chargées, nb de lignes modifiées)."""
    if not os.path.exists(chemin):
        print(f"       ATTENTION : fichier de corrections '{chemin}' introuvable, aucune correction appliquée "
              f"(normal s'il n'y a encore eu aucune correction validée)")
        return raw, 0, 0
    mapping = lire_corrections(chemin)
    avant = raw["Type d'activité"].copy()
    raw["Type d'activité"] = raw["Type d'activité"].map(
        lambda v: mapping.get(v.strip(), v) if isinstance(v, str) else v
    )
    n_lignes = int((avant != raw["Type d'activité"]).sum())
    return raw, len(mapping), n_lignes


def semaine_label_to_month(label: str) -> int:
    """'2025-19' -> mois calendaire du lundi de cette semaine ISO."""
    annee_label, semaine = label.split("-")
    d = date.fromisocalendar(int(annee_label), int(semaine), 1)
    return d.month


def main():
    p = argparse.ArgumentParser(description="Transforme l'export brut Tempeau au format Grist")
    p.add_argument("--raw", required=True, help="CSV brut exporté de Tempeau")
    p.add_argument("--annee", required=False, type=int, default=None,
                    help="Ne sert plus a fixer ANNEE (deduite automatiquement de 'Semaine de l'activité'). "
                         "Optionnel : si fourni, sert juste a vérifier que le fichier correspond bien à la campagne attendue.")
    p.add_argument("--lookup", required=True, help="CSV de correspondance Type d'activité -> Thématique/Sous-thématique")
    p.add_argument("--ref-agents", required=True, help="CSV Ref_Agents (Agent, Agent_anonyme, Quotite, Statut, Heures_attendues)")
    p.add_argument("--unite", default="EGDD/EAU/PAG/Gestion des patrimoines", help="Unité à conserver (les autres lignes sont filtrées)")
    p.add_argument("--out", required=True, help="Fichier CSV de sortie, prêt à importer dans Grist")
    p.add_argument("--sheet", default=0, help="Nom ou numero de l'onglet si --raw est un fichier Excel (defaut: premier onglet)")
    p.add_argument("--corrections", default=None,
                    help="Optionnel : fichier des corrections validees par Hervé (colonnes Type d'activité / Intitulé corrigé)")
    args = p.parse_args()

    raw = lire_fichier(args.raw, sheet_name=args.sheet)
    lookup = pd.read_csv(args.lookup)
    ref_agents = pd.read_csv(args.ref_agents)

    # --- 1. Filtrer sur l'unité ---
    avant = len(raw)
    raw = raw[raw["Unité"] == args.unite].copy()
    print(f"[1/6] Filtre Unité '{args.unite}' : {avant} -> {len(raw)} lignes")

    # --- 1bis. Ecarter les lignes sans "Semaine de l'activité" (lignes vides/incompletes) ---
    avant_vides = len(raw)
    lignes_vides = raw[raw["Semaine de l'activité"].isna()]
    raw = raw[raw["Semaine de l'activité"].notna()].copy()
    if len(lignes_vides):
        print(f"[1bis/6] {avant_vides - len(raw)} ligne(s) sans 'Semaine de l'activité' écartée(s) (probablement des lignes vides du fichier)")

    # --- 1ter. Corrections manuelles validees par Hervé (si fournies) ---
    # Appliquees AVANT la reconnaissance des thematiques : l'intitule corrige
    # passe ensuite par la meme logique que les autres (regle du prefixe, etc.).
    if args.corrections:
        raw, n_corr, n_lignes = appliquer_corrections(raw, args.corrections)
        print(f"[1ter/6] Corrections validées : {n_corr} chargée(s) depuis {args.corrections}, {n_lignes} ligne(s) modifiée(s)")

    # --- 2. Dériver ANNEE / SEMAINE / MOIS -- ANNEE est deduite ligne par ligne
    # de l'annee reelle contenue dans "Semaine de l'activité" (ex: "2023-52" -> 2023),
    # PAS fixee globalement : une meme campagne annuelle peut legitimement contenir
    # des semaines de fin d'annee precedente / debut d'annee suivante. ---
    raw["ANNEE"] = raw["Semaine de l'activité"].astype(str).str.split("-").str[0].astype(int)
    raw["SEMAINE"] = raw["Semaine de l'activité"].astype(str).str.split("-").str[1].astype(int)
    raw["MOIS"] = raw["Semaine de l'activité"].apply(semaine_label_to_month)
    annees_trouvees = sorted(raw["ANNEE"].unique())
    print(f"[2/6] ANNEE déduite de 'Semaine de l'activité' -> années présentes : {annees_trouvees}")
    if args.annee is not None and args.annee not in annees_trouvees:
        print(f"       ATTENTION : --annee {args.annee} ne correspond à aucune ligne du fichier (années trouvées : {annees_trouvees})")

    # --- 3. Enrichir Thématique / Sous-thématique (auto-correction + anomalies reelles) ---
    ref = build_reference(lookup)
    resultats = raw["Type d'activité"].apply(lambda x: resoudre_activite(x, ref))
    raw["Thématique"] = [r[0] for r in resultats]
    raw["Sous-thématique"] = [r[1] for r in resultats]
    methode = pd.Series([r[2] for r in resultats], index=raw.index)

    corrigees = raw[methode.isin(["grands_projets", "fuzzy_lookup", "fuzzy_grands_projets"])][
        ["Type d'activité", "Thématique", "Sous-thématique"]
    ].drop_duplicates()
    if len(corrigees):
        print(f"[3/6] {len(corrigees)} intitulé(s) corrigé(s) automatiquement (préfixe manquant / faute de frappe) :")
        for _, r in corrigees.iterrows():
            type_act = r["Type d'activité"]
            print(f"       '{type_act}' -> {r['Thématique']} / {r['Sous-thématique']}")

    anomalies_activites = raw[raw["Thématique"].isna()][
        ["Semaine de l'activité", "Agent", "Type d'activité", "Durée"]
    ].drop_duplicates(subset=["Type d'activité"])
    print(f"[3/6] Thématique/Sous-thématique enrichies : {len(anomalies_activites)} activité(s) réellement inconnue(s)")

    # --- 4. Enrichir Agent_anonyme / Quotite / Statut / Heures_attendues ---
    # Si le fichier d'entree contient deja ces colonnes (fichier deja transforme
    # passe par erreur en --raw), on les retire pour eviter les suffixes _x/_y.
    deja_la = [c for c in ["Agent_anonyme", "Quotite", "Statut", "Heures_attendues", "ETP"] if c in raw.columns]
    if deja_la:
        print(f"       ATTENTION : colonnes déjà présentes dans le fichier d'entrée (fichier déjà transformé ?), recalculées : {deja_la}")
        raw = raw.drop(columns=deja_la)
    raw = raw.merge(
        ref_agents[["Agent", "Agent_anonyme", "Quotite", "Statut", "Heures_attendues"]],
        on="Agent", how="left",
    )
    anomalies_agents = raw[raw["Agent_anonyme"].isna()][
        ["Agent"]
    ].drop_duplicates()
    print(f"[4/6] Agents enrichis : {len(anomalies_agents)} agent(s) absent(s) de Ref_Agents")

    # --- 5. Calculer ETP ---
    raw["ETP"] = raw["Durée"] / raw["Heures_attendues"]
    print("[5/6] ETP calculé (Durée / Heures_attendues de l'agent)")

    # --- 6. Réordonner et exporter ---
    for col in COLONNES_FINALES:
        if col not in raw.columns:
            raw[col] = pd.NA
    final = raw[COLONNES_FINALES]
    final.to_csv(args.out, index=False)

    anomalies_activites.to_csv(args.out.replace(".csv", "_anomalies_activites.csv"), index=False)
    anomalies_agents.to_csv(args.out.replace(".csv", "_anomalies_agents.csv"), index=False)

    print(f"[6/6] Terminé.")
    print(f"  -> {args.out} ({len(final)} lignes)")
    if len(anomalies_activites):
        print(f"  -> A VERIFIER : {args.out.replace('.csv','_anomalies_activites.csv')} "
              f"({len(anomalies_activites)} activités sans Thématique/Sous-thématique)")
    if len(anomalies_agents):
        print(f"  -> A VERIFIER : {args.out.replace('.csv','_anomalies_agents.csv')} "
              f"({len(anomalies_agents)} agents absents de Ref_Agents)")
    if not len(anomalies_activites) and not len(anomalies_agents):
        print("  -> Aucune anomalie, fichier pret a etre importe dans Grist tel quel.")


if __name__ == "__main__":
    sys.exit(main())
