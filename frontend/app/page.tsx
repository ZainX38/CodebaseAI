import Link from "next/link"

export default async function Home() {
  

  // If there is a session, return this:
  return (
    <div className="flex flex-col gap-8 items-center justify-center min-h-screen bg-linear-to-b from-gray-900 via-sky-600 to-gray-800 text-white">
      <h1 className="text-2xl font-bold">
        Welcome to Home Page
      </h1>

      <Link 
        href="/login"
        className="bg-cyan-600 border border-sky-600 shadow-xl rounded-md p-4 text-xl"
      >Go to Login
      </Link>
    </div>
  );
}
