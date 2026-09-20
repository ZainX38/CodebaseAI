# Repository File Explorer: Implementation and Learning Guide

This document explains the repository file explorer currently present in the working tree. It is based on the uncommitted files themselves, their differences from `HEAD`, and the earlier Git history. It does not assume that the latest commit contains the explorer.

## 1. Evidence and change scope

### Git state examined

Before this document was created, `git status --short` reported:

```text
 M backend/main.py
 M frontend/app/components/Profile.tsx
 D frontend/app/profile/[username]/[repository]/page.tsx
 M frontend/app/profile/page.tsx
 M frontend/lib/api.ts
?? AGENTS.md
?? CLAUDE.md
?? frontend/app/api/
?? frontend/app/components/RepositoryExplorer.tsx
?? frontend/app/profile/[owner]/
```

`git diff --cached` was empty, so none of the implementation was staged. The normal `git diff` showed the modified and deleted tracked files. Because untracked files do not appear in a normal diff, these were also read directly:

- `frontend/app/components/RepositoryExplorer.tsx`
- `frontend/app/api/repositories/[owner]/[repoName]/contents/[[...path]]/route.ts`
- `frontend/app/profile/[owner]/[repoName]/page.tsx`

`AGENTS.md` and `CLAUDE.md` were also untracked, but they are repository guidance rather than part of the file explorer implementation.

### What existed in `HEAD`

The current `HEAD` is commit `b4f24e3`, whose message is:

> User can now access private repos. Workflow: user is used to obtain Management API token which is then used to obtain GitHub access token

At that commit:

- The route was `profile/[username]/[repository]/page.tsx` and only printed the two route parameters.
- `Profile.tsx` sent an Auth0 access token, owner, and repository name from the browser to `POST /api/data`, then requested `GET /api/repo`.
- `frontend/lib/api.ts` called FastAPI directly through `NEXT_PUBLIC_BACKEND_URL` and logged request data, including the token.
- FastAPI stored the current owner, repository name, and authorization header in the global `user_dict`.
- `GET /api/repo` used that shared global state and a hardcoded Auth0 user ID.
- FastAPI fetched only the repository root from GitHub.

The explorer, recursive folder rendering, file-content view, Next.js API proxy, request-scoped repository route, Auth0 `/userinfo` validation, and file decoding did not exist in `HEAD`.

### Current uncommitted implementation

The implementation changes the tracked files as follows:

| File | Git state | Role in the implementation |
| --- | --- | --- |
| `backend/main.py` | Modified | Replaces global repository state and the unauthenticated repository endpoint with an authenticated, path-based Contents API endpoint. |
| `frontend/lib/api.ts` | Modified | Replaces the old two-request setup flow with typed directory and file requests to a same-origin Next.js endpoint. |
| `frontend/app/components/Profile.tsx` | Modified | Stops sending a token from the browser and navigates directly to the repository route. |
| `frontend/app/profile/page.tsx` | Modified | Stops reading, logging, and passing the entire server session into the client profile component. |
| `frontend/app/profile/[username]/[repository]/page.tsx` | Deleted | Removes the old placeholder route. |
| `frontend/app/profile/[owner]/[repoName]/page.tsx` | New/untracked | Reads the renamed route parameters and renders the explorer. |
| `frontend/app/components/RepositoryExplorer.tsx` | New/untracked | Implements the interactive tree, lazy folder loading, file selection, caching, and two-panel UI. |
| `frontend/app/api/repositories/[owner]/[repoName]/contents/[[...path]]/route.ts` | New/untracked | Authenticates the Next.js request and proxies it to FastAPI without exposing a token to browser code. |

Git can establish the difference between `HEAD` and the current working tree. It cannot, by itself, establish who authored each individual uncommitted line or the exact order in which those lines were added. This guide therefore describes them as the current uncommitted implementation rather than attributing every change.

## 2. How the explanation is calibrated

The earlier commits provide evidence of concepts already used in the project:

- `8262689` introduced a frontend `fetch` helper, JSON request bodies, response status checks, and `try`/`catch`.
- `cb2c170` and `1871089` introduced FastAPI request models, GET and POST endpoints, headers, a temporary global dictionary, and frontend-to-backend data flow.
- `0874a9d` introduced `httpx.AsyncClient` and an asynchronous GitHub REST API request.
- `5b51b0e` introduced a controlled repository-name input using `useState`, event handling, and a small API module.
- `9901e92` introduced Auth0 sessions, server-side `getSession()`, the Auth0 middleware, and client-side `useUser()`.
- `478a9ef` added route protection and redirects in `proxy.ts`.
- `b4f24e3` added the Auth0 Management API client-credentials flow, environment-based credentials, URL quoting, and retrieval of a GitHub identity-provider token. It also added a basic dynamic repository page.

