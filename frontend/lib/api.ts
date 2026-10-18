export type RepositoryEntry = {
    name: string;
    path: string;
    type: string;
}

export type RepositoryFile = {
    name: string;
    path: string;
    content: string;
    ref?: string; // the commit SHA the file was read at, if it isn't the default branch
}

export type Repository = {
    name: string;
    description: string | null;
    private: boolean;
    owner: string;
}

// queued --> downloading --> chunking --> embedding --> uploading --> ready (or failed)
export type IndexState =
    | "not_indexed" | "queued" | "downloading" | "chunking" | "embedding" | "uploading" | "ready" | "failed";

export type IndexStatus = {
    status: IndexState;
    progress?: number;
    total?: number;
    commit_sha?: string;
    // The newest commit that finished indexing; questions use it even if a newer job failed
    ready_commit_sha?: string;
    error?: string | null;
    updated_at?: string;
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

export async function getRepositories(): Promise<Repository[]> {
    const response = await fetch("/api/repositories");
    return getResponseData<Repository[]>(response);
}

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
// ref is optional: a commit SHA, used when a file is opened from a citation so the line
// numbers match the commit that was indexed, even if the branch has moved on since
export async function getFileContent(
    owner: string,
    repoName: string,
    path: string,
    ref?: string,
): Promise<RepositoryFile> {
    // Same URL used as getDirectoryEntries()
    // To differentiate between folder and files, types and path is used. URL does not decide this
    const url = getRepositoryContentsUrl(owner, repoName, path);
    const response = await fetch(ref ? `${url}?ref=${encodeURIComponent(ref)}` : url);
    const file = await getResponseData<RepositoryFile>(response);
    return ref ? {...file, ref} : file;
}

function getIndexUrl(owner: string, repoName: string): string {
    return `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/index`;
}

// GET --> route.ts --> FastAPI /index/status
export async function getIndexStatus(owner: string, repoName: string): Promise<IndexStatus> {
    const response = await fetch(getIndexUrl(owner, repoName), {cache: "no-store"});
    return getResponseData<IndexStatus>(response);
}

// POST starts indexing. It returns straight away with the first status ("queued"),
// and the actual work continues on the backend
export async function startIndexing(owner: string, repoName: string): Promise<IndexStatus> {
    const response = await fetch(getIndexUrl(owner, repoName), {method: "POST"});
    return getResponseData<IndexStatus>(response);
}

// ------------------------------ CHAT ----------------------------------

export type Citation = {
    id: string;
    path: string;
    start_line: number;
    end_line: number;
    symbol: string | null;
    commit_sha: string;
    url: string;
}

export type ChatDone = {
    answer: string;
    citations: Citation[];
    rejected_citations: string[];
    commit_sha: string | null;
}

// What the chat panel does with each Server-Sent Event
export type ChatHandlers = {
    onStatus: (stage: string) => void;
    onToken: (text: string) => void;
    onDone: (result: ChatDone) => void;
    onError: (detail: string) => void;
}

// EventSource can only send GET requests, so the question is sent with fetch() POST and
// the streamed body is split into events by hand: each event ends with a blank line,
// and has an "event: name" line and a "data: {json}" line
export async function streamChat(
    owner: string,
    repoName: string,
    question: string,
    handlers: ChatHandlers,
    signal?: AbortSignal,
): Promise<void> {
    const response = await fetch(
        `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/chat`,
        {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({question}),
            signal,
        },
    );

    // Errors before streaming (not indexed, no access...) are normal JSON responses
    if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        // FastAPI's 422 has a list of problems as "detail" instead of a message
        throw new Error(typeof data.detail === "string" ? data.detail : "The question could not be sent");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    while (!finished) {
        const {value, done} = await reader.read();
        // stream: true keeps a multi-byte character that is split between two pieces.
        // \r\n and \r are also valid SSE line endings (e.g. after a proxy), so they become \n
        buffer += decoder.decode(value, {stream: !done});
        // A "\r" at the very end could be the first half of "\r\n" split between two pieces,
        // so it waits for the next piece instead of becoming a "\n" too early
        const endsWithCarriageReturn = !done && buffer.endsWith("\r");
        buffer = (endsWithCarriageReturn ? buffer.slice(0, -1) : buffer).replace(/\r\n?/g, "\n")
            + (endsWithCarriageReturn ? "\r" : "");

        // Every complete event (ending with a blank line) is handled; the rest waits.
        // Nothing after "done" or "error" is handled, because the answer is already finished
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1 && !finished) {
            finished = handleEvent(buffer.slice(0, boundary), handlers);
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
        }

        if (done) {
            // The last event may arrive without its blank line before the stream closes
            if (!finished && buffer.trim()) finished = handleEvent(buffer, handlers);
            break;
        }
    }

    // Stops reading as soon as the answer is complete
    if (finished) await reader.cancel().catch(() => undefined);
    else handlers.onError("The connection closed before the answer was finished");
}

// Returns true for the last event of an answer ("done" or "error")
function handleEvent(block: string, handlers: ChatHandlers): boolean {
    let eventName = "message";
    let data = "";
    // The space after the colon is optional in SSE ("data:{...}" is also valid)
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    // Several data lines belong to one event and are joined with a new line, as the spec says
    data = dataLines.join("\n");

    let payload;
    try {
        payload = JSON.parse(data);
    } catch {
        return false; // an incomplete or unknown event is ignored
    }

    switch (eventName) {
        case "status":
            handlers.onStatus(payload.stage);
            return false;
        case "token":
            handlers.onToken(payload.text);
            return false;
        case "done":
            handlers.onDone(payload as ChatDone);
            return true;
        case "error":
            handlers.onError(payload.detail);
            return true;
        default:
            return false;
    }
}
