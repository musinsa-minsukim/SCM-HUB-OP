"""원천 쿼리 모음 — 전부 **SKU ID(ocmp.scm_hub.sku.sku_id)** 를 키로 한다.

왜 SKU ID 인가 (2026-09-30 실측, docs/INTEGRITY-REVIEW.md):
- goods_no 는 한 상품에 위탁·매입·글로벌 SKU 가 동시에 달려 있어(헌터·킨·살로몬 등) 키로 모호하다.
- 바코드는 대상 브랜드 공급사 바코드의 5%(1,290개)가 여러 SKU 에 동시에 걸려 있어 키로 쓸 수 없다.
- SKU ID 는 sku 테이블에서 유일(227만 행 = 227만 개)하고, 매장재고(daily_inventory)·MFS 재고·
  매장 운영등록(storage_sku)·오프라인 판매(moss.order_option.sku_id)가 모두 SKU 로 직접 붙는다.

바코드는 키가 아니라 **속성**으로만 다룬다: 재고는 바코드 행을 SKU 로 합산하고, 화면엔 대표 바코드 +
추가 바코드 목록을 보여준다.
"""
from __future__ import annotations

from typing import Iterable

# 대표 바코드 규칙: 활성(sku_barcode.enabled=1) 우선 → 최근 등록 순.
# (최근 28일 POS 스캔의 98.7% 가 이 규칙의 1순위 바코드였다. 나머지 1.3% 는 2순위 바코드로 찍힘.)

KST_TODAY = "to_date(from_utc_timestamp(current_timestamp(), 'Asia/Seoul'))"

# sku → product_option 1건 (ACTIVE 우선). 없으면 조인 시 ~5% 부풀려진다 (scm-hub 스킬 규칙).
# SKU → 상품 옵션(UID) 고르기. 한 SKU 에 ACTIVE 연결이 여러 상품인 경우가 있다(2026-10-02 실측: 대상 4,636 SKU 중 20개).
#   예) S2601130000419: 3417694(판매중, 원래 연결) + 3862861(등록중, 2026-04 추가) → 최신순이면 3862861 로 잘못 잡힘.
#   매장 실판매 UID(moss.order_option.goods_no)와 맞춰 본 우선순위:
#   ① 연결 ACTIVE ② 상품 판매상태 ACTIVE(판매중지·등록중 뒤로) ③ 오프라인 판매 가능 ④ 최근 연결.
# ⚠️ 이 우선순위를 SQL 한 방(연결 3,300만 행 × 상품 조인 × 윈도우)으로 하면 쿼리 하나가 10분+ 걸려 새로고침이 끝나지
#    않았다(2026-10-06). → sku_links(대상 SKU 연결 행) + option_products(그 옵션의 상품) 두 번의 작은 조회 후
#    engine.pick_links 에서 고른다.
def sku_links(fk_sku_ids: Iterable[int]) -> str:
    return f"""
SELECT fk_sku_id, fk_product_option_id, mapping_type, updated_at
FROM ocmp.scm_hub.sku_product_option WHERE fk_sku_id IN ({int_list(fk_sku_ids)})"""


def option_products(option_ids: Iterable[int]) -> str:
    return f"""
SELECT po._id fk_product_option_id, po.option_name, po.option_code,
       p.product_no goods_no, p.product_name, p.offline_sale_enabled, p.platform product_platform, p.sales_status
FROM ocmp.scm_hub.product_option po
JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id
WHERE po._id IN ({int_list(option_ids)})"""


# enabled_store_skus(운영중 매장×SKU 전체, CASE2 후보)용 가벼운 매핑: ACTIVE 우선 → 최근. 브랜드 판별용이라 이걸로 충분
# (UID 가 여러 개인 SKU 도 같은 브랜드). 화면의 상품 정보는 engine 이 pick_links 로 다시 붙인다.
SPO = """spo AS (
  SELECT fk_sku_id, fk_product_option_id FROM (
    SELECT fk_sku_id, fk_product_option_id,
      ROW_NUMBER() OVER (PARTITION BY fk_sku_id
        ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
    FROM ocmp.scm_hub.sku_product_option) WHERE rn = 1)"""


def lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def str_list(vals: Iterable) -> str:
    vals = sorted({str(v) for v in vals if v is not None and str(v) != ""})
    return ", ".join(lit(v) for v in vals) or "NULL"


def int_list(vals: Iterable) -> str:
    vals = sorted({int(v) for v in vals})
    return ", ".join(str(v) for v in vals) or "NULL"


