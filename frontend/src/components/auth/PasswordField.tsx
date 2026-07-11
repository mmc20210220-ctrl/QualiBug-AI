import { useState, type ChangeEventHandler, type FocusEventHandler, type ReactNode } from 'react';

type PasswordFieldProps = {
  id: string;
  name: string;
  label: string;
  value: string;
  onChange: ChangeEventHandler<HTMLInputElement>;
  onFocus?: FocusEventHandler<HTMLInputElement>;
  onBlur?: FocusEventHandler<HTMLInputElement>;
  placeholder: string;
  autoComplete: 'current-password' | 'new-password';
  invalid?: boolean;
  describedBy?: string;
  action?: ReactNode;
};

function VisibilityIcon({ visible }: { visible: boolean }) {
  return visible ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 3l18 18M10.6 10.7a2 2 0 002.7 2.7M9.9 4.2A10.8 10.8 0 0112 4c5.5 0 9 5 9 5a16.8 16.8 0 01-2.1 2.5M6.2 6.2C4.2 7.5 3 9 3 9s3.5 5 9 5c1.1 0 2.2-.2 3.1-.5" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 12s3.5-5 9-5 9 5 9 5-3.5 5-9 5-9-5-9-5z" />
      <circle cx="12" cy="12" r="2.25" />
    </svg>
  );
}

export function PasswordField({
  id,
  name,
  label,
  value,
  onChange,
  onFocus,
  onBlur,
  placeholder,
  autoComplete,
  invalid = false,
  describedBy,
  action,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const toggleLabel = visible ? '隐藏密码' : '显示密码';

  return (
    <div className="login-field">
      <div className="login-label-row">
        <label htmlFor={id}>{label}</label>
        {action}
      </div>
      <div className="login-password-control">
        <input
          id={id}
          name={name}
          className="form-input"
          value={value}
          onChange={onChange}
          onFocus={onFocus}
          onBlur={onBlur}
          placeholder={placeholder}
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
        />
        <button
          type="button"
          className="login-password-toggle"
          aria-label={toggleLabel}
          aria-pressed={visible}
          onClick={() => setVisible((current) => !current)}
        >
          <VisibilityIcon visible={visible} />
        </button>
      </div>
    </div>
  );
}
