# 위탁 운영 (위탁 IPS 웹 전환)

`위탁 IPS` 구글시트 + Apps Script 를 대체하는 사내 웹앱. 브랜드 공유 시트로 운영 상품을 받는 방식은 유지한다.

## 결정 사항 (2026-09-30)
- 호스팅: 별도 Cloud Run 서비스(대시보드와 같은 스택: FastAPI + React, scale-to-zero). 사내 계정 접근.
- 대상 브랜드: `partnerportal.company_brand.offline_md_id IN ('minsu.kim','jieun.kim12')` 자동 추출.
- **모든 결합 키 = 브랜드가 기재한 SKU ID.** 바코드는 속성(대표 1개 + 추가 목록).
- 적정 재고 1단계 = 브랜드 기재 '매장 고정 수량'. 2단계 = 사이즈런·판매 기반 capa (`engine.target_qty` 교체).
- 보충은 매일 가능 기준. 결과는 브랜드 시트에도 배포.

## 구성
| 파일 | 역할 |
|---|---|
| `dbx.py` | Databricks 접속 (env 우선, 로컬은 대시보드 secrets.toml) |
| `queries.py` | 원천 쿼리 — 전부 SKU ID 키. 각 쿼리 위에 정의·함정 주석 |
| `engine.py` | 취합 행 검증 → 원천 결합 → 보충 제안. `python engine.py --csv in.csv --out out.csv` |
| `sheets.py` | 브랜드 폴더의 시트 읽기(서비스 계정). 헤더를 **이름으로** 찾음(브랜드마다 템플릿 열 위치가 다름) |
| `refresh.py` | 시트 읽기 → engine → `cache/rows.parquet` + `meta.json` 스냅샷 |
| `api.py` / `serve.py` | FastAPI (스냅샷만 읽음) + SPA 서빙. 인증은 Cloud Run IAP |
| `web/` | React + Tailwind + AG Grid (대시보드 ui.tsx·Grid.tsx·index.css 재사용) |
| `docs/INTEGRITY-REVIEW.md` | 기존 앱스크립트 쿼리 정합성 점검 결과 |
| `DEPLOY.md` | Cloud Run 서비스 + 매일 새로고침 Job 배포 명령 |

## 로컬 실행
```
.venv\Scripts\python.exe refresh.py                  # 서비스 계정 없으면 BRAND_ROWS_CSV=<csv> 로 대체
.venv\Scripts\python.exe -m uvicorn api:app --port 8010
cd web && npm run build                              # npm 이 프록시에 막히면 대시보드 node_modules 복사
```

## 다음 단계
1. 서비스 계정 발급 + 브랜드 폴더 공유 → 실제 55개 시트로 새로고침 검증
2. 브랜드 시트 결과 배포(값만 덮어쓰기) — 옛 2-2 대체
3. Cloud Run 배포 (DEPLOY.md)