# ── 1) 대상 브랜드: 오프라인 MD 기준 ─────────────────────────────────────────
def target_brands(md_ids: Iterable[str], extra: Iterable[tuple[str, str]] = ()) -> str:
    """MD 기준 + 수동 포함(extra = (com_id, brand) 쌍). 수동 포함분은 target_reason='수동 포함'."""
    extra = list(extra)
    extra_cond = " OR ".join(f"(cb.com_id = {lit(c)} AND cb.brand = {lit(b)})" for c, b in extra) or "FALSE"
    return f"""
SELECT cb.offline_md_id, cb.com_id, cb.brand, g.brand_nm,
       CASE WHEN cb.offline_md_id IN ({str_list(md_ids)}) THEN 'MD 기준' ELSE '수동 포함' END target_reason
FROM musinsa.partnerportal.company_brand cb
LEFT JOIN (SELECT brand, MAX(brand_nm) brand_nm FROM datamart.datamart.goods GROUP BY brand) g
  ON g.brand = cb.brand
WHERE cb.offline_md_id IN ({str_list(md_ids)}) OR {extra_cond}"""


# ── 2) 매장 마스터: shop_no ↔ SCM storage_id ↔ 이름들 ──────────────────────
STORES = """
SELECT CAST(m.shop_no AS INT) shop_no, CAST(m.storage_id AS BIGINT) storage_id,
       m.shop_name, s.name scm_name, s.storage_no
FROM team.commercepm.offline_shopno_storageid m
LEFT JOIN ocmp.scm_hub.storage s ON s._id = CAST(m.storage_id AS BIGINT)"""


# ── 3) SKU 마스터 + 바코드 ──────────────────────────────────────────────────
def sku_master(sku_ids: Iterable[str]) -> str:
    return f"""
WITH s AS (
  SELECT _id fk_sku_id, sku_id, sku_name, sku_style_no, purchase_type, consignment_type,
         operation_status, regular_price
  FROM ocmp.scm_hub.sku WHERE sku_id IN ({str_list(sku_ids)})),
bc AS (
  SELECT sb.fk_sku_id,
         b.supplier_barcode, b.internal_barcode, sb.enabled,
         ROW_NUMBER() OVER (PARTITION BY sb.fk_sku_id
           ORDER BY sb.enabled DESC, sb.created_at DESC) rk
  FROM ocmp.scm_hub.sku_barcode sb
  JOIN ocmp.scm_hub.barcode b ON b._id = sb.fk_barcode_id
  WHERE sb.fk_sku_id IN (SELECT fk_sku_id FROM s)),
bca AS (
  SELECT fk_sku_id,
         MAX(CASE WHEN rk = 1 THEN supplier_barcode END) rep_barcode,
         concat_ws(', ', sort_array(collect_list(CASE WHEN rk > 1 AND enabled = 1 THEN supplier_barcode END))) other_barcodes,
         SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) n_barcode,
         SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END) n_barcode_disabled
  FROM bc GROUP BY fk_sku_id)
SELECT s.sku_id, s.fk_sku_id, s.sku_name, s.sku_style_no, s.purchase_type, s.consignment_type,
       s.operation_status, s.regular_price,
       bca.rep_barcode, bca.other_barcodes, COALESCE(bca.n_barcode, 0) n_barcode,
       COALESCE(bca.n_barcode_disabled, 0) n_barcode_disabled
FROM s
LEFT JOIN bca ON bca.fk_sku_id = s.fk_sku_id"""


def goods_attr(goods_nos: Iterable) -> str:
    """상품(UID) 브랜드·카테고리.
    ⚠️ 2026-10-20: 예전엔 datamart.datamart.goods(하루 배치)에서 읽었는데 최신 UID ~1만 개가 아직 없어
    신상품의 브랜드가 비고 → ① 운영상태 파일 등에서 빠질 수 있었다. 브랜드(업체·브랜드 코드·브랜드명)는
    실시간 원장(musinsa.stock.product + partnerportal.brand, 약 1시간 지연)에서 읽고,
    카테고리·정상가·이미지(표시용)만 datamart 에서 붙인다."""
    return f"""
WITH p AS (
  SELECT CAST(id AS BIGINT) goods_no, company_id com_id, brand_id brand FROM (
    SELECT id, company_id, brand_id, ROW_NUMBER() OVER (PARTITION BY id ORDER BY ut DESC) rn
    FROM musinsa.stock.product WHERE id IN ({int_list(int(g) for g in goods_nos)})) WHERE rn = 1),
b AS (
  SELECT brand, brand_nm FROM (
    SELECT brand, brand_nm, ROW_NUMBER() OVER (PARTITION BY brand ORDER BY tr_ts DESC NULLS LAST, ut DESC) rn
    FROM musinsa.partnerportal.brand WHERE Op IS NULL OR Op != 'delete') WHERE rn = 1),
dg AS (
  SELECT goods_no, brand_nm, small_nm, normal_price, img FROM (
    SELECT CAST(goods_no AS BIGINT) goods_no, brand_nm, small_nm, normal_price, img,
           ROW_NUMBER() OVER (PARTITION BY goods_no ORDER BY goods_no) rn
    FROM datamart.datamart.goods WHERE goods_no IN ({str_list(goods_nos)})) WHERE rn = 1)
SELECT CAST(p.goods_no AS STRING) goods_no, COALESCE(b.brand_nm, dg.brand_nm) brand_nm, p.com_id, p.brand,
       dg.small_nm, dg.normal_price, dg.img
FROM p LEFT JOIN b ON b.brand = p.brand LEFT JOIN dg ON dg.goods_no = p.goods_no"""


