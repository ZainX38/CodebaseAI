import type { RepositoryEntry } from "@/lib/api";

// Gets an array of repository entries and returns a new sorted array
// ...entries is a copy of the entries array
// This copy is passed and the method .sort() is used. It gets the valus of the 1st and 2nd values in array as parameters
// At the end, it checks if it's a directory and if it is, it returns -1. If not, it is considered a file and 1 is returned
// But if the types are the same, "localeCompare" is used to sort them alphabetically
// The final array is sorted with directories first (from a-z) and then files (from a-z)
export function sortEntries(entries: RepositoryEntry[]): RepositoryEntry[] {
    return [...entries].sort((firstEntry, secondEntry) => {
        if (firstEntry.type === secondEntry.type) {
            return firstEntry.name.localeCompare(secondEntry.name);
        }

        return firstEntry.type === "dir" ? -1 : 1;
    });
}
