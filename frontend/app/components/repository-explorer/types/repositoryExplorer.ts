import type { RepositoryEntry } from "@/lib/api";

// Defines the data it expects to receive from profile/[owner]/[repoName]/page.tsx as a prop
export type RepositoryExplorerProps = {
    owner: string;
    repoName: string;
}

type OnFileSelect = (entry: RepositoryEntry) => void;

export type FileTreeProps = RepositoryExplorerProps & {
    entries: RepositoryEntry[];
    level: number;
    onFileSelect: OnFileSelect;
}

// & means FolderProps has all the properties of RepositoryExplorerProps + 3 additional ones
export type FolderProps = RepositoryExplorerProps & {
    entry: RepositoryEntry; // Folder metadata
    level: number;          // Its depth in the tree
    onFileSelect: OnFileSelect; // A callback when a file is selected
}

export type RepositoryTreeProps = RepositoryExplorerProps & {
    entries: RepositoryEntry[];
    isLoading: boolean;
    error: string;
    onFileSelect: OnFileSelect;
}