# (재고 기준일 = daily_inventory 최근 5일 안의 MAX(stock_date) — 전체 스캔 방지)
# ── 4) 매장 재고 (SCM-HUB daily_inventory, 당일 진행분) ─────────────────────
# daily_inventory 는 (SKU × 바코드 × 창고) 행이다. 반드시 SKU 로 SUM 한다.
#   ⚠️ 옛 2-5 는 SELECT DISTINCT 라 두 바코드 행 값이 같으면 하나로 합쳐져 사라졌다.
# 재고 정의 (한 기준으로 통일):
#   stock_qty     = end_quantity            (실물 재고, 당일 입출고·판매 반영)
#   avail_qty     = end_available_quantity  (판매가능 = 실물 − 출고예정 등 묶인 수량) ← 보충 계산 기준
#   incoming_qty  = 이동 입고 예정 = expected_in(편집샵 보충 요청) + moving_in(편집샵 보충 확정 후 이동중)
#                   (사용자 정의 2026-10-21. 직납 예정 expected_inbound 는 direct_in_qty 로 따로 표시, 계산 제외)
#   outgoing_qty  = 매장 반납/반출 예정(매장 → MFS·파트너사) = expected_out + moving_out + expected_vendor_return
#   보충 계산 = 예상 재고(현재고 + 이동 입고 예정 − 반납/반출 예정) 기준 (engine.replenish)
#   ⚠️ 옛 2-5 의 start + completed_in 은 오늘 판매·출고를 빼지 않아 재고를 부풀린다.
def store_stock(fk_sku_ids: Iterable[int], storage_ids: Iterable[int]) -> str:
    return f"""
WITH d AS (SELECT MAX(stock_date) d FROM ocmp.scm_hub.daily_inventory
                   WHERE stock_date >= date_format(date_sub(current_date(), 5), 'yyyy-MM-dd'))
SELECT di.fk_sku_id, di.fk_storage_id storage_id,
       CAST(SUM(di.end_quantity) AS BIGINT) stock_qty,
       CAST(SUM(di.end_available_quantity) AS BIGINT) avail_qty,
       CAST(SUM(di.expected_in_quantity + di.moving_in_quantity) AS BIGINT) incoming_qty,
       CAST(SUM(di.expected_in_quantity) AS BIGINT) req_in_qty,
       CAST(SUM(di.moving_in_quantity) AS BIGINT) moving_in_qty,
       CAST(SUM(di.expected_inbound_quantity) AS BIGINT) direct_in_qty,
       CAST(SUM(di.expected_out_quantity + di.moving_out_quantity + COALESCE(di.expected_vendor_return_quantity, 0)) AS BIGINT) outgoing_qty,
       CAST(SUM(di.end_defect_quantity) AS BIGINT) defect_qty,
       COUNT(DISTINCT CASE WHEN di.end_quantity <> 0 THEN di.fk_barcode_id END) n_barcode_with_stock,
       MAX(di.stock_date) stock_date, MAX(di.updated_at) updated_at
FROM ocmp.scm_hub.daily_inventory di
WHERE di.stock_date = (SELECT d FROM d)
  AND di.fk_sku_id IN ({int_list(fk_sku_ids)})
  AND di.fk_storage_id IN ({int_list(storage_ids)})
GROUP BY di.fk_sku_id, di.fk_storage_id"""


# ── 5) 매장 운영 등록 (SCM-HUB storage_sku) ────────────────────────────────
# 등록이 없거나 OPERATION_ENABLED 가 아니면 SCM-HUB 에서 그 매장으로 이동지시를 낼 수 없다.
def storage_sku(fk_sku_ids: Iterable[int], storage_ids: Iterable[int]) -> str:
    return f"""
SELECT fk_sku_id, fk_storage_id storage_id, status storage_status, first_inbound, cooldown_until
FROM ocmp.scm_hub.storage_sku
WHERE fk_sku_id IN ({int_list(fk_sku_ids)}) AND fk_storage_id IN ({int_list(storage_ids)})"""


