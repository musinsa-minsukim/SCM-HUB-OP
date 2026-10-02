import { useState } from "react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { Download } from "lucide-react";
import { colText, type ColDef } from "./Grid";
import { COL, ErrorBox, Filters, PRODUCT_COLS, TotalGrid, useApi, type PageProps } from "./common";
import { qs, num } from "./lib";

// 상태(실제) vs 정상 상태 — 다르면 빨강
const statusCol = (field: string, header: string): ColDef => ({
  ...colText(field, header, { minWidth: 90 }),
  cellStyle: ((p: any) => {
    if (!p.data || p.data.__muTotal) return null;
    if (p.value === "운영중") return { color: "var(--ratio-up)", fontWeight: 700 };
    if (p.value === "미운영") return { color: "var(--ratio-neutral)", fontWeight: 700 };
    return null;
  }) as any,
});
const ACTUAL: ColDef = {
  ...statusCol("actual_status", "상태"),
  headerTooltip: "실제 데이터(SCM-HUB 매장×SKU 운영상태). OPERATION_ENABLED = 운영중, 그 외(등록만·비활성·삭제·등록 없음) = 미운영",
  cellStyle: ((p: any) => {
    if (!p.data || p.data.__muTotal) return null;
    const bad = p.data.mismatch === true || p.data.mismatch === "True";
    return bad ? { color: "var(--ratio-down)", fontWeight: 700, background: "rgba(220,38,38,0.08)" }
      : { color: p.value === "운영중" ? "var(--ratio-up)" : "var(--ratio-neutral)", fontWeight: 700 };
  }) as any,
};
const EXPECTED: ColDef = { ...statusCol("expected_status", "정상 상태"), headerTooltip: "운영리스트에 있으면 운영중, 없으면 미운영" };
const CASE: ColDef = { ...colText("case", "구분", { pinned: "left", minWidth: 80 }),
  headerTooltip: "CASE1 = 운영리스트에 있는 매장×SKU / CASE2 = 운영리스트에 없는데 실제 운영중" };
const SCMCODE: ColDef = colText("scm_code", "SCM 상태 코드", { minWidth: 150 });

// 무엇을 고쳐야 하는지 — 확인 사항별 안내
const HELP: Record<string, string> = {
  "템플릿 예시 행": "브랜드 시트 맨 위 예시 행(오드타입). 브랜드 시트에서 지워 달라고 요청",
  "SKU ID 형식 오류": "S + 숫자 13자리가 아님. 브랜드에 SKU ID 재기재 요청",
  "SKU ID 원천에 없음": "SCM-HUB 에 없는 SKU ID. 오타이거나 SKU 미생성",
  "매장명 확인 불가": "어느 매장인지 찾지 못함. 브랜드 시트 매장명 수정 요청",
  "매장명 후보 여럿": "'홍대'처럼 여러 매장이 걸려 정할 수 없음(후보는 '매장 매칭' 열). 브랜드 시트에 정확한 매장명 요청",
  "매장명 자동 매칭": "표기가 달라 키워드로 찾은 매장. '매장 매칭' 열에서 맞는지 확인",
  "고정 수량 비어 있음/숫자 아님": "매장 고정(초도) 수량 칸이 비었거나 숫자가 아님",
  "위탁 SKU 아님": "매입(PURCHASE) SKU. 위탁 IPS 대상 아님",
  "중복 행(같은 매장·SKU)": "같은 매장·SKU 가 두 번 이상 — 첫 행만 계산",
  "브랜드 불일치": "시트 브랜드와 SKU 의 실제 브랜드가 다름",
  "담당 MD 브랜드 아님": "SKU 브랜드의 오프라인 MD 가 대상이 아님",
  "SCM 운영중 전환 필요": "시트에 있는데 SCM-HUB 가 운영중이 아님(등록 없음 포함). 업로드 파일 ① 에 '운영중'으로 담김",
  "글로벌 SKU(GLOBAL_3P)": "글로벌 위탁 SKU — 국내 오프라인용 SKU 인지 확인",
  "바코드 없음": "SCM-HUB 에 활성 바코드가 없음",
  "바코드 2개 이상": "재고·판매는 SKU 로 합산됨. 매장 라벨 확인용 참고",
  "비제스트 오프라인 판매 N": "비제스트·SCM-HUB 모두 오프라인 판매 N → 보충 발주 불가, MFS 배분 0. 비제스트에서 Y 로 바꿔야 함(업로드 파일 ③ 목록)",
  "SCM 오프라인 판매 미반영": "비제스트는 Y 인데 SCM-HUB 는 아직 N → 보충 발주 불가, MFS 배분 0. 넘어오길 기다리고, 하루 넘게 그대로면 SCM-HUB 문의",
  "오프라인 판매 비제스트 N·SCM Y": "비제스트는 N 인데 SCM-HUB 는 Y (참고). 비제스트에서 N 으로 바꾼 직후일 수 있음",
  "시트에 없음 · SCM 운영중": "CASE2 — 운영리스트에 없는데 SCM-HUB 에서 운영중. 업로드 파일 ① 에 '미운영'으로 담김",
};

