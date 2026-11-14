import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatPercent, toPercent } from '../../../core/formatters';

@Component({
  selector: 'app-score-ring',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="score-ring"
      [class.score-ring--strong]="tone() === 'strong'"
      [class.score-ring--moderate]="tone() === 'moderate'"
      [class.score-ring--weak]="tone() === 'weak'"
      role="progressbar"
      [attr.aria-label]="ariaLabel()"
      aria-valuemin="0"
      aria-valuemax="100"
      [attr.aria-valuenow]="percent()"
    >
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle class="score-ring__track" cx="50" cy="50" r="42" />
        <circle
          class="score-ring__value"
          cx="50"
          cy="50"
          r="42"
          [attr.stroke-dasharray]="circumference"
          [attr.stroke-dashoffset]="dashOffset()"
        />
      </svg>
      <span class="score-ring__label">
        <strong>{{ display() }}</strong>
        @if (caption()) {
          <small>{{ caption() }}</small>
        }
      </span>
    </div>
  `,
  styleUrl: './score-ring.css',
})
export class ScoreRingComponent {
  readonly score = input(0);
  readonly caption = input('');
  readonly tone = input<'strong' | 'moderate' | 'weak' | 'neutral'>('neutral');
  readonly circumference = 2 * Math.PI * 42;
  readonly percent = computed(() => toPercent(this.score()));
  readonly dashOffset = computed(
    () => this.circumference - (this.percent() / 100) * this.circumference,
  );
  readonly display = computed(() => formatPercent(this.score()));
  readonly ariaLabel = computed(
    () => `${this.display()} overall compatibility${this.caption() ? `, ${this.caption()}` : ''}`,
  );
}
