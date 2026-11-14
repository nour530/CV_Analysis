import { HttpErrorResponse } from '@angular/common/http';
import { ApiErrorDetail, ApiErrorResponse } from './api.models';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];
  readonly requestId: string | null;

  constructor(options: {
    message: string;
    status: number;
    code: string;
    details?: ApiErrorDetail[];
    requestId?: string | null;
  }) {
    super(options.message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.details = options.details ?? [];
    this.requestId = options.requestId ?? null;
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }

  if (error instanceof HttpErrorResponse) {
    const body = parseErrorBody(error.error);
    const status = error.status;
    const requestId =
      body?.request_id ??
      error.headers?.get('x-request-id') ??
      null;

    if (body) {
      return new ApiError({
        message: body.error.message,
        status,
        code: body.error.code,
        details: body.error.details,
        requestId,
      });
    }

    if (status === 0) {
      return new ApiError({
        message:
          'The API could not be reached. Check that the backend is running and that CORS allows this origin.',
        status,
        code: 'network_error',
        requestId,
      });
    }

    if (status === 413) {
      return new ApiError({
        message: 'The file or resume text is too large.',
        status,
        code: 'payload_too_large',
        requestId,
      });
    }

    if (status === 415) {
      return new ApiError({
        message: 'This file type is not supported. Upload a TXT, PDF, or DOCX CV.',
        status,
        code: 'unsupported_media_type',
        requestId,
      });
    }

    if (status === 422) {
      return new ApiError({
        message: 'The request could not be validated. Check the submitted information.',
        status,
        code: 'validation_error',
        requestId,
      });
    }

    if (status >= 500) {
      return new ApiError({
        message: 'The service is temporarily unavailable. Please try again.',
        status,
        code: 'server_error',
        requestId,
      });
    }

    return new ApiError({
      message: error.message || 'The request failed.',
      status,
      code: 'http_error',
      requestId,
    });
  }

  if (error instanceof Error && error.name === 'TimeoutError') {
    return new ApiError({
      message: 'The request timed out. Please try again.',
      status: 0,
      code: 'timeout',
    });
  }

  return new ApiError({
    message: 'Something went wrong. Please try again.',
    status: 0,
    code: 'unknown_error',
  });
}

function parseErrorBody(value: unknown): ApiErrorResponse | null {
  if (!value || typeof value !== 'object') {
    return null;
  }

  const candidate = value as Partial<ApiErrorResponse>;
  if (
    !candidate.error ||
    typeof candidate.error !== 'object' ||
    typeof candidate.error.code !== 'string' ||
    typeof candidate.error.message !== 'string'
  ) {
    return null;
  }

  return {
    error: {
      code: candidate.error.code,
      message: candidate.error.message,
      details: Array.isArray(candidate.error.details)
        ? candidate.error.details
        : [],
    },
    request_id:
      typeof candidate.request_id === 'string' ? candidate.request_id : null,
  };
}
