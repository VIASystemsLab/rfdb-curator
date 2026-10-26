/**
 * Number formatting shared by the record counts, the data-context panel and the
 * file fields.
 */

/** A count with en-US thousands separators; anything not a number shows as 0. */
export function formatCount(value) {
  const n = Number(value ?? 0)
  return Number.isFinite(n) ? n.toLocaleString('en-US') : '0'
}

/** A byte size in B, KB, MB or GB, one decimal above bytes. */
export function formatBytes(bytes) {
  const n = Number(bytes ?? 0)
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  const value = n / 1024 ** i
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`
}
