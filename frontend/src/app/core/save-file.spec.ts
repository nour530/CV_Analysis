import { of } from 'rxjs';
import { vi } from 'vitest';
import { saveFile } from './save-file';

describe('saveFile', () => {
  it('asks where to save first, then writes the file there', async () => {
    const write = vi.fn(async () => undefined);
    const close = vi.fn(async () => undefined);
    const picker = vi.fn(async () => ({ createWritable: async () => ({ write, close }) }));
    const blob = new Blob(['%PDF']);

    const result = await saveFile(of(blob), 'Amira_CV.pdf', 'pdf', { showSaveFilePicker: picker } as never);

    expect(result).toBe('saved');
    expect(picker).toHaveBeenCalledWith({
      suggestedName: 'Amira_CV.pdf',
      types: [{ description: 'PDF document', accept: { 'application/pdf': ['.pdf'] } }],
    });
    expect(write).toHaveBeenCalledWith(blob);
    expect(close).toHaveBeenCalled();
  });

  it('does nothing when the user cancels the dialog', async () => {
    const source = vi.fn(() => of(new Blob(['x'])));
    const picker = vi.fn(async () => {
      throw new DOMException('cancelled', 'AbortError');
    });
    const result = await saveFile(source(), 'cv.docx', 'docx', { showSaveFilePicker: picker } as never);
    expect(result).toBe('cancelled');
  });

  it('downloads directly when the browser has no save dialog', async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:cv'), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const result = await saveFile(of(new Blob(['x'])), 'cv.docx', 'docx', {} as never);
    expect(result).toBe('downloaded');
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });
});
