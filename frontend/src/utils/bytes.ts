const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

/** Format a byte count with dynamic binary units (e.g. 1536 -> "1.5 KB", 5e9 -> "4.7 GB"). */
export function humanBytes(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "-" : "";
  let v = Math.abs(n);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  // Integers (bytes) show no decimal; scaled values show one.
  const digits = i === 0 ? 0 : 1;
  return `${sign}${v.toFixed(digits)} ${UNITS[i]}`;
}
