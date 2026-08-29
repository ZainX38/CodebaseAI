export default function LoginPage() {
  return (
    <div className="min-h-screen flex flex-col items-center bg-linear-to-b from-gray-900 via-sky-600 to-gray-800 text-white">
        <main className="flex flex-col items-center justify-center flex-grow p-8">
            <h1 className="text-3xl font-bold mb-4">Login Page</h1>
            <p className="text-lg mb-8">Please log in to access the application.</p>
            <a
                href="/api/auth/login"
                className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  Login  
            </a>
        </main>
    </div>
  );
}