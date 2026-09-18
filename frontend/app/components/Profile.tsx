"use client"

import { useUser } from "@auth0/nextjs-auth0/client"
import { useState } from "react";
import Link from "next/link"

import { sendTokenData } from "@/lib/api";
import { getRepoData } from "@/lib/api";

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

export default function Profile({ session }: any) {
    const {user, isLoading} = useUser();

    const [repoName, setRepoName] = useState<string>("") // TypeScript defines that it is a string

    if (isLoading) {
        return <p className="text-xs text-gray-500">Loading...</p>
    };

    const handleTextChange = (e: any) => {
        setRepoName(e.target.value);
    }

    async function getRepoName() {
        await sendTokenData(session?.tokenSet.accessToken, session?.user.nickname, repoName)
        
            try {
                const repoData = await getRepoData()
            } catch {
                throw new Error("There was an error")
            }
        }

    if (!user) return null;

    return (
        <>
            <div className="flex items-center gap-2 bg-gray-200 pl-2 pr-6 py-2 rounded-full text-gray-800 max-w-full">
                <span className="w-7 h-7 mr-4 bg-gradient-to-b from-gray-800 to-gray-900 rounded-full flex items-center justify-center text-white text-xs font-semibold shrink-0">
                    {getInitials(user.name)}
                </span>
                <span className="truncate text-md font-semibold">{user.nickname}</span>
            </div>

            <div className="mt-10 mb-10">
                <input 
                    type="text"
                    value={repoName}
                    onChange={handleTextChange}
                    placeholder="Enter Repository Name"
                    className="rounded-md py-2 pr-16 pl-2 focus:outline-none placeholder:text-gray-400 border border-gray-400 text-gray-800"
                ></input>
                <Link
                    onClick={getRepoName}
                    href={`/profile/${[session?.user.nickname]}/${repoName}`}
                    className="rounded-md bg-sky-600 p-2 ml-2 hover:opacity-85 duration-100 cursor-pointer"
                    >ADD
                </Link>
            </div>
        </>
    );
}