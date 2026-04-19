const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

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
  return localStorage.getItem("megooci_access_token");
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAccessToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

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

export interface SystemInfo {
  version: string;
  public_url: string;
  log_level: string;
  ai: AiInfo;
  storage: StorageInfo;
  auth: AuthInfo;
  registry: RegistryInfo;
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
};
