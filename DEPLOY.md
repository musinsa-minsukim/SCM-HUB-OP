# 배포 (Cloud Run, 전용 GCP 프로젝트 scm-hub-operation)

구성: **서비스 `consign-ips`**(화면·API, IAP 로 사내 계정만) + **Job `consign-ips-refresh`**(매일 새로고침)
+ **GCS 버킷**(스냅샷 공유). 둘 다 요청/실행 때만 돈다 → 대시보드처럼 사실상 무료.
화면의 새로고침 버튼은 서비스 안에서 같은 refresh.py 를 돌린다.

아래 명령은 Cloud Shell 에서 사용자가 실행한다 (권한·결제는 사용자 몫).

```bash
PROJECT=scm-hub-operation   # ⚠️ 프로젝트 ID (이름과 다르면 gcloud projects list 의 PROJECT_ID 로)
REGION=asia-northeast3
SA=consign-ips@$PROJECT.iam.gserviceaccount.com
BUCKET=$PROJECT-consign-ips-cache
gcloud config set project $PROJECT
```

## 1. 서비스 계정 + 버킷 (한 번)
```bash
gcloud iam service-accounts create consign-ips --display-name="위탁 운영"
gcloud storage buckets create gs://$BUCKET --location=$REGION
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
gcloud services enable sheets.googleapis.com drive.googleapis.com iap.googleapis.com iamcredentials.googleapis.com \n  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com
```
**브랜드 파일 폴더 공유** — Drive 에서 폴더 `1DF5dsU2LgA04Gm9CNLC2bBKQhFCitL6Y` 를 위 `$SA` 이메일에
**편집자**로 공유 (읽기만 쓰면 뷰어로도 되지만, 브랜드 시트 결과 배포에 편집자가 필요).
jieun.kim12 소유 파일도 폴더 공유로 함께 적용된다.

## 2. Databricks 토큰 시크릿 (새로 발급한 토큰)
```bash
printf '%s' '<새 토큰>' | gcloud secrets create consign-dbx-token --data-file=-
gcloud secrets add-iam-policy-binding consign-dbx-token --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
```

## 3. 서비스 배포 (코드 폴더에서)
```bash
gcloud run deploy consign-ips --source . --region $REGION \
  --service-account $SA --no-allow-unauthenticated --iap \
  --memory 2Gi --cpu 1 --min-instances 0 --max-instances 2 --timeout 900 \
  --set-env-vars '^;^DATABRICKS_HOST=musinsa-data-ws.cloud.databricks.com;DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/c0ee970a9c3ed562;TARGET_MDS=minsu.kim,jieun.kim12' \
  --set-secrets DATABRICKS_TOKEN=consign-dbx-token:latest \
  --add-volume name=cache,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=cache,mount-path=/mnt/cache
# 사내 계정 전체에 접속 허용 (IAP)
gcloud iap web add-iam-policy-binding --resource-type=cloud-run --service=consign-ips --region=$REGION \
  --member=domain:musinsa.com --role=roles/iap.httpsResourceAccessor
```

## 4. 매일 새로고침 Job (07:30 KST)
```bash
gcloud run jobs deploy consign-ips-refresh --source . --region $REGION --service-account $SA \
  --command python --args refresh.py --memory 2Gi --task-timeout 1800 \
  --set-env-vars '^;^DATABRICKS_HOST=musinsa-data-ws.cloud.databricks.com;DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/c0ee970a9c3ed562;CACHE_DIR=/mnt/cache;TARGET_MDS=minsu.kim,jieun.kim12' \
  --set-secrets DATABRICKS_TOKEN=consign-dbx-token:latest \
  --add-volume name=cache,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=cache,mount-path=/mnt/cache
gcloud run jobs execute consign-ips-refresh --region $REGION --wait   # 첫 스냅샷
gcloud scheduler jobs create http consign-ips-daily --location $REGION --schedule "30 7 * * *" --time-zone "Asia/Seoul" \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/consign-ips-refresh:run" \
  --http-method POST --oauth-service-account-email $SA
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/run.invoker
```

## 확인
- 서비스 URL 접속 → 사내 Google 로그인 → 브랜드 현황에 대상 브랜드와 시트 상태가 보이면 정상
- 시트 상태가 '읽기 실패'(403)면 폴더 공유가 빠진 것

## 대상 MD 추가·변경
대상 브랜드 = `TARGET_MDS` 에 적힌 오프라인 MD(partnerportal offline_md_id)들이 담당하는 브랜드.
MD 가 늘면 **서비스와 새로고침 Job 둘 다** 값을 바꾼다 (값에 쉼표가 있어 `^;^` 구분자 문법 사용). 재배포·코드 수정 불필요.
```bash
MDS='minsu.kim,jieun.kim12,새MD1,새MD2,새MD3'
gcloud run services update consign-ips --region $REGION --update-env-vars "^;^TARGET_MDS=$MDS"
gcloud run jobs update consign-ips-refresh --region $REGION --update-env-vars "^;^TARGET_MDS=$MDS"
```
새 MD 브랜드의 공유 시트도 같은 브랜드 폴더(`1DF5ds…`)에 `{브랜드}_온오프판매및MFS재고` 이름으로 있어야 읽힌다.
