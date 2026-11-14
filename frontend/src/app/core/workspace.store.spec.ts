import { TestBed } from '@angular/core/testing';
import { PROFILE_RESPONSE } from './testing/fixtures';
import { WorkspaceStore } from './workspace.store';

describe('WorkspaceStore', () => {
  let store: WorkspaceStore;

  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    TestBed.configureTestingModule({});
    store = TestBed.inject(WorkspaceStore);
  });

  it('stores CV text and profile in session state', () => {
    store.setCv('Python and SQL', PROFILE_RESPONSE);

    expect(store.hasCv()).toBe(true);
    expect(store.resumeText()).toBe('Python and SQL');
    expect(store.cvProfile()?.skill_count).toBe(2);
    expect(store.cvUpdatedAt()).toBeTruthy();
  });

  it('restores saved CV data for a new store instance', () => {
    store.setCv('Python and SQL', PROFILE_RESPONSE);

    const restored = new WorkspaceStore();
    expect(restored.hasCv()).toBe(true);
    expect(restored.cvProfile()?.profile.category).toBe('Data Science');
  });

  it('clamps recommendation preferences to API-supported ranges', () => {
    store.updatePreferences({
      recommendationLimit: 999,
      candidatePoolSize: 0,
      preferredCategory: '  Data Science  ',
    });

    expect(store.preferences().recommendationLimit).toBe(50);
    expect(store.preferences().candidatePoolSize).toBe(1);
    expect(store.preferences().preferredCategory).toBe('Data Science');
  });

  it('clears CV, activity, and preferences from the workspace', () => {
    store.setCv('Python and SQL', PROFILE_RESPONSE);
    store.updatePreferences({ recommendationLimit: 5 });
    store.recordActivity({
      type: 'cv_analysis',
      title: 'CV analysed',
      detail: '2 skills',
    });

    expect(store.recentActivity().length).toBe(1);

    store.clearWorkspace();

    expect(store.hasCv()).toBe(false);
    expect(store.recentActivity()).toEqual([]);
    expect(store.preferences().recommendationLimit).toBe(10);
    expect(store.preferences().candidatePoolSize).toBe(40);
  });
});