# ── 5-1) 매장 × SKU × 바코드 재고 — 반출 파일은 실제 재고가 있는 바코드로 줄을 나눠야 한다 ──
def store_stock_barcode(fk_sku_ids: Iterable[int], storage_ids: Iterable[int]) -> str:
    return f"""
WITH d AS (SELECT MAX(stock_date) d FROM ocmp.scm_hub.daily_inventory
                   WHERE stock_date >= date_format(date_sub(current_date(), 5), 'yyyy-MM-dd'))
SELECT di.fk_sku_id, di.fk_storage_id storage_id, b.supplier_barcode barcode,
       CAST(SUM(di.end_available_quantity) AS BIGINT) avail_qty
FROM ocmp.scm_hub.daily_inventory di
JOIN ocmp.scm_hub.barcode b ON b._id = di.fk_barcode_id
WHERE di.stock_date = (SELECT d FROM d)
  AND di.fk_sku_id IN ({int_list(fk_sku_ids)}) AND di.fk_storage_id IN ({int_list(storage_ids)})
  AND di.end_available_quantity > 0
GROUP BY di.fk_sku_id, di.fk_storage_id, b.supplier_barcode"""


# ── 5-2) 지금 SCM-HUB 에서 '운영(OPERATION_ENABLED)' 인 매장×SKU (대상 브랜드 위탁 SKU) ──
# 브랜드 시트에 없는 조합은 미운영이어야 하므로, 이 목록 − 시트 = '미운영' 으로 바꿀 대상.
# 상태값: OPERATION_ENABLED=운영 / CREATED(등록만)·OPERATION_DISABLED·DELETED = 미운영.
def enabled_store_skus(storage_ids: Iterable[int], brand_pairs: Iterable[tuple[str, str]]) -> str:
    pairs = " OR ".join(f"(g.com_id = {lit(c)} AND g.brand = {lit(b)})" for c, b in brand_pairs) or "FALSE"
    return f"""
WITH ss AS (SELECT fk_sku_id, fk_storage_id storage_id, status, status_reason
       FROM ocmp.scm_hub.storage_sku
       WHERE status = 'OPERATION_ENABLED' AND fk_storage_id IN ({int_list(storage_ids)})),
{SPO},
d AS (SELECT MAX(stock_date) d FROM ocmp.scm_hub.daily_inventory
                   WHERE stock_date >= date_format(date_sub(current_date(), 5), 'yyyy-MM-dd')),
st AS (SELECT fk_sku_id, fk_storage_id storage_id, CAST(SUM(end_quantity) AS BIGINT) stock_qty
       FROM ocmp.scm_hub.daily_inventory
       WHERE stock_date = (SELECT d FROM d) AND fk_storage_id IN ({int_list(storage_ids)})
         AND fk_sku_id IN (SELECT fk_sku_id FROM ss)
       GROUP BY 1, 2)
SELECT ss.storage_id, s.sku_id, ss.fk_sku_id, ss.status_reason, g.brand_nm, g.com_id, g.brand,
       p.product_no goods_no, p.product_name, po.option_name, COALESCE(st.stock_qty, 0) stock_qty
FROM ss
JOIN ocmp.scm_hub.sku s ON s._id = ss.fk_sku_id AND s.purchase_type = 'CONSIGNMENT'
JOIN spo ON spo.fk_sku_id = ss.fk_sku_id
JOIN ocmp.scm_hub.product_option po ON po._id = spo.fk_product_option_id
JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id
JOIN (SELECT DISTINCT goods_no, brand_nm, com_id, brand FROM datamart.datamart.goods) g ON g.goods_no = p.product_no
LEFT JOIN st ON st.fk_sku_id = ss.fk_sku_id AND st.storage_id = ss.storage_id
WHERE {pairs}"""


# ── 6) MFS 재고 (매일 04시 KST 재생성 스냅샷, sku_id 키) ──────────────────
# ── (참고) 보충 발주 조건 ① '비제스트 UID 오프라인 판매 여부' ─────────────────────
# = ocmp.scm_hub.product.offline_sale_enabled (사용자 확인 2026-10-01: 5778332·1343131 둘 다 Y → True).
# ⚠️ musinsa.bizest.goods.offline_goods_yn 은 '오프라인 전용 상품' 표시라 다른 항목이다(Y 대부분 [오프라인 전용]).
# ⚠️ product 는 product_no 가 같은 29CM 상품 행이 따로 있다 → product_no 로 찾지 말고 SKU 가 연결된 product 행에서 읽는다
#    (sku_master 의 p.offline_sale_enabled).


