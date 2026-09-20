export type RepositoryEntry = {
    name: string;
    path: string;
    type: string;
}

export type RepositoryFile = {
    name: string;
    path: string;
    content: string;
}

// path is given a default value of ""
function getRepositoryContentsUrl(owner: string, repoName: string, path: string = ""): string {
    // E.g. path = "src/components/Button.tsx"
    // 1. .split() --> ["src", "components", "Button.tsx"]
    // 2. .filter() - removes empty spaces
    // 3. map(encodeURIComponents) --> transforms unsafe characters into safe ones for URL path
    // 4. .join() combines everything together
    const encodedPath = path
        .split("/")
        .filter(Boolean)
        .map(encodeURIComponent)
        .join("/");

    // Constructs a URL without the path 
    // If encodedPath exists meaning that path was provided, return this. If not, return the URL without the pass    
    const repositoryUrl = `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/contents`;
    return encodedPath ? `${repositoryUrl}/${encodedPath}` : repositoryUrl;
}

async function getResponseData<T>(response: Response): Promise<T> {
    // Reads the response and parses into a JS object
    const responseData = await response.json();

    if (!response.ok) {
        throw new Error(responseData.detail || "Repository contents could not be loaded");
    }

    return responseData as T;
}

// ------------------------------ MAIN EXPORTS ----------------------------------

// It should return a Promise as an array of RepositoryEntrys
// path is given a default value of an empty string, meaning that path is optional to be provided
export async function getDirectoryEntries(
    owner: string,
    repoName: string,
    path: string = "",
): Promise<RepositoryEntry[]> {
    // 1. getRepositoryContentsUrl() constructs and returns a URL using the values provided in the parameters
    // 2. Browser sends request to the Next.js server (route.ts) using the URL
    // E.g. owner = "ZainX38", repoName="portfolio", path="" --> URL = /api/repositories/ZainX38/portfolio/contents
    // As the URL is relative, it resolves against the current website's origin --> http://localhost:3000/api/repositories/ZainX38/portfolio/contents
    // This corresponds to route.ts path
    const response = await fetch(getRepositoryContentsUrl(owner, repoName, path));

    // Calls helper function getResponseData() which parses the response into a JS object and returns it
    // This value is sent to RepositoryExplorer.tsx and uses this response (which came from FastAPI backend through route.ts)
    return getResponseData<RepositoryEntry[]>(response);
}

// It returns a Promise as a repositoryFile
// This function is identical to getDirectoryEntries() with the only difference being the Response Type
// RepositoryFile in this function returns the content of the file in the response and not the type as RepositoryEntry[] does
export async function getFileContent(
    owner: string,
    repoName: string,
    path: string,
): Promise<RepositoryFile> {
    // Same URL used as getDirectoryEntries()
    // To differentiate between folder and files, types and path is used. URL does not decide this
    const response = await fetch(getRepositoryContentsUrl(owner, repoName, path));
    return getResponseData<RepositoryFile>(response);
}
