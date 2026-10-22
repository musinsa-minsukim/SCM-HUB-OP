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
  colText("sku_ids", "조회한 SKU ID", { minWidth: 150, headerTooltip: "SKU ID 로 조회했을 때, 그 SKU 가 연결된 대표 UID 로 비교했다" }),
  colText("bm_name", "거래형태", { minWidth: 80, headerTooltip: "매입 / 위탁" }),
  colText("brand_nm", "브랜드", { minWidth: 110 }),
  colText("product_name", "상품명", { minWidth: 240 }),
  { ...colText("bz_offline_yn", "비제스트 오프라인 판매", { minWidth: 130 }), cellStyle: yn },
  colText("bz_offline_ut", "비제스트 변경 시각", { minWidth: 140 }),
  { ...colText("scm_offline_yn", "SCM-HUB 오프라인 판매", { minWidth: 140 }), cellStyle: yn },
  colText("scm_updated_at", "SCM-HUB 상품 갱신 시각", { minWidth: 150 }),
  colNum("hours_since_bz", "비제스트 변경 후 경과(시간)", "num", { headerTooltip: "불일치일 때만. 하루(24시간) 넘게 그대로면 SCM-HUB 연동 문의" }),
  colText("scm_sales_status", "SCM-HUB 판매상태", { minWidth: 120 }),
];

function Dl({ href }: { href: string }) {
  return (
    <a href={href} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500">
      <Download size={14} /> CSV
    </a>
  );
}

const ago = (now?: string, t?: string) => {
  if (!now || !t) return "";
  const m = Math.round((Date.parse(now.replace(" ", "T")) - Date.parse(t.replace(" ", "T"))) / 60000);
  return m < 60 ? `${m}분 전` : `${Math.floor(m / 60)}시간 ${m % 60}분 전`;
};
const dur = (m?: number | null) => (m == null ? "—" : m < 60 ? `${m}분` : `${Math.floor(m / 60)}시간 ${m % 60}분`);

