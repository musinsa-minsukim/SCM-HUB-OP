import { useState } from "react";
import { Download } from "lucide-react";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { colNum, colText, type ColDef } from "./Grid";
import { COL, ErrorBox, Filters, Kpi, PRODUCT_COLS, TotalGrid, useApi, type PageProps } from "./common";
import { num, qs } from "./lib";

function DlButton({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-500">
      <Download size={14} /> {label}
    </a>
  );
}

const opsCols: ColDef[] = [
  colText("storage_no", "스토어코드", { pinned: "left", minWidth: 110 }),
  ...PRODUCT_COLS(),
  {
    ...colText("target_status", "스토어 운영상태", { minWidth: 110 }),
    cellStyle: ((p: any) => (p.value === "미운영" ? { color: "var(--ratio-down)", fontWeight: 700 }
      : p.value === "운영중" ? { color: "var(--ratio-up)", fontWeight: 700 } : null)) as any,
  },
  colText("brand_nm", "브랜드", { minWidth: 100 }),
  colText("store_name", "매장", { minWidth: 140 }),
  colText("storage_status", "현재 SCM 상태", { minWidth: 150 }),
  {
    ...colNum("stock_qty", "매장 재고", "int"),
    cellStyle: ((p: any) => (p.data?.target_status === "미운영" && p.value > 0
      ? { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } : { textAlign: "right" })) as any,
  },
  COL.mfs,
  colText("reason", "사유", { minWidth: 200 }),
];

const moveCols: ColDef[] = [
  colText("storage_no", "스토어코드", { pinned: "left", minWidth: 110 }),
  ...PRODUCT_COLS(),
  colText("barcode", "바코드", { minWidth: 150 }),
  { ...colNum("move_qty", "이동수량", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } },
  { ...colNum("ret_qty", "과재고 반출 수량", "int"), cellStyle: { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } },
  colNum("fixed_qty", "고정 운영 수량", "int"),
  colNum("stock_qty", "매장 현재고", "int"),
  colNum("avail_qty", "판매가능", "int", { headerTooltip: "매장 현재고 중 판매 가능한 수량(불량·보류 제외). 보충 필요 = 고정 − (판매가능 + 이동중)" }),
  COL.mfs,
  { ...colNum("incoming_qty", "이동중 재고", "int", { headerTooltip: "매장으로 들어오는 중 = 출고 요청(출고 전) + 출고 후 이동중 + 직납 예정" }), cellStyle: { fontWeight: 700, textAlign: "right" } },
  colNum("req_in_qty", "└ 출고 요청", "int", { headerTooltip: "이동지시는 났지만 아직 허브에서 출고 전" }),
  colNum("moving_in_qty", "└ 출고 후 이동중", "int", { headerTooltip: "허브에서 출고됐고 매장 입고 전" }),
  colNum("direct_in_qty", "└ 직납 예정", "int", { headerTooltip: "브랜드 → 매장 직접 입고 예정(물류센터 미경유)" }),
  colText("brand", "브랜드", { minWidth: 100 }),
  colText("store_name", "매장", { minWidth: 140 }),
  colNum("off_4w", "매장 판매 4주", "int"),
  colNum("off_cum", "누적 판매", "int"),
  colNum("off_w1", "7일 판매", "int"),
  colText("note", "메모", { minWidth: 140 }),
];

const offCols: ColDef[] = [
  { ...COL.goods, pinned: "left", minWidth: 100 } as ColDef,
  colText("src_file", "브랜드", { minWidth: 100 }),
  colText("product_name", "상품명", { minWidth: 220 }),
  { ...colText("todo", "할 일", { minWidth: 120, headerTooltip: "비제스트 Y 전환 = 비제스트에서 Y 로 바꿔야 함 / SCM 반영 대기 = 비제스트는 이미 Y, SCM-HUB 로 넘어오길 기다림(하루 넘으면 문의)" }),
    cellStyle: ((p: any) => (!p.data || p.data.__muTotal ? null
      : p.value === "SCM 반영 대기" ? { color: "var(--ratio-up)", fontWeight: 700 } : { color: "var(--ratio-down)", fontWeight: 700 })) as any },
  COL.bzOffline,
  colText("bz_offline_ut", "비제스트 변경 시각", { minWidth: 130 }),
  COL.offline,
  colNum("stores", "운영 매장 수", "int"),
  colNum("skus", "SKU 수", "int"),
  colNum("fixed_qty", "고정 운영 수량", "int"),
  colNum("stock_qty", "매장 현재고", "int"),
  colNum("mfs_qty", "MFS 재고", "int", { headerTooltip: "이 UID 의 운영리스트 SKU 들의 MFS 재고 합(SKU 별 1번)" }),
  colNum("need_qty", "보충 필요", "int"),
  colNum("off_cum", "누적 판매", "int"),
  colNum("off_w1", "7일 판매", "int"),
];

