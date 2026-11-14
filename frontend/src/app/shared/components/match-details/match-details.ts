import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import {
  HybridMatchResult,
  MatchedSkill,
  MissingSkill,
  PreferredSkill,
} from '../../../core/api.models';
import {
  classificationLabel,
  componentLabel,
  formatPercent,
  importanceLabel,
  matchStrengthLabel,
  skillCategoryLabel,
  toPercent,
} from '../../../core/formatters';
import { IconComponent } from '../icon/icon';

@Component({
  selector: 'app-match-details',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [IconComponent],
  template: `
    <div class="match-details">
      @if (scoreComponents().length) {
        <section class="match-details__section">
          <div class="section-heading">
            <h4>Score breakdown</h4>
            <span class="muted">Calculated by the matching API</span>
          </div>
          <div class="meter-list">
            @for (component of scoreComponents(); track component.name) {
              <div class="meter-row">
                <div class="meter-row__top">
                  <span>{{ component.label }}</span>
                  <strong>{{ component.display }}</strong>
                </div>
                <div
                  class="meter"
                  role="progressbar"
                  [attr.aria-label]="component.label"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  [attr.aria-valuenow]="component.percent"
                >
                  <span [style.width.%]="component.percent"></span>
                </div>
                <small>{{ component.detail }}</small>
              </div>
            }
          </div>
        </section>
      }

      <div class="match-details__columns">
        <section class="match-details__section">
          <div class="section-heading">
            <h4>Matched skills</h4>
            <span class="count-pill">{{ matchedSkills().length }}</span>
          </div>
          @if (matchedSkills().length) {
            <div class="chip-list">
              @for (skill of matchedSkills(); track skill.skill) {
                <span
                  class="tag tag--success"
                  [title]="skillTitle(skill)"
                >
                  {{ skill.skill }}
                  <small>{{ matchStrengthLabel(skill.match_quality) }}</small>
                </span>
              }
            </div>
          } @else {
            <p class="muted small">No matching skills were found.</p>
          }
        </section>

        <section class="match-details__section">
          <div class="section-heading">
            <h4>Missing required skills</h4>
            <span class="count-pill">{{ missingSkills().length }}</span>
          </div>
          @if (missingSkills().length) {
            <div class="chip-list">
              @for (skill of missingSkills(); track skill.skill) {
                <span class="tag tag--warning" [title]="skill.reason">
                  {{ skill.skill }}
                  <small>{{ importanceLabel(skill.importance) }}</small>
                </span>
              }
            </div>
          } @else {
            <p class="muted small">All required skills were evidenced.</p>
          }
        </section>
      </div>

      @if (preferredSkills().length) {
        <section class="match-details__section">
          <div class="section-heading">
            <h4>Preferred skills</h4>
            <span class="muted">{{ preferredSkills().length }} listed</span>
          </div>
          <div class="chip-list">
            @for (skill of preferredSkills(); track skill.skill) {
              <span
                class="tag"
                [class.tag--success]="skill.matched"
                [class.tag--neutral]="!skill.matched"
              >
                {{ skill.skill }}
                <small>{{ skill.matched ? 'Matched' : 'Not matched' }}</small>
              </span>
            }
          </div>
        </section>
      }

      @if (match().relevant_experience_evidence.length) {
        <details class="inline-details">
          <summary>Relevant experience evidence</summary>
          <ul class="plain-list">
            @for (item of match().relevant_experience_evidence; track $index) {
              <li>
                <strong>{{ item.requirement }}</strong>
                <p>{{ item.evidence }}</p>
                @if (item.matched_skills.length) {
                  <small>Skills: {{ item.matched_skills.join(', ') }}</small>
                }
              </li>
            }
          </ul>
        </details>
      }

      @if (match().explanation; as explanation) {
        <section class="explanation-card">
          <div class="section-heading">
            <h4>
              <app-icon name="sparkle" [size]="18" />
              AI explanation
            </h4>
            <span class="tag tag--accent">Optional AI</span>
          </div>
          <p class="explanation-card__headline">{{ explanation.headline }}</p>
          <p class="explanation-card__narrative">{{ explanation.narrative }}</p>
          @if (explanation.strengths.length) {
            <h5>Strengths</h5>
            <ul class="check-list">
              @for (item of explanation.strengths; track $index) {
                <li><app-icon name="check" [size]="15" /> {{ item }}</li>
              }
            </ul>
          }
          @if (explanation.gaps.length) {
            <h5>Gaps</h5>
            <ul class="gap-list">
              @for (item of explanation.gaps; track $index) {
                <li><app-icon name="alert" [size]="15" /> {{ item }}</li>
              }
            </ul>
          }
          @if (match().llm_metadata; as metadata) {
            <p class="explanation-card__meta">
              Generated by {{ metadata.provider }} · {{ metadata.model }} ·
              prompt {{ metadata.prompt_version }}
            </p>
          }
          <p class="explanation-card__meta">
            The score and ranking above remain deterministic local results.
            AI text does not change the compatibility score.
          </p>
        </section>
      } @else if (llmRequested()) {
        <div class="inline-notice">
          <app-icon name="info" [size]="18" />
          <p>
            @if (llmAuthorized()) {
              An AI explanation was requested but is not available for this
              result. The local compatibility score is unchanged.
            } @else {
              Remote AI analysis is disabled on the API server. The local
              compatibility score is shown without an AI explanation.
            }
          </p>
        </div>
      }

      @if (match().recommendations.length) {
        <section class="match-details__section">
          <h4>Matching recommendations</h4>
          <ul class="check-list">
            @for (item of match().recommendations; track $index) {
              <li><app-icon name="arrow-right" [size]="15" /> {{ item }}</li>
            }
          </ul>
        </section>
      }

      @if (match().uncertainty_indicators.length) {
        <details class="inline-details">
          <summary>Uncertainty indicators</summary>
          <ul class="plain-list">
            @for (item of match().uncertainty_indicators; track $index) {
              <li>
                <strong>{{ item.field }}</strong>
                <p>{{ item.reason }}</p>
                <small>Severity: {{ item.severity }}</small>
              </li>
            }
          </ul>
        </details>
      }

      <div class="notice-grid">
        <div class="inline-notice">
          <app-icon name="shield" [size]="18" />
          <p>{{ match().fairness_notice }}</p>
        </div>
        <div class="inline-notice">
          <app-icon name="info" [size]="18" />
          <p>{{ match().disclaimer }}</p>
        </div>
      </div>

      <div class="analysis-meta">
        <span>
          <strong>Classification:</strong>
          {{ classificationLabel(match().classification) }}
        </span>
        <span>
          <strong>Source:</strong>
          {{ match().recommendation_source === 'openai' ? 'AI-enriched local match' : 'Local deterministic match' }}
        </span>
      </div>
    </div>
  `,
  styleUrl: './match-details.css',
})
export class MatchDetailsComponent {
  readonly match = input.required<HybridMatchResult>();
  readonly llmRequested = input(false);
  readonly llmAuthorized = input(false);

  readonly classificationLabel = classificationLabel;
  readonly matchStrengthLabel = matchStrengthLabel;
  readonly importanceLabel = importanceLabel;
  readonly skillCategoryLabel = skillCategoryLabel;

  readonly matchedSkills = computed<MatchedSkill[]>(
    () => this.match().matched_skills ?? [],
  );
  readonly missingSkills = computed<MissingSkill[]>(
    () => this.match().missing_required_skills ?? [],
  );
  readonly preferredSkills = computed<PreferredSkill[]>(
    () => this.match().preferred_skills ?? [],
  );
  readonly scoreComponents = computed(() =>
    (this.match().score_breakdown?.components ?? [])
      .filter((component) => component.available && component.raw_value !== null)
      .map((component) => ({
        ...component,
        label: componentLabel(component.name),
        percent: toPercent(component.raw_value),
        display: formatPercent(component.raw_value),
      })),
  );

  skillTitle(skill: MatchedSkill): string {
    const evidence = skill.resume_evidence?.length
      ? `Resume evidence: ${skill.resume_evidence.join(' · ')}`
      : 'No resume evidence recorded';
    return `${evidence} · ${skillCategoryLabel(skill.category)} · ${importanceLabel(skill.importance)}`;
  }
}
