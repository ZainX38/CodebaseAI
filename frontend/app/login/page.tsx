"use client"

import SvgComponent from "@/app/components/svgLogo";

import Link from "next/link"

export default function Login() {
    return (
      <div className="flex flex-col gap-8 items-center justify-center p-8 w-full h-screen
      bg-linear-to-b from-gray-50 to-gray-100 rounded-lg shadow-md">
        
        <SvgComponent />

        <div className="flex flex-col gap-8 bg-white p-8 rounded-2xl border-2 border-gray-200 shadow-xl">
          <button>
            <Link href="/auth/login?connection=github&connection_scope=repo&returnTo=/profile" // connection=github skips the screen hint and directly logs in the user with GitHub as soon as they click the button
            className="bg-sky-800 text-white font-semibold py-4 px-28 rounded-md hover:bg-sky-600 transition-colors duration-75">
              Sign in with GitHub
            </Link>
          </button>

          {/* Outer div puts them in the center and aligns the 2 divs and the span horizontally
              The two divs, create a gray border (thickness 1px by default border-t) and grow forces them
              to expand and take remaining space
          */}
          <div className="flex items-center">
            <div className="grow border-t border-gray-300"></div>
            <span className="mx-4 text-xs text-gray-400 shrink">OR</span>
            <div className="grow border-t border-gray-300"></div>
          </div>

          <div className="flex gap-2">
              <input 
                placeholder="Search public GitHub Repository URL"
                className="grow border border-gray-400 rounded-md p-2 placeholder-gray-400 text-gray-900 focus:outline-none"
              />

              <button className="bg-sky-600 p-4 text-white text-md font-bold rounded-md hover:opacity-85 duration-200 cursor-pointer">
                ADD
              </button>

          </div>
        </div>
          

        <p className="text-gray-700">Don't have an account? 
          <a 
            className="text-sky-700 font-semibold hover:text-blue-700 transition-colors 
            duration-75 cursor-pointer"
            href="https://github.com/signup"
            target="_blank"
            rel="noopener noreferrer"
            > Sign up with GitHub
          </a>
        </p>
        
      </div>
    );
}