"use client"

// Following File does 3 different jobs:
// 1. Load Repositories - loadRepository() fetches the root directory when explorer opens
// 2. Expand/ Collapse Folders - toggleFolder() opens or collapses folders when needed and fetches their content. Also manages cache of files
// 3. Select File  - selectFile() fetches the selected file and displays its content

// Lazy Loading is used as folder contents are requested only as needed

import { useEffect, useRef, useState } from "react";

import {
    getDirectoryEntries,    // performs HTTP requests
    getFileContent,
    RepositoryEntry,        // TypeScript types describing the expected response data
    RepositoryFile,
} from "@/lib/api";

import FileViewer from "./components/FileViewer";
import RepositoryTree from "./components/RepositoryTree";
import type { RepositoryExplorerProps } from "./types/repositoryExplorer";

export default function RepositoryExplorer({owner, repoName}: RepositoryExplorerProps) {
    const [entries, setEntries] = useState<RepositoryEntry[]>([]);
    const [selectedFile, setSelectedFile] = useState<RepositoryFile | null>(null);
    const [isTreeLoading, setIsTreeLoading] = useState(true);
    const [isFileLoading, setIsFileLoading] = useState(false);
    const [treeError, setTreeError] = useState("");
    const [fileError, setFileError] = useState("");
    const fileCache = useRef<Record<string, RepositoryFile>>({});
    const selectedPath = useRef("");

    useEffect(() => {
        let isCurrentRequest = true;

        async function loadRepository() {
            try {
                // Uses helper to get all the repository entries (from the root) from FastAPI backend
                // A JS object should be returned where each directory or file have the keys: name, path and type
                const rootEntries = await getDirectoryEntries(owner, repoName);

                // Stores entries into a React state, so it overrides the intial empty array []
                if (isCurrentRequest) setEntries(rootEntries);
            } catch (error) {
                if (isCurrentRequest) {
                    setTreeError(error instanceof Error ? error.message : "Repository could not be loaded");
                }
            } finally {
                if (isCurrentRequest) setIsTreeLoading(false);
            }
        }

        loadRepository();

        return () => {
            isCurrentRequest = false;
        };
    }, [owner, repoName]);

    async function selectFile(entry: RepositoryEntry) {
        selectedPath.current = entry.path;
        setFileError("");

        // Checks if file is in cache and if it is, no HTTP request is done 
        if (fileCache.current[entry.path]) {
            setSelectedFile(fileCache.current[entry.path]);
            setIsFileLoading(false);
            return;
        }

        setSelectedFile(null);
        setIsFileLoading(true);

        // This is what happens if file is in cache
        try {
            // Gets the content of the file by using HTTP request
            const file = await getFileContent(owner, repoName, entry.path);
            // Stores file in cache
            fileCache.current[entry.path] = file;

            if (selectedPath.current === entry.path) setSelectedFile(file);
        } catch (error) {
            if (selectedPath.current === entry.path) {
                setFileError(error instanceof Error ? error.message : "File could not be loaded");
            }
        } finally {
            if (selectedPath.current === entry.path) setIsFileLoading(false);
        }
    }

    return (
        <main className="min-h-screen bg-sky-50 px-4 py-6 text-slate-900 sm:px-6 sm:py-8 lg:px-8">
            <div className="mx-auto flex min-h-full w-full max-w-screen-2xl flex-col">
                <header className="flex flex-col gap-5 rounded-t-2xl border border-slate-800 border-b-4 border-b-sky-500 bg-slate-950 p-5 shadow-xl shadow-slate-300/40 sm:flex-row sm:items-center sm:justify-between sm:p-6">
                    <div className="flex min-w-0 items-center gap-4">
                        <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-sky-500 text-slate-950 shadow-sm">
                            <svg
                                aria-hidden="true"
                                className="size-6"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="1.5"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75A2.25 2.25 0 0 1 6 4.5h3.879c.597 0 1.169.237 1.591.659l1.371 1.371c.422.422.994.659 1.591.659H18A2.25 2.25 0 0 1 20.25 9.44v7.81A2.25 2.25 0 0 1 18 19.5H6a2.25 2.25 0 0 1-2.25-2.25V6.75Z" />
                            </svg>
                        </span>
                        <div className="min-w-0">
                            <p className="mb-1 text-xs font-semibold tracking-widest text-sky-400 uppercase">
                                Repository workspace
                            </p>
                            <h1 className="truncate text-2xl font-bold tracking-tight text-white sm:text-3xl">
                                {repoName}
                            </h1>
                            <p className="mt-1 truncate font-mono text-sm text-slate-400">{owner}/{repoName}</p>
                        </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-3 rounded-xl border border-slate-700 bg-slate-900 px-4 py-3">
                        <span className="flex size-8 items-center justify-center rounded-full bg-sky-500 text-slate-950">
                            <svg
                                aria-hidden="true"
                                className="size-4"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="1.5"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.5 20.118a7.5 7.5 0 0 1 15 0A17.933 17.933 0 0 1 12 21.75a17.933 17.933 0 0 1-7.5-1.632Z" />
                            </svg>
                        </span>
                        <div>
                            <p className="text-xs font-medium text-slate-400">Owner</p>
                            <p className="text-sm font-semibold text-slate-100">{owner}</p>
                        </div>
                    </div>
                </header>

                <div className="grid min-h-0 flex-1 overflow-hidden rounded-b-2xl border-x border-b border-slate-300 bg-white shadow-xl shadow-slate-300/40 md:grid-cols-[320px_minmax(0,1fr)]">
                    <RepositoryTree
                        entries={entries}
                        owner={owner}
                        repoName={repoName}
                        isLoading={isTreeLoading}
                        error={treeError}
                        onFileSelect={selectFile}
                    />
                    <FileViewer
                        selectedFile={selectedFile}
                        isLoading={isFileLoading}
                        error={fileError}
                    />
                </div>
            </div>
        </main>
    );
}
