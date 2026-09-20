"use client"

import { useEffect, useRef, useState } from "react";

// Following File does 3 different jobs:
// 1. Load Repositories - loadRepository() fetches the root directory when explorer opens
// 2. Expand/ Collapse Folders - toggleFolder() opens or collapses folders when needed and fetches their content. Also manages cache of files
// 3. Select File  - selectFile() fetches the selected file and displays its content

// Lazy Loading is used as folder contents are requested only as needed

import {
    getDirectoryEntries,    // performs HTTP requests
    getFileContent,
    RepositoryEntry,        // TypeScript types describing the expected response data
    RepositoryFile,
} from "@/lib/api";


// Defines the data it expects to receive from profile/[owner]/[repoName]/page.tsx as a prop
type RepositoryExplorerProps = {
    owner: string;
    repoName: string;
}

// & means FolderProps has all the properties of RepositoryExplorerProps + 3 additional ones
type FolderProps = RepositoryExplorerProps & {
    entry: RepositoryEntry; // Folder metadata
    level: number;          // Its depth in the tree
    onFileSelect: (entry: RepositoryEntry) => void; // A callback when a file is selected
}

// Gets an array of repository entries and returns a new sorted array
// ...entries is a copy of the entries array
// This copy is passed and the method .sort() is used. It gets the valus of the 1st and 2nd values in array as parameters
// At the end, it checks if it's a directory and if it is, it returns -1. If not, it is considered a file and 1 is returned
// But if the types are the same, "localeCompare" is used to sort them alphabetically
// The final array is sorted with directories first (from a-z) and then files (from a-z)
function sortEntries(entries: RepositoryEntry[]): RepositoryEntry[] {
    return [...entries].sort((firstEntry, secondEntry) => {
        if (firstEntry.type === secondEntry.type) {
            return firstEntry.name.localeCompare(secondEntry.name);
        }

        return firstEntry.type === "dir" ? -1 : 1;
    });
}

// Responsible for rendering one level of a directory tree
function FileTree({
    entries,
    owner,
    repoName,
    level,
    onFileSelect,
}: RepositoryExplorerProps & {
    entries: RepositoryEntry[];
    level: number;
    onFileSelect: (entry: RepositoryEntry) => void;
}) {
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
                <aside className="overflow-auto border-b border-gray-300 md:border-r md:border-b-0">
                    <div className="border-b border-gray-200 px-3 py-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                        Explorer
                    </div>

                    {isTreeLoading && <p className="p-3 text-sm text-gray-500">Loading repository...</p>}
                    {treeError && <p className="p-3 text-sm text-red-600">{treeError}</p>}
                    {!isTreeLoading && !treeError && (
                        <FileTree
                            entries={entries}
                            owner={owner}
                            repoName={repoName}
                            level={0}
                            onFileSelect={selectFile}
                        />
                    )}
                </aside>

                <section className="min-w-0 overflow-auto bg-gray-50">
                    {isFileLoading && <p className="p-4 text-sm text-gray-500">Loading file...</p>}
                    {fileError && <p className="p-4 text-sm text-red-600">{fileError}</p>}
                    {!isFileLoading && !fileError && !selectedFile && (
                        <p className="p-4 text-sm text-gray-500">Select a file to view its contents.</p>
                    )}
                    {selectedFile && !isFileLoading && (
                        <>
                            <div className="border-b border-gray-200 bg-white px-4 py-2 text-sm text-gray-600">
                                {selectedFile.path}
                            </div>
                            <pre className="min-w-max p-4 font-mono text-sm leading-6 whitespace-pre">
                                <code>{selectedFile.content}</code>
                            </pre>
                        </>
                    )}
                </section>
            </div>
        </main>
    );
}