For that reason, this guide treats basic JSX, a single `useState`, ordinary `fetch`, `async`/`await`, simple FastAPI routes, and the existence of Auth0 sessions as familiar foundations. It gives more detail to the patterns not demonstrated before the explorer: recursive component trees, independent state per folder, `useRef`, effect cleanup, lazy loading, client-side caching, stale asynchronous results, TypeScript generics and composed types, optional catch-all routes, a backend-for-frontend proxy, request-scoped authentication, response-shape discrimination, Base64 decoding, and HTTP error translation.

This is only a calibration of the explanation. Git history is incomplete evidence and does not prove everything that is or is not understood outside this repository.

## 3. The architecture at a glance

The old flow first wrote repository selection into process-wide backend state and then read it through a separate endpoint:

```text
Profile browser component
  -> POST owner + repo + Auth0 token to /api/data
  -> FastAPI stores values in global user_dict
  -> browser calls GET /api/repo without authentication
  -> FastAPI uses global values + hardcoded user ID
  -> GitHub root contents
```

The current flow puts all information needed for a request into that request:

```text
RepositoryExplorer (browser)
  -> GET same-origin /api/repositories/{owner}/{repo}/contents/{optional path}
     -> Next.js Route Handler verifies the Auth0 session
     -> Next.js reads the Auth0 access token on the server
     -> FastAPI validates that token with Auth0 /userinfo
     -> FastAPI obtains that user's GitHub token through Auth0 Management API
     -> FastAPI calls GitHub Contents API for exactly the requested path
     -> directory metadata or decoded file text returns to the browser
```

This introduces three deliberate boundaries:

1. **The route page** translates URL parameters into component props.
2. **The interactive client component** owns browser UI state and asks for data when required.
3. **Server-only layers** handle Auth0 and GitHub credentials.

The key architectural change is not merely a new UI. Repository selection is now request-scoped rather than stored in shared global memory.

## 4. End-to-end request examples

Assume the user visits:

```text
/profile/octocat/hello-world
```

### Initial tree load

1. Next.js matches `[owner]` with `octocat` and `[repoName]` with `hello-world`.
2. The route page passes those strings to `RepositoryExplorer`.
3. After the client component mounts, its `useEffect` calls:

   ```text
   GET /api/repositories/octocat/hello-world/contents
   ```

4. The Next.js Route Handler verifies the session and forwards the request to FastAPI with a server-side `Authorization: Bearer ...` header.
5. FastAPI validates the Auth0 token, finds the user's GitHub provider token, and requests:

   ```text
   GET https://api.github.com/repos/octocat/hello-world/contents
   ```

6. GitHub returns an array of root entries. FastAPI reduces every item to `name`, `path`, and `type` before returning it.
7. React stores the array in `entries`, re-renders, and displays the root tree.

### Expanding `src/components`

1. Clicking `src` runs that folder component's `toggleFolder()`.
2. Its `isExpanded` becomes `true`.
3. Because its local `entries` is still `null`, it requests `contents/src`.
4. The returned children are stored in that particular `Folder` instance.
5. Its nested `FileTree` renders the children one level deeper.
6. Expanding `components` repeats the same process for `contents/src/components`.

### Opening `src/index.ts`

1. Clicking the file calls the top-level `selectFile(entry)` callback.
2. The component checks `fileCache.current[entry.path]`.
3. If the text has not been loaded before, the browser requests `contents/src/index.ts`.
4. GitHub returns a file object whose `content` is Base64-encoded.
5. FastAPI decodes it into UTF-8 text.
6. React stores it as `selectedFile` and renders it inside `<pre><code>`.
7. Selecting another file changes state and DOM content without navigating or reloading the page.

## 5. The route page and the App Router boundary

The new route is:

```text
frontend/app/profile/[owner]/[repoName]/page.tsx
```

Square brackets make a segment dynamic. For `/profile/octocat/hello-world`, Next.js produces approximately:

```ts
{ owner: "octocat", repoName: "hello-world" }
```