function Freshness() {
  const { data: f, loading } = useApi<any>("/offline_sync/freshness");
  return (
    <Card>
      <CardBody>
        <SectionTitle title="데이터 갱신 주기"
          sub="두 값 모두 Databricks 사본에서 읽습니다. 사본은 매시 정각까지의 변경을 한 시간 단위로 복사합니다(실측). 연동 지연은 최근 7일 동안 비제스트에서 오프라인 판매 여부를 바꾼 시각부터 SCM-HUB 에 같은 값이 처음 나타난 시각까지입니다. 30분마다 다시 계산합니다."
          right={loading ? <Spinner /> : f ? <span className="text-xs text-slate-500">기준 {f.now_kst}</span> : null} />
        {f && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Kpi label="비제스트 데이터 기준" value={f.bz_asof?.slice(11) ?? "—"} sub={`${ago(f.now_kst, f.bz_asof)} · 1시간 단위 복사`} />
            <Kpi label="SCM-HUB 상품 데이터 기준" value={f.scm_asof?.slice(11) ?? "—"} sub={`${ago(f.now_kst, f.scm_asof)} · 1시간 단위 복사`} />
            <Kpi label="비제스트 → SCM-HUB 연동 (중앙값)" value={dur(f.p50_min)} sub={`상위 10% ${dur(f.p90_min)} 이상`} />
            <Kpi label="최근 7일 변경 건" value={num(f.n_events ?? 0)} sub={`아직 미반영 ${num(f.n_pending ?? 0)}건`} />
            <Kpi label="24시간 넘게 미반영" value={num(f.n_pending_24h ?? 0)} tone={f.n_pending_24h ? "bad" : undefined} sub="SCM-HUB 연동 문의 대상" />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export default function OfflineSync({ dark }: PageProps) {
  const [text, setText] = useState("");
  const [uids, setUids] = useState("");
  const one = useApi<any>(uids ? "/offline_sync" + qs({ uids }) : null);
  const [brand, setBrand] = useState("");
  const [bq, setBq] = useState<string | null>(null);   // null = 아직 안 불러옴
  const [st, setSt] = useState("");
  const [bm, setBm] = useState("");   // "" 전체 / P 위탁 / S 매입
  const mis = useApi<any>(bq === null ? null : "/offline_sync/mismatch" + qs({ brand: bq, bm }));
  const misRows = (mis.data?.rows ?? []).filter((r: any) => !st || r.status === st);
  const cnt = (s: string) => (mis.data?.rows ?? []).filter((r: any) => r.status === s).length;
  const inp = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800";

  return (
    <div className="space-y-5">
      <ErrorBox msg={one.err || mis.err} />
      <Freshness />
      <Card>
        <CardBody>
          <SectionTitle title="UID · SKU ID 로 조회"
            sub="브랜드 운영리스트와 상관없이, 붙여 넣은 UID 또는 SKU ID 의 비제스트 오프라인 판매 여부와 SCM-HUB 반영 여부를 바로 비교합니다(위 갱신 주기의 사본 기준). 줄바꿈·쉼표·공백 어느 것으로 구분해도 되고 섞어도 됩니다. SKU ID 는 SCM-HUB 에서 연결된 대표 UID 로 바꿔 비교합니다."
            right={<div className="flex items-center gap-2">{one.loading && <Spinner />}{uids && one.data?.total ? <Dl href={"/api/offline_sync.csv" + qs({ uids })} /> : null}</div>} />
          <div className="mb-3 flex flex-wrap items-end gap-2">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder={"7010167\nS2601130000419, 7252283"}
              className={inp + " w-full max-w-xl font-mono"} />
            <button onClick={() => setUids(text.trim())}
              className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">조회</button>
          </div>
          {one.data?.missing?.length ? (
            <div className="mb-2 text-xs text-rose-600">찾지 못한 UID·SKU ID: {one.data.missing.join(", ")}</div>
          ) : null}
          {uids && <TotalGrid dark={dark} rows={one.data?.rows ?? []} columns={cols} height={320} sum={[]} />}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`전체 상품(매입·위탁) 불일치 ${bq === null ? "" : num(mis.data?.total ?? 0) + "개"}`}
            sub="모든 매입·위탁 상품 중 비제스트와 SCM-HUB 오프라인 판매 여부가 다른 것(브랜드 무관, 비제스트 변경 최신순). 비제스트 Y 인데 SCM 이 N 이면 보충 발주가 막힙니다. 방금 바꾼 건 1시간쯤 기다리고, 하루 넘게 그대로면 SCM-HUB 연동 문의."
            right={<div className="flex items-center gap-2">{mis.loading && <Spinner />}
              <form onSubmit={(e) => { e.preventDefault(); setBq(brand.trim()); }} className="flex gap-2">
                <input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="브랜드명·코드 (비우면 전체)" className={inp + " w-52"} />
                <button className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800">불러오기</button>
              </form>
              {bq !== null && <Dl href={"/api/offline_sync.csv" + qs({ mismatch: "1", brand: bq, bm })} />}</div>} />
          {bq !== null && (
            <>
              <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                <Kpi label="비제스트 Y · SCM N" value={num(cnt("SCM 미반영(비제스트 Y)"))} tone={cnt("SCM 미반영(비제스트 Y)") ? "bad" : undefined} sub="보충 발주 막힘" />
                <Kpi label="비제스트 N · SCM Y" value={num(cnt("SCM 미반영(비제스트 N)"))} />
                <div className="col-span-2 flex flex-wrap items-center gap-1.5">
                  {[["", "매입·위탁"], ["S", "매입"], ["P", "위탁"]].map(([v, l]) =>
                    <Chip key={"bm" + v} active={bm === v} onClick={() => setBm(v)}>{l}</Chip>)}
                  <span className="mx-1 text-slate-300">|</span>
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
