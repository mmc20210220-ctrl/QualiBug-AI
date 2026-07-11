# QualiBug Login Page 99-Point Design

## Goal

Raise the QualiBug authentication page from a polished demo to a credible enterprise product surface while preserving its distinctive dark technology identity. The design covers login, workspace registration, and password reset at frontend port 5174 with backend health sourced from port 8088 through the existing `/api` proxy.

## Product Direction

Use the approved **Trusted Technology** direction:

- Retain the two-column stage and the existing animated canvas.
- Reduce decorative scan, glow, and parallax noise so the form and product promise dominate.
- Keep cyan and teal as the brand accents, with restrained gradients and higher text contrast.
- Express value through evidence, reproducibility, acceptance, and release decisions instead of generic AI claims.
- Keep the implementation industry-neutral; no benchmark, customer, or domain-specific copy may appear.

## Component Boundaries

The existing `Login` route remains the orchestration boundary for authentication state and navigation. Focused units provide the reusable behavior:

- `ServiceHealthBadge` owns the service-health lifecycle and presentation.
- `PasswordField` owns label association, password visibility, autocomplete, and accessible toggle state.
- The page keeps the three authentication forms in one route while using shared field and feedback conventions.
- `LoginStageCanvas` remains a decorative background and must not become a source of product or service truth.

No authentication endpoint or backend authentication contract changes are included.

## Observable Service Health

The current static `引擎在线` claim is removed. The badge calls the existing `getHealth()` client and maps only verified API availability:

- Initial request: `正在检查登录服务`.
- A successful payload with top-level `status === "healthy"` and `components.api.status === "healthy"`: `登录服务可用`.
- A rejected request, malformed response, non-healthy top-level status, or non-healthy API component: `登录服务不可用`.

The UI must never turn `configured_unverified` or `not_configured` components into a healthy claim. A failed health request remains visible and emits a structured console error containing the operation and error object. It does not block the user from attempting login; the authentication request remains the authoritative operation and surfaces its own error.

## Visual Hierarchy and Copy

The desktop page uses a 56/44 stage-to-panel balance. The left stage contains:

- Brand mark.
- Verified login-service badge.
- Kicker: `EVIDENCE-DRIVEN QUALITY`.
- Primary promise: `上线前，先看清业务会不会出事`.
- Supporting copy: `把软件风险变成可复现、可验收、可决策的业务结论。`
- Three compact proof cards:
  - `发现真问题` / `验证后再交付`
  - `结论有证据` / `影响与复现可追溯`
  - `发布有依据` / `风险门禁清晰可见`

The authentication panel uses explicit mode language:

- Login: `登录工作区`; primary action `安全登录`.
- Register: `创建工作区`; primary action `创建并进入工作区`.
- Reset: `重置登录密码`; primary action `重置密码并登录`.

The panel trust note states only verifiable frontend behavior: `身份信息通过安全连接提交 · 不在页面保存密码`. It must not make unsupported compliance or encryption claims.

## Form Interaction and Accessibility

- Every input has a stable `id`, `name`, associated `label[for]`, correct `type`, and appropriate `autoComplete` value.
- `忘记密码？` is an independent button outside the password label.
- Password inputs include an accessible show/hide control whose name changes between `显示密码` and `隐藏密码` without altering the value.
- Submit buttons expose understandable loading text and prevent duplicate submission while a request is active.
- Validation errors appear in a visible `role="alert"` region. Field-level invalid states use `aria-invalid` and `aria-describedby` when the error applies to a specific field.
- Success feedback uses `role="status"` and does not replace backend confirmation.
- All interactive controls have visible keyboard focus.
- Decorative visuals are hidden from assistive technology.

Existing password rules remain authoritative: non-empty required fields, password confirmation equality, and a minimum password length of eight for registration and reset.

## Responsive and Motion Behavior

- At 1280px and wider, the full two-column stage is shown without horizontal overflow.
- At tablet widths, the stage becomes more compact and the panel retains a minimum usable form width.
- At 768px and below, content stacks, proof cards are removed or condensed, and the form remains above unnecessary decoration.
- At 390px, the page shows compact brand, health, promise, and the complete active form without horizontal overflow.
- Touch controls are at least 44px high where practical.
- `prefers-reduced-motion: reduce` disables nonessential canvas animation, parallax, scan, orb, and transition effects.

## Failure Behavior

- Health-check failure is visible as unavailable and logged; it is never swallowed or presented as healthy.
- Authentication errors preserve the existing humanized customer-facing messages while raw failure categories remain observable through the request path.
- No automatic retry is added to login, registration, or password reset because a write may have been accepted.
- Switching modes clears stale success, error, confirmation-password, and password-visibility state.

## Verification

Automated browser checks must demonstrate:

- Login, registration, and reset modes expose the correct headings and actions.
- Labels resolve to exactly one input.
- The forgot-password control has its own accessible name.
- Show/hide password changes the input type and accessible name without changing the value.
- Health states cover checking, verified available, and unavailable.
- Duplicate submit is disabled while the request is active.
- Login errors use an alert region.
- 390px, 768px, 1280px, and 1440px viewports have no horizontal overflow.

The implementation is complete only after frontend lint, TypeScript checking, production build, automated browser checks, and live visual inspection pass without console errors. A live `/api/health` failure must be reported as unavailable rather than reclassified.

## Non-Goals

- Changing backend authentication behavior or credentials.
- Claiming LLM, execution-engine, or external-service health from the API process health check.
- Adding social login, SSO, CAPTCHA, analytics, or new dependencies unrelated to the approved page.
- Encoding benchmark or industry-specific language.
