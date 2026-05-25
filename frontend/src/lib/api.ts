const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

const ACCESS_KEY = "megooci_access_token";
const REFRESH_KEY = "megooci_refresh_token";

class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message || `API error: ${status}`);
    this.name = "ApiError";
  }
}

/**
 * Turn a FastAPI error response into a human-readable string.
 *
 * FastAPI shapes:
 *   - HTTPException → { "detail": "string" }
 *   - Pydantic validation errors → { "detail": [{ "msg": "...", "loc": [...] }, ...] }
 *   - Plain text body (rare) → "string"
 *
 * Falls back to friendly per-status messages so the user never sees "API error: 401".
 */
function extractErrorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((e) =>
          e && typeof e === "object" && "msg" in e
            ? String((e as { msg: unknown }).msg)
            : null,
        )
        .filter((m): m is string => Boolean(m));
      if (msgs.length) return msgs.join("; ");
    }
  }
  if (typeof body === "string" && body.trim()) return body.trim();

  switch (status) {
    case 400: return "The request was invalid. Please check the form and try again.";
    case 401: return "Invalid credentials. Please check your email and password.";
    case 403: return "You don't have permission to do that.";
    case 404: return "We couldn't find what you were looking for.";
    case 409: return "That doesn't match the current state. Refresh and try again.";
    case 422: return "Some fields are invalid. Please review and try again.";
    case 429: return "Too many requests — please wait a moment and try again.";
    case 500: return "The server hit an unexpected error. Please try again.";
    case 502:
    case 503:
    case 504: return "The server is temporarily unavailable. Please try again shortly.";
    default: return `Request failed (${status}). Please try again.`;
  }
}

function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

function setTokensInStorage(access: string, refresh: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

function clearTokens(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

/**
 * Single-flight refresh: concurrent 401s share one /refresh call instead of
 * racing and consuming multiple refresh tokens. Resolves to the new access
 * token on success, or null if refresh fails (user needs to re-authenticate).
 */
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessTokenOnce(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  const refresh = getRefreshToken();
  if (!refresh) return null;

  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        clearTokens();
        return null;
      }
      const tokens = (await res.json()) as {
        access_token: string;
        refresh_token: string;
      };
      setTokensInStorage(tokens.access_token, tokens.refresh_token);
      return tokens.access_token;
    } catch {
      clearTokens();
      return null;
    } finally {
      // Release the lock so the next 401 can trigger a fresh refresh if
      // needed (for long-lived SPA sessions).
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

async function performFetch(
  endpoint: string,
  options: RequestInit,
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    return await fetch(`${BASE_URL}${endpoint}`, { ...options, headers, cache: "no-store" });
  } catch {
    throw new ApiError(
      0,
      null,
      "Couldn't reach the server. Please check your connection and try again.",
    );
  }
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  // Never attempt silent-refresh on the auth endpoints themselves, otherwise
  // a bad login loops against /refresh.
  const isAuthEndpoint = endpoint.startsWith("/api/v1/auth/");

  let res = await performFetch(endpoint, options, getAccessToken());

  // On 401, try exactly one silent refresh + retry. This extends sessions
  // far beyond the access-token TTL without re-login, for as long as the
  // refresh token is valid.
  if (res.status === 401 && !isAuthEndpoint) {
    const newAccess = await refreshAccessTokenOnce();
    if (newAccess) {
      res = await performFetch(endpoint, options, newAccess);
    } else if (typeof window !== "undefined") {
      // Refresh failed: surface the 401 to the caller. The AppLayout
      // redirect-to-login guard will kick in once the auth store hydrates
      // without tokens.
    }
  }

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, body, extractErrorMessage(res.status, body));
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

/**
 * Like fetchApi but also returns the response headers.
 * Used when the backend includes metadata (e.g. X-Updated-Pipelines).
 */
async function fetchApiWithHeaders<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<{ data: T; headers: Headers }> {
  const isAuthEndpoint = endpoint.startsWith("/api/v1/auth/");
  let res = await performFetch(endpoint, options, getAccessToken());

  if (res.status === 401 && !isAuthEndpoint) {
    const newAccess = await refreshAccessTokenOnce();
    if (newAccess) {
      res = await performFetch(endpoint, options, newAccess);
    }
  }

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, body, extractErrorMessage(res.status, body));
  }

  if (res.status === 204) return { data: undefined as T, headers: res.headers };
  const data = await res.json();
  return { data, headers: res.headers };
}

// ------------------------------------------------------------------
// Auth
// ------------------------------------------------------------------
export interface LoginRequest {
  email: string;
  password: string;
}

