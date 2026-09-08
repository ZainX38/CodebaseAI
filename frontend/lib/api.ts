const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function sendTokenData(token: string | undefined, owner: string | undefined, repo_name: string) {    
    try {
        console.log("sendTokenData called")
        console.log(token)
        console.log(owner)
        console.log(repo_name)

        const response = await fetch (`${BACKEND_URL}/api/data`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ 
                owner,
                repo_name
            }), // Sends the data as a JSON object in the request body
        })

        console.log("POST RESPONSE: ", response.status)

        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }

        return response.json()

    } catch (error) {
        console.error("Error sending token data:", error);
    }
}