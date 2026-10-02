import { useState } from "react";
import { Card, CardBody, SectionTitle, Spinner } from "./ui";
import DataGrid from "./Grid";
import { COL, CsvButton, ErrorBox, Filters, PRODUCT_COLS, useApi, type PageProps, TotalGrid } from "./common";
import { qs, num } from "./lib";

export default function Search({ dark, preset }: PageProps) {
  const [brand, setBrand] = useState<string[]>(preset.brand ? [preset.brand] : []);
  const [store, setStore] = useState<string[]>(preset.store ? [preset.store] : []);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [md, setMd] = useState<string[]>([]);
  const ready = brand.length > 0 || store.length > 0 || md.length > 0 || q.length > 0;
  const params = { brand, store, md, q };
  const { data, err, loading } = useApi<any>(ready ? "/rows" + qs(params) : null);
  const rows = data?.rows ?? [];
  const sumF = ["fixed_qty", "stock_qty", "incoming_qty", "outgoing_qty", "off_cum", "off_w1", "avail_qty", "off_4w", "need_qty", "alloc_qty"];

  return (
    <div className="space-y-4">
      <ErrorBox msg={err} />
      <Filters brand={brand} setBrand={setBrand} store={store} setStore={setStore} md={md} setMd={setMd}
        extra={<>
          <form onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }} className="flex gap-2">
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="SKU ID · 상품번호 · 상품명 · 바코드"
              className="w-72 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800" />
          </form>
          {ready && <div className="ml-auto"><CsvButton params={params} /></div>}
        </>} />
      <Card>
        <CardBody>
          <SectionTitle title={ready ? `상품 ${num(data?.total ?? 0)}행` : "MD·브랜드·매장을 고르거나 검색하세요"}
            sub="바코드로 검색하면 추가 바코드까지 찾습니다. 재고·판매는 모든 바코드를 SKU 로 합친 값입니다."
            right={loading ? <Spinner /> : null} />
          {ready && <TotalGrid dark={dark} rows={rows} height={620}
            columns={[COL.brand, COL.store, ...PRODUCT_COLS(), COL.sev, COL.bzOffline, COL.offline, COL.barcode, COL.otherBc,
              COL.fixed, COL.stock, COL.incoming, COL.outgoing, COL.mfs, COL.mfsIn, COL.mfsInDate, COL.mfsLate, COL.offCum, COL.off1, COL.avail, COL.off4, COL.offAll, COL.onl1,
              COL.need, COL.alloc, COL.scm, COL.flags, COL.src]} />}
        </CardBody>
      </Card>
    </div>
  );
}