const rtCols: ColDef[] = [
  {
    ...colText("priority_name", "구분", { pinned: "left", minWidth: 150 }),
    cellStyle: ((p: any) => (p.data?.priority === 1 || p.data?.priority === "1" ? { color: "var(--ratio-down)", fontWeight: 700 } : { fontWeight: 600 })) as any,
  },
  colText("from_store", "보내는 매장", { minWidth: 150 }),
  colText("to_store", "받는 매장", { minWidth: 150 }),
  ...PRODUCT_COLS(),
  colText("barcode", "바코드", { minWidth: 140 }),
  { ...colNum("rt_qty", "이동수량", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } },
  colNum("from_stock", "보내는 매장 현재고", "int"),
  colNum("from_avail", "보내는 매장 판매가능", "int"),
  colNum("from_fixed", "보내는 매장 고정 수량", "int", { headerTooltip: "1순위(미운영 매장)는 운영리스트에 없어서 0" }),
  colNum("from_off_w1", "보내는 매장 7일 판매", "int"),
  colNum("to_fixed", "받는 매장 고정 수량", "int"),
  colNum("to_stock", "받는 매장 현재고", "int"),
  colNum("to_avail", "받는 매장 판매가능", "int"),
  colNum("to_incoming", "받는 매장 이동중", "int"),
  colNum("to_mfs_alloc", "받는 매장 MFS 배분", "int", { headerTooltip: "② 재고 보충 파일로 MFS 에서 먼저 받는 수량. RT 는 그 뒤에도 남는 부족분만 채운다" }),
  colNum("to_off_w1", "받는 매장 7일 판매", "int"),
  colNum("to_off_4w", "받는 매장 4주 판매", "int"),
  colNum("to_off_cum", "받는 매장 누적 판매", "int"),
  COL.mfs,
  colText("from_storage_no", "보내는 스토어코드", { minWidth: 110 }),
  colText("to_storage_no", "받는 스토어코드", { minWidth: 110 }),
  colText("src_file", "브랜드", { minWidth: 100 }),
  colText("note", "메모", { minWidth: 160 }),
];

