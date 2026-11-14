import { computed, Injectable, signal } from '@angular/core';
import { CVProfileResponse, UserSettings } from './api.models';

export interface RecentActivity {
  id: string;
  type: 'cv_analysis' | 'job_match' | 'recommendations';
  title: string;
  detail: string;
  occurredAt: string;
  score?: number;
}

export interface WorkspacePreferences {
  recommendationLimit: number;
  candidatePoolSize: number;
  preferredCategory: string;
  allowRemoteAnalysis: boolean;
}

interface StoredCv {
  resumeText: string | null;
  profile: CVProfileResponse;
  updatedAt: string;
}

const CV_STORAGE_KEY = 'jobmatch.cv.v1';
const ACTIVITY_STORAGE_KEY = 'jobmatch.activity.v1';
const PREFERENCES_STORAGE_KEY = 'jobmatch.preferences.v1';

const DEFAULT_PREFERENCES: WorkspacePreferences = {
  recommendationLimit: 10,
  candidatePoolSize: 40,
  preferredCategory: '',
  allowRemoteAnalysis: false,
};

@Injectable({ providedIn: 'root' })
export class WorkspaceStore {
  private readonly storedCv = readStorage<StoredCv | null>(CV_STORAGE_KEY, null, 'session');
  private readonly cvState = signal<StoredCv | null>(
    isStoredCv(this.storedCv) ? this.storedCv : null,
  );
  private readonly activityState = signal<RecentActivity[]>(
    readActivity(),
  );
  private readonly preferencesState = signal<WorkspacePreferences>(
    readPreferences(),
  );

  readonly cvProfile = computed(() => this.cvState()?.profile ?? null);
  readonly resumeText = computed(() => this.cvState()?.resumeText ?? null);
  readonly cvUpdatedAt = computed(() => this.cvState()?.updatedAt ?? null);
  readonly hasCv = computed(() => this.cvState() !== null);
  readonly recentActivity = this.activityState.asReadonly();
  readonly preferences = this.preferencesState.asReadonly();

  setCv(resumeText: string | null, profile: CVProfileResponse): void {
    const cv: StoredCv = {
      resumeText,
      profile,
      updatedAt: new Date().toISOString(),
    };
    this.cvState.set(cv);
    writeStorage(CV_STORAGE_KEY, cv, 'session');
  }

  setServerCv(profile: CVProfileResponse): void {
    this.cvState.set({
      resumeText: null,
      profile,
      updatedAt: new Date().toISOString(),
    });
    removeStorage(CV_STORAGE_KEY, 'session');
  }

  clearCv(): void {
    this.cvState.set(null);
    removeStorage(CV_STORAGE_KEY, 'session');
  }

  recordActivity(activity: Omit<RecentActivity, 'id' | 'occurredAt'>): void {
    const entry: RecentActivity = {
      ...activity,
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      occurredAt: new Date().toISOString(),
    };
    const next = [entry, ...this.activityState()].slice(0, 12);
    this.activityState.set(next);
    writeStorage(ACTIVITY_STORAGE_KEY, next, 'session');
  }

  clearActivity(): void {
    this.activityState.set([]);
    removeStorage(ACTIVITY_STORAGE_KEY, 'session');
  }

  updatePreferences(preferences: Partial<WorkspacePreferences>): void {
    const next: WorkspacePreferences = {
      recommendationLimit: clamp(
        preferences.recommendationLimit ??
          this.preferencesState().recommendationLimit,
        1,
        50,
      ),
      candidatePoolSize: clamp(
        preferences.candidatePoolSize ??
          this.preferencesState().candidatePoolSize,
        1,
        200,
      ),
      preferredCategory: normalizeCategory(
        preferences.preferredCategory ??
          this.preferencesState().preferredCategory,
      ),
      allowRemoteAnalysis:
        preferences.allowRemoteAnalysis ??
        this.preferencesState().allowRemoteAnalysis,
    };
    this.preferencesState.set(next);
    writeStorage(PREFERENCES_STORAGE_KEY, next, 'local');
  }

