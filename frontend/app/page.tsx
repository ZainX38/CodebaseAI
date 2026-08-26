
function Header() {
  return (
    <header className="bg-gray-900 p-8 w-full">
      <h1 className="text-2xl font-bold">Codebase AI</h1>
    </header>
  );
}

function SignIn() {
  return (
    <div className="flex flex-col items-center justify-center p-8 w-2xl h-96
    bg-linear-to-br from-blue-700 to-sky-950 rounded-lg shadow-md">
      <h2 className="text-xl font-semibold mb-4">Welcome to Codebase AI</h2>
      <p className="text-gray-300 mb-4">Sign in to continue</p>
      <button>
        <a className="bg-sky-600 text-white py-2 px-4 rounded-md hover:bg-sky-700">
          Sign in with GitHub
        </a>
      </button>
    </div>
  );
}

export default function Home() {
  return (
    <div className="min-h-screen flex flex-col items-center bg-linear-to-b from-gray-900 via-sky-600 to-gray-800 text-white">
      <Header />
      <main className="flex flex-col items-center justify-center flex-grow p-8">
        <SignIn />
      </main>
    </div>
    
  );
}
