import { SettingsAccessibilitySection } from './SettingsAccessibilitySection';
import { SettingsBrowserMatrixSection } from './SettingsBrowserMatrixSection';
import { SettingsComplexInteractionSection } from './SettingsComplexInteractionSection';
import { SettingsUploadFixtureSection } from './SettingsUploadFixtureSection';
import { SettingsUploadScenarioSection } from './SettingsUploadScenarioSection';

type SettingsInfoSectionProps = {
  productVersion: string;
  serviceStatus: string;
  auditStatus: string;
  statusToneClass: string;
};

export function SettingsInfoSection({ productVersion, serviceStatus, auditStatus, statusToneClass }: SettingsInfoSectionProps) {
  return (
    <details className="section-card settings-span-2">
      <summary>
        <strong>内部治理与兼容工具</strong>
        <span className="muted">正常客户流程不需要维护</span>
      </summary>
      <div className="settings-card-note settings-mt-10">
        浏览器矩阵、无障碍规则、上传 Fixture、上传场景和复杂交互合同应优先由后台从企业资料、页面结构和执行轨迹自动生成。
        这里仅保留给自动识别失败、历史项目迁移或安全审计排查，不作为开始验证前的必填步骤。
      </div>

      <div className="section-card settings-mt-10">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">只读诊断</span>
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
      <SettingsUploadFixtureSection />
      <SettingsUploadScenarioSection />
      <SettingsComplexInteractionSection />
    </details>
  );
}
