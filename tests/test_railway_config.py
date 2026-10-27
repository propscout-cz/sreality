"""Railway settings live only in .railway/railway.ts (#2060)."""

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scraper import image_storage, job_process, job_runner, jobs
from tests._railway_iac import iac_constant, iac_entry, iac_text

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "railway_iac_wizard.sh"
PINS = ROOT / "data" / "requirements-clip-runner.txt"


def test_no_config_as_code_file_is_left_to_override_the_iac_file() -> None:
    """Until 2026-12-01 Railway still reads a railway.json/railway.toml on deploy, over the applied settings."""
    left = sorted(str(p.relative_to(ROOT)) for d in (ROOT, ROOT / "frontend")
                  for p in d.glob("railway*") if p.suffix in {".json", ".toml"})
    assert left == []


def test_each_railway_project_id_plans_only_its_own_definition() -> None:
    """All three services are named `sreality`, so a swapped entry would plan one project's settings onto
    another with no destructive line: the map is pinned by id, name and definition."""
    entries = re.findall(r'"([0-9a-f-]{36})": \{ name: "([^"]+)", define: (\w+) \}', iac_text())
    assert entries == [
        ("78652c43-bb7f-401b-a3de-5f532f76f72c", "API", "api"),
        ("6f6ac731-10a5-4a28-b831-350ead8a4a97", "realtime-worker", "worker"),
        ("cc62432a-5cd0-4c1a-8e3f-ba5d0bf79140", "vite", "vite"),
    ]


def test_the_wizard_plans_every_project_the_iac_file_declares() -> None:
    """The wizard keeps its own project tables: a project missing there is never planned or drift-checked, and its
    drift mode still exits 0."""
    wizard = WIZARD.read_text(encoding="utf-8")

    def table(name: str) -> dict[str, str]:
        match = re.search(rf"^declare -A {name}=\((.*?)\)$", wizard, re.M | re.S)
        assert match, name
        return dict(re.findall(r"\[(\w+)\]=(\S+)", match.group(1)))

    labels_line = re.search(r"^LABELS=\((.*?)\)$", wizard, re.M)
    assert labels_line
    labels = labels_line.group(1).split()
    ids, names = table("PROJECT_ID"), table("PROJECT_NAME")
    assert sorted(ids) == sorted(names) == sorted(labels)
    declared = re.findall(r'"([0-9a-f-]{36})": \{ name: "([^"]+)"', iac_text())
    assert sorted((ids[label], names[label]) for label in labels) == sorted(declared)


def _declared_services(define: str) -> list[str]:
    """The services one project's definition creates: its own `service("…")` calls and those of the top-level
    service consts it calls (`jobRunner(sreality)`)."""
    text = iac_entry(define)
    found = re.findall(r'\bservice\("([^"]+)"', text)
    for const in re.findall(r"^const (\w+) = ", iac_text(), re.M):
        if const != define and re.search(rf"\b{const}\(", text):
            found += re.findall(r'\bservice\("([^"]+)"', iac_entry(const))
    return found


def test_the_window_waits_for_a_status_from_every_service_the_iac_file_declares() -> None:
    """Railway posts one commit status per service (`<project> - <service>`): a window that waited for one per
    project would read the merge as final with job-runner's (or #2075's agent-worker's) still unposted."""
    wizard = WIZARD.read_text(encoding="utf-8")
    table = re.search(r"^declare -A SERVICES=\((.*?)\)$", wizard, re.M | re.S)
    assert table, "SERVICES"
    services = {label: names.split() for label, names in re.findall(r'\[(\w+)\]="([^"]*)"', table.group(1))}
    names = dict(re.findall(r"\[(\w+)\]=(\S+)", re.search(r"^declare -A PROJECT_NAME=\((.*?)\)$", wizard,
                                                         re.M | re.S).group(1)))
    defines = dict(re.findall(r'\{ name: "([^"]+)", define: (\w+) \}', iac_text()))
    assert {label: _declared_services(defines[names[label]]) for label in names} == services
    assert services["worker"] == ["sreality", "job-runner"]
    window = wizard.split("\nmode_window() {\n", 1)[1].split("\n}\n", 1)[0]
    calls = re.findall(r"railway_statuses (.*?)(?: >|$)", window, re.M)
    assert calls == ['"$OUT/merge-status.json" "${contexts[@]}"'], calls
    assert 'contexts+=("${PROJECT_NAME[$l]} - $s")' in window and "${SERVICES[$l]}" in window


