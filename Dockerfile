# The CLIP checkpoint pin, alone: the job runner's model layer below is keyed on model + revision only, so a prompt
# edit to data/clip_taxonomy.json neither re-downloads the weights nor invalidates the pip layers.
FROM python:3.12-slim AS clip-ref
COPY data/clip_taxonomy.json scripts/bake_clip_weights.py /ref/
RUN python /ref/bake_clip_weights.py --pin /ref/clip_taxonomy.json /ref/clip_checkpoint.json

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# EXTRAS: the pyproject extras this image installs. A Railway service variable of the same name reaches this ARG at
# build time (.railway/railway.ts: the API and the worker set `api`, the job runner `api,clip,geo`); unset, it is
# `api`. The echo shows in the build log.
ARG EXTRAS

# The job runner's model layer (EXTRAS names clip; a no-op for the API and the worker), before the source so a
# code-only build reuses it: CPU torch from PyTorch's CPU index (PyPI's wheel pulls CUDA), then the pinned CLIP
# checkpoint. These two pins are the clip extra's runtime companions (pyproject.toml); the offline load below proves
# them. The Xet chunk cache would only duplicate the weights.
COPY --from=clip-ref /ref/clip_checkpoint.json ./data/
COPY scripts/bake_clip_weights.py ./scripts/
RUN case ",${EXTRAS}," in *,clip,*) \
      pip install --upgrade pip \
      && pip install "torch==2.14.1" --index-url https://download.pytorch.org/whl/cpu \
      && pip install "huggingface_hub>=0.34,<1.0" \
      && HF_HUB_DOWNLOAD_TIMEOUT=60 python scripts/bake_clip_weights.py data/clip_checkpoint.json \
      && rm -rf /root/.cache/huggingface/xet ;; \
    esac

COPY pyproject.toml ./
COPY scraper/ ./scraper/
COPY toolkit/ ./toolkit/
COPY api/ ./api/
COPY scripts/ ./scripts/
# location_data/ backs the W1v operator-correction route (/location/corrections
# lazily imports the claim producer + the resolver's single-listing drain for
# the synchronous 5.5.5 read-your-writes). The resolve path is deliberately
# pyproj-free (location_data/resolver/geo.py), so the [api] extra covers it.
# scraper/db.py also lazy-imports payload_norm here for the W2a-0 churn
# instrument, which the realtime worker's detail drain runs from this image.
COPY location_data/ ./location_data/
# contracts/ is READ AT RUNTIME by location_data/payload_norm.py: a portal's
# `persistence.volatile_paths` decides the projection `payload_sha256` addresses, and
# that address is permanent. Sourcing it from the deployed artefact rather than from the
# DB projection is what stops two runners hashing one body two ways depending on whether
# the contract-load job had run yet. Everything else in a contract stays deploy-time.
COPY contracts/ ./contracts/
# data/ carries runtime-read files (clip_taxonomy.json for the dedup engine's CLIP
# settings, condition rubrics/markers). load_taxonomy() reads data/clip_taxonomy.json
# from the image root, so the realtime-worker dedup lane FileNotFoundError'd without it.
COPY data/ ./data/
# autodedup/ backs the admin-gated /autodedup/* progress routes: api/routes/autodedup.py
# imports autodedup/progress_sql.py at module scope, so api.main cannot boot without this
# tree in the image (setuptools' packages.find matches nothing and fails silently at build
# time, so the miss only shows as a crash-loop at runtime).
COPY autodedup/ ./autodedup/

RUN echo "EXTRAS=${EXTRAS:-unset}" && pip install --upgrade pip && pip install ".[${EXTRAS:-api}]"
# The job runner's proof, offline: CPU torch, and Tagger.load reads only the baked checkpoint (a missing file fails
# the build after Tagger.load's retries, and the old deployment stays live).
RUN case ",${EXTRAS}," in *,clip,*) \
      HF_HUB_OFFLINE=1 python -c 'import torch; assert torch.version.cuda is None, torch.version.cuda; \
        from scraper.clip_tagger import Tagger; t = Tagger.load(); \
        print("CLIP baked", t.model_id, t.revision, "torch", torch.__version__)' \
      && du -sh /root/.cache/huggingface ;; \
    esac

EXPOSE 8000

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
