import {
  MatchClassification,
  MatchStrength,
  MissingImportance,
  RequirementImportance,
  SkillCategory,
} from './api.models';

const SKILL_CATEGORY_LABELS: Record<SkillCategory, string> = {
  programming_language: 'Programming languages',
  framework_library: 'Frameworks & libraries',
  database: 'Databases',
  tool_technology: 'Tools & technologies',
  soft_skill: 'Soft skills',
  domain_knowledge: 'Domain knowledge',
  other: 'Other',
};

const COMPONENT_LABELS: Record<string, string> = {
  required_skill_coverage: 'Required skills',
  preferred_skill_coverage: 'Preferred skills',
  skill_overlap: 'Skill overlap',
  semantic_similarity: 'Text similarity',
  category_compatibility: 'Category fit',
};

const IMPORTANCE_LABELS: Record<
  RequirementImportance | MissingImportance,
  string
> = {
  required: 'Required',
  preferred: 'Preferred',
  mentioned: 'Mentioned',
  optional: 'Optional',
};

export function formatPercent(
  value: number | null | undefined,
  fractionDigits = 0,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }

  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

export function toPercent(value: number | null | undefined): number {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return 0;
  }

  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

const WORKPLACE_LABELS: Record<string, string> = {
  onsite: 'On-site',
  hybrid: 'Hybrid',
  remote: 'Remote',
};

export function workplaceLabel(value: string | null | undefined): string | null {
  return value ? (WORKPLACE_LABELS[value] ?? null) : null;
}

/** "Today", "3 days ago", "2 weeks ago", "5 months ago" for a posting date. */
export function postedAgo(
  value: string | null | undefined,
  now: Date = new Date(),
): string | null {
  if (!value) {
    return null;
  }
  const posted = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
  if (Number.isNaN(posted.getTime())) {
    return null;
  }
  const days = Math.max(0, Math.floor((now.getTime() - posted.getTime()) / 86_400_000));
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`;
  if (days < 730) return `${Math.floor(days / 30)} months ago`;
  return `${Math.floor(days / 365)} years ago`;
}

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) {
    return '—';
  }

  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }

  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) {
    return '—';
  }

  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }

  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

export function classificationLabel(
  classification: MatchClassification | null | undefined,
): string {
  switch (classification) {
    case 'strong_match':
      return 'Strong match';
    case 'moderate_match':
      return 'Moderate match';
    case 'weak_match':
      return 'Weak match';
    default:
      return 'Unclassified';
  }
}

export function classificationTone(
  classification: MatchClassification | null | undefined,
): 'strong' | 'moderate' | 'weak' {
  if (classification === 'strong_match') {
    return 'strong';
  }
  if (classification === 'moderate_match') {
    return 'moderate';
  }
  return 'weak';
}

export function skillCategoryLabel(category: SkillCategory): string {
  return SKILL_CATEGORY_LABELS[category] ?? 'Other';
}

export function componentLabel(name: string): string {
  return (
    COMPONENT_LABELS[name] ??
    name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (character) => character.toUpperCase())
  );
}

export function importanceLabel(
  importance: RequirementImportance | MissingImportance,
): string {
  return IMPORTANCE_LABELS[importance] ?? importance;
}

export function matchStrengthLabel(strength: MatchStrength): string {
  switch (strength) {
    case 'strong':
      return 'Strong';
    case 'partial':
      return 'Partial';
    case 'weak':
      return 'Weak';
    default:
      return strength;
  }
}
