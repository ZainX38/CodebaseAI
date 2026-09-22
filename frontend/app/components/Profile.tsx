"use client"

import { useUser } from "@auth0/nextjs-auth0/client"
import { useEffect, useState } from "react";
import Link from "next/link"

import {getRepositories, Repository} from "@/lib/api";

function getInitials(name?: string | null): string {
    if (name) {
        // trim() removes white spaces at start and end
        // split(" ") separates the name into parts e.g. "Zain Mohammad" --> ["Zain", "Mohammad"]
        const nameParts = name.trim().split(" "); 

        // Gets the initials
        return nameParts.length >= 2
        // If name has 2 or more parts, take the first letter of the first two parts e.g. "Zain Mohammad" --> "ZM"
        ? `${nameParts[0][0]}${nameParts[1][0]}`.toUpperCase()
        // If name has only one part, take the first two letters of that part e.g. "Zain" --> "ZA"
        : nameParts[0].slice(0, 2).toUpperCase();
    }

    // If neither name nor email is available, return a default value
    return "U";
}

export default function Profile() {
    const {user, isLoading} = useUser();

    const [repositories, setRepositories] = useState<Repository[]>([]);
    const [isRepositoriesLoading, setIsRepositoriesLoading] = useState(true);
    const [repositoriesError, setRepositoriesError] = useState("");

    useEffect(() => {
        if (!user) return;

        let isCurrentRequest = true;

        async function loadRepositories() {
            try {
                const userRepositories = await getRepositories();
                if (isCurrentRequest) setRepositories(userRepositories);
            } catch (error) {
                if (isCurrentRequest) {
                    setRepositoriesError(error instanceof Error ? error.message : "Repositories could not be loaded");
                }
            } finally {
                if (isCurrentRequest) setIsRepositoriesLoading(false);
            }
        }

        loadRepositories();

        return () => {
            isCurrentRequest = false;
        };
    }, [user]);

    if (isLoading) {
        return <p className="text-xs text-gray-500">Loading...</p>
    };

    if (!user) return null;

    return (
        <>
            <div className="flex items-center gap-2 bg-gray-200 pl-2 pr-6 py-2 rounded-full text-gray-800 max-w-full">
                <span className="w-7 h-7 mr-4 bg-gradient-to-b from-gray-800 to-gray-900 rounded-full flex items-center justify-center text-white text-xs font-semibold shrink-0">
                    {getInitials(user.name)}
                </span>
                <span className="truncate text-md font-semibold">{user.nickname}</span>
            </div>

            <div className="mt-8 w-full text-gray-800">
                <h1 className="mb-4 text-xl font-semibold">Repositories</h1>

                {isRepositoriesLoading && <p className="text-sm text-gray-500">Loading repositories...</p>}
                {repositoriesError && <p className="text-sm text-red-600">{repositoriesError}</p>}
                {!isRepositoriesLoading && !repositoriesError && repositories.length === 0 && (
                    <p className="text-sm text-gray-500">No repositories found.</p>
                )}

                <ul className="space-y-3">
                    {repositories.map((repository) => (
                        <li key={`${repository.owner}/${repository.name}`}>
                            <Link
                                href={`/profile/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.name)}`}
                                className="block rounded-md border border-gray-300 p-3 hover:bg-gray-50"
                            >
                                <div className="flex items-center justify-between gap-4">
                                    <h2 className="font-semibold">{repository.name}</h2>
                                    <span className="text-xs text-gray-500">
                                        {repository.private ? "Private" : "Public"}
                                    </span>
                                </div>
                                <p className="mt-1 text-sm text-gray-600">
                                    {repository.description || "No description"}
                                </p>
                            </Link>
                        </li>
                    ))}
                </ul>
            </div>
        </>
    );
}
