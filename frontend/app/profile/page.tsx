import { auth0 } from "@/lib/auth0"
import Profile from "../components/Profile"
import SvgComponent from "../components/svgLogo";

export default async function userProfile() {
    const session = await auth0.getSession(); // Gets the sessions data
    const user = session?.user; // Get the user data from the session if available

    console.log("Session:", session);
    console.log("User:", user);

    return (
        <div className="min-h-screen flex flex-col gap-8 items-center justify-start bg-linear-to-b bg-linear-to-b from-gray-50 to-gray-100 text-white">
            
            <SvgComponent />

            <main 
                className="flex flex-col items-center justify-center p-8 bg-white
                border-2 border-gray-200 rounded-xl shadow-2xl">
                <Profile session={session}/>
                <a href="/auth/logout"
                    className="mt-4 bg-sky-800 text-white py-2 px-4 rounded-md hover:bg-sky-700 transition-colors duration-200">
                    Logout
                </a>
            </main>
        </div>
    );
}