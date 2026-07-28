import { SettingsAccessibilitySection } from './SettingsAccessibilitySection';
import { SettingsBrowserMatrixSection } from './SettingsBrowserMatrixSection';

type SettingsInfoSectionProps = {
  productVersion: string;
  serviceStatus: string;
  auditStatus: string;
  statusToneClass: string;
};

export function SettingsInfoSection({ productVersion, serviceStatus, auditStatus, statusToneClass }: SettingsInfoSectionProps) {
  return (
    <>
      <div className="section-card">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">运行状态</span>
            <h2>系统信息</h2>
          </div>
        </div>
        <div className="settings-info-list">
          {[
            { label: '产品版本', value: productVersion },
            { label: '服务状态', value: serviceStatus },
            { label: '审计链路', value: auditStatus },
          ].map((item) => (
            <div key={item.label} className="settings-info-row">
              <span>{item.label}</span>
              <strong className={statusToneClass}>{item.value}</strong>
            </div>
          ))}
        </div>
      </div>
      <SettingsBrowserMatrixSection />
      <SettingsAccessibilitySection />
    </>
  );
}
