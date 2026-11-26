# Decisions

Append-only. Supersede by adding a new entry that points at the old one; never edit history.

## 2026-11-26 — Configuration audit

- **Standard:** PROJECT-BOOTSTRAP-AND-AUDIT v0.52.0 (copy sha256 `c83afe9c…b376`)
- **Tier:** T1 (blast radius B1, audience A1), **draft**. The owner answered "not sure" to
  whether the app is live, whether anyone depends on it, and where it runs. T1 is the lowest
  tier the evidence supports: it ran only on the owner's PC. If it is live with real users,
  the tier is T3 (B2–B3, A3), because it stores people's CVs.
- **Maintainers:** no (default; the owner answered "not sure"). Every commit is by one person.
- **Time zone:** UTC alone (default).
- **Irreversible decisions, recorded at their evidenced state, not yet confirmed by the owner:**
  MIT license (locked for published versions), no published package, `/api/v1` used only
  by the project's own frontend, PostgreSQL schema under Alembic, Python 3.12 with
  TypeScript.
- **Chosen:**
  - A1: declare `pandas` for the test suite. CI had failed on every push to `main` since the
    first commit, because a test imported it and CI never installed it.
  - A2: hashed Python lockfiles generated from the tested environment, installed with
    `--require-hashes` in CI and Docker. Saved models reject other scikit-learn versions.
  - A3: `AGENTS.md`, the `CLAUDE.md` shim, and deny rules for secrets.
  - A4: this record.
  - A5: `TEST-VERIFICATION-CHECKLIST.md`.
  - A6: `.gitattributes` and `.editorconfig`.
  - A7: CI gates for ruff lint and format, a gitleaks secret scan, and `pip-audit` and
    `npm audit`.
  - A8: Dependabot, grouped weekly, with security updates ungrouped.
- **Declined:** A9, a ruleset on `main` requiring CI. *Reopen when:* a second person can
  push, or the app is confirmed live with real users.
- **Declined:** A10, `CHANGELOG.md` and annotated release tags. *Reopen when:* a built
  artifact or a deployment reaches anyone other than the owner, or the tier is confirmed T2
  or above.
- **Declined:** A11, `SECURITY.md`. *Reopen when:* the app is confirmed live with real users,
  or the tier is confirmed T3.
- **Deferred:** a test that runs the Alembic migrations against PostgreSQL. No test runs them
  today. *Trigger:* the next migration, or a CI PostgreSQL service being added.
- **Do not re-propose:** relicensing to Apache-2.0, because MIT is already granted for every
  published version and nothing requires a change. Restructuring the two-root layout,
  because it is clear and the churn outweighs the value.
- **Local mitigation:** none found.
- **Open for the owner:** confirm the tier answers, the copyright holder (`LICENSE` names
  "nour mansour"), and the GitHub security settings (secret scanning, push protection,
  Dependabot alerts, email privacy).
- **Alias:** the decision record here is `docs/decisions.md`.
