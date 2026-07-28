export type BrowserMatrixProfile = {
  profile_id: string;
  browser_engine: 'chromium' | 'firefox' | 'webkit';
  device_class: 'desktop' | 'tablet' | 'mobile';
  viewport_width: number;
  viewport_height: number;
  device_scale_factor: number;
  is_mobile: boolean;
  has_touch: boolean;
  locale: string;
  timezone_id: string;
  color_scheme: 'light' | 'dark' | 'no-preference';
  reduced_motion: 'reduce' | 'no-preference';
  user_agent: string;
};

export type BrowserMatrixContract = {
  schema_version: 'qualibug.ui-browser-matrix.v1';
  aggregation_policy: 'all_profiles_must_pass';
  profiles: BrowserMatrixProfile[];
};

export const BROWSER_MATRIX_PRESETS: Array<BrowserMatrixProfile & {
  label: string;
  note: string;
  enabledByDefault: boolean;
}> = [
  {
    label: 'Chromium 桌面端', note: 'Chromium 内核桌面环境', enabledByDefault: true,
    profile_id: 'chromium-desktop-1280', browser_engine: 'chromium', device_class: 'desktop',
    viewport_width: 1280, viewport_height: 720, device_scale_factor: 1,
    is_mobile: false, has_touch: false, locale: 'zh-CN', timezone_id: 'Asia/Shanghai',
    color_scheme: 'light', reduced_motion: 'no-preference', user_agent: '',
  },
  {
    label: 'Firefox 桌面端', note: 'Gecko 内核兼容性验证', enabledByDefault: true,
    profile_id: 'firefox-desktop-1280', browser_engine: 'firefox', device_class: 'desktop',
    viewport_width: 1280, viewport_height: 720, device_scale_factor: 1,
    is_mobile: false, has_touch: false, locale: 'zh-CN', timezone_id: 'Asia/Shanghai',
    color_scheme: 'light', reduced_motion: 'no-preference', user_agent: '',
  },
  {
    label: 'WebKit 移动端', note: '触摸、移动视口与 WebKit 行为', enabledByDefault: true,
    profile_id: 'webkit-mobile-390', browser_engine: 'webkit', device_class: 'mobile',
    viewport_width: 390, viewport_height: 844, device_scale_factor: 3,
    is_mobile: true, has_touch: true, locale: 'zh-CN', timezone_id: 'Asia/Shanghai',
    color_scheme: 'light', reduced_motion: 'no-preference', user_agent: '',
  },
  {
    label: 'Chromium 平板端', note: '触摸输入与中等宽度布局', enabledByDefault: false,
    profile_id: 'chromium-tablet-820', browser_engine: 'chromium', device_class: 'tablet',
    viewport_width: 820, viewport_height: 1180, device_scale_factor: 2,
    is_mobile: false, has_touch: true, locale: 'zh-CN', timezone_id: 'Asia/Shanghai',
    color_scheme: 'light', reduced_motion: 'no-preference', user_agent: '',
  },
];

export function buildBrowserMatrixContract(profileIds: string[]): BrowserMatrixContract {
  const selected = new Set(profileIds);
  const profiles = BROWSER_MATRIX_PRESETS
    .filter((profile) => selected.has(profile.profile_id))
    .map(({ label: _label, note: _note, enabledByDefault: _enabled, ...profile }) => profile);
  if (profiles.length < 2 || profiles.length > 12) {
    throw new Error('浏览器矩阵必须包含 2–12 个唯一 profile。');
  }
  return {
    schema_version: 'qualibug.ui-browser-matrix.v1',
    aggregation_policy: 'all_profiles_must_pass',
    profiles,
  };
}
