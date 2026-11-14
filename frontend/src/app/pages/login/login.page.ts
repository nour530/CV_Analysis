import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { AuthService } from '../../core/auth.service';
import { IconComponent } from '../../shared/components/icon/icon';

@Component({
  selector: 'app-login-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, RouterLink, IconComponent],
  templateUrl: './login.page.html',
  styleUrl: './login.page.css',
})
export class LoginPage {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly formBuilder = inject(FormBuilder);

  readonly form = this.formBuilder.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required]],
  });
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly notice = signal<string | null>(null);

  constructor() {
    const queryParams = this.route.snapshot.queryParamMap;
    if (queryParams.get('reason') === 'session-expired') {
      this.error.set(
        new ApiError({
          status: 401,
          code: 'session_expired',
          message: 'Your session expired. Sign in again to continue.',
        }),
      );
    }
    if (queryParams.get('registered') === '1') {
      this.notice.set(
        'If this email was available, the account can now sign in.',
      );
      const state = this.router.getCurrentNavigation()?.extras.state ?? history.state;
      const email = typeof state?.['email'] === 'string' ? (state['email'] as string) : null;
      if (email) {
        this.form.controls.email.setValue(email);
      }
    }
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.loading.set(true);
    this.error.set(null);
    const { email, password } = this.form.getRawValue();

    this.auth
      .login({ email: email.trim(), password })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.loading.set(false);
          void this.router.navigateByUrl(this.returnUrl());
        },
        error: (error: unknown) => {
          this.loading.set(false);
          this.error.set(toApiError(error));
        },
      });
  }

  private returnUrl(): string {
    const requested = this.route.snapshot.queryParamMap.get('returnUrl');
    return requested?.startsWith('/') ? requested : '/dashboard';
  }
}
