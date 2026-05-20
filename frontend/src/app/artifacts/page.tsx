"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  Download,
  FileArchive,
  Trash2,
  ChevronDown,
  ChevronRight,
  FolderKanban,
  GitBranch,
  Hammer,
  HardDrive,
  Clock,
  Search,
  X,
  LayoutGrid,
  List,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { usePermission } from "@/hooks/use-permission";
import { artifactsApi, type ArtifactListItem } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// ──────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

type SortKey = "newest" | "oldest" | "size_desc" | "size_asc" | "name_asc";
type GroupMode = "project" | "flat";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "size_desc", label: "Largest first" },
  { value: "size_asc", label: "Smallest first" },
  { value: "name_asc", label: "Filename A→Z" },
];

function sortArtifacts(list: ArtifactListItem[], key: SortKey): ArtifactListItem[] {
  const sorted = [...list];
  switch (key) {
    case "newest":
      return sorted.sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );
    case "oldest":
      return sorted.sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
    case "size_desc":
      return sorted.sort((a, b) => b.size_bytes - a.size_bytes);
    case "size_asc":
      return sorted.sort((a, b) => a.size_bytes - b.size_bytes);
    case "name_asc":
      return sorted.sort((a, b) => a.relative_path.localeCompare(b.relative_path));
  }
}

// Group [project → build → files] for the default view.
interface BuildGroup {
  build_id: string;
  build_number: number;
  pipeline_id: string;
  pipeline_name: string;
  files: ArtifactListItem[];
  total_size: number;
  newest: string;
}

interface ProjectGroup {
  project_id: string;
  project_name: string;
  builds: BuildGroup[];
  total_files: number;
  total_size: number;
  newest: string;
}

function groupByProject(list: ArtifactListItem[]): ProjectGroup[] {
  const projects = new Map<string, ProjectGroup>();

  for (const a of list) {
    let proj = projects.get(a.project_id);
    if (!proj) {
      proj = {
        project_id: a.project_id,
        project_name: a.project_name,
        builds: [],
        total_files: 0,
        total_size: 0,
        newest: a.created_at,
      };
      projects.set(a.project_id, proj);
    }
    proj.total_files += 1;
    proj.total_size += a.size_bytes;
    if (new Date(a.created_at) > new Date(proj.newest)) proj.newest = a.created_at;

    let build = proj.builds.find((b) => b.build_id === a.build_id);
    if (!build) {
      build = {
        build_id: a.build_id,
        build_number: a.build_number,
        pipeline_id: a.pipeline_id,
        pipeline_name: a.pipeline_name,
        files: [],
        total_size: 0,
        newest: a.created_at,
      };
      proj.builds.push(build);
    }
    build.files.push(a);
    build.total_size += a.size_bytes;
    if (new Date(a.created_at) > new Date(build.newest)) build.newest = a.created_at;
  }

  // Sort: projects by newest desc; builds within a project by build_number desc.
  const groups = Array.from(projects.values());
  groups.sort((a, b) => new Date(b.newest).getTime() - new Date(a.newest).getTime());
  for (const p of groups) {
    p.builds.sort((a, b) => b.build_number - a.build_number);
  }
  return groups;
}

// ──────────────────────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────────────────────

export default function ArtifactsPage() {
  return (
    <React.Suspense fallback={null}>
      <ArtifactsPageInner />
    </React.Suspense>
  );
}

