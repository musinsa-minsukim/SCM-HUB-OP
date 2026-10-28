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
  colNum("erp_avail_qty", "매장 가용(ERP)", "int", { headerTooltip: "ERP available_stock — 과재고 반출은 이 수량 이내" }),
  { ...colNum("incoming_qty", "이동 입고 예정", "int", { headerTooltip: "허브 → 매장 ERP 재고이동 = 편집샵 보충 요청 + 보충 확정 후 이동중" }), cellStyle: { fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("req_in_qty", "└ 편집샵 보충 요청", "int", { headerTooltip: "이동지시(최근 30일)는 났지만 아직 허브 출고 전" }),
  colNum("moving_in_qty", "└ 편집샵 보충 확정 후 이동중", "int", { headerTooltip: "허브 출고(313) 후 매장 입고(315) 전" }),
  { ...colNum("expected_qty", "예상 재고", "int", { headerTooltip: "매장 현재고 + 이동 입고 예정 − 매장 반납/반출 예정(매입은 아직 0)" }), cellStyle: { fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("hub_qty", "허브 재고", "int", { headerTooltip: "ERP 허브 1000(2000·2010·2020·2060) + 1700(2000) — 하루 1번(10시) 적재. 매입의 MFS 에 해당" }),
  colNum("off_w1", "7일 판매", "int"),
  colNum("off_cum", "누적 판매", "int"),
  { ...colNum("need_qty", "보충 필요", "int", { headerTooltip: "적정 − 예상 재고" }), cellStyle: { fontWeight: 700, textAlign: "right" } } as ColDef,
  { ...colNum("alloc_qty", "허브 배분", "int", { headerTooltip: "허브 재고를 보충 필요에 배분 — 매장 판매(7일 → 4주 → 누적) 좋은 매장부터" }),
    cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("over_qty", "과잉", "int"),
  { ...colNum("ret_qty", "과재고 반출", "int", { headerTooltip: "min(매장 가용, 예상 재고 − 적정)" }), cellStyle: { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("hub1000_qty", "허브1000", "int"),
  colNum("hub1700_qty", "허브1700", "int"),
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

const opsCols: ColDef[] = [
  colText("storage_no", "스토어코드", { pinned: "left", minWidth: 100 }),
  colText("sku_id", "SKU ID", { minWidth: 140 }),
  { ...colText("target_status", "스토어 운영상태", { minWidth: 110 }),
    cellStyle: ((p: any) => (p.value === "미운영" ? { color: "var(--ratio-down)", fontWeight: 700 } : p.value === "운영중" ? { color: "var(--ratio-up)", fontWeight: 700 } : null)) as any },
  colText("store_name", "매장", { minWidth: 150 }),
  colText("uid", "UID", { minWidth: 90 }),
  colText("product_name", "상품명", { minWidth: 220 }),
  colText("size", "사이즈", { minWidth: 70 }),
  colText("storage_status", "현재 SCM 상태", { minWidth: 140 }),
  colNum("erp_stock_qty", "매장 현재고(ERP)", "int"),
  colText("reason", "사유", { minWidth: 180 }),
];
const mvCols: ColDef[] = [
  colText("storage_no", "스토어코드", { pinned: "left", minWidth: 100 }),
  colText("sku_id", "SKU ID", { minWidth: 140 }),
  colText("barcode", "바코드", { minWidth: 150 }),
  { ...colNum("move_qty", "이동수량", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } } as ColDef,
  { ...colNum("ret_qty", "과재고 반출 수량", "int"), cellStyle: { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } } as ColDef,
  colText("store_name", "매장", { minWidth: 150 }),
  colText("uid", "UID", { minWidth: 90 }),
  colText("product_name", "상품명", { minWidth: 220 }),
  colText("size", "사이즈", { minWidth: 70 }),
  colNum("fixed_qty", "매장 적정 수량", "int"),
  colNum("erp_stock_qty", "매장 현재고(ERP)", "int"),
  colNum("incoming_qty", "이동 입고 예정", "int"),
  colNum("expected_qty", "예상 재고", "int"),
  colNum("hub_qty", "허브 재고", "int"),
  colNum("off_w1", "7일 판매", "int"),
  colNum("off_cum", "누적 판매", "int"),
];

function Dl({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500">
      <Download size={14} /> {label}
    </a>
  );
}

export default function Purchase({ dark }: PageProps) {
  const [store, setStore] = useState("");
  const [sev, setSev] = useState("");
  const [flag, setFlag] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const params = { store, severity: sev, flag, q };
  const { data, err, loading } = useApi<any>("/purchase" + qs(params));
  const ops = useApi<any>("/purchase/ops" + qs({ store }));
  const mv = useApi<any>("/purchase/moves" + qs({ store }));
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
            sub="매입 운영 시트('2. 매장별 SKU산정')의 UID × 사이즈별 적정 수량을, 'Y' 표시한 매장마다 SKU ID 로 펼친 표입니다. 매장은 시트 14행 ERP 저장위치 코드로 찾습니다. 재고는 ERP(매장 = MOSS ERP 매장 재고, 허브 = ERP 허브 1000·1700)입니다. 브랜드 시트·전체 새로고침 때 함께 읽습니다. 이동 입고 예정 = 허브→매장 ERP 재고이동(편집샵 보충 요청 + 보충 확정 후 이동중). 보충 필요 = 적정 − 예상 재고, 허브 배분은 판매 좋은 매장부터."
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
          <TotalGrid dark={dark} rows={data?.rows ?? []} columns={cols} height={560} sum={["fixed_qty", "erp_stock_qty", "erp_avail_qty", "incoming_qty", "req_in_qty", "moving_in_qty", "expected_qty", "off_w1", "off_cum", "need_qty", "alloc_qty", "over_qty", "ret_qty"]} />
        </CardBody>
      </Card>
      <Card>
        <CardBody>
          <SectionTitle title={`① 매입 스토어 운영상태 변경 — 운영중 ${num(ops.data?.counts?.["운영중"] ?? 0)} · 미운영 ${num(ops.data?.counts?.["미운영"] ?? 0)}`}
            sub="위탁과 같은 SCM-HUB 양식(스토어코드 / SKU번호 / 스토어 운영상태). 시트 'Y' 인데 SCM 운영중이 아니면 운영중, SCM 운영중인데 시트에 없으면(같은 브랜드 매입 SKU, 시트 매장) 미운영. 재고 보충 등록 전에 먼저 올리세요. 매장 칩 필터가 같이 걸립니다."
            right={<div className="flex items-center gap-2">{ops.loading && <Spinner />}<Dl href={"/api/purchase/ops.csv" + qs({ store })} label="업로드 파일" /></div>} />
          <TotalGrid dark={dark} rows={ops.data?.rows ?? []} columns={opsCols} height={360} sum={["erp_stock_qty"]} />
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`② 매입 재고 보충 등록 ${num(mv.data?.total ?? 0)}줄 — 이동 ${num(mv.data?.move_qty ?? 0)} · 반출 ${num(mv.data?.ret_qty ?? 0)}`}
            sub="위탁과 같은 SCM-HUB 양식(스토어코드 / SKU 번호 / 바코드 / 이동수량 / 과재고 반출 수량). 이동수량 = 허브 배분, 과재고 반출 = min(매장 가용, 예상 재고 − 적정). 같은 SKU 는 묶고 판매(7일 → 4주 → 누적) 좋은 매장이 위."
            right={<div className="flex items-center gap-2">{mv.loading && <Spinner />}<Dl href={"/api/purchase/moves.csv" + qs({ store })} label="업로드 파일" /></div>} />
          <TotalGrid dark={dark} rows={mv.data?.rows ?? []} columns={mvCols} height={420} sum={["move_qty", "ret_qty"]} />
        </CardBody>
      </Card>
    </div>
  );
}
