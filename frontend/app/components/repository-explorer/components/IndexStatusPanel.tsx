"use client"

// Shows whether the repository is indexed for AI questions and lets the user start indexing.
// While a job is running, it polls the status every 3 seconds until it is "ready" or "failed"

import { useEffect, useState } from "react";

import { getIndexStatus, startIndexing } from "@/lib/api";
import type { IndexState, IndexStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;
const ACTIVE_STATES: IndexState[] = ["queued", "downloading", "chunking", "embedding", "uploading"];

const STATE_LABELS: Record<IndexState, string> = {
    not_indexed: "Not indexed",
    queued: "Waiting to start...",
    downloading: "Downloading repository...",
    chunking: "Splitting code into chunks...",
    embedding: "Creating embeddings...",
    uploading: "Saving to Pinecone...",
    ready: "Ready for questions",
    failed: "Indexing failed",
};

type IndexStatusPanelProps = {
    owner: string;
    repoName: string;
}

export default function IndexStatusPanel({owner, repoName}: IndexStatusPanelProps) {
    const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null);
    const [requestError, setRequestError] = useState("");
    const [isStarting, setIsStarting] = useState(false);
    const [pollCount, setPollCount] = useState(0);

    const isActive = indexStatus !== null && ACTIVE_STATES.includes(indexStatus.status);

    // 1. Loads the status once when the explorer opens
    useEffect(() => {
        let isCurrentRequest = true;

        getIndexStatus(owner, repoName)
            .then((status) => { if (isCurrentRequest) setIndexStatus(status); })
            .catch((error) => {
                if (isCurrentRequest) setRequestError(error instanceof Error ? error.message : "Status could not be loaded");
            });

        return () => {
            isCurrentRequest = false;
        };
    }, [owner, repoName]);

    // 2. Polls while a job is running. A new timeout is only set after each response,
    // so slow responses never pile up like they could with setInterval.
    // pollCount changes after every attempt (also after an error), so one failed
    // request doesn't stop the polling
    useEffect(() => {
        if (!isActive) return;

        let isCurrentPoll = true;

        const timeout = setTimeout(async () => {
            try {
                const status = await getIndexStatus(owner, repoName);
                // An old response must not overwrite the status after switching repository
                if (!isCurrentPoll) return;
                setIndexStatus(status);
                setRequestError("");
            } catch (error) {
                if (isCurrentPoll) setRequestError(error instanceof Error ? error.message : "Status could not be loaded");
            } finally {
                if (isCurrentPoll) setPollCount((count) => count + 1);
            }
        }, POLL_INTERVAL_MS);

        return () => {
            isCurrentPoll = false;
            clearTimeout(timeout);
        };
    }, [isActive, pollCount, owner, repoName]);

    async function handleStartIndexing() {
        setIsStarting(true);
        setRequestError("");

        try {
            setIndexStatus(await startIndexing(owner, repoName));
        } catch (error) {
            setRequestError(error instanceof Error ? error.message : "Indexing could not be started");
        } finally {
            setIsStarting(false);
        }
    }

    const status = indexStatus?.status ?? "not_indexed";
    // The button also appears if the first status request failed, so the user isn't stuck
    const showButton = !isActive && (indexStatus !== null || requestError !== "");
    const showProgress = status === "embedding" && indexStatus?.total;
    const buttonLabel = status === "failed" ? "Retry" : status === "ready" ? "Check for new commits" : "Index repository";

    return (
        <div className="flex min-w-0 shrink-0 items-center gap-3 rounded-xl border border-slate-700 bg-slate-900 px-4 py-3">
            <span
                className={`size-2.5 shrink-0 rounded-full ${
                    status === "ready" ? "bg-emerald-400" : status === "failed" ? "bg-red-400" : isActive ? "animate-pulse bg-sky-400" : "bg-slate-500"
                }`}
            ></span>

            <div className="min-w-0">
                <p className="text-xs font-medium text-slate-400">AI index</p>
                <p className="text-sm font-semibold text-slate-100">
                    {indexStatus ? STATE_LABELS[status] : "Checking..."}
                    {showProgress && ` ${indexStatus?.progress} / ${indexStatus?.total}`}
                </p>
                {status === "ready" && indexStatus?.commit_sha && (
                    <p className="font-mono text-xs text-slate-400">commit {indexStatus.commit_sha.slice(0, 7)}</p>
                )}
                {status !== "ready" && indexStatus?.ready_commit_sha && (
                    <p className="font-mono text-xs text-slate-400">
                        still using commit {indexStatus.ready_commit_sha.slice(0, 7)}
                    </p>
                )}
                {(requestError || (status === "failed" && indexStatus?.error)) && (
                    <p className="max-w-64 truncate text-xs text-red-300" title={requestError || indexStatus?.error || ""}>
                        {requestError || indexStatus?.error}
                    </p>
                )}
            </div>

            {showButton && (
                <button
                    type="button"
                    onClick={handleStartIndexing}
                    disabled={isStarting}
                    className={`ml-2 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                        status === "ready"
                            ? "border border-slate-600 text-slate-200 hover:bg-slate-800"
                            : "bg-sky-500 text-slate-950 hover:bg-sky-400"
                    }`}
                >
                    {isStarting ? "Starting..." : buttonLabel}
                </button>
            )}
        </div>
    );
}
