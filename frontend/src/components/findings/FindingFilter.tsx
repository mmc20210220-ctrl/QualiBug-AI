interface FilterOption {
  label: string;
  value: string;
}

interface FindingFilterProps {
  filters: FilterOption[];
  active: string;
  onChange: (value: string) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
}

export function FindingFilter({ filters, active, onChange, searchQuery, onSearchChange }: FindingFilterProps) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div className="findings-filter-bar">
        {filters.map((f) => (
          <button
            key={f.value}
            className={`findings-filter-btn${active === f.value ? ' active' : ''}`}
            onClick={() => onChange(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <input
        type="search"
        aria-label="搜索问题标题、模块"
        placeholder="搜索问题标题、模块..."
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        style={{
          width: '100%',
          maxWidth: 360,
          padding: '8px 14px',
          borderRadius: 8,
          border: '1px solid var(--line)',
          fontSize: 13,
          background: 'var(--surface)',
          outline: 'none',
        }}
      />
    </div>
  );
}
