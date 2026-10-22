import { useState } from "react";
import { Download } from "lucide-react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { colNum, colText, type ColDef } from "./Grid";
import { ErrorBox, Kpi, TotalGrid, useApi, type PageProps } from "./common";
import { num, qs } from "./lib";

// 오프라인 판매 여부 동기화 점검 — 브랜드 운영리스트와 무관하게 UID 로 비제스트 vs SCM-HUB 를 비교 (2026-10-22)
const yn = ((p: any) => (!p.data || p.data.__muTotal || !p.value ? null
  : p.value === "Y" ? { color: "var(--ratio-up)", fontWeight: 700 } : { color: "var(--ratio-down)", fontWeight: 700 })) as any;

const cols: ColDef[] = [
  {
    ...colText("status", "상태", { pinned: "left", minWidth: 170 }),
    cellStyle: ((p: any) => (!p.data || p.data.__muTotal ? null
      : p.value === "일치" ? { color: "var(--ratio-up)", fontWeight: 700 }
      : String(p.value).startsWith("SCM 미반영") ? { color: "var(--ratio-down)", fontWeight: 700 } : { color: "var(--ratio-neutral)" })) as any,
  },
  colText("uid", "UID", { minWidth: 100 }),
  colText("brand_nm", "브랜드", { minWidth: 110 }),
  colText("product_name", "상품명", { minWidth: 240 }),
  { ...colText("bz_offline_yn", "비제스트 오프라인 판매", { minWidth: 130 }), cellStyle: yn },
  colText("bz_offline_ut", "비제스트 변경 시각", { minWidth: 140 }),
  { ...colText("scm_offline_yn", "SCM-HUB 오프라인 판매", { minWidth: 140 }), cellStyle: yn },
  colText("scm_updated_at", "SCM-HUB 상품 갱신 시각", { minWidth: 150 }),
  colNum("hours_since_bz", "비제스트 변경 후 경과(시간)", "num", { headerTooltip: "불일치일 때만. 하루(24시간) 넘게 그대로면 SCM-HUB 연동 문의" }),
  colText("scm_sales_status", "SCM-HUB 판매상태", { minWidth: 120 }),
  colText("bm", "거래형태", { minWidth: 80, headerTooltip: "P = 위탁, S = 매입" }),
];

function Dl({ href }: { href: string }) {
  return (
    <a href={href} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500">
      <Download size={14} /> CSV
    </a>
  );
}

export default function OfflineSync({ dark }: PageProps) {
  const [text, setText] = useState("");
  const [uids, setUids] = useState("");
  const one = useApi<any>(uids ? "/offline_sync" + qs({ uids }) : null);
  const [brand, setBrand] = useState("");
  const [bq, setBq] = useState<string | null>(null);   // null = 아직 안 불러옴
  const [st, setSt] = useState("");
  const mis = useApi<any>(bq === null ? null : "/offline_sync/mismatch" + qs({ brand: bq }));
  const misRows = (mis.data?.rows ?? []).filter((r: any) => !st || r.status === st);
  const cnt = (s: string) => (mis.data?.rows ?? []).filter((r: any) => r.status === s).length;
  const inp = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800";

  return (
    <div className="space-y-5">
      <ErrorBox msg={one.err || mis.err} />
      <Card>
        <CardBody>
          <SectionTitle title="UID 로 조회"
            sub="브랜드 운영리스트와 상관없이, 붙여 넣은 UID 의 비제스트 오프라인 판매 여부와 SCM-HUB 반영 여부를 바로 비교합니다(실시간 사본, 약 15분~1시간 지연). 줄바꿈·쉼표·공백 어느 것으로 구분해도 됩니다."
            right={<div className="flex items-center gap-2">{one.loading && <Spinner />}{uids && one.data?.total ? <Dl href={"/api/offline_sync.csv" + qs({ uids })} /> : null}</div>} />
          <div className="mb-3 flex flex-wrap items-end gap-2">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder={"7010167\n3417694, 7252283"}
              className={inp + " w-full max-w-xl font-mono"} />
            <button onClick={() => setUids(text.trim())}
              className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">조회</button>
          </div>
          {one.data?.missing?.length ? (
            <div className="mb-2 text-xs text-rose-600">비제스트에 없는 UID: {one.data.missing.join(", ")}</div>
          ) : null}
          {uids && <TotalGrid dark={dark} rows={one.data?.rows ?? []} columns={cols} height={320} sum={[]} />}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`전체 위탁 상품 불일치 ${bq === null ? "" : num(mis.data?.total ?? 0) + "개"}`}
            sub="모든 위탁(P) 상품 중 비제스트와 SCM-HUB 오프라인 판매 여부가 다른 것(브랜드 무관, 비제스트 변경 최신순). 비제스트 Y 인데 SCM 이 N 이면 보충 발주가 막힙니다. 방금 바꾼 건 1시간쯤 기다리고, 하루 넘게 그대로면 SCM-HUB 연동 문의."
            right={<div className="flex items-center gap-2">{mis.loading && <Spinner />}
              <form onSubmit={(e) => { e.preventDefault(); setBq(brand.trim()); }} className="flex gap-2">
                <input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="브랜드명·코드 (비우면 전체)" className={inp + " w-52"} />
                <button className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800">불러오기</button>
              </form>
              {bq !== null && <Dl href={"/api/offline_sync.csv" + qs({ mismatch: "1", brand: bq })} />}</div>} />
          {bq !== null && (
            <>
              <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                <Kpi label="비제스트 Y · SCM N" value={num(cnt("SCM 미반영(비제스트 Y)"))} tone={cnt("SCM 미반영(비제스트 Y)") ? "bad" : undefined} sub="보충 발주 막힘" />
                <Kpi label="비제스트 N · SCM Y" value={num(cnt("SCM 미반영(비제스트 N)"))} />
                <div className="col-span-2 flex flex-wrap items-center gap-1.5">
                  {[["", "전체"], ["SCM 미반영(비제스트 Y)", "비제스트 Y·SCM N"], ["SCM 미반영(비제스트 N)", "비제스트 N·SCM Y"]].map(([v, l]) =>
                    <Chip key={v} active={st === v} onClick={() => setSt(v)}>{l}</Chip>)}
                </div>
              </div>
              <TotalGrid dark={dark} rows={misRows} columns={cols} height={480} sum={[]} />
            </>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
