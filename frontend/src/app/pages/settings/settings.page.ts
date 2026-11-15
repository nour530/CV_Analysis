import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { CareerDomain, Occupation } from '../../core/career.models';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  AbstractControl,
  FormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router } from '@angular/router';
import { API_BASE_URL } from '../../core/api.config';
import { ApiError, toApiError } from '../../core/api-error';
import { HealthResponse } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { formatDateTime } from '../../core/formatters';
import { WorkspaceStore } from '../../core/workspace.store';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';

@Component({
  selector: 'app-settings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    IconComponent,
    PageHeaderComponent,
  ],
  templateUrl: './settings.page.html',
  styleUrl: './settings.page.css',
})
export class SettingsPage {
  private readonly api = inject(JobMatchApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly formBuilder = inject(FormBuilder);
  readonly store = inject(WorkspaceStore);
  readonly apiBaseUrl = inject(API_BASE_URL);

  readonly form = this.formBuilder.nonNullable.group({
    recommendationLimit: [
      this.store.preferences().recommendationLimit,
      [Validators.required, Validators.min(1), Validators.max(50)],
    ],
    candidatePoolSize: [
      this.store.preferences().candidatePoolSize,
      [Validators.required, Validators.min(1), Validators.max(200)],
    ],
    preferredCategory: [
      this.store.preferences().preferredCategory,
      [Validators.maxLength(120)],
    ],
    allowRemoteAnalysis: [this.store.preferences().allowRemoteAnalysis],
  });

  readonly profileForm = this.formBuilder.nonNullable.group({
    fullName: [
      this.auth.user()?.full_name ?? '',
      [Validators.required, Validators.minLength(2), Validators.maxLength(160)],
    ],
  });

  readonly passwordForm = this.formBuilder.nonNullable.group(
    {
      currentPassword: ['', [Validators.required]],
      newPassword: [
        '',
        [Validators.required, Validators.minLength(12), Validators.maxLength(128)],
      ],
      confirmPassword: ['', [Validators.required]],
    },
    { validators: passwordsMatch },
  );

  readonly saving = signal(false);
  readonly domains = signal<CareerDomain[]>([]);
  readonly goalDomain = signal('');
  readonly goalRole = signal('');
  readonly roleSuggestions = signal<Occupation[]>([]);
  readonly goalSaving = signal(false);
  private roleTimer: ReturnType<typeof setTimeout> | null = null;
  readonly profileSaving = signal(false);
  readonly passwordSaving = signal(false);
  readonly logoutSaving = signal(false);
  readonly clearing = signal(false);
  readonly feedback = signal('');
  readonly feedbackTone = signal<'success' | 'error'>('success');
  readonly checkingConnection = signal(false);
  readonly connectionError = signal<ApiError | null>(null);
  readonly health = signal<HealthResponse | null>(null);
  readonly checkedAt = signal<Date | null>(null);
  readonly formatDateTime = formatDateTime;

  constructor() {
    this.loadSettings();
    this.api
      .getCareerDomains()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: (list) => this.domains.set(list.domains), error: () => undefined });
  }

  setGoalDomain(event: Event): void {
    this.goalDomain.set((event.target as HTMLSelectElement).value);
  }

  setGoalRole(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.goalRole.set(value);
    if (this.roleTimer) {
      clearTimeout(this.roleTimer);
    }
    if (value.trim().length < 2) {
      this.roleSuggestions.set([]);
      return;
    }
    this.roleTimer = setTimeout(() => {
      this.api
        .searchOccupations(value, this.goalDomain() || null)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: (list) => this.roleSuggestions.set(list.occupations), error: () => undefined });
    }, 250);
  }

  saveGoal(): void {
    const value = this.form.getRawValue();
    this.goalSaving.set(true);
    this.api
      .updateUserSettings({
        recommendation_limit: value.recommendationLimit,
        candidate_pool_size: value.candidatePoolSize,
        preferred_category: value.preferredCategory.trim() || null,
        allow_remote_analysis: value.allowRemoteAnalysis,
        target_domain: this.goalDomain() || null,
        target_role: this.goalRole().trim() || null,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (settings) => {
          this.store.applyServerSettings(settings);
          this.showFeedback('Career goal saved.', 'success');
          this.goalSaving.set(false);
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.goalSaving.set(false);
        },
      });
  }

  savePreferences(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const value = this.form.getRawValue();
    this.saving.set(true);
    this.api
      .updateUserSettings({
        recommendation_limit: value.recommendationLimit,
        candidate_pool_size: value.candidatePoolSize,
        preferred_category: value.preferredCategory.trim() || null,
        allow_remote_analysis: value.allowRemoteAnalysis,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (settings) => {
          this.store.applyServerSettings(settings);
          this.syncForm();
          this.showFeedback('Account preferences saved.', 'success');
          this.saving.set(false);
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.saving.set(false);
        },
      });
  }

  resetPreferences(): void {
    this.form.reset({
      recommendationLimit: 10,
      candidatePoolSize: 40,
      preferredCategory: '',
      allowRemoteAnalysis: false,
    });
    this.savePreferences();
  }

  saveProfile(): void {
    if (this.profileForm.invalid) {
      this.profileForm.markAllAsTouched();
      return;
    }

    this.profileSaving.set(true);
    this.auth
      .updateProfile(this.profileForm.controls.fullName.value)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (user) => {
          this.profileForm.reset({ fullName: user.full_name });
          this.showFeedback('Profile updated.', 'success');
          this.profileSaving.set(false);
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.profileSaving.set(false);
        },
      });
  }

  changePassword(): void {
    if (this.passwordForm.invalid) {
      this.passwordForm.markAllAsTouched();
      return;
    }

    const value = this.passwordForm.getRawValue();
    this.passwordSaving.set(true);
    this.auth
      .changePassword(value.currentPassword, value.newPassword)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.passwordForm.reset({
            currentPassword: '',
            newPassword: '',
            confirmPassword: '',
          });
          this.showFeedback(
            'Password changed. Other sessions were signed out.',
            'success',
          );
          this.passwordSaving.set(false);
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.passwordSaving.set(false);
        },
      });
  }

  clearActivity(): void {
    this.store.clearActivity();
    this.showFeedback('Recent activity was cleared from this browser.', 'success');
  }

  clearMatchHistory(): void {
    if (!window.confirm('Delete all saved match results from your account?')) {
      return;
    }
    this.clearing.set(true);
    this.api
      .deleteMatchHistory()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.store.clearActivity();
          this.showFeedback('Saved match history was deleted.', 'success');
          this.clearing.set(false);
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.clearing.set(false);
        },
      });
  }

  clearWorkspace(): void {
    if (
      !window.confirm(
        'Delete all CV analyses and match history from your account?',
      )
    ) {
      return;
    }
    this.clearing.set(true);
    this.api
      .deleteCvHistory()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.api
            .deleteMatchHistory()
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
              next: () => {
                this.store.clearWorkspace();
                this.syncForm();
                this.showFeedback(
                  'CV analyses and match history were deleted from your account.',
                  'success',
                );
                this.clearing.set(false);
              },
              error: (error: unknown) => {
                this.showFeedback(toApiError(error).message, 'error');
                this.clearing.set(false);
              },
            });
        },
        error: (error: unknown) => {
          this.showFeedback(toApiError(error).message, 'error');
          this.clearing.set(false);
        },
      });
  }

  logout(): void {
    if (this.logoutSaving()) {
      return;
    }
    this.logoutSaving.set(true);
    this.auth
      .logout()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.logoutSaving.set(false);
          void this.router.navigate(['/login']);
        },
        error: (error: unknown) => {
          this.logoutSaving.set(false);
          this.showFeedback(
            `Sign out failed. Your session is still active. ${toApiError(error).message}`,
            'error',
          );
        },
      });
  }

  checkConnection(): void {
    this.checkingConnection.set(true);
    this.connectionError.set(null);
    this.health.set(null);

    this.api
      .health()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (health) => {
          this.health.set(health);
          this.checkedAt.set(new Date());
          this.checkingConnection.set(false);
        },
        error: (error: unknown) => {
          this.connectionError.set(toApiError(error));
          this.checkedAt.set(new Date());
          this.checkingConnection.set(false);
        },
      });
  }

  private loadSettings(): void {
    this.api
      .getUserSettings()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (settings) => {
          this.store.applyServerSettings(settings);
          this.syncForm();
          this.goalDomain.set(settings.target_domain ?? '');
          this.goalRole.set(settings.target_role ?? '');
        },
        error: () => undefined,
      });
  }

  private syncForm(): void {
    const preferences = this.store.preferences();
    this.form.reset({
      recommendationLimit: preferences.recommendationLimit,
      candidatePoolSize: preferences.candidatePoolSize,
      preferredCategory: preferences.preferredCategory,
      allowRemoteAnalysis: preferences.allowRemoteAnalysis,
    });
  }

  private showFeedback(message: string, tone: 'success' | 'error'): void {
    this.feedback.set(message);
    this.feedbackTone.set(tone);
  }
}

function passwordsMatch(
  control: AbstractControl,
): ValidationErrors | null {
  const password = control.get('newPassword')?.value;
  const confirmation = control.get('confirmPassword')?.value;
  return password === confirmation ? null : { passwordsMismatch: true };
}
