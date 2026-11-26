import { firstValueFrom, Observable } from 'rxjs';

export type SaveResult = 'saved' | 'downloaded' | 'cancelled';

interface SaveFilePickerOptions {
  suggestedName?: string;
  types?: { description: string; accept: Record<string, string[]> }[];
}
interface WritableFile {
  write(data: Blob): Promise<void>;
  close(): Promise<void>;
}
interface FileHandle {
  createWritable(): Promise<WritableFile>;
}
type PickerWindow = Window & { showSaveFilePicker?: (options: SaveFilePickerOptions) => Promise<FileHandle> };

export const FILE_TYPES = {
  pdf: { description: 'PDF document', mime: 'application/pdf', extension: '.pdf' },
  docx: {
    description: 'Word document',
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    extension: '.docx',
  },
  json: { description: 'JSON file', mime: 'application/json', extension: '.json' },
} as const;

/**
 * Save a generated file to the user's computer.
 *
 * Where the browser supports it (Chrome, Edge), a "Save as" dialog opens
 * first so the user picks the folder and name; the file is then fetched and
 * written there. Elsewhere it is downloaded directly to the browser's
 * download folder. Opening the dialog before fetching keeps the click's user
 * activation, which browsers require for the dialog.
 */
export async function saveFile(
  source: Observable<Blob>,
  filename: string,
  kind: keyof typeof FILE_TYPES,
  win: PickerWindow = window,
): Promise<SaveResult> {
  const type = FILE_TYPES[kind];
  if (typeof win.showSaveFilePicker === 'function') {
    let handle: FileHandle;
    try {
      handle = await win.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: type.description, accept: { [type.mime]: [type.extension] } }],
      });
    } catch (error) {
      if ((error as DOMException)?.name === 'AbortError') {
        return 'cancelled';
      }
      // Blocked picker (e.g. inside an iframe): fall back to a download.
      return downloadBlob(await firstValueFrom(source), filename);
    }
    const blob = await firstValueFrom(source);
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return 'saved';
  }
  return downloadBlob(await firstValueFrom(source), filename);
}

function downloadBlob(blob: Blob, filename: string): SaveResult {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return 'downloaded';
}
