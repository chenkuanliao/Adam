export function parseControlNumber(raw: string, min: number, max: number, integer = false): number | null {
  const text = integer ? raw.trim() : raw.trim().replace(/%$/, '').trim();
  if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)) return null;
  const value = Number(text);
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) return null;
  return value;
}