def bizest_offline(goods_nos: Iterable[int]) -> str:
    """비제스트 원장의 상품(UID) 오프라인 판매 여부 — musinsa.stock.product.for_offline_sale (1 = Y).
    SCM-HUB product.offline_sale_enabled 는 이 값이 넘어간 사본이다. 넘어가는 데 몇 시간~하루 걸리는 경우가 있어
    (2026-10-01 실측: 비제스트 14:33 변경 → SCM-HUB 다음날 10:33) 둘을 나란히 보여 준다.
    ut 는 KST 값이 UTC 표기로 저장돼 있다."""
    return f"""
SELECT CAST(id AS BIGINT) goods_no, MAX(for_offline_sale) bz_offline, MAX(ut) bz_offline_ut
FROM musinsa.stock.product WHERE id IN ({int_list(goods_nos)}) GROUP BY id"""


# MFS 재고 = SCM-HUB 일별 재고(당일 진행분, 수시 갱신)에서 MFS 센터('…-온라인' 창고, 29CM 센터 제외)의 판매가능 합.
# ⚠️ 2026-10-20: 예전 원천 team.commercepm.mfs_stock_daily 는 매일 04시 스냅샷이라, 아침에 입고된 신상(야세 16 SKU,
#    07:18 입고 완료)이 하루 종일 0 → MFS 배분 0 → ② 재고 보충에 안 나왔다. 실시간 합은 스냅샷과 96% 일치(나머지는 당일 입출고).
#    스냅샷 테이블은 더 이상 쓰지 않는다(mfs_inbound_qty 는 호환용 0 — 화면의 'MFS 입고 예정'은 mfs_inbound 쿼리, 실시간).
# ── 오프라인 판매 여부 동기화 점검 (브랜드 운영리스트와 무관, 2026-10-22 사용자 요청) ──────────────
# 비제스트 원장(musinsa.stock.product.for_offline_sale, ut=KST 값이 UTC 표기) vs SCM-HUB product.offline_sale_enabled
# (MUSINSA 플랫폼 행만 — 같은 product_no 의 29CM 행 제외). 둘 다 ~15분~1시간 지연 사본.
def offline_sync(uids: Iterable[int] | None = None, only_mismatch: bool = False, brand: str | None = None,
                 bm: str | None = None) -> str:
    """bm: None = 매입(S)+위탁(P) 전체, 'P' 위탁, 'S' 매입 (2026-10-22 사용자: 매입도 필요)."""
    cond = [f"b.id IN ({int_list(uids)})"] if uids is not None else ["b.business_model_type IN ('P', 'S')"]
    if bm in ("P", "S"):
        cond.append(f"b.business_model_type = {lit(bm)}")
    if brand:
        cond.append(f"(b.brand_id = {lit(brand)} OR br.brand_nm = {lit(brand)})")
    mis = ("AND p._id IS NOT NULL AND (CASE WHEN b.for_offline_sale = 1 THEN 1 ELSE 0 END) <> "
           "(CASE WHEN p.offline_sale_enabled THEN 1 ELSE 0 END)") if only_mismatch else ""
    return f"""
WITH b AS (
  SELECT id, for_offline_sale, ut, business_model_type, brand_id, company_id, default_name FROM (
    SELECT id, for_offline_sale, ut, business_model_type, brand_id, company_id, default_name,
           ROW_NUMBER() OVER (PARTITION BY id ORDER BY ut DESC) rn
    FROM musinsa.stock.product) WHERE rn = 1),
br AS (
  SELECT brand, brand_nm FROM (
    SELECT brand, brand_nm, ROW_NUMBER() OVER (PARTITION BY brand ORDER BY tr_ts DESC NULLS LAST, ut DESC) rn
    FROM musinsa.partnerportal.brand WHERE Op IS NULL OR Op != 'delete') WHERE rn = 1),
p AS (
  SELECT _id, product_no, offline_sale_enabled, updated_at, sales_status FROM (
    SELECT _id, product_no, offline_sale_enabled, updated_at, sales_status,
           ROW_NUMBER() OVER (PARTITION BY product_no ORDER BY updated_at DESC) rn
    FROM ocmp.scm_hub.product WHERE platform = 'MUSINSA') WHERE rn = 1)
SELECT CAST(b.id AS BIGINT) uid, b.default_name product_name, br.brand_nm, b.brand_id brand, b.company_id com_id,
       b.business_model_type bm,
       CASE WHEN b.for_offline_sale = 1 THEN 'Y' ELSE 'N' END bz_offline_yn,
       date_format(b.ut, 'yyyy-MM-dd HH:mm') bz_offline_ut,
       CASE WHEN p._id IS NULL THEN '' WHEN p.offline_sale_enabled THEN 'Y' ELSE 'N' END scm_offline_yn,
       date_format(from_utc_timestamp(p.updated_at, 'Asia/Seoul'), 'yyyy-MM-dd HH:mm') scm_updated_at,
       p.sales_status scm_sales_status
FROM b
LEFT JOIN br ON br.brand = b.brand_id
LEFT JOIN p ON p.product_no = CAST(b.id AS STRING)
WHERE {" AND ".join(cond)} {mis}
ORDER BY b.ut DESC
LIMIT 20000"""


