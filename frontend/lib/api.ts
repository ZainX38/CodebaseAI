const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function sendTokenData(token: string | unknown, owner: string | undefined, repo_name: string) {
    try {
        const response = await fetch (`${BACKEND_URL}/zain`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ token, owner, repo_name }), // Sends the data as a JSON object in the request body
        })

        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }

        return response.json

    } catch (error) {
        console.error("Error sending token data:", error);
    }
}