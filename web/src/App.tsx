import { useCallback, useEffect, useState, lazy, Suspense } from "react";
import { Building2, ShieldAlert, Grid3x3, Truck, Search as SearchIcon, RefreshCw, Sun, Moon, Boxes, FileUp } from "lucide-react";
import { api } from "./lib";
import { Spinner } from "./ui";

const Brands = lazy(() => import("./Brands"));
const Issues = lazy(() => import("./Issues"));
const Matrix = lazy(() => import("./Matrix"));
const Replenish = lazy(() => import("./Replenish"));
const Search = lazy(() => import("./Search"));
const Uploads = lazy(() => import("./Uploads"));

const NAV = [
  { key: "brands", label: "브랜드 현황", icon: Building2 },
  { key: "issues", label: "취합 검증", icon: ShieldAlert },
  { key: "matrix", label: "전개 매트릭스", icon: Grid3x3 },
  { key: "replenish", label: "보충·반출", icon: Truck },
  { key: "uploads", label: "업로드 파일", icon: FileUp },
  { key: "search", label: "상품 조회", icon: SearchIcon },
] as const;
export type View = (typeof NAV)[number]["key"];

// 화면 간 이동 시 넘기는 필터 (예: 브랜드 현황에서 브랜드 클릭 → 취합 검증)
export type Nav = (v: View, f?: { brand?: string; store?: string; flag?: string }) => void;

function useDark(): [boolean, () => void] {
  const [dark, setDark] = useState(() => {
    try {
      const s = localStorage.getItem("theme");
      if (s) return s === "dark";
    } catch {}
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch {}
  }, [dark]);
  return [dark, () => setDark((d) => !d)];
}

export default function App() {
  const [dark, toggleDark] = useDark();
  const [view, setView] = useState<View>(() => (location.hash.slice(1) as View) || "brands");
  const [preset, setPreset] = useState<{ brand?: string; store?: string; flag?: string }>({});
  const [status, setStatus] = useState<any>(null);
  const [me, setMe] = useState("");
  const [ver, setVer] = useState(0); // 새로고침 완료 시 화면 재조회 트리거

  const nav: Nav = (v, f = {}) => { setPreset(f); setView(v); location.hash = v; };
  // 뒤로 가기·주소 직접 입력 등 해시 변경을 화면에 반영
  useEffect(() => {
    const h = () => {
      const v = location.hash.slice(1) as View;
      if (NAV.some((n) => n.key === v)) setView(v);
    };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  const loadStatus = useCallback(() => api("/status").then(setStatus).catch(() => {}), []);
  useEffect(() => { loadStatus(); api("/me").then((r) => setMe(r.email)).catch(() => {}); }, [loadStatus]);
  // 새로고침 진행 중이면 5초마다 상태 확인, 끝나면 화면 재조회
  useEffect(() => {
    if (!status?.job?.running) return;
    const t = setInterval(async () => {
      const s = await api("/status").catch(() => null);
      if (!s) return;
      setStatus(s);
      if (!s.job.running) {
        setVer((v) => v + 1);
        if (s.job.error) alert(`새로고침 실패: ${s.job.error}`);
      }
    }, 5000);
    return () => clearInterval(t);
  }, [status?.job?.running]);

  // 새로고침은 서버 백그라운드에서 돈다. 시작만 요청하고, 진행·완료는 /status 폴링(위)으로 본다.
  const [busy, setBusy] = useState<"" | "full" | "scm">("");
  const startRefresh = async (kind: "full" | "scm" = "full") => {
    setBusy(kind);
    try {
      await api(kind === "scm" ? "/refresh/scm" : "/refresh", { method: "POST" });
    } catch (e: any) {
      alert(String(e?.message || e));
    } finally {
      await loadStatus();
      setBusy("");
    }
  };

  const running = !!busy || status?.job?.running;
  const kindNow = busy || (status?.job?.running ? status.job.kind : "");
  const Page = { brands: Brands, issues: Issues, matrix: Matrix, replenish: Replenish, uploads: Uploads, search: Search }[view];
  const title = NAV.find((n) => n.key === view)?.label;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex dark:border-slate-800 dark:bg-slate-900">
        <div className="flex h-16 items-center gap-2.5 border-b border-slate-100 px-5 dark:border-slate-800">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-indigo-600 text-white shadow-sm shadow-indigo-600/25">
            <Boxes size={18} />
          </div>
          <span className="font-semibold tracking-tight text-slate-900 dark:text-slate-50">위탁 운영</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((n) => {
            const active = view === n.key;
            return (
              <button key={n.key} onClick={() => nav(n.key)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-indigo-50 font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-100 dark:bg-indigo-950/70 dark:text-indigo-300 dark:ring-indigo-900/60"
                    : "font-medium text-slate-600 hover:bg-slate-100/70 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                <n.icon size={18} />{n.label}
              </button>
            );
          })}
        </nav>
        <div className="border-t border-slate-100 p-4 text-xs text-slate-400 dark:border-slate-800">
          {me && <div className="mb-1 truncate">{me}</div>}MUSINSA · 사내 전용
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex min-h-16 flex-wrap items-center gap-3 border-b border-slate-200/70 bg-white/70 px-5 py-2 backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-900/70">
          <select value={view} onChange={(e) => nav(e.target.value as View)}
            className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm lg:hidden dark:border-slate-700 dark:bg-slate-800">
            {NAV.map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
          </select>
          <h2 className="hidden text-base font-semibold tracking-tight text-slate-900 lg:block dark:text-slate-50">{title}</h2>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
            <span title="전체 = 브랜드 시트·재고·판매까지 마지막으로 읽은 시각 / SCM = 매장 운영상태·오프라인 판매 여부를 마지막으로 읽은 시각">
              갱신 {status?.refreshed_at?.slice(5, 16) ?? "—"}
              {status?.scm_refreshed_at && status.scm_refreshed_at !== status.refreshed_at && <> · SCM {status.scm_refreshed_at.slice(11, 16)}</>}
              {status?.stock_date && <> · 재고 기준 {status.stock_date.slice(5)}</>}
            </span>
            {status?.job?.running && status.job.step && (
              <span className="text-indigo-600 dark:text-indigo-300" title={`시작 ${status.job.started_at}`}>{status.job.step}…</span>
            )}
            {!status?.job?.running && status?.job?.error && <span className="text-rose-600" title={status.job.error}>새로고침 실패</span>}
            <button onClick={() => startRefresh("scm")} disabled={running}
              title="매장 운영상태(운영중/미운영)·비제스트 오프라인 판매 여부만 다시 읽습니다 (약 30초). 운영상태 업로드·비제스트 변경 후 반영 확인용. 원천 사본 지연 30분~1시간은 그대로"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
              <RefreshCw size={14} className={kindNow === "scm" ? "animate-spin" : ""} />
              {kindNow === "scm" ? "SCM 읽는 중…" : "SCM 상태만"}
            </button>
            <button onClick={() => startRefresh("full")} disabled={running}
              title="브랜드 시트·재고·판매·SCM 상태 전체를 다시 읽습니다 (1~3분)"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
              <RefreshCw size={14} className={kindNow === "full" ? "animate-spin" : ""} />
              {kindNow === "full" ? "읽는 중…" : "새로고침"}
            </button>
            <button onClick={toggleDark} className="rounded-lg p-2 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="테마">
              {dark ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] flex-1 p-4 sm:p-6">
          <Suspense fallback={<div className="flex justify-center p-10"><Spinner /></div>}>
            <Page key={`${view}-${ver}-${JSON.stringify(preset)}`} dark={dark} nav={nav} preset={preset} />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
