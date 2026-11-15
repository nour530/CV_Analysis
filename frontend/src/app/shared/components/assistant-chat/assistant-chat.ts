import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { toApiError } from '../../../core/api-error';
import { JobMatchApiService } from '../../../core/api.service';
import { AssistantStatus, ChatMessage } from '../../../core/career.models';
import { IconComponent } from '../icon/icon';

const SUGGESTIONS = [
  'Which jobs fit my CV right now?',
  'How can I raise my ATS score?',
  'What skills do I need for my target role?',
  'Help me prepare for an interview',
];

/** Escape HTML, then allow **bold**, bullet lists and http(s) links. */
export function renderMessage(text: string): string {
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  const lines = escaped.split('\n');
  const out: string[] = [];
  let inList = false;
  for (const line of lines) {
    const bullet = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (inList) {
      out.push('</ul>');
      inList = false;
    }
    out.push(line.trim() ? `<p>${inline(line)}</p>` : '');
  }
  if (inList) {
    out.push('</ul>');
  }
  return out.join('');
}

function inline(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(
      /(https?:\/\/[^\s<)]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

@Component({
  selector: 'app-assistant-chat',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, IconComponent],
  templateUrl: './assistant-chat.html',
  styleUrl: './assistant-chat.css',
})
export class AssistantChatComponent {
  private readonly api = inject(JobMatchApiService);
  private readonly destroyRef = inject(DestroyRef);

  @ViewChild('scroller') private scroller?: ElementRef<HTMLElement>;
  @ViewChild('input') private input?: ElementRef<HTMLTextAreaElement>;

  readonly open = signal(false);
  readonly status = signal<AssistantStatus | null>(null);
  readonly messages = signal<ChatMessage[]>([]);
  readonly draft = signal('');
  readonly sending = signal(false);
  readonly error = signal('');
  readonly loaded = signal(false);
  readonly suggestions = SUGGESTIONS;
  readonly canSend = computed(
    () => !!this.status()?.enabled && !this.sending() && this.draft().trim().length > 0,
  );
  readonly render = renderMessage;

  toggle(): void {
    this.open.update((value) => !value);
    if (this.open() && !this.loaded()) {
      this.load();
    }
    if (this.open()) {
      setTimeout(() => this.input?.nativeElement.focus(), 50);
    }
  }

  close(): void {
    this.open.set(false);
  }

  private load(): void {
    this.loaded.set(true);
    this.api
      .getAssistantStatus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (status) => this.status.set(status),
        error: () => this.status.set({ enabled: false, personalised: false, reason: 'The assistant could not be reached.', provider: '' }),
      });
    this.api
      .getAssistantHistory()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (history) => {
          this.messages.set(history.messages);
          this.scrollToEnd();
        },
        error: () => undefined,
      });
  }

  setDraft(event: Event): void {
    this.draft.set((event.target as HTMLTextAreaElement).value);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  ask(question: string): void {
    this.draft.set(question);
    this.send();
  }

  send(): void {
    const message = this.draft().trim();
    if (!message || this.sending() || !this.status()?.enabled) {
      return;
    }
    this.sending.set(true);
    this.error.set('');
    const pending: ChatMessage = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: message,
      created_at: new Date().toISOString(),
    };
    this.messages.update((items) => [...items, pending]);
    this.draft.set('');
    if (this.input) {
      this.input.nativeElement.value = '';
    }
    this.scrollToEnd();
    this.api
      .sendAssistantMessage(message)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (reply) => {
          this.messages.update((items) => [
            ...items.filter((item) => item.id !== pending.id),
            reply.user_message,
            reply.reply,
          ]);
          this.sending.set(false);
          this.scrollToEnd();
        },
        error: (error: unknown) => {
          this.messages.update((items) => items.filter((item) => item.id !== pending.id));
          this.draft.set(message);
          this.error.set(toApiError(error).message);
          this.sending.set(false);
        },
      });
  }

  clear(): void {
    if (!confirm('Delete this conversation?')) {
      return;
    }
    this.api
      .clearAssistantHistory()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: () => this.messages.set([]), error: () => undefined });
  }

  private scrollToEnd(): void {
    setTimeout(() => {
      const element = this.scroller?.nativeElement;
      if (element) {
        element.scrollTop = element.scrollHeight;
      }
    }, 30);
  }
}
