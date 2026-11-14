"""Sample CVs shared by ATS and CV builder tests."""

STRONG_CV = """Amira Ben Salah
Tunis, Tunisia | +216 22 345 678 | amira.bensalah@example.com | linkedin.com/in/amira-bensalah

PROFESSIONAL SUMMARY
Data analyst with 4 years of experience turning retail and telecom data into decisions.

PROFESSIONAL EXPERIENCE
Data Analyst, Ooredoo, Tunis | Mar 2022 – Present
- Built 12 Power BI dashboards used by 150 managers across 6 regions
- Reduced monthly reporting time by 40% by automating SQL pipelines in Python
- Led A/B tests on 3 pricing offers, increasing conversion by 8%
- Trained 20 business users on self-service reporting
Junior Analyst, Monoprix, Tunis | Jul 2020 – Feb 2022
- Analysed sales of 2,000 products to optimise stock levels across 25 stores
- Created weekly KPI reports for regional managers
- Supported the migration of reporting to Microsoft Azure
- Automated data quality checks, cutting errors by 30%

EDUCATION
Master in Business Intelligence, ESSEC Tunis | 2018 – 2020
Bachelor in Computer Science, ISG Tunis | 2015 – 2018

SKILLS
Python, SQL, Power BI, Tableau, Excel, Azure, Statistics, Machine learning, ETL, Data visualization, Git

LANGUAGES
Arabic: native · French: fluent · English: fluent (C1)

CERTIFICATIONS
Microsoft Certified: Power BI Data Analyst Associate (2023)
""" + "\n".join(
    f"- Delivered analysis project {index} for a regional team of {index + 3} people" for index in range(1, 8)
)

FRENCH_CV = """Youssef Trabelsi
Sfax, Tunisie · +216 98 765 432 · youssef.trabelsi@example.com

PROFIL
Ingénieur qualité avec 5 ans d'expérience dans l'industrie automobile.

EXPÉRIENCE PROFESSIONNELLE
Ingénieur qualité, Câblerie Sud, Sfax | Janvier 2021 – Aujourd'hui
- Piloté 25 analyses AMDEC sur de nouveaux faisceaux électriques
- Réduit le taux de rebut de 18 % grâce à la méthode 8D
- Animé une équipe de 6 techniciens qualité
Technicien qualité, Autoparts, Sousse | Sept 2018 – Déc 2020
- Réalisé les audits internes ISO 9001 et IATF 16949
- Mis en place le SPC sur 4 lignes de production

FORMATION
Diplôme d'ingénieur en génie industriel, ENIS | 2013 – 2018

COMPÉTENCES
AMDEC, 8D, SPC, PPAP, ISO 9001, IATF 16949, Lean Manufacturing, Kaizen, Excel, SAP

LANGUES
Arabe : langue maternelle · Français : courant · Anglais : B2
"""