In the installed Next.js version, `params` is asynchronous, so the page awaits it:

```tsx
const {owner, repoName} = await params;
```

The earlier placeholder route already demonstrated the basic form of asynchronous App Router parameters. The new page keeps that familiar pattern but changes the parameter names to match their domain meaning and passes them into an interactive component:

```tsx
<RepositoryExplorer owner={owner} repoName={repoName}/>
```

The page remains a Server Component because it has no `"use client"` directive. `RepositoryExplorer` is a Client Component because it needs state, effects, and click handlers. This is a server/client boundary: the server page handles routing, while the smallest useful interactive subtree is shipped to the browser.

An alternative would be to make `page.tsx` itself a Client Component. That would work, but it would mix route concerns with a large amount of explorer state and UI logic. The current separation makes the page describe the route and the component describe the feature.

## 6. Frontend API types and URL construction

`frontend/lib/api.ts` defines two response shapes:

```ts
type RepositoryEntry = {
    name: string;
    path: string;
    type: string;
}

type RepositoryFile = {
    name: string;
    path: string;
    content: string;
}
```

The first is deliberately small. A directory listing does not need file contents, download URLs, Git object IDs, or every other field returned by GitHub. The second contains text only when a file is selected.

This differs from the earlier API helper, which accepted several possibly undefined values and returned effectively untyped JSON. Explicit response types help TypeScript catch mistakes such as reading `content` from a directory entry.

### Generic response parsing

`getResponseData<T>()` is generic. `T` is a placeholder type chosen by the caller:

```ts
getResponseData<RepositoryEntry[]>(response)
getResponseData<RepositoryFile>(response)
```

This avoids duplicating the same status check and error extraction in both functions. Underneath, TypeScript types disappear at runtime. The `as T` assertion does not validate the server's JSON; it tells the compiler to trust that the API fulfilled the contract. Runtime schema validation would provide stronger protection, but would require manual validators or another library. For this small internal API, a shared server/client contract and an assertion keep the MVP simple.

### Encoding paths safely

Repository and file names can contain spaces, `#`, `?`, Unicode characters, and other values with special URL meanings. `encodeURIComponent` converts each logical segment into a safe URL segment.

The file path is split, each part is encoded separately, and the `/` separators are then restored:

```ts
path.split("/").filter(Boolean).map(encodeURIComponent).join("/")
```

Encoding the entire path in one operation would encode `/` as `%2F`, losing the route's segment structure. Encoding nothing would allow special characters to change how the URL is parsed.

## 7. Recursive rendering: representing an unknown-depth tree

A repository hierarchy has no fixed depth:

```text
src/
  components/
    forms/
      RepositoryForm.tsx
```

Writing separate markup for “root folder,” “child folder,” “grandchild folder,” and so on cannot support arbitrary repositories. Recursion solves this by letting the same rendering rule apply at every depth.

### The recursive relationship

The implementation has two cooperating components:

- `FileTree` maps an array of entries into UI rows.
- For a directory entry, `FileTree` renders `Folder`.
- When that folder is expanded and has children, `Folder` renders another `FileTree`.

The relationship is:

```text
FileTree
  -> Folder
       -> FileTree
            -> Folder
                 -> FileTree
```

This is mutual recursion rather than one function directly calling itself. The base case is a file, which renders a button and does not create another tree. An empty directory also stops naturally because mapping an empty array produces no child rows.

### What React does underneath

React does not run one permanent loop through the whole repository. It builds a component tree from the JSX returned during rendering. Every `Folder` occurrence becomes a distinct component instance with its own hook state.

For example, `src` and `public` each execute the same `Folder` function, but React associates different state with their positions and keys:

```tsx
<Folder key={entry.path} ... />
```

The full path is a suitable key because it is stable and unique within a repository tree. The key lets React match each folder with its previous instance across renders, preserving that folder's expansion state and loaded children.

### Why state lives in each folder

Every `Folder` owns:

- `isExpanded`: whether its descendants should be visible.
- `entries`: `null` before loading, then the cached child array.
- `isLoading`: whether its request is in progress.
- `error`: a message for that folder's request.

This is a progression from the earlier single `repoName` state. The meaning of `useState` is unchanged, but there are now many independent instances of the same state definition—one per mounted folder.

