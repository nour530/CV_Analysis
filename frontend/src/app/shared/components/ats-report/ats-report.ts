import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { AtsCategoryId, AtsCheck, AtsReport, AtsStatus } from '../../../core/api.models';
import { IconComponent } from '../icon/icon';
import { ScoreRingComponent } from '../score-ring/score-ring';

const GRADE_LABELS: Record<AtsReport['grade'], string> = {
  excellent: 'Excellent',
  good: 'Good',
  fair: 'Needs work',
  poor: 'At risk',
};

const STATUS_LABELS: Record<AtsStatus, string> = {
  pass: 'Passed',
  warning: 'Improve',
  fail: 'Fix',
  info: 'Note',
};

interface CheckGroup {
  id: AtsCategoryId;
  label: string;
  score: number;
  checks: AtsCheck[];
  issues: number;
}

@Component({
  selector: 'app-ats-report',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [IconComponent, ScoreRingComponent],
  templateUrl: './ats-report.html',
  styleUrl: './ats-report.css',
})
export class AtsReportComponent {
  readonly report = input.required<AtsReport>();
  /** Compact mode hides the detailed checklist (used on job pages). */
  readonly compact = input(false);
  /** Hide the score and summary when the page already shows them. */
  readonly showHead = input(true);

  readonly gradeLabel = computed(() => GRADE_LABELS[this.report().grade]);
  readonly tone = computed<'strong' | 'moderate' | 'weak'>(() => {
    const score = this.report().score;
    return score >= 75 ? 'strong' : score >= 50 ? 'moderate' : 'weak';
  });
  readonly ringValue = computed(() => this.report().score / 100);
  readonly groups = computed<CheckGroup[]>(() =>
    this.report().categories.map((category) => {
      const checks = this.report()
        .checks.filter((check) => check.category === category.id)
        .sort((a, b) => order(a.status) - order(b.status));
      return {
        id: category.id,
        label: category.label,
        score: category.score,
        checks,
        issues: checks.filter((check) => check.status === 'fail' || check.status === 'warning').length,
      };
    }),
  );
  readonly missingSections = computed(() =>
    this.report().sections.filter((section) => !section.found),
  );
  readonly foundSections = computed(() =>
    this.report().sections.filter((section) => section.found),
  );
  readonly keywordPercent = computed(() =>
    Math.round((this.report().keyword_match?.coverage ?? 0) * 100),
  );

  statusLabel(status: AtsStatus): string {
    return STATUS_LABELS[status];
  }

  categoryTone(score: number): string {
    return score >= 75 ? 'good' : score >= 50 ? 'fair' : 'poor';
  }
}

function order(status: AtsStatus): number {
  return { fail: 0, warning: 1, info: 2, pass: 3 }[status];
}
