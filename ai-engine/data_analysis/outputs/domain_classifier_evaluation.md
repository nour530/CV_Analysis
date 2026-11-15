# Career-field classifier evaluation

Generated: 2026-11-15T20:32:01.327303+00:00

Model: TF-IDF (word 1-2 grams + character 3-5 grams) and logistic regression
over 14 career fields. Training data: ESCO occupations in English and French
(title, synonyms, description, essential skills), labelled real CVs and
about 400 IT job descriptions (developer, data and cloud vocabulary).

## Summary

| Test | Accuracy | Top-3 | Macro-F1 |
| --- | ---: | ---: | ---: |
| ESCO occupations, ESCO-only model (grouped 5-fold) | 0.8336 | - | 0.8209 |
| Real CVs, ESCO-only model | 0.3383 | 0.7126 | 0.2931 |
| **Real CVs, full model (5-fold, unseen CVs)** | **0.7156** | **0.9225** | **0.7049** |

ESCO alone describes jobs in dictionary style, so on real CVs it mistakes
generic CV wording (manage, team, clients, reports) for business roles.
Adding real CVs to training fixes most of that.

## Spot checks: short English and French CVs (12/12 first choice correct)

| Expected | Predicted (top 2) | CV excerpt |
| --- | --- | --- |
| it | it 49%, engineering 12% | Développeur full-stack, 4 ans d'expérience. Développement d'API REST a… |
| it | it 48%, engineering 14% | Software engineer: built React and Node.js web apps, AWS deployments, … |
| health | health 55%, hospitality 17% | Infirmière diplômée d'État, 6 ans en service de chirurgie : soins aux … |
| finance | finance 50%, legal_social 10% | Comptable : tenue de la comptabilité générale, bilans, déclarations fi… |
| hospitality | hospitality 68%, trades 7% | Chef de cuisine : gestion de brigade, élaboration des menus, normes HA… |
| sales | sales 58%, trades 8% | Commercial terrain : prospection de nouveaux clients, négociation, sui… |
| logistics | logistics 62%, business 24% | Responsable logistique : gestion d'entrepôt, réception et expédition d… |
| trades | trades 47%, manufacturing 25% | Électricien bâtiment : installation et câblage électrique, tableaux él… |
| manufacturing | manufacturing 74%, trades 17% | Opérateur de production en usine textile : conduite de machines, contr… |
| education | education 68%, trades 6% | Enseignante de mathématiques au lycée : préparation des cours, évaluat… |
| agriculture | agriculture 52%, trades 16% | Technicien agricole : suivi des cultures d'oliviers et de céréales, ir… |
| engineering | engineering 89%, manufacturing 2% | Ingénieur génie civil : conception de structures en béton armé, calcul… |

## Real CVs by category, full model

| CV category | Field | CVs | Accuracy | Most common confusions |
| --- | --- | ---: | ---: | --- |
| ACCOUNTANT | finance | 118 | 0.949 | sales (3), creative (1) |
| ADVOCATE | legal_social | 118 | 0.508 | health (20), sales (15) |
| AGRICULTURE | agriculture | 63 | 0.429 | creative (6), finance (6) |
| ARTS | creative | 103 | 0.379 | education (21), sales (18) |
| BANKING | finance | 115 | 0.73 | sales (11), it (8) |
| BPO | sales | 22 | 0.227 | finance (7), it (5) |
| BUSINESS-DEVELOPMENT | sales | 119 | 0.782 | finance (16), business (3) |
| CHEF | hospitality | 118 | 0.805 | sales (12), creative (4) |
| CONSTRUCTION | trades | 112 | 0.679 | creative (9), finance (9) |
| DESIGNER | creative | 107 | 0.757 | sales (8), engineering (7) |
| DIGITAL-MEDIA | creative | 96 | 0.635 | sales (24), it (6) |
| ENGINEERING | engineering | 118 | 0.695 | it (12), creative (8) |
| FINANCE | finance | 118 | 0.881 | sales (9), creative (2) |
| HEALTHCARE | health | 115 | 0.609 | sales (16), legal_social (7) |
| HR | business | 110 | 0.809 | sales (8), finance (4) |
| INFORMATION-TECHNOLOGY | it | 120 | 0.85 | creative (7), finance (3) |
| PUBLIC-RELATIONS | sales | 111 | 0.748 | creative (17), finance (5) |
| SALES | sales | 116 | 0.784 | creative (8), trades (4) |
| TEACHER | education | 102 | 0.765 | creative (11), sales (5) |

## Real CVs by category, ESCO-only model

| CV category | Field | CVs | Accuracy | Most common confusions |
| --- | --- | ---: | ---: | --- |
| ACCOUNTANT | finance | 118 | 0.61 | business (39), sales (6) |
| ADVOCATE | legal_social | 118 | 0.195 | business (63), sales (18) |
| AGRICULTURE | agriculture | 63 | 0.016 | business (36), engineering (12) |
| ARTS | creative | 103 | 0.039 | business (39), education (27) |
| BANKING | finance | 115 | 0.174 | business (70), sales (14) |
| BPO | sales | 22 | 0.0 | business (17), it (3) |
| BUSINESS-DEVELOPMENT | sales | 119 | 0.252 | business (85), finance (2) |
| CHEF | hospitality | 118 | 0.636 | business (28), sales (10) |
| CONSTRUCTION | trades | 112 | 0.062 | business (68), engineering (19) |
| DESIGNER | creative | 107 | 0.29 | engineering (26), business (19) |
| DIGITAL-MEDIA | creative | 96 | 0.073 | business (46), sales (36) |
| ENGINEERING | engineering | 118 | 0.593 | business (30), it (13) |
| FINANCE | finance | 118 | 0.229 | business (85), sales (5) |
| HEALTHCARE | health | 115 | 0.183 | business (69), sales (12) |
| HR | business | 110 | 0.955 | sales (2), legal_social (1) |
| INFORMATION-TECHNOLOGY | it | 120 | 0.433 | business (57), legal_social (4) |
| PUBLIC-RELATIONS | sales | 111 | 0.216 | business (82), hospitality (2) |
| SALES | sales | 116 | 0.543 | business (35), hospitality (12) |
| TEACHER | education | 102 | 0.441 | business (29), health (12) |

## ESCO cross-validation by field (F1)

| Field | F1 |
| --- | ---: |
| agriculture | 0.798 |
| business | 0.836 |
| creative | 0.831 |
| education | 0.921 |
| engineering | 0.857 |
| finance | 0.816 |
| health | 0.837 |
| hospitality | 0.778 |
| it | 0.823 |
| legal_social | 0.826 |
| logistics | 0.646 |
| manufacturing | 0.845 |
| sales | 0.838 |
| trades | 0.841 |

Categories spanning several fields (consultant, fitness, automobile, apparel,
aviation) are left out. The CV dataset is US-centric and in English; French is
covered by the ESCO French labels and descriptions.

This service uses the ESCO classification of the European Commission.