An alternative is to keep one large object in `RepositoryExplorer`, keyed by path, containing every expanded state and child array. That makes all tree state centrally inspectable and can simplify operations such as “collapse all.” It also requires more update logic and immutable nested-object handling. Per-folder state fits the MVP because folder behavior is local and there are no global tree commands.

## 8. Lazy loading and API efficiency

The explorer loads only the root at first. A directory is requested only when the user expands it:

```ts
if (entries) return;
setEntries(await getDirectoryEntries(owner, repoName, entry.path));
```

After the first successful request, `entries` remains an array when the folder collapses. Re-expanding it skips the request. This is a small component-local cache.

Lazy loading solves two problems:

1. A repository can contain thousands of entries, most of which the user will never open.
2. GitHub's Contents API naturally returns one directory at a time.

The alternative is an eager recursive load or GitHub's Git Trees API with a recursive option. That can reduce the number of round trips when the entire hierarchy is genuinely required, but it transfers much more data up front, can be truncated for large repositories, and still does not provide selected file text in the desired on-demand manner. The Contents API aligns directly with the interaction: one directory expansion, one directory request.

The implementation sorts a copied array:

```ts
return [...entries].sort(...)
```

The spread is important because JavaScript's `.sort()` mutates its array. React state should not be mutated during rendering. Copying keeps the state value unchanged while displaying directories first and alphabetizing entries of the same type.

## 9. Initial loading with `useEffect`

The root listing is loaded in a `useEffect` whose dependencies are `owner` and `repoName`.

Earlier history shows a simple `useEffect(..., [])` used to call the backend once after sign-in. Here the same lifecycle idea is extended in two ways:

1. The effect reacts if the repository identity changes.
2. It includes cleanup protection for an asynchronous result.

An effect runs after React commits the component to the DOM. It starts `loadRepository()`, which awaits the network request. While it is waiting, the user could navigate away or React could replace this explorer with one for another repository.

The local flag handles that situation:

```ts
let isCurrentRequest = true;

return () => {
    isCurrentRequest = false;
};
```

The cleanup runs before the effect is replaced or when the component unmounts. The old request may still complete, but it will not write its result into the current component state.

This does **not** cancel the network request. An `AbortController` could cancel it and save work, but it adds another moving part. The current flag is a straightforward way to prevent a stale state update for this MVP.

## 10. `useRef`: cache and latest-selection tracking

The explorer introduces `useRef` for two values:

```ts
const fileCache = useRef<Record<string, RepositoryFile>>({});
const selectedPath = useRef("");
```

A ref is a stable object with a mutable `.current` property. React preserves the object between renders, but changing `.current` does not trigger a render.

### File cache

`fileCache.current` maps a repository path to previously fetched file data. Reopening a file reads from memory rather than making another API call.

State would also preserve the cache, but every cache insertion would schedule a render even though cache contents are not displayed directly. A ref is appropriate because `selectedFile` already drives the visible update.

The cache lasts only for the lifetime of this mounted explorer. A page reload creates a new empty cache. A global store, browser storage, or server cache would live longer, but each introduces invalidation questions: what if the repository changed, the user's permissions changed, or the selected branch changed? A component-lifetime cache avoids those issues for the MVP.

### Preventing out-of-order file results

Suppose the user clicks `large-a.ts`, then immediately clicks `small-b.ts`:

```text
request A starts
request B starts
request B finishes -> show B
request A finishes later
```

Without protection, A could overwrite B simply because it finished last. `selectedPath.current` always holds the latest click. A response updates visible state only if its path still matches:

```ts
if (selectedPath.current === entry.path) setSelectedFile(file);
```

The completed A response is still useful and enters the cache, but it cannot replace the user's current selection. This is a basic asynchronous race-condition guard.

An alternative is an `AbortController` per file request. Cancellation can save bandwidth, while the ref comparison remains useful because cancellation is not guaranteed at every stage. A request library could manage caching and races, but adding one would be unnecessary for this feature.

## 11. Conditional rendering and preserving source formatting

The explorer stores loading and error state separately for the tree and selected file. That distinction allows a file-loading message without replacing the already loaded tree.

The right panel uses:

```tsx
<pre className="... whitespace-pre">
    <code>{selectedFile.content}</code>
</pre>
```

Normal HTML collapses repeated spaces and line breaks. `<pre>` uses preformatted text semantics, and `whitespace-pre` reinforces that whitespace should be preserved. React inserts `selectedFile.content` as text, escaping HTML-like characters instead of interpreting repository content as markup. This preserves indentation and also avoids executing code contained in a displayed file.

