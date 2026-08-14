"""Typed env loader for Madras.

Doctrine: reads ONLY from the master vault at
O:/Brijesh-OS/secrets/vault.env. Project-namespaced keys
(DRONA_*, FINPILOT_*, DISCOVERY_*) are explicitly NOT loaded —
those belong to those projects.

If a key Madras needs isn't in the master vault yet, the founder
adds it to vault.env (template at vault.env.example).

Implementation note: we use pydantic-settings' built-in env_file mechanism
rather than load_dotenv() to avoid polluting os.environ as a side effect of
importing this module (which would break other tests that read os.environ
directly with their own fallback defaults).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Vault path is overridable via MADRAS_VAULT_PATH so CI / non-Windows hosts can point
# at a different location (or omit it entirely for safe-default mode).
# The engine reads a shared secrets vault at an absolute path on the founder's machine.
# An extracted repo must not: that path is a personal detail nobody chose to publish, it
# means nothing to anyone who clones this, and -- the part that actually bit us -- on the
# machine where the vault DOES exist, this repo silently loaded production credentials
# and its test suite connected to a real database as a real user.
#
# Here, configuration comes from your own .env and your own environment. Nothing else.
_VAULT_PATH = Path(os.environ.get("MADRAS_VAULT_PATH", ""))
_LOCAL_ENV = Path(__file__).resolve().parents[2] / ".env"

# Build env_file list: vault first (lower priority), local .env second (higher priority).
# pydantic-settings processes env_file entries in order, later files override earlier ones.
_ENV_FILES: list[str] = []
if _VAULT_PATH.exists():
    _ENV_FILES.append(str(_VAULT_PATH))
if _LOCAL_ENV.exists():
    _ENV_FILES.append(str(_LOCAL_ENV))


# Explicit allowlist of master-vault keys Madras may read.
# Adding to this list requires updating WORKSPACE_CONTEXT.md §4.
class Settings(BaseSettings):
    """Madras settings. Only un-namespaced shared keys are loaded.

    `extra="ignore"` is critical: it means project-namespaced keys
    present in the vault (DRONA_*, FINPILOT_*, etc.) are silently
    dropped — they cannot leak into Madras code.

    We read vault.env via env_file (not load_dotenv) so that os.environ
    is never mutated — other modules that call os.getenv() with their own
    defaults remain unaffected.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM providers (shared, un-namespaced)
    openrouter_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    # s46: the single test-vs-launch switch for LLM routing's RoutingPolicy.free_only.
    # True today (zero-cost fleet only, no paid spend during dev/test) -- flip to False
    # (or drive per-customer-tier via eval_/economics/pricing.py) at launch. One flag,
    # not a code change.
    llm_free_only: bool = Field(default=True, validation_alias="MADRAS_LLM_FREE_ONLY")
    # s46: HMAC signing secret for security/approval_doctrine.py's ExecutionGuard
    # (doctrine 5, protect-resume-endpoint). Empty by default (single-user cockpit dev) --
    # ExecutionGuard.authorize() skips signature verification when the secret is unset,
    # same zero-cost-safe-default pattern as llm_free_only above. Set in vault.env before
    # exposing /approve to more than one trusted caller.
    approval_secret: str = Field(default="", validation_alias="MADRAS_APPROVAL_SECRET")
    # s46: per-channel HMAC secrets for messaging/inbound_verify.py's verify_inbound()
    # (row 81). Empty by default -- POST /messages/inbound/{channel} skips verification
    # for a channel with no configured secret (dev-friendly), same pattern as above.
    # Set the matching secret in vault.env to turn on signature checking for that channel.
    inbound_secret_slack: str = Field(default="", validation_alias="MADRAS_INBOUND_SECRET_SLACK")
    inbound_secret_github: str = Field(default="", validation_alias="MADRAS_INBOUND_SECRET_GITHUB")
    inbound_secret_generic: str = Field(
        default="", validation_alias="MADRAS_INBOUND_SECRET_GENERIC"
    )
    # row social-presence — /a2a/message accepted any unsigned POST body and launched a
    # governed task from it. Same dev-friendly pattern: empty = unaffected (today's
    # behavior), set to turn on HMAC verification for inbound A2A calls.
    inbound_secret_a2a: str = Field(default="", validation_alias="MADRAS_INBOUND_SECRET_A2A")
    # s46: Git-PR-CI (codeact/github_ci.py) -- outward-facing (pushes real branches, opens
    # real PRs via `gh`). Empty by default = the tool refuses to run at all. Set to a single
    # "owner/name" to opt in; the tool is hard-scoped to ONLY this repo (never caller-chosen)
    # and still requires the normal per-call approval gate on top (toolset "git_ci" is not
    # auto-allowed).
    git_pr_ci_repo: str = Field(default="", validation_alias="MADRAS_GIT_PR_CI_REPO")
    # s46: Governed Computer Use (tools/computer_use.py) -- native OS automation via the
    # SANDBOXED trycua/cua backend (MIT), never the raw host. Off by default; opt in
    # explicitly since this is a genuinely new, high-trust attack surface.
    computer_use_enabled: bool = Field(
        default=False, validation_alias="MADRAS_COMPUTER_USE_ENABLED"
    )
    # T2.1 (s44): Madras's OWN dedicated litellm (madras-litellm, port 4001) — was the
    # shared outkast-litellm:4000. Reads MADRAS_LITELLM_MASTER_KEY from vault.env, not
    # the shared LITELLM_MASTER_KEY (which other projects still use against port 4000).
    litellm_master_key: str = Field(default="", validation_alias="MADRAS_LITELLM_MASTER_KEY")
    litellm_base_url: str = Field(
        default="http://localhost:4001", validation_alias="MADRAS_LITELLM_BASE_URL"
    )
    e2b_api_key: str = Field(
        default="",
        description=(
            "E2B (Firecracker micro-VM sandbox) API key — optional, not required by default. "
            "D47: the self-hosted DockerSandbox is the production sandbox choice; this key only "
            "matters if sandbox_backend='e2b' is explicitly selected."
        ),
    )
    # Phase I (s56, tamil-and-backend-spatial D77): E2E Networks MyAccount API — the two-part
    # credential their API actually requires (api_key as a query param, auth_token as the
    # Bearer header — NOT a single bearer token despite the docs' own example). Used by
    # scripts/provision_e2e_backend.py to (re)create/verify madras-backend-01, never by the
    # app runtime itself.
    e2e_networks_api_key: str = Field(default="")
    e2e_networks_auth_token: str = Field(default="")
    mercur_admin_url: str = Field(
        default="http://localhost:9000", description="Mercur/Medusa marketplace backend base URL"
    )
    mercur_admin_email: str = Field(
        default="", description="Mercur engine-automation admin account email (§ B9)"
    )
    mercur_admin_password: str = Field(
        default="", description="Mercur engine-automation admin account password (§ B9)"
    )
    madras_byok_encryption_key: str = Field(
        default="", description="Fernet key for BYOK encryption-at-rest (§ B11); empty = BYOK off"
    )
    # Per-LLM-call HTTP timeout. Local OSS models (e.g. llama-70b on modest
    # hardware) are slow on heavy tool-schema requests; 90s timed out mid-turn.
    litellm_timeout: float = Field(default=300.0)

    # Hugging Face (gated datasets, e.g. GAIA). Vault key: HUGGINGFACE_TOKEN.
    huggingface_token: str = Field(default="")

    # Observability — T2.1 (s44): Madras's OWN dedicated langfuse (madras-langfuse, port
    # 3004, project "madras"), was the shared outkast-langfuse:3003 (project
    # "outkast-cockpit"). Reads the MADRAS_LANGFUSE_* vault.env vars, not the legacy
    # LANGFUSE_*_LOCAL ones (kept only until the old container is confirmed retired).
    langfuse_public_key_local: str = Field(
        default="", validation_alias="MADRAS_LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key_local: str = Field(
        default="", validation_alias="MADRAS_LANGFUSE_SECRET_KEY"
    )
    langfuse_host: str = Field(
        default="http://localhost:3004", validation_alias="MADRAS_LANGFUSE_HOST"
    )

    # Infra (shared outkast-* stack)
    postgres_url: str = Field(
        default="postgresql://madras:madras@localhost:5432/madras",
        alias="madras_postgres_url",
        description="Shared outkast-postgres, madras db/user (vault key: MADRAS_POSTGRES_URL)",
    )
    postgres_admin_url: str = Field(
        default="",
        alias="madras_postgres_admin_url",
        description="The OWNER connection -- migrations and provisioning only (vault key: "
        "MADRAS_POSTGRES_ADMIN_URL). Everything else uses `postgres_url`, which connects as the "
        "DDL-less `madras_app` role so RLS policies actually apply to it (D83: a superuser or "
        "table owner bypasses every policy, so the application must be neither). Falls back to "
        "`postgres_url` when unset, which keeps a database that was never split working.",
    )
    redis_working_url: str = Field(
        default="redis://localhost:6380/9",
        validation_alias="MADRAS_REDIS_URL",
        description="Madras's own dedicated madras-redis (host port 6380, s35/s44 isolation), "
        "db 9 = working memory. Vault's plain REDIS_URL is the shared outkast-redis instance.",
    )
    redis_reflex_url: str = Field(
        default="redis://localhost:6380/10",
        description="Madras's own dedicated madras-redis (host port 6380), db 10 = reflex memory",
    )
    qdrant_url: str = Field(
        default="http://127.0.0.1:6335",
        validation_alias="MADRAS_QDRANT_URL",
        description="Madras's own dedicated madras-qdrant (host port 6335, s35/s44 isolation) "
        "— vault's plain QDRANT_URL is stale (still 6333, the old shared instance). "
        "127.0.0.1, never `localhost`: Windows resolves `localhost` to ::1 first, and a "
        "loopback-bound server listening on IPv4 refuses that connection (the s60 IPv6 trap). "
        "This default was the one the s60 sweep missed, because unlike postgres/redis it has no "
        "vault override to mask it.",
    )
    qdrant_api_key: str = Field(
        default="",
        validation_alias="MADRAS_QDRANT_API_KEY",
        description="Sent as the `api-key` header on every Qdrant request. Empty by default and "
        "then NO header is sent at all — an empty api-key is rejected outright by some servers, "
        "which would break every unauthenticated local Qdrant. Added s66: base-01's Qdrant refuses "
        "unauthenticated requests, and nothing in this codebase could send a key, so semantic "
        "recall silently degraded to keyword-only there. Guarded by "
        "tests/test_memory/test_qdrant_api_key.py.",
    )
    llm_backend: str = Field(
        default="ollama",
        validation_alias="MADRAS_LLM_BACKEND",
        description="Which LLM backend to use by default: 'ollama' (local, nothing leaves the "
        "machine) or 'openrouter'. Added s67 because `.env.example` documented this variable and "
        "nothing read it -- a person could set it to openrouter, restart, still be on ollama, and "
        "get no error explaining why. Found by a test that checks every variable the example file "
        "documents is one the code actually looks for.",
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        validation_alias="MADRAS_OLLAMA_URL",
        description="Namespaced deliberately (s66). The shared vault defines a bare OLLAMA_URL for "
        "another project, so without this alias any shell that sources the vault silently "
        "repoints Madras's embedder at a different server — nothing errors, recall just stops "
        "working. Same rule as postgres/redis/qdrant. Guarded by "
        "tests/test_config/test_madras_namespaced_aliases.py.",
    )
    # searxng removed (§H / D45): AGPL-3.0. web_search/deep_search now use ddgs (MIT).
    crawl4ai_url: str = Field(default="http://localhost:11235")
    n8n_url: str = Field(default="http://localhost:5678", description="outkast-n8n (webhooks)")
    perplexica_url: str = Field(default="http://localhost:3033", description="perplexica search")

    # MLflow — Track 2b experiment tracking (T2.2). Vault key: MADRAS_MLFLOW_TRACKING_URI.
    mlflow_tracking_uri: str = Field(
        default="http://localhost:5000",
        validation_alias="MADRAS_MLFLOW_TRACKING_URI",
        description="MLflow server URL for experiment tracking (T2.2)",
    )

    # Prometheus — Track 2b metrics (T2.4). Vault key: MADRAS_PROMETHEUS_URL.
    prometheus_url: str = Field(
        default="http://localhost:9090",
        validation_alias="MADRAS_PROMETHEUS_URL",
        description="Prometheus metrics server URL (T2.4)",
    )

    # Grafana — Track 2b dashboards (T2.5). Vault key: MADRAS_GRAFANA_URL.
    grafana_url: str = Field(
        default="http://localhost:3001",
        validation_alias="MADRAS_GRAFANA_URL",
        description="Grafana dashboard UI URL (T2.5)",
    )
    # DuckDB — Track 2b analytics (T2.7). Vault key: MADRAS_DUCKDB_DSN.
    duckdb_dsn: str = Field(
        default="postgresql://madras:madras@127.0.0.1:5433/madras",
        validation_alias="MADRAS_DUCKDB_DSN",
        description="Postgres DSN for DuckDB analytics queries (T2.7)",
    )
    # minio_endpoint removed (§H / D45): MinIO is AGPL-3.0, and it was a DEAD config field
    # (never used in src/madras — no minio/boto3 dep). When object storage is actually
    # needed, use a permissive S3-compatible backend (SeaweedFS/Cloudflare R2/AWS S3), not MinIO.
    embed_model: str = Field(
        default="nomic-embed-text",
        validation_alias="MADRAS_EMBED_MODEL",
        description="Ollama embedding model. Namespaced (s66) for the same reason as ollama_url: "
        "a bare EMBED_MODEL belongs to whichever project set it last.",
    )

    # Messaging channels (E-E18) — Apprise URL per channel, loaded from the vault by name.
    # 20+ first-class channels; the full Apprise 80+ remain reachable via a per-message
    # apprise_url override. Empty default => that channel stays an unconfigured draft.
    apprise_email_url: str = Field(default="")
    apprise_slack_url: str = Field(default="")
    apprise_telegram_url: str = Field(default="")
    apprise_discord_url: str = Field(default="")
    apprise_sms_url: str = Field(default="")
    apprise_ntfy_url: str = Field(default="")
    apprise_push_url: str = Field(default="")
    apprise_whatsapp_url: str = Field(default="")
    apprise_msteams_url: str = Field(default="")
    apprise_matrix_url: str = Field(default="")
    apprise_signal_url: str = Field(default="")
    apprise_mattermost_url: str = Field(default="")
    apprise_rocketchat_url: str = Field(default="")
    apprise_gotify_url: str = Field(default="")
    apprise_pushover_url: str = Field(default="")
    apprise_pushbullet_url: str = Field(default="")
    apprise_webhook_url: str = Field(default="")
    apprise_twitter_url: str = Field(default="")
    apprise_reddit_url: str = Field(default="")
    apprise_mastodon_url: str = Field(default="")
    apprise_line_url: str = Field(default="")
    apprise_zulip_url: str = Field(default="")

    madras_workspace: str = Field(
        default="",
        description="Root dir file_read is confined to (path-security boundary). "
        "Empty → resolved to <repo>/workspace at tool load.",
    )

    worker_model: str = Field(
        default="llama-70b",
        description="Default fast model for subagent workers",
    )
    vision_model: str = Field(
        default="moondream:latest",
        description="Vision model for vision_analyze, served via the Ollama-direct vision "
        "gateway (LiteLLM-proxied VL aliases like qwen2.5-vl 400 until registered)",
    )
    comfyui_url: str = Field(
        default="http://127.0.0.1:8188",
        description="Self-hosted ComfyUI server for image_generate (D47: no hosted "
        "third-party image-gen API by default).",
    )
    comfyui_checkpoint: str = Field(
        default="sd_xl_base_1.0.safetensors",
        description="ComfyUI checkpoint for image_generate — SDXL base, verified working "
        "end-to-end on the local install (s43). The lower-VRAM FLUX-2-Klein-4B "
        "GGUF checkpoint is present but not yet wired: its GGUF text-encoder "
        "output shape doesn't match what ComfyUI's stock 'flux' CLIP-loader type "
        "expects on this ComfyUI-GGUF version — needs a newer node/loader before "
        "it's usable, logged as a follow-up, not blocking.",
    )
    tts_voice: str = Field(default="af_heart", description="Default Kokoro TTS voice")
    tts_model_path: str = Field(
        default="kokoro-v1.0.onnx", description="Path to the Kokoro ONNX model file"
    )
    tts_voices_path: str = Field(
        default="voices-v1.0.bin", description="Path to the Kokoro voices bin file"
    )

    # Sandbox settings
    sandbox_backend: str = Field(
        default="local",
        description="local | docker | e2b — isolation backend (e2b = Firecracker microVM at scale)",
    )
    sandbox_image: str = Field(
        default="python:3.11-slim",
        description="Docker image for the docker sandbox",
    )
    sandbox_memory: str = Field(default="512m")
    sandbox_cpus: str = Field(default="1.0")
    sandbox_timeout: float = Field(default=30.0)
    # cgroup pids.max counts THREADS, not processes -- a thread-pooled job blows through a
    # process-shaped number. 1024 stops a fork bomb dead while leaving real work room; CIS's
    # example of 100 is an illustration its own text warns not to copy blindly (s63).
    sandbox_pids_limit: str = Field(default="1024")

    @property
    def admin_url(self) -> str:
        r"""The connection migrations and provisioning use -- the table OWNER.

        Everything else uses `postgres_url`, which since D83 connects as the DDL-less
        `madras_app` role. Splitting them is the whole point: RLS policies do not apply to a
        superuser or a table owner, so an application that connects as either has policies that
        are listed by `\d`, pass review, and protect nothing.

        Falls back to `postgres_url` when `MADRAS_POSTGRES_ADMIN_URL` is unset, so a database that
        was never split keeps working rather than failing at migration time.
        """
        return self.postgres_admin_url or self.postgres_url

    @model_validator(mode="after")
    def _normalize_urls(self) -> Settings:
        # When running INSIDE the outkast Docker network (MADRAS_IN_DOCKER=1), rewrite
        # localhost endpoints to container DNS names + internal ports — no host port-forward,
        # which eliminates the Docker Desktop port-forward flakiness for long-running work.
        if os.environ.get("MADRAS_IN_DOCKER"):
            self._rewrite_for_docker()
        # Normalize postgres:// -> postgresql:// (asyncpg requires the latter)
        if self.postgres_url.startswith("postgres://"):
            object.__setattr__(
                self,
                "postgres_url",
                "postgresql://" + self.postgres_url[len("postgres://") :],
            )
        # Force sslmode=disable — these are private, local-only Postgres containers with no SSL
        # configured. asyncpg's default sslmode=prefer attempts an SSL handshake regardless, and
        # on Windows' ProactorEventLoop a failed handshake surfaces as an opaque
        # `ConnectionError: unexpected connection_lost() call` instead of falling back to
        # plaintext cleanly — breaks every DB-touching test on Windows dev machines.
        if "sslmode=" not in self.postgres_url:
            sep = "&" if "?" in self.postgres_url else "?"
            object.__setattr__(self, "postgres_url", f"{self.postgres_url}{sep}sslmode=disable")
        # Append /9 if redis_working_url has no db index (vault stores bare redis://host:port[/]).
        parsed = urlparse(self.redis_working_url)
        if not parsed.path or parsed.path == "/":
            normalized = self.redis_working_url.rstrip("/") + "/9"
            object.__setattr__(self, "redis_working_url", normalized)
        return self

    def _rewrite_for_docker(self) -> None:
        """localhost:<host_port> -> <container_host>:<container_port> for in-network access."""
        # host_port -> (container_dns, container_port)
        port_map: dict[str, tuple[str, str]] = {
            "5432": ("outkast-postgres", "5432"),
            "6379": ("outkast-redis", "6379"),
            "6333": ("outkast-qdrant", "6333"),
            "11434": ("outkast-ollama", "11434"),
            "4000": ("outkast-litellm", "4000"),
            "11235": ("infra-crawl4ai-1", "11235"),
            "3033": ("infra-perplexica-1", "3000"),  # internal port differs
            "5678": ("outkast-n8n", "5678"),
            "5000": ("madras-mlflow", "5000"),
            "9090": ("madras-prometheus", "9090"),
            "3001": ("madras-grafana", "3000"),
        }
        url_fields = [
            "postgres_url",
            "redis_working_url",
            "redis_reflex_url",
            "qdrant_url",
            "ollama_url",
            "litellm_base_url",
            "crawl4ai_url",
            "perplexica_url",
            "n8n_url",
            "mlflow_tracking_uri",
            "prometheus_url",
            "grafana_url",
            "duckdb_dsn",
        ]
        for field in url_fields:
            val = getattr(self, field, "")
            if not isinstance(val, str) or not val:
                continue
            for host in ("localhost", "127.0.0.1"):
                if host in val:
                    for host_port, (dns, cport) in port_map.items():
                        token = f"{host}:{host_port}"
                        if token in val:
                            val = val.replace(token, f"{dns}:{cport}")
                    # bare host with no port (rare) — leave as-is
            object.__setattr__(self, field, val)


settings = Settings()
