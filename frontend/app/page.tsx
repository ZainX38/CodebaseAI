import { useAuth0 } from "@auth0/auth0-react";

import { auth0 } from "../lib/auth0"
import Header from "./components/Header"
import SignInButton from "./components/SignInButton"
import Profile from "./components/Profile"

export default async function Home() {
  const session = await auth0.getSession(); // Gets the sessions data
  const user = session?.user; // Get the user data from the session if available

  if (!session) {
    return (
      <>
        <Header />
        <SignInButton />
      </>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center bg-linear-to-b from-gray-900 via-sky-600 to-gray-800 text-white">
      <Header />
      <main className="flex flex-col items-center justify-center flex-grow p-8">
        {user && (
          <>
            <Profile />
            <a href="/auth/logout"
              className="mt-4 bg-red-600 text-white py-2 px-4 rounded-md hover:bg-red-700">
              Logout
            </a>
          </>
        )
        }
      </main>
    </div>
    
  );
}
