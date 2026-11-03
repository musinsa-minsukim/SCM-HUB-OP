import { useState } from "react";
import { Download } from "lucide-react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { colNum, colText, type ColDef } from "./Grid";
import { ErrorBox, Kpi, TotalGrid, useApi, type PageProps } from "./common";
import { num, qs } from "./lib";

// 미운영 재고 반출 — 브랜드 시트에 없는 매장×SKU 인데 매장 재고가 있는 것. 위탁 + 매입, SKU·UID·스타일넘버로 조회.
const cols: ColDef[] = [
  { ...colText("action", "처리", { pinned: "left", minWidth: 80, headerTooltip: "그 SKU 를 시트에서 운영하는 다른 매장이 있으면 RT 가능, 없으면 반출" }),
    cellStyle: ((p: any) => (!p.data || p.data.__muTotal ? null : p.value === "반출" ? { color: "var(--ratio-down)", fontWeight: 700 } : { color: "var(--ratio-up)", fontWeight: 700 })) as any },
  colText("store_name", "매장", { minWidth: 160 }),
  colText("sku_id", "SKU ID", { minWidth: 140 }),
  colText("uid", "UID", { minWidth: 90 }),
  colText("product_name", "상품명", { minWidth: 220 }),
  colText("option_name", "옵션명", { minWidth: 100 }),
  colText("gubun", "구분", { minWidth: 60 }),
  colText("brand", "브랜드", { minWidth: 110 }),
  colNum("stock_qty", "매장 현재고", "int"),
  { ...colNum("avail_qty", "판매가능(반출 가능)", "int", { headerTooltip: "이미 출고 중인 수량은 빠져 있다. 반출 파일에는 이 수량이 들어간다" }), cellStyle: { fontWeight: 700, textAlign: "right" } } as ColDef,
  colNum("outgoing_qty", "매장 반납/반출 예정", "int", { headerTooltip: "이미 진행 중인 반납·반출(위탁만)" }),
  colNum("incoming_qty", "이동 입고 예정", "int", { headerTooltip: "미운영인데 들어오는 중인 수량(위탁만)" }),
  colNum("wh_qty", "MFS·허브 재고", "int", { headerTooltip: "위탁 = MFS 재고, 매입 = ERP 허브 재고" }),
  colNum("off_w1", "7일 판매", "int"),
  colNum("off_cum", "누적 판매", "int"),
  colText("storage_status", "SCM 운영상태", { minWidth: 140 }),
  colText("reason", "사유", { minWidth: 170 }),
  colText("run_stores", "운영 매장", { minWidth: 220 }),
  colText("style_no", "스타일넘버", { minWidth: 110 }),
  colText("small_nm", "카테고리", { minWidth: 100 }),
  colText("storage_no", "스토어코드", { minWidth: 100 }),
];

export default function OffStock({ dark }: PageProps) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [gubun, setGubun] = useState("");
  const [action, setAction] = useState("");
  const [store, setStore] = useState("");
  const [brand, setBrand] = useState("");
  const params = { q, store, brand, gubun, action };
  const { data, err, loading } = useApi<any>("/offstock" + qs(params));
  const inp = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800";
  const btn = "inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500";

  return (
    <div className="space-y-4">
      <ErrorBox msg={err} />
      <Card>
        <CardBody>
          <SectionTitle title="미운영 재고 반출"
            sub="브랜드 시트에 없는 매장×SKU 인데 매장 재고가 남아 있는 것입니다. 위탁은 시트를 정상으로 읽은 브랜드의 대상 매장, 매입은 시트에 Y 가 있는 매장(메가스토어 제외) 기준입니다. 그 SKU 를 운영하는 다른 매장이 있으면 RT 가능, 없으면 반출. 새로고침 때 함께 계산합니다."
            right={<div className="flex items-center gap-2">{loading && <Spinner />}
              <a href={"/api/offstock.csv" + qs(params)} className="text-xs text-slate-500 underline">상세 CSV</a>
              <a href={"/api/offstock.csv" + qs({ ...params, upload: "1" })} className={btn}><Download size={14} /> 반출 업로드 파일</a></div>} />
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="매장×SKU" value={num(data?.total ?? 0)} />
            <Kpi label="매장 현재고" value={num(data?.stock_qty ?? 0)} />
            <Kpi label="반출 (운영 매장 없음)" value={num(data?.ret_qty ?? 0)} tone={data?.ret_qty ? "bad" : undefined} sub="판매가능 기준" />
            <Kpi label="RT 가능 (운영 매장 있음)" value={num(data?.rt_qty ?? 0)} tone="good" sub="판매가능 기준" />
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <form onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }} className="flex gap-2">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="SKU ID · UID · 스타일넘버 · 상품명 (쉼표로 여러 개)" className={inp + " w-80"} />
              <button className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800">조회</button>
            </form>
            <select value={store} onChange={(e) => setStore(e.target.value)} className={inp}>
              <option value="">전체 매장</option>
              {(data?.stores ?? []).map((s: string) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={brand} onChange={(e) => setBrand(e.target.value)} className={inp}>
              <option value="">전체 브랜드</option>
              {(data?.brands ?? []).map((s: string) => <option key={s} value={s}>{s}</option>)}
            </select>
            <span className="mx-1 text-slate-300">|</span>
            {[["", "위탁·매입"], ["위탁", "위탁"], ["매입", "매입"]].map(([v, l]) => <Chip key={"g" + v} active={gubun === v} onClick={() => setGubun(v)}>{l}</Chip>)}
            <span className="mx-1 text-slate-300">|</span>
            {[["", "전체"], ["반출", "반출"], ["RT 가능", "RT 가능"]].map(([v, l]) => <Chip key={"a" + v} active={action === v} onClick={() => setAction(v)}>{l}</Chip>)}
          </div>
          <TotalGrid dark={dark} rows={data?.rows ?? []} columns={cols} height={620} sum={["stock_qty", "avail_qty", "outgoing_qty", "incoming_qty", "off_w1", "off_cum"]} />
        </CardBody>
      </Card>
    </div>
  );
}