export default function Uploads({ dark, preset }: PageProps) {
  const [brand, setBrand] = useState<string[]>(preset.brand ? [preset.brand] : []);
  const [store, setStore] = useState<string[]>(preset.store ? [preset.store] : []);
  const [opsStatus, setOpsStatus] = useState<string>("");
  const [ret, setRet] = useState<"all" | "nosale">("all");

  const [md, setMd] = useState<string[]>([]);
  const opsQ = { status: opsStatus, brand, store, md };
  const mvQ = { ret, brand, store, md };
  const ops = useApi<any>("/ops" + qs(opsQ));
  const mv = useApi<any>("/moves" + qs(mvQ));
  const offQ = { brand, md };
  const off = useApi<any>("/offline_goods" + qs(offQ));
  const [rtPri, setRtPri] = useState<string>("");
  const rtQ = { priority: rtPri, brand, store, md };
  const rt = useApi<any>("/rt" + qs(rtQ));
  const counts = ops.data?.counts ?? {};
  const stockOff = (ops.data?.rows ?? []).filter((r: any) => r.target_status === "미운영" && r.stock_qty > 0).length;

  return (
    <div className="space-y-5">
      <ErrorBox msg={ops.err || mv.err || off.err || rt.err} />
      <Filters brand={brand} setBrand={setBrand} store={store} setStore={setStore} md={md} setMd={setMd} />

      <Card>
        <CardBody>
          <SectionTitle title="① 스토어 운영상태 변경"
            sub="브랜드 시트에 있는 매장×SKU = 운영중, 없는 조합 = 미운영. 지금 SCM-HUB 상태와 다른 것만 담습니다. 시트를 정상으로 읽은 브랜드만 대상이며, 재고 등록 전에 먼저 올리세요."
            right={<div className="flex items-center gap-2">{ops.loading && <Spinner />}
              <a href={"/api/ops.csv" + qs({ ...opsQ, detail: "1" })} className="text-xs text-slate-500 underline">상세 포함 CSV</a>
              <DlButton href={"/api/ops.csv" + qs(opsQ)} label="업로드 파일" /></div>} />
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="운영중으로 전환" value={num(counts["운영중"] ?? 0)} tone="good" />
            <Kpi label="미운영으로 전환" value={num(counts["미운영"] ?? 0)} tone={counts["미운영"] ? "bad" : undefined} />
            <Kpi label="미운영 전환인데 매장 재고 있음" value={num(stockOff)} tone={stockOff ? "warn" : undefined} sub="반출 필요 여부 확인" />
            <div className="flex flex-wrap items-center gap-1.5 whitespace-nowrap">
              {[["", "전체"], ["운영중", "운영중"], ["미운영", "미운영"]].map(([v, l]) =>
                <Chip key={v} active={opsStatus === v} onClick={() => setOpsStatus(v)}>{l}</Chip>)}
            </div>
          </div>
          <TotalGrid dark={dark} rows={ops.data?.rows ?? []} columns={opsCols} height={420} />
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`② 재고 보충 등록 ${num(mv.data?.total ?? 0)}줄`}
            sub="같은 SKU 는 묶어서, 판매(7일 → 4주 → 누적)가 좋은 매장이 위로 오도록 정렬합니다. 이동수량 = MFS 배분(대표 바코드). 과재고 반출 수량 = 판매가능 − 고정 수량 이며, 매장에 재고가 있는 바코드별로 줄을 나눕니다."
            right={<div className="flex items-center gap-2">{mv.loading && <Spinner />}
              <Chip active={ret === "all"} onClick={() => setRet("all")}>반출: 과재고 전체</Chip>
              <Chip active={ret === "nosale"} onClick={() => setRet("nosale")}>반출: 4주 무판매만</Chip>
              <DlButton href={"/api/moves.csv" + qs(mvQ)} label="업로드 파일" /></div>} />
          <TotalGrid dark={dark} rows={mv.data?.rows ?? []} columns={moveCols} height={460} />
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`③ 오프라인 판매 Y 전환 필요 ${num(off.data?.total ?? 0)}개 상품`}
            sub="보충 발주 조건 ① — SCM-HUB 상품(UID) 오프라인 판매 여부가 Y 여야 발주가 됩니다. 운영리스트에 있는데 Y 가 아닌 상품입니다. '할 일'이 비제스트 Y 전환이면 비제스트에서 바꾸고, SCM 반영 대기면 이미 바꾼 것이라 넘어오길 기다리면 됩니다. 이 상품들은 MFS 배분이 0 이라 ② 파일에 나오지 않습니다(보충 필요 순 정렬)."
            right={<div className="flex items-center gap-2">{off.loading && <Spinner />}
              <DlButton href={"/api/offline_goods.csv" + qs(offQ)} label="상품 목록" /></div>} />
          <TotalGrid dark={dark} rows={off.data?.rows ?? []} columns={offCols} height={420} />
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <SectionTitle title={`④ RT(점간이동) 추천 ${num(rt.data?.total ?? 0)}줄`}
            sub="MFS 배분(②) 뒤에도 남는 매장 부족분을 다른 매장 재고로 채웁니다. 1순위 = 운영리스트에 없는(미운영) 매장의 판매가능 재고, 2순위 = 운영 매장의 과재고(판매가능 − 고정). 받는 매장은 판매(7일 → 4주 → 누적)가 좋은 곳부터, 오프라인 판매 Y 인 상품만. 2순위로 보낸 수량은 ② 의 과재고 반출 수량에서 뺐습니다. 매장 필터는 보내는·받는 매장 어느 쪽이든 걸립니다."
            right={<div className="flex items-center gap-2">{rt.loading && <Spinner />}
              {[["", "전체"], ["1", "1순위 미운영"], ["2", "2순위 과재고"]].map(([v, l]) =>
                <Chip key={v} active={rtPri === v} onClick={() => setRtPri(v)}>{l}</Chip>)}
              <DlButton href={"/api/rt.csv" + qs(rtQ)} label="RT 추천 CSV" /></div>} />
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="RT 이동수량" value={num(rt.data?.qty ?? 0)} tone="good" />
            <Kpi label="1순위 미운영 매장 재고" value={num(rt.data?.qty_p1 ?? 0)} />
            <Kpi label="2순위 과재고" value={num(rt.data?.qty_p2 ?? 0)} />
            <Kpi label="보내는 매장 수" value={num(rt.data?.from_stores ?? 0)} />
          </div>
          {/* 보내는·받는 매장 재고·판매는 한 매장이 여러 줄에 반복되므로 합계에서 뺀다(이동수량·MFS 재고만 합계) */}
          <TotalGrid dark={dark} rows={rt.data?.rows ?? []} columns={rtCols} height={460} sum={["rt_qty", "mfs_qty"]} />
        </CardBody>
      </Card>
    </div>
  );
}
