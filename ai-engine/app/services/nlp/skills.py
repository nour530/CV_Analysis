from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.schemas.hybrid_match import JobRequirement, ProfileSkill
from app.schemas.resume_analysis import SkillCategory

DEFAULT_FEATURES_DIR = Path(__file__).resolve().parents[3] / "data" / "features"
DEFAULT_DICTIONARY_PATH = DEFAULT_FEATURES_DIR / "skills_dictionary.csv"
DEFAULT_ALIASES_PATH = DEFAULT_FEATURES_DIR / "skill_aliases.csv"
# Hand-curated cross-industry skills with English and French aliases. The O*NET
# dictionary only covers IT software tools, and misses common skills such as
# Java, AWS, Agile, accounting, quality management or languages.
DEFAULT_SUPPLEMENT_PATH = DEFAULT_FEATURES_DIR / "skills_supplement.csv"

SHORT_CONTEXT_ALIASES = frozenset({"c", "r", "j", "go"})
# Dictionary aliases that are also everyday words (often French). They only
# count when the surrounding text is clearly about software tooling.
STRICT_CONTEXT_ALIASES = frozenset({"chef", "rapide", "route"})
MAX_EVIDENCE_PER_SKILL = 5
MAX_ALIAS_CHUNK = 300
# "&" is a boundary so "R&D" never matches the R language; accented letters are
# word characters so French words are not split.
ALIAS_BOUNDARY_LEFT = r"(?<![A-Za-z0-9+#&\u00C0-\u024F])"
ALIAS_BOUNDARY_RIGHT = r"(?![A-Za-z0-9+#&\u00C0-\u024F])"
SKILL_KEY_PATTERN = re.compile(r"[^a-z0-9+#]+")

PROGRAMMING_CONTEXT_PATTERN = re.compile(
    r"\b(?:language|programming|coding|scripting|developer|software|technical\s+skills|"
    r"skills|experience|experienced|proficien(?:t|cy)|familiar|knowledge|framework|"
    r"library|stack|engineer|architect)\b",
    re.IGNORECASE,
)
GENERAL_CONTEXT_PATTERN = re.compile(
    r"(?i)(?:\b(?:skills?|experience[ds]?|knowledge|proficien(?:t|cy)|fluent(?:ly)?|fluency|speaks?|spoken|"
    r"bilingual|bilingue|native|parlée?s?|écrite?s?|"
    r"languages?|required|requirements?|profile|qualifications?|background|"
    r"expertise|competenc(?:e|ies|y)|ability|strong|good|excellent|"
    r"compétences?|competences?|expériences?|experiences?|connaissances?|maîtrise|"
    r"maitrise|langues?|courant|requis|exigée?s?|profil|niveau|bonne|solide|"
    r"formation|diplôme|diplome)\b|:)"
)
STRICT_TECH_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(?:puppet|ansible|terraform|saltstack|configuration\s+management|devops|"
    r"cookbooks?|infrastructure\s+as\s+code|kubernetes|docker|linux)\b"
)
PREFERRED_CUE_PATTERN = re.compile(
    r"(?i)\b(?:preferred|nice\s+to\s+have|desired|a\s+plus|plus|bonus|advantage|"
    r"ideally|preferred\s+qualifications?|preferred\s+skills?|souhaitée?s?|"
    r"appréciée?s?|un\s+plus|un\s+atout|serait\s+un\s+plus|idéalement)\b"
)
REQUIRED_CUE_PATTERN = re.compile(
    r"(?i)\b(?:require(?:s|d)?|must\s+have|must|minimum|mandatory|need(?:ed|s)?|"
    r"qualifications?|proficien(?:t|cy)|strong\s+knowledge|experience\s+with|"
    r"knowledge\s+of|familiar\s+with|hands-on|expertise|ability\s+to|"
    r"requirements?|required\s+skills?|requise?s?|exigée?s?|obligatoires?|"
    r"indispensables?|maîtrise|maitrise|expérience\s+(?:en|dans|avec)|"
    r"connaissances?\s+(?:en|de|du|des)|vous\s+maîtrisez|profil\s+recherché)\b"
)
SENTENCE_PATTERN = re.compile(r"[^.!?;\u2022\n]+(?:[.!?;]|$)", re.UNICODE)


class LocalSkillExtractorError(RuntimeError):
    """Raised when the local skill dictionary cannot be loaded or used."""


@dataclass(frozen=True, slots=True)
class _AliasEntry:
    canonical: str
    category: SkillCategory
    alias: str
    alias_type: str
    requires_context: bool
    context: str = "programming"


