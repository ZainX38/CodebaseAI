import { useState } from "react";

import { getDirectoryEntries } from "@/lib/api";
import type { RepositoryEntry } from "@/lib/api";

import type {
    FileTreeProps,
    FolderProps,
    RepositoryTreeProps,
} from "../types/repositoryExplorer";
import { sortEntries } from "../utils/sortEntries";

// Responsible for rendering one level of a directory tree
function FileTree({entries, owner, repoName, level, onFileSelect}: FileTreeProps) {
    return (
        <ul className="space-y-0.5">
            {sortEntries(entries).map((entry) => (
                entry.type === "dir" ? (
                    <Folder
                        key={entry.path}
                        entry={entry}
                        owner={owner}
                        repoName={repoName}
                        level={level}
                        onFileSelect={onFileSelect}
                    />
                ) : (
                    // If an entry isn't a directory, the following gets called to render the content of file
                    <li key={entry.path}>
                        <button
                            type="button"
                            onClick={() => onFileSelect(entry)}
                            className="group flex w-full items-center gap-2 rounded-md py-2 pr-3 text-left text-sm text-slate-600 transition-colors hover:bg-sky-50 hover:text-sky-900 focus:bg-sky-100 focus:text-sky-950 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400"
                            style={{paddingLeft: `${level * 16 + 16}px`}}
                            title={entry.path}
                        >
                            <svg
                                aria-hidden="true"
                                className="size-4 shrink-0 text-slate-400 transition-colors group-hover:text-sky-600 group-focus:text-sky-700"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="1.5"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25H6.75A2.25 2.25 0 0 0 4.5 4.5v15a2.25 2.25 0 0 0 2.25 2.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-12L14.25 2.25Z" />
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25V7.5h5.25" />
                            </svg>
                            <span className="truncate">{entry.name}</span>
                        </button>
                    </li>
                )
            ))}
        </ul>
    );
}

function Folder({entry, owner, repoName, level, onFileSelect}: FolderProps) {
    // Controls if folder is expanded or collapsed
    const [isExpanded, setIsExpanded] = useState(false);
    // Folder contents loaded at once which get saved in memory as cache
    // null means the directory has not succesfully loaded yet
    // [] (empty array) means the directory has loaded but has no entries
    // [a, b] shows the loaded directories
    const [entries, setEntries] = useState<RepositoryEntry[] | null>(null);
    const [isLoading, setIsLoading] = useState(false); // Shows whether a request is active
    const [error, setError] = useState(""); // Shows error message if any

    async function toggleFolder() {
        // isExpanded is set to False at the start so initially, this shouldn't run
        // This basically collapses the directory
        // However, it doesn't erase the entries, meaning the directory content remains in memory
        if (isExpanded) {
            setIsExpanded(false);
            return;
        }

        // This expands the folder and is called after the if statement
        setIsExpanded(true);

        if (entries) return;

        setIsLoading(true);
        setError("");

        try {
            // It gets the path of the folder and FastAPI backend gets called which sends all the
            // folders and files inside the current folder
            setEntries(await getDirectoryEntries(owner, repoName, entry.path));
        } catch (error) {
            setError(error instanceof Error ? error.message : "Folder could not be loaded");
        } finally {
            setIsLoading(false);
        }
    }


    // Renders a file button 
    return (
        <li>
            <button
                type="button"
                onClick={toggleFolder}
                className={`group flex w-full items-center gap-2 rounded-md py-2 pr-3 text-left text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-300 ${
                    isExpanded
                        ? "bg-sky-50 text-sky-950"
                        : "text-slate-700 hover:bg-slate-100 hover:text-slate-950"
                }`}
                style={{paddingLeft: `${level * 16 + 12}px`}}
                title={entry.path}
                aria-expanded={isExpanded}
            >
                <svg
                    aria-hidden="true"
                    className={`size-3 shrink-0 transition-transform ${isExpanded ? "rotate-90 text-sky-700" : "text-slate-400"}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth="2"
                >
                    <path strokeLinecap="round" strokeLinejoin="round" d="m9 5 7 7-7 7" />
                </svg>
                <svg
                    aria-hidden="true"
                    className="size-4 shrink-0 text-amber-500"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth="1.5"
                >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75A2.25 2.25 0 0 1 6 4.5h3.879c.597 0 1.169.237 1.591.659l1.371 1.371c.422.422.994.659 1.591.659H18A2.25 2.25 0 0 1 20.25 9.44v7.81A2.25 2.25 0 0 1 18 19.5H6a2.25 2.25 0 0 1-2.25-2.25V6.75Z" />
                </svg>
                <span className="truncate">{entry.name}</span>
            </button>

            {isExpanded && isLoading && (
                <p className="flex items-center gap-2 py-2 text-xs text-slate-500" style={{paddingLeft: `${(level + 1) * 16 + 16}px`}}>
                    <span className="size-1.5 rounded-full bg-sky-500"></span>
                    <span>Loading...</span>
                </p>
            )}
            {isExpanded && error && (
                <p className="py-2 pr-3 text-xs leading-5 text-red-600" style={{paddingLeft: `${(level + 1) * 16 + 16}px`}}>
                    {error}
                </p>
            )}
            {isExpanded && entries && (
                <FileTree
                    entries={entries}
                    owner={owner}
                    repoName={repoName}
                    level={level + 1}
                    onFileSelect={onFileSelect}
                />
            )}
        </li>
    );
}

export default function RepositoryTree({
    entries,
    owner,
    repoName,
    isLoading,
    error,
    onFileSelect,
}: RepositoryTreeProps) {
    return (
        <aside className="flex min-h-72 min-w-0 flex-col overflow-hidden border-b border-slate-300 bg-white md:min-h-0 md:border-b-0">
            <div className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 px-4 py-4">
                <span className="flex size-9 items-center justify-center rounded-lg border border-sky-200 bg-sky-50 text-sky-700">
                    <svg
                        aria-hidden="true"
                        className="size-4"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth="1.5"
                    >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75A2.25 2.25 0 0 1 6 4.5h3.879c.597 0 1.169.237 1.591.659l1.371 1.371c.422.422.994.659 1.591.659H18A2.25 2.25 0 0 1 20.25 9.44v7.81A2.25 2.25 0 0 1 18 19.5H6a2.25 2.25 0 0 1-2.25-2.25V6.75Z" />
                    </svg>
                </span>
                <div>
                    <p className="text-sm font-semibold text-slate-800">Repository files</p>
                    <p className="text-xs text-slate-500">Browse folders and files</p>
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-auto p-2">
                {isLoading && (
                    <div className="flex items-center gap-2 rounded-lg px-3 py-4 text-sm text-slate-500">
                        <span className="size-2 rounded-full bg-sky-500"></span>
                        <span>Loading repository...</span>
                    </div>
                )}
                {error && (
                    <p className="m-2 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm leading-5 text-red-700">
                        {error}
                    </p>
                )}
                {!isLoading && !error && (
                    <FileTree
                        entries={entries}
                        owner={owner}
                        repoName={repoName}
                        level={0}
                        onFileSelect={onFileSelect}
                    />
                )}
            </div>
        </aside>
    );
}
