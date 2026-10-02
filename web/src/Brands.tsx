import { useMemo, useState } from "react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import DataGrid, { colNum, colText, type ColDef } from "./Grid";
import { ErrorBox, Kpi, useApi, type PageProps, TotalGrid } from "./common";
import { num } from "./lib";

const STATUS_STYLE: Record<string, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  "파일 없음": "text-slate-400",
  "시트 미연결": "text-amber-600 dark:text-amber-400",
};

export default function Brands({ dark, nav }: PageProps) {
  const { data, err, loading } = useApi<{ brands: any[] }>("/brands");
  const { data: st } = useApi<any>("/status");
  const [md, setMd] = useState<string>("전체");
  // MD 칩은 데이터에 있는 오프라인 MD 로 자동 생성 (MD 가 늘어나도 코드 수정 불필요)
  const mdOptions = useMemo(() => [...new Set((data?.brands ?? []).flatMap((b) =>
    String(b.offline_md || "미지정").split(",").map((x) => x.trim()).filter(Boolean)))].sort(), [data]);

  const rows = useMemo(() => {
    const all = (data?.brands ?? []).map((b) => ({ ...b, src_file: b.brand }));
    return md === "전체" ? all : all.filter((b) => String(b.offline_md || "").includes(md) || (md === "수동 포함" && b.target_reason === "수동 포함"));
  }, [data, md]);
  const withRows = rows.filter((r) => r.rows > 0);
  const s = st?.summary;
  // 요약 카드는 MD 선택에 맞춰 화면에 보이는 브랜드 행으로 계산
  const sumOf = (f: string) => rows.reduce((a, r) => a + (Number(r[f]) || 0), 0);
  const k = { rows: sumOf("rows"), warns: sumOf("warns"), errors: sumOf("errors"),
              need: sumOf("need_qty"), alloc: sumOf("alloc_qty"), short: sumOf("short_qty") };

  const cols: ColDef[] = [
    colText("src_file", "브랜드", { pinned: "left", minWidth: 110 }),
    colText("offline_md", "오프라인 MD", { minWidth: 110 }),
    {
      ...colText("status", "시트 상태", { minWidth: 110 }),
      cellClass: (p: any) => STATUS_STYLE[p.value] ?? (p.value ? "text-rose-600 dark:text-rose-400" : ""),
      tooltipField: "detail",
    },
    colText("qty_header", "수량 열", { minWidth: 110 }),
    colNum("rows", "취합 행", "int"),
    colNum("stores", "매장", "int"),
    colNum("skus", "SKU", "int"),
    { ...colNum("errors", "오류", "int"), cellStyle: ((p: any) => (p.value > 0 ? { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } : { textAlign: "right" })) as any },
    colNum("warns", "확인", "int"),
    colNum("unregistered", "운영중 전환 필요", "int"),
    colNum("multi_barcode", "바코드 2개+", "int"),
    colNum("offline_n", "오프라인 판매 N", "int", { headerTooltip: "SCM-HUB 오프라인 판매 여부가 Y 가 아닌 행(보충 발주 불가). 비제스트 N + SCM 미반영 합계" }),
    colNum("fixed_pcs", "고정 운영 수량", "int"),
    colNum("stock_qty", "매장 현재고", "int"),
    colNum("incoming_qty", "이동중 재고", "int"),
    colNum("outgoing_qty", "매장 반납 예정", "int"),
    colNum("mfs_qty", "MFS 재고", "int", { headerTooltip: "브랜드 운영리스트 SKU 의 MFS 재고 (SKU 별 1번만 합산)" }),
    colNum("mfs_in_qty", "MFS 입고 예정", "int", { headerTooltip: "브랜드 → MFS 입고 예정 (예정일 최근 7일 이내·이후, SKU 별 1번만 합산)" }),
    colNum("mfs_in_late_qty", "MFS 입고 지연", "int", { headerTooltip: "입고 예정일이 7일 넘게 지났는데 미입고 (SKU 별 1번만 합산)" }),
    colNum("off_cum", "누적 판매", "int"),
    colNum("off_w1", "7일 판매", "int"),
    colNum("need_qty", "보충 필요", "int"),
    { ...colNum("alloc_qty", "MFS 배분", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } },
    colNum("short_qty", "부족", "int"),
    colNum("return_cand", "반출 후보", "int"),
    colText("modified", "시트 수정", { minWidth: 110, valueFormatter: (p: any) => (p.value ? String(p.value).slice(0, 16).replace("T", " ") : "") }),
  ];
  const sumFields = ["rows", "stores", "skus", "errors", "warns", "unregistered", "multi_barcode", "fixed_pcs", "stock_qty", "incoming_qty", "outgoing_qty", "mfs_qty", "off_cum", "off_w1", "need_qty", "alloc_qty", "short_qty", "return_cand"];

  if (loading && !data) return <div className="flex justify-center p-10"><Spinner /></div>;
  return (
    <div className="space-y-5">
      <ErrorBox msg={err} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="대상 브랜드" value={num(rows.length)} sub={`취합 있음 ${withRows.length}`} />
        <Kpi label="취합 행" value={num(k.rows)} sub={`확인 ${num(k.warns)}`} />
        <Kpi label="오류 (계산 제외)" value={num(k.errors)} tone={k.errors ? "bad" : undefined} />
        <Kpi label="보충 필요" value={num(k.need)} sub="고정 수량 − (판매가능+입고 예정)" />
        <Kpi label="MFS 배분 가능" value={num(k.alloc)} tone="good" />
        <Kpi label="MFS 부족" value={num(k.short)} tone={k.short ? "warn" : undefined} sub="MFS 재고 없음·SCM 미등록 포함" />
      </div>
      <Card>
        <CardBody>
          <SectionTitle title="브랜드별 현황" sub="행을 누르면 그 브랜드의 취합 검증으로 이동합니다. 수량 열 = 브랜드 시트에서 읽은 고정 수량 열 이름."
            right={<div className="flex flex-wrap gap-1.5">{["전체", ...mdOptions, "수동 포함"].map((m) => <Chip key={m} active={md === m} onClick={() => setMd(m)}>{m}</Chip>)}</div>} />
          <TotalGrid dark={dark} rows={rows} columns={cols} height={620}
            onCellClicked={(e) => e.data?.src_file && !e.data.__muTotal && nav("issues", { brand: e.data.src_file })} />
        </CardBody>
      </Card>
    </div>
  );
}
