"use client"

import { useUser } from "@auth0/nextjs-auth0/client"
import { useEffect } from "react"

import { sendTokenData } from "@/lib/api"

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

    useEffect(() => {
        console.log("1")
        sendTokenData(session?.tokenSet.accessToken, "ZainX38", "portfolio")
    }, [])

    if (isLoading) {
        return <p className="text-xs text-gray-500">Loading...</p>
    };

    if (!user) return null;

    return (
        <>
            <div className="flex items-center gap-2 text-green-500 text-[13px] font-medium">
                <span className="w-5 h-5 bg-green-500 rounded-full flex items-center justify-center shrink-0">
                <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                    <path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                </span>
                Successfully authenticated
            </div>
            <div className="flex items-center gap-2 bg-gray-100 rounded-full py-1.5 pl-1.5 pr-4 text-[12px] text-gray-700 max-w-full">
                <span className="w-7 h-7 bg-gradient-to-b from-[#2d2d42] to-[#161620] rounded-full flex items-center justify-center text-white text-[10px] font-semibold shrink-0">
                {getInitials(user.name)}
                </span>
                <span className="truncate">{user.email}</span>
            </div>
        </>
    );
}