# 갱신 주기 표시용 (2026-10-22 사용자 요청)
#  - 비제스트 사본(musinsa.stock.product_history, rt = KST 값이 UTC 표기) / SCM-HUB 상품 사본(product_version, updated_at UTC)
#    둘 다 '매시 정각까지의 변경'을 한 시간 단위로 복사한다(실측).
#  - 연동 지연 = 최근 7일 비제스트 for_offline_sale 변경 시각 → SCM-HUB 에 같은 값이 처음 나타난 시각.
SCM_PRODUCT_VERSION = "ocmp.scm_hub_tables.product_version_1761524566"


def offline_sync_freshness() -> str:
    return f"""
WITH h AS (
  SELECT product_id, for_offline_sale f, rt,
         LAG(for_offline_sale) OVER (PARTITION BY product_id ORDER BY rt) pf
  FROM musinsa.stock.product_history WHERE rt >= current_timestamp() - INTERVAL 10 DAYS),
ev AS (SELECT product_id, f, to_timestamp(date_format(rt, 'yyyy-MM-dd HH:mm:ss')) t
       FROM h WHERE pf IS NOT NULL AND f <> pf AND rt >= current_timestamp() - INTERVAL 7 DAYS),
v AS (SELECT product_no, offline_sale_enabled e, from_utc_timestamp(updated_at, 'Asia/Seoul') u
      FROM {SCM_PRODUCT_VERSION} WHERE platform = 'MUSINSA' AND updated_at >= current_timestamp() - INTERVAL 8 DAYS),
m AS (SELECT ev.product_id, ev.t, MIN(v.u) first_u
      FROM ev LEFT JOIN v ON v.product_no = CAST(ev.product_id AS STRING) AND v.e = (ev.f = 1) AND v.u >= ev.t
      GROUP BY 1, 2),
now_kst AS (SELECT from_utc_timestamp(current_timestamp(), 'Asia/Seoul') n)
SELECT
  date_format((SELECT n FROM now_kst), 'yyyy-MM-dd HH:mm') now_kst,
  (SELECT date_format(MAX(rt), 'yyyy-MM-dd HH:mm') FROM musinsa.stock.product_history WHERE rt >= current_timestamp() - INTERVAL 2 DAYS) bz_asof,
  (SELECT date_format(from_utc_timestamp(MAX(updated_at), 'Asia/Seoul'), 'yyyy-MM-dd HH:mm') FROM {SCM_PRODUCT_VERSION}
     WHERE updated_at >= current_timestamp() - INTERVAL 2 DAYS) scm_asof,
  COUNT(*) n_events,
  SUM(CASE WHEN first_u IS NULL THEN 1 ELSE 0 END) n_pending,
  SUM(CASE WHEN first_u IS NULL AND t < (SELECT n FROM now_kst) - INTERVAL 24 HOURS THEN 1 ELSE 0 END) n_pending_24h,
  ROUND(percentile((unix_timestamp(first_u) - unix_timestamp(t)) / 60, 0.5)) p50_min,
  ROUND(percentile((unix_timestamp(first_u) - unix_timestamp(t)) / 60, 0.9)) p90_min
FROM m"""


def uids_by_style(styles: Iterable[str]) -> str:
    """스타일넘버 → UID (비제스트 상품 원장 style_number, 대소문자·앞뒤 공백 무시, 정확히 일치)."""
    return f"""
SELECT CAST(id AS BIGINT) uid, style_number FROM (
  SELECT id, style_number, ROW_NUMBER() OVER (PARTITION BY id ORDER BY ut DESC) rn
  FROM musinsa.stock.product
  WHERE upper(trim(style_number)) IN ({str_list(s.strip().upper() for s in styles)})) WHERE rn = 1
LIMIT 5000"""


