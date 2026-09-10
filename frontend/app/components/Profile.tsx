"use client"

import { useUser } from "@auth0/nextjs-auth0/client"

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

    if (isLoading) {
        return <p className="text-xs text-gray-500">Loading...</p>
    };

    if (!user) return null;

    return (
        <>
            <div className="flex items-center gap-2 bg-white rounded-full py-2 pl-2 pr-6 text-[12px] text-gray-800 max-w-full">
                <span className="w-7 h-7 bg-gradient-to-b from-gray-800 to-gray-900 rounded-full flex items-center justify-center text-white text-xs font-semibold shrink-0">
                    {getInitials(user.name)}
                </span>
                <span className="truncate text-md font-semibold">{user.nickname}</span>
            </div>
        </>
    );
}