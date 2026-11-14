import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { JobFacetsResponse } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { ThemeService } from '../../core/theme.service';
import { IconComponent, IconName } from '../../shared/components/icon/icon';
import { ScoreRingComponent } from '../../shared/components/score-ring/score-ring';

interface Feature {
  icon: IconName;
  title: string;
  text: string;
}

@Component({
  selector: 'app-landing-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DecimalPipe, RouterLink, IconComponent, ScoreRingComponent],
  templateUrl: './landing.page.html',
  styleUrl: './landing.page.css',
})
export class LandingPage {
  private readonly api = inject(JobMatchApiService);
  readonly theme = inject(ThemeService);
  readonly facets = signal<JobFacetsResponse | null>(null);

  readonly liveJobs = computed(() => {
    const facets = this.facets();
    return facets && facets.live ? facets : null;
  });
  readonly topCompanies = computed(() =>
    (this.liveJobs()?.companies ?? []).slice(0, 8).map((item) => item.value),
  );
  readonly topCities = computed(() =>
    (this.liveJobs()?.cities ?? []).slice(0, 4).map((item) => item.value),
  );

  readonly steps = [
    { title: 'Upload your CV', text: 'PDF, Word or plain text, in English or French. Your file is read, never stored.' },
    { title: 'Get your ATS score', text: 'See how applicant tracking systems read your CV, with a fix for every problem.' },
    { title: 'Improve it', text: 'Edit section by section with a live preview. Accept or reject each suggestion.' },
    { title: 'Match and apply', text: 'See your match with real openings, then track every application in one place.' },
  ];

  readonly features: Feature[] = [
    { icon: 'target', title: 'ATS check in seconds', text: 'Readability, contact details, sections, measurable results and keywords: over 20 checks with plain-language fixes.' },
    { icon: 'sparkle', title: 'AI that sticks to your facts', text: 'Rewrites keep your facts. New figures or skills are flagged, and placeholders like [X%] are left for you to fill in.' },
    { icon: 'jobs', title: 'Real jobs in Tunisia', text: 'Current openings published by employers themselves, refreshed every few hours, with a direct link to apply.' },
    { icon: 'matches', title: 'Know your match', text: 'Every job shows how well your CV fits and which required skills are missing.' },
    { icon: 'edit', title: 'Word and PDF, ATS-safe', text: 'Single-column templates with standard headings that every ATS can read.' },
    { icon: 'bookmark', title: 'Track applications', text: 'Save jobs and follow them from applied to interview and offer.' },
  ];

  constructor() {
    this.api
      .getJobFacets()
      .pipe(takeUntilDestroyed(inject(DestroyRef)))
      .subscribe({ next: (facets) => this.facets.set(facets), error: () => undefined });
  }
}
