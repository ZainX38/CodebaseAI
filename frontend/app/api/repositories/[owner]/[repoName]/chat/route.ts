// /api/repositories/[owner]/[repoName]/chat
// POST --> FastAPI /chat, and the answer is streamed back to the browser as Server-Sent Events

import {auth0} from "@/lib/auth0";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function POST(
    request: Request,
    {params}: {params: Promise<{owner: string, repoName: string}>},
) {
    const session = await auth0.getSession();

    if (!session) {
        return Response.json({detail: "Authentication required"}, {status: 401});
    }

    let question: unknown;
    try {
        question = (await request.json()).question;
    } catch {
        return Response.json({detail: "The request body must be JSON with a question"}, {status: 400});
    }

    // If the session's token can't be refreshed, the user has to log in again (401),
    // which is a different problem from the backend being down (502)
    let token: string;
    try {
        token = (await auth0.getAccessToken()).token;
    } catch {
        return Response.json({detail: "Your session has expired; please log in again"}, {status: 401});
    }

    try {
        const {owner, repoName} = await params;
        const backendPath = `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/chat`;

        const response = await fetch(`${BACKEND_URL}${backendPath}`, {
            method: "POST",
            headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
            // Only the question is forwarded, never anything else the browser sends
            body: JSON.stringify({question}),
            cache: "no-store",
            // If the user closes the page, the request to FastAPI is cancelled too,
            // so the backend stops generating an answer nobody will read
            signal: request.signal,
        });

        // Errors before streaming (401, 404, 409, 422) are normal JSON responses
        if (!response.ok || !response.body) {
            return new Response(await response.text(), {
                status: response.status,
                headers: {"Content-Type": "application/json"},
            });
        }

        // The body is passed through as it arrives. Reading it with .text() first would
        // wait for the whole answer and lose the streaming
        return new Response(response.body, {
            status: 200,
            headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        });
    } catch {
        return Response.json({detail: "Chat service is unavailable"}, {status: 502});
    }
}
