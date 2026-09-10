import { auth0 } from "./lib/auth0"
import { NextRequest, NextResponse } from "next/server";

export async function proxy(request: NextRequest) {
    const authResponse = await auth0.middleware(request);

    // Get path of incoming request
    const path = request.nextUrl.pathname;

    // Set Public paths
    let isPublicPath = false
    if (path === "/login" || path === "/" ) {
        isPublicPath = true
    }

    // Set Private Paths
    let isPrivatePath = false
    if (path === "/profile" || path.startsWith("/profile")) {
        isPrivatePath = true
    }

    // authResponse must always be returned
    // Note: authResponse forwards the request to your app by default if the user is authenticated,
    // or returns a 401 response if not authenticated.
    return authResponse;
}

export const config = {
    matcher: [
        // Match all request paths except for the ones starting with: 
        // _next/static (static files), _next/image (image optimisation files), 
        // favicon.ico, sitemap.xml, robots.txt (metadata files)
        "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)" ,
        "/",
        "/login",
        "/profile",
    ],
};