No router action occurs when a file is selected. The click changes React state, React reconciles the old and new virtual trees, and only the affected DOM is updated. That is why navigation between files does not reload the page.

## 12. The Next.js Route Handler as a backend-for-frontend

The new handler is located at:

```text
app/api/repositories/[owner]/[repoName]/contents/[[...path]]/route.ts
```

This is different from a page route. A `route.ts` exports HTTP method functions such as `GET` and returns an HTTP `Response`; it does not render JSX.

### Optional catch-all route

`[[...path]]` means “zero or more path segments.” It supports both:

```text
/api/repositories/o/r/contents
/api/repositories/o/r/contents/src/components/Button.tsx
```

Next.js supplies the second form as an array:

```ts
path = ["src", "components", "Button.tsx"]
```

The handler encodes each item and joins the array with `/` before calling FastAPI. A query string such as `?path=src/components` was another possible design. The catch-all route mirrors the repository hierarchy and maps cleanly onto FastAPI's path parameter, while a query parameter can be easier when paths contain unusual routing edge cases. Both are valid if encoding is handled carefully.

### Why this extra server hop exists

The browser could call FastAPI directly, as the earlier code did. The problem is authentication: FastAPI needs proof of the current user, but passing the session's access token through client component props exposes it to browser JavaScript.

The Route Handler acts as a backend-for-frontend, often shortened to BFF:

- The browser sends only its normal same-origin session cookie.
- Next.js verifies the session with `auth0.getSession()`.
- Next.js retrieves the access token on the server with `auth0.getAccessToken()`.
- Only the server-to-server request contains the bearer token.

The handler forwards FastAPI's body and status instead of converting every possible response into a new custom format:

```ts
return new Response(await response.text(), {
    status: response.status,
    headers: {"Content-Type": "application/json"},
});
```

This lets a FastAPI `404`, `403`, or `415` reach `getResponseData()`, which extracts its `detail` message.

The current handler uses `NEXT_PUBLIC_BACKEND_URL`. The backend URL itself is not a credential, so this does not expose a secret. However, the `NEXT_PUBLIC_` convention means Next.js may include that environment value in client bundles when referenced from client code. A server-only `BACKEND_URL` would communicate the new boundary more clearly.

The current `request` parameter is not used, although the catch comment mentions aborted requests. Passing `request.signal` to the server-side `fetch` would connect browser cancellation to the backend fetch. As written, cancellation is handled only at the UI-result level, not by aborting this proxy request.

## 13. Request-scoped backend design

The previous backend had a module-level dictionary:

```py
user_dict = {}
```

One request wrote the selected repository and a later request read it. This works during a single-user experiment, but a FastAPI process serves many requests and potentially many users. Global mutable state belongs to the whole Python process:

```text
User A selects private-repo-A
User B selects repo-B before A's GET
User A's GET could read B's selection
```

It also breaks across multiple server processes because each process has a different dictionary.

The new endpoint carries `owner`, `repo_name`, `path`, and authorization with the request itself. This is **request-scoped** and **stateless** with respect to repository selection: processing one request does not depend on a previous user's POST.

If repository connections later need to persist as user-owned application data, a database keyed by the authenticated user would be appropriate. A database solves durable storage and isolation; it should not be replaced by an in-memory global dictionary.

## 14. Authentication and token flow

The implementation uses three different credentials or identities. Keeping their roles separate is essential.

### 1. Auth0 application session

The browser has an encrypted/session cookie managed by the Auth0 Next.js SDK. The Route Handler calls `getSession()` to establish that the request belongs to a logged-in user.

### 2. Auth0 user access token

Next.js retrieves this on the server and sends it to FastAPI as a bearer token. FastAPI does not trust the presence of an arbitrary header. `get_authenticated_user_id()` sends the token to Auth0's `/userinfo` endpoint.

If Auth0 accepts it, the response contains `sub`, the stable Auth0 user identifier. This replaces the hardcoded `github|...` value in `HEAD`.

Underneath, this is token validation by delegation: instead of FastAPI verifying a JWT signature, issuer, audience, expiry, and scopes locally, Auth0 validates the token and returns user information.