def _wizard_node_script(function: str) -> str:
    """The `node -e '…'` program inside one wizard function."""
    body = WIZARD.read_text(encoding="utf-8").split(f"\n{function}() {{\n", 1)[1].split("\n}\n", 1)[0]
    return body.split("node -e '", 1)[1].rsplit("' ", 1)[0]


@pytest.mark.parametrize(("posted", "code"), [
    ({"API - sreality": "success", "vite - sreality": "success", "realtime-worker - sreality": "success"}, 3),
    ({"API - sreality": "success", "vite - sreality": "success", "realtime-worker - sreality": "success",
      "realtime-worker - job-runner": "pending"}, 3),
    ({"API - sreality": "success", "vite - sreality": "success", "realtime-worker - sreality": "success",
      "realtime-worker - job-runner": "failure"}, 1),
    ({"API - sreality": "success", "vite - sreality": "success", "realtime-worker - sreality": "success",
      "realtime-worker - job-runner": "success", "CI": "failure"}, 0),
])
def test_the_merge_statuses_are_final_only_once_every_service_posted(tmp_path: Path, posted: dict[str, str],
                                                                       code: int) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"statuses": [{"context": c, "state": v} for c, v in posted.items()]}))
    contexts = ["vite - sreality", "API - sreality", "realtime-worker - sreality", "realtime-worker - job-runner"]
    run = subprocess.run([node, "-e", _wizard_node_script("railway_statuses"), str(status), *contexts],
                         capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == code, run.stdout + run.stderr


def test_a_window_that_deletes_its_service_never_waits_for_its_deployment() -> None:
    """U-1 for a service add runs `window <label> 0 <n> <service>`: once the apply deleted the service, no deployment of
    it reaches SUCCESS, so every read of one sits in the branch a removal skips, decided before the apply (which
    clears the pinned plan)."""
    window = WIZARD.read_text(encoding="utf-8").split("\nmode_window() {\n", 1)[1].split("\n}\n", 1)[0]
    assert window.index('if plan_removes "${PLAN_FILE[$label]}"; then') < window.index('apply_pinned "$label"')
    stack: list[list[str]] = []
    reads = 0
    for line in window.splitlines():
        if re.match(r"\s*if .*; then$", line):
            stack.append([line.strip(), "then"])
        elif re.match(r"\s*else$", line) and stack:
            stack[-1][1] = "else"
        elif re.match(r"\s*fi$", line) and stack:
            stack.pop()
        elif re.search(r"\bwait_success |Resource Limits|list-deployments on|merge build \(", line):
            reads += 1
            assert ["if (( removes )); then", "else"] in stack, line
    assert reads == 5


@pytest.mark.parametrize(("fixture", "code"), [("delete_job_runner.json", 0), ("create_job_runner.json", 1),
                                               ("no_changes.json", 1)])
def test_a_removal_is_read_off_the_pinned_plan(fixture: str, code: int) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    plan = ROOT / "tests" / "fixtures" / "railway_plans" / fixture
    run = subprocess.run([node, "-e", _wizard_node_script("plan_removes"), str(plan)], capture_output=True,
                         text=True, timeout=60, check=False)
    assert run.returncode == code, run.stdout + run.stderr


@pytest.mark.parametrize(("description", "code"), [
    ("No deployment needed - watched paths not modified", 1), ("Deployment succeeded", 0), (None, 0)])
def test_a_merge_that_redeployed_vite_gets_the_spa_check(tmp_path: Path, description: str | None, code: int) -> None:
    """vite watches /frontend/**, so a regenerated frontend/public/workflow-docs.json redeploys it on a worker window's
    merge: the window reads that off vite's status and runs the SPA's read-only check (a missing status counts)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    status = tmp_path / "status.json"
    statuses = [] if description is None else [{"context": "vite - sreality", "state": "success",
                                                "description": description}]
    status.write_text(json.dumps({"statuses": statuses}))
    run = subprocess.run([node, "-e", _wizard_node_script("railway_redeployed"), str(status), "vite - sreality"],
                         capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == code, run.stdout + run.stderr
    window = WIZARD.read_text(encoding="utf-8").split("\nmode_window() {\n", 1)[1].split("\n}\n", 1)[0]
    after = window.split('stop "a Railway status on ${merge_sha:0:9} is not success (above)"', 1)[1]
    loop = after.split("\n  done\n", 1)[0]
    assert "for l in API vite; do" in loop and 'railway_redeployed "$OUT/merge-status.json"' in loop
    assert 'check_health "$l"' in loop and "check_spa" in loop


def test_only_the_worker_and_the_runner_declare_a_start_command() -> None:
    """Ruling R5: the API and vite run their image's own command. Railway runs a start command in exec form, so a
    copied `uvicorn ... --port $PORT` never expands $PORT and fails every health check (8a362a0ea); the runner's is
    bare python for the same reason, and so that python is PID 1 and gets the SIGTERM. #2075 scopes this to the
    `sreality` service when it adds agent-worker to `api`."""
    for name in ("api", "vite"):
        assert not re.search(r"\bstart(Command)?\s*:", iac_entry(name)), name
    runner, worker = iac_entry("jobRunner"), iac_entry("worker")
    assert re.findall(r"\bstart(?:Command)?\s*:\s*(\w+)", runner) == ["RUNNER_START"]
    assert re.findall(r"\bstart(?:Command)?\s*:\s*(\w+)", worker) == ["WORKER_START"]
    command = iac_constant("RUNNER_START")
    assert command == "python -m scraper.job_runner run"
    assert "$" not in command and not set(command.split()) & {"sh", "bash", "-c"}, "no shell before python"
    assert iac_constant("RUNNER_REPLICAS") == 2
    assert "replicas: { [REGION]: RUNNER_REPLICAS }" in runner
    assert 'restartPolicyType: "ALWAYS"' in runner
    assert "jobRunner(sreality)" in worker, "the runner is a resource of the worker's project"


def test_the_runner_drain_outlasts_its_own_fallback() -> None:
    """The runner checks its own drain only between ticks, then forgets its heartbeat: Railway's SIGKILL comes after
    a guard poll, that drain, a tick, one stopping tick and the forget, and not much later (a deploy waits on it)."""
    bound = (job_process.GUARD_POLL_S + job_runner.DRAINING_S + job_runner.TICK_S + job_runner.STOP_PHASE_BUDGET_S
             + 2 * job_runner.CALL_MAX_S)
    draining = iac_constant("RUNNER_DRAINING_SECONDS")
    assert bound <= draining <= bound + 30, f"drainingSeconds {draining}: the bound is {bound:.0f} s, +30 at most"
    assert "drainingSeconds: RUNNER_DRAINING_SECONDS" in iac_entry("jobRunner")


def test_the_runner_replica_is_the_registrys_replica() -> None:
    """The co-tenancy census sizes jobs against jobs.REPLICA_MEMORY_MB, which must be the limit Railway sets: it
    counts decimal bytes, so 8 GB is 8e9 B, 7,629 MiB in the cgroup. No job asks for more CPUs than a replica has."""
    memory, cpus = iac_constant("RUNNER_MEMORY_BYTES"), iac_constant("RUNNER_CPUS")
    assert memory // 2**20 == jobs.REPLICA_MEMORY_MB, f"{memory} B is {memory // 2**20} MiB"
    assert [j.name for j in jobs.declared() if j.cpus > cpus] == []
    runner = iac_entry("jobRunner")
    assert "cpu: RUNNER_CPUS" in runner and "memoryBytes: RUNNER_MEMORY_BYTES" in runner


def test_the_runner_watches_every_tree_the_dockerfile_copies() -> None:
    """A watch-skipped deploy keeps the old image, so a runner that misses a tree its image copies runs stale code.
    `COPY --from=<stage>` copies from a build stage, not the repo; .railway/ stays out (applies are by hand)."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied: set[str] = set()
    for line in re.findall(r"^COPY (?:\S+ )*\S+$", dockerfile, re.M):
        if "--from=" in line:
            continue
        for source in [w for w in line.split()[1:-1] if not w.startswith("--")]:
            first = Path(source).parts[0]
            copied.add(f"/{first}/**" if (ROOT / first).is_dir() else f"/{first}")
    watch = iac_constant("BACKEND_WATCH")
    assert len(watch) == len(set(watch))
    assert sorted(copied - set(watch)) == [], "the runner would not redeploy on these"
    assert set(watch) - copied == {"/Dockerfile", "/.dockerignore"}
    assert "watchPatterns: BACKEND_WATCH" in iac_entry("jobRunner")


def test_the_runner_reads_the_workers_secrets_by_reference() -> None:
    """#2025-R3: the runner stores no secret of its own; it references the worker's SUPABASE_DB_URL and its R2 set
    (#2032: clip downloads photos), so a rotation changes one place (and redeploys the runner after the worker)."""
    runner = iac_entry("jobRunner")
    env = re.search(r"\benv: \{ (.*?) \},", runner, re.S)
    assert env, "the runner declares its variables inline"
    declared = re.findall(r"(\w+): ", env.group(1))
    assert len(declared) == len(set(declared))
    plain = {"EXTRAS", "HF_HUB_OFFLINE"}
    assert set(declared) == plain | {"SUPABASE_DB_URL", *image_storage.R2_ENV_VARS}
    worker_kept = re.search(r"\bkept\((.*?)\)", iac_entry("worker"), re.S)
    assert worker_kept, "the worker keeps its secrets"
    kept_names = set(re.findall(r'"(\w+)"', worker_kept.group(1)))
    for name in sorted(set(declared) - plain):
        assert re.search(rf"\b{name}: sreality\.env\.{name}\b", env.group(1)), f"{name} is not the worker's"
        assert name in kept_names, f"the worker keeps no {name}"
    assert not re.search(r"preserve\(|kept\(|ctx\.shared", runner)


def test_the_api_and_the_worker_pass_their_extras_to_the_root_dockerfile_build() -> None:
    """Railway has no build-args field: a service variable reaches the Dockerfile ARG of the same name. Unset,
    the image installs `api` as before, and the build log's echo tells a delivered value from the fallback."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG EXTRAS\n" in dockerfile
    assert 'echo "EXTRAS=${EXTRAS:-unset}"' in dockerfile
    assert 'pip install ".[${EXTRAS:-api}]"' in dockerfile
    for name in ("api", "worker"):
        assert 'EXTRAS: "api"' in iac_entry(name), name
    assert 'EXTRAS: "api,clip,geo"' in iac_entry("jobRunner")
    assert 'EXTRAS: "api"' not in iac_entry("jobRunner")


def test_the_runner_image_is_cpu_torch_baked_and_offline() -> None:
    """EXTRAS naming clip adds CPU torch (PyPI's wheel pulls CUDA) and the pinned CLIP checkpoint in a layer keyed on
    the pin alone (a prompt edit reuses it), before the source so a code-only build reuses it too; the build then
    loads the model with the hub offline, so a missing file fails the build, never a runtime download."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(r"^FROM .*$", dockerfile, re.M) == ["FROM python:3.12-slim AS clip-ref", "FROM python:3.12-slim"]
    final = dockerfile.rsplit("\nFROM ", 1)[1]
    model = final.index("scripts/bake_clip_weights.py data/clip_checkpoint.json")
    assert final.index("ARG EXTRAS\n") < model < final.index("COPY pyproject.toml ./")
    early = re.findall(r"^COPY .*$", final[:final.index("COPY pyproject.toml ./")], re.M)
    assert [c for c in early if "data/" in c] == ["COPY --from=clip-ref /ref/clip_checkpoint.json ./data/",
                                                  f"COPY {PINS.relative_to(ROOT)} ./data/"], early
    assert final.count('case ",${EXTRAS}," in *,clip,*)') == 3
    assert "--index-url https://download.pytorch.org/whl/cpu" in final and '"torch==' in final
    assert "HF_HUB_OFFLINE=1 python" in final and "assert torch.version.cuda is None" in final
    assert not (ROOT / "data" / "clip_checkpoint.json").exists(), "the repo's data/ would overwrite the staged pin"
    assert "MALLOC_ARENA_MAX" not in dockerfile
    assert "COPY scripts/ ./scripts/" in final and "COPY autodedup/ ./autodedup/" in final


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in PINS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, sep, version = line.partition("==")
            assert sep and re.fullmatch(r"\d+(\.\d+)*", version), f"not an exact pin: {line}"
            pins[name] = version
    return pins


def test_the_runner_vector_stack_is_pinned_and_proven() -> None:
    """Pillow decodes and resizes the pixels CLIP reads, so the re-embed proof (#2032 AC2) holds only while the stack
    under it stays put: one manifest pins it, a layer after the weights installs it (a bump rebuilds neither torch
    nor the checkpoint), and the offline proof asserts the installed versions against the same file."""
    pins = _pins()
    assert set(pins) == {"transformers", "Pillow", "numpy"}, pins
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    final = dockerfile.rsplit("\nFROM ", 1)[1]
    rel = PINS.relative_to(ROOT).as_posix()
    model = final.index("scripts/bake_clip_weights.py data/clip_checkpoint.json")
    copy = final.index(f"COPY {rel} ./data/")
    install = final.index(f"pip install -r {rel}")
    assert model < copy < install < final.index("COPY pyproject.toml ./")
    guard = final.rindex('case ",${EXTRAS}," in *,clip,*)', 0, install)
    assert copy < guard and "esac" not in final[guard:install], "the install runs inside a clip guard"
    for name in pins:
        assert not re.search(rf"{name}\s*==", dockerfile, re.I), f"{name} is pinned in the Dockerfile, not the file"
    proof = final.split("HF_HUB_OFFLINE=1 python -c", 1)[1].split("esac", 1)[0]
    torch_pin = re.search(r'pip install "torch==([\d.]+)"', final)
    assert torch_pin and torch_pin.group(1) == "2.14.1"
    assert rel in proof and f'"{torch_pin.group(1)}"' in proof, "the proof asserts the file's versions and torch"
    # Asserts, not prints: a resolver that swapped Pillow or numpy must fail the build.
    assert "got = {name: version(name) for name in pins}; assert got == pins," in proof
    assert f'assert torch.__version__.split("+")[0] == "{torch_pin.group(1)}",' in proof


def test_the_api_image_is_proven_free_of_clip() -> None:
    """AC3b (#2032): the API and the worker are one build (EXTRAS=api). CI builds it at the base and at the pull
    request, fails if it can import torch or transformers or holds the hub cache, and prints both sizes."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "runner_image.yml").read_text(encoding="utf-8"))
    api = [job for job in workflow["jobs"].values()
           if any("--build-arg EXTRAS=api " in s.get("run", "") for s in job["steps"])]
    assert len(api) == 1, "one job builds the API image"
    runs = "\n".join(s.get("run", "") for s in api[0]["steps"])
    assert "github.event.pull_request.base.sha" in str(api[0]) and "fetch-depth: 0" not in runs
    assert any(s.get("with", {}).get("fetch-depth") == 0 for s in api[0]["steps"])
    assert runs.count("docker build ") == 2
    checks = [s for s in api[0]["steps"] if "find_spec" in s.get("run", "")]
    assert len(checks) == 1, "one step asserts the head image is clip-free"
    check = checks[0]["run"]
    assert all(m in check for m in ('"torch"', '"transformers"', "/root/.cache/huggingface"))
    assert "assert not found, found" in check and "test ! -e /root/.cache/huggingface" in check, "a verdict, not a print"
    assert "||" not in check, "nothing in the verdict step may swallow its exit status"
    assert "docker image inspect -f '{{.Size}}'" in runs and "pip freeze" in runs


def test_the_runner_image_workflow_builds_on_every_image_input() -> None:
    """Decision 17: the torch image is built in CI before Railway builds it. The window requires the check by its
    name when a PR changes an image input, so the wizard's list and the workflow's paths are one list."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "runner_image.yml").read_text(encoding="utf-8"))
    assert workflow["name"] == "CI: runner image"
    triggers = workflow[True]  # PyYAML reads the `on:` key as the boolean True
    assert set(triggers) == {"pull_request"}, "no schedule: nothing for the registry's pairing census"
    paths = triggers["pull_request"]["paths"]
    inputs = re.search(r"^IMAGE_INPUTS=\((.*?)\)$", WIZARD.read_text(encoding="utf-8"), re.M | re.S)
    assert inputs and set(paths) - {".github/workflows/runner_image.yml"} == set(inputs.group(1).split())
    assert {"Dockerfile", ".dockerignore", "pyproject.toml", "scripts/bake_clip_weights.py",
            "data/clip_taxonomy.json"} <= set(paths)
    runs = [s.get("run", "") for job in workflow["jobs"].values() for s in job["steps"]]
    assert any(r.startswith("docker build ") and "--build-arg EXTRAS=api,clip,geo" in r for r in runs), runs


def _scraper_import_closure(modules: list[str]) -> set[str]:
    """The repo files importing these modules runs: each one, its packages' __init__.py, and every scraper module
    they import, at any depth (function-level imports included)."""
    seen: set[str] = set()
    todo = list(modules)
    while todo:
        name = todo.pop()
        path = ROOT / f"{name.replace('.', '/')}.py"
        if name in seen or not path.exists():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "scraper":
                todo += [f"scraper.{a.name}" for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith("scraper."):
                todo.append(node.module)
            elif isinstance(node, ast.Import):
                todo += [a.name for a in node.names if a.name.startswith("scraper.")]
    files = {f"{m.replace('.', '/')}.py" for m in seen}
    return files | {f"{'/'.join(m.split('.')[:i])}/__init__.py" for m in seen for i in range(1, m.count(".") + 1)}


def test_the_runner_image_check_runs_when_its_offline_proof_s_code_changes() -> None:
    """The build's proof RUN imports scraper code (Tagger.load) after the source COPY, so a change there can fail
    Railway's runner build after the merge exactly as a Dockerfile change can: those files are image inputs too."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    proof = dockerfile.split("HF_HUB_OFFLINE=1 python -c", 1)[1].split("esac", 1)[0]
    modules = re.findall(r"\bfrom (scraper\.\w+) import", proof)
    assert modules == ["scraper.clip_tagger"]
    closure = _scraper_import_closure(modules)
    assert {"scraper/__init__.py", "scraper/clip_tagger.py", "scraper/cpus.py"} <= closure
    inputs = re.search(r"^IMAGE_INPUTS=\((.*?)\)$", WIZARD.read_text(encoding="utf-8"), re.M | re.S)
    assert inputs and sorted(closure - set(inputs.group(1).split())) == []


def test_the_wizard_has_the_general_modes_and_checks_with_its_own_copy() -> None:
    """M0's one-shot windows are gone (evidence on #2060). drift compares Railway with main's file, so it refuses
    another checkout. A pull during a window never changes the checker mid-window: the wizard runs its copy."""
    wizard = WIZARD.read_text(encoding="utf-8")
    modes = re.search(r'^case "\$MODE" in\n  ([\w|-]+)\) ;;$', wizard, re.M)
    assert modes and modes.group(1).split("|") == ["preflight", "window", "drift", "runner-list", "runner-pause"]
    drift = wizard.split("\nmode_drift() {\n", 1)[1].split("\n}\n", 1)[0]
    assert 'git -C "$WT" diff --quiet HEAD origin/main -- .railway' in drift
    # Its way back is the main branch: until #2025 merges, main's wizard (#2060's) refuses a detached HEAD (U-4).
    assert 'checkout main, then git -C \\"$WT\\" pull --ff-only' in drift and "--detach" not in drift
    copy_step = wizard.split('if [[ "${RAILWAY_M0_RUNNING_COPY:-}" != 1 ]]; then', 1)[1].split("\nfi\n", 1)[0]
    # A copy per run: a second run (runner-list beside a window) never rewrites a running window's files in place.
    assert 'RUN_DIR=$(mktemp -d "$OUT/run.XXXXXX")' in copy_step
    assert re.findall(r'^\s*cp (.*)$', copy_step, re.M) == ['"${BASH_SOURCE[0]}" "$RUN_DIR/wizard.sh"',
                                                           '"$WT/.railway/plan_check.mjs" "$RUN_DIR/plan_check.mjs"']
    assert 'exec bash "$RUN_DIR/wizard.sh"' in copy_step and 'RAILWAY_M0_RUN_DIR="$RUN_DIR"' in copy_step
    calls = re.findall(r"^.*\bnode\b.*plan_check.*$", wizard, re.M)
    assert calls and all('node "$RUN_DIR/plan_check.mjs"' in c for c in calls), calls
    assert "-running." not in wizard


def test_a_runner_only_window_asks_no_brake() -> None:
    """#2025-R2 (a), ruled by e9 on 2026-10-26: a change that targets only job-runner needs no autodedup brake, so
    its window asks none (#2032's R2 references, W3's and W4's variables). The merge stays on e9's light."""
    wizard = WIZARD.read_text(encoding="utf-8")
    assert re.search(r"^RUNNER=job-runner\b", wizard, re.M)
    window = wizard.split("\nmode_window() {\n", 1)[1].split("\n}\n", 1)[0]
    brake = window.split('  stage "Brake"\n', 1)[1].split("\n  pause\n", 1)[0]
    assert brake.startswith('  if [[ "$label" == worker && "$service" != "$RUNNER" ]]; then\n'), brake
    asked, rest = brake.split('\n  elif [[ "$label" == worker ]]; then\n', 1)
    runner_only, otherwise = rest.split("\n  else\n", 1)
    assert "realtime_autodedup_interval_seconds" in asked and "BRAKE_ASKED=1" in asked
    assert "#2025-R2" in runner_only and "$RUNNER" in runner_only and "BRAKE_ASKED" not in runner_only
    # A vite or API window skips the brake for its own reason, never the runner ruling's.
    assert "the worker's project" in otherwise and "#2025-R2" not in otherwise and "BRAKE_ASKED" not in otherwise
