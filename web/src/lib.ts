// API 클라이언트 + 포맷 유틸 (Grid.tsx 가 num/won/compact 를 가져다 쓴다)

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export function qs(p: Record<string, string | string[] | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null) continue;
    const s = Array.isArray(v) ? v.join("|") : v;
    if (s !== "") u.set(k, s);
  }
  const t = u.toString();
  return t ? "?" + t : "";
}

export const num = (n: number) => Math.round(n || 0).toLocaleString("ko-KR");
export const won = (n: number) => num(n) + "원";
export function compact(n: number): string {
  const a = Math.abs(n || 0);
  if (a >= 1e8) return (n / 1e8).toFixed(1) + "억";
  if (a >= 1e4) return (n / 1e4).toFixed(0) + "만";
  return num(n);
}

export type Row = Record<string, any>;

// 확인 사항 → 심각도 색
export const SEV_STYLE: Record<string, string> = {
  error: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-950/60 dark:text-rose-300 dark:ring-rose-900",
  warn: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/60 dark:text-amber-300 dark:ring-amber-900",
  info: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-950/60 dark:text-sky-300 dark:ring-sky-900",
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:ring-emerald-900",
};
export const SEV_LABEL: Record<string, string> = { error: "오류", warn: "확인", info: "참고", ok: "정상" };