  applyServerSettings(settings: UserSettings): void {
    const next: WorkspacePreferences = {
      recommendationLimit: clamp(settings.recommendation_limit, 1, 50),
      candidatePoolSize: clamp(settings.candidate_pool_size, 1, 200),
      preferredCategory: normalizeCategory(settings.preferred_category ?? ''),
      allowRemoteAnalysis: settings.allow_remote_analysis,
    };
    this.preferencesState.set(next);
    removeStorage(PREFERENCES_STORAGE_KEY, 'local');
  }

  resetPreferences(): void {
    this.preferencesState.set(DEFAULT_PREFERENCES);
    removeStorage(PREFERENCES_STORAGE_KEY, 'local');
  }

  clearWorkspace(): void {
    this.clearCv();
    this.clearActivity();
    this.resetPreferences();
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) {
    return minimum;
  }
  return Math.min(maximum, Math.max(minimum, Math.round(value)));
}

function isStoredCv(value: unknown): value is StoredCv {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const candidate = value as Partial<StoredCv>;
  return (
    (candidate.resumeText === null ||
      typeof candidate.resumeText === 'string') &&
    typeof candidate.updatedAt === 'string' &&
    Boolean(candidate.profile) &&
    typeof candidate.profile === 'object'
  );
}

function isActivityArray(value: unknown): value is RecentActivity[] {
  return (
    Array.isArray(value) &&
    value.every(
      (entry) =>
        entry &&
        typeof entry === 'object' &&
        typeof entry.id === 'string' &&
        typeof entry.title === 'string' &&
        typeof entry.detail === 'string' &&
        typeof entry.occurredAt === 'string',
    )
  );
}

function readActivity(): RecentActivity[] {
  const value = readStorage<unknown>(ACTIVITY_STORAGE_KEY, null, 'session');
  return isActivityArray(value) ? value.slice(0, 12) : [];
}

function readPreferences(): WorkspacePreferences {
  const value = readStorage<Partial<WorkspacePreferences> | null>(
    PREFERENCES_STORAGE_KEY,
    null,
    'local',
  );
  if (!value || typeof value !== 'object') {
    return { ...DEFAULT_PREFERENCES };
  }

  return {
    recommendationLimit: clamp(
      typeof value.recommendationLimit === 'number'
        ? value.recommendationLimit
        : DEFAULT_PREFERENCES.recommendationLimit,
      1,
      50,
    ),
    candidatePoolSize: clamp(
      typeof value.candidatePoolSize === 'number'
        ? value.candidatePoolSize
        : DEFAULT_PREFERENCES.candidatePoolSize,
      1,
      200,
    ),
    preferredCategory: normalizeCategory(
      typeof value.preferredCategory === 'string'
        ? value.preferredCategory
        : DEFAULT_PREFERENCES.preferredCategory,
    ),
    allowRemoteAnalysis:
      typeof value.allowRemoteAnalysis === 'boolean'
        ? value.allowRemoteAnalysis
        : DEFAULT_PREFERENCES.allowRemoteAnalysis,
  };
}

function normalizeCategory(value: string): string {
  return value.trim().slice(0, 120);
}

function storageFor(kind: 'local' | 'session'): Storage | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return kind === 'local' ? window.localStorage : window.sessionStorage;
}

function readStorage<T>(key: string, fallback: T, kind: 'local' | 'session'): T {
  try {
    const value = storageFor(kind)?.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStorage(
  key: string,
  value: unknown,
  kind: 'local' | 'session',
): void {
  try {
    storageFor(kind)?.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be unavailable or full; the in-memory state still works.
  }
}

function removeStorage(key: string, kind: 'local' | 'session'): void {
  try {
    storageFor(kind)?.removeItem(key);
  } catch {
    // Ignore unavailable storage.
  }
}