export interface SignupRequest {
  email: string;
  name: string;
  password: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface User {
  id: string;
  email: string;
  name: string;
  is_admin: boolean;
  is_active: boolean;
  auth_provider: string;
  created_at: string;
  role: string | null;
  permissions: string[];
}

export const authApi = {
  signup: (data: SignupRequest) =>
    fetchApi<AuthTokens>("/api/v1/auth/signup", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  login: (data: LoginRequest) =>
    fetchApi<AuthTokens>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  refresh: (refreshToken: string) =>
    fetchApi<AuthTokens>("/api/v1/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  getMe: () => fetchApi<User>("/api/v1/auth/me"),

  changePassword: (data: { current_password: string; new_password: string }) =>
    fetchApi<{ message: string }>("/api/v1/auth/change-password", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  forgotPassword: (email: string) =>
    fetchApi<{ message: string }>("/api/v1/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, new_password: string) =>
    fetchApi<{ message: string }>("/api/v1/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, new_password }),
    }),

  updateProfile: (data: { name?: string; email?: string }) =>
    fetchApi<User>("/api/v1/auth/update-profile", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------
// Projects
// ------------------------------------------------------------------
export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  parent_id: string | null;
  created_by: string;
  allow_ai_repo_context: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
  parent_id?: string;
}

export const projectsApi = {
  list: (params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<Project[]>(`/api/v1/projects${query ? `?${query}` : ""}`);
  },

  create: (data: CreateProjectRequest) =>
    fetchApi<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) => fetchApi<Project>(`/api/v1/projects/${id}`),

  update: (id: string, data: Partial<CreateProjectRequest>) =>
    fetchApi<Project>(`/api/v1/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string, opts?: { force?: boolean }) =>
    fetchApi<void>(
      `/api/v1/projects/${id}${opts?.force ? "?force=true" : ""}`,
      { method: "DELETE" },
    ),
};

// ------------------------------------------------------------------
// Pipelines
// ------------------------------------------------------------------
export interface Pipeline {
  id: string;
  project_id: string;
  project_repository_id: string | null;
  name: string;
  source_repo_url: string | null;
  default_branch: string;
  definition_path: string;
  definition_format: "yaml";
  yaml_content: string | null;
  enabled: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CreatePipelineRequest {
  project_id: string;
  name: string;
  project_repository_id?: string | null;
  source_repo_url?: string;
  default_branch?: string;
  definition_format?: "yaml";
  yaml_content?: string;
}

export const pipelinesApi = {
  list: (params?: { project_id?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.project_id) qs.set("project_id", params.project_id);
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<Pipeline[]>(
      `/api/v1/pipelines${query ? `?${query}` : ""}`,
    );
  },

  create: (data: CreatePipelineRequest) =>
    fetchApi<Pipeline>("/api/v1/pipelines", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) => fetchApi<Pipeline>(`/api/v1/pipelines/${id}`),

  update: (id: string, data: Partial<CreatePipelineRequest> & { enabled?: boolean }) =>
    fetchApi<Pipeline>(`/api/v1/pipelines/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/pipelines/${id}`, { method: "DELETE" }),
};

// ------------------------------------------------------------------
// Builds
// ------------------------------------------------------------------
export type BuildStatus =
  | "pending"
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "cancelled";

export interface BuildStep {
  id: string;
  stage_id: string;
  name: string;
  step_type: string;
  command: string | null;
  config_json: Record<string, unknown> | null;
  status: string;
  exit_code: number | null;
  sort_order: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface BuildStage {
  id: string;
  build_id: string;
  name: string;
  status: string;
  sort_order: number;
  started_at: string | null;
  finished_at: string | null;
  steps: BuildStep[];
}

export interface Build {
  id: string;
  pipeline_id: string;
  number: number;
  branch: string | null;
  commit_sha: string | null;
  status: BuildStatus;
  started_at: string | null;
  finished_at: string | null;
  triggered_by: string | null;
  trigger_type: string;
  params_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface BuildDetail extends Build {
  stages: BuildStage[];
}

export const buildsApi = {
  list: (params?: {
    pipeline_id?: string;
    skip?: number;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.pipeline_id) qs.set("pipeline_id", params.pipeline_id);
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<Build[]>(`/api/v1/builds${query ? `?${query}` : ""}`);
  },

  trigger: (
    pipelineId: string,
    data?: { branch?: string; commit_sha?: string; params?: Record<string, string> },
  ) =>
    fetchApi<Build>(`/api/v1/builds/${pipelineId}/trigger`, {
      method: "POST",
      body: JSON.stringify(data || {}),
    }),

  get: (id: string) => fetchApi<BuildDetail>(`/api/v1/builds/${id}`),

  logs: (id: string) =>
    fetchApi<Array<{
      step_id: string;
      seq: number;
      timestamp: string | null;
      stream: string;
      content: string;
      stage_name?: string;
      step_name?: string;
    }>>(`/api/v1/builds/${id}/logs`),

  cancel: (id: string) =>
    fetchApi<Build>(`/api/v1/builds/${id}/cancel`, { method: "POST" }),

  retry: (id: string) =>
    fetchApi<Build>(`/api/v1/builds/${id}/retry`, { method: "POST" }),

  dispatch: (id: string) =>
    fetchApi<Build>(`/api/v1/builds/${id}/dispatch`, { method: "POST" }),
};

// ------------------------------------------------------------------
// Gates (Pipeline approval / webhook resolution)
// ------------------------------------------------------------------
export const gatesApi = {
  /** Approve or reject a wait_input step. */
  resolveInput: (stepId: string, approved: boolean) =>
    fetchApi<{ status: string; step_id: string }>(
      `/api/v1/gates/input/${stepId}`,
      { method: "POST", body: JSON.stringify({ approved }) },
    ),
};

// ------------------------------------------------------------------
// Artifacts
// ------------------------------------------------------------------
export interface Artifact {
  id: string;
  build_id: string;
  relative_path: string;
  size_bytes: number;
  checksum_sha256: string;
  retention_until: string | null;
  created_at: string;
}

export interface ArtifactListItem extends Artifact {
  build_number: number;
  pipeline_id: string;
  pipeline_name: string;
  project_id: string;
  project_name: string;
}

export const artifactsApi = {
  listAll: (params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<ArtifactListItem[]>(`/api/v1/artifacts${query ? `?${query}` : ""}`);
  },

  list: (buildId: string) =>
    fetchApi<Artifact[]>(`/api/v1/builds/${buildId}/artifacts`),

  getSignedUrl: (artifactId: string, ttl?: number) => {
    const qs = ttl ? `?ttl=${ttl}` : "";
    return fetchApi<{ url: string; expires_in: number }>(
      `/api/v1/artifacts/${artifactId}/signed-url${qs}`,
    );
  },

  download: async (artifactId: string) => {
    const { url } = await artifactsApi.getSignedUrl(artifactId);
    window.open(url, "_blank");
  },

  delete: (artifactId: string) =>
    fetchApi<void>(`/api/v1/artifacts/${artifactId}`, { method: "DELETE" }),
};

// ------------------------------------------------------------------
// Secrets
// ------------------------------------------------------------------
export interface Secret {
  id: string;
  scope_type: string;
  scope_id: string | null;
  name: string;
  secret_type: string;
  created_at: string;
  updated_at: string;
}

export interface CreateSecretRequest {
  scope_type: string;
  scope_id?: string;
  name: string;
  secret_type?: string;
  value: string;
}

/** Info about a pipeline that was auto-updated when a secret/variable was renamed. */
export interface UpdatedPipelineRef {
  pipeline_id: string;
  pipeline_name: string;
  occurrences: number;
}

/** Parse the X-Updated-Pipelines header from a response. */
function parseUpdatedPipelines(headers: Headers): UpdatedPipelineRef[] {
  const raw = headers.get("x-updated-pipelines");
  if (!raw) return [];
  try {
    return JSON.parse(raw) as UpdatedPipelineRef[];
  } catch {
    return [];
  }
}

export const secretsApi = {
  list: (scopeType: string, scopeId?: string) => {
    const qs = new URLSearchParams({ scope_type: scopeType });
    if (scopeId) qs.set("scope_id", scopeId);
    return fetchApi<Secret[]>(`/api/v1/secrets-env/secrets?${qs.toString()}`);
  },

  create: (data: CreateSecretRequest) =>
    fetchApi<Secret>("/api/v1/secrets-env/secrets", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: async (secretId: string, data: { name?: string; value?: string; scope_type?: string; scope_id?: string | null }) => {
    const { data: secret, headers } = await fetchApiWithHeaders<Secret>(
      `/api/v1/secrets-env/secrets/${secretId}`,
      { method: "PUT", body: JSON.stringify(data) },
    );
    return { secret, updatedPipelines: parseUpdatedPipelines(headers) };
  },

  delete: (secretId: string) =>
    fetchApi<void>(`/api/v1/secrets-env/secrets/${secretId}`, {
      method: "DELETE",
    }),
};

// ------------------------------------------------------------------
// Environment Variables
// ------------------------------------------------------------------
export interface EnvVar {
  id: string;
  scope_type: string;
  scope_id: string | null;
  name: string;
  value: string;
  is_secret_ref: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateEnvVarRequest {
  scope_type: string;
  scope_id?: string;
  name: string;
  value: string;
}

export const envVarsApi = {
  list: (scopeType: string, scopeId?: string) => {
    const qs = new URLSearchParams({ scope_type: scopeType });
    if (scopeId) qs.set("scope_id", scopeId);
    return fetchApi<EnvVar[]>(`/api/v1/secrets-env/env-vars?${qs.toString()}`);
  },

  create: (data: CreateEnvVarRequest) =>
    fetchApi<EnvVar>("/api/v1/secrets-env/env-vars", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: async (envVarId: string, data: { value?: string; name?: string; scope_type?: string; scope_id?: string | null }) => {
    const { data: envVar, headers } = await fetchApiWithHeaders<EnvVar>(
      `/api/v1/secrets-env/env-vars/${envVarId}`,
      { method: "PUT", body: JSON.stringify(data) },
    );
    return { envVar, updatedPipelines: parseUpdatedPipelines(headers) };
  },

  delete: (envVarId: string) =>
    fetchApi<void>(`/api/v1/secrets-env/env-vars/${envVarId}`, {
      method: "DELETE",
    }),
};

// ------------------------------------------------------------------
// Agents
// ------------------------------------------------------------------
export type AgentStatus = "online" | "offline" | "busy";

export interface Agent {
  id: string;
  name: string;
  labels: string[];
  os: string | null;
  arch: string | null;
  capacity: number;
  enabled: boolean;
  last_seen_at: string | null;
  status: AgentStatus | string;
  // Agent-token metadata (PRD §6.3 / F-3.4). Plaintext token is returned
  // only on register / rotate via AgentRegistrationResponse.
  token_prefix?: string | null;
  token_issued_at?: string | null;
  // Reported by the agent on connect.
  agent_version?: string | null;
  connected_at?: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface AgentRegistrationResponse extends Agent {
  registration_token: string;
}

export interface CreateAgentRequest {
  name: string;
  labels?: string[];
  os?: string;
  arch?: string;
  capacity?: number;
  enabled?: boolean;
}

export interface UpdateAgentRequest {
  name?: string;
  labels?: string[];
  os?: string;
  arch?: string;
  capacity?: number;
  enabled?: boolean;
}

// Keep in sync with backend `SUPPORTED_OS` / `SUPPORTED_ARCH` in
// pipeline_compiler.py — these are the values the dispatcher knows how
// to match against a stage's `runs_on`.
export const AGENT_OS_OPTIONS: { value: string; label: string }[] = [
  { value: "linux", label: "Linux" },
  { value: "windows", label: "Windows" },
  { value: "darwin", label: "macOS" },
];

export const AGENT_ARCH_OPTIONS: { value: string; label: string }[] = [
  { value: "amd64", label: "amd64 (x86_64)" },
  { value: "arm64", label: "arm64 (aarch64)" },
];

// ------------------------------------------------------------------
// System / Runtime config
// ------------------------------------------------------------------
export type AiStatus =
  | "ready"
  | "disabled"
  | "missing_api_key"
  | "misconfigured";

export interface AiInfo {
  enabled: boolean;
  provider: string;
  model: string;
  base_url: string | null;
  has_api_key: boolean;
  configured: boolean;
  status: AiStatus;
  status_detail: string;
}

export interface StorageInfo {
  storage_root: string;
  retention_builds: number;
  retention_days: number;
}

export interface AuthInfo {
  signup_enabled: boolean;
  default_role: string;
}

export interface RegistryInfo {
  enabled: boolean;
  host: string;
  storage_path: string;
  max_upload_mb: number;
  gc_cron: string;
}

export interface GitIntegrationInfo {
  github_oauth_configured: boolean;
  gitlab_oauth_configured: boolean;
  webhook_delivery_retention: number;
  webhook_rate_limit_per_minute: number;
}

export interface MaintenanceInfo {
  enabled: boolean;
  message: string | null;
}

export interface SystemInfo {
  version: string;
  public_url: string;
  log_level: string;
  maintenance: MaintenanceInfo;
  ai: AiInfo;
  storage: StorageInfo;
  auth: AuthInfo;
  registry: RegistryInfo;
  git: GitIntegrationInfo;
}

export interface AiSettingsUpdate {
  enabled?: boolean;
  provider?: string;
  api_key?: string;
  model?: string;
  base_url?: string;
}

export const systemApi = {
  info: () => fetchApi<SystemInfo>("/api/v1/system/info"),

  updateAi: (data: AiSettingsUpdate) =>
    fetchApi<AiInfo>("/api/v1/system/ai", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getMaintenance: () =>
    fetchApi<MaintenanceInfo>("/api/v1/system/maintenance"),

  setMaintenance: (data: { enabled: boolean; message?: string | null }) =>
    fetchApi<MaintenanceInfo>("/api/v1/system/maintenance", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------
// Search
// ------------------------------------------------------------------
export interface SearchHit {
  id: string;
  type: "project" | "pipeline" | "build" | "artifact";
  title: string;
  subtitle: string | null;
  url: string;
  extra: Record<string, unknown>;
}

export interface SearchResponse {
  query: string;
  results: SearchHit[];
}

export const searchApi = {
  search: (q: string, limit = 5) =>
    fetchApi<SearchResponse>(
      `/api/v1/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
};

export const agentsApi = {
  list: (params?: { status?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<Agent[]>(`/api/v1/agents${query ? `?${query}` : ""}`);
  },

  create: (data: CreateAgentRequest) =>
    fetchApi<AgentRegistrationResponse>("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) => fetchApi<Agent>(`/api/v1/agents/${id}`),

  update: (id: string, data: Partial<CreateAgentRequest>) =>
    fetchApi<Agent>(`/api/v1/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/agents/${id}`, { method: "DELETE" }),

  rotateToken: (id: string) =>
    fetchApi<AgentRegistrationResponse>(
      `/api/v1/agents/${id}/rotate-token`,
      { method: "POST" },
    ),
};

// ------------------------------------------------------------------
// Git Provider Integration (PRD §6.16)
// ------------------------------------------------------------------
export type GitProviderType = "github" | "gitlab" | "generic";
export type GitAuthMode = "pat" | "oauth";
export type GitValidationStatus = "unknown" | "ok" | "failed";

export interface GitConnection {
  id: string;
  name: string;
  provider_type: GitProviderType | string;
  base_url: string | null;
  auth_mode: GitAuthMode | string;
  credential_hint: string | null;
  validation_status: GitValidationStatus | string;
  last_validated_at: string | null;
  validation_error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface CreateGitConnectionRequest {
  name: string;
  provider_type: GitProviderType;
  base_url?: string | null;
  auth_mode?: GitAuthMode;
  credential: string;
}

export interface UpdateGitConnectionRequest {
  name?: string;
  base_url?: string | null;
  credential?: string;
}

export interface GitConnectionTestResult {
  ok: boolean;
  status: string;
  detail: string;
  http_status: number | null;
  latency_ms: number | null;
}

export interface ProviderRepositoryInfo {
  full_name: string;
  clone_url: string;
  default_branch: string;
  private: boolean;
  description: string | null;
  html_url: string | null;
  updated_at: string | null;
}

export interface ProviderRepositoryList {
  ok: boolean;
  status: string;       // "ok" | "failed" | "unsupported"
  detail: string;
  repositories: ProviderRepositoryInfo[];
}

export interface ProviderBranchList {
  ok: boolean;
  status: string;
  detail: string;
  branches: string[];
}

export const gitConnectionsApi = {
  list: () => fetchApi<GitConnection[]>("/api/v1/git/connections/"),

  create: (data: CreateGitConnectionRequest) =>
    fetchApi<GitConnection>("/api/v1/git/connections/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) =>
    fetchApi<GitConnection>(`/api/v1/git/connections/${id}`),

  update: (id: string, data: UpdateGitConnectionRequest) =>
    fetchApi<GitConnection>(`/api/v1/git/connections/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/git/connections/${id}`, { method: "DELETE" }),

  test: (id: string) =>
    fetchApi<GitConnectionTestResult>(
      `/api/v1/git/connections/${id}/test`,
      { method: "POST" },
    ),

  repositories: (id: string, limit = 100) =>
    fetchApi<ProviderRepositoryList>(
      `/api/v1/git/connections/${id}/repositories?limit=${limit}`,
    ),

  branches: (id: string, repoFullName: string) =>
    fetchApi<ProviderBranchList>(
      `/api/v1/git/connections/${id}/branches?repo=${encodeURIComponent(repoFullName)}`,
    ),
};

export interface ProjectRepository {
  id: string;
  project_id: string;
  connection_id: string;
  repo_url: string;
  default_branch: string;
  display_name: string | null;
  webhook_slug: string;
  last_event_at: string | null;
  last_event_status: string | null;
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface ProjectRepositoryWithSecret extends ProjectRepository {
  webhook_secret: string;
  webhook_url: string;
}

export interface CreateProjectRepositoryRequest {
  connection_id: string;
  repo_url: string;
  default_branch?: string;
  display_name?: string | null;
}

export interface UpdateProjectRepositoryRequest {
  default_branch?: string;
  display_name?: string | null;
}

export interface WebhookDelivery {
  id: string;
  project_repository_id: string;
  provider_delivery_id: string;
  event_type: string | null;
  branch: string | null;
  commit_sha: string | null;
  author: string | null;
  signature_valid: boolean;
  http_status: number;
  error: string | null;
  payload_excerpt: string | null;
  received_at: string;
  processed_at: string | null;
}

export const projectRepositoriesApi = {
  list: (projectId: string) =>
    fetchApi<ProjectRepository[]>(
      `/api/v1/projects/${projectId}/repositories/`,
    ),

  create: (projectId: string, data: CreateProjectRepositoryRequest) =>
    fetchApi<ProjectRepositoryWithSecret>(
      `/api/v1/projects/${projectId}/repositories/`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  update: (
    projectId: string,
    repoId: string,
    data: UpdateProjectRepositoryRequest,
  ) =>
    fetchApi<ProjectRepository>(
      `/api/v1/projects/${projectId}/repositories/${repoId}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),

  delete: (projectId: string, repoId: string) =>
    fetchApi<void>(
      `/api/v1/projects/${projectId}/repositories/${repoId}`,
      { method: "DELETE" },
    ),

  rotateSecret: (projectId: string, repoId: string) =>
    fetchApi<ProjectRepositoryWithSecret>(
      `/api/v1/projects/${projectId}/repositories/${repoId}/rotate-secret`,
      { method: "POST" },
    ),

  deliveries: (projectId: string, repoId: string, limit = 50) =>
    fetchApi<WebhookDelivery[]>(
      `/api/v1/projects/${projectId}/repositories/${repoId}/deliveries?limit=${limit}`,
    ),
};

// ------------------------------------------------------------------
// Roles
// ------------------------------------------------------------------
export interface Role {
  id: string;
  name: string;
  description: string | null;
  permissions: string[];
  is_system: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface CreateRoleRequest {
  name: string;
  description?: string;
  permissions?: string[];
}

export interface UpdateRoleRequest {
  name?: string;
  description?: string;
  permissions?: string[];
}

export const rolesApi = {
  list: () => fetchApi<Role[]>("/api/v1/roles/"),

  get: (id: string) => fetchApi<Role>(`/api/v1/roles/${id}`),

  create: (data: CreateRoleRequest) =>
    fetchApi<Role>("/api/v1/roles/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: (id: string, data: UpdateRoleRequest) =>
    fetchApi<Role>(`/api/v1/roles/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/roles/${id}`, { method: "DELETE" }),
};

// ------------------------------------------------------------------
// User Management
// ------------------------------------------------------------------
export interface UserRoleInfo {
  id: string;
  role_id: string;
  role_name: string | null;
  scope_type: string;
  scope_id: string | null;
}

export interface UserDetail {
  id: string;
  email: string;
  name: string;
  is_admin: boolean;
  is_active: boolean;
  auth_provider: string;
  created_at: string;
  updated_at: string | null;
  roles: UserRoleInfo[];
}

export interface CreateUserRequest {
  email: string;
  name: string;
  role_id: string;
}

export interface UserCreated extends UserDetail {
  generated_password: string;
}

export interface UpdateUserRequest {
  name?: string;
  is_active?: boolean;
  is_admin?: boolean;
}

export interface AssignRoleRequest {
  role_id: string;
  scope_type?: string;
  scope_id?: string;
}

export interface UserRoleAssignment {
  id: string;
  user_id: string;
  role_id: string;
  scope_type: string;
  scope_id: string | null;
  role_name: string | null;
  created_at: string;
}

export const usersApi = {
  list: (params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<UserDetail[]>(`/api/v1/users/${query ? `?${query}` : ""}`);
  },

  create: (data: CreateUserRequest) =>
    fetchApi<UserCreated>("/api/v1/users/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) => fetchApi<UserDetail>(`/api/v1/users/${id}`),

  update: (id: string, data: UpdateUserRequest) =>
    fetchApi<UserDetail>(`/api/v1/users/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  assignRole: (userId: string, data: AssignRoleRequest) =>
    fetchApi<UserRoleAssignment>(`/api/v1/users/${userId}/roles`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  removeRole: (userId: string, userRoleId: string) =>
    fetchApi<void>(`/api/v1/users/${userId}/roles/${userRoleId}`, {
      method: "DELETE",
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/users/${id}`, { method: "DELETE" }),
};

// ------------------------------------------------------------------
// Invitations
// ------------------------------------------------------------------
export interface Invite {
  id: string;
  email: string;
  role_id: string;
  role_name: string | null;
  status: string;
  expires_at: string;
  created_by: string | null;
  creator_name: string | null;
  accepted_at: string | null;
  created_at: string;
}

export interface InviteCreated extends Invite {
  invite_link: string;
}

export interface CreateInviteRequest {
  email: string;
  role_id: string;
}

export interface AcceptInviteRequest {
  token: string;
  name: string;
  password: string;
}

export const invitesApi = {
  list: (status?: string) => {
    const qs = status ? `?status_filter=${status}` : "";
    return fetchApi<Invite[]>(`/api/v1/invites/${qs}`);
  },

  create: (data: CreateInviteRequest) =>
    fetchApi<InviteCreated>("/api/v1/invites/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revoke: (id: string) =>
    fetchApi<void>(`/api/v1/invites/${id}`, { method: "DELETE" }),

  resend: (id: string) =>
    fetchApi<Invite>(`/api/v1/invites/${id}/resend`, { method: "POST" }),

  cleanup: () =>
    fetchApi<{ deleted: number }>("/api/v1/invites/cleanup", { method: "DELETE" }),

  accept: (data: AcceptInviteRequest) =>
    fetchApi<AuthTokens>("/api/v1/invites/accept", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------
// Notification Channels
// ------------------------------------------------------------------
export type NotificationChannelType = "email" | "slack" | "telegram";

export interface NotificationChannel {
  id: string;
  name: string;
  channel_type: NotificationChannelType;
  enabled: boolean;
  config_summary: Record<string, unknown>;
  validation_status: string;
  last_validated_at: string | null;
  validation_error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface NotificationChannelCreate {
  name: string;
  channel_type: NotificationChannelType;
  config: Record<string, unknown>;
}

export interface NotificationChannelUpdate {
  name?: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
}

export interface NotificationChannelTestResult {
  ok: boolean;
  detail: string;
}

export interface NotificationDelivery {
  id: string;
  channel_id: string;
  build_id: string | null;
  step_id: string | null;
  recipient: string | null;
  subject: string | null;
  message: string;
  status: string;
  error: string | null;
  sent_at: string | null;
  created_at: string;
}

export const notificationChannelsApi = {
  list: () =>
    fetchApi<NotificationChannel[]>("/api/v1/notifications/channels"),

  create: (data: NotificationChannelCreate) =>
    fetchApi<NotificationChannel>("/api/v1/notifications/channels", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  get: (id: string) =>
    fetchApi<NotificationChannel>(`/api/v1/notifications/channels/${id}`),

  update: (id: string, data: NotificationChannelUpdate) =>
    fetchApi<NotificationChannel>(`/api/v1/notifications/channels/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/notifications/channels/${id}`, {
      method: "DELETE",
    }),

  test: (id: string) =>
    fetchApi<NotificationChannelTestResult>(
      `/api/v1/notifications/channels/${id}/test`,
      { method: "POST" },
    ),

  deliveries: (params?: { channel_id?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.channel_id) qs.set("channel_id", params.channel_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<NotificationDelivery[]>(
      `/api/v1/notifications/deliveries${query ? `?${query}` : ""}`,
    );
  },
};

// ------------------------------------------------------------------
// AI Pipeline Assistant
// ------------------------------------------------------------------
export interface AiChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AiAssistantRequest {
  prompt: string;
  current_yaml?: string | null;
  project_id?: string | null;
  pipeline_id?: string | null;
  repo_url?: string | null;
  branch?: string | null;
  history?: AiChatMessage[];
}

export interface AiAssistantResponse {
  reply: string;
  yaml: string | null;
}

export const aiAssistantApi = {
  ask: (data: AiAssistantRequest) =>
    fetchApi<AiAssistantResponse>("/api/v1/ai/assistant", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------
// In-App User Notifications
// ------------------------------------------------------------------
export type UserNotificationType =
  | "build_success"
  | "build_failed"
  | "build_cancelled"
  | "agent_offline"
  | "agent_online"
  | "invite_received"
  | "invite_accepted"
  | "role_assigned"
  | "role_removed"
  | "pipeline_enabled"
  | "pipeline_disabled";

export interface UserNotification {
  id: string;
  user_id: string;
  type: UserNotificationType | string;
  title: string;
  body: string;
  entity_type: string | null;
  entity_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface UnreadCountResponse {
  count: number;
}

export const userNotificationsApi = {
  list: (params?: { unread_only?: boolean; limit?: number; before?: string }) => {
    const qs = new URLSearchParams();
    if (params?.unread_only) qs.set("unread_only", "true");
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.before) qs.set("before", params.before);
    const query = qs.toString();
    return fetchApi<UserNotification[]>(
      `/api/v1/user-notifications${query ? `?${query}` : ""}`,
    );
  },

  unreadCount: () =>
    fetchApi<UnreadCountResponse>("/api/v1/user-notifications/unread-count"),

  markRead: (id: string) =>
    fetchApi<UserNotification>(
      `/api/v1/user-notifications/${id}/read`,
      { method: "PATCH" },
    ),

  markAllRead: () =>
    fetchApi<void>("/api/v1/user-notifications/mark-all-read", {
      method: "POST",
    }),
};

// ------------------------------------------------------------------
// Container Registry (PRD §6.13)
// ------------------------------------------------------------------

export interface ContainerRepository {
  id: string;
  project_id: string;
  name: string;
  allow_anonymous_pull: boolean;
  immutable_tags: boolean;
  quota_bytes: number | null;
  used_bytes: number;
  created_at: string;
  updated_at: string | null;
}

export interface ContainerImage {
  id: string;
  repository_id: string;
  digest: string;
  media_type: string;
  size_bytes: number;
  config_digest: string | null;
  build_id: string | null;
  pushed_by: string | null;
  created_at: string;
}

export interface ContainerImageDetail extends ContainerImage {
  tags: ContainerTag[];
}

export interface ContainerTag {
  id: string;
  repository_id: string;
  image_id: string;
  name: string;
  created_at: string;
  updated_at: string | null;
}

export interface DeployToken {
  id: string;
  project_id: string | null;
  name: string;
  token_hint: string;
  scope: string;
  expires_at: string | null;
  is_active: boolean;
  last_used_at: string | null;
  created_by: string;
  created_at: string;
}

export interface DeployTokenCreated extends DeployToken {
  token: string;
}

export interface RegistryEvent {
  id: string;
  repository_id: string;
  event_type: string;
  digest: string | null;
  tag: string | null;
  actor_id: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface RegistryOverview {
  total_repositories: number;
  total_images: number;
  total_tags: number;
  total_size_bytes: number;
}

export const registryApi = {
  overview: () =>
    fetchApi<RegistryOverview>("/api/v1/registry/overview"),

  listRepositories: (params?: { project_id?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.project_id) qs.set("project_id", params.project_id);
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<ContainerRepository[]>(
      `/api/v1/registry/repositories${query ? `?${query}` : ""}`,
    );
  },

  getRepository: (id: string) =>
    fetchApi<ContainerRepository>(`/api/v1/registry/repositories/${id}`),

  updateRepository: (id: string, data: Partial<Pick<ContainerRepository, "allow_anonymous_pull" | "immutable_tags" | "quota_bytes">>) =>
    fetchApi<ContainerRepository>(`/api/v1/registry/repositories/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteRepository: (id: string) =>
    fetchApi<void>(`/api/v1/registry/repositories/${id}`, { method: "DELETE" }),

  listImages: (repoId: string, params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<ContainerImage[]>(
      `/api/v1/registry/repositories/${repoId}/images${query ? `?${query}` : ""}`,
    );
  },

  getImage: (imageId: string) =>
    fetchApi<ContainerImageDetail>(`/api/v1/registry/images/${imageId}`),

  listTags: (repoId: string, params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<ContainerTag[]>(
      `/api/v1/registry/repositories/${repoId}/tags${query ? `?${query}` : ""}`,
    );
  },

  deleteTag: (tagId: string) =>
    fetchApi<void>(`/api/v1/registry/tags/${tagId}`, { method: "DELETE" }),

  listDeployTokens: (params?: { project_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.project_id) qs.set("project_id", params.project_id);
    const query = qs.toString();
    return fetchApi<DeployToken[]>(
      `/api/v1/registry/deploy-tokens${query ? `?${query}` : ""}`,
    );
  },

  createDeployToken: (data: { name: string; scope: string; expires_in_days?: number }, projectId?: string | null) => {
    const qs = projectId ? `?project_id=${projectId}` : "";
    return fetchApi<DeployTokenCreated>(
      `/api/v1/registry/deploy-tokens${qs}`,
      { method: "POST", body: JSON.stringify(data) },
    );
  },

  revokeDeployToken: (tokenId: string) =>
    fetchApi<void>(`/api/v1/registry/deploy-tokens/${tokenId}/revoke`, { method: "PATCH" }),

  deleteDeployToken: (tokenId: string) =>
    fetchApi<void>(`/api/v1/registry/deploy-tokens/${tokenId}`, { method: "DELETE" }),

  listEvents: (params?: { repository_id?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.repository_id) qs.set("repository_id", params.repository_id);
    if (params?.skip) qs.set("skip", String(params.skip));
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return fetchApi<RegistryEvent[]>(
      `/api/v1/registry/events${query ? `?${query}` : ""}`,
    );
  },
};

// ------------------------------------------------------------------
// API Tokens (Personal Access Tokens)
// ------------------------------------------------------------------
export interface ApiToken {
  id: string;
  name: string;
  token_hint: string;
  scopes: string[] | null;
  expires_at: string | null;
  is_active: boolean;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export const apiTokensApi = {
  list: () => fetchApi<ApiToken[]>("/api/v1/tokens"),

  create: (data: { name: string; expires_in_days?: number | null; scopes?: string[] | null }) =>
    fetchApi<ApiTokenCreated>("/api/v1/tokens", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revoke: (tokenId: string) =>
    fetchApi<void>(`/api/v1/tokens/${tokenId}`, { method: "DELETE" }),
};
