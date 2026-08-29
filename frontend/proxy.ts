import { auth0 } from "./lib/auth0"

export async function proxy(request: Request) {
    const authResponse = await auth0.middleware(request);

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
        "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)" 
    ],
};