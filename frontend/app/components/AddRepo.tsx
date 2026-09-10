"use client"

import { useState } from "react";
import { useRouter } from "next/navigation";

import { sendTokenData } from "@/lib/api";
import { getRepoData } from "@/lib/api";

export default function AddRepo({ session }: any) {
    const router = useRouter();

    const [repoName, setRepoName] = useState<string>("") // TypeScript defines that it is a string

    const handleTextChange = (e: any) => {
        setRepoName(e.target.value);
    }

    async function getRepoName() {
        await sendTokenData(session?.tokenSet.accessToken, session?.user.nickname, repoName)
        
            try {
                const repoData = await getRepoData()
                router.push("/displayRepo")
            } catch {
                console.log("u got an error nigga")
            }
        }
    
    return (
        <div className="mt-10 mb-10">
            <input 
                type="text"
                value={repoName}
                onChange={handleTextChange}
                placeholder="Enter Repository Name"
                className="rounded-md py-2 pr-16 pl-2 focus:outline-none placeholder:text-gray-400 border border-gray-400 text-gray-800"
            ></input>
            <button
                onClick={getRepoName}
                className="rounded-md bg-sky-600 p-2 ml-2 hover:opacity-85 duration-100 cursor-pointer"
                >ADD
            </button>
        </div>
    );
}