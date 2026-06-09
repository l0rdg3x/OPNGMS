import type { Range } from "./types";

const SPAN_SECONDS: Record<Range, number> = { "1h": 3600, "24h": 86400, "7d": 604800 };
const BUCKET_SECONDS: Record<Range, number> = { "1h": 60, "24h": 300, "7d": 3600 };

export interface RangeParams {
  from: string;
  to: string;
  bucket: number;
}

/** Converts a range preset into the metrics endpoint query params.
 *  The bucket is chosen to stay under the API MAX_POINTS (5000) and keep charts smooth. */
export function rangeToParams(range: Range, now: Date): RangeParams {
  const to = now;
  const from = new Date(now.getTime() - SPAN_SECONDS[range] * 1000);
  return { from: from.toISOString(), to: to.toISOString(), bucket: BUCKET_SECONDS[range] };
}