def mfs_stock(sku_ids: Iterable[str]) -> str:
    return f"""
WITH d AS (SELECT MAX(stock_date) d FROM ocmp.scm_hub.daily_inventory
           WHERE stock_date >= date_format(date_sub(current_date(), 5), 'yyyy-MM-dd')),
s AS (SELECT _id, sku_id FROM ocmp.scm_hub.sku WHERE sku_id IN ({str_list(sku_ids)})),
live AS (
  SELECT s.sku_id, CAST(SUM(GREATEST(di.end_available_quantity, 0)) AS BIGINT) mfs_qty
  FROM ocmp.scm_hub.daily_inventory di
  JOIN s ON s._id = di.fk_sku_id
  JOIN ocmp.scm_hub.storage sto ON sto._id = di.fk_storage_id
   AND sto.name LIKE '%온라인%' AND sto.name NOT LIKE '%29CM%'
  WHERE di.stock_date = (SELECT d FROM d)
  GROUP BY s.sku_id)
SELECT s.sku_id, COALESCE(live.mfs_qty, 0) mfs_qty, CAST(0 AS BIGINT) mfs_inbound_qty
FROM s
LEFT JOIN live ON live.sku_id = s.sku_id"""


# ── 6-1) MFS 입고 예정 (브랜드 → MFS 센터, SCM-HUB 입고 원장 실시간) ─────────
# inbound_type = WAREHOUSE_INBOUND(브랜드→센터), 목적지 = '온라인' 센터(여주·이천 MFS),
# 상태 EXPECTED(예정)/ARRIVED(도착·검수 전)/PARTIALLY_INBOUNDED(부분 입고) 의 남은 수량(요청 − 완료).
# 입고 예정일이 7일 넘게 지난 건은 대부분 정리 안 된 문서라(대상 SKU 실측 ~1.8만 개) 따로 '지연' 으로 뺀다.
# ⚠️ mfs_stock_daily.mfs_inbound_expected_qty(새벽 스냅샷)는 어떤 조건으로도 재현되지 않아 쓰지 않는다.
def mfs_inbound(sku_ids: Iterable[str]) -> str:
    cut = "date_format(date_sub(current_date(), 7), 'yyyy-MM-dd')"
    return f"""
WITH b AS (
  SELECT s.sku_id, ib.expected_inbound_date eid,
         ii.inbound_requested_quantity - COALESCE(ii.inbound_completed_quantity, 0) q
  FROM ocmp.scm_hub.inbound ib
  JOIN ocmp.scm_hub.inbound_item ii ON ii.fk_inbound_id = ib._id
  JOIN ocmp.scm_hub.sku s ON s._id = ii.fk_sku_id
  JOIN ocmp.scm_hub.storage sto ON sto._id = ib.fk_destination_storage_id AND sto.name LIKE '%온라인%'
  WHERE ib.inbound_type = 'WAREHOUSE_INBOUND'
    AND ii.inbound_item_status IN ('EXPECTED', 'ARRIVED', 'PARTIALLY_INBOUNDED')
    AND s.sku_id IN ({str_list(sku_ids)}))
SELECT sku_id,
  CAST(SUM(CASE WHEN eid >= {cut} THEN q ELSE 0 END) AS BIGINT) mfs_in_qty,
  CAST(SUM(CASE WHEN eid <  {cut} OR eid IS NULL THEN q ELSE 0 END) AS BIGINT) mfs_in_late_qty,
  MIN(CASE WHEN eid >= {cut} AND q > 0 THEN eid END) mfs_in_date
FROM b WHERE q > 0 GROUP BY sku_id"""