An alternative is local JWT validation using Auth0's JWKS public keys. That removes one `/userinfo` network request per explorer request and is common for APIs configured with a custom audience. It requires correct signature, issuer, audience, algorithm, expiry, and key-rotation handling. The current `/userinfo` approach reuses the existing Auth0 setup and is simpler, at the cost of an extra remote call.

If `/userinfo` rejects the token, FastAPI returns `401 Unauthorized`. In the current code, a successful response without `sub` returns `404`; semantically, `401` would usually describe an unusable identity more accurately, but this guide records the current behavior.

### 3. GitHub identity-provider token

The Auth0 Management API token is an application credential obtained through the client-credentials grant. It authorizes the backend application to read the Auth0 user record. The code searches that record's `identities` array for the GitHub provider:

```py
github_identity = next(
    (identity for identity in identities if identity.get("provider") == "github"),
    None,
)
```

This generator examines identities lazily. `next(..., None)` returns the first GitHub identity or `None` instead of raising `StopIteration`. It is safer than assuming `identities[0]` is GitHub, because Auth0 accounts can have multiple linked identities.

The resulting GitHub token is used only in the backend-to-GitHub request. It is never returned to the frontend.

The Auth0 machine-to-machine application needs `read:users` and `read:user_idp_tokens`. Private repository access also depends on the GitHub OAuth connection having the required repository permissions. Code cannot compensate for missing provider scopes.

### Defence in depth

There are two authentication checks:

- Next.js rejects a missing application session.
- FastAPI independently validates the bearer token with Auth0.

The second check matters because FastAPI is a separate network service. It should not assume that every request reaching its port came through the trusted Next.js handler. This is why merely hiding the FastAPI URL or relying on CORS would not secure private repository contents.

CORS controls which browser origins may read cross-origin responses. It is not authentication, and non-browser clients do not have to enforce it. The tightened CORS configuration is useful, but the bearer-token check is the actual access control.

## 15. FastAPI routing and asynchronous work

Two decorators point to the same function:

```py
@app.get("/api/repositories/{owner}/{repo_name}/contents")
@app.get("/api/repositories/{owner}/{repo_name}/contents/{path:path}")
```

The first handles the repository root. The second uses FastAPI's `path` converter so the parameter can contain `/` characters. Both produce the same internal operation with `path=""` as the root default.

`authorization: Annotated[str | None, Header()]` tells FastAPI to obtain the argument from the HTTP header rather than a query parameter. This extends the header-injection pattern already used in commit `1871089`.

### What `async` is doing

The endpoint awaits several network operations:

1. Auth0 `/userinfo`.
2. Auth0's token endpoint.
3. Auth0 Management API user lookup.
4. GitHub Contents API.

Network I/O spends most of its time waiting. `await` suspends the current coroutine and returns control to the event loop, allowing the server to work on other requests rather than blocking a thread for the entire chain.

This does not make the four dependent calls run in parallel. Each later call needs data from the previous one, so they are sequential. Possible future optimizations include caching the Management API token until shortly before expiry or validating the Auth0 user token locally. Those optimizations introduce expiration and invalidation logic and are outside the MVP.

`get_github_contents_url()` is synchronous because it only transforms strings. Marking it `async` would add no concurrency benefit because there is nothing to await.

## 16. GitHub Contents API response shapes

The same GitHub endpoint returns different JSON shapes depending on the path:

- A directory returns an array of entry objects.
- A file returns one object with metadata, encoding, and content.

The backend discriminates the shapes with:

```py
if isinstance(contents, list):
```

For directories, it deliberately returns only the fields required by the tree. This keeps the browser contract stable and avoids leaking unrelated upstream fields.

For files, GitHub's JSON representation contains Base64 text rather than the original bytes. Base64 represents binary data using ASCII characters so it can be transported safely inside JSON. It is encoding, not encryption.

The backend performs two conversions:

```py
b64decode(contents["content"])  # Base64 text -> bytes
.decode("utf-8")                # bytes -> Python string
```

If the bytes are not valid UTF-8, the backend returns `415 Unsupported Media Type`. A null byte is also treated as an indicator that the file is binary. This keeps the MVP focused on source text rather than image or binary previews.

GitHub may omit inline Base64 content for large files. The backend returns `413 Content Too Large` when the expected encoding or content is missing. One edge case in the current condition is that an empty text file also has an empty content value and will be treated as too large. Supporting empty files would require distinguishing an empty but valid Base64 payload from an omitted payload.