export default function Issues({ dark, preset }: PageProps) {
  const [brand, setBrand] = useState<string[]>(preset.brand ? [preset.brand] : []);
  const [store, setStore] = useState<string[]>(preset.store ? [preset.store] : []);
  const [flag, setFlag] = useState<string>(preset.flag ?? "");
  const [sev, setSev] = useState("error|warn");
  const [cs, setCs] = useState("");          // "" | CASE1 | CASE2
  const [mis, setMis] = useState(false);     // 상태 불일치만
  const [md, setMd] = useState<string[]>([]);
  const params = { brand, store, md, flag, severity: sev, case: cs, mismatch: mis ? "1" : "" };
  const { data, err, loading } = useApi<any>("/issues" + qs(params));
  const counts: Record<string, number> = data?.flag_counts ?? {};

  return (
    <div className="space-y-4">
      <ErrorBox msg={err} />
      <Filters brand={brand} setBrand={setBrand} store={store} setStore={setStore} md={md} setMd={setMd}
        extra={<>
          <span className="mx-1 h-5 w-px bg-slate-200 dark:bg-slate-700" />
          {[["", `전체 ${num((data?.case_counts?.CASE1 ?? 0) + (data?.case_counts?.CASE2 ?? 0))}`],
            ["CASE1", `CASE1 운영리스트 ${num(data?.case_counts?.CASE1 ?? 0)}`],
            ["CASE2", `CASE2 리스트 밖 운영중 ${num(data?.case_counts?.CASE2 ?? 0)}`]].map(([v, l]) =>
            <Chip key={v} active={cs === v} onClick={() => setCs(v)}>{l}</Chip>)}
          <Chip active={mis} onClick={() => setMis(!mis)}>상태 불일치만 {num(data?.mismatch_count ?? 0)}</Chip>
          <span className="mx-1 h-5 w-px bg-slate-200 dark:bg-slate-700" />
          {[["error|warn", "오류+확인"], ["error", "오류만"], ["warn", "확인만"], ["all", "전체 행"]].map(([v, l]) =>
            <Chip key={v} active={sev === v} onClick={() => setSev(v)}>{l}</Chip>)}
          <a href={"/api/issues.csv" + qs(params)} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
            <Download size={14} /> CSV</a>
        </>} />
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {Object.entries(counts).map(([f, n]) => (
          <button key={f} onClick={() => setFlag(flag === f ? "" : f)}
            className={`rounded-xl border p-3 text-left transition ${flag === f
              ? "border-indigo-300 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950/60"
              : "border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900"}`}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{f}</span>
              <span className="tabular-nums text-sm font-semibold text-slate-500">{num(n)}</span>
            </div>
            <div className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">{HELP[f]}</div>
          </button>
        ))}
      </div>
      <Card>
        <CardBody>
          <SectionTitle title={`매장×SKU ${num(data?.total ?? 0)}건`}
            sub="CASE1 = 브랜드 운영리스트에 있는 조합(정상 상태 운영중) · CASE2 = 리스트에 없는데 실제 운영중인 조합(정상 상태 미운영). 상태가 정상 상태와 다르면 빨강. 원본 행 = 브랜드 시트 행 번호." right={loading ? <Spinner /> : null} />
          {/* 재고·판매는 브랜드 현황과 같은 6개 지표 · 같은 순서 */}
          <TotalGrid dark={dark} rows={data?.rows ?? []} height={600}
            columns={[CASE, COL.brand, COL.store, ...PRODUCT_COLS(), ACTUAL, EXPECTED, COL.bzOffline, COL.offline, COL.sev, COL.src, COL.flags, COL.storeIn, COL.storeMatch,
              COL.fixed, COL.stock, COL.incoming, COL.outgoing, COL.mfs, COL.mfsIn, COL.mfsInDate, COL.mfsLate, COL.offCum, COL.off1,
              COL.barcode, COL.otherBc, SCMCODE]} />
        </CardBody>
      </Card>
    </div>
  );
}
