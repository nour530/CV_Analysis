import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { IconComponent, IconName } from '../icon/icon';

export type StateVariant = 'loading' | 'empty' | 'error' | 'info';

const DEFAULT_ICONS: Record<StateVariant, IconName> = {
  loading: 'refresh',
  empty: 'search',
  error: 'alert',
  info: 'info',
};

@Component({
  selector: 'app-state-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [IconComponent],
  template: `
    <div
      class="state-panel"
      [class.state-panel--compact]="compact()"
      [class.state-panel--error]="variant() === 'error'"
      [attr.role]="variant() === 'error' ? 'alert' : 'status'"
      [attr.aria-live]="variant() === 'error' ? 'assertive' : 'polite'"
    >
      <span
        class="state-panel__icon"
        [class.is-spinning]="variant() === 'loading'"
      >
        <app-icon [name]="icon()" [size]="22" />
      </span>
      <div class="state-panel__copy">
        <h2>{{ title() }}</h2>
        @if (message()) {
          <p>{{ message() }}</p>
        }
      </div>
      @if (actionLabel()) {
        <button type="button" class="btn btn--secondary" (click)="action.emit()">
          {{ actionLabel() }}
        </button>
      }
    </div>
  `,
  styleUrl: './state-panel.css',
})
export class StatePanelComponent {
  readonly variant = input<StateVariant>('info');
  readonly title = input.required<string>();
  readonly message = input('');
  readonly actionLabel = input('');
  readonly compact = input(false);
  readonly icon = computed(() => DEFAULT_ICONS[this.variant()]);
  readonly action = output<void>();
}
