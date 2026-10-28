import { useState } from "react";
import { Download } from "lucide-react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { colNum, colText, type ColDef } from "./Grid";
import { ErrorBox, Kpi, TotalGrid, useApi, type PageProps } from "./common";
import { num, qs } from "./lib";

// 매입(무신사 스탠다드 등) 운영 시트 → 매장 × SKU ID 전개 (1단계: 전개·검증). 재고·보충은 2단계에서 ERP 재고로 붙인다.
const sevStyle = ((p: any) => (!p.data || p.data.__muTotal ? null
  : p.value === "error" ? { color: "var(--ratio-down)", fontWeight: 700 }
  : p.value === "warn" ? { color: "#d97706", fontWeight: 700 } : null)) as any;

const cols: ColDef[] = [
  colText("store_name", "매장", { pinned: "left", minWidth: 150 }),
  colText("sku_id", "SKU ID", { minWidth: 140 }),
  colText("uid", "UID", { minWidth: 90 }),
  colText("product_name", "상품명", { minWidth: 220 }),
  colText("size", "사이즈", { minWidth: 70 }),
  { ...colNum("fixed_qty", "매장 적정 수량", "int"), cellStyle: { fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("erp_stock_qty", "매장 현재고(ERP)", "int", { headerTooltip: "MOSS ERP 매장 재고 current_stock (plant 1000, 시트 14행 저장위치)" }),
  colNum("erp_avail_qty", "매장 가용(ERP)", "int", { headerTooltip: "ERP available_stock — 판매가능" }),
  colNum("erp_transfer_qty", "이동중(ERP)", "int", { headerTooltip: "ERP transfer_stock" }),
  colNum("hub1000_qty", "허브1000", "int", { headerTooltip: "ERP 허브 1000 (2000·2010·2020·2060) — 하루 1번(10시) 적재" }),
  colNum("hub1700_qty", "허브1700", "int", { headerTooltip: "ERP 허브 1700 (2000)" }),
  { ...colNum("need_qty", "보충 필요(참고)", "int", { headerTooltip: "적정 수량 − (매장 가용 + ERP 이동중). ERP 재고이동(STO) 진행분은 다음 단계에서 반영" }),
    cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("over_qty", "과잉(참고)", "int"),
  colText("category", "대카테", { minWidth: 80 }),
  colText("storage_status", "SCM 매장 등록", { minWidth: 140, headerTooltip: "SCM-HUB storage_sku 상태 (비어 있으면 등록 없음)" }),
  colText("offline_yn", "오프라인 판매(SCM)", { minWidth: 110 }),
  colText("sales_status", "SCM 판매상태", { minWidth: 100 }),
  { ...colText("severity", "검증", { minWidth: 70 }), cellStyle: sevStyle },
  colText("flags_text", "확인 사항", { minWidth: 220 }),
  colText("uid_sizes", "상품 실제 사이즈", { minWidth: 200, headerTooltip: "SCM-HUB 에 등록된 이 UID 의 사이즈 옵션 — 'SKU 없음' 일 때 비교용" }),
  colText("storage_no", "스토어코드", { minWidth: 100 }),
  colText("lgort", "ERP 저장위치", { minWidth: 90 }),
  colText("store_in", "시트 매장명", { minWidth: 150 }),
  colText("brand_in", "브랜드", { minWidth: 110 }),
  colText("src_row", "원본 행", { minWidth: 70 }),
];

export default function Purchase({ dark }: PageProps) {
  const [store, setStore] = useState("");
  const [sev, setSev] = useState("");
  const [flag, setFlag] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const params = { store, severity: sev, flag, q };
  const { data, err, loading } = useApi<any>("/purchase" + qs(params));
  const m = data?.meta ?? {};
  const s = m.summary ?? {};
  const files: any[] = m.files ?? [];
  const inp = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800";

  return (
    <div className="space-y-5">
      <ErrorBox msg={err || m.error} />
      <Card>
        <CardBody>
          <SectionTitle title="매입 운영 시트 → 매장 × SKU ID"
            sub="매입 운영 시트('2. 매장별 SKU산정')의 UID × 사이즈별 적정 수량을, 'Y' 표시한 매장마다 SKU ID 로 펼친 표입니다. 매장은 시트 14행 ERP 저장위치 코드로 찾습니다. 재고는 ERP(매장 = MOSS ERP 매장 재고, 허브 = ERP 허브 1000·1700)입니다. 브랜드 시트·전체 새로고침 때 함께 읽습니다. 보충 필요는 참고값(ERP 재고이동 진행분 미반영)."
            right={<div className="flex items-center gap-2">{loading && <Spinner />}
              <span className="text-xs text-slate-500" title="ERP 매장 재고 = MOSS sap_inventory 마지막 변경 시각 / 허브 = raw_erp_stock 적재일(하루 1번)">
                시트 {m.at?.slice(5, 16) ?? "—"} · ERP 매장 {s.erp_store_at?.slice(5) || "—"} · 허브 {s.hub_dt?.slice(5) || "—"}</span>
              <a href={"/api/purchase.csv" + qs(params)} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
                <Download size={14} /> CSV</a></div>} />
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Kpi label="매장 × SKU" value={num(s.rows ?? 0)} />
            <Kpi label="UID" value={num(s.uids ?? 0)} />
            <Kpi label="SKU" value={num(s.skus ?? 0)} />
            <Kpi label="운영 매장" value={num(s.stores ?? 0)} />
            <Kpi label="오류 행" value={num(s.by_severity?.error ?? 0)} tone={s.by_severity?.error ? "bad" : undefined} sub="SKU 없음·매장 확인 불가 등" />
          </div>
          <div className="mb-2 text-xs text-slate-500">
            {files.map((f) => <span key={f.file_id} className="mr-3">{f.brand}: {f.status === "ok" ? `${num(f.rows)}행` : <b className="text-rose-600">{f.status} {f.detail}</b>}</span>)}
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <Chip active={!store} onClick={() => setStore("")}>전체 매장</Chip>
            {(data?.by_store ?? []).map((b: any) =>
              <Chip key={b.store_name} active={store === b.store_name} onClick={() => setStore(b.store_name)}>
                {b.store_name.replace("무신사 ", "")} · SKU {num(b.skus)} · 적정 {num(b.pcs)} · 가용 {num(b.avail ?? 0)} · 보충 {num(b.need ?? 0)}</Chip>)}
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {[["", "전체"], ["error", "오류"], ["warn", "주의"], ["info|ok", "정상·참고"]].map(([v, l]) =>
              <Chip key={v} active={sev === v} onClick={() => setSev(v)}>{l}</Chip>)}
            <span className="mx-1 text-slate-300">|</span>
            {Object.entries(data?.flags ?? {}).map(([f, n]) =>
              <Chip key={f} active={flag === f} onClick={() => setFlag(flag === f ? "" : f)}>{f} {num(n as number)}</Chip>)}
            <form onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }} className="ml-auto">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="SKU ID · UID · 상품명" className={inp + " w-60"} />
            </form>
          </div>
          <TotalGrid dark={dark} rows={data?.rows ?? []} columns={cols} height={600} sum={["fixed_qty", "erp_stock_qty", "erp_avail_qty", "erp_transfer_qty", "need_qty", "over_qty"]} />
        </CardBody>
      </Card>
    </div>
  );
}