def normalize_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    text = re.sub(r"[\t\n\r\f\v\u2028\u2029]+", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_skill_key(value: Any) -> str:
    return SKILL_KEY_PATTERN.sub("", strip_accents(normalize_text(value)).casefold())


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise LocalSkillExtractorError(f"Skill data file not found: {path}")
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
        except OSError as exc:
            raise LocalSkillExtractorError(f"Could not read {path}: {exc}") from exc
    raise LocalSkillExtractorError(f"Could not decode {path}: {last_error}")


def _cell(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    return value.strip() if isinstance(value, str) else ""


_VALID_CATEGORIES = frozenset(
    {
        "programming_language",
        "framework_library",
        "database",
        "tool_technology",
        "soft_skill",
        "domain_knowledge",
        "other",
    }
)


def _merge_supplement(
    path: Path,
    canonical_names: dict[str, str],
    categories: dict[str, SkillCategory],
    alias_candidates: dict[str, list[_AliasEntry]],
) -> None:
    """Add supplementary skills. A supplement alias replaces a conflicting
    dictionary alias, so curated entries win over O*NET ambiguities."""
    if not path.exists():
        return
    for row in _read_csv_rows(path):
        canonical = _cell(row, "canonical_skill")
        if not canonical:
            continue
        key = normalize_skill_key(canonical)
        canonical = canonical_names.get(key, canonical)
        canonical_names[key] = canonical
        category = _cell(row, "skill_category") or "other"
        if category not in _VALID_CATEGORIES:
            category = "other"
        categories.setdefault(key, category)  # type: ignore[arg-type]
        contextual = _cell(row, "requires_context").casefold() in {"true", "1", "yes"}
        surfaces = [canonical] + [
            alias.strip() for alias in _cell(row, "aliases").split(";") if alias.strip()
        ]
        variants: list[str] = []
        for surface in surfaces:
            variants.append(surface)
            plain = strip_accents(surface)
            if plain != surface:
                variants.append(plain)
        for alias in dict.fromkeys(variants):
            alias_key = normalize_skill_key(alias)
            if not alias_key:
                continue
            single_word = " " not in alias.strip() and "-" not in alias.strip()
            entry = _AliasEntry(
                canonical=canonical,
                category=categories[key],
                alias=alias,
                alias_type="canonical_surface" if alias == canonical else "supplement_alias",
                requires_context=(contextual and single_word)
                or alias_key in SHORT_CONTEXT_ALIASES,
                context="general" if contextual else "programming",
            )
            existing = alias_candidates.get(alias_key, [])
            if any(item.canonical != canonical for item in existing):
                existing = []
            alias_candidates[alias_key] = [*existing, entry]


@lru_cache(maxsize=8)
def _load_skill_data(
    dictionary_path: str,
    aliases_path: str | None,
    supplement_path: str | None = None,
) -> tuple[
    dict[str, str],
    dict[str, SkillCategory],
    dict[str, _AliasEntry],
    tuple[str, ...],
]:
    dictionary_rows = _read_csv_rows(Path(dictionary_path))
    canonical_names: dict[str, str] = {}
    categories: dict[str, SkillCategory] = {}
    aliases_from_dictionary: dict[str, list[tuple[str, str]]] = {}

    for row in dictionary_rows:
        canonical = _cell(row, "canonical_skill")
        if not canonical:
            continue
        key = normalize_skill_key(canonical)
        canonical_names[key] = canonical
        category = _cell(row, "skill_category") or "other"
        if category not in {
            "programming_language",
            "framework_library",
            "database",
            "tool_technology",
            "soft_skill",
            "domain_knowledge",
            "other",
        }:
            category = "other"
        categories[key] = category  # type: ignore[assignment]
        for alias in _cell(row, "aliases").split(";"):
            alias = alias.strip()
            if alias:
                aliases_from_dictionary.setdefault(key, []).append(
                    (alias, "dictionary_alias")
                )

    alias_candidates: dict[str, list[_AliasEntry]] = {}
    alias_path = Path(aliases_path) if aliases_path else None
    if alias_path is not None and alias_path.exists():
        for row in _read_csv_rows(alias_path):
            enabled = _cell(row, "match_enabled").casefold()
            if enabled not in {"true", "1", "yes"}:
                continue
            alias = _cell(row, "alias")
            canonical = _cell(row, "canonical_skill")
            if not alias or not canonical:
                continue
            canonical_key = normalize_skill_key(canonical)
            category = categories.get(canonical_key, "other")
            alias_key = normalize_skill_key(alias)
            alias_candidates.setdefault(alias_key, []).append(
                _AliasEntry(
                    canonical=canonical,
                    category=category,
                    alias=alias,
                    alias_type=_cell(row, "alias_type") or "alias",
                    requires_context=alias_key in SHORT_CONTEXT_ALIASES,
                )
            )
    else:
        for canonical_key, variants in aliases_from_dictionary.items():
            canonical = canonical_names[canonical_key]
            category = categories.get(canonical_key, "other")
            for alias, alias_type in variants:
                alias_key = normalize_skill_key(alias)
                alias_candidates.setdefault(alias_key, []).append(
                    _AliasEntry(
                        canonical=canonical,
                        category=category,
                        alias=alias,
                        alias_type=alias_type,
                        requires_context=alias_key in SHORT_CONTEXT_ALIASES,
                    )
                )

    for key in list(alias_candidates):
        if key in STRICT_CONTEXT_ALIASES:
            alias_candidates[key] = [
                _AliasEntry(
                    canonical=entry.canonical,
                    category=entry.category,
                    alias=entry.alias,
                    alias_type=entry.alias_type,
                    requires_context=True,
                    context="strict",
                )
                for entry in alias_candidates[key]
            ]

    if supplement_path:
        _merge_supplement(
            Path(supplement_path), canonical_names, categories, alias_candidates
        )

    alias_lookup: dict[str, _AliasEntry] = {}
    literals: list[str] = []
    for alias_key, entries in alias_candidates.items():
        canonical_values = {entry.canonical for entry in entries}
        if len(canonical_values) != 1:
            continue
        entry = sorted(
            entries,
            key=lambda item: (
                0 if item.alias_type == "canonical_surface" else 1,
                len(item.alias),
                item.alias.casefold(),
            ),
        )[0]
        alias_lookup[alias_key] = entry
        literals.extend(item.alias for item in entries if item.alias)

    return canonical_names, categories, alias_lookup, tuple(
        sorted(set(literals), key=lambda value: (-len(value), value.casefold(), value))
    )


class LocalSkillExtractor:
    """Boundary-aware dictionary matcher backed by the local feature artifacts."""

    def __init__(
        self,
        dictionary_path: str | Path | None = None,
        aliases_path: str | Path | None = None,
        supplement_path: str | Path | None = None,
    ) -> None:
        resolved_dictionary = Path(dictionary_path or DEFAULT_DICTIONARY_PATH)
        resolved_supplement = (
            Path(supplement_path)
            if supplement_path is not None
            else resolved_dictionary.parent / DEFAULT_SUPPLEMENT_PATH.name
        )
        if aliases_path is not None:
            resolved_aliases = Path(aliases_path)
        else:
            candidate_aliases = resolved_dictionary.parent / "skill_aliases.csv"
            resolved_aliases = (
                candidate_aliases
                if candidate_aliases.exists()
                else DEFAULT_ALIASES_PATH
                if dictionary_path is None
                else Path()
            )

        (
            self.canonical_names,
            self.categories,
            self.alias_lookup,
            literals,
        ) = _load_skill_data(
            str(resolved_dictionary.resolve()),
            str(resolved_aliases.resolve()) if resolved_aliases.name else None,
            str(resolved_supplement.resolve()) if resolved_supplement.name else None,
        )

        self.patterns: list[re.Pattern[str]] = []
        for start in range(0, len(literals), MAX_ALIAS_CHUNK):
            chunk = literals[start : start + MAX_ALIAS_CHUNK]
            expression = (
                ALIAS_BOUNDARY_LEFT
                + "(?:"
                + "|".join(re.escape(value) for value in chunk)
                + ")"
                + ALIAS_BOUNDARY_RIGHT
            )
            self.patterns.append(re.compile(expression, re.IGNORECASE))

    def _context_ok(
        self, text: str, start: int, end: int, context: str = "programming"
    ) -> bool:
        window = text[max(0, start - 120) : min(len(text), end + 120)]
        if context == "strict":
            return STRICT_TECH_CONTEXT_PATTERN.search(window) is not None
        if context == "general":
            return (
                GENERAL_CONTEXT_PATTERN.search(window) is not None
                or PROGRAMMING_CONTEXT_PATTERN.search(window) is not None
            )
        return PROGRAMMING_CONTEXT_PATTERN.search(window) is not None

    @staticmethod
    def _is_sentence_delimiter(text: str, index: int) -> bool:
        if index < 0 or index >= len(text):
            return False
        char = text[index]
        if char in "!?;\n":
            return True
        if char != ".":
            return False
        return index + 1 >= len(text) or text[index + 1].isspace()

    @classmethod
    def _sentence_bounds(cls, text: str, start: int, end: int) -> tuple[int, int]:
        sentence_start = start
        while sentence_start > 0 and not cls._is_sentence_delimiter(
            text, sentence_start - 1
        ):
            sentence_start -= 1
        sentence_end = end
        while sentence_end < len(text) and not cls._is_sentence_delimiter(
            text, sentence_end
        ):
            sentence_end += 1
        return sentence_start, min(len(text), sentence_end + 1)

    def canonicalize_skill(self, skill: str) -> str:
        key = normalize_skill_key(skill)
        return self.canonical_names.get(key, normalize_text(skill))

    def category_for_skill(self, skill: str) -> SkillCategory:
        key = normalize_skill_key(skill)
        return self.categories.get(key, "other")

    def _find_matches(self, text: str) -> list[dict[str, Any]]:
        normalized = normalize_text(text)
        if not normalized:
            return []

        raw: list[dict[str, Any]] = []
        for pattern in self.patterns:
            for match in pattern.finditer(normalized):
                alias_key = normalize_skill_key(match.group(0))
                entry = self.alias_lookup.get(alias_key)
                if entry is None:
                    continue
                if entry.requires_context and not self._context_ok(
                    normalized, match.start(), match.end(), entry.context
                ):
                    continue
                raw.append(
                    {
                        "canonical": entry.canonical,
                        "category": entry.category,
                        "alias": entry.alias,
                        "alias_type": entry.alias_type,
                        "start": match.start(),
                        "end": match.end(),
                        "requires_context": entry.requires_context,
                    }
                )

        raw.sort(
            key=lambda item: (
                item["start"],
                -(item["end"] - item["start"]),
                item["canonical"],
            )
        )
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()
        for item in raw:
            key = (item["start"], item["end"], item["canonical"])
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)

        kept: list[dict[str, Any]] = []
        for item in deduplicated:
            contained = any(
                other["start"] <= item["start"]
                and item["end"] <= other["end"]
                and (other["end"] - other["start"]) > (item["end"] - item["start"])
                for other in deduplicated
            )
            if not contained:
                kept.append(item)
        return kept

    @staticmethod
    def _confidence(match: Mapping[str, Any]) -> float:
        if match["requires_context"]:
            return 0.45
        alias_type = str(match["alias_type"])
        alias_length = len(str(match["alias"]))
        if alias_type in {"canonical_surface", "expanded_parenthetical"} and alias_length >= 4:
            return 0.85
        if alias_length >= 4:
            return 0.75
        return 0.6

    @staticmethod
    def _evidence(text: str, start: int, end: int) -> str:
        left = max(0, start - 120)
        right = min(len(text), end + 180)
        return normalize_text(text[left:right])

    def _grouped_matches(self, text: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for match in self._find_matches(text):
            match["evidence"] = self._evidence(text, match["start"], match["end"])
            match["confidence"] = self._confidence(match)
            grouped.setdefault(match["canonical"], []).append(match)
        return grouped

    def extract_skills(self, text: str) -> list[ProfileSkill]:
        grouped = self._grouped_matches(text)
        results: list[ProfileSkill] = []
        for canonical, matches in grouped.items():
            matches.sort(
                key=lambda item: (
                    -item["confidence"],
                    item["start"],
                    item["alias"].casefold(),
                )
            )
            evidence = list(
                dict.fromkeys(match["evidence"] for match in matches if match["evidence"])
            )[:MAX_EVIDENCE_PER_SKILL]
            results.append(
                ProfileSkill(
                    skill=canonical,
                    category=matches[0]["category"],
                    evidence=evidence,
                    confidence=matches[0]["confidence"],
                )
            )
        return sorted(results, key=lambda item: item.skill.casefold())

    def extract_requirements(self, text: str) -> list[JobRequirement]:
        grouped = self._grouped_matches(text)
        normalized = normalize_text(text)
        importance_rank = {"required": 0, "preferred": 1, "mentioned": 2}
        results: list[JobRequirement] = []
        for canonical, matches in grouped.items():
            best_importance = "mentioned"
            evidence: list[str] = []
            best_confidence = 0.0
            for match in matches:
                start, end = self._sentence_bounds(
                    normalized, match["start"], match["end"]
                )
                window = normalized[start:end]
                if PREFERRED_CUE_PATTERN.search(window):
                    importance = "preferred"
                elif REQUIRED_CUE_PATTERN.search(window):
                    importance = "required"
                else:
                    importance = "mentioned"
                if importance_rank[importance] < importance_rank[best_importance]:
                    best_importance = importance
                if match["evidence"]:
                    evidence.append(match["evidence"])
                best_confidence = max(best_confidence, match["confidence"])
            results.append(
                JobRequirement(
                    skill=canonical,
                    category=matches[0]["category"],
                    importance=best_importance,  # type: ignore[arg-type]
                    evidence=list(dict.fromkeys(evidence))[:MAX_EVIDENCE_PER_SKILL],
                    confidence=best_confidence or 0.5,
                )
            )
        return sorted(
            results,
            key=lambda item: (
                importance_rank[item.importance],
                item.skill.casefold(),
            ),
        )

    def split_sentences(self, text: str) -> list[str]:
        return [
            normalize_text(match.group(0))
            for match in SENTENCE_PATTERN.finditer(normalize_text(text))
            if normalize_text(match.group(0))
        ]
