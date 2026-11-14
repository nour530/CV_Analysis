import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export type IconName =
  | 'dashboard'
  | 'cv'
  | 'jobs'
  | 'matches'
  | 'settings'
  | 'menu'
  | 'close'
  | 'upload'
  | 'search'
  | 'arrow-right'
  | 'check'
  | 'alert'
  | 'refresh'
  | 'user'
  | 'external'
  | 'shield'
  | 'chevron-left'
  | 'chevron-right'
  | 'info'
  | 'sparkle'
  | 'edit'
  | 'bookmark'
  | 'bookmark-filled'
  | 'sun'
  | 'moon'
  | 'monitor'
  | 'target'
  | 'trash';

const ICON_PATHS: Record<IconName, string[]> = {
  dashboard: [
    'M4 4h6v6H4z',
    'M14 4h6v6h-6z',
    'M4 14h6v6H4z',
    'M14 14h6v6h-6z',
  ],
  cv: ['M7 3.5h7l3.5 3.5v13.5H7z', 'M14 3.5V7h3.5', 'M10 12h5', 'M10 16h5'],
  jobs: ['M4 8.5h16v10.5H4z', 'M9 8.5V6h6v2.5', 'M4 13h16', 'M10 13v2h4v-2'],
  matches: [
    'M12 3.5l1.6 4.1 4.1 1.6-4.1 1.6L12 15l-1.6-4.2L6.3 9.2l4.1-1.6z',
    'M18 15l.8 1.9 1.9.8-1.9.8L18 20.5l-.8-2-1.9-.8 1.9-.8z',
  ],
  settings: [
    'M4 7h10',
    'M18 7h2',
    'M4 17h2',
    'M10 17h10',
    'M16 5v4',
    'M8 15v4',
  ],
  menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  upload: ['M12 16V4', 'M7 9l5-5 5 5', 'M4 16v3h16v-3'],
  search: ['M10.5 4.5a6 6 0 1 0 0 12 6 6 0 0 0 0-12z', 'M15 15l5 5'],
  'arrow-right': ['M5 12h14', 'M13 6l6 6-6 6'],
  check: ['M5 12.5l4 4L19 7'],
  alert: ['M12 3.5L21 20H3z', 'M12 9v5', 'M12 17h.01'],
  refresh: ['M20 12a8 8 0 1 1-2.3-5.7', 'M20 4v5h-5'],
  user: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4.5 20a7.5 7.5 0 0 1 15 0'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M19 14v6H4V5h6'],
  shield: ['M12 3l7 3v5c0 4.4-3 8-7 10-4-2-7-5.6-7-10V6z', 'M9 12l2 2 4-4'],
  'chevron-left': ['M15 5l-7 7 7 7'],
  'chevron-right': ['M9 5l7 7-7 7'],
  info: ['M12 11v5', 'M12 8h.01', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z'],
  sparkle: [
    'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
    'M18 16l.7 1.8 1.8.7-1.8.7L18 21l-.7-1.8-1.8-.7 1.8-.7z',
  ],
  edit: ['M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z', 'M13.5 6.5l4 4'],
  bookmark: ['M6.5 3.5h11v17L12 16.5l-5.5 4z'],
  'bookmark-filled': ['M6.5 3.5h11v17L12 16.5l-5.5 4z', 'M9 7h6', 'M9 10h6'],
  sun: [
    'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z',
    'M12 2.5v2', 'M12 19.5v2', 'M2.5 12h2', 'M19.5 12h2',
    'M5.3 5.3l1.4 1.4', 'M17.3 17.3l1.4 1.4', 'M5.3 18.7l1.4-1.4', 'M17.3 6.7l1.4-1.4',
  ],
  moon: ['M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'],
  monitor: ['M3.5 5h17v11h-17z', 'M9 20h6', 'M12 16v4'],
  target: ['M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z', 'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z', 'M12 11.5v1'],
  trash: ['M4.5 7h15', 'M9.5 7V4.5h5V7', 'M6.5 7l1 13h9l1-13', 'M10.5 11v5', 'M13.5 11v5'],
};

@Component({
  selector: 'app-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg
      [attr.width]="size()"
      [attr.height]="size()"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      [attr.stroke-width]="strokeWidth()"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      @for (path of paths(); track $index) {
        <path [attr.d]="path" />
      }
    </svg>
  `,
  styleUrl: './icon.css',
})
export class IconComponent {
  readonly name = input.required<IconName>();
  readonly size = input(20);
  readonly strokeWidth = input(1.8);
  readonly paths = computed(() => ICON_PATHS[this.name()] ?? []);
}
