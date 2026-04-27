"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Download, FileArchive, Trash2 } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { usePermission } from "@/hooks/use-permission";
import {
  artifactsApi,
  type ArtifactListItem,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

export default function ArtifactsPage() {
  const confirm = useConfirm();
  const canManage = usePermission("artifacts.manage");
  const [artifacts, setArtifacts] = React.useState<ArtifactListItem[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    artifactsApi
      .listAll({ limit: 100 })
      .then(setArtifacts)
      .catch(() => toast.error("Failed to load artifacts"))
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete(artifact: ArtifactListItem) {
    const ok = await confirm({
      title: "Delete this artifact?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {artifact.relative_path}
          </code>{" "}
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

  const totalSize = artifacts.reduce((sum, a) => sum + a.size_bytes, 0);

  return (
    <AppLayout>
      <div className="space-y-8">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Artifacts
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Build outputs collected from your pipelines.
            {!loading && artifacts.length > 0 && (
              <span className="ml-1">
                {artifacts.length} artifact{artifacts.length !== 1 && "s"} ·{" "}
                {formatSize(totalSize)} total
              </span>
            )}
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
              <FileArchive className="h-5 w-5" />
              All Artifacts
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : artifacts.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No artifacts yet. Artifacts appear here once a pipeline build
                produces outputs.
              </p>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[600px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">File</th>
                      <th className="pb-3 pr-4 font-medium">Pipeline</th>
                      <th className="pb-3 pr-4 font-medium">Build</th>
                      <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                        Size
                      </th>
                      <th className="hidden pb-3 pr-4 font-medium md:table-cell">
                        Created
                      </th>
                      <th className="pb-3 font-medium w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {artifacts.map((a) => (
                      <tr key={a.id} className="border-b last:border-0">
                        <td className="py-3 pr-4">
                          <code className="break-all font-medium">
                            {a.relative_path}
                          </code>
                        </td>
                        <td className="py-3 pr-4 text-muted-foreground">
                          {a.pipeline_name}
                        </td>
                        <td className="py-3 pr-4">
                          <Link
                            href={`/builds/${a.build_id}`}
                            className="text-primary hover:underline"
                          >
                            #{a.build_number}
                          </Link>
                        </td>
                        <td className="hidden py-3 pr-4 text-muted-foreground sm:table-cell">
                          {formatSize(a.size_bytes)}
                        </td>
                        <td className="hidden py-3 pr-4 text-muted-foreground md:table-cell">
                          {formatDistanceToNow(new Date(a.created_at), {
                            addSuffix: true,
                          })}
                        </td>
                        <td className="py-3">
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => artifactsApi.download(a.id)}
                              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                              title="Download"
                            >
                              <Download className="h-3.5 w-3.5" />
                            </button>
                            {canManage && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-destructive"
                                onClick={() => handleDelete(a)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            )}
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
      </div>
    </AppLayout>
  );
}