Alternatives for large files include GitHub's raw media type or downloading through `download_url`. Both require explicit size limits to avoid turning one click into an unbounded memory or bandwidth operation.

## 17. HTTP status handling

The implementation does more than throw a generic JavaScript error:

- `401` means authentication is absent, invalid, or expired.
- `403` means the user lacks a GitHub connection/token or GitHub denied repository access.
- `404` means GitHub could not find the repository or path. For private repositories, GitHub may also use `404` to avoid revealing existence.
- `413` means file content is not available through this small-file path.
- `415` means the selected item cannot be displayed as UTF-8 text.
- `502` means an upstream/server dependency failed in a way represented as a bad gateway.

FastAPI maps GitHub responses below `500` through to the client, except that it supplies application-specific messages for common statuses. GitHub `5xx` responses become `502`, indicating that this application was acting as a gateway and its upstream dependency failed.

The Next.js handler preserves FastAPI's status. The frontend helper checks `response.ok`, extracts FastAPI's `detail`, and throws an `Error`. The component catches that error and renders its message in the relevant panel.

The Route Handler currently catches all exceptions and returns one generic `502`. That keeps internal errors out of the browser but also merges token-refresh failures, network failures, and programming errors. More specific server logging and error categories would improve diagnosis later without exposing secrets.

## 18. Changes to repository selection

The previous `Profile` component performed two jobs when ADD was clicked:

1. POST selection and token data to FastAPI.
2. Navigate to a repository route.

The new request-scoped endpoint makes the first job unnecessary. The link now contains the owner and repository:

```tsx
href={`/profile/${encodeURIComponent(user.nickname || "")}/${encodeURIComponent(repoName)}`}
```

The owner and repository are encoded because user input and GitHub names become URL segments.

The profile page no longer calls `auth0.getSession()`, logs the session, or passes it to a Client Component. `Profile` already uses `useUser()` for display data. Removing the session prop reduces the data crossing the server/client boundary and prevents the access token from being available to this browser component.

This is an example of architecture simplifying UI code: once repository identity and authentication are carried by each request, the UI no longer needs a preparatory mutation before navigation.

## 19. State transitions worth tracing

### Folder state

```text
Initial:
  isExpanded = false
  entries = null
  isLoading = false
  error = ""

First click:
  isExpanded = true
  isLoading = true
  request starts

Success:
  entries = returned children
  isLoading = false
  nested FileTree renders

Collapse:
  isExpanded = false
  entries remains cached

Re-expand:
  isExpanded = true
  entries already exists, so no request
```

After an error, `entries` remains `null`. The user can collapse and expand again to retry.

### File state

```text
Click uncached file:
  selectedPath ref = clicked path
  selectedFile = null
  isFileLoading = true
  fileError = ""

Success, still latest selection:
  cache[path] = response
  selectedFile = response
  isFileLoading = false

Click cached file:
  selectedFile = cache[path]
  no HTTP request
```

Separating tree state from file state is important. A single `isLoading` flag would not reveal whether the repository, one nested folder, or the selected file was loading.

## 20. Security progression from the earlier design

The old design was explicitly marked as temporary development state. The new design addresses its most important risks with limited architectural change:

### Global selection state -> request parameters

This prevents users and concurrent requests from overwriting one another's active repository selection.

### Hardcoded Auth0 user ID -> authenticated `sub`

The GitHub token now belongs to the user represented by the incoming Auth0 access token.

### Browser-visible token -> server-side BFF token forwarding

The token is no longer passed into `Profile`, logged by `api.ts`, or sent by browser JavaScript to FastAPI.

### Unauthenticated repository GET -> protected FastAPI endpoint

Knowing an owner and repository path is insufficient. FastAPI requires a bearer token and validates it with Auth0 before obtaining any GitHub token.

### First identity -> GitHub identity lookup

Searching by provider avoids silently using the wrong linked identity.

This is not a complete production authentication architecture. It still depends on correct Auth0 scopes, GitHub permissions, secure deployment networking, HTTPS, protected environment variables, appropriate logging, and sensible rate/size limits. It is, however, a direct correction of the specific multi-user and secret-exposure problems visible in `HEAD`.

## 21. Current limitations and deliberate MVP boundaries

The implementation intentionally does not include syntax highlighting, tabs, search, editing, branch selection, image previews, or a full Git object browser.

Other current constraints are worth understanding:

- It operates on the repository's default branch because no `ref` is supplied to GitHub.
- Directory and file caches exist only in the mounted React component tree.
- The UI does not cancel folder or file network requests; it prevents some stale results from becoming visible.
- Nested `Folder` requests do not use the same explicit cleanup flag as the root effect.
- Every content request currently repeats Auth0 and Management API network work.
- The frontend trusts its internal JSON response shape at runtime; TypeScript alone does not validate JSON.
- Empty files currently follow the same backend error path as missing large-file content.
- All non-directory entries are displayed as clickable files, while GitHub can also describe symlinks and submodules.
- The Route Handler's generic `502` response limits diagnostic detail.
- The code reads the backend URL from a public-named environment variable even though only server code needs it.
- The explorer has no automated test suite in the repository yet.

These are not reasons to replace the MVP with a large framework. They identify the next engineering decisions if real usage demonstrates a need.

## 22. Important alternatives and when they would become useful

### One recursive Contents request at a time vs a complete tree request

- **Current approach:** minimal initial data, natural expansion behavior, more requests as the user explores.
- **Complete Git tree:** fewer interactions with GitHub for users who explore everything, larger up-front response, truncation/scale concerns.

### Local component state vs a reducer or external store

- **Current approach:** direct and readable for a small tree UI.
- **`useReducer`:** useful if folder operations become numerous and state transitions need central rules.
- **External store:** useful only if unrelated pages/components must share explorer state.

### Refs vs state for the cache

- **Current ref:** no render for cache-only changes; selected-file state controls the UI.
- **State:** easier to inspect in React tools and useful if cache metadata must be displayed, but causes additional renders.

### Auth0 `/userinfo` vs local JWT verification

- **Current `/userinfo`:** simpler reuse of Auth0, authoritative remote validation, extra latency and dependency per request.
- **Local JWT verification:** faster after key caching, but requires an API audience and careful claim/signature validation.

### User OAuth token vs a GitHub App

- **Current user token:** matches the existing Auth0 GitHub login and accesses repositories as that user.
- **GitHub App installation token:** offers explicit repository installation, narrower permissions, and an application-oriented model, but requires a substantially different connection and token lifecycle.

### Direct browser-to-FastAPI vs Next.js BFF

- **Current BFF:** keeps the Auth0 access token in server code and gives the browser a same-origin API.
- **Direct FastAPI:** one fewer hop, but requires the browser to obtain and send an API token safely and requires a complete API-audience/CORS design.

## 23. A practical mental model

The feature can be understood as four cooperating recursive or request-driven systems:

1. **The URL identifies the repository.** There is no separate “set current repository” mutation.
2. **Each folder knows only its immediate children.** Expanding it asks the server for one more level.
3. **The top explorer knows the selected file.** Child file buttons send entries upward through a callback.
4. **Every server request proves the user again.** No repository content endpoint trusts global state or a hardcoded identity.

The most important progression from the earlier project is the move from a linear demonstration flow—send some data, save it globally, then fetch a root response—to an interactive, concurrent, multi-user-aware flow. React state is now distributed according to UI ownership, API calls are triggered by user intent, URLs describe resources, and authentication remains on server boundaries.

## 24. Suggested order for studying the code

To understand the implementation independently, read it in this order:

1. `frontend/app/profile/[owner]/[repoName]/page.tsx` — see how the repository identity enters the feature.
2. `frontend/lib/api.ts` — learn the browser-visible data contract and URLs.
3. `RepositoryExplorer` from its top-level component downward — trace root loading and file selection.
4. `FileTree` and `Folder` — trace recursion and per-folder state.
5. The Next.js `route.ts` — follow the session and server-only token boundary.
6. `get_repository_contents()` in FastAPI — follow one request through authentication to GitHub.
7. `get_authenticated_user_id()` and `get_github_token()` — distinguish the Auth0 user token, Management API token, and GitHub provider token.
8. Compare those files with `git show HEAD:<path>` — revisit exactly which temporary mechanisms were removed.

While tracing, use one concrete path such as `src/components/Button.tsx`. At every layer, ask:

- What values are available here?
- Which values are trusted, and why?
- Is this code running in the browser, Next.js server, FastAPI server, Auth0, or GitHub?
- Does this step fetch directory metadata or actual file content?
- Which state change causes the next render?

Those questions expose the implementation's boundaries more clearly than memorizing individual framework calls.