function ArtifactsPageInner() {
  const confirm = useConfirm();
  const canManage = usePermission("artifacts.manage");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // ── URL-backed state ──────────────────────────────────────────────────
  const initialQuery = searchParams.get("q") ?? "";
  const initialProject = searchParams.get("project") ?? "";
  const initialPipeline = searchParams.get("pipeline") ?? "";
  const initialSort = (searchParams.get("sort") as SortKey) || "newest";
  const initialGroup = (searchParams.get("group") as GroupMode) || "project";

  const [query, setQuery] = React.useState(initialQuery);
  const [projectFilter, setProjectFilter] = React.useState(initialProject);
  const [pipelineFilter, setPipelineFilter] = React.useState(initialPipeline);
  const [sortKey, setSortKey] = React.useState<SortKey>(initialSort);
  const [groupMode, setGroupMode] = React.useState<GroupMode>(initialGroup);

  // Sync state → URL (replace, not push, so back-button doesn't pile up).
  React.useEffect(() => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (projectFilter) params.set("project", projectFilter);
    if (pipelineFilter) params.set("pipeline", pipelineFilter);
    if (sortKey !== "newest") params.set("sort", sortKey);
    if (groupMode !== "project") params.set("group", groupMode);
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [query, projectFilter, pipelineFilter, sortKey, groupMode, pathname, router]);

  // ── Data ──────────────────────────────────────────────────────────────
  const [artifacts, setArtifacts] = React.useState<ArtifactListItem[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    artifactsApi
      .listAll({ limit: 200 })
      .then(setArtifacts)
      .catch(() => toast.error("Failed to load artifacts"))
      .finally(() => setLoading(false));
  }, []);

  // Distinct projects / pipelines for the filter dropdowns.
  const projectOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const a of artifacts) map.set(a.project_id, a.project_name);
    return Array.from(map.entries())
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [artifacts]);

  const pipelineOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const a of artifacts) {
      if (projectFilter && a.project_id !== projectFilter) continue;
      map.set(a.pipeline_id, a.pipeline_name);
    }
    return Array.from(map.entries())
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [artifacts, projectFilter]);

  // Reset pipeline filter when the chosen pipeline no longer belongs to the
  // currently selected project (keeps the URL state coherent).
  React.useEffect(() => {
    if (!pipelineFilter) return;
    if (!pipelineOptions.some((p) => p.value === pipelineFilter)) {
      setPipelineFilter("");
    }
  }, [pipelineOptions, pipelineFilter]);

  // ── Filter + sort ─────────────────────────────────────────────────────
  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    const f = artifacts.filter((a) => {
      if (projectFilter && a.project_id !== projectFilter) return false;
      if (pipelineFilter && a.pipeline_id !== pipelineFilter) return false;
      if (q && !a.relative_path.toLowerCase().includes(q)) return false;
      return true;
    });
    return sortArtifacts(f, sortKey);
  }, [artifacts, query, projectFilter, pipelineFilter, sortKey]);

  // ── Stats (computed from the filtered set so the cards reflect what's shown) ─
  const totalSize = filtered.reduce((sum, a) => sum + a.size_bytes, 0);
  const soonestExpiry = filtered.reduce<Date | null>((soonest, a) => {
    if (!a.retention_until) return soonest;
    const d = new Date(a.retention_until);
    return soonest === null || d < soonest ? d : soonest;
  }, null);

  // ── Actions ───────────────────────────────────────────────────────────
  async function handleDelete(artifact: ArtifactListItem) {
    const ok = await confirm({
      title: "Delete this artifact?",
      description: (
        <>
          <code className="font-mono text-foreground">{artifact.relative_path}</code>{" "}
          from build #{artifact.build_number} will be permanently removed.
        </>
      ),
      confirmText: "Delete",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await artifactsApi.delete(artifact.id);
      setArtifacts((prev) => prev.filter((a) => a.id !== artifact.id));
      toast.success("Artifact deleted");
    } catch {
      toast.error("Failed to delete artifact");
    }
  }

  const hasActiveFilter =
    query !== "" || projectFilter !== "" || pipelineFilter !== "";

  // ──────────────────────────────────────────────────────────────────────
  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">Artifacts</h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Build outputs collected from your pipelines.
          </p>
        </div>

        {/* Stat cards */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard
            icon={<FileArchive className="h-4 w-4" />}
            label="Artifacts"
            value={loading ? "—" : `${filtered.length}`}
            sub={
              hasActiveFilter && !loading
                ? `of ${artifacts.length} total`
                : undefined
            }
          />
          <StatCard
            icon={<HardDrive className="h-4 w-4" />}
            label="Total size"
            value={loading ? "—" : formatSize(totalSize)}
          />
          <StatCard
            icon={<Clock className="h-4 w-4" />}
            label="Soonest expiry"
            value={
              loading
                ? "—"
                : soonestExpiry
                  ? formatDistanceToNow(soonestExpiry, { addSuffix: true })
                  : "—"
            }
          />
        </div>

        {/* Filters */}
        <Card>
          <CardContent className="flex flex-col gap-3 p-3 sm:flex-row sm:flex-wrap sm:items-center">
            {/* Filename search */}
            <div className="relative min-w-0 flex-1 sm:flex-[2]">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search filename…"
                className="h-9 pl-9 pr-9"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label="Clear search"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Select
                value={projectFilter}
                onChange={(e) => setProjectFilter(e.target.value)}
                options={[{ value: "", label: "All projects" }, ...projectOptions]}
                className="h-9 w-full sm:w-44"
              />
              <Select
                value={pipelineFilter}
                onChange={(e) => setPipelineFilter(e.target.value)}
                options={[{ value: "", label: "All pipelines" }, ...pipelineOptions]}
                className="h-9 w-full sm:w-44"
              />
              <Select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as SortKey)}
                options={SORT_OPTIONS}
                className="h-9 w-full sm:w-40"
              />

              {/* Group / Flat toggle */}
              <div className="inline-flex rounded-md border bg-background p-0.5">
                <button
                  type="button"
                  onClick={() => setGroupMode("project")}
                  className={cn(
                    "inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-medium transition-colors",
                    groupMode === "project"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  title="Group by project / build"
                >
                  <LayoutGrid className="h-3.5 w-3.5" />
                  <span>Grouped</span>
                </button>
                <button
                  type="button"
                  onClick={() => setGroupMode("flat")}
                  className={cn(
                    "inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-medium transition-colors",
                    groupMode === "flat"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  title="Flat table"
                >
                  <List className="h-3.5 w-3.5" />
                  <span>Flat</span>
                </button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Body */}
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <FileArchive className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                {artifacts.length === 0
                  ? "No artifacts yet. Artifacts appear here once a pipeline build produces outputs."
                  : "No artifacts match the current filters."}
              </p>
              {hasActiveFilter && artifacts.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-3"
                  onClick={() => {
                    setQuery("");
                    setProjectFilter("");
                    setPipelineFilter("");
                  }}
                >
                  Clear filters
                </Button>
              )}
            </CardContent>
          </Card>
        ) : groupMode === "project" ? (
          <GroupedView
            groups={groupByProject(filtered)}
            canManage={canManage}
            onDelete={handleDelete}
          />
        ) : (
          <FlatTable
            artifacts={filtered}
            canManage={canManage}
            onDelete={handleDelete}
          />
        )}
      </div>
    </AppLayout>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Stat card
