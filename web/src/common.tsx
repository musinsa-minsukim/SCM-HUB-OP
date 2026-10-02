import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api, num, qs, SEV_LABEL, SEV_STYLE } from "./lib";
import { Card, MultiSelect } from "./ui";
import DataGrid, { colNum, colText, type ColDef } from "./Grid";
import type { Nav } from "./App";

export type PageProps = { dark: boolean; nav: Nav; preset: { brand?: string; store?: string; flag?: string } };

export function useApi<T = any>(path: string | null): { data: T | null; err: string; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setLoading(true); setErr("");
    api<T>(path).then((d) => live && setData(d)).catch((e) => live && setErr(String(e.message || e)))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [path]);
  return { data, err, loading };
}

export function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "bad" | "warn" | "good" }) {
  const c = tone === "bad" ? "text-rose-600 dark:text-rose-400" : tone === "warn" ? "text-amber-600 dark:text-amber-400"
    : tone === "good" ? "text-emerald-600 dark:text-emerald-400" : "text-slate-900 dark:text-slate-50";
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${c}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-400">{sub}</div>}
    </Card>
  );
}

export function SevBadge({ s }: { s: string }) {
  return <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${SEV_STYLE[s] ?? ""}`}>{SEV_LABEL[s] ?? s}</span>;
}

export function ErrorBox({ msg }: { msg: string }) {
  if (!msg) return null;
  return <Card className="border-rose-200 p-4 text-sm text-rose-700 dark:border-rose-900 dark:text-rose-300">{msg}</Card>;
}

/** 브랜드·매장 필터 (옵션은 /api/options) */
export function Filters({ brand, setBrand, store, setStore, md, setMd, extra }: {
  brand: string[]; setBrand: (v: string[]) => void; store: string[]; setStore: (v: string[]) => void;
  md?: string[]; setMd?: (v: string[]) => void; extra?: React.ReactNode;
}) {
  const { data } = useApi<{ brands: string[]; stores: string[]; mds: string[] }>("/options");
  return (
    <div className="flex flex-wrap items-center gap-2">
      {setMd && <MultiSelect label="오프라인 MD" options={data?.mds ?? []} value={md ?? []} onChange={setMd} searchable={false} />}
      <MultiSelect label="브랜드" options={data?.brands ?? []} value={brand} onChange={setBrand} />
      <MultiSelect label="매장" options={data?.stores ?? []} value={store} onChange={setStore} />
      {extra}
    </div>
  );
}

export function CsvButton({ params }: { params: Record<string, any> }) {
  return (
    <a href={"/api/rows.csv" + qs(params)}
      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
      <Download size={14} /> CSV
    </a>
  );
}

// 상품 행 공통 컬럼
const ynStyle = ((p: any) => (!p.data || p.data.__muTotal || !p.value ? null
  : p.value === "Y" ? { color: "var(--ratio-up)", fontWeight: 700 } : { color: "var(--ratio-down)", fontWeight: 700 })) as any;

export const COL = {
  brand: colText("src_file", "브랜드", { pinned: "left", minWidth: 100 }),
  store: colText("store_name", "매장", { minWidth: 140 }),
  sku: colText("sku_id", "SKU ID", { pinned: "left", minWidth: 140 }),
  goods: colText("goods_no", "UID", { minWidth: 96, valueFormatter: (p: any) => uid(p.value) }),
  name: colText("product_name", "상품명", { minWidth: 220 }),
  opt: colText("option_name", "옵션명", { minWidth: 110 }),
  barcode: colText("rep_barcode", "대표 바코드", { minWidth: 130 }),
  otherBc: {
    ...colText("other_barcodes", "추가 바코드", { minWidth: 130 }),
    cellStyle: (p: any) => (p.value ? { color: "var(--ratio-new)", fontWeight: 600 } : undefined),
  } as ColDef,
  sev: { ...colText("severity", "검증", { minWidth: 70 }), cellRenderer: (p: any) => <SevBadge s={p.value} /> } as ColDef,
  flags: colText("flags_text", "확인 사항", { minWidth: 260 }),
  scm: colText("storage_status", "SCM 매장 운영", { minWidth: 120 }),
  fixed: colNum("fixed_qty", "고정 운영 수량", "int"),
  stock: colNum("stock_qty", "매장 현재고", "int"),
  avail: colNum("avail_qty", "판매가능", "int"),
  incoming: colNum("incoming_qty", "이동중 재고", "int", { headerTooltip: "매장으로 들어오는 중: 입고 예정(지시) + 운송 중 + 직납 예정" }),
  outgoing: colNum("outgoing_qty", "매장 반납 예정", "int", { headerTooltip: "매장에서 나가는 중: 출고 예정 + 출고 이동 중 (MFS·반품창고 반납)" }),
  off1: colNum("off_w1", "7일 판매", "int", { headerTooltip: "해당 매장 D-7~D-1 순판매(주문−환불)" }),
  offCum: colNum("off_cum", "누적 판매", "int", { headerTooltip: "해당 매장 전 기간 순판매(주문−환불)" }),
  off4: colNum("off_4w", "매장 판매 4주", "int"),
  offAll: colNum("off_all_4w", "전 매장 4주", "int"),
  onl1: colNum("onl_w1", "온라인 1주(주문)", "int"),
  mfs: colNum("mfs_qty", "MFS 재고", "int", { headerTooltip: "MFS 센터 재고(SKU 단위, 매장 공통 값). 여러 매장에 같은 SKU 가 있으면 같은 값이 반복되므로 합계는 SKU 별 1번만" }),
  mfsIn: colNum("mfs_in_qty", "MFS 입고 예정", "int", { headerTooltip: "브랜드 → MFS 센터 입고 예정(아직 안 들어온 수량). 입고 예정일이 최근 7일 이내이거나 앞으로인 건. SKU 단위" }),
  mfsInDate: colText("mfs_in_date", "MFS 입고 예정일", { minWidth: 110 }),
  mfsLate: colNum("mfs_in_late_qty", "MFS 입고 지연", "int", { headerTooltip: "입고 예정일이 7일 넘게 지났는데 아직 입고되지 않은 수량(정리 안 된 문서 가능성). SKU 단위" }),
  need: colNum("need_qty", "보충 필요", "int"),
  alloc: { ...colNum("alloc_qty", "MFS 배분", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } } as ColDef,
  short: { ...colNum("short_qty", "부족", "int"), cellStyle: (p: any) => (p.value > 0 ? { color: "var(--ratio-down)", fontWeight: 600, textAlign: "right" } : { textAlign: "right" }) } as ColDef,
  over: colNum("over_qty", "과잉", "int"),
  src: colText("src_row", "원본 행", { minWidth: 80 }),
  bzOffline: {
    ...colText("bz_offline_yn", "오프라인 판매(비제스트)", { minWidth: 120, headerTooltip: "비제스트 원장의 상품(UID) 오프라인 판매 여부. 여기서 바꾼 값이 SCM-HUB 로 넘어간다(몇 시간~하루 걸리기도 함)" }),
    cellStyle: ynStyle,
  } as ColDef,
  offline: {
    ...colText("offline_yn", "오프라인 판매(SCM)", { minWidth: 120, headerTooltip: "SCM-HUB 상품(UID) 오프라인 판매 여부 — 실제로 보충 발주를 막는 값. Y 여야 보충 발주 가능 (조건 ①), 조건 ② = SCM 매장 운영중. 비제스트가 Y 인데 여기가 N 이면 아직 안 넘어온 것" }),
    cellStyle: ynStyle,
  } as ColDef,
  otherGoods: colText("other_goods", "다른 연결 UID", { minWidth: 120, headerTooltip: "SCM-HUB 에서 이 SKU 에 함께 연결된 다른 UID. 판매중·오프라인 판매 가능한 UID 를 대표로 골랐다" }),
  storeIn: colText("store_in", "시트 매장명", { minWidth: 140 }),
  storeMatch: colText("store_match_note", "매장 매칭", { minWidth: 200, headerTooltip: "자동 매칭이면 '시트 표기 → 매장', 후보 여럿이면 후보 목록" }),
};

/** UID(상품번호) 표시 — 원천에서 실수형("1343131.0")으로 오는 경우가 있어 정수로 */
export function uid(v: any): string {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.trunc(n)) : String(v);
}

/** 모든 상품 단위 표의 기본 열 — SKU ID · UID · 상품명 · 옵션명 (같은 이름·같은 순서, 2026-10-01 사용자 요청) */
export const PRODUCT_COLS = (): ColDef[] => [{ ...COL.sku, pinned: undefined }, COL.goods, COL.name, COL.opt];

export function totalRow(rows: any[], fields: string[], label = "합계") {
  const t: any = { src_file: label, __muTotal: true };
  for (const f of fields) t[f] = rows.reduce((a, r) => a + (Number(r[f]) || 0), 0);
  return t;
}

export const fmtN = num;

/** SKU 단위 값 — 매장 행마다 같은 값이 반복되므로 합계는 SKU 별 1번만 더한다. */
const SKU_LEVEL = new Set(["mfs_qty", "mfs_inbound_qty", "mfs_in_qty", "mfs_in_late_qty", "off_all_w1", "off_all_4w", "off_all_cum", "onl_w1", "onl_4w"]);

/** 합계행 표 — 표의 **모든 숫자 열**을 자동으로 합계해 rowData 첫 행(__muTotal)으로 넣는다.
 *  Grid 가 정렬해도 맨 위에 고정하고 mu-total 로 강조한다.
 *  (pinnedTopRowData 는 이 Grid 설정에서 값 갱신이 안 되는 한계가 있어 쓰지 않는다)
 *  sum 을 주면 그 열만 합계한다(기본은 숫자 열 전체). */
export function TotalGrid({ rows, sum, ...p }: {
  rows: any[]; sum?: string[]; columns: ColDef[]; dark: boolean; height?: number; onCellClicked?: (e: any) => void;
}) {
  const first = (p.columns[0] as any)?.field ?? "src_file";   // 합계 라벨은 표의 첫 열에
  const fields = sum ?? p.columns.filter((c: any) => c.type === "numericColumn" && c.field).map((c: any) => c.field as string);
  const bySku = rows.length > 0 && "sku_id" in rows[0];
  const t: any = { __muTotal: true };
  for (const f of fields) {
    if (bySku && SKU_LEVEL.has(f)) {
      const seen = new Map<string, number>();
      for (const r of rows) if (!seen.has(r.sku_id)) seen.set(r.sku_id, Number(r[f]) || 0);
      t[f] = [...seen.values()].reduce((a, b) => a + b, 0);
    } else {
      t[f] = rows.reduce((a, r) => a + (Number(r[f]) || 0), 0);
    }
  }
  t[first] = "합계";
  const data = rows.length ? [t, ...rows] : [];
  return <DataGrid rows={data} getRowClass={(r: any) => (r.data?.__muTotal ? "mu-total" : "")} {...p}
    onCellClicked={(e: any) => { if (!e.data?.__muTotal) p.onCellClicked?.(e); }} />;
}
