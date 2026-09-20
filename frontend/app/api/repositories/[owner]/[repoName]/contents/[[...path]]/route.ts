// The folder structure defines the URL path
// /api/repositories/[owner]/[repoName]/contents/[[...path]]
// [[...path]] means the path is an optional value and it can be more than one value


import {auth0} from "@/lib/auth0";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function GET(
    request: Request,
    {params}: {params: Promise<{owner: string, repoName: string, path?: string[]}>},
) {
    const session = await auth0.getSession();

    // Checks if user is authenticated
    if (!session) {
        return Response.json({detail: "Authentication required"}, {status: 401});
    }

    try {
        const {token} = await auth0.getAccessToken(); // Gets Auth0 Access Token
        const {owner, repoName, path = []} = await params;  // Gets the route parameters which are sent by api.ts
        
        // ------------ BUILD FastAPI URL -----------
        // Encodes the path array and adds slashes in between all values
        const encodedPath = path.map(encodeURIComponent).join("/");
        const backendPath = `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/contents`;
        // URL is formed with BACKEND URL + BACKEND PATH (FastAPI defined path) + REPO PATH (optional as it culd be the root)
        const backendUrl = encodedPath ? `${BACKEND_URL}${backendPath}/${encodedPath}` : `${BACKEND_URL}${backendPath}`;
        
        
        // Sends the following data to FastAPI backend: owner, repoName, path and Auth0 Access Token
        const response = await fetch(backendUrl, {
            headers: {Authorization: `Bearer ${token}`},
            cache: "no-store",  // Next.js won't store this fetch response in its server side data cache
        });

        // Reads backend JSON response and converts it into JS string
        const backendData = await response.text()

        // new Response() creates a new HTTP response and sends it back to the original caller (api.ts)
        return new Response(backendData, {
            status: response.status,
            headers: {"Content-Type": "application/json"},
        });
    } catch {
        // If getAccessToken() fails OR fetch() fails OR request was aborted, it returns a server error
        // TODO: Add specific error messages instead of a generic 502
        return Response.json({detail: "Repository service is unavailable"}, {status: 502});
    }
}