// ──────────────────────────────────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
          {icon}
        </div>
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {label}
          </div>
          <div className="truncate text-lg font-semibold leading-tight">
            {value}
          </div>
          {sub && (
            <div className="text-[11px] text-muted-foreground">{sub}</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Grouped view — Project ▸ Build ▸ Files
// ──────────────────────────────────────────────────────────────────────────

function GroupedView({
  groups,
  canManage,
  onDelete,
}: {
  groups: ProjectGroup[];
  canManage: boolean;
  onDelete: (a: ArtifactListItem) => void;
}) {
  return (
    <div className="space-y-3">
      {groups.map((p) => (
        <ProjectCard
          key={p.project_id}
          project={p}
          canManage={canManage}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function ProjectCard({
  project,
  canManage,
  onDelete,
}: {
  project: ProjectGroup;
  canManage: boolean;
  onDelete: (a: ArtifactListItem) => void;
}) {
  const [open, setOpen] = React.useState(true);

  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/30"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <FolderKanban className="h-4 w-4 shrink-0 text-blue-500" />
        <div className="min-w-0 flex-1">
          <Link
            href={`/projects/${project.project_id}`}
            onClick={(e) => e.stopPropagation()}
            className="font-semibold hover:underline"
          >
            {project.project_name}
          </Link>
        </div>
        <div className="hidden text-xs text-muted-foreground sm:block">
          {project.builds.length} build{project.builds.length !== 1 && "s"} ·{" "}
          {project.total_files} file{project.total_files !== 1 && "s"} ·{" "}
          {formatSize(project.total_size)}
        </div>
      </button>

      {open && (
        <div className="border-t">
          {project.builds.map((b) => (
            <BuildRow
              key={b.build_id}
              build={b}
              canManage={canManage}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

function BuildRow({
  build,
  canManage,
  onDelete,
}: {
  build: BuildGroup;
  canManage: boolean;
  onDelete: (a: ArtifactListItem) => void;
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-muted/30"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <Hammer className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        <Link
          href={`/builds/${build.build_id}`}
          onClick={(e) => e.stopPropagation()}
          className="font-mono font-medium text-primary hover:underline"
        >
          #{build.build_number}
        </Link>
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <GitBranch className="h-3 w-3" />
          <Link
            href={`/pipelines/${build.pipeline_id}`}
            onClick={(e) => e.stopPropagation()}
            className="hover:underline"
          >
            {build.pipeline_name}
          </Link>
        </span>
        <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          <span>
            {build.files.length} file{build.files.length !== 1 && "s"}
          </span>
          <span className="hidden sm:inline">{formatSize(build.total_size)}</span>
          <span className="hidden md:inline">
            {formatDistanceToNow(new Date(build.newest), { addSuffix: true })}
          </span>
        </div>
      </button>

      {open && (
        <ul className="border-t bg-muted/10">
          {build.files.map((a) => (
            <li
              key={a.id}
              className="flex items-center gap-3 px-4 py-2 pl-12 text-sm last:rounded-b-md"
            >
              <FileArchive className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
              <code className="min-w-0 flex-1 truncate break-all font-mono">
                {a.relative_path}
              </code>
              <span className="hidden text-xs text-muted-foreground sm:inline">
                {formatSize(a.size_bytes)}
              </span>
              <FileActions
                artifact={a}
                canManage={canManage}
                onDelete={onDelete}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Flat table view
// ──────────────────────────────────────────────────────────────────────────

function FlatTable({
  artifacts,
  canManage,
  onDelete,
}: {
  artifacts: ArtifactListItem[];
  canManage: boolean;
  onDelete: (a: ArtifactListItem) => void;
}) {
  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="px-4 py-3 font-medium">File</th>
                <th className="px-4 py-3 font-medium">Project</th>
                <th className="px-4 py-3 font-medium">Pipeline</th>
                <th className="px-4 py-3 font-medium">Build</th>
                <th className="hidden px-4 py-3 font-medium sm:table-cell">
                  Size
                </th>
                <th className="hidden px-4 py-3 font-medium md:table-cell">
                  Created
                </th>
                <th className="w-20 px-4 py-3 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {artifacts.map((a) => (
                <tr key={a.id} className="border-b last:border-0">
                  <td className="px-4 py-3">
                    <code className="break-all font-medium">
                      {a.relative_path}
                    </code>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <Link
                      href={`/projects/${a.project_id}`}
                      className="hover:underline"
                    >
                      {a.project_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <Link
                      href={`/pipelines/${a.pipeline_id}`}
                      className="hover:underline"
                    >
                      {a.pipeline_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/builds/${a.build_id}`}
                      className="text-primary hover:underline"
                    >
                      #{a.build_number}
                    </Link>
                  </td>
                  <td className="hidden px-4 py-3 text-muted-foreground sm:table-cell">
                    {formatSize(a.size_bytes)}
                  </td>
                  <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">
                    {formatDistanceToNow(new Date(a.created_at), {
                      addSuffix: true,
                    })}
                  </td>
                  <td className="px-4 py-3">
                    <FileActions
                      artifact={a}
                      canManage={canManage}
                      onDelete={onDelete}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Per-row download/delete actions
// ──────────────────────────────────────────────────────────────────────────

function FileActions({
  artifact,
  canManage,
  onDelete,
}: {
  artifact: ArtifactListItem;
  canManage: boolean;
  onDelete: (a: ArtifactListItem) => void;
}) {
  return (
    <div className="flex items-center justify-end gap-1">
      <button
        onClick={() => artifactsApi.download(artifact.id)}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        title="Download"
      >
        <Download className="h-3.5 w-3.5" />
      </button>
      {canManage && (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-destructive"
          onClick={() => onDelete(artifact)}
          title="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );
}
