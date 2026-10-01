# GitHub + 자동배포 최초 셋업 (위탁 운영 consign-ips)

`git push` → GitHub Actions → Cloud Run(`consign-ips`, 프로젝트 `scm-hub-operation`) 자동배포. **일회성** 설정.
대시보드(`MOC-DASHBOARD-KICKS`)와 같은 구조. 이후 zip 업로드 불필요.

이미 끝난 것 (2026-10-01): 서비스 계정 `consign-ips@scm-hub-operation…` + 브랜드 폴더 공유, 버킷
`scm-hub-operation-consign-ips-cache`, 비밀값 `consign-dbx-token`.

## 1) GitHub 저장소 만들기 + 첫 푸시
1. GitHub 에서 **private** 저장소를 만든다 (예: `musinsa-minsukim/SCM-HUB-OP`). README·.gitignore 없이 빈 저장소로.
2. 로컬 Git Bash:
```bash
cd /c/Users/MUSINSA/musinsa-consign-ips
git remote add origin https://github.com/musinsa-minsukim/SCM-HUB-OP.git
git push -u origin main
```
(첫 push 는 test 는 통과하고 deploy 는 아직 WIF 값이 없어 실패한다 — 정상. 3·4) 후 다시 실행.)

## 2) GCP — 배포용 서비스 계정 + 권한 (Cloud Shell)
```bash
PROJECT=scm-hub-operation
PNUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
gcloud config set project $PROJECT
gcloud iam service-accounts create gh-deployer --display-name="GitHub Actions deployer"
DEPLOYER="gh-deployer@${PROJECT}.iam.gserviceaccount.com"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin \
            roles/storage.admin roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$DEPLOYER" --role=$ROLE
done
# 새 프로젝트: --source 빌드를 돌리는 기본 계정에 빌드 권한
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" --role=roles/run.builder
```

## 3) Workload Identity Federation (키 없이 GitHub ↔ GCP)
```bash
gcloud iam workload-identity-pools create github-pool --location=global --display-name="GitHub pool"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-pool --display-name="GitHub provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='musinsa-minsukim/SCM-HUB-OP'" \
  --issuer-uri="https://token.actions.githubusercontent.com"
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/musinsa-minsukim/SCM-HUB-OP"
echo "WIF_PROVIDER=projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
echo "WIF_SERVICE_ACCOUNT=$DEPLOYER"
```

## 4) GitHub 저장소 → Settings → Secrets and variables → Actions
**Secrets**: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT` (3) 출력값)
**Variables**:
| 이름 | 값 |
|---|---|
| `GCP_PROJECT` | `scm-hub-operation` |
| `GCP_REGION` | `asia-northeast3` |
| `CLOUD_RUN_SERVICE` | `consign-ips` |
| `RUNTIME_SERVICE_ACCOUNT` | `consign-ips@scm-hub-operation.iam.gserviceaccount.com` |
| `TARGET_MDS` | `minsu.kim,jieun.kim12` (MD 추가 시 여기만 바꾸고 재실행) |

## 5) 첫 배포 → 접속 권한 (첫 배포 후 1회)
Actions 탭 → Deploy to Cloud Run → **Run workflow**. 성공하면 Cloud Shell:
```bash
REGION=asia-northeast3
gcloud run services add-iam-policy-binding consign-ips --region $REGION \
  --member="serviceAccount:service-${PNUM}@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker
gcloud iap web add-iam-policy-binding --resource-type=cloud-run --service=consign-ips --region=$REGION \
  --member=domain:musinsa.com --role=roles/iap.httpsResourceAccessor
```
이후로는 코드 수정 → `git push` 만 하면 자동배포.

## 참고
- 비밀값은 저장소에 없다 (Databricks 토큰 = Secret Manager, 브랜드 시트 = 서비스 계정 공유).
- `.gitignore`: cache/(브랜드 CSV·스냅샷), .venv, node_modules, dist, *.csv, 키 파일류.
- 매일 07:30 새로고침(Cloud Run Job + Scheduler)은 DEPLOY.md 4 절 — 첫 배포 확인 후 진행.
