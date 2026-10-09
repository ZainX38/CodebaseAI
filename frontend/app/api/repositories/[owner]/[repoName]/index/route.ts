// /api/repositories/[owner]/[repoName]/index
// GET  --> FastAPI /index/status (the browser polls this while indexing runs)
// POST --> FastAPI /index (starts indexing in the background)

import {auth0} from "@/lib/auth0";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

type RouteContext = {params: Promise<{owner: string, repoName: string}>};

// Both methods do the same thing apart from the HTTP method and the FastAPI path,
// so the shared work (session check, token, error handling) lives in one function
async function forwardToBackend(method: "GET" | "POST", context: RouteContext, backendSuffix: string) {
    const session = await auth0.getSession();

    if (!session) {
        return Response.json({detail: "Authentication required"}, {status: 401});
    }

    try {
        const {token} = await auth0.getAccessToken();
        const {owner, repoName} = await context.params;
        const backendPath = `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/${backendSuffix}`;

        const response = await fetch(`${BACKEND_URL}${backendPath}`, {
            method,
            headers: {Authorization: `Bearer ${token}`},
            cache: "no-store",  // the status changes every few seconds, so it must never be cached
        });
        const backendData = await response.text();

        return new Response(backendData, {
            status: response.status,
            headers: {"Content-Type": "application/json"},
        });
    } catch {
        return Response.json({detail: "Indexing service is unavailable"}, {status: 502});
    }
}

export async function GET(request: Request, context: RouteContext) {
    return forwardToBackend("GET", context, "index/status");
}

export async function POST(request: Request, context: RouteContext) {
    return forwardToBackend("POST", context, "index");
}
