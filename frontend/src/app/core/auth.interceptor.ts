import {
  HttpErrorResponse,
  HttpInterceptorFn,
} from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService } from './auth.service';

const PUBLIC_AUTH_PATHS = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
] as const;

function isPublicAuthRequest(url: string): boolean {
  const path = url.split('?')[0].split('#')[0];
  return PUBLIC_AUTH_PATHS.some((publicPath) => path.endsWith(publicPath));
}

export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (isPublicAuthRequest(request.url)) {
    return next(request.clone({ withCredentials: true }));
  }

  const token = auth.accessToken();
  const authorized =
    token && !request.headers.has('Authorization')
      ? request.clone({
          setHeaders: { Authorization: `Bearer ${token}` },
          withCredentials: true,
        })
      : request.clone({ withCredentials: true });

  return next(authorized).pipe(
    catchError((error: unknown) => {
      if (!(error instanceof HttpErrorResponse) || error.status !== 401 || !token) {
        return throwError(() => error);
      }

      return auth.refreshAccessToken().pipe(
        switchMap((response) =>
          next(
            request.clone({
              setHeaders: {
                Authorization: `Bearer ${response.access_token}`,
              },
              withCredentials: true,
            }),
          ),
        ),
        catchError((refreshError: unknown) => {
          auth.clearSession();
          void router.navigate(['/login'], {
            queryParams: { reason: 'session-expired' },
          });
          return throwError(() => refreshError);
        }),
      );
    }),
  );
};
