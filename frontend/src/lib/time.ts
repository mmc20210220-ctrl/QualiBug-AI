const BEIJING_TIME_ZONE = 'Asia/Shanghai';

function pad(value: string | number) {
  return String(value).padStart(2, '0');
}

function isNaiveDateTime(raw: string) {
  return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/.test(raw);
}

export function formatBeijingDateTime(value: string | number | Date | null | undefined, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback;

  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return fallback;
    if (isNaiveDateTime(raw)) {
      const [datePart, timePart] = raw.replace('T', ' ').split(' ');
      const [year, month, day] = datePart.split('-');
      const [hour, minute] = timePart.split(':');
      return `${year}-${month}-${day} ${pad(hour)}:${pad(minute)}`;
    }
  }

  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: BEIJING_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);

  const lookup = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day} ${lookup.hour}:${lookup.minute}`;
}

export { BEIJING_TIME_ZONE };
