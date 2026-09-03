import { Auth0Client } from "@auth0/nextjs-auth0/server";

// Creates the auth0 client instance with the configuration from the environment variables
export const auth0 = new Auth0Client();