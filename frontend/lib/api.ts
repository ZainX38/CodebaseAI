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

        return {"response": response.json(), "status": response.status}

    } catch (error) {
        console.error("Error sending token data:", error);
    }
}


// Gets the Repository data obtained in the backend for the user
export async function getRepoData() {
    // method is not defined, so it takes the default 'GET' method
    const response = await fetch(`${BACKEND_URL}/api/repo`)
    const fetchedRepoData = await response.json()

    console.log(fetchedRepoData)
    
    if (fetchedRepoData.status != undefined) {
        console.error("Portfolio with that name does not exist", fetchedRepoData)
        throw new Error(`Error: ${fetchedRepoData.status}`)
    }

    return fetchedRepoData
}