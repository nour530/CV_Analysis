import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../../core/api.service';
import { AssistantStatus } from '../../../core/career.models';
import { AssistantChatComponent, renderMessage } from './assistant-chat';

describe('renderMessage', () => {
  it('escapes HTML and formats bold, lists and links', () => {
    const html = renderMessage('**Tip**: try this\n- one <script>x</script>\n- two\nSee https://example.com/jobs');
    expect(html).toContain('<strong>Tip</strong>');
    expect(html).toContain('<ul><li>one &lt;script&gt;x&lt;/script&gt;</li><li>two</li></ul>');
    expect(html).toContain('<a href="https://example.com/jobs" target="_blank" rel="noopener noreferrer">');
    expect(html).not.toContain('<script>');
  });

  it('does not turn javascript: text into links', () => {
    expect(renderMessage('javascript:alert(1)')).not.toContain('<a ');
  });
});

describe('AssistantChatComponent', () => {
  let fixture: ComponentFixture<AssistantChatComponent>;
  let chat: AssistantChatComponent;
  const api = {
    getAssistantStatus: vi.fn(() => of<AssistantStatus>({ enabled: true, personalised: false, reason: null, provider: 'Anthropic Claude' })),
    getAssistantHistory: vi.fn(() => of({ messages: [] })),
    sendAssistantMessage: vi.fn((message: string) =>
      of({
        user_message: { id: 'u1', role: 'user', content: message, created_at: '2026-11-15T10:00:00' },
        reply: { id: 'a1', role: 'assistant', content: 'Here are **3 jobs**.', created_at: '2026-11-15T10:00:01' },
        tools_used: ['search_jobs'],
        personalised: false,
      }),
    ),
    clearAssistantHistory: vi.fn(() => of(undefined)),
  };

  beforeEach(async () => {
    Object.values(api).forEach((mock) => mock.mockClear());
    await TestBed.configureTestingModule({
      imports: [AssistantChatComponent],
      providers: [provideRouter([]), { provide: JobMatchApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(AssistantChatComponent);
    chat = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('loads status and history only when opened', () => {
    expect(api.getAssistantStatus).not.toHaveBeenCalled();
    chat.toggle();
    fixture.detectChanges();
    expect(api.getAssistantStatus).toHaveBeenCalledTimes(1);
    expect(fixture.nativeElement.textContent).toContain('General advice only');
    expect(fixture.nativeElement.textContent).toContain('Which jobs fit my CV right now?');
  });

  it('sends a suggested question and shows the formatted reply', () => {
    chat.toggle();
    fixture.detectChanges();
    chat.ask('Which jobs fit my CV right now?');
    fixture.detectChanges();
    expect(api.sendAssistantMessage).toHaveBeenCalledWith('Which jobs fit my CV right now?');
    expect(chat.messages().map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(fixture.nativeElement.querySelector('.msg--assistant strong').textContent).toBe('3 jobs');
  });

  it('restores the draft and shows an error when sending fails', () => {
    api.sendAssistantMessage.mockImplementationOnce(() => throwError(() => new Error('down')));
    chat.toggle();
    fixture.detectChanges();
    chat.draft.set('Hello');
    chat.send();
    fixture.detectChanges();
    expect(chat.messages()).toEqual([]);
    expect(chat.draft()).toBe('Hello');
    expect(chat.error()).not.toBe('');
  });

  it('disables input when the assistant is off', () => {
    api.getAssistantStatus.mockImplementationOnce(() =>
      of({ enabled: false, personalised: false, reason: 'The career assistant is turned off on this server.', provider: '' }),
    );
    chat.toggle();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('turned off');
    expect(fixture.nativeElement.querySelector('textarea').disabled).toBe(true);
  });
});
