import { useState } from "react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import DataGrid from "./Grid";
import { COL, CsvButton, ErrorBox, Filters, PRODUCT_COLS, useApi, type PageProps, TotalGrid } from "./common";
import { qs, num } from "./lib";

export default function Replenish({ dark, preset }: PageProps) {
  const [brand, setBrand] = useState<string[]>(preset.brand ? [preset.brand] : []);
  const [store, setStore] = useState<string[]>(preset.store ? [preset.store] : []);
  const [md, setMd] = useState<string[]>([]);
  const [mode, setMode] = useState<"need" | "return">("need");
  const params = { brand, store, md, only: mode };
  const { data, err, loading } = useApi<any>("/rows" + qs(params));
  const rows = data?.rows ?? [];

  const needCols = [COL.brand, COL.store, ...PRODUCT_COLS(), COL.offline, COL.barcode, COL.otherBc, COL.fixed, COL.stock,
    COL.incoming, COL.outgoing, COL.mfs, COL.mfsIn, COL.mfsInDate, COL.mfsLate, COL.offCum, COL.off1, COL.avail, COL.need, COL.alloc, COL.short, COL.scm, COL.flags];
  const retCols = [COL.brand, COL.store, ...PRODUCT_COLS(), COL.barcode, COL.otherBc, COL.fixed, COL.stock,
    COL.incoming, COL.outgoing, COL.mfs, COL.mfsIn, COL.mfsInDate, COL.mfsLate, COL.offCum, COL.off1, COL.avail, COL.off4, COL.over, COL.flags];
  const sumF = mode === "need" ? ["fixed_qty", "stock_qty", "incoming_qty", "outgoing_qty", "off_cum", "off_w1", "avail_qty", "need_qty", "alloc_qty", "short_qty"]
    : ["fixed_qty", "stock_qty", "incoming_qty", "outgoing_qty", "off_cum", "off_w1", "avail_qty", "off_4w", "over_qty"];

  return (
    <div className="space-y-4">
      <ErrorBox msg={err} />
      <Filters brand={brand} setBrand={setBrand} store={store} setStore={setStore} md={md} setMd={setMd}
        extra={<>
          <Chip active={mode === "need"} onClick={() => setMode("need")}>보충</Chip>
          <Chip active={mode === "return"} onClick={() => setMode("return")}>반출 후보</Chip>
          <div className="ml-auto"><CsvButton params={params} /></div>
        </>} />
      <Card>
        <CardBody>
          <SectionTitle
            title={mode === "need" ? `보충 필요 ${num(data?.total ?? 0)}행` : `반출 후보 ${num(data?.total ?? 0)}행`}
            sub={mode === "need"
              ? "보충 필요 = 고정 수량 − (판매가능 + 입고 예정). 발주 조건 = ① 비제스트 오프라인 판매 Y ② SCM 매장 운영중 — ① 이 N 이면 MFS 배분 0. MFS 배분은 매장 판매(7일 → 4주 → 누적)가 많은 매장부터 MFS 재고 안에서 나눕니다. SCM 운영 미등록 매장은 배분하지 않습니다."
              : "과잉 = (판매가능 + 입고 예정) − 고정 수량 > 0 이면서 최근 4주 매장 판매 0. 자동 지시가 아니라 검토용입니다."}
            right={loading ? <Spinner /> : null} />
          <TotalGrid dark={dark} rows={rows} height={620} columns={mode === "need" ? needCols : retCols} />
        </CardBody>
      </Card>
    </div>
  );
}
