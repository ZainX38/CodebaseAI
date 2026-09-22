import {auth0} from "@/lib/auth0";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function GET() {
    const session = await auth0.getSession();

    if (!session) {
        return Response.json({detail: "Authentication required"}, {status: 401});
    }

    try {
        const {token} = await auth0.getAccessToken();
        const response = await fetch(`${BACKEND_URL}/api/repositories`, {
            headers: {Authorization: `Bearer ${token}`},
            cache: "no-store",
        });
        const backendData = await response.text();

        return new Response(backendData, {
            status: response.status,
            headers: {"Content-Type": "application/json"},
        });
    } catch {
        return Response.json({detail: "Repository service is unavailable"}, {status: 502});
    }
}
