import { useMemo, useState } from "react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import DataGrid, { colNum, colText, type ColDef } from "./Grid";
import { ErrorBox, useApi, type PageProps, TotalGrid } from "./common";

const METRICS = [
  ["skus", "SKU 수"], ["fixed", "고정 수량(PCS)"], ["stock", "매장 재고"], ["need", "보충 필요"],
] as const;

export default function Matrix({ dark, nav }: PageProps) {
  const [metric, setMetric] = useState<string>("skus");
  const { data, err, loading } = useApi<{ stores: string[]; rows: any[] }>(`/matrix?metric=${metric}`);
  const rows = useMemo(() => (data?.rows ?? []).map((r) => ({ ...r, src_file: r.brand })), [data]);
  const stores = data?.stores ?? [];
  const cols: ColDef[] = [
    colText("src_file", "브랜드", { pinned: "left", minWidth: 110 }),
    colNum("_total", "합계", "int", { cellStyle: { fontWeight: 700, textAlign: "right" } }),
    ...(metric === "stock" ? [colNum("_mfs", "MFS 재고", "int", { headerTooltip: "브랜드 운영리스트 SKU 의 MFS 센터 재고(SKU 별 1번 합산). 매장 공통이라 매장 열과 별도" })] : []),
    ...stores.map((s) => colNum(s, s.replace("무신사 ", ""), "int", {
      cellStyle: ((p: any) => (p.value ? { textAlign: "right" } : { textAlign: "right", color: "var(--ratio-neutral)" })) as any,
    })),
  ];
  return (
    <div className="space-y-4">
      <ErrorBox msg={err} />
      <Card>
        <CardBody>
          <SectionTitle title="브랜드 × 매장 전개" sub="오류 행·중복 행 제외. 셀을 누르면 그 브랜드·매장 상품으로 이동합니다."
            right={<div className="flex items-center gap-1.5">{loading && <Spinner />}{METRICS.map(([k, l]) =>
              <Chip key={k} active={metric === k} onClick={() => setMetric(k)}>{l}</Chip>)}</div>} />
          {data && <TotalGrid dark={dark} rows={rows} columns={cols} height={640}
            onCellClicked={(e) => {
              const f = e.colDef?.field;
              if (!e.data || e.data.__muTotal || !f || f === "src_file" || f === "_total" || f === "_mfs") return;
              nav("search", { brand: e.data.src_file, store: f });
            }} />}
        </CardBody>
      </Card>
    </div>
  );
}
