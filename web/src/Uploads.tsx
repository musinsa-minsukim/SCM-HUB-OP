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
  colText("reason", "사유", { minWidth: 200 }),
];

const moveCols: ColDef[] = [
  colText("storage_no", "스토어코드", { pinned: "left", minWidth: 110 }),
  ...PRODUCT_COLS(),
  colText("barcode", "바코드", { minWidth: 150 }),
  { ...colNum("move_qty", "이동수량", "int"), cellStyle: { color: "var(--ratio-up)", fontWeight: 700, textAlign: "right" } },
  { ...colNum("ret_qty", "과재고 반출 수량", "int"), cellStyle: { color: "var(--ratio-down)", fontWeight: 700, textAlign: "right" } },
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
  colNum("need_qty", "보충 필요", "int"),
  colNum("off_cum", "누적 판매", "int"),
  colNum("off_w1", "7일 판매", "int"),
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
  const counts = ops.data?.counts ?? {};
  const stockOff = (ops.data?.rows ?? []).filter((r: any) => r.target_status === "미운영" && r.stock_qty > 0).length;

  return (
    <div className="space-y-5">
      <ErrorBox msg={ops.err || mv.err || off.err} />
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
    </div>
  );
}
