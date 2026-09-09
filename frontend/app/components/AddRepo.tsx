"use client"

import { useState } from "react";
import { sendTokenData } from "@/lib/api";
import { getRepoData } from "@/lib/api";

export default function AddRepo({ session }: any) {
    const [repoName, setRepoName] = useState<string>("") // TypeScript defines that it is a string

    const handleTextChange = (e: any) => {
        setRepoName(e.target.value);
    }

    async function getRepoName() {
        const response = await sendTokenData(session?.tokenSet.accessToken, session?.user.nickname, repoName)
        
        if (response?.status == 200) {
            getRepoData()
        }
    }
    
    return (
        <div className="mt-10 mb-10">
            <input 
                type="text"
                value={repoName}
                onChange={handleTextChange}
                placeholder="Enter Repository Name"
                className="rounded-xl bg-cyan-500 p-2 focus:outline-none"
            ></input>
            <button
                onClick={getRepoName}
                className="rounded-xl bg-cyan-900 p-2 ml-2 hover:bg-cyan-700 transition-colors duration-100"
                >ADD
            </button>
        </div>
    );
}