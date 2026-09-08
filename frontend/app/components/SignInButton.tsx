"use client"

export default function SignIn() {
  return (
    <>
      <div className="flex flex-col items-center justify-center p-8 w-full h-screen
      bg-linear-to-br from-blue-700 to-sky-950 rounded-lg shadow-md">
        <h2 className="text-xl font-semibold mb-4">Welcome to Codebase AI</h2>

        <button className="mb-8">
          <a href="/auth/login?" // connection=github skips the screen hint and directly logs in the user with GitHub as soon as they click the button
          className="bg-sky-600 text-white py-2 px-4 rounded-md hover:bg-sky-700">
            Login with GitHub
          </a>
        </button>

        <button>
          <a href="/auth/login?screen_hint=signup"
          className="bg-sky-600 text-white py-2 px-4 rounded-md hover:bg-sky-700">
            Sign Up with GitHub
          </a>
        </button>
      </div>
    </>
  );
}