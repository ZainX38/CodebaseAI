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
        <main className="flex min-h-screen flex-col bg-gray-100 p-4 text-gray-900 sm:p-8">
            <div className="mb-4">
                <h1 className="text-xl font-bold">{repoName}</h1>
                <p className="text-sm text-gray-500">{owner}/{repoName}</p>
            </div>

            <div className="grid min-h-0 flex-1 overflow-hidden rounded-lg border border-gray-300 bg-white shadow-sm md:grid-cols-[280px_minmax(0,1fr)]">
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
        </main>
    );
}
