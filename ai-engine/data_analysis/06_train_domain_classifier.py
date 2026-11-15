"""Train, evaluate and save the career-field classifier.

1. ESCO cross-validation: 5 folds over ESCO occupations, grouped so the
   English and French versions of one occupation never sit on both sides.
2. Real CVs, ESCO-only model: a model trained only on ESCO is tested on the
   labelled CVs in data/raw/resumes/Resume.csv (the baseline).
3. Real CVs, full model: 5-fold stratified cross-validation over the CVs;
   each fold trains on all of ESCO, ~400 IT job descriptions and the other
   4/5 of the CVs, and is tested on CVs it has never seen.
4. Spot checks on short English and French CVs written for Tunisia.
5. The final model (ESCO + all CVs + IT job descriptions) is saved to
   data/models/domain_classifier.joblib for the API.

Writes data_analysis/outputs/domain_classifier_evaluation.{json,md}.
Run:  python data_analysis/06_train_domain_classifier.py   (about 10-15 minutes)
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

import numpy as np  # noqa: E402
from sklearn.metrics import accuracy_score, classification_report, f1_score  # noqa: E402
from sklearn.model_selection import GroupKFold, StratifiedKFold  # noqa: E402

from app.services.career.classifier import (  # noqa: E402
    MODEL_PATH,
    DomainClassifier,
    build_pipeline,
    cv_training_documents,
    it_job_documents,
    training_documents,
)
from app.services.career.esco import EscoCatalog  # noqa: E402

OUTPUT_DIR = AI_ENGINE_DIR / "data_analysis" / "outputs"

SPOT_CHECKS = [
    ("it", "Développeur full-stack, 4 ans d'expérience. Développement d'API REST avec Python et FastAPI, "
           "interfaces Angular et TypeScript, Docker, GitLab CI, PostgreSQL. Diplôme d'ingénieur en informatique."),
    ("it", "Software engineer: built React and Node.js web apps, AWS deployments, Kubernetes, CI/CD pipelines, "
           "unit testing, SQL databases. BSc Computer Science."),
    ("health", "Infirmière diplômée d'État, 6 ans en service de chirurgie : soins aux patients, préparation et "
               "administration des traitements, hygiène hospitalière, suivi des dossiers de soins."),
    ("finance", "Comptable : tenue de la comptabilité générale, bilans, déclarations fiscales, rapprochements "
                "bancaires, paie, logiciel Sage."),
    ("hospitality", "Chef de cuisine : gestion de brigade, élaboration des menus, normes HACCP, pâtisserie, "
                    "gestion des commandes et des stocks alimentaires."),
    ("sales", "Commercial terrain : prospection de nouveaux clients, négociation, suivi du portefeuille, "
              "atteinte des objectifs de vente, CRM."),
    ("logistics", "Responsable logistique : gestion d'entrepôt, réception et expédition des marchandises, "
                  "planification du transport, gestion des stocks, chauffeurs, supply chain."),
    ("trades", "Électricien bâtiment : installation et câblage électrique, tableaux électriques, lecture de "
               "plans, mise en conformité, chantiers résidentiels."),
    ("manufacturing", "Opérateur de production en usine textile : conduite de machines, contrôle qualité des "
                      "pièces, réglage, respect des cadences et des consignes de sécurité."),
    ("education", "Enseignante de mathématiques au lycée : préparation des cours, évaluation des élèves, "
                  "suivi pédagogique, conseils de classe."),
    ("agriculture", "Technicien agricole : suivi des cultures d'oliviers et de céréales, irrigation, traitements "
                    "phytosanitaires, conduite de tracteur, récolte."),
    ("engineering", "Ingénieur génie civil : conception de structures en béton armé, calculs, suivi de chantier, "
                    "AutoCAD, Robot Structural Analysis."),
]


def esco_cross_validation(catalog: EscoCatalog) -> dict:
    texts, labels, groups = training_documents(catalog)
    predicted: list[str] = [""] * len(texts)
    for train_idx, test_idx in GroupKFold(n_splits=5).split(texts, labels, groups):
        pipeline = build_pipeline()
        pipeline.fit([texts[i] for i in train_idx], [labels[i] for i in train_idx])
        for index, label in zip(test_idx, pipeline.predict([texts[i] for i in test_idx])):
            predicted[index] = label
    report = classification_report(labels, predicted, output_dict=True, zero_division=0)
    return {
        "documents": len(texts),
        "accuracy": round(accuracy_score(labels, predicted), 4),
        "macro_f1": round(f1_score(labels, predicted, average="macro"), 4),
        "per_field_f1": {
            k: round(v["f1-score"], 3)
            for k, v in report.items()
            if isinstance(v, dict) and k not in ("macro avg", "weighted avg")
        },
    }


def summarize(expected: list[str], predicted: list[str], top3: list[list[str]], categories: list[str]) -> dict:
    per_category: dict[str, dict] = {}
    for category in sorted(set(categories)):
        idx = [i for i, c in enumerate(categories) if c == category]
        correct = sum(1 for i in idx if predicted[i] == expected[i])
        mistakes = Counter(predicted[i] for i in idx if predicted[i] != expected[i]).most_common(2)
        per_category[category] = {
            "field": expected[idx[0]],
            "cvs": len(idx),
            "accuracy": round(correct / len(idx), 3),
            "common_confusions": mistakes,
        }
    return {
        "cvs": len(expected),
        "accuracy": round(accuracy_score(expected, predicted), 4),
        "top3_accuracy": round(sum(e in t for e, t in zip(expected, top3)) / len(expected), 4),
        "macro_f1": round(f1_score(expected, predicted, average="macro"), 4),
        "per_category": per_category,
    }


def top_k(pipeline, texts: list[str], k: int = 3) -> tuple[list[str], list[list[str]]]:
    classes = np.array(pipeline.named_steps["model"].classes_)
    probabilities = pipeline.predict_proba(texts)
    order = np.argsort(-probabilities, axis=1)[:, :k]
    return [str(classes[row[0]]) for row in order], [[str(c) for c in classes[row]] for row in order]


def esco_only_on_cvs(catalog: EscoCatalog, texts: list[str], labels: list[str], categories: list[str]) -> dict:
    classifier = DomainClassifier.train(catalog)
    predicted, top3 = top_k(classifier.pipeline, texts)
    return summarize(labels, predicted, top3, categories)


def combined_cross_validation(
    catalog: EscoCatalog, texts: list[str], labels: list[str], categories: list[str], job_texts: list[str], job_labels: list[str]
) -> dict:
    esco_texts, esco_labels, _ = training_documents(catalog)
    esco_texts, esco_labels = [*esco_texts, *job_texts], [*esco_labels, *job_labels]
    predicted: list[str] = [""] * len(texts)
    top3: list[list[str]] = [[] for _ in texts]
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    for fold, (train_idx, test_idx) in enumerate(folds.split(texts, labels), start=1):
        started = time.time()
        pipeline = build_pipeline()
        pipeline.fit(
            [*esco_texts, *(texts[i] for i in train_idx)],
            [*esco_labels, *(labels[i] for i in train_idx)],
        )
        fold_pred, fold_top3 = top_k(pipeline, [texts[i] for i in test_idx])
        for i, p, t in zip(test_idx, fold_pred, fold_top3):
            predicted[i], top3[i] = p, t
        print(f"  fold {fold}/5 done in {time.time() - started:.0f}s", flush=True)
    return summarize(labels, predicted, top3, categories)


def cv_table(result: dict) -> list[str]:
    return [
        "| CV category | Field | CVs | Accuracy | Most common confusions |",
        "| --- | --- | ---: | ---: | --- |",
        *[
            f"| {cat} | {v['field']} | {v['cvs']} | {v['accuracy']} | "
            f"{', '.join(f'{d} ({n})' for d, n in v['common_confusions']) or '-'} |"
            for cat, v in result["per_category"].items()
        ],
    ]


def main() -> int:
    catalog = EscoCatalog()
    if not catalog.available:
        print("ESCO data not found. Run data_analysis/05_import_esco.py first.")
        return 1
    cv_texts, cv_labels, cv_categories = cv_training_documents()
    if not cv_texts:
        print("data/raw/resumes/Resume.csv not found.")
        return 1
    job_texts, job_labels = it_job_documents()
    print(f"ESCO: {len(catalog.occupations)} occupations, {len(catalog.skills)} skills; CVs: {len(cv_texts)}; IT job ads: {len(job_texts)}")

    esco_cv = esco_cross_validation(catalog)
    print(f"1. ESCO 5-fold CV: accuracy {esco_cv['accuracy']}, macro-F1 {esco_cv['macro_f1']}", flush=True)
    baseline = esco_only_on_cvs(catalog, cv_texts, cv_labels, cv_categories)
    print(f"2. Real CVs, ESCO-only model: accuracy {baseline['accuracy']}, top-3 {baseline['top3_accuracy']}", flush=True)
    combined = combined_cross_validation(catalog, cv_texts, cv_labels, cv_categories, job_texts, job_labels)
    print(f"3. Real CVs, full model: accuracy {combined['accuracy']}, top-3 {combined['top3_accuracy']}", flush=True)

    final = DomainClassifier.train(catalog, [*cv_texts, *job_texts], [*cv_labels, *job_labels])
    spots = []
    for expected, text in SPOT_CHECKS:
        top = final.predict(text, top=3)
        spots.append({"expected": expected, "text": text, "top3": [(p.domain_id, p.probability) for p in top]})
    spot_hits = sum(s["top3"][0][0] == s["expected"] for s in spots)
    print(f"4. Spot checks: {spot_hits}/{len(spots)} correct first choice", flush=True)
    for s in spots:
        print(f"   {'OK ' if s['top3'][0][0] == s['expected'] else 'MISS'} {s['expected']:<13} -> {s['top3'][:2]}")
    trained_on = {"esco_occupations": len(catalog.occupations), "cvs": len(cv_texts), "it_job_ads": len(job_texts)}
    final.save(catalog, trained_on=trained_on)
    print(f"5. Saved {MODEL_PATH.relative_to(AI_ENGINE_DIR)} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB)")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "esco_cross_validation": esco_cv,
        "real_cvs_esco_only": baseline,
        "real_cvs_full_model": combined,
        "spot_checks": spots,
        "saved_model": {"path": str(MODEL_PATH.relative_to(AI_ENGINE_DIR)), **trained_on},
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "domain_classifier_evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Career-field classifier evaluation",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "Model: TF-IDF (word 1-2 grams + character 3-5 grams) and logistic regression",
        "over 14 career fields. Training data: ESCO occupations in English and French",
        "(title, synonyms, description, essential skills), labelled real CVs and",
        "about 400 IT job descriptions (developer, data and cloud vocabulary).",
        "",
        "## Summary",
        "",
        "| Test | Accuracy | Top-3 | Macro-F1 |",
        "| --- | ---: | ---: | ---: |",
        f"| ESCO occupations, ESCO-only model (grouped 5-fold) | {esco_cv['accuracy']} | - | {esco_cv['macro_f1']} |",
        f"| Real CVs, ESCO-only model | {baseline['accuracy']} | {baseline['top3_accuracy']} | {baseline['macro_f1']} |",
        f"| **Real CVs, full model (5-fold, unseen CVs)** | **{combined['accuracy']}** | **{combined['top3_accuracy']}** | **{combined['macro_f1']}** |",
        "",
        "ESCO alone describes jobs in dictionary style, so on real CVs it mistakes",
        "generic CV wording (manage, team, clients, reports) for business roles.",
        "Adding real CVs to training fixes most of that.",
        "",
        f"## Spot checks: short English and French CVs ({spot_hits}/{len(spots)} first choice correct)",
        "",
        "| Expected | Predicted (top 2) | CV excerpt |",
        "| --- | --- | --- |",
        *[
            f"| {s['expected']} | {', '.join(f'{d} {p:.0%}' for d, p in s['top3'][:2])} | {s['text'][:70]}… |"
            for s in spots
        ],
        "",
        "## Real CVs by category, full model",
        "",
        *cv_table(combined),
        "",
        "## Real CVs by category, ESCO-only model",
        "",
        *cv_table(baseline),
        "",
        "## ESCO cross-validation by field (F1)",
        "",
        "| Field | F1 |",
        "| --- | ---: |",
        *[f"| {k} | {v} |" for k, v in sorted(esco_cv["per_field_f1"].items())],
        "",
        "Categories spanning several fields (consultant, fitness, automobile, apparel,",
        "aviation) are left out. The CV dataset is US-centric and in English; French is",
        "covered by the ESCO French labels and descriptions.",
        "",
        "This service uses the ESCO classification of the European Commission.",
    ]
    (OUTPUT_DIR / "domain_classifier_evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Report written to data_analysis/outputs/domain_classifier_evaluation.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
