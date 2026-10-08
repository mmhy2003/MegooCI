"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  CloudOff,
  CloudUpload,
  DatabaseBackup,
  Download,
  Loader2,
  RotateCcw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { RequireAdmin } from "@/components/require-permission";
import {
  backupsApi,
  type BackupEntry,
  type BackupOverview,
  type BackupRestoreCheck,
  type BackupSchedule,
} from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const KIND_LABEL: Record<BackupEntry["kind"], string> = {
  manual: "Manual",
  scheduled: "Scheduled",
  "pre-restore": "Before restore",
  uploaded: "Uploaded",
};

function message(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function scheduleInWords(schedule: BackupSchedule): string {
  if (schedule.frequency === "off") return "No scheduled backups.";
  const when =
    schedule.frequency === "daily"
      ? `every day at ${schedule.time} UTC`
      : `every ${WEEKDAYS[schedule.weekday]} at ${schedule.time} UTC`;
  return `A backup is made ${when}; the newest ${schedule.keep} are kept.`;
}

/** The restore dialog: what a restore does, what it needs, and the confirmation. */
function RestoreDialog({
  backup,
  onClose,
  onRestored,
}: {
  backup: BackupEntry | null;
  onClose: () => void;
  onRestored: () => void;
}) {
  const [checks, setChecks] = React.useState<BackupRestoreCheck[] | null>(null);
  const [confirm, setConfirm] = React.useState("");
  const [passphrase, setPassphrase] = React.useState("");
  const [needsPassphrase, setNeedsPassphrase] = React.useState(false);
  const [problem, setProblem] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const name = backup?.name ?? null;

  React.useEffect(() => {
    setChecks(null);
    setConfirm("");
    setPassphrase("");
    setNeedsPassphrase(false);
    setProblem(null);
    if (!name) return;
    let current = true;
    backupsApi
      .restoreChecks(name)
      .then((result) => current && setChecks(result.checks))
      .catch((err) => current && setProblem(message(err)));
    return () => {
      current = false;
    };
  }, [name]);

  async function restore() {
    if (!name) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await backupsApi.restore(name, {
        confirm,
        passphrase: needsPassphrase ? passphrase : null,
      });
      toast.success(`Restored. The previous state was saved as ${result.pre_restore}.`);
      if (!result.search_index_rebuilt) {
        toast.warning("The search index could not be rebuilt; search may be out of date.");
      }
      onRestored();
      onClose();
    } catch (err) {
      // 422: the stored passphrase does not open this file.
      if ((err as { status?: number }).status === 422) setNeedsPassphrase(true);
      setProblem(message(err));
    } finally {
      setBusy(false);
    }
  }

  const ready = checks !== null && checks.every((check) => check.ok);

  return (
    <Dialog open={backup !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Restore this backup?</DialogTitle>
          <DialogDescription>
            {backup && `${KIND_LABEL[backup.kind]} backup from ${formatTime(backup.created_at)}`}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 leading-relaxed">
            The server&apos;s configuration will be replaced with this backup: users, roles,
            projects, pipelines, secrets, channels, agents and settings. Anything created since
            the backup is removed, together with its build history. Build history of everything
            that still exists is kept. Your own account keeps its password and stays an
            administrator. A backup of the current state is made first.
          </div>

          <ul className="space-y-1.5">
            {checks === null && !problem && (
              <li className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking…
              </li>
            )}
            {checks?.map((check) => (
              <li key={check.name} className="flex items-start gap-2">
                {check.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                )}
                <span>{check.message}</span>
              </li>
            ))}
          </ul>

          {needsPassphrase && (
            <label className="block space-y-1.5">
              <span className="font-medium">Passphrase this backup was made with</span>
              <Input
                type="password"
                autoComplete="off"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </label>
          )}

          <label className="block space-y-1.5">
            <span className="font-medium">
              Type <code className="rounded bg-muted px-1">restore</code> to confirm
            </span>
            <Input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="off"
              disabled={!ready}
            />
          </label>

          {problem && <p className="text-destructive">{problem}</p>}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={restore}
            disabled={busy || !ready || confirm !== "restore" || (needsPassphrase && !passphrase)}
          >
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Restore
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Passphrase, schedule and remote storage. Each part is saved on its own. */
function BackupSettings({
  overview,
  onSaved,
}: {
  overview: BackupOverview;
  onSaved: (overview: BackupOverview) => void;
}) {
  const [passphrase, setPassphrase] = React.useState("");
  const [schedule, setSchedule] = React.useState(overview.schedule);
  const [remote, setRemote] = React.useState({ ...overview.remote, secret_access_key: "" });
  const [busy, setBusy] = React.useState<string | null>(null);

  async function run(what: string, action: () => Promise<void>) {
    setBusy(what);
    try {
      await action();
    } catch (err) {
      toast.error(message(err));
    } finally {
      setBusy(null);
    }
  }

  function remoteBody() {
    return {
      enabled: remote.enabled,
      endpoint_url: remote.endpoint_url.trim(),
      region: remote.region.trim(),
      bucket: remote.bucket.trim(),
      prefix: remote.prefix.trim(),
      access_key_id: remote.access_key_id.trim(),
      secret_access_key: remote.secret_access_key,
    };
  }

  const field = "block space-y-1.5 text-sm";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Settings</CardTitle>
        <CardDescription>
          These belong to this server. They are not part of a backup and a restore does not
          change them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-8">
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Passphrase</h3>
          <p className="text-sm text-muted-foreground">
            Every backup is encrypted with this passphrase.{" "}
            <strong>
              A backup cannot be restored without the passphrase it was made with, and the
              passphrase cannot be recovered from the server.
            </strong>{" "}
            Changing it affects new backups only.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className={`${field} min-w-64 flex-1`}>
              <span className="font-medium">
                {overview.passphrase_set ? "New passphrase" : "Passphrase"} (at least 12
                characters)
              </span>
              <Input
                type="password"
                autoComplete="new-password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </label>
            <Button
              type="button"
              disabled={busy !== null || passphrase.length < 12}
              onClick={() =>
                run("passphrase", async () => {
                  onSaved(await backupsApi.updateSettings({ passphrase }));
                  setPassphrase("");
                  toast.success("Passphrase saved. Keep a copy of it somewhere safe.");
                })
              }
            >
              {overview.passphrase_set ? "Change passphrase" : "Set passphrase"}
            </Button>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Schedule</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className={field}>
              <span className="font-medium">Frequency</span>
              <Select
                value={schedule.frequency}
                onChange={(e) =>
                  setSchedule({
                    ...schedule,
                    frequency: e.target.value as BackupSchedule["frequency"],
                  })
                }
                options={[
                  { value: "off", label: "Off" },
                  { value: "daily", label: "Daily" },
                  { value: "weekly", label: "Weekly" },
                ]}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Time (UTC)</span>
              <Input
                type="time"
                value={schedule.time}
                disabled={schedule.frequency === "off"}
                onChange={(e) => setSchedule({ ...schedule, time: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Day</span>
              <Select
                value={String(schedule.weekday)}
                disabled={schedule.frequency !== "weekly"}
                onChange={(e) => setSchedule({ ...schedule, weekday: Number(e.target.value) })}
                options={WEEKDAYS.map((label, index) => ({ value: String(index), label }))}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Scheduled backups to keep</span>
              <Input
                type="number"
                min={1}
                max={365}
                value={schedule.keep}
                disabled={schedule.frequency === "off"}
                onChange={(e) => setSchedule({ ...schedule, keep: Number(e.target.value) })}
              />
            </label>
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              run("schedule", async () => {
                onSaved(await backupsApi.updateSettings({ schedule }));
                toast.success("Schedule saved.");
              })
            }
          >
            Save schedule
          </Button>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Remote copy</h3>
          <p className="text-sm text-muted-foreground">
            Optional. When on, every backup is also copied to S3-compatible storage (AWS S3,
            MinIO, Backblaze B2 and others). Restoring uses the files on this server: to
            restore a backup that exists only in the bucket, download it there and upload it
            here.
          </p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={remote.enabled}
              onChange={(e) => setRemote({ ...remote, enabled: e.target.checked })}
            />
            Copy every backup to remote storage
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={field}>
              <span className="font-medium">Endpoint URL (empty for AWS)</span>
              <Input
                placeholder="https://s3.example.com"
                value={remote.endpoint_url}
                onChange={(e) => setRemote({ ...remote, endpoint_url: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Region</span>
              <Input
                placeholder="us-east-1"
                value={remote.region}
                onChange={(e) => setRemote({ ...remote, region: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Bucket</span>
              <Input
                value={remote.bucket}
                onChange={(e) => setRemote({ ...remote, bucket: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Folder in the bucket (optional)</span>
              <Input
                placeholder="megooci/backups"
                value={remote.prefix}
                onChange={(e) => setRemote({ ...remote, prefix: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Access key ID</span>
              <Input
                autoComplete="off"
                value={remote.access_key_id}
                onChange={(e) => setRemote({ ...remote, access_key_id: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Secret access key</span>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  overview.remote.secret_access_key_set ? "Saved — leave empty to keep it" : ""
                }
                value={remote.secret_access_key}
                onChange={(e) => setRemote({ ...remote, secret_access_key: e.target.value })}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null}
              onClick={() =>
                run("test", async () => {
                  await backupsApi.testRemote(remoteBody());
                  toast.success("The remote storage accepted a test file.");
                })
              }
            >
              {busy === "test" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Test connection
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null}
              onClick={() =>
                run("remote", async () => {
                  onSaved(await backupsApi.updateSettings({ remote: remoteBody() }));
                  setRemote((current) => ({ ...current, secret_access_key: "" }));
                  toast.success("Remote storage settings saved.");
                })
              }
            >
              Save remote storage
            </Button>
          </div>
        </section>
      </CardContent>
    </Card>
  );
}

function RemoteBadge({ backup, enabled }: { backup: BackupEntry; enabled: boolean }) {
  if (backup.remote?.status === "uploaded") {
    return (
      <span className="flex items-center gap-1 text-success">
        <CloudUpload className="h-3.5 w-3.5" /> Copied
      </span>
    );
  }
  if (backup.remote?.status === "failed") {
    return (
      <span className="flex items-center gap-1 text-destructive" title={backup.remote.error ?? ""}>
        <CloudOff className="h-3.5 w-3.5" /> Failed
      </span>
    );
  }
  return <span className="text-muted-foreground">{enabled ? "Not copied" : "—"}</span>;
}

function BackupsPage() {
  const [overview, setOverview] = React.useState<BackupOverview | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [restoring, setRestoring] = React.useState<BackupEntry | null>(null);
  const fileInput = React.useRef<HTMLInputElement>(null);

  const load = React.useCallback(async () => {
    try {
      setOverview(await backupsApi.overview());
      setLoadError(null);
    } catch (err) {
      setLoadError(message(err));
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  async function run(what: string, action: () => Promise<void>) {
    setBusy(what);
    try {
      await action();
      await load();
    } catch (err) {
      toast.error(message(err));
    } finally {
      setBusy(null);
    }
  }

  async function download(name: string) {
    const blob = await backupsApi.download(name);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (loadError && !overview) {
    return <p className="text-sm text-destructive">{loadError}</p>;
  }
  if (!overview) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const lastRun = overview.last_run;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <DatabaseBackup className="h-6 w-6" /> Backups
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            A backup holds the server&apos;s configuration: users, roles, projects, pipelines,
            secrets, channels, agents and settings. It does not hold build history, logs,
            artifacts or registry images.
          </p>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".mcbak"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) {
                run("upload", async () => {
                  await backupsApi.upload(file);
                  toast.success("Backup uploaded.");
                });
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={() => fileInput.current?.click()}
          >
            <Upload className="mr-2 h-4 w-4" /> Upload backup
          </Button>
          <Button
            type="button"
            disabled={busy !== null || !overview.passphrase_set}
            title={overview.passphrase_set ? undefined : "Set a passphrase first"}
            onClick={() =>
              run("create", async () => {
                await backupsApi.create();
                toast.success("Backup created.");
              })
            }
          >
            {busy === "create" ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <DatabaseBackup className="mr-2 h-4 w-4" />
            )}
            Create backup
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="space-y-1.5 pt-6 text-sm">
          {!overview.passphrase_set && (
            <p className="flex items-center gap-2 text-warning">
              <AlertTriangle className="h-4 w-4" /> No passphrase is set. Set one below before
              the first backup.
            </p>
          )}
          <p>{scheduleInWords(overview.schedule)}</p>
          {lastRun && (
            <p className={lastRun.ok ? "text-muted-foreground" : "text-destructive"}>
              Last scheduled backup: {formatTime(lastRun.at)} —{" "}
              {lastRun.ok ? "succeeded" : `failed: ${lastRun.error}`}
            </p>
          )}
          <p className="text-muted-foreground">
            Remote copy: {overview.remote.enabled ? `on (bucket ${overview.remote.bucket})` : "off"}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Backups on this server</CardTitle>
          <CardDescription>
            These files are on the server&apos;s own disk and do not survive losing it. Download
            the ones you need to keep, or turn on the remote copy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {overview.backups.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No backups yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Made</th>
                    <th className="py-2 pr-4 font-medium">Kind</th>
                    <th className="py-2 pr-4 font-medium">Size</th>
                    <th className="py-2 pr-4 font-medium">Remote</th>
                    <th className="py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.backups.map((backup) => (
                    <tr key={backup.name} className="border-b last:border-0">
                      <td className="py-2.5 pr-4">
                        <div>{formatTime(backup.created_at)}</div>
                        <div className="font-mono text-xs text-muted-foreground">{backup.name}</div>
                        {backup.error && (
                          <div className="text-xs text-destructive">{backup.error}</div>
                        )}
                        {!backup.error && !backup.compatible && (
                          <div className="text-xs text-warning">
                            Made with database version {backup.schema_revision}; this server is
                            at {overview.schema_revision}. It cannot be restored here.
                          </div>
                        )}
                      </td>
                      <td className="py-2.5 pr-4">
                        <Badge variant="outline">{KIND_LABEL[backup.kind]}</Badge>
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap">{formatSize(backup.size)}</td>
                      <td className="py-2.5 pr-4 whitespace-nowrap">
                        <RemoteBadge backup={backup} enabled={overview.remote.enabled} />
                      </td>
                      <td className="py-2.5">
                        <div className="flex justify-end gap-1">
                          {overview.remote.enabled && backup.remote?.status !== "uploaded" && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={busy !== null}
                              title="Copy to remote storage"
                              onClick={() =>
                                run(`remote:${backup.name}`, async () => {
                                  const status = await backupsApi.retryRemote(backup.name);
                                  if (status.status === "failed") {
                                    throw new Error(`The remote storage reported: ${status.error}`);
                                  }
                                  toast.success("Copied to remote storage.");
                                })
                              }
                            >
                              <CloudUpload className="h-4 w-4" />
                            </Button>
                          )}
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null}
                            title="Download"
                            onClick={() => run(`download:${backup.name}`, () => download(backup.name))}
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null || !backup.compatible}
                            title={backup.compatible ? "Restore" : "Cannot be restored on this server"}
                            onClick={() => setRestoring(backup)}
                          >
                            <RotateCcw className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null}
                            title="Delete"
                            onClick={() => {
                              if (!window.confirm(`Delete ${backup.name}? This cannot be undone.`)) {
                                return;
                              }
                              run(`delete:${backup.name}`, async () => {
                                const result = await backupsApi.remove(backup.name);
                                if (result.remote_error) {
                                  toast.warning(
                                    `Deleted here, but the remote copy could not be deleted: ${result.remote_error}`,
                                  );
                                } else {
                                  toast.success("Backup deleted.");
                                }
                              });
                            }}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <BackupSettings overview={overview} onSaved={setOverview} />

      <RestoreDialog backup={restoring} onClose={() => setRestoring(null)} onRestored={load} />
    </div>
  );
}

export default function AdminBackupsPage() {
  return (
    <AppLayout>
      <RequireAdmin
        fallback={
          <p className="text-sm text-muted-foreground">
            Only administrators can manage backups.
          </p>
        }
      >
        <BackupsPage />
      </RequireAdmin>
    </AppLayout>
  );
}
