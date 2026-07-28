export type ComplexInteractionKind = 'upload' | 'download' | 'popup' | 'iframe-click';

export type ComplexInteractionInput = {
  kind: ComplexInteractionKind;
  selector: string;
  fileRef?: string;
  expectedUrl?: string;
  expectedSha256?: string;
  frameSelector?: string;
  frameOrigin?: string;
};

export type ComplexInteractionStep = Record<string, unknown> & {
  phase: 'treatment' | 'cleanup';
  action: string;
  selector: string;
};

function required(value: string | undefined, label: string): string {
  const normalized = String(value || '').trim();
  if (!normalized) {
    throw new Error(`${label}不能为空。`);
  }
  return normalized;
}

function exactOrigin(value: string | undefined): string {
  const normalized = required(value, 'iframe origin').replace(/\/$/, '');
  const parsed = new URL(normalized);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin !== normalized) {
    throw new Error('iframe origin 必须是纯 HTTP(S) origin，不能包含路径、查询或片段。');
  }
  return normalized;
}

function frameFields(input: ComplexInteractionInput): Record<string, string> {
  const frameSelector = String(input.frameSelector || '').trim();
  const frameOrigin = String(input.frameOrigin || '').trim();
  if (!frameSelector && !frameOrigin) {
    return {};
  }
  return {
    frame_selector: required(frameSelector, 'iframe selector'),
    frame_origin: exactOrigin(frameOrigin),
  };
}

export function buildComplexInteractionStep(input: ComplexInteractionInput): ComplexInteractionStep {
  const selector = required(input.selector, '目标 selector');
  const scoped = frameFields(input);
  if (input.kind === 'upload') {
    return {
      phase: 'treatment',
      action: 'set_input_files',
      selector,
      file_refs: [required(input.fileRef, '运行时文件引用')],
      ...scoped,
    };
  }
  if (input.kind === 'download') {
    const expectedSha = String(input.expectedSha256 || '').trim().toLowerCase();
    if (expectedSha && !/^[0-9a-f]{64}$/.test(expectedSha)) {
      throw new Error('期望 SHA-256 必须是 64 位小写十六进制。');
    }
    return {
      phase: 'treatment',
      action: 'click_download',
      selector,
      max_download_bytes: 50_000_000,
      delete_after_observation: true,
      ...(expectedSha ? { expected_sha256: expectedSha } : {}),
      ...scoped,
    };
  }
  if (input.kind === 'popup') {
    return {
      phase: 'treatment',
      action: 'click_popup',
      selector,
      expected_url: required(input.expectedUrl, '弹窗期望 URL'),
      close_after_observation: true,
      wait_until: 'domcontentloaded',
      ...scoped,
    };
  }
  return {
    phase: 'treatment',
    action: 'click',
    selector,
    frame_selector: required(input.frameSelector, 'iframe selector'),
    frame_origin: exactOrigin(input.frameOrigin),
  };
}

export function buildUploadCleanupStep(
  selector: string,
  frameSelector?: string,
  frameOrigin?: string,
): ComplexInteractionStep {
  return {
    phase: 'cleanup',
    action: 'set_input_files',
    selector: required(selector, '目标 selector'),
    file_refs: [],
    ...frameFields({
      kind: 'upload',
      selector,
      frameSelector,
      frameOrigin,
    }),
  };
}
