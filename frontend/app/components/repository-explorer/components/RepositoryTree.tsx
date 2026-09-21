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
        <ul>
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
                            className="w-full truncate py-1 pr-2 text-left text-sm text-gray-700 hover:bg-gray-100"
                            style={{paddingLeft: `${level * 16 + 12}px`}}
                            title={entry.path}
                        >
                            <span className="mr-2 text-gray-400">▱</span>
                            {entry.name}
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
                className="w-full truncate py-1 pr-2 text-left text-sm font-medium text-gray-800 hover:bg-gray-100"
                style={{paddingLeft: `${level * 16 + 8}px`}}
                title={entry.path}
                aria-expanded={isExpanded}
            >
                <span className="mr-2 inline-block w-3 text-gray-500">{isExpanded ? "⌄" : "›"}</span>
                {entry.name}
            </button>

            {isExpanded && isLoading && (
                <p className="py-1 text-xs text-gray-500" style={{paddingLeft: `${(level + 1) * 16 + 12}px`}}>
                    Loading...
                </p>
            )}
            {isExpanded && error && (
                <p className="py-1 pr-2 text-xs text-red-600" style={{paddingLeft: `${(level + 1) * 16 + 12}px`}}>
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
        <aside className="overflow-auto border-b border-gray-300 md:border-r md:border-b-0">
            <div className="border-b border-gray-200 px-3 py-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                Explorer
            </div>

            {isLoading && <p className="p-3 text-sm text-gray-500">Loading repository...</p>}
            {error && <p className="p-3 text-sm text-red-600">{error}</p>}
            {!isLoading && !error && (
                <FileTree
                    entries={entries}
                    owner={owner}
                    repoName={repoName}
                    level={0}
                    onFileSelect={onFileSelect}
                />
            )}
        </aside>
    );
}
