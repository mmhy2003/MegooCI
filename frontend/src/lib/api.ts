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
  return fetch(`${BASE_URL}${endpoint}`, { ...options, headers });
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
    throw new ApiError(res.status, body, `API error: ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
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

  delete: (id: string) =>
    fetchApi<void>(`/api/v1/projects/${id}`, { method: "DELETE" }),
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
  definition_format: "yaml" | "python";
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
  definition_format?: "yaml" | "python";
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

  update: (id: string, data: Partial<CreatePipelineRequest>) =>
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
  command: string | null;
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

  cancel: (id: string) =>
    fetchApi<Build>(`/api/v1/builds/${id}/cancel`, { method: "POST" }),

  retry: (id: string) =>
    fetchApi<Build>(`/api/v1/builds/${id}/retry`, { method: "POST" }),
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

  update: (envVarId: string, data: { value: string }) =>
    fetchApi<EnvVar>(`/api/v1/secrets-env/env-vars/${envVarId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

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
}

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
}

export interface GitIntegrationInfo {
  github_oauth_configured: boolean;
  gitlab_oauth_configured: boolean;
  webhook_delivery_retention: number;
  webhook_rate_limit_per_minute: number;
}

export interface SystemInfo {
  version: string;
  public_url: string;
  log_level: string;
  ai: AiInfo;
  storage: StorageInfo;
  auth: AuthInfo;
  registry: RegistryInfo;
  git: GitIntegrationInfo;
}

export const systemApi = {
  info: () => fetchApi<SystemInfo>("/api/v1/system/info"),
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