# ── 7) 오프라인 판매 (MOSS, sku_id 직접) ────────────────────────────────────
# 순판매 = 완료주문(+) − 환불(−), 스킬 musinsa-offline-sales 정의 그대로.
# moss.order_option.sku_id 는 2026-08-24 이후 100% 채워짐 → 그 이전 행은 바코드로 SKU 를 찾는다
#   (POS 바코드는 최근 28일 100% 해당 SKU 의 supplier_barcode 였다). 바코드가 여러 SKU 에 걸리면
#   후보 중 위탁(CONSIGNMENT) 을 우선한다.
# 주차: W1 = D-7~D-1 (= 화면의 '7일 판매'), W2 = D-14~D-8, W3, W4 (완료된 날만, 오늘 제외).
# off_cum = 누적 판매: 그 매장에서 해당 SKU 의 전 기간 순판매(주문 − 환불).
def offline_sales(fk_sku_ids: Iterable[int]) -> str:
    wk = lambda a, b: f"l.d BETWEEN date_sub({KST_TODAY}, {a}) AND date_sub({KST_TODAY}, {b})"
    return f"""
WITH sk AS (SELECT _id fk_sku_id, sku_id, purchase_type FROM ocmp.scm_hub.sku WHERE _id IN ({int_list(fk_sku_ids)})),
bc AS (
  SELECT supplier_barcode, sku_id FROM (
    SELECT b.supplier_barcode, sk.sku_id,
           ROW_NUMBER() OVER (PARTITION BY b.supplier_barcode
             ORDER BY CASE WHEN sk.purchase_type='CONSIGNMENT' THEN 0 ELSE 1 END, sb.enabled DESC) rn
    FROM ocmp.scm_hub.sku_barcode sb
    JOIN ocmp.scm_hub.barcode b ON b._id = sb.fk_barcode_id
    JOIN sk ON sk.fk_sku_id = sb.fk_sku_id) WHERE rn = 1),
oo AS (   -- 누적 판매라 기간 제한 없음. OR 조건 한 번 대신 두 갈래로 나눠야 빠르다(200초 → 수십 초)
  SELECT order_id, sku_id, quantity FROM ocmp.moss.order_option
  WHERE sku_id IN (SELECT sku_id FROM sk)                                   -- 2026-08-24 이후: sku_id 직접
  UNION ALL
  SELECT oo.order_id, bc.sku_id, oo.quantity FROM ocmp.moss.order_option oo
  JOIN bc ON bc.supplier_barcode = oo.barcode
  WHERE oo.sku_id IS NULL OR oo.sku_id = ''),  -- sku_id 가 빈 주문(주로 2026-08-24 이전): 바코드로
ord AS (
  SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) d, om.shop_no, oo.sku_id, 1 sgn, oo.quantity q
  FROM oo JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
  WHERE om.dummy_order = 0 AND om.order_status = 50),
ref AS (
  SELECT rc.d, om.shop_no, oo.sku_id, -1 sgn, oo.quantity q
  FROM (SELECT order_id, CAST(created_at AS DATE) d FROM ocmp.moss.claim
        WHERE claim_type = 'REFUND'
        GROUP BY order_id, CAST(created_at AS DATE)) rc
  JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
  JOIN oo ON oo.order_id = rc.order_id
  WHERE om.dummy_order = 0),
l AS (SELECT * FROM ord UNION ALL SELECT * FROM ref)
SELECT l.sku_id, CAST(l.shop_no AS INT) shop_no,
  CAST(SUM(CASE WHEN {wk(7, 1)}   THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_w1,
  CAST(SUM(CASE WHEN {wk(14, 8)}  THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_w2,
  CAST(SUM(CASE WHEN {wk(21, 15)} THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_w3,
  CAST(SUM(CASE WHEN {wk(28, 22)} THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_w4,
  CAST(SUM(CASE WHEN l.d = {KST_TODAY} THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_today,
  CAST(SUM(l.sgn*l.q) AS BIGINT) off_cum,                       -- 누적 판매 (그 매장에서 전 기간 순판매)
  CAST(MIN(CASE WHEN l.sgn = 1 THEN l.d END) AS STRING) first_sale_date
FROM l WHERE l.sku_id IS NOT NULL
GROUP BY l.sku_id, l.shop_no"""


# ── 8) 온라인 판매 (옵션 단위, option_code = order_opt.goods_option_no) ─────
# 옵션명 문자열 조인(옛 2-4)은 대상 브랜드 온라인 판매의 27% 를 놓쳤다. 옵션 코드는 100% 일치.
# 온라인 판매는 옵션 단위라 같은 옵션의 여러 SKU(위탁/매입/글로벌)는 같은 값을 공유한다.
# 주문 기준(ord_state >= 10 결제완료 이상), 반품 미차감 — 화면에 그렇게 표기한다.
def online_sales(option_codes: Iterable[str]) -> str:
    wk = lambda a, b: f"CAST(ord_date AS DATE) BETWEEN date_sub({KST_TODAY}, {a}) AND date_sub({KST_TODAY}, {b})"
    return f"""
SELECT CAST(goods_option_no AS STRING) option_code,
  CAST(SUM(CASE WHEN {wk(7, 1)}  THEN qty ELSE 0 END) AS BIGINT) onl_w1,
  CAST(SUM(CASE WHEN {wk(28, 1)} THEN qty ELSE 0 END) AS BIGINT) onl_4w
FROM musinsa.order_group.order_opt
WHERE ord_date >= date_sub({KST_TODAY}, 29) AND ord_state >= 10
  AND CAST(goods_option_no AS STRING) IN ({str_list(option_codes)})
GROUP BY goods_option_no"""
