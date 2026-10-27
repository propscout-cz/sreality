// Railway settings for the three production projects (#2060): API, realtime-worker and vite; each runs service
// `sreality`, and realtime-worker also the job runner `job-runner` (#2025).
// Deploys never read this file: a change takes effect only when the operator applies it, linked to ONE project, inside an
// announced window (scripts/railway_iac_wizard.sh). `railway config plan --detailed-exit-code` = 0 means Railway matches it.
// Every live variable NAME is listed (values stay in Railway): a name missing here plans as a destructive delete.
import { defineRailway, github, preserve, project, service } from "railway/iac";

const REPO = "waiff/sreality";               // the services' GitHub source; "propscout/sreality" once #2008 re-links them
const REGION = "europe-west4-drams3a";       // a plan cannot see this (one region, one replica): describe-service does
const DOCKERFILE = { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" };
// No override existed before #2060, so each replica had the Pro ceiling (the worker's cgroup reads 22,888 MiB = 24e9 B).
const LIMITS = { containers: { cpu: 24, memoryBytes: 24_000_000_000 } };
const API_DRAINING_SECONDS = 0;              // ruling R4
const VITE_DRAINING_SECONDS = 0;             // ruling R4
// The API and vite declare no start command: each runs its image's own (Dockerfile CMD; caddy:2-alpine CMD), ruling R5.
// E949 + E941, pinned by tests/scraper/test_realtime_worker_autodedup.py.
const WORKER_START = "env MALLOC_ARENA_MAX=2 python -m scraper.realtime_worker";
const WORKER_DRAINING_SECONDS = 30;
// The job runner (Actions exit §4.2): scraper/jobs.py's runner entries on two identical replicas. Exec form, so python
// is PID 1 and gets Railway's SIGTERM. tests/test_railway_config.py ties the drain to scraper/job_runner.py and the
// memory to jobs.REPLICA_MEMORY_MB (Railway counts decimal bytes).
const RUNNER_START = "python -m scraper.job_runner run";
const RUNNER_REPLICAS = 2;
const RUNNER_CPUS = 4;
const RUNNER_MEMORY_BYTES = 8000000000;
const RUNNER_DRAINING_SECONDS = 250;
// Every repo tree the root Dockerfile copies, plus the files that build it (a census pins it to the COPY lines).
const BACKEND_WATCH = ["/Dockerfile", "/.dockerignore", "/pyproject.toml", "/scraper/**", "/toolkit/**", "/api/**", "/scripts/**", "/location_data/**", "/contracts/**", "/data/**", "/autodedup/**"];
const kept = (...names: string[]) => Object.fromEntries(names.map((name) => [name, preserve()]));

const api = (name: string) => project(name, { resources: [service("sreality", {
  source: github(REPO),
  build: DOCKERFILE,
  healthcheck: "/health",
  healthcheckTimeout: 30,
  replicas: { [REGION]: 1 },
  deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 3,
            drainingSeconds: API_DRAINING_SECONDS, limitOverride: LIMITS },
  domains: [{ domain: "api.propscout.cz", port: 8080 }],   // dashboard-owned: IaC cannot register a domain
  env: { EXTRAS: "api", ...kept(
    "ANTHROPIC_API_KEY", "CORS_ALLOW_ORIGINS", "GEMINI_API_KEY", "GH_DISPATCH_TOKEN", "MAPY2_CZ_API_KEY",
    "MAPY_CZ_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID", "R2_BUCKET_NAME",
    "R2_SECRET_ACCESS_KEY", "RUNPOD_API_KEY", "SUPABASE_DB_SESSION_URL", "SUPABASE_DB_URL", "SUPABASE_URL",
    "TENANT_POOL_DB_URL", "VALUO_API_TOKEN", "VALUO_SANDBOX_API_TOKEN", "VAPID_PRIVATE_KEY") },
})] });

const jobRunner = (sreality: ReturnType<typeof service>) => service("job-runner", {
  source: github(REPO, { checkSuites: false }),
  build: { ...DOCKERFILE, watchPatterns: BACKEND_WATCH },
  start: RUNNER_START,
  replicas: { [REGION]: RUNNER_REPLICAS },
  deploy: { restartPolicyType: "ALWAYS", drainingSeconds: RUNNER_DRAINING_SECONDS,
            limitOverride: { containers: { cpu: RUNNER_CPUS, memoryBytes: RUNNER_MEMORY_BYTES } } },
  // The worker's secrets, by reference (${{sreality.NAME}}): a rotation changes one place. SUPABASE_DB_URL (#2025-R3);
  // the R2 set (#2032).
  env: { EXTRAS: "api,clip,geo", HF_HUB_OFFLINE: "1", SUPABASE_DB_URL: sreality.env.SUPABASE_DB_URL,
         R2_ACCOUNT_ID: sreality.env.R2_ACCOUNT_ID, R2_ACCESS_KEY_ID: sreality.env.R2_ACCESS_KEY_ID,
         R2_SECRET_ACCESS_KEY: sreality.env.R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME: sreality.env.R2_BUCKET_NAME },
});

const worker = (name: string) => {
  const sreality = service("sreality", {
  source: github(REPO),
  build: DOCKERFILE,
  start: WORKER_START,
  replicas: { [REGION]: 1 },
  deploy: { restartPolicyType: "ALWAYS", drainingSeconds: WORKER_DRAINING_SECONDS, limitOverride: LIMITS },
  env: { EXTRAS: "api", ...kept(
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID",
    "R2_BUCKET_NAME", "R2_SECRET_ACCESS_KEY", "REALTIME_WORKER_ENABLED", "RUNPOD_API_KEY", "SCRAPER_PROXY_URL",
    "SUPABASE_DB_SESSION_URL", "SUPABASE_DB_URL", "VAPID_PRIVATE_KEY") },
  });
  return project(name, { resources: [sreality, jobRunner(sreality)] });
};

const vite = (name: string) => project(name, { resources: [service("sreality", {
  source: github(REPO, { rootDirectory: "/frontend" }),
  build: { ...DOCKERFILE, watchPatterns: ["/frontend/**"] },   // frontend/Dockerfile; the dashboard's spelling
  healthcheck: "/healthz",
  healthcheckTimeout: 30,
  replicas: { [REGION]: 1 },
  deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 3,
            drainingSeconds: VITE_DRAINING_SECONDS, limitOverride: LIMITS },
  domains: [{ domain: "app.propscout.cz", port: 8080 }],
  env: kept("VITE_API_BASE_URL", "VITE_SUPABASE_ANON_KEY", "VITE_SUPABASE_URL"),   // frontend/Dockerfile build args
})] });

// Keyed by id AND name, so a wrong `railway link` can never plan one project's settings onto another.
const PROJECTS: Record<string, { name: string; define: (name: string) => unknown }> = {
  "78652c43-bb7f-401b-a3de-5f532f76f72c": { name: "API", define: api },
  "6f6ac731-10a5-4a28-b831-350ead8a4a97": { name: "realtime-worker", define: worker },
  "cc62432a-5cd0-4c1a-8e3f-ba5d0bf79140": { name: "vite", define: vite },
};

export default defineRailway((ctx) => {
  const entry = PROJECTS[ctx.projectId ?? ""];
  if (!entry || (ctx.projectName && ctx.projectName !== entry.name)) {
    throw new Error(`.railway/railway.ts declares no project ${ctx.projectId} (${ctx.projectName})`);
  }
  return entry.define(entry.name);
